"""Uploads are untrusted: the real type comes from the signature and limits are enforced."""

from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image

from app.importing.validation import UploadRejected, sanitize_display_name, validate_upload
from tests.conftest import SAMPLES


def _png(size=(800, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format="PNG")
    return out.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def test_accepts_supported_formats(settings):
    assert validate_upload("prova.docx", (SAMPLES / "prova-matematica.docx").read_bytes(), settings).kind == "docx"
    pdf = validate_upload("prova.pdf", (SAMPLES / "prova-matematica.pdf").read_bytes(), settings)
    assert pdf.kind == "pdf" and pdf.pages == 2
    image = validate_upload("foto.jpg", (SAMPLES / "foto-pagina-1.jpg").read_bytes(), settings)
    assert image.kind == "image" and image.data.startswith(b"\x89PNG")


def test_type_comes_from_content_not_extension(settings):
    assert validate_upload("disfarçado.pdf", _png(), settings).kind == "image"
    with pytest.raises(UploadRejected) as err:
        validate_upload("prova.docx", b"just some text pretending to be a document", settings)
    assert err.value.code == "unsupported"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 64, "legacy_doc"),
        (b"\x00\x00\x00\x18ftypheic" + b"0" * 64, "heic"),
        (b"", "empty"),
    ],
)
def test_explains_unsupported_inputs(settings, data, code):
    with pytest.raises(UploadRejected) as err:
        validate_upload("arquivo", data, settings)
    assert err.value.code == code
    assert err.value.message  # always a message the teacher can act on


def test_rejects_macros_and_non_word_archives(settings):
    macro = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>", "word/vbaProject.bin": b"x"})
    with pytest.raises(UploadRejected) as err:
        validate_upload("prova.docm", macro, settings)
    assert err.value.code == "docx_macros"
    spreadsheet = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<x/>"})
    with pytest.raises(UploadRejected) as err:
        validate_upload("notas.xlsx", spreadsheet, settings)
    assert err.value.code == "not_docx"


def test_rejects_oversized_and_tiny_files(settings):
    small_limit = settings.model_copy(update={"max_upload_mb": 1})
    with pytest.raises(UploadRejected) as err:
        validate_upload("grande.pdf", b"%PDF-1.7" + b"0" * (2 * 1024 * 1024), small_limit)
    assert err.value.code == "too_large"
    with pytest.raises(UploadRejected) as err:
        validate_upload("miniatura.png", _png((120, 90)), settings)
    assert err.value.code == "image_too_small"


def test_display_names_are_sanitised():
    assert sanitize_display_name("..\\..\\C:/temp/<prova>.pdf") == "prova.pdf"
    assert sanitize_display_name(None) == "arquivo"
