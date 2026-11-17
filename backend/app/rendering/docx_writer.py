"""Build the print-ready Word document (python-docx) from the structured exam.

This is the single layout engine: the PDF is a conversion of this DOCX and the
on-screen preview is that PDF, so what the teacher checks is what gets printed.
Pagination relies on Word paragraph rules that both Word and LibreOffice honour:
"keep with next" chains a question's number, statement, images and alternatives;
"keep lines together" avoids splitting a paragraph; table rows never split.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from app.domain.models import DOC_TYPE_LABELS, AnswerSpace, Block, Exam, Part, Run, TableCell
from app.templates_store import HeaderTemplate

_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
TITLE_BY_TYPE = {"prova": "AVALIAÇÃO", "atividade": "ATIVIDADE", "revisao": "REVISÃO"}
COLUMN_GAP_CM = 0.8
ALIGN = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
         "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT}


def safe_filename(exam: Exam, ext: str) -> str:
    meta = exam.meta
    parts = [DOC_TYPE_LABELS.get(meta.doc_type, "Prova"), meta.disciplina, f"{meta.ano} {meta.turma}".strip()]
    name = " - ".join(p.strip() for p in parts if p and p.strip())
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name)[:100].strip() or "Prova"
    return f"{name}.{ext}"


def header_title(exam: Exam) -> str:
    meta = exam.meta
    if meta.title.strip():
        return meta.title.strip()
    title = TITLE_BY_TYPE.get(meta.doc_type, "AVALIAÇÃO")
    if meta.disciplina.strip():
        title += f" DE {meta.disciplina.strip().upper()}"
    unidade = meta.unidade.strip()
    if unidade:
        title += f" – {unidade.upper()}" if "UNIDADE" in unidade.upper() else f" – {unidade.upper()} UNIDADE"
    return title


# ----------------------------------------------------------------- xml helpers


def _border_el(tag: str, val: str = "single", sz: int = 4, color: str = "808080") -> OxmlElement:
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:val"), val)
    el.set(qn("w:sz"), str(sz))
    el.set(qn("w:space"), "0")
    el.set(qn("w:color"), color)
    return el


def _table_borders(table, val: str = "single", sz: int = 4, color: str = "808080") -> None:
    tbl_pr = table._tbl.tblPr
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for tag in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(_border_el(tag, val, sz, color))
    tbl_pr.append(borders)


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


def _set_run_font(run, family: str) -> None:
    run.font.name = family
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), family)


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
        section.footer_distance = Cm(max(0.5, m.bottom - 0.9))
        section.header_distance = Cm(0.6)

        normal = self.doc.styles["Normal"]
        normal.font.name = self.layout.font_family
        normal.font.size = Pt(self.layout.font_size)
        normal.font.color.rgb = RGBColor(0, 0, 0)
        rpr = normal.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), self.layout.font_family)
        pf = normal.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = self.layout.line_spacing
        pf.widow_control = True

        props = self.doc.core_properties
        props.title = header_title(self.exam)
        props.author = "Prova Pronta"
        props.comments = ""

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

    def _paragraph(self, *, indent: float = 0.0, hanging: float = 0.0, align: str | None = None):
        p = self.doc.add_paragraph()
        pf = p.paragraph_format
        if indent or hanging:
            pf.left_indent = Cm(indent + hanging)
            if hanging:
                pf.first_line_indent = Cm(-hanging)
                pf.tab_stops.add_tab_stop(Cm(indent + hanging))
        pf.keep_together = True
        if align in ALIGN:
            p.alignment = ALIGN[align]
        elif self.layout.justify_text:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        return p

    # ------------------------------------------------------------------ header
    def _render_template_header(self) -> None:
        t = self.template
        if t is None:
            return
        meta = self.exam.meta
        logo = self.logo_path if (t.show_logo and self.logo_path and self.logo_path.exists()) else None
        has_school = bool(t.school_name.strip() or any(line.strip() for line in t.school_lines))
        if logo or has_school:
            table = self.doc.add_table(rows=1, cols=2 if logo else 1)
            _table_borders(table, "nil", 0)
            _fixed_layout(table)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            text_cell = table.rows[0].cells[-1]
            if logo:
                _set_widths(table, [2.6, self.text_width - 2.6])
                logo_cell = table.rows[0].cells[0]
                p = logo_cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                source = logo
                if self.layout.grayscale:
                    source = self.work_dir / "logo-gray.png"
                    with Image.open(logo) as img:
                        img.convert("L").save(source, format="PNG")
                p.add_run().add_picture(str(source), width=Cm(1.9))
            p = text_cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if t.school_name.strip():
                run = p.add_run(t.school_name.strip())
                run.bold = True
                run.font.size = Pt(10.5)
            for line in t.school_lines:
                if not line.strip():
                    continue
                q = text_cell.add_paragraph()
                q.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = q.add_run(line.strip())
                run.font.size = Pt(8.5)

        if t.show_title:
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(4)
            run = p.add_run(header_title(self.exam))
            run.bold = True
            run.font.size = Pt(max(11.0, self.layout.font_size))

        values = {
            "aluno": "",
            "numero": "",
            "professor": meta.professor.strip(),
            "disciplina": meta.disciplina.strip(),
            "ano_turma": f"{meta.ano} {meta.turma}".strip(),
            "turno": meta.turno.strip(),
            "unidade": meta.unidade.strip(),
            "data": meta.data.strip() or "____/____/______",
            "nota": "",
            "valor": meta.valor.strip(),
            "data_entrega": meta.data_entrega.strip() or "____/____/______",
        }
        applicable = {"nota": meta.doc_type == "prova", "data_entrega": meta.doc_type == "atividade"}
        fields = [f for f in t.fields if f.enabled and applicable.get(f.key, True)]
        if not fields:
            return
        first_row = [f for f in fields if f.key in ("aluno", "numero")]
        others = [f for f in fields if f.key not in ("aluno", "numero")]
        rows: list[list] = []
        if first_row:
            rows.append(first_row)
        for i in range(0, len(others), 3):
            rows.append(others[i:i + 3])
        size = max(9.0, self.layout.font_size - 2)
        for fields_row in rows:
            table = self.doc.add_table(rows=1, cols=len(fields_row))
            _table_borders(table, "single", 4, "000000" if self.layout.grayscale else "7F7F7F")
            _fixed_layout(table)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            _set_widths(table, self._field_widths(fields_row))
            for cell, field in zip(table.rows[0].cells, fields_row, strict=True):
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                label = p.add_run(f"{field.label}: ")
                label.bold = True
                label.font.size = Pt(size)
                value = values.get(field.key, "")
                run = p.add_run(value)
                run.font.size = Pt(size)
        spacer = self.doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(4)
        spacer.paragraph_format.line_spacing = Pt(4)

    def _field_widths(self, fields) -> list[float]:
        total = self.text_width
        if [f.key for f in fields] == ["aluno", "numero"]:
            return [total - 2.6, 2.6]
        return [total / len(fields)] * len(fields)

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
        self._footer()
        self.doc.save(out_path)
        return out_path

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
        hanging = 0.0
        if is_question and label:
            hanging = 0.75 if len(label) <= 4 else 0.0
        indent = 0.0
        content_indent = hanging  # where statement text and alternatives start

        parts = list(block.parts)
        if is_question and label:
            first_part = parts[0] if parts and parts[0].kind == "paragraph" else None
            p = self._paragraph(hanging=hanging, align=None)
            run = p.add_run(label)
            run.bold = self.layout.bold_question_numbers
            p.add_run("\t" if hanging else " ")
            if first_part is not None:
                self._add_runs(p, first_part.runs)
                parts = parts[1:]
            units.append(p)

        alternatives = [p for p in parts if p.kind == "alternative"]
        inline_done = False
        for part in parts:
            if part.kind == "alternative" and block.alternatives_inline:
                if not inline_done:
                    units.append(self._inline_alternatives(alternatives, content_indent))
                    inline_done = True
                continue
            units.extend(self._render_part(part, block, content_indent if is_question else indent))

        if block.answer_space.kind != "none":
            units.extend(self._answer_space(block.answer_space, content_indent if is_question else indent))

        if not units:
            return
        head = units[0]
        if not first:
            head.paragraph_format.space_before = Pt(self.layout.question_spacing_pt)
        if block.page_break_before:
            head.paragraph_format.page_break_before = True

        # Pagination: keep the block together (or, when splitting is allowed, at least keep the
        # statement with the first alternatives).
        if is_question and block.allow_split:
            first_alt = next((i for i, u in enumerate(units) if getattr(u, "_is_alt", False)), len(units) - 1)
            chain_end = min(len(units) - 1, first_alt + 1)
        elif block.kind == "support_text":
            chain_end = len(units) - 1 if len(units) <= 6 else 1
        else:
            chain_end = len(units) - 1
        for unit in units[:chain_end]:
            unit.paragraph_format.keep_with_next = True
        if block.kind == "support_text":
            units[-1].paragraph_format.keep_with_next = True  # glue the text to the question that follows

    def _render_part(self, part: Part, block: Block, indent: float) -> list:
        if part.kind == "paragraph":
            p = self._paragraph(indent=indent, align=part.align)
            self._add_runs(p, part.runs)
            return [p]
        if part.kind == "alternative":
            label = (part.label or "").strip()
            alt_hang = 0.75 if len(label) <= 4 else 1.1
            p = self._paragraph(indent=indent, hanging=alt_hang)
            if label:
                p.add_run(label)
                p.add_run("\t")
            self._add_runs(p, part.runs)
            p._is_alt = True  # type: ignore[attr-defined]
            return [p]
        if part.kind in ("image", "crop"):
            path = self._image_path(part.asset_id)
            if path is None:
                return []
            avail = self.column_width - indent
            natural = part.width_cm or min(avail, 10.0)
            wanted = natural * part.width_pct / 100
            width = self._fit_width(path, wanted, avail)
            if part.width_cm and part.width_cm >= 5 and width < wanted * 0.7:
                what = "A tabela" if any(i.code == "table_as_crop" for i in part.issues) else "A imagem"
                self.notes.append((self._block.id if self._block else "", (
                    f"{what} em {self._block_label()} foi reduzida para {round(width / wanted * 100)}% para caber "
                    "na página/coluna. Confira se ficou legível (com 1 coluna ela fica maior).")))
            p = self._paragraph(indent=indent, align="center")
            p.add_run().add_picture(str(path), width=Cm(width))
            return [p]
        if part.kind == "table" and part.rows:
            return self._table(part.rows, indent)
        if part.kind == "answer_space" and part.answer is not None:
            return self._answer_space(part.answer, indent)
        return []

    def _inline_alternatives(self, alternatives: list[Part], indent: float):
        p = self._paragraph(indent=indent)
        per_row = min(5, max(2, len(alternatives)))
        step = (self.column_width - indent) / per_row
        for i in range(1, per_row):
            p.paragraph_format.tab_stops.add_tab_stop(Cm(indent + step * i))
        for i, alt in enumerate(alternatives):
            if i:
                p.add_run("\t")
            if alt.label:
                p.add_run(f"{alt.label} ")
            self._add_runs(p, alt.runs)
        return p

    def _table(self, rows: list[list[TableCell]], indent: float) -> list:
        ncols = max(sum(max(1, c.colspan) for c in row) for row in rows)
        table = self.doc.add_table(rows=len(rows), cols=ncols)
        table.style = self.doc.styles["Table Grid"]
        table.alignment = WD_TABLE_ALIGNMENT.CENTER if indent == 0 else WD_TABLE_ALIGNMENT.LEFT
        if indent:
            _table_indent(table, indent)
        if self.layout.grayscale:
            _table_borders(table, "single", 4, "000000")
        avail = self.column_width - indent
        cell_width = avail / ncols
        _set_widths(table, [cell_width] * ncols)
        size = max(9.0, self.layout.font_size - 1)
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

    def _answer_space(self, space: AnswerSpace, indent: float) -> list:
        units = []
        if space.kind == "lines":
            spacing = Cm(self.layout.answer_line_spacing_cm)
            for _ in range(space.lines):
                p = self.doc.add_paragraph()
                pf = p.paragraph_format
                pf.left_indent = Cm(indent)
                pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                pf.line_spacing = spacing
                pf.keep_together = True
                pf.tab_stops.add_tab_stop(Cm(self.column_width), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.LINES)
                p.add_run("\t")
                units.append(p)
        elif space.kind == "box":
            table = self.doc.add_table(rows=1, cols=1)
            _table_borders(table, "single", 6, "000000" if self.layout.grayscale else "595959")
            if indent:
                _table_indent(table, indent)
            row = table.rows[0]
            row.height = Cm(space.height_cm)
            row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
            _cant_split(row)
            row.cells[0].width = Cm(self.column_width - indent)
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
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        text = self.layout.footer_text.strip()
        if text:
            run = p.add_run(_XML_INVALID.sub("", text))
            run.font.size = Pt(9)
            if self.layout.page_numbers:
                sep = p.add_run("   •   ")
                sep.font.size = Pt(9)
        if self.layout.page_numbers:
            run = p.add_run("Página ")
            run.font.size = Pt(9)
            _add_field(p, "PAGE", 9)
            run = p.add_run(" de ")
            run.font.size = Pt(9)
            _add_field(p, "NUMPAGES", 9)
