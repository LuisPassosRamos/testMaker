"""Build the print-ready Word document (python-docx) from the structured exam.

This is the single layout engine: the PDF is a conversion of this DOCX and the
on-screen preview is that PDF, so what the teacher checks is what gets printed.

The look follows the school's own formatted exams (see docs/ARCHITECTURE.md, "Padrão
da escola"): a framed header with the logo and the identification lines, a centred
title such as "Avaliação II Trimestre - Português 2º ano", bold question numbers,
short alternatives side by side, continuous answer lines, a thin frame around the
page and the page number in the bottom-right corner. The document carries named
paragraph styles ("Enunciado", "Alternativa", "Texto da prova"…) so the Word file
the print room receives can be adjusted by hand like the ones the school makes.

Pagination relies on Word paragraph rules that both Word and LibreOffice honour:
"keep with next" chains a question's number, statement, images and alternatives;
"keep lines together" avoids splitting a paragraph; table rows never split.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from app.domain.models import DOC_TYPE_LABELS, AnswerSpace, Block, Exam, Part, Run, TableCell, runs_text
from app.rendering.fonts import text_width_cm
from app.templates_store import HeaderTemplate

_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
COLUMN_GAP_CM = 0.8
ALIGN = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
         "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT}

# School standard measurements (taken from the school's formatted exams).
ALT_INDENT_CM = 0.4
LOGO_CELL_CM = 2.6
LOGO_WIDTH_CM = 1.75
IDENT_FONT = "Times New Roman"
IDENT_SIZE = 10.5
TITLE_SIZE = 14
IDENT_TAB_CM = 7.5  # second item on an identification line ("DATA: …    TURMA: …")

PERIODO_LABELS = {"unidade": "Unidade", "trimestre": "Trimestre", "periodo": "Período"}
MODALIDADE_WORDS = {"final": "Final", "parcial": "Parcial", "recuperacao": "de Recuperação",
                    "segunda_chamada": "de Segunda Chamada"}
# Lines such as "Fonte: …", "Disponível em: …" or "Texto: Tudo Sala de Aula, 2021" credit a support text.
_SOURCE_LINE = re.compile(
    r"^\s*\(?(fonte\s*:|texto\s*:\s*\S|dispon[íi]vel\s+(em|no|na)\b|adaptado\s+de\b|extra[íi]do\s+de\b|adaptado\)?\s*\.?$)",
    re.IGNORECASE,
)


def header_title(exam: Exam) -> str:
    """Title in the school's format, e.g. "Avaliação Final III Trimestre - Matemática 6º ano"."""
    meta = exam.meta
    if meta.title.strip():
        return meta.title.strip()
    if meta.doc_type == "prova":
        words = ["Simulado" if meta.modalidade == "simulado" else "Avaliação"]
        if meta.modalidade in MODALIDADE_WORDS:
            words.append(MODALIDADE_WORDS[meta.modalidade])
    else:
        words = [DOC_TYPE_LABELS.get(meta.doc_type, "Atividade")]
    unidade = meta.unidade.strip()
    if unidade:
        has_word = re.search(r"unidade|trimestre|bimestre|per[íi]odo|semestre", unidade, re.IGNORECASE)
        words.append(unidade if has_word else f"{unidade} {PERIODO_LABELS.get(meta.periodo, 'Unidade')}")
    left = " ".join(words)
    right = " ".join(x for x in (meta.disciplina.strip(), meta.ano.strip()) if x)
    title = f"{left} - {right}" if right else left
    # 1st grade children read in capital letters: the school's 1st-grade exams use an all-caps title.
    if re.match(r"^\s*1\s*[ºo°]", meta.ano):
        title = title.upper()
    return title


def safe_filename(exam: Exam, ext: str) -> str:
    name = f"{header_title(exam)} {exam.meta.turma.strip()}".strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name)[:100].strip() or "Prova"
    return f"{name}.{ext}"


# ----------------------------------------------------------------- xml helpers


def _border_el(tag: str, val: str = "single", sz: int = 4, color: str = "000000", space: int = 0) -> OxmlElement:
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:val"), val)
    el.set(qn("w:sz"), str(sz))
    el.set(qn("w:space"), str(space))
    el.set(qn("w:color"), color)
    return el


