"""OCR for scanned PDFs and photos (Tesseract, Portuguese model).

Pre-processing: fix orientation (Tesseract OSD), straighten small skews, even out
lighting from phone photos and upscale small images. Tesseract returns words with
confidences; low-confidence text is flagged for review instead of being trusted.
Figures, ruled tables and answer lines are found from the ink layout and preserved
as crops of the page image (never re-drawn). Bold and italic cannot be recognised
reliably from images, so the document says so explicitly.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.config import BACKEND_DIR, Settings
from app.domain.models import Issue, Provenance
from app.extraction.items import AnswerLinesItem, Item, MediaItem, TextItem
from app.extraction.layout import Line, Word, lines_to_paragraphs, order_page_lines
from app.extraction.pdf_reader import _merge_boxes
from app.structuring.patterns import PAGE_NUMBER_RE, UNDERSCORE_LINE_RE, is_structural_start

log = logging.getLogger(__name__)

_DEFAULT_WINDOWS_CMD = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
_POSSIBLE_EXPONENT = re.compile(r"(?<![\w])[a-zA-Z]\d\b|\b(cm|m|km)[23]\b")


class OcrUnavailable(Exception):
    pass


@dataclass
class OcrStatus:
    available: bool
    version: str | None
    languages: list[str]
    message: str


def _resolve_cmd(settings: Settings) -> str | None:
    if settings.tesseract_cmd:
        return settings.tesseract_cmd
    found = shutil.which("tesseract")
    if found:
        return found
    if _DEFAULT_WINDOWS_CMD.exists():
        return str(_DEFAULT_WINDOWS_CMD)
    return None


def _tessdata_dir(settings: Settings) -> Path | None:
    if settings.tessdata_dir:
        return settings.tessdata_dir
    vendored = BACKEND_DIR / "vendor" / "tessdata"
    return vendored if (vendored / "por.traineddata").exists() else None


def _config(settings: Settings, psm: int = 3) -> str:
    _configure(settings)
    return f"--oem 1 --psm {psm}"


def _configure(settings: Settings) -> str | None:
    """Point pytesseract at the binary and the language data folder.

    The data folder goes through TESSDATA_PREFIX because quoting ``--tessdata-dir``
    paths with spaces is not portable across pytesseract/Windows argument parsing.
    """
    cmd = _resolve_cmd(settings)
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    tessdata = _tessdata_dir(settings)
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    return cmd


def ocr_status(settings: Settings) -> OcrStatus:
    cmd = _configure(settings)
    if not cmd:
        return OcrStatus(False, None, [], "Tesseract não encontrado. Veja o README para instalar.")
    try:
        version = str(pytesseract.get_tesseract_version())
        langs = pytesseract.get_languages(config="")
    except Exception as exc:  # binary present but broken
        return OcrStatus(False, None, [], f"Tesseract não pôde ser executado: {exc.__class__.__name__}")
    wanted = settings.ocr_languages.split("+")
    missing = [lang for lang in wanted if lang not in langs]
    if missing:
        return OcrStatus(False, version, langs, f"Falta o idioma {', '.join(missing)} do Tesseract (veja o README).")
    return OcrStatus(True, version, langs, "OCR pronto")


# ---------------------------------------------------------------- preprocessing


def _estimate_skew(gray: np.ndarray) -> float:
    h, w = gray.shape
    scale = 1000 / max(h, w)
    small = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    sh, sw = bw.shape
    centre = (sw / 2, sh / 2)
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-5, 5.01, 0.25):
        matrix = cv2.getRotationMatrix2D(centre, float(angle), 1.0)
        rotated = cv2.warpAffine(bw, matrix, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        profile = rotated.sum(axis=1, dtype=np.float64)
        score = float(np.sum(np.diff(profile) ** 2))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def _rotate(img: np.ndarray, angle: float, border: int | tuple[int, int, int]) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=border)


def preprocess(image: Image.Image, settings: Settings) -> tuple[np.ndarray, Image.Image, list[str]]:
    """Return (normalised grayscale for OCR, straightened RGB for display/crops, notes)."""
    notes: list[str] = []
    rgb = np.array(image.convert("RGB"))
    h, w = rgb.shape[:2]
    if w < 1700:
        factor = min(2.5, 1700 / w)
        rgb = cv2.resize(rgb, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_CUBIC)
        notes.append("upscaled")

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    try:
        osd = pytesseract.image_to_osd(gray, config=_config(settings, psm=0).replace("--oem 1 ", ""), timeout=30)
        match = re.search(r"Rotate:\s+(\d+)", osd)
        conf = re.search(r"Orientation confidence:\s+([\d.]+)", osd)
        rotate = int(match.group(1)) if match else 0
        if rotate and conf and float(conf.group(1)) > 2:
            code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotate]
            rgb = cv2.rotate(rgb, code)
            gray = cv2.rotate(gray, code)
            notes.append(f"rotated {rotate}")
    except Exception:
        pass  # OSD needs enough text; skipping it is harmless

    angle = _estimate_skew(gray)
    if abs(angle) >= 0.3:
        rgb = _rotate(rgb, angle, (255, 255, 255))
        gray = _rotate(gray, angle, 255)
        notes.append(f"deskew {angle:.2f}")

    # Even out shadows and uneven lighting (typical of phone photos).
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 41)
    normalised = cv2.divide(gray, background, scale=255)
    return normalised, Image.fromarray(rgb), notes


# ------------------------------------------------------------------- analysis


@dataclass
class _Region:
    bbox: tuple[float, float, float, float]
    kind: str
    lines: int = 0


@dataclass
class OcrPageResult:
    display_image: Image.Image
    items: list[Item] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    mean_confidence: float | None = None


def _words_from_tesseract(data: dict) -> list[Word]:
    words: list[Word] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(Word(text=text, x0=x, top=y, x1=x + w, bottom=y + h, conf=conf, block=data["block_num"][i]))
    return words


def _line_masks(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = ink.shape
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 30), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 45))))
    return horiz, vert


def _horizontal_segments(horiz: np.ndarray) -> list[tuple[float, float, float]]:
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    segments = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h <= 12:
            segments.append((float(x), float(x + w), float(y + h / 2)))
    return segments


def _text_mask(shape: tuple[int, ...], words: list[Word]) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    for word in words:
        pad = int(word.height * 0.3)
        cv2.rectangle(mask, (int(word.x0) - pad, int(word.top) - pad), (int(word.x1) + pad, int(word.bottom) + pad),
                      255, -1)
    return mask


def _unexplained_ink(ink, horiz, vert, words, regions) -> np.ndarray:
    """Ink that is not covered by recognised words, rules or already preserved regions."""
    covered = cv2.bitwise_or(_text_mask(ink.shape, words), cv2.bitwise_or(horiz, vert))
    other = cv2.bitwise_and(ink, cv2.bitwise_not(covered))
    for region in regions:
        x0, y0, x1, y1 = (int(v) for v in region.bbox)
        other[max(0, y0):y1, max(0, x0):x1] = 0
    return cv2.morphologyEx(other, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # drop speckle noise


def _line_like_components(other: np.ndarray, line_h: float) -> list[tuple[int, int, int, int]]:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(line_h * 1.2)), max(1, int(line_h * 0.25))))
    joined = cv2.dilate(other, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(joined)
    boxes = []
    for i in range(1, count):
        x, y, cw, ch, _area = stats[i]
        if ch < line_h * 0.45 or ch > line_h * 4 or cw < line_h * 0.8:
            continue
        if cv2.countNonZero(other[y:y + ch, x:x + cw]) < line_h * line_h * 0.25:
            continue
        boxes.append((int(x), int(y), int(cw), int(ch)))
    return boxes


def _recover_missed_text(norm: np.ndarray, boxes, line_h: float, settings: Settings):
    """OCR again, one by one, short text areas the page-level layout analysis skipped.

    Tesseract sometimes classifies short isolated lines (e.g. "a) 28") as images;
    without this second pass they would silently disappear from the exam.
    Returns (recovered words, boxes that still could not be read).
    """
    recovered: list[Word] = []
    unread: list[tuple[int, int, int, int]] = []
    for x, y, cw, ch in boxes[:60]:
        pad = int(line_h * 0.5)
        y0, x0 = max(0, y - pad), max(0, x - pad)
        crop = norm[y0:y + ch + pad, x0:x + cw + pad]
        crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        try:
            data = pytesseract.image_to_data(crop, lang=settings.ocr_languages, config=_config(settings, psm=6),
                                             output_type=pytesseract.Output.DICT, timeout=30)
        except RuntimeError:
            unread.append((x, y, cw, ch))
            continue
        found = [w for w in _words_from_tesseract(data) if w.conf is not None and w.conf >= 30]
        if not found:
            unread.append((x, y, cw, ch))
            continue
        for word in found:
            word.x0 += x0 - 20
            word.x1 += x0 - 20
            word.top += y0 - 20
            word.bottom += y0 - 20
            recovered.append(word)
    return recovered, unread


def _find_regions(norm: np.ndarray, words: list[Word], settings: Settings):
    """Locate tables, figures, unreadable areas and answer lines; recover skipped text.

    Returns (regions, short horizontal rules, words including recovered ones).
    """
    h, w = norm.shape
    _, ink = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    horiz, vert = _line_masks(ink)
    segments = _horizontal_segments(horiz)
    regions: list[_Region] = []
    heights = sorted(wd.height for wd in words if len(wd.text) > 1)
    line_h = float(heights[len(heights) // 2]) if heights else h * 0.012

    # Ruled tables: areas with both horizontal and vertical rules.
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < w * 0.15 or ch < h * 0.03:
            continue
        v_count = cv2.connectedComponents(vert[y:y + ch, x:x + cw])[0] - 1
        h_count = cv2.connectedComponents(horiz[y:y + ch, x:x + cw])[0] - 1
        if v_count >= 2 and h_count >= 2:
            regions.append(_Region((x, y, x + cw, y + ch), "table"))

    # Figures: large areas of ink that are neither text nor rules.
    other = _unexplained_ink(ink, horiz, vert, words, regions)
    blobs = cv2.dilate(other, np.ones((35, 35), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(blobs)
    for i in range(1, count):
        x, y, cw, ch, _area = stats[i]
        if cw < w * 0.07 or ch < h * 0.035 or cw * ch > w * h * 0.6:
            continue
        ink_inside = int(cv2.countNonZero(other[y:y + ch, x:x + cw]))
        if ink_inside < cw * ch * 0.015 or ch < line_h * 4:
            continue
        box = (max(0, x - 8), max(0, y - 8), min(w, x + cw + 8), min(h, y + ch + 8))
        if not any(_overlaps(box, r.bbox) for r in regions):
            regions.append(_Region(box, "figure"))

    _absorb_labels(regions, words, line_h)

    # Text the page-level analysis skipped: read it again; keep what still fails as an image.
    other = _unexplained_ink(ink, horiz, vert, words, regions)
    recovered, unread = _recover_missed_text(norm, _line_like_components(other, line_h), line_h, settings)
    words = words + recovered
    for x, y, cw, ch in unread:
        pad = int(line_h * 0.4)
        regions.append(_Region((max(0, x - pad), max(0, y - pad), min(w, x + cw + pad), min(h, y + ch + pad)),
                               "unread"))

    # Answer lines: long rules with nothing written on them and no vertical rules.
    long_segments = sorted((s for s in segments if s[1] - s[0] > w * 0.35), key=lambda s: s[2])
    free = [
        s for s in long_segments
        if not any(r.kind == "table" and r.bbox[1] - 5 <= s[2] <= r.bbox[3] + 5 for r in regions)
        and not any(wd.bottom - wd.height <= s[2] <= wd.bottom + 4 and wd.x0 < s[1] and wd.x1 > s[0]
                    and len(wd.text.strip("_-.")) > 0 for wd in words)
    ]
    group: list[tuple[float, float, float]] = []
    for seg in free + [(0.0, 0.0, 1e9)]:
        if group and seg[2] - group[-1][2] > h * 0.06:
            if len(group) >= 1:
                regions.append(_Region((min(s[0] for s in group), group[0][2] - h * 0.02,
                                        max(s[1] for s in group), group[-1][2] + 4), "answer_lines", len(group)))
            group = []
        group.append(seg)

    boxes = _merge_boxes([r.bbox for r in regions if r.kind == "figure"], pad=10)
    regions = [r for r in regions if r.kind != "figure"] + [_Region(b, "figure") for b in boxes]

    # Short thin rules (underlines and fill-in blanks) need a finer kernel than table rules.
    fine = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(line_h * 0.6)), 1)))
    short_rules = [s for s in _horizontal_segments(fine) if s[1] - s[0] <= w * 0.35]
    short_rules = [s for s in short_rules
                   if not any(r.bbox[0] <= s[0] and s[1] <= r.bbox[2] and r.bbox[1] <= s[2] <= r.bbox[3]
                              for r in regions if r.kind in ("table", "figure"))]
    blanks = _blank_words(words, short_rules, ink)
    return regions, short_rules, words + blanks


def _blank_words(words: list[Word], rules: list[tuple[float, float, float]], ink: np.ndarray) -> list[Word]:
    """Turn short rules on a text baseline with nothing written above them into "____".

    Tesseract drops underscores, so fill-in-the-blank gaps ("pa__aro", "O resultado é ____")
    would otherwise vanish from the exam. A real blank has empty paper above the rule;
    letter strokes and underlines do not, which keeps false positives out.
    """
    blanks: list[Word] = []
    for rx0, rx1, y in rules:
        on_line = [w for w in words if w.top + w.height * 0.5 <= y <= w.bottom + w.height * 0.35]
        if not on_line:
            continue
        ref = min((w for w in on_line if w.height > 8) or on_line, key=lambda w: abs((w.x0 + w.x1) / 2 - (rx0 + rx1) / 2))
        bottoms = sorted(w.bottom for w in on_line if w.height > ref.height * 0.4)
        baseline = bottoms[len(bottoms) // 2] if bottoms else ref.bottom
        if y < baseline - ref.height * 0.2:
            continue  # a dash or a letter stroke in the middle of the line, not a blank
        top = int(min(w.top for w in on_line))
        bottom = int(y - ref.height * 0.25)
        x0, x1 = int(rx0), int(rx1)
        if bottom <= top or x1 - x0 < 4:
            continue
        # Columns above the rule with no ink: that stretch of the rule is an empty blank.
        empty = ~(ink[top:bottom, x0:x1] > 0).any(axis=0)
        best_start, best_len, start = 0, 0, None
        for i, is_empty in enumerate(list(empty) + [False]):
            if is_empty and start is None:
                start = i
            elif not is_empty and start is not None:
                if i - start > best_len:
                    best_start, best_len = start, i - start
                start = None
        if best_len < max(12, ref.height * 0.7):
            continue  # letters (or an underlined word) sit on this rule
        char_w = max(4.0, ref.height * 0.5)
        count = max(2, min(40, round(best_len / char_w)))
        bx0 = x0 + best_start
        blanks.append(Word(text="_" * count, x0=bx0, top=ref.top, x1=bx0 + best_len, bottom=ref.bottom, conf=None,
                           blank=True))
    # Pieces of the same blank split by the image processing are joined back.
    merged: list[Word] = []
    for blank in sorted(blanks, key=lambda b: (round(b.top / 10), b.x0)):
        prev = merged[-1] if merged else None
        if prev and abs(prev.top - blank.top) < blank.height * 0.5 and blank.x0 - prev.x1 < blank.height * 0.6:
            prev.x1 = max(prev.x1, blank.x1)
            prev.text = "_" * min(40, len(prev.text) + len(blank.text))
        else:
            merged.append(blank)
    return merged


def _absorb_labels(regions: list[_Region], words: list[Word], line_h: float) -> None:
    """Grow table/figure crops to include text lines glued to them (chart titles, axis labels).

    Otherwise labels such as "Seg Ter Qua" would be separated from the bars they name.
    """
    from app.extraction.layout import group_lines

    lines = group_lines(words, 0)
    for region in regions:
        if region.kind not in ("table", "figure"):
            continue
        changed = True
        while changed:
            changed = False
            x0, y0, x1, y1 = region.bbox
            for line in lines:
                if line.x0 < x0 - line_h * 0.5 or line.x1 > x1 + line_h * 0.5:
                    continue
                if is_structural_start(line.text):
                    continue  # a question or alternative right next to a figure stays text
                tokens = [t for t in line.text.split() if t]
                if tokens and sum(len(t) for t in tokens) / len(tokens) > 7:
                    continue  # a real sentence, not a label
                above = 0 <= y0 - line.bottom < line_h * 1.2
                below = 0 <= line.top - y1 < line_h * 1.2
                if above or below:
                    region.bbox = (x0, min(y0, line.top - 6), x1, max(y1, line.bottom + 6))
                    changed = True
                    break


def _overlaps(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def ocr_page(
    image: Image.Image, *, source_id: str, page_no: int, settings: Settings, save_crop
) -> OcrPageResult:
    if not _configure(settings):
        raise OcrUnavailable("Tesseract não encontrado")

    norm, display, _notes = preprocess(image, settings)
    data = pytesseract.image_to_data(
        norm, lang=settings.ocr_languages, config=_config(settings), output_type=pytesseract.Output.DICT,
        timeout=settings.ocr_timeout_s,
    )
    words = _words_from_tesseract(data)
    height, width = norm.shape
    result = OcrPageResult(display_image=display)

    # Underline is not inferred from photos: letter strokes make it unreliable (stated to the user).
    regions, _short_rules, words = _find_regions(norm, words, settings)

    flow: list[Word] = []
    for word in words:
        cx, cy = (word.x0 + word.x1) / 2, (word.top + word.bottom) / 2
        region = next((r for r in regions if r.kind not in ("answer_lines", "unread")
                       and r.bbox[0] <= cx <= r.bbox[2] and r.bbox[1] <= cy <= r.bbox[3]), None)
        if region is None:
            flow.append(word)

    # Drop printed page numbers from the margins of the original.
    margin_lines = [ln for ln in order_page_lines(
        [w for w in flow if w.top < height * 0.05 or w.bottom > height * 0.95], page_no, width, 0, width)[0]]
    drop = {id(w) for ln in margin_lines if PAGE_NUMBER_RE.match(ln.text) for w in ln.words}
    flow = [w for w in flow if id(w) not in drop]

    confs = [w.conf for w in flow if w.conf is not None]
    result.mean_confidence = sum(confs) / len(confs) if confs else None
    text_x0 = min((w.x0 for w in flow), default=0)
    text_x1 = max((w.x1 for w in flow), default=width)
    lines, two_columns = order_page_lines(flow, page_no, width, text_x0, text_x1)
    if two_columns:
        result.issues.append(Issue(
            code="two_columns", severity="warning",
            message=f"A página {page_no} parece ter duas colunas. Confira se a ordem das questões ficou certa.",
        ))

    anchors: list[tuple[tuple[int, float], int, Item]] = []

    def key(line: Line) -> tuple[int, float]:
        return ({0: 0, 1: 0, 2: 1}[line.column] if two_columns else 0, line.top)

    low = settings.ocr_low_confidence
    for para in lines_to_paragraphs(lines):
        runs, joined = para.runs()
        text = "".join(r.text for r in runs)
        x0, y0, x1, y1 = para.bbox()
        prov = Provenance(source_id=source_id, page=page_no, bbox=(x0 / width, y0 / height, x1 / width, y1 / height),
                          method="ocr", confidence=round(para.confidence, 1) if para.confidence is not None else None)
        if UNDERSCORE_LINE_RE.match(text):
            anchors.append((key(para.lines[0]), 0, AnswerLinesItem(count=len(para.lines), prov=prov)))
            continue
        item = TextItem(runs=runs, prov=prov, align=para.align)
        weak = para.low_conf_words
        if (para.confidence is not None and para.confidence < low) or weak:
            item.issues.append(Issue(
                code="ocr_low_confidence",
                severity="error" if (para.confidence or 0) < 50 else "warning",
                message=(
                    "Leitura incerta neste trecho"
                    + (f" (confiança {para.confidence:.0f}%)" if para.confidence is not None else "")
                    + ". Compare com o original."
                ),
                detail={"words": weak[:30], "confidence": para.confidence},
            ))
        if any(w.blank for ln in para.lines for w in ln.words):
            item.issues.append(Issue(
                code="ocr_blank_inserted", severity="info",
                message="Lacunas para completar (____) foram reconstruídas a partir da imagem. Confira.",
            ))
        if _POSSIBLE_EXPONENT.search(text):
            item.issues.append(Issue(
                code="ocr_possible_exponent", severity="warning",
                message="Pode haver expoente ou índice lido como número comum (ex.: “x2” em vez de “x²”). Confira.",
            ))
        if joined:
            item.issues.append(Issue(
                code="hyphen_joined", severity="info",
                message="Palavras divididas no fim da linha foram reunidas: " + ", ".join(joined[:5]),
                detail={"words": joined},
            ))
        anchors.append((key(para.lines[0]), 0, item))

    for region in regions:
        x0, y0, x1, y1 = region.bbox
        prov = Provenance(source_id=source_id, page=page_no, bbox=(x0 / width, y0 / height, x1 / width, y1 / height),
                          method="crop")
        line = Line(words=[Word("", x0, y0, x1, y1)], page=page_no)
        if two_columns:
            line.column = 1 if (x0 + x1) / 2 < width / 2 and x1 - x0 < width * 0.55 else (
                2 if x1 - x0 < width * 0.55 else 0)
        if region.kind == "answer_lines":
            anchors.append((key(line), 1, AnswerLinesItem(count=max(1, region.lines), prov=prov)))
            continue
        crop = display.crop((int(x0), int(y0), int(x1), int(y1)))
        asset = save_crop(crop)
        dpi = width / 21.0 * 2.54  # assume the photo/scan covers an A4 page width
        media = MediaItem(kind="crop", prov=prov, asset_id=asset, width_cm=round((x1 - x0) / dpi * 2.54, 2))
        code, severity, message = {
            "table": ("table_as_crop", "warning",
                      "Tabela preservada como recorte da imagem original (texto não editável)."),
            "unread": ("ocr_unread_area", "error",
                       "Não conseguimos ler este trecho, então ele foi mantido como imagem do original. "
                       "Confira se é texto da prova e, se quiser, digite-o no lugar da imagem."),
        }.get(region.kind, ("figure_as_crop", "info", "Figura preservada como recorte da imagem original."))
        media.issues.append(Issue(code=code, severity=severity, message=message))  # type: ignore[arg-type]
        anchors.append((key(line), 0 if region.kind == "unread" else 1, media))

    anchors.sort(key=lambda a: (a[0], a[1]))
    result.items = [a[2] for a in anchors]

    ink_ratio = float(np.count_nonzero(norm < 128)) / norm.size
    if not flow and ink_ratio > 0.02:
        result.issues.append(Issue(
            code="ocr_no_text", severity="error",
            message=(f"Não conseguimos ler o texto da página {page_no}. A foto pode estar escura, tremida ou "
                     "manuscrita. Tente outra foto ou use “Recortar do original” para usar a página como imagem."),
        ))
    elif result.mean_confidence is not None and result.mean_confidence < 45:
        # The text would be mostly wrong: keep the page itself instead of presenting guesses as content.
        asset = save_crop(display)
        result.items = [MediaItem(
            kind="crop", asset_id=asset, width_cm=18.0,
            prov=Provenance(source_id=source_id, page=page_no, bbox=(0, 0, 1, 1), method="crop"),
            issues=[Issue(
                code="ocr_unreadable_page", severity="error",
                message=(f"A foto da página {page_no} está difícil de ler (confiança média "
                         f"{result.mean_confidence:.0f}%). Para não imprimir texto errado, a página foi mantida "
                         "como imagem. Envie uma foto mais nítida (boa luz, sem tremer) ou digite o conteúdo."),
            )],
        )]
    elif result.mean_confidence is not None and result.mean_confidence < 65:
        result.issues.append(Issue(
            code="ocr_poor_page", severity="error",
            message=(f"A leitura da página {page_no} ficou ruim (confiança média {result.mean_confidence:.0f}%). "
                     "Confira cada trecho com atenção ou envie uma foto mais nítida."),
        ))
    return result
