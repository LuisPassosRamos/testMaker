"""Hosted mode: work survives a restart through the database, and the school code protects the API."""

from __future__ import annotations

import io
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from app.importing.cleanup import cleanup_expired
from app.main import create_app
from app.persistence import Database


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 480), (30, 90, 200)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def postgres_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


def _database_urls(request) -> str:
    if request.param == "sqlite":
        return f"sqlite:///{request.getfixturevalue('tmp_path') / 'escola.db'}"
    return request.getfixturevalue("postgres_url")


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request):
    return _database_urls(request)


def test_projects_and_templates_survive_a_restart(settings, db_url):
    settings = settings.model_copy(update={"database_url": SecretStr(db_url)})
    with TestClient(create_app(settings)) as client:
        pid = client.post("/api/projects", json={"start_mode": "paste", "meta": {"disciplina": "Ciências"}}).json()["id"]
        client.post(f"/api/projects/{pid}/paste", json={"paragraphs": [[{"text": "1) Qual é a cor do céu?"}],
                                                                        [{"text": "a) Azul"}]]})
        asset = client.post(f"/api/projects/{pid}/assets", files={"file": ("figura.png", _png())}).json()["asset_id"]
        original = client.get(f"/api/projects/{pid}/assets/{asset}").content
        template = client.get("/api/templates").json()[0]
        template["school_name"] = "ESCOLA DE TESTE"
        client.put(f"/api/templates/{template['id']}", json=template)
        logo = client.post(f"/api/templates/{template['id']}/logo", files={"file": ("logo.png", _png())}).json()["logo"]

    # A new container on an ephemeral disk: nothing left locally, only the database.
    shutil.rmtree(settings.data_dir)
    with TestClient(create_app(settings)) as client:
        project = client.get(f"/api/projects/{pid}").json()
        assert project["exam"]["meta"]["disciplina"] == "Ciências"
        assert [b["number"] for b in project["exam"]["blocks"] if b["kind"] == "question"] == ["1)"]
        assert client.get(f"/api/projects/{pid}/assets/{asset}").content == original
        restored = client.get("/api/templates").json()[0]
        assert restored["school_name"] == "ESCOLA DE TESTE" and restored["logo"] == logo
        assert client.get(f"/api/templates/{template['id']}/logo").status_code == 200
        health = client.get("/api/health").json()
        assert health["database"] == {"configured": True, "ok": True}


def test_expired_projects_are_removed_from_the_database(settings, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'escola.db'}")
    db.save_project("a" * 32, "{}", updated_at=1000.0)
    db.put_file("a" * 32, "sources/x.png", b"123", 1000.0)
    db.save_project("b" * 32, "{}")
    removed = cleanup_expired(settings, now=1000.0 + settings.retention_hours * 3600 + 1, db=db)
    assert removed >= 1
    assert db.get_project("a" * 32) is None and db.file_manifest("a" * 32) == {}
    assert db.get_project("b" * 32) == "{}"


def test_school_code_protects_the_api(settings):
    settings = settings.model_copy(update={"access_code": SecretStr("Escola Madre 2026")})
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/status").json() == {"required": True, "authenticated": False}
        blocked = client.post("/api/projects", json={"start_mode": "blank"})
        assert blocked.status_code == 401 and blocked.json()["detail"]["code"] == "auth_required"

        wrong = client.post("/api/auth/login", json={"code": "senha errada"})
        assert wrong.status_code == 401 and "secretaria" in wrong.json()["detail"]["message"]

        # Case, spaces and accents do not matter, so the code is easy to type on a phone.
        ok = client.post("/api/auth/login", json={"code": "escolamadre 2026"})
        assert ok.status_code == 200 and "pp_sessao" in ok.cookies
        assert client.get("/api/auth/status").json()["authenticated"] is True
        assert client.post("/api/projects", json={"start_mode": "blank"}).status_code == 200

        client.post("/api/auth/logout")
        client.cookies.clear()
        assert client.post("/api/projects", json={"start_mode": "blank"}).status_code == 401


def test_changing_the_code_signs_everyone_out(settings):
    first = settings.model_copy(update={"access_code": SecretStr("codigo-um")})
    with TestClient(create_app(first)) as client:
        client.post("/api/auth/login", json={"code": "codigo-um"})
        cookie = client.cookies.get("pp_sessao")
    second = settings.model_copy(update={"access_code": SecretStr("codigo-dois")})
    with TestClient(create_app(second)) as client:
        client.cookies.set("pp_sessao", cookie)
        assert client.get("/api/auth/status").json()["authenticated"] is False


def test_login_attempts_are_limited(settings):
    settings = settings.model_copy(update={"access_code": SecretStr("certo")})
    with TestClient(create_app(settings)) as client:
        codes = [client.post("/api/auth/login", json={"code": f"errado{i}"}).status_code for i in range(12)]
        assert codes[:10] == [401] * 10 and codes[-1] == 429
