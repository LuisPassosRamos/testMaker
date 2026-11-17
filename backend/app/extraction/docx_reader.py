"""Direct extraction from Word (.docx) files — no OCR involved.

Walks the document XML in reading order and emits :mod:`app.extraction.items`:
text with effective bold/italic/underline/superscript/subscript, Word automatic
numbering labels, inline and floating images (stored untouched), tables (with merged
cells), text boxes and equations. Anything that cannot be converted faithfully
(charts, SmartArt, drawn shapes, legacy OLE objects without preview) becomes a
placeholder with an issue asking the reviewer to crop it from the original.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from docx import Document
from docx.oxml.ns import qn

from app.domain.models import CellContent, Issue, Provenance, Run, TableCell, merge_runs, runs_text
from app.extraction.docx_numbering import NumberingResolver
from app.extraction.items import AnswerLinesItem, Item, MediaItem, TextItem
from app.extraction.omml import M_NS, omml_to_runs

log = logging.getLogger(__name__)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": M_NS,
}
EMU_PER_CM = 360000
_R_EMBED = f"{{{NS['r']}}}embed"
_R_ID = f"{{{NS['r']}}}id"
_R_LINK = f"{{{NS['r']}}}link"
_UNDERSCORE_LINE = re.compile(r"^[\s_.\-–—]{10,}$")

# Wingdings / Symbol characters frequently used in exams (checkboxes, arrows, math).
_SYMBOL_FONT_MAP = {
    "F0A8": "□", "F06F": "□", "F071": "❑", "F0FE": "☑", "F0FD": "☒", "F0FC": "✓",
    "F0E0": "→", "F0DF": "←", "F0AE": "→", "F0AC": "←", "F0B4": "×", "F0B8": "÷",
    "F0A3": "≤", "F0B3": "≥", "F0B9": "≠", "F0BB": "≈", "F070": "π", "F0D6": "√",
    "F0B1": "±", "F0B0": "°", "F0A5": "∞", "F0E5": "∑", "F0D7": "·", "F0B7": "•",
    "F06C": "●", "F06E": "■", "F0A7": "▪", "F0D8": "➢", "F04A": "☺", "F04C": "☹",
}

_BOOL_FALSE = {"0", "false", "off", "none"}


def _q(prefix_tag: str) -> str:
    prefix, tag = prefix_tag.split(":")
    return f"{{{NS[prefix]}}}{tag}"


def _bool_prop(rpr, tag: str) -> bool | None:
    if rpr is None:
        return None
    el = rpr.find(qn(f"w:{tag}"))
    if el is None:
        return None
    return el.get(qn("w:val"), "true").lower() not in _BOOL_FALSE


def _read_rpr(rpr) -> dict[str, object]:
    props: dict[str, object] = {}
    if rpr is None:
        return props
    for key, tag in (("bold", "b"), ("italic", "i"), ("caps", "caps"), ("vanish", "vanish")):
        value = _bool_prop(rpr, tag)
        if value is not None:
            props[key] = value
    u = rpr.find(qn("w:u"))
    if u is not None:
        props["underline"] = u.get(qn("w:val"), "single") not in _BOOL_FALSE
    va = rpr.find(qn("w:vertAlign"))
    if va is not None:
        val = va.get(qn("w:val"), "baseline")
        props["sup"] = val == "superscript"
        props["sub"] = val == "subscript"
    rstyle = rpr.find(qn("w:rStyle"))
    if rstyle is not None:
        props["_rstyle"] = rstyle.get(qn("w:val"))
    return props


class _StyleResolver:
    """Computes effective character formatting from defaults and style chains."""

    def __init__(self, document):
        self._styles: dict[str, tuple[str | None, dict[str, object]]] = {}
        self._defaults: dict[str, object] = {}
        self.names: dict[str, str] = {}
        try:
            root = document.styles.element
        except AttributeError:
            return
        defaults = root.find(qn("w:docDefaults"))
        if defaults is not None:
            rpr = defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}")
            self._defaults = _read_rpr(rpr)
        for style in root.findall(qn("w:style")):
            sid = style.get(qn("w:styleId"))
            based = style.find(qn("w:basedOn"))
            name = style.find(qn("w:name"))
            self.names[sid] = name.get(qn("w:val")) if name is not None else sid
            props = _read_rpr(style.find(qn("w:rPr")))
            props.pop("_rstyle", None)
            self._styles[sid] = (based.get(qn("w:val")) if based is not None else None, props)

    def chain(self, sid: str | None) -> dict[str, object]:
        stack = []
        seen: set[str] = set()
        while sid and sid not in seen and sid in self._styles:
            seen.add(sid)
            based, props = self._styles[sid]
            stack.append(props)
            sid = based
        merged: dict[str, object] = {}
        for props in reversed(stack):
            merged.update(props)
        return merged

    def effective(self, para_style: str | None, direct: dict[str, object]) -> dict[str, object]:
        merged = dict(self._defaults)
        merged.update(self.chain(para_style))
        merged.update(self.chain(direct.get("_rstyle")))  # type: ignore[arg-type]
        merged.update({k: v for k, v in direct.items() if not k.startswith("_")})
        return merged


@dataclass
class _Segment:
    """Paragraph content split at images: text runs or a media item, in order."""

    runs: list[Run] = field(default_factory=list)
    media: MediaItem | None = None


class DocxExtractor:
    def __init__(self, path: str, source_id: str, save_asset: Callable[[bytes], str]):
        self.path = path
        self.source_id = source_id
        self.save_asset = save_asset
        self.doc = Document(path)
        self.styles = _StyleResolver(self.doc)
        self.numbering = NumberingResolver(self.doc)
        self.document_issues: list[Issue] = []
        self._ignored_page_breaks = 0
        self._auto_numbered = 0

    # ------------------------------------------------------------------ public
    def extract(self) -> list[Item]:
        items: list[Item] = []
        items.extend(self._page_header_items())
        items.extend(self._walk_container(self.doc.element.body, self.doc.part))
        items = self._collapse_answer_lines(items)
        if self._auto_numbered:
            self.document_issues.append(
                Issue(
                    code="docx_auto_numbering",
                    severity="info",
                    message=(
                        f"{self._auto_numbered} número(s) de questões/alternativas vinham da numeração automática "
                        "do Word e foram convertidos em texto, exatamente como apareciam."
                    ),
                )
            )
        if self._ignored_page_breaks:
            self.document_issues.append(
                Issue(
                    code="page_breaks_ignored",
                    severity="info",
                    message=(
                        f"{self._ignored_page_breaks} quebra(s) de página do arquivo original foram "
                        "desconsideradas, porque a prova será diagramada de novo. Você pode inserir "
                        "quebras na conferência ou no ajuste de impressão."
                    ),
                )
            )
        self._report_footer()
        return items

    # ------------------------------------------------------------------ helpers
    def _prov(self) -> Provenance:
        return Provenance(source_id=self.source_id, method="docx")

    def _page_header_items(self) -> list[Item]:
        try:
            section = self.doc.sections[0]
        except IndexError:
            return []
        header = section.first_page_header if section.different_first_page_header_footer else section.header
        if header is None or header.is_linked_to_previous:
            return []  # the first section has no header definition of its own
        try:
            items = self._walk_container(header._element, header.part)
        except Exception:  # headers are optional content; never fail the import for them
            log.warning("docx: could not read page header")
            return []
        for item in items:
            if isinstance(item, (TextItem, MediaItem)):
                item.from_page_header = True
        return [i for i in items if not isinstance(i, AnswerLinesItem)]

    def _report_footer(self) -> None:
        try:
            footer = self.doc.sections[0].footer
            text = " ".join(p.text.strip() for p in footer.paragraphs if p.text.strip())
        except Exception:
            return
        if text:
            self.document_issues.append(
                Issue(
                    code="footer_ignored",
                    severity="info",
                    message=(
                        f"O rodapé do arquivo original não foi incluído: “{text[:120]}”. "
                        "Se quiser mantê-lo, copie o texto para o rodapé no ajuste de impressão."
                    ),
                )
            )

    def _walk_container(self, container, part) -> list[Item]:
        items: list[Item] = []
        for child in self._block_children(container):
            if child.tag == qn("w:p"):
                items.extend(self._paragraph(child, part))
            elif child.tag == qn("w:tbl"):
                items.extend(self._table(child, part))
        return items

    def _block_children(self, container):
        for child in container.iterchildren():
            tag = child.tag
            if tag in (qn("w:p"), qn("w:tbl")):
                yield child
            elif tag == qn("w:sdt"):
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    yield from self._block_children(content)
            elif tag == qn("w:customXml"):
                yield from self._block_children(child)
            elif tag == _q("mc:AlternateContent"):
                chosen = child.find(_q("mc:Choice"))
                if chosen is None:
                    chosen = child.find(_q("mc:Fallback"))
                if chosen is not None:
                    yield from self._block_children(chosen)

    # ---------------------------------------------------------------- paragraph
    def _paragraph(self, p_el, part) -> list[Item]:
        ppr = p_el.find(qn("w:pPr"))
        style_id = None
        align = None
        page_break_before = False
        if ppr is not None:
            if (ps := ppr.find(qn("w:pStyle"))) is not None:
                style_id = ps.get(qn("w:val"))
            if (jc := ppr.find(qn("w:jc"))) is not None:
                align = {"center": "center", "right": "right", "end": "right", "both": "justify"}.get(
                    jc.get(qn("w:val"), ""), None
                )
            page_break_before = bool(_bool_prop(ppr, "pageBreakBefore"))
        if page_break_before:
            self._ignored_page_breaks += 1
        label = self.numbering.label_for(p_el)

        segments: list[_Segment] = [_Segment()]
        extra_items: list[Item] = []
        issues: list[Issue] = []
        self._collect(p_el, part, style_id, segments, extra_items, issues)

        items: list[Item] = []
        first_text = True
        for seg in segments:
            if seg.media is not None:
                items.append(seg.media)
                continue
            runs = merge_runs(seg.runs)
            if not runs_text(runs).strip():
                continue
            item = TextItem(
                runs=runs,
                prov=self._prov(),
                issues=issues if first_text else [],
                align=align,
                style=self.styles.names.get(style_id or "", style_id),
            )
            if first_text and label is not None:
                item.list_label = label.text
                item.list_level = label.level
                if not label.is_bullet:
                    self._auto_numbered += 1
                if not label.supported:
                    item.issues.append(
                        Issue(
                            code="docx_numbering_format",
                            severity="warning",
                            message="Formato de numeração do Word pouco comum: confira o número.",
                        )
                    )
            first_text = False
            items.append(item)

        if label is not None and not label.is_bullet and not any(isinstance(i, TextItem) for i in items):
            # A numbered paragraph without text (e.g. the number followed only by an image).
            items.insert(0, TextItem(runs=[], prov=self._prov(), list_label=label.text, issues=issues))
        if not items and not extra_items:
            return [_EmptyParagraph(self._prov())]  # type: ignore[list-item]
        return items + extra_items

    def _collect(self, el, part, style_id, segments: list[_Segment], extra: list[Item], issues: list[Issue]):
        for child in el.iterchildren():
            tag = child.tag
            if tag == qn("w:r"):
                self._run(child, part, style_id, segments, extra, issues)
            elif tag in (qn("w:hyperlink"), qn("w:smartTag"), qn("w:ins"), qn("w:customXml"), qn("w:fldSimple"),
                         qn("w:moveTo")):
                self._collect(child, part, style_id, segments, extra, issues)
            elif tag == qn("w:sdt"):
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    self._collect(content, part, style_id, segments, extra, issues)
            elif tag in (_q("m:oMath"), _q("m:oMathPara")):
                runs, unsupported = omml_to_runs(child)
                segments[-1].runs.extend(runs)
                issues.append(
                    Issue(
                        code="docx_equation",
                        severity="warning",
                        message=(
                            "Esta questão tinha uma fórmula do Word, convertida para texto linear "
                            f"(“{runs_text(runs)[:60]}”). Confira; se ficou diferente, recorte do original."
                        ),
                        detail={"unsupported": unsupported} if unsupported else None,
                    )
                )
            elif tag == _q("mc:AlternateContent"):
                chosen = child.find(_q("mc:Choice"))
                if chosen is None:
                    chosen = child.find(_q("mc:Fallback"))
                if chosen is not None:
                    self._collect(chosen, part, style_id, segments, extra, issues)
            # w:del, w:moveFrom, bookmarks, proofing marks and properties are ignored on purpose.

    def _run(self, r_el, part, style_id, segments: list[_Segment], extra: list[Item], issues: list[Issue]):
        direct = _read_rpr(r_el.find(qn("w:rPr")))
        props = self.styles.effective(style_id, direct)
        if props.get("vanish"):
            return  # hidden text (e.g. an answer key) is not visible in the original
        style = {
            "bold": bool(props.get("bold")),
            "italic": bool(props.get("italic")),
            "underline": bool(props.get("underline")),
            "sup": bool(props.get("sup")),
            "sub": bool(props.get("sub")),
        }
        caps = bool(props.get("caps"))

        def add_text(text: str) -> None:
            if text:
                segments[-1].runs.append(Run(text=text.upper() if caps else text, **style))

        for child in r_el.iterchildren():
            tag = child.tag
            if tag == qn("w:t"):
                add_text(child.text or "")
            elif tag == qn("w:tab"):
                add_text("\t")
            elif tag in (qn("w:br"), qn("w:cr")):
                if child.get(qn("w:type")) == "page":
                    self._ignored_page_breaks += 1
                elif child.get(qn("w:type")) != "column":
                    add_text("\n")
            elif tag == qn("w:noBreakHyphen"):
                add_text("-")
            elif tag == qn("w:sym"):
                code = (child.get(qn("w:char")) or "").upper()
                symbol = _SYMBOL_FONT_MAP.get(code)
                if symbol is None:
                    try:
                        value = int(code, 16)
                        symbol = chr(value) if value < 0xE000 or value > 0xF8FF else "□"
                    except ValueError:
                        symbol = "□"
                    issues.append(
                        Issue(
                            code="docx_symbol",
                            severity="warning",
                            message="Havia um símbolo especial do Word; confira se ele aparece corretamente.",
                        )
                    )
                add_text(symbol)
            elif tag == qn("w:drawing"):
                self._drawing(child, part, segments, extra)
            elif tag == qn("w:pict"):
                self._vml(child, part, segments, extra, kind="pict")
            elif tag == qn("w:object"):
                self._vml(child, part, segments, extra, kind="object")
            elif tag == _q("mc:AlternateContent"):
                chosen = child.find(_q("mc:Choice"))
                if chosen is None:
                    chosen = child.find(_q("mc:Fallback"))
                if chosen is not None:
                    fake_run = chosen  # contains w:drawing / w:pict directly
                    for sub in fake_run.iterchildren():
                        if sub.tag == qn("w:drawing"):
                            self._drawing(sub, part, segments, extra)
                        elif sub.tag == qn("w:pict"):
                            self._vml(sub, part, segments, extra, kind="pict")

    # ------------------------------------------------------------------ media
    def _push_media(self, segments: list[_Segment], media: MediaItem) -> None:
        segments.append(_Segment(media=media))
        segments.append(_Segment())

    def _image_from_rel(self, part, rid: str | None) -> str | None:
        if not rid:
            return None
        try:
            image_part = part.related_parts[rid]
        except KeyError:
            return None
        blob = getattr(image_part, "blob", None)
        if not blob:
            return None
        try:
            return self.save_asset(blob)
        except Exception:
            log.info("docx: unsupported image format (%s)", getattr(image_part, "content_type", "?"))
            return None

    def _placeholder(self, message: str, code: str) -> MediaItem:
        return MediaItem(
            kind="crop",
            prov=self._prov(),
            asset_id=None,
            issues=[Issue(code=code, severity="error", message=message)],
        )

    def _drawing(self, drawing, part, segments: list[_Segment], extra: list[Item]) -> None:
        container = drawing.find(_q("wp:inline"))
        floating = False
        if container is None:
            container = drawing.find(_q("wp:anchor"))
            floating = container is not None
        if container is None:
            return
        extent = container.find(_q("wp:extent"))
        width_cm = None
        if extent is not None and extent.get("cx"):
            width_cm = round(int(extent.get("cx")) / EMU_PER_CM, 2)

        graphic_data = container.find(f"{_q('a:graphic')}/{_q('a:graphicData')}")
        uri = graphic_data.get("uri", "") if graphic_data is not None else ""
        blips = list(container.iter(_q("a:blip")))
        textboxes = list(container.iter(qn("w:txbxContent")))

        added = False
        for blip in blips:
            rid = blip.get(_R_EMBED)
            if rid is None and blip.get(_R_LINK):
                self._push_media(
                    segments,
                    self._placeholder(
                        "Esta imagem estava apenas vinculada (fora do arquivo) e não pôde ser lida. "
                        "Recorte do original ou envie a imagem.",
                        "docx_linked_image",
                    ),
                )
                added = True
                continue
            asset = self._image_from_rel(part, rid)
            if asset is None:
                self._push_media(
                    segments,
                    self._placeholder(
                        "Uma imagem do Word está num formato que não conseguimos converter (ex.: EMF/WMF). "
                        "Use “Recortar do original” para preservá-la.",
                        "docx_image_format",
                    ),
                )
            else:
                media = MediaItem(kind="image", prov=self._prov(), asset_id=asset,
                                  width_cm=width_cm if len(blips) == 1 else None)
                if floating:
                    media.issues.append(
                        Issue(
                            code="docx_floating_image",
                            severity="info",
                            message="A imagem estava solta (flutuante) no Word; agora fica na linha do texto.",
                        )
                    )
                self._push_media(segments, media)
            added = True

        for txbx in textboxes:
            inner = self._walk_container(txbx, part)
            for item in inner:
                if isinstance(item, TextItem):
                    item.issues.append(
                        Issue(
                            code="docx_textbox",
                            severity="warning",
                            message="Este texto estava dentro de uma caixa de texto do Word; confira a posição.",
                        )
                    )
            extra.extend(i for i in inner if not isinstance(i, _EmptyParagraph))
            added = added or bool(inner)

        if not added:
            if "chart" in uri:
                msg = "Havia um gráfico do Word que não pode ser convertido. Use “Recortar do original”."
                code = "docx_chart"
            elif "diagram" in uri:
                msg = "Havia um diagrama (SmartArt) que não pode ser convertido. Use “Recortar do original”."
                code = "docx_smartart"
            else:
                msg = "Havia um desenho ou forma do Word que não pode ser convertido. Use “Recortar do original”."
                code = "docx_shape"
            self._push_media(segments, self._placeholder(msg, code))

    def _vml(self, el, part, segments: list[_Segment], extra: list[Item], kind: str) -> None:
        image_data = list(el.iter(_q("v:imagedata")))
        textboxes = list(el.iter(qn("w:txbxContent")))
        for data in image_data:
            asset = self._image_from_rel(part, data.get(_R_ID))
            if asset is None:
                self._push_media(
                    segments,
                    self._placeholder(
                        "Havia uma equação ou objeto antigo do Word que não pôde ser convertido. "
                        "Use “Recortar do original”.",
                        "docx_ole_object",
                    ),
                )
                continue
            media = MediaItem(kind="image", prov=self._prov(), asset_id=asset)
            if kind == "object":
                media.issues.append(
                    Issue(
                        code="docx_ole_preview",
                        severity="warning",
                        message="Objeto antigo do Word (ex.: equação) preservado como imagem. Confira a nitidez.",
                    )
                )
            self._push_media(segments, media)
        for txbx in textboxes:
            inner = self._walk_container(txbx, part)
            extra.extend(i for i in inner if not isinstance(i, _EmptyParagraph))
        if not image_data and not textboxes and kind == "object":
            self._push_media(
                segments,
                self._placeholder("Objeto incorporado não suportado. Use “Recortar do original”.", "docx_ole_object"),
            )

    # ------------------------------------------------------------------ tables
    def _table(self, tbl, part) -> list[Item]:
        rows: list[list[TableCell]] = []
        nested = False
        for tr in tbl.findall(qn("w:tr")):
            row: list[TableCell] = []
            for tc in tr.findall(qn("w:tc")):
                tcpr = tc.find(qn("w:tcPr"))
                colspan = 1
                vmerge = None
                if tcpr is not None:
                    if (gs := tcpr.find(qn("w:gridSpan"))) is not None:
                        colspan = int(gs.get(qn("w:val"), "1"))
                    if (vm := tcpr.find(qn("w:vMerge"))) is not None:
                        vmerge = "restart" if vm.get(qn("w:val")) == "restart" else "continue"
                content: list[CellContent] = []
                for inner in self._walk_container(tc, part):
                    if isinstance(inner, TextItem):
                        runs = list(inner.runs)
                        if inner.list_label:
                            runs.insert(0, Run(text=f"{inner.list_label} "))
                        content.append(CellContent(runs=runs))
                    elif isinstance(inner, MediaItem) and inner.kind == "image":
                        content.append(CellContent(asset_id=inner.asset_id, width_cm=inner.width_cm))
                    elif isinstance(inner, MediaItem) and inner.kind == "table":
                        nested = True
                        for nrow in inner.rows or []:
                            text = " | ".join(
                                runs_text(c.runs) for cell in nrow for c in cell.content if c.runs
                            )
                            content.append(CellContent(runs=[Run(text=text)]))
                row.append(TableCell(content=content, colspan=colspan, vmerge=vmerge))
            if row:
                rows.append(row)
        if not rows:
            return []

        # A single-cell table is usually a decorative box around content: unwrap it.
        if len(rows) == 1 and len(rows[0]) == 1:
            tc = tbl.find(f"{qn('w:tr')}/{qn('w:tc')}")
            inner_items = self._walk_container(tc, part) if tc is not None else []
            for item in inner_items:
                if isinstance(item, TextItem):
                    item.issues.append(
                        Issue(
                            code="docx_box_unwrapped",
                            severity="info",
                            message="Este trecho estava dentro de uma caixa (tabela de uma célula) no Word.",
                        )
                    )
                    break
            return inner_items

        media = MediaItem(kind="table", prov=self._prov(), rows=rows)
        if nested:
            media.issues.append(
                Issue(
                    code="docx_nested_table",
                    severity="warning",
                    message="Havia uma tabela dentro de outra; o conteúdo interno virou texto. Confira.",
                )
            )
        return [media]

    # ------------------------------------------------------------ answer lines
    def _collapse_answer_lines(self, items: list[Item]) -> list[Item]:
        """Turn runs of underscore-only paragraphs or empty paragraphs into answer space markers."""
        out: list[Item] = []
        empty_run = 0
        for item in items:
            if isinstance(item, _EmptyParagraph):
                empty_run += 1
                continue
            if empty_run >= 3:
                out.append(AnswerLinesItem(count=empty_run, prov=self._prov(), blank=True))
            empty_run = 0
            if isinstance(item, TextItem) and not item.list_label and _UNDERSCORE_LINE.match(item.text):
                lines = max(1, item.text.count("\n") + 1)
                if out and isinstance(out[-1], AnswerLinesItem) and not out[-1].blank:
                    out[-1].count += lines
                else:
                    out.append(AnswerLinesItem(count=lines, prov=self._prov()))
                continue
            out.append(item)
        if empty_run >= 3:
            out.append(AnswerLinesItem(count=empty_run, prov=self._prov(), blank=True))
        return out


class _EmptyParagraph:
    """Marker for an empty paragraph; consumed by ``_collapse_answer_lines``."""

    def __init__(self, prov: Provenance):
        self.prov = prov

