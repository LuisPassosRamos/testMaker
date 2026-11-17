"""End-to-end flow through the HTTP API: upload -> extract -> edit -> render -> download -> send."""

from __future__ import annotations

import email
from email import policy

import pytest

from tests.conftest import SAMPLES, wait_job

META = {"doc_type": "prova", "disciplina": "Matemática", "unidade": "II", "ano": "5º ano", "turma": "A",
        "professor": "Ana Souza"}
PRINT = {"professor": "Ana Souza", "disciplina": "Matemática", "ano": "5º ano", "turma": "A", "turno": "Matutino",
         "unidade": "II", "data_aplicacao": "20/11/2026", "copias": 32, "frente_verso": True,
         "observacoes": "Grampear", "email_professor": "ana@example.com"}


def _create(client, mode="upload"):
    res = client.post("/api/projects", json={"start_mode": mode, "meta": META})
    assert res.status_code == 200
    return res.json()["id"]


def test_health_reports_capabilities(client):
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert data["mail"] == {"mode": "outbox", "to": "escolamadre2impressoes@gmail.com"}
    assert data["ai"]["enabled"] is False


def test_rejects_unsupported_and_mixed_uploads(client):
    pid = _create(client)
    res = client.post(f"/api/projects/{pid}/sources", files=[("files", ("prova.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100))])
    body = res.json()
    assert body["rejected"] and ".doc" in body["rejected"][0]["message"]
    docx = (SAMPLES / "prova-matematica.docx").read_bytes()
    pdf = (SAMPLES / "prova-matematica.pdf").read_bytes()
    body = client.post(f"/api/projects/{pid}/sources", files=[
        ("files", ("a.docx", docx)), ("files", ("b.pdf", pdf))]).json()
    assert len(body["project"]["sources"]) == 1
    assert "um arquivo por prova" in body["rejected"][0]["message"]


def test_paste_text_is_structured_without_ai(client):
    pid = _create(client, "paste")
    lines = ["1) Quanto é 2 + 2?", "a) 3", "b) 4", "c) 5", "2) Explique o que é uma fração.", "_" * 30, "_" * 30]
    res = client.post(f"/api/projects/{pid}/paste", json={"paragraphs": [[{"text": t}] for t in lines]})
    blocks = res.json()["exam"]["blocks"]
    questions = [b for b in blocks if b["kind"] == "question"]
    assert [q["number"] for q in questions] == ["1)", "2)"]
    assert [p["label"] for p in questions[0]["parts"] if p["kind"] == "alternative"] == ["a)", "b)", "c)"]
    assert questions[1]["answer_space"] == {"kind": "lines", "lines": 2, "height_cm": 4.0}


def test_save_exam_detects_conflicts(client):
    pid = _create(client, "blank")
    project = client.get(f"/api/projects/{pid}").json()
    ok = client.put(f"/api/projects/{pid}/exam", json={"revision": project["revision"], "exam": project["exam"]})
    assert ok.status_code == 200
    stale = client.put(f"/api/projects/{pid}/exam", json={"revision": project["revision"], "exam": project["exam"]})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"


@pytest.mark.converter
def test_full_flow_docx_to_print_inbox(client, settings):
    pid = _create(client)
    docx = (SAMPLES / "prova-matematica.docx").read_bytes()
    client.post(f"/api/projects/{pid}/sources", files=[("files", ("prova.docx", docx))])
    assert client.post(f"/api/projects/{pid}/extract").status_code == 200
    assert wait_job(client, pid)["status"] == "done"
    project = client.get(f"/api/projects/{pid}").json()
    assert sum(1 for b in project["exam"]["blocks"] if b["kind"] == "question") == 6
    assert project["sources"][0]["pages"], "the original must be viewable side by side"

    # A manual correction is saved and must reach the PDF.
    exam = project["exam"]
    question = next(b for b in exam["blocks"] if b["kind"] == "question")
    question["parts"][0]["runs"] = [{"text": "Quanto é dois ao cubo mais quatro vezes cinco?"}]
    question["answer_space"] = {"kind": "lines", "lines": 3, "height_cm": 4}
    exam["layout"]["columns"] = 2
    saved = client.put(f"/api/projects/{pid}/exam", json={"revision": project["revision"], "exam": exam})
    assert saved.status_code == 200

    assert client.post(f"/api/projects/{pid}/render").status_code == 200
    assert wait_job(client, pid)["status"] == "done"
    status = client.get(f"/api/projects/{pid}/render").json()
    assert status["up_to_date"] is True
    render = status["render"]
    assert render["pages"] >= 1
    assert not [c for c in render["checks"] if c["severity"] == "error"], render["checks"]

    rid = render["render_id"]
    pdf = client.get(f"/api/projects/{pid}/renders/{rid}/download/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["content-disposition"]
    assert client.get(f"/api/projects/{pid}/renders/{rid}/pages/1.jpg").status_code == 200

    sent = client.post(f"/api/projects/{pid}/send", json={"render_id": rid, "request": PRINT})
    assert sent.status_code == 200, sent.text
    assert sent.json()["mode"] == "outbox"
    eml = next((settings.data_dir / "outbox").glob("*.eml"))
    message = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert message["To"] == "escolamadre2impressoes@gmail.com"
    assert message["Reply-To"] == "ana@example.com"
    assert "Matemática" in message["Subject"]
    names = sorted(part.get_filename() for part in message.iter_attachments())
    assert names == ["Prova - Matemática - 5º ano A.docx", "Prova - Matemática - 5º ano A.pdf"]
    body = message.get_body(("plain",)).get_content()
    assert "Quantidade de cópias: 32" in body


@pytest.mark.converter
def test_send_refuses_outdated_preview(client):
    pid = _create(client, "paste")
    client.post(f"/api/projects/{pid}/paste", json={"paragraphs": [[{"text": "1) Quanto é 1 + 1?"}], [{"text": "a) 2"}]]})
    client.post(f"/api/projects/{pid}/render")
    assert wait_job(client, pid)["status"] == "done"
    rid = client.get(f"/api/projects/{pid}/render").json()["render"]["render_id"]
    project = client.get(f"/api/projects/{pid}").json()
    project["exam"]["layout"]["font_size"] = 14
    client.put(f"/api/projects/{pid}/exam", json={"revision": project["revision"], "exam": project["exam"]})
    res = client.post(f"/api/projects/{pid}/send", json={"render_id": rid, "request": PRINT})
    assert res.status_code == 409
