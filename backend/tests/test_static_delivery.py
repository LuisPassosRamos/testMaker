"""The built site is sent compressed and cached; already-compressed downloads go as they are."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_assets_are_compressed_and_cached(settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Prova Pronta</title>" + " " * 2000, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log('prova pronta');\n" * 2000, encoding="utf-8")
    app = create_app(settings.model_copy(update={"frontend_dist": dist}))
    with TestClient(app) as client:
        js = client.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
        assert js.status_code == 200
        assert js.headers["content-encoding"] == "gzip"
        assert int(js.headers["content-length"]) < 2000  # 58 KB of text shrinks a lot
        assert "immutable" in js.headers["cache-control"]
        page = client.get("/", headers={"Accept-Encoding": "gzip"})
        assert page.headers["content-encoding"] == "gzip"
        assert page.headers["cache-control"] == "no-cache"  # a new deploy shows up at once
        health = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
        assert health.headers["cache-control"] == "no-store"


def test_pdf_downloads_are_not_recompressed(settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (dist / "manual.pdf").write_bytes(b"%PDF-1.4\n" + b"0" * 5000)
    with TestClient(create_app(settings.model_copy(update={"frontend_dist": dist}))) as client:
        pdf = client.get("/manual.pdf", headers={"Accept-Encoding": "gzip"})
        assert pdf.status_code == 200 and "content-encoding" not in pdf.headers
