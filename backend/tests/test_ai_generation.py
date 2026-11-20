"""Creating exams with AI: request → validated JSON → school blocks + teacher-only answer key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.ai import creation
from app.ai.generate import system_prompt, text_runs
from app.domain.models import Exam, runs_text
from app.main import create_app
from app.rendering.answer_key import render_answer_key
from tests.conftest import SAMPLES, wait_job

GENERATED = {
    "instrucoes": ["Leia com atenção antes de responder."],
    "itens": [
        {"tipo": "texto_apoio", "titulo": "A água", "texto": "A água cobre boa parte da Terra.\n\nSem ela não há vida.",
         "fonte": "Texto elaborado para esta avaliação."},
        {"tipo": "questao", "formato": "escolha_unica", "enunciado": "Qual frase é **INCORRETA**?",
         "alternativas": ["a) A água é essencial.", "Os rios são água doce.", "O mar é salgado.", "A água não evapora."],
         "gabarito": "D) A água não evapora.", "habilidade": "Ciclo da água", "demanda": "direta", "pontos": 2.5},
        {"tipo": "questao", "formato": "verdadeiro_falso", "enunciado": "Marque V ou F.",
         "afirmacoes": ["( ) O gelo é água sólida.", "O vapor é visível sempre."], "gabarito": "V, F"},
        {"tipo": "questao", "formato": "associacao", "enunciado": "Ligue as colunas.",
         "coluna_a": ["Sólido", "Líquido"], "coluna_b": ["( ) Rio", "( ) Gelo"], "gabarito": "2, 1"},
        {"tipo": "questao", "formato": "resposta_longa", "enunciado": "Explique o ciclo da água.",
         "linhas_resposta": 5, "gabarito": "Evaporação, condensação e precipitação.",
         "criterios": "Cita as três etapas.", "imagem_necessaria": "Esquema do ciclo da água com setas."},
        {"tipo": "questao", "formato": "calculo", "enunciado": "Uma caixa tem 500 L e gasta 120 L. Quanto sobra?",
         "gabarito": "380 L (500 − 120)"},
    ],
    "mensagem_final": "Você é capaz!",
    "observacoes_professor": "A questão 4 precisa de um esquema.",
}


class FakeWriter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate_json(self, system, contents, schema):
        self.calls.append((system, contents))
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        return json.dumps(reply, ensure_ascii=False)


@pytest.fixture
def ai_client(settings, monkeypatch):
    configured = settings.model_copy(update={
        "inference_url": "https://inference.example", "inference_key": SecretStr("k"), "ai_provider": "heroku"})
    holder = {}

    def fake_model(_settings, task="read"):
        return holder["model"]

    monkeypatch.setattr(creation, "get_model", fake_model)
    with TestClient(create_app(configured)) as c:
        c.holder = holder  # type: ignore[attr-defined]
        yield c


def _project(client, **meta):
    body = {"start_mode": "generate", "meta": {"doc_type": "prova", "disciplina": "Ciências", "ano": "4º ano",
                                               "turma": "A", "professor": "Ana", "valor": "10", **meta}}
    return client.post("/api/projects", json=body).json()


def test_generated_exam_becomes_school_blocks_and_answer_key(ai_client):
    model = FakeWriter(GENERATED)
    ai_client.holder["model"] = model
    project = _project(ai_client)
    job = ai_client.post(f"/api/projects/{project['id']}/ai/generate",
                         json={"conteudo": "Ciclo da água e estados físicos", "quantidade": 5}).json()
    assert job["kind"] == "generate"
    assert wait_job(ai_client, project["id"])["status"] == "done"
    exam = Exam.model_validate(ai_client.get(f"/api/projects/{project['id']}").json()["exam"])

    kinds = [b.kind for b in exam.blocks]
    assert kinds == ["instructions", "support_text", "question", "question", "question", "question", "question"]
    questions = [b for b in exam.blocks if b.kind == "question"]
    assert [q.number for q in questions] == ["1.", "2.", "3.", "4.", "5."]
    q1 = questions[0]
    assert [p.label for p in q1.parts if p.kind == "alternative"] == ["A)", "B)", "C)", "D)"]
    assert runs_text(q1.parts[1].runs) == "A água é essencial.", "letters written by the model are removed"
    assert any(r.bold and r.text == "INCORRETA" for r in q1.parts[0].runs)
    assert [p.label for p in questions[1].parts if p.kind == "alternative"] == ["( )", "( )"]
    assert runs_text(questions[1].parts[1].runs) == "O gelo é água sólida."
    assert any(p.kind == "table" for p in questions[2].parts)
    assert questions[3].answer_space.kind == "lines" and questions[3].answer_space.lines == 5
    assert any(i.code == "needs_image" for i in questions[3].issues)
    assert questions[4].answer_space.kind == "blank" and questions[4].answer_space.label == "Cálculo"
    assert [e.block_id for e in exam.answer_key] == [q.id for q in questions]
    assert exam.answer_key[0].answer.startswith("D)") and exam.answer_key[0].points == "2,5"
    assert exam.meta.mensagem_final == "Você é capaz!"
    assert {i.code for i in exam.document_issues} == {"ai_teacher_note", "ai_generated"}
    system, contents = model.calls[0]
    assert "Manual de geração" in system and "DADO" in system
    assert "Ciclo da água e estados físicos" in contents[0] and "Valor da prova: 10" in contents[0]


def test_teacher_material_is_read_and_sent_as_data(ai_client):
    model = FakeWriter(GENERATED)
    ai_client.holder["model"] = model
    pid = _project(ai_client)["id"]
    with open(SAMPLES / "atividade-portugues.docx", "rb") as fh:
        up = ai_client.post(f"/api/projects/{pid}/sources?role=material",
                            files={"files": ("plano.docx", fh)}).json()
    assert up["project"]["sources"][0]["role"] == "material"
    # Material is not an exam to format.
    assert ai_client.post(f"/api/projects/{pid}/extract", json={}).status_code == 400
    ai_client.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": ""})
    assert wait_job(ai_client, pid)["status"] == "done"
    prompt = model.calls[0][1][0]
    assert "MATERIAL DE REFERÊNCIA “plano.docx”" in prompt and "<<<" in prompt


def test_nothing_to_base_the_exam_on_is_explained(ai_client):
    ai_client.holder["model"] = FakeWriter(GENERATED)
    pid = _project(ai_client)["id"]
    ai_client.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": "  "})
    job = wait_job(ai_client, pid)
    assert job["status"] == "failed" and "Conte o que foi trabalhado" in job["error"]


def test_redo_one_question_keeps_its_place_and_number(ai_client):
    ai_client.holder["model"] = FakeWriter(GENERATED)
    pid = _project(ai_client)["id"]
    ai_client.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": "água"})
    wait_job(ai_client, pid)
    exam = Exam.model_validate(ai_client.get(f"/api/projects/{pid}").json()["exam"])
    target = [b for b in exam.blocks if b.kind == "question"][1]
    model = FakeWriter({"item": {"tipo": "questao", "formato": "resposta_curta", "enunciado": "O que é evaporação?",
                                 "gabarito": "Passagem do líquido para vapor."}})
    ai_client.holder["model"] = model
    ai_client.post(f"/api/projects/{pid}/ai/question/{target.id}", json={"action": "mais_facil"})
    assert wait_job(ai_client, pid)["status"] == "done"
    after = Exam.model_validate(ai_client.get(f"/api/projects/{pid}").json()["exam"])
    new = next(b for b in after.blocks if b.id == target.id)
    assert new.number == target.number == "2."
    assert runs_text(new.parts[0].runs) == "O que é evaporação?"
    assert new.answer_space.kind == "lines"
    entry = next(e for e in after.answer_key if e.block_id == target.id)
    assert entry.answer == "Passagem do líquido para vapor."
    assert [e.block_id for e in after.answer_key] == [b.id for b in after.blocks if b.kind == "question"]
    prompt = model.calls[0][1][0]
    assert "MAIS FÁCIL" in prompt and "Marque V ou F" in prompt


def test_ai_requests_are_limited_per_hour(settings, monkeypatch):
    configured = settings.model_copy(update={
        "inference_url": "https://inference.example", "inference_key": SecretStr("k"), "ai_provider": "heroku",
        "max_ai_requests_per_hour": 2})
    monkeypatch.setattr(creation, "get_model", lambda _s, task="read": FakeWriter(GENERATED))
    with TestClient(create_app(configured)) as c:
        pid = _project(c)["id"]
        for _ in range(2):
            assert c.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": "água"}).status_code == 200
            wait_job(c, pid)
        res = c.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": "água"})
        assert res.status_code == 429 and "Espere" in res.json()["detail"]["message"]


def test_ai_endpoints_explain_when_ai_is_not_configured(client):
    pid = client.post("/api/projects", json={"start_mode": "generate"}).json()["id"]
    res = client.post(f"/api/projects/{pid}/ai/generate", json={"conteudo": "frações"})
    assert res.status_code == 503 and res.json()["detail"]["code"] == "ai_unavailable"
    assert client.get("/api/health").json()["ai"]["enabled"] is False


def test_health_describes_where_content_goes(ai_client):
    ai = ai_client.get("/api/health").json()["ai"]
    assert ai["enabled"] and ai["provider"] == "heroku" and "não é guardado" in ai["notice"]


def test_first_years_start_with_bigger_letters(client):
    small = client.post("/api/projects", json={"meta": {"ano": "1º ano"}}).json()
    regular = client.post("/api/projects", json={"meta": {"ano": "7º ano"}}).json()
    assert small["exam"]["layout"]["font_size"] == 14
    assert regular["exam"]["layout"]["font_size"] == 12


def test_answer_key_document(tmp_path: Path):
    from app.ai.generate import GeneratedExam, to_exam_parts

    blocks, key, _ = to_exam_parts(GeneratedExam.model_validate(GENERATED))
    exam = Exam(blocks=blocks, answer_key=key)
    exam.meta.disciplina, exam.meta.ano, exam.meta.professor = "Ciências", "4º ano", "Ana"
    path = tmp_path / "g.docx"
    render_answer_key(exam, path)
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert "GABARITO" in text and "Não entregar aos alunos" in text
    assert "1. D) A água não evapora." in text and "Critérios: Cita as três etapas." in text


def test_bold_markers_and_prompt_rules():
    runs = text_runs("Marque a **INCORRETA** e a **outra**.")
    assert [(r.text, r.bold) for r in runs] == [("Marque a ", False), ("INCORRETA", True), (" e a ", False),
                                                ("outra", True), (".", False)]
    prompt = system_prompt()
    assert "NÃO o\nescreva" in prompt or "NÃO o escreva" in prompt.replace("\n", " ")
