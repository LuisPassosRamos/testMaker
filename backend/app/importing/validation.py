"""Validation of uploaded files.

Uploads are untrusted. We identify the real type from the file signature (never
from the extension), enforce size/page/pixel limits, refuse macro-enabled Word
files and reject archives that look like zip bombs. Error messages are written
for teachers, in Portuguese, and say what to do next.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageOps

from app.config import Settings
from app.domain.models import SourceKind


class UploadRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidatedFile:
    kind: SourceKind
    ext: str
    data: bytes
    display_name: str
    pages: int | None = None


_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"


def sanitize_display_name(name: str | None) -> str:
    name = (name or "arquivo").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip() or "arquivo"
    return name[:120]


def sniff(data: bytes) -> str | None:
    head = data[:2048]
    if _PNG in head[:8]:
        return "png"
    if head.startswith(_JPEG):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(_OLE):
        return "ole"
    if head.startswith(_ZIP):
        return "zip"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc", b"ftypheim"):
        return "heic"
    return None


def validate_upload(filename: str | None, data: bytes, settings: Settings) -> ValidatedFile:
    display = sanitize_display_name(filename)
    if not data:
        raise UploadRejected("empty", f"O arquivo “{display}” está vazio.")
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected(
            "too_large",
            f"O arquivo “{display}” passa de {settings.max_upload_mb} MB. "
            "Tente salvar uma versão menor (por exemplo, fotos com menos resolução).",
        )

    kind = sniff(data)
    if kind == "pdf":
        return _validate_pdf(display, data, settings)
    if kind in ("png", "jpg", "webp"):
        return _validate_image(display, data, kind, settings)
    if kind == "zip":
        return _validate_docx(display, data)
    if kind == "ole":
        raise UploadRejected(
            "legacy_doc",
            f"“{display}” é um arquivo .doc antigo ou protegido por senha. "
            "Abra no Word, use “Salvar como” e escolha “Documento do Word (.docx)”.",
        )
    if kind == "heic":
        raise UploadRejected(
            "heic",
            f"“{display}” é uma foto HEIC (iPhone). Envie a foto em JPG — no iPhone, "
            "use Ajustes › Câmera › Formatos › “Mais Compatível”.",
        )
    raise UploadRejected(
        "unsupported",
        f"Não reconhecemos o formato de “{display}”. Envie Word (.docx), PDF ou fotos (JPG/PNG).",
    )


def _validate_pdf(display: str, data: bytes, settings: Settings) -> ValidatedFile:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise UploadRejected(
                "pdf_password", f"O PDF “{display}” está protegido por senha. Envie uma versão sem senha."
            ) from exc
        raise UploadRejected("pdf_invalid", f"O PDF “{display}” parece estar corrompido.") from exc
    try:
        pages = len(pdf)
    finally:
        pdf.close()
    if pages == 0:
        raise UploadRejected("pdf_empty", f"O PDF “{display}” não tem páginas.")
    if pages > settings.max_pdf_pages:
        raise UploadRejected(
            "pdf_too_many_pages",
            f"O PDF “{display}” tem {pages} páginas; o limite é {settings.max_pdf_pages}.",
        )
    return ValidatedFile("pdf", ".pdf", data, display, pages)


def _validate_image(display: str, data: bytes, kind: str, settings: Settings) -> ValidatedFile:
    max_pixels = settings.max_image_megapixels * 1_000_000
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > max_pixels:
                raise UploadRejected("image_too_big", f"A imagem “{display}” tem resolução grande demais.")
            if min(width, height) < 300:
                raise UploadRejected(
                    "image_too_small",
                    f"A imagem “{display}” é pequena demais para ler o texto ({width}×{height}). "
                    "Envie a foto original, sem reduzir.",
                )
            # Normalise: respect phone orientation, drop metadata (EXIF may carry location).
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=False)
    except UploadRejected:
        raise
    except Exception as exc:  # Pillow raises many error types for broken files
        raise UploadRejected("image_invalid", f"Não foi possível abrir a imagem “{display}”.") from exc
    return ValidatedFile("image", ".png", out.getvalue(), display, 1)


def _validate_docx(display: str, data: bytes) -> ValidatedFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("docx_invalid", f"O arquivo “{display}” parece estar corrompido.") from exc
    with zf:
        names = zf.namelist()
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise UploadRejected(
                "not_docx",
                f"“{display}” não é um documento do Word. Envie .docx, PDF ou fotos.",
            )
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UploadRejected(
                "docx_macros",
                f"“{display}” contém macros e não é aceito por segurança. Salve como .docx comum.",
            )
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if len(infos) > 5000 or total > 400 * 1024 * 1024:
            raise UploadRejected("docx_bomb", f"“{display}” tem um conteúdo interno grande demais.")
        for info in infos:
            if info.file_size > 20 * 1024 * 1024 and info.compress_size and (
                info.file_size / info.compress_size > 150
            ):
                raise UploadRejected("docx_bomb", f"“{display}” tem um conteúdo interno suspeito.")
        try:
            content_types = zf.read("[Content_Types].xml")
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise UploadRejected(
                "docx_invalid", f"“{display}” parece estar corrompido."
            ) from exc
        if b"macroEnabled" in content_types:
            raise UploadRejected(
                "docx_macros",
                f"“{display}” contém macros e não é aceito por segurança. Salve como .docx comum.",
            )
    return ValidatedFile("docx", ".docx", data, display, None)