def _table_borders(table, val: str = "single", sz: int = 4, color: str = "000000",
                   inside: bool = True) -> None:
    tbl_pr = table._tbl.tblPr
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for tag in ("top", "left", "bottom", "right"):
        borders.append(_border_el(tag, val, sz, color))
    for tag in ("insideH", "insideV"):
        borders.append(_border_el(tag, val if inside else "nil", sz if inside else 0, color))
    tbl_pr.append(borders)


def _cell_border(cell, side: str, sz: int, color: str = "000000") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    borders.append(_border_el(side, "single", sz, color))


def _cell_margins(cell, top: float, left: float, bottom: float, right: float) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for side, cm in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(int(cm * 567)))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.append(mar)


def _table_indent(table, cm: float) -> None:
    tbl_pr = table._tbl.tblPr
    ind = OxmlElement("w:tblInd")
    ind.set(qn("w:w"), str(int(cm * 567)))
    ind.set(qn("w:type"), "dxa")
    tbl_pr.append(ind)


def _fixed_layout(table) -> None:
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)


def _set_widths(table, widths_cm: list[float]) -> None:
    """Set column widths in a way both Word and LibreOffice respect (grid + every cell)."""
    for column, width in zip(table.columns, widths_cm, strict=False):
        column.width = Cm(width)
    for row in table.rows:
        for cell, width in zip(row.cells, widths_cm, strict=False):
            cell.width = Cm(width)


def _cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))


def _add_field(paragraph, instr: str, size: float) -> None:
    def run_with(child):
        run = paragraph.add_run()
        run.font.size = Pt(size)
        run._r.append(child)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run_with(begin)
    text = OxmlElement("w:instrText")
    text.set(qn("xml:space"), "preserve")
    text.text = f" {instr} "
    run_with(text)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run_with(sep)
    placeholder = paragraph.add_run("1")
    placeholder.font.size = Pt(size)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run_with(end)


def _set_columns(section, count: int, gap_cm: float, separator: bool) -> None:
    sect_pr = section._sectPr
    cols = sect_pr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        sect_pr.append(cols)
    cols.set(qn("w:num"), str(count))
    cols.set(qn("w:space"), str(int(gap_cm * 567)))
    if count > 1 and separator:
        cols.set(qn("w:sep"), "1")


def _page_border(section) -> None:
    """Thin frame around the page, 24 pt from the paper edge (school standard)."""
    sect_pr = section._sectPr
    for old in sect_pr.findall(qn("w:pgBorders")):
        sect_pr.remove(old)
    borders = OxmlElement("w:pgBorders")
    borders.set(qn("w:offsetFrom"), "page")
    for side in ("top", "left", "bottom", "right"):
        borders.append(_border_el(side, "single", 9, "000000", space=24))
    # Schema order: … pgSz, pgMar, paperSrc, pgBorders, lnNumType, pgNumType, cols …
    anchor = sect_pr.find(qn("w:paperSrc"))
    if anchor is None:
        anchor = sect_pr.find(qn("w:pgMar"))
    if anchor is not None:
        anchor.addnext(borders)
    else:
        sect_pr.append(borders)


def _set_style_font(style, family: str) -> None:
    style.font.name = family
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), family)


# pPr children that the schema places after pBdr.
_AFTER_PBDR = {qn(f"w:{t}") for t in (
    "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
    "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
    "suppressOverlap", "jc", "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId",
    "cnfStyle", "rPr", "sectPr", "pPrChange")}


def _answer_line_borders(paragraph) -> None:
    """Bottom + "between" borders: consecutive answer lines draw one rule per line, edge to edge."""
    p_pr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bdr.append(_border_el("bottom", "single", 4))
    bdr.append(_border_el("between", "single", 4))
    anchor = next((child for child in p_pr if child.tag in _AFTER_PBDR), None)
    if anchor is not None:
        anchor.addprevious(bdr)
    else:
        p_pr.append(bdr)


# ---------------------------------------------------------------------- writer


