"""Answer key (gabarito): a separate, simple document for the teacher only.

It is never attached to the e-mail sent to the print team.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from app.domain.models import Exam
from app.rendering.docx_writer import header_title, safe_filename

DEMANDS = {"direta": "direta", "aplicacao": "aplicação", "raciocinio": "raciocínio"}


def answer_key_filename(exam: Exam, ext: str) -> str:
    return f"Gabarito - {safe_filename(exam, ext)}"


def render_answer_key(exam: Exam, path: Path) -> None:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.0))
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(3)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(f"GABARITO — {header_title(exam)}")
    run.bold = True
    run.font.size = Pt(13)
    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = note.add_run("Uso exclusivo do(a) professor(a). Não entregar aos alunos.")
    run.italic = True
    details = [x for x in (
        f"Professor(a): {exam.meta.professor}" if exam.meta.professor.strip() else "",
        f"Turma: {exam.meta.ano} {exam.meta.turma}".strip() if exam.meta.turma.strip() else "",
    ) if x]
    if details:
        doc.add_paragraph(" · ".join(details))

    keys = {entry.block_id: entry for entry in exam.answer_key}
    for block in exam.blocks:
        if block.kind != "question":
            continue
        entry = keys.get(block.id)
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        number = block.number or (entry.number if entry else "") or "•"
        run = p.add_run(f"{number} ")
        run.bold = True
        p.add_run(entry.answer if entry and entry.answer.strip() else "(resposta não informada)")
        if entry and entry.points.strip():
            p.add_run(f"  (valor: {entry.points})").italic = True
        if entry and entry.criteria.strip():
            c = doc.add_paragraph()
            c.paragraph_format.left_indent = Cm(0.6)
            c.add_run("Critérios: ").bold = True
            c.add_run(entry.criteria)
        if entry and (entry.skill.strip() or entry.demand.strip()):
            s = doc.add_paragraph()
            s.paragraph_format.left_indent = Cm(0.6)
            parts = []
            if entry.skill.strip():
                parts.append(f"Habilidade: {entry.skill}")
            if entry.demand.strip():
                parts.append(f"Demanda: {DEMANDS.get(entry.demand, entry.demand)}")
            run = s.add_run(" · ".join(parts))
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    doc.save(str(path))
