from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.extraction.ocr import ocr_status
from app.main import create_app
from app.rendering.converter import converter_status

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def pytest_collection_modifyitems(config, items):
    settings = Settings()
    has_converter = converter_status(settings).available
    has_ocr = ocr_status(settings).available
    for item in items:
        if "converter" in item.keywords and not has_converter:
            item.add_marker(pytest.mark.skip(reason="LibreOffice/Word not installed"))
        if "ocr" in item.keywords and not has_ocr:
            item.add_marker(pytest.mark.skip(reason="Tesseract with Portuguese data not installed"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", mail_mode="outbox", gemini_api_key=None,
                    frontend_dist=tmp_path / "no-frontend")


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))


def wait_job(client: TestClient, pid: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/projects/{pid}/job").json()
        if job and job["status"] in ("done", "failed"):
            return job
        time.sleep(0.2)
    raise AssertionError("job timed out")