class DocxRenderer:
    def __init__(self, exam: Exam, template: HeaderTemplate | None, assets_dir: Path, logo_path: Path | None,
                 work_dir: Path):
        self.exam = exam
        self.layout = exam.layout
        self.template = template
        self.assets_dir = assets_dir
        self.logo_path = logo_path
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.doc = Document()
        margins = self.layout.margins_cm
        self.text_width = 21.0 - margins.left - margins.right
        self.text_height = 29.7 - margins.top - margins.bottom
        cols = self.layout.columns
        self.column_width = (self.text_width - COLUMN_GAP_CM) / 2 if cols == 2 else self.text_width
        # Layout notes for the checks panel (e.g. an image reduced to fit the column).
        self.notes: list[tuple[str, str]] = []  # (block id, message)
        self._block: Block | None = None

    # ------------------------------------------------------------------ setup
    def _setup(self) -> None:
        section = self.doc.sections[0]
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        m = self.layout.margins_cm
        section.top_margin, section.bottom_margin = Cm(m.top), Cm(m.bottom)
        section.left_margin, section.right_margin = Cm(m.left), Cm(m.right)
        section.footer_distance = Cm(max(0.5, min(1.0, m.bottom - 0.6)))
        section.header_distance = Cm(1.0)

        size = self.layout.font_size
        normal = self.doc.styles["Normal"]
        _set_style_font(normal, self.layout.font_family)
        normal.font.size = Pt(size)
        normal.font.color.rgb = RGBColor(0, 0, 0)
        pf = normal.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(3)
        pf.line_spacing = self.layout.line_spacing
        pf.widow_control = True

        small = max(9.0, size - 2)
        self._add_style("Identificação", font=IDENT_FONT, size=IDENT_SIZE, after=0, line=1.1)
        self._add_style("Título da avaliação", size=max(TITLE_SIZE, size + 2), bold=True, before=10, after=10,
                        align=WD_ALIGN_PARAGRAPH.CENTER, keep_next=True)
        self._add_style("Enunciado", before=self.layout.question_spacing_pt, after=3, keep_next=True)
        self._add_style("Alternativa", after=1, indent=ALT_INDENT_CM)
        self._add_style("Texto da prova", after=4)
        self._add_style("Figura da prova", before=2, after=4, align=WD_ALIGN_PARAGRAPH.CENTER)
        self._add_style("Fonte do estímulo", size=small, italic=True, after=4)
        self._add_style("Rótulo de resposta", size=small, italic=True, before=2, after=0, keep_next=True)
        self._add_style("Linha de resposta", after=0)
        self._add_style("Orientações", after=1)
        self._add_style("Mensagem final", bold=True, before=14, after=0)

        props = self.doc.core_properties
        props.title = header_title(self.exam)
        props.author = "Prova Pronta"
        props.comments = ""

    def _add_style(self, name: str, *, font: str | None = None, size: float | None = None, bold: bool = False,
                   italic: bool = False, before: float = 0, after: float = 0, line: float | None = None,
                   indent: float = 0, align=None, keep_next: bool = False) -> None:
        style = self.doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = self.doc.styles["Normal"]
        style.quick_style = True
        if font:
            _set_style_font(style, font)
        if size:
            style.font.size = Pt(size)
        if bold:
            style.font.bold = True
        if italic:
            style.font.italic = True
        pf = style.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        if line:
            pf.line_spacing = line
        if indent:
            pf.left_indent = Cm(indent)
        if align is not None:
            pf.alignment = align
        if keep_next:
            pf.keep_with_next = True

    # --------------------------------------------------------------- helpers
    def _image_path(self, asset_id: str | None) -> Path | None:
        if not asset_id:
            return None
        path = self.assets_dir / asset_id
        if not path.exists():
            return None
        if not self.layout.grayscale:
            return path
        gray = self.work_dir / f"gray-{path.stem}.png"
        if not gray.exists():
            with Image.open(path) as img:
                img.convert("L").save(gray, format="PNG")
        return gray

    def _fit_width(self, path: Path, wanted_cm: float, max_width_cm: float) -> float:
        width = min(wanted_cm, max_width_cm)
        with Image.open(path) as img:
            ratio = img.height / max(1, img.width)
        max_height = self.text_height * 0.7
        if width * ratio > max_height:
            width = max_height / ratio
        return max(1.0, width)

    def _add_runs(self, paragraph, runs: list[Run], size: float | None = None, bold: bool = False) -> None:
        for r in runs:
            text = _XML_INVALID.sub("", r.text)
            if not text:
                continue
            run = paragraph.add_run(text)
            if r.bold or bold:
                run.bold = True
            if r.italic:
                run.italic = True
            if r.underline:
                run.underline = True
            if r.sup:
                run.font.superscript = True
            if r.sub:
                run.font.subscript = True
            if size:
                run.font.size = Pt(size)

    def _paragraph(self, style: str = "Texto da prova", *, indent: float = 0.0, align: str | None = None):
        p = self.doc.add_paragraph(style=style)
        pf = p.paragraph_format
        if indent:
            pf.left_indent = Cm(indent)
        pf.keep_together = True
        if align in ALIGN:
            p.alignment = ALIGN[align]
        elif self.layout.justify_text and style in ("Texto da prova", "Enunciado"):
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        return p

    # ------------------------------------------------------------------ header
    def _logo_source(self) -> Path | None:
        t = self.template
        if t is None or not t.show_logo or not self.logo_path or not self.logo_path.exists():
            return None
        if not self.layout.grayscale:
            return self.logo_path
        source = self.work_dir / "logo-gray.png"
        with Image.open(self.logo_path) as img:
            img.convert("LA" if img.mode in ("RGBA", "LA", "P") else "L").save(source, format="PNG")
        return source

    def _ident_lines(self) -> list[list[tuple[str, str, bool]]]:
        """Identification lines in the school's order: (label, value, fill-to-the-end)."""
        t = self.template
        assert t is not None
        meta = self.exam.meta
        year = date.today().year
        blank = "______"
        turma = meta.turma.strip()
        ano = meta.ano.strip()
        unidade = meta.unidade.strip()
        if unidade and not re.search(r"unidade|trimestre|bimestre|per[íi]odo", unidade, re.I):
            unidade = f"{unidade} {PERIODO_LABELS.get(meta.periodo, 'Unidade')}"
        values: dict[str, tuple[str, bool]] = {
            "aluno": ("", True),
            "numero": ("____", False),
            "data": (meta.data.strip() or f"______/______/{year}", False),
            "ano_turma": (f"{ano} {turma or blank}".strip() if ano else (turma or blank), False),
            "professor": (meta.professor.strip() or "", not meta.professor.strip()),
            "disciplina": (meta.disciplina.strip() or "", not meta.disciplina.strip()),
            "turno": (meta.turno.strip() or blank, False),
            "unidade": (unidade or blank, False),
            "nota": (blank, False),
            "valor": (meta.valor.strip() or blank, False),
            "data_entrega": (meta.data_entrega.strip() or f"______/______/{year}", False),
        }
        applicable = {"nota": meta.doc_type == "prova", "valor": meta.doc_type == "prova",
                      "data_entrega": meta.doc_type == "atividade"}
        fields = [f for f in t.fields if f.enabled and applicable.get(f.key, True)]
        by_key = {f.key: f for f in fields}

        def item(key: str) -> tuple[str, str, bool]:
            value, fill = values[key]
            return by_key[key].label, value, fill

        lines: list[list[tuple[str, str, bool]]] = []
        used: set[str] = set()

        def take(*keys: str) -> None:
            present = [k for k in keys if k in by_key and k not in used]
            if present:
                lines.append([item(k) for k in present])
                used.update(present)

        take("aluno", "numero")
        take("data", "ano_turma")
        take("professor")
        rest = [f.key for f in fields if f.key not in used]
        for i in range(0, len(rest), 2):
            take(*rest[i:i + 2])
        return lines

    def _render_template_header(self) -> None:
        t = self.template
        if t is None:
            return
        logo = self._logo_source()
        school = [x.strip() for x in [t.school_name, *t.school_lines] if x.strip()]
        lines = self._ident_lines()
        if logo or school or lines:
            table = self.doc.add_table(rows=1, cols=2 if logo else 1)
            _table_borders(table, "single", 12, inside=False)
            _fixed_layout(table)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            text_width = self.text_width - (LOGO_CELL_CM if logo else 0)
            _set_widths(table, [LOGO_CELL_CM, text_width] if logo else [text_width])
            _cant_split(table.rows[0])
            text_cell = table.rows[0].cells[-1]
            _cell_margins(text_cell, 0.18, 0.38, 0.18, 0.2)
            text_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if logo:
                logo_cell = table.rows[0].cells[0]
                _cell_border(logo_cell, "right", 4)
                _cell_margins(logo_cell, 0.18, 0.11, 0.18, 0.11)
                logo_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                # The school frames the logo in its own small box.
                frame = logo_cell.add_table(rows=1, cols=1)
                _table_borders(frame, "single", 8)
                _fixed_layout(frame)
                frame.alignment = WD_TABLE_ALIGNMENT.CENTER
                _set_widths(frame, [LOGO_CELL_CM - 0.5])
                inner = frame.rows[0].cells[0]
                _cell_margins(inner, 0.1, 0.1, 0.1, 0.1)
                p = inner.paragraphs[0]
                p.style = self.doc.styles["Identificação"]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(str(logo), width=Cm(LOGO_WIDTH_CM))
                # The cell keeps an empty paragraph before and after the frame (Word requires the
                # last one); make them invisible so the logo box sits in the middle.
                for spacer in logo_cell.paragraphs:
                    spacer.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                    spacer.paragraph_format.line_spacing = Pt(1)
                    spacer.paragraph_format.space_after = Pt(0)
            first = True
            for text in school:
                p = text_cell.paragraphs[0] if first else text_cell.add_paragraph()
                first = False
                p.style = self.doc.styles["Identificação"]
                p.add_run(_XML_INVALID.sub("", text.upper()))
            for line in lines:
                p = text_cell.paragraphs[0] if first else text_cell.add_paragraph()
                first = False
                p.style = self.doc.styles["Identificação"]
                self._ident_line(p, line, text_width)

        if t.show_title:
            p = self.doc.add_paragraph(style="Título da avaliação")
            p.add_run(_XML_INVALID.sub("", header_title(self.exam)))

    def _ident_line(self, p, line: list[tuple[str, str, bool]], cell_width: float) -> None:
        """One identification line, e.g. "DATA: ____/____/2026    TURMA: 2º ANO ____".

        Blank fields that the student fills in ("ALUNO(A):") get a row of underscores up to the
        next item or to the edge of the box — real characters, like the school types them, so
        Word and LibreOffice print exactly the same thing.
        """
        edge = cell_width - 0.8
        texts = [_XML_INVALID.sub("", f"{label.upper()}: {value.upper()}".rstrip()) for label, value, _ in line]
        widths = [text_width_cm(t + " ", IDENT_FONT, IDENT_SIZE) for t in texts]
        underscore = text_width_cm("_" * 10, IDENT_FONT, IDENT_SIZE) / 10
        if len(line) > 1:
            if line[0][2]:
                # "ALUNO(A): ________  Nº: ____" — the second item sits at the right end.
                split = max(4.0, edge - widths[1] - 0.2)
            else:
                split = min(IDENT_TAB_CM, edge - 4)
        else:
            split = edge
        x = 0.0
        for i, ((_, _, fill), text) in enumerate(zip(line, texts, strict=True)):
            if i and not line[i - 1][2]:
                p.paragraph_format.tab_stops.add_tab_stop(Cm(split))
                p.add_run("\t")
                x = split
            elif i:
                p.add_run(" ")
            if fill:
                end = split if i == 0 and len(line) > 1 else edge
                count = max(4, int((end - x - widths[i]) / underscore * 0.97))
                text = f"{text} {'_' * count}"
                x = end
            else:
                x += widths[i]
            p.add_run(text)

    # ------------------------------------------------------------------- body
    def render(self, out_path: Path) -> Path:
        self._setup()
        mode = self.exam.header.mode
        if mode in ("template", "both"):
            self._render_template_header()
        blocks = [b for b in self.exam.blocks if b.kind != "original_header" or mode in ("original", "both")]
        if self.layout.columns == 2:
            # The header stays full width; the questions flow in two columns.
            section = self.doc.add_section(WD_SECTION.CONTINUOUS)
            _set_columns(section, 2, COLUMN_GAP_CM, self.layout.column_separator)
        first = True
        for block in blocks:
            self._render_block(block, first)
            first = False
        self._closing_message()
        if self.layout.page_border:
            for section in self.doc.sections:
                _page_border(section)
        self._footer()
        self.doc.save(out_path)
        return out_path

    def _closing_message(self) -> None:
        lines = [x.strip() for x in self.exam.meta.mensagem_final.splitlines() if x.strip()]
        for i, line in enumerate(lines):
            p = self.doc.add_paragraph(style="Mensagem final")
            p.paragraph_format.keep_together = True
            if i:
                p.paragraph_format.space_before = Pt(0)
            if i < len(lines) - 1:
                p.paragraph_format.keep_with_next = True
            p.add_run(_XML_INVALID.sub("", line))

    def _block_label(self) -> str:
        block = self._block
        if block is None:
            return "o documento"
        if block.kind == "question":
            return f"a questão {block.number or ''}".strip()
        return "o texto de apoio" if block.kind == "support_text" else "um trecho"

    def _render_block(self, block: Block, first: bool) -> None:
        self._block = block
        if block.kind == "page_break":
            p = self.doc.add_paragraph()
            p.add_run().add_break(WD_BREAK.PAGE)
            return
        units: list = []  # paragraphs in reading order, used for pagination rules
        is_question = block.kind == "question"
        label = (block.number or "").strip() if is_question else ""
        text_style = "Orientações" if block.kind == "instructions" else "Texto da prova"

        parts = list(block.parts)
        if is_question and label:
            first_part = parts[0] if parts and parts[0].kind == "paragraph" else None
            p = self._paragraph("Enunciado", align=first_part.align if first_part else None)
            run = p.add_run(label)
            run.bold = self.layout.bold_question_numbers
            p.add_run(" ")
            if first_part is not None:
                self._add_runs(p, first_part.runs)
                parts = parts[1:]
            units.append(p)

        i = 0
        while i < len(parts):
            part = parts[i]
            if part.kind == "alternative":
                group = [part]
                while i + 1 < len(parts) and parts[i + 1].kind == "alternative":
                    i += 1
                    group.append(parts[i])
                units.extend(self._alternatives(group, block))
            else:
                units.extend(self._render_part(part, block, text_style))
            i += 1

        if block.answer_space.kind != "none":
            units.extend(self._answer_space(block.answer_space))

        if not units:
            return
        head = units[0]
        if first:
            head.paragraph_format.space_before = Pt(0)
        elif head.style.name != "Enunciado":
            head.paragraph_format.space_before = Pt(self.layout.question_spacing_pt)
        if block.page_break_before:
            head.paragraph_format.page_break_before = True

        # Pagination: keep the block together. A question that carries a long text of its own (or
        # that the teacher allowed to split) may break inside the text, but its number stays with the
        # first line and the last paragraph stays with the alternatives and answer lines.
        chained: set[int]
        if is_question and (block.allow_split or self._estimated_height_cm(block) > 0.45 * self.text_height):
            tail = next((i for i, u in enumerate(units) if getattr(u, "_is_alt", False) or getattr(u, "_is_answer", False)),
                        len(units) - 1)
            chained = {0} | set(range(max(0, tail - 1), len(units) - 1))
        elif block.kind == "support_text":
            chained = set(range(len(units) - 1)) if len(units) <= 6 else {0}
        else:
            chained = set(range(len(units) - 1))
        for index in chained:
            units[index].paragraph_format.keep_with_next = True
        if block.kind == "support_text":
            units[-1].paragraph_format.keep_with_next = True  # glue the text to the question that follows

    def _estimated_height_cm(self, block: Block) -> float:
        """Rough printed height of a block, used only to decide whether it may break across pages."""
        size = self.layout.font_size
        line = size * self.layout.line_spacing * 1.17 / 72 * 2.54
        total = 0.0
        for part in block.parts:
            if part.kind in ("paragraph", "alternative"):
                for piece in (runs_text(part.runs) or " ").split("\n"):
                    width = text_width_cm(piece, self.layout.font_family, size)
                    total += max(1, -(-width // max(1.0, self.column_width - 0.5))) * line
            elif part.kind in ("image", "crop") and part.asset_id:
                path = self.assets_dir / part.asset_id
                if path.exists():
                    with Image.open(path) as img:
                        ratio = img.height / max(1, img.width)
                    width = min(self.column_width, (part.width_cm or 10.0) * part.width_pct / 100)
                    total += min(width * ratio, self.text_height * 0.7)
            elif part.kind == "table" and part.rows:
                total += len(part.rows) * line * 1.3
            elif part.kind == "answer_space" and part.answer is not None:
                total += self._space_height_cm(part.answer)
        return total + self._space_height_cm(block.answer_space)

    def _space_height_cm(self, space: AnswerSpace) -> float:
        if space.kind == "lines":
            return space.lines * self.layout.answer_line_spacing_cm
        if space.kind in ("box", "blank"):
            return space.height_cm
        return 0.0

    def _render_part(self, part: Part, block: Block, text_style: str = "Texto da prova") -> list:
        if part.kind == "paragraph":
            text = runs_text(part.runs)
            style = "Fonte do estímulo" if len(text) < 220 and _SOURCE_LINE.match(text) else text_style
            p = self._paragraph(style, align=part.align)
            self._add_runs(p, part.runs)
            return [p]
        if part.kind == "alternative":
            return self._alternatives([part], block)
        if part.kind in ("image", "crop"):
            path = self._image_path(part.asset_id)
            if path is None:
                return []
            avail = self.column_width
            natural = part.width_cm or min(avail, 10.0)
            wanted = natural * part.width_pct / 100
            width = self._fit_width(path, wanted, avail)
            if part.width_cm and part.width_cm >= 5 and width < wanted * 0.7:
                what = "A tabela" if any(i.code == "table_as_crop" for i in part.issues) else "A imagem"
                self.notes.append((self._block.id if self._block else "", (
                    f"{what} em {self._block_label()} foi reduzida para {round(width / wanted * 100)}% para caber "
                    "na página/coluna. Confira se ficou legível (com 1 coluna ela fica maior).")))
            p = self._paragraph("Figura da prova", align="center")
            p.add_run().add_picture(str(path), width=Cm(width))
            return [p]
        if part.kind == "table" and part.rows:
            return self._table(part.rows, borders=part.table_borders)
        if part.kind == "answer_space" and part.answer is not None:
            return self._answer_space(part.answer)
        return []

    # ----------------------------------------------------------- alternatives
    def _alternative_text(self, alt: Part) -> str:
        return f"{(alt.label or '').strip()} {runs_text(alt.runs)}".strip()

    def _per_row(self, group: list[Part], block: Block) -> int:
        """How many alternatives go on each line: the school writes short ones side by side."""
        n = len(group)
        mode = block.alternatives_layout
        if n < 2 or mode == "list":
            return 1
        if mode == "row":
            return n
        if not self.layout.compact_alternatives:
            return 1
        texts = [self._alternative_text(a) for a in group]
        if any("\n" in t for t in texts):
            return 1
        avail = self.column_width - ALT_INDENT_CM
        size = self.layout.font_size
        # Bold runs are a little wider; measure the widest version to stay on the safe side.
        widest = max(text_width_cm(t, self.layout.font_family, size, bold=any(r.bold for r in a.runs))
                     for t, a in zip(texts, group, strict=True))
        gap = 0.6

        def fits(per_row: int) -> bool:
            return widest <= avail / per_row - gap

        if n == 4:
            # All four on one line only when very short ("A) 73   B) 229   C) 219   D) 291"); otherwise
            # the school writes them two by two ("A) …   B) …" / "C) …   D) …").
            if fits(4) and widest <= avail / 8:
                return 4
            return 2 if fits(2) else 1
        if n == 3:
            return 3 if fits(3) else 1  # never "a b" + "c" alone
        if n == 2:
            return 2 if fits(2) else 1
        if n == 5 and fits(5):
            return 5
        for per_row in (3, 2):
            if fits(per_row):
                return per_row
        return 1

    def _alternatives(self, group: list[Part], block: Block) -> list:
        per_row = self._per_row(group, block)
        units = []
        avail = self.column_width - ALT_INDENT_CM
        step = avail / per_row
        for start in range(0, len(group), per_row):
            row = group[start:start + per_row]
            p = self._paragraph("Alternativa")
            for k in range(1, per_row):
                p.paragraph_format.tab_stops.add_tab_stop(Cm(ALT_INDENT_CM + step * k))
            for k, alt in enumerate(row):
                if k:
                    p.add_run("\t")
                if alt.label:
                    p.add_run(f"{alt.label.strip()} ")
                self._add_runs(p, alt.runs)
            p._is_alt = True  # type: ignore[attr-defined]
            units.append(p)
        return units

    # ------------------------------------------------------------------ tables
    def _table(self, rows: list[list[TableCell]], borders: bool = True) -> list:
        ncols = max(sum(max(1, c.colspan) for c in row) for row in rows)
        table = self.doc.add_table(rows=len(rows), cols=ncols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        if borders:
            table.style = self.doc.styles["Table Grid"]
            _table_borders(table, "single", 4, "000000")
        else:
            _table_borders(table, "nil", 0)
        avail = self.column_width
        cell_width = avail / ncols
        _set_widths(table, [cell_width] * ncols)
        # Data tables get slightly smaller text; invisible layout tables (columns, poems) keep the body size.
        size = max(9.0, self.layout.font_size - 1) if borders else self.layout.font_size
        paragraphs: list = []
        for r, row in enumerate(rows):
            c = 0
            for cell_model in row:
                span = max(1, cell_model.colspan)
                if c >= ncols:
                    break
                cell = table.cell(r, c)
                if span > 1 and c + span - 1 < ncols:
                    try:
                        cell = cell.merge(table.cell(r, c + span - 1))
                    except Exception:
                        pass
                if cell_model.vmerge == "continue" and r > 0:
                    try:
                        table.cell(r - 1, c).merge(cell)
                    except Exception:
                        pass
                    c += span
                    continue
                first_paragraph = True
                for content in cell_model.content:
                    p = cell.paragraphs[0] if first_paragraph else cell.add_paragraph()
                    first_paragraph = False
                    p.paragraph_format.space_after = Pt(0)
                    if content.asset_id:
                        path = self._image_path(content.asset_id)
                        if path:
                            width = self._fit_width(path, content.width_cm or cell_width * span * 0.9,
                                                    cell_width * span * 0.9)
                            p.add_run().add_picture(str(path), width=Cm(width))
                    else:
                        self._add_runs(p, content.runs, size=size)
                c += span
            _cant_split(table.rows[r])
            for cell in table.rows[r].cells:
                paragraphs.extend(cell.paragraphs)
        last_row = set(id(p._p) for cell in table.rows[-1].cells for p in cell.paragraphs)
        for p in paragraphs:
            p.paragraph_format.keep_together = True
            if id(p._p) not in last_row:
                p.paragraph_format.keep_with_next = True
        tail = [p for p in paragraphs if id(p._p) in last_row]
        return tail[-1:] if tail else []

    # ----------------------------------------------------------------- answers
    def _answer_space(self, space: AnswerSpace) -> list:
        units = []
        if space.label.strip():
            p = self._paragraph("Rótulo de resposta")
            p.add_run(_XML_INVALID.sub("", space.label.strip()))
            p._is_answer = True  # type: ignore[attr-defined]
            units.append(p)
        if space.kind == "lines":
            spacing = Cm(self.layout.answer_line_spacing_cm)
            for _ in range(space.lines):
                p = self.doc.add_paragraph(style="Linha de resposta")
                pf = p.paragraph_format
                pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                pf.line_spacing = spacing
                pf.keep_together = True
                _answer_line_borders(p)
                p.add_run(" ")
                p._is_answer = True  # type: ignore[attr-defined]
                units.append(p)
        elif space.kind == "box":
            table = self.doc.add_table(rows=1, cols=1)
            _table_borders(table, "single", 6, "000000")
            row = table.rows[0]
            row.height = Cm(space.height_cm)
            row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
            _cant_split(row)
            row.cells[0].width = Cm(self.column_width)
            p = row.cells[0].paragraphs[0]
            units.append(p)
        elif space.kind == "blank":
            p = self.doc.add_paragraph()
            pf = p.paragraph_format
            pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            pf.line_spacing = Cm(space.height_cm)
            units.append(p)
        return units

    def _footer(self) -> None:
        footer = self.doc.sections[0].footer
        p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        text = self.layout.footer_text.strip()
        if text:
            run = p.add_run(_XML_INVALID.sub("", text))
            run.font.size = Pt(9)
        if self.layout.page_numbers:
            if text:
                p.paragraph_format.tab_stops.add_tab_stop(Cm(self.text_width), WD_TAB_ALIGNMENT.RIGHT)
                p.add_run("\t")
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _add_field(p, "PAGE", 9)
