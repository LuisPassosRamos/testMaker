"""Word generation (python-docx) and the exported PDF (LibreOffice/Word)."""

from __future__ import annotations

import pdfplumber
import pytest
from docx import Document

from app.domain.models import AnswerSpace, Block, Exam, ExamMeta, Part, Run
from app.rendering.checks import run_checks
from app.rendering.converter import docx_to_pdf
from app.rendering.docx_writer import DocxRenderer, header_title, safe_filename
from app.structuring.paste import pasted_items, plain_text_paragraphs
from app.structuring.segmenter import segment
from app.templates_store import builtin_template

QUESTIONS = 14


def _exam(columns: int = 1) -> Exam:
    lines = ["Leia o texto para responder às questões 1 e 2.", "Um texto de apoio curto, com acentuação: ação, ônibus, pé."]
    for n in range(1, QUESTIONS + 1):
        lines.append(f"{n}) Enunciado da questão número {n}, com símbolos ≤ ≥ π √ ½ e uma frase longa o suficiente para quebrar a linha.")
        lines += [f"{letter}) Alternativa {letter} da questão {n}" for letter in "abcd"]
    exam = Exam(meta=ExamMeta(doc_type="prova", disciplina="Ciências", unidade="II", ano="5º ano", turma="B",
                              professor="Ana Souza"))
    exam.blocks = segment(pasted_items(plain_text_paragraphs("\n".join(lines)))).blocks
    exam.layout.columns = columns  # type: ignore[assignment]
    exam.blocks[-1].answer_space = AnswerSpace(kind="lines", lines=4)
    return exam


def _render(tmp_path, exam: Exam):
    template = builtin_template()
    out = tmp_path / "prova.docx"
    renderer = DocxRenderer(exam, template, tmp_path / "assets", None, tmp_path / "work")
    renderer.render(out)
    return out


def test_docx_has_school_header_a4_and_pagination_rules(tmp_path):
    exam = _exam()
    doc = Document(str(_render(tmp_path, exam)))
    section = doc.sections[0]
    assert round(section.page_width.cm, 1) == 21.0 and round(section.page_height.cm, 1) == 29.7
    text = "\n".join(p.text for p in doc.paragraphs)
    cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert "ESCOLA MUNICIPALIZADA MADRE Mª DO ROSÁRIO DE ALMEIDA II" in cells
    assert header_title(exam) == "AVALIAÇÃO DE CIÊNCIAS – II UNIDADE"
    assert "Professor(a): Ana Souza" in cells and "Nota:" in cells
    # Every question label and alternative appears exactly once.
    lines = text.split("\n")
    for n in range(1, QUESTIONS + 1):
        assert sum(line.startswith(f"{n})\tEnunciado da questão número {n},") for line in lines) == 1
        assert lines.count(f"d)\tAlternativa d da questão {n}") == 1
    # The number, statement and first alternatives are chained to stay on the same page.
    first_question = next(p for p in doc.paragraphs if p.text.startswith("1)\t"))
    assert first_question.paragraph_format.keep_with_next is True
    assert first_question.paragraph_format.keep_together is True


def test_header_fields_follow_document_type(tmp_path):
    exam = _exam()
    exam.meta.doc_type = "atividade"
    doc = Document(str(_render(tmp_path, exam)))
    cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert "Nota:" not in cells and "Entrega:" in cells
    assert safe_filename(exam, "pdf") == "Atividade - Ciências - 5º ano B.pdf"


def test_runs_keep_their_formatting(tmp_path):
    exam = _exam()
    exam.blocks[1].parts[0].runs = [Run(text="Calcule x"), Run(text="2", sup=True), Run(text=" e H"),
                                    Run(text="2", sub=True), Run(text="O "), Run(text="NÃO", bold=True, underline=True)]
    doc = Document(str(_render(tmp_path, exam)))
    paragraph = next(p for p in doc.paragraphs if "Calcule" in p.text)
    assert any(r.font.superscript and r.text == "2" for r in paragraph.runs)
    assert any(r.font.subscript and r.text == "2" for r in paragraph.runs)
    assert any(r.bold and r.underline and r.text == "NÃO" for r in paragraph.runs)


@pytest.mark.converter
@pytest.mark.parametrize("columns", [1, 2])
def test_exported_pdf_is_a4_complete_ordered_and_within_margins(tmp_path, settings, columns):
    exam = _exam(columns)
    pdf_path = docx_to_pdf(_render(tmp_path, exam), tmp_path / "pdf", settings)
    with pdfplumber.open(str(pdf_path)) as pdf:
        assert len(pdf.pages) >= 2
        for page in pdf.pages:
            assert abs(page.width - 595.28) < 2 and abs(page.height - 841.89) < 2
    errors = [c for c in run_checks(exam, str(pdf_path)) if c.severity == "error"]
    assert errors == [], errors


@pytest.mark.converter
def test_checks_catch_missing_content(tmp_path, settings):
    exam = _exam()
    pdf_path = docx_to_pdf(_render(tmp_path, exam), tmp_path / "pdf", settings)
    # A question that is not in the PDF (e.g. lost by a bug) must be reported.
    ghost = Block(kind="question", number="99)", parts=[Part(runs=[Run(text="Pergunta que não existe no arquivo")])])
    exam.blocks.append(ghost)
    codes = {c.code for c in run_checks(exam, str(pdf_path))}
    assert "question_missing" in codes
