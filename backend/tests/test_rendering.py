"""Word generation (python-docx) in the school's standard and the exported PDF (LibreOffice/Word)."""

from __future__ import annotations

import itertools
import json
import re

import pdfplumber
import pytest
from docx import Document
from docx.oxml.ns import qn

from app.domain.models import AnswerSpace, Block, Exam, ExamMeta, Part, Run
from app.extraction.docx_reader import DocxExtractor
from app.rendering.checks import run_checks
from app.rendering.converter import docx_to_pdf
from app.rendering.docx_writer import DocxRenderer, header_title, safe_filename
from app.structuring.paste import pasted_items, plain_text_paragraphs
from app.structuring.segmenter import segment
from app.templates_store import BUILTIN_REVISION, TemplateStore, builtin_template
from tests.conftest import SAMPLES

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


def _cells(doc) -> str:
    return "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)


def _question(exam: Exam, alternatives: list[str], **block) -> Exam:
    parts = [Part(runs=[Run(text="Escolha a resposta certa:")])]
    for i, text in enumerate(alternatives):
        parts.append(Part(kind="alternative", label=f"{'ABCDE'[i]})", runs=[Run(text=text)]))
    exam.blocks = [Block(kind="question", number="1.", parts=parts, **block)]
    return exam


def test_docx_has_school_header_a4_and_pagination_rules(tmp_path):
    exam = _exam()
    doc = Document(str(_render(tmp_path, exam)))
    section = doc.sections[0]
    assert round(section.page_width.cm, 1) == 21.0 and round(section.page_height.cm, 1) == 29.7
    assert (round(section.top_margin.cm, 1), round(section.left_margin.cm, 1)) == (1.5, 1.6)
    # Thin frame around the page, as in the school's exams.
    assert section._sectPr.find(qn("w:pgBorders")) is not None
    cells = _cells(doc)
    assert "ESCOLA MUNICIPALIZADA MADRE MARIA DO ROSÁRIO DE ALMEIDA II" in cells
    assert "ALUNO(A): ____" in cells and "PROFESSOR(A): ANA SOUZA" in cells and "TURMA: 5º ANO B" in cells
    title = next(p for p in doc.paragraphs if p.style.name == "Título da avaliação")
    assert title.text == header_title(exam) == "Avaliação II Unidade - Ciências 5º ano"
    text = "\n".join(p.text for p in doc.paragraphs)
    # Every question label and alternative appears exactly once.
    for n in range(1, QUESTIONS + 1):
        assert sum(line.startswith(f"{n}) Enunciado da questão número {n},") for line in text.split("\n")) == 1
        assert len(re.findall(rf"d\) Alternativa d da questão {n}(?!\d)", text)) == 1
    # The number, statement and alternatives are chained to stay on the same page.
    first_question = next(p for p in doc.paragraphs if p.text.startswith("1) "))
    assert first_question.style.name == "Enunciado"
    assert first_question.runs[0].bold and first_question.runs[0].text == "1)"
    assert first_question.paragraph_format.keep_with_next is True
    assert first_question.paragraph_format.keep_together is True


def test_header_fields_follow_document_type(tmp_path):
    exam = _exam()
    exam.meta.doc_type = "atividade"
    cells = _cells(Document(str(_render(tmp_path, exam))))
    assert "NOTA:" not in cells and "ENTREGA:" in cells
    assert safe_filename(exam, "pdf") == "Atividade II Unidade - Ciências 5º ano B.pdf"


@pytest.mark.parametrize(("meta", "title"), [
    ({"modalidade": "final", "unidade": "III", "periodo": "trimestre", "disciplina": "Matemática", "ano": "6º ano"},
     "Avaliação Final III Trimestre - Matemática 6º ano"),
    ({"unidade": "II", "periodo": "periodo", "disciplina": "Ciências", "ano": "9º ano"},
     "Avaliação II Período - Ciências 9º ano"),
    ({"modalidade": "final", "unidade": "I", "disciplina": "Ciências", "ano": "1º ano"},
     "AVALIAÇÃO FINAL I UNIDADE - CIÊNCIAS 1º ANO"),  # 1st grade: capital letters
    ({"modalidade": "recuperacao", "disciplina": "História", "ano": "9º ano"},
     "Avaliação de Recuperação - História 9º ano"),
    ({"modalidade": "simulado", "unidade": "II", "disciplina": "Matemática", "ano": "7º ano"},
     "Simulado II Unidade - Matemática 7º ano"),
    ({"doc_type": "atividade", "unidade": "II", "disciplina": "Português", "ano": "5º ano"},
     "Atividade II Unidade - Português 5º ano"),
    ({"title": "Prova bimestral de Arte", "disciplina": "Arte"}, "Prova bimestral de Arte"),
])
def test_title_follows_the_school_format(meta, title):
    assert header_title(Exam(meta=ExamMeta(**meta))) == title


@pytest.mark.parametrize(("alternatives", "layout", "rows"), [
    (["73", "229", "219", "291"], "auto", 1),  # very short: all on one line
    (["( ) poema", "( ) bilhete", "( ) aviso", "( ) receita"], "auto", 2),  # two by two
    (["Positiva, pois o celular aproxima as pessoas e melhora a comunicação."] * 4, "auto", 4),
    (["um lugar.", "um animal.", "uma planta."], "auto", 1),  # three never leave one alone
    (["73", "229", "219", "291"], "list", 4),
    (["Positiva, pois o celular aproxima as pessoas e melhora a comunicação."] * 4, "row", 1),
])
def test_short_alternatives_go_side_by_side(tmp_path, alternatives, layout, rows):
    exam = _question(Exam(), alternatives, alternatives_layout=layout)
    doc = Document(str(_render(tmp_path, exam)))
    alt_paragraphs = [p for p in doc.paragraphs if p.style.name == "Alternativa"]
    assert len(alt_paragraphs) == rows
    text = "\t".join(p.text for p in alt_paragraphs)
    assert [t for t in text.split("\t")] == [f"{'ABCDE'[i]}) {a}" for i, a in enumerate(alternatives)]


def test_old_inline_flag_still_means_one_line():
    block = Block.model_validate({"kind": "question", "alternatives_inline": True})
    assert block.alternatives_layout == "row"


def test_answer_lines_work_label_and_closing_message(tmp_path):
    exam = _question(Exam(meta=ExamMeta(mensagem_final="Boa prova!\nEu acredito em você!")), ["10", "20"])
    exam.blocks[0].answer_space = AnswerSpace(kind="lines", lines=3, label="Cálculo")
    doc = Document(str(_render(tmp_path, exam)))
    label = next(p for p in doc.paragraphs if p.text == "Cálculo")
    assert label.style.name == "Rótulo de resposta"
    lines = [p for p in doc.paragraphs if p.style.name == "Linha de resposta"]
    assert len(lines) == 3
    assert all(p._p.pPr.find(qn("w:pBdr")) is not None for p in lines)
    closing = [p for p in doc.paragraphs if p.style.name == "Mensagem final"]
    assert [p.text for p in closing] == ["Boa prova!", "Eu acredito em você!"]
    assert doc.paragraphs[-1].text == "Eu acredito em você!"


def test_page_border_can_be_turned_off(tmp_path):
    exam = _exam()
    exam.layout.page_border = False
    doc = Document(str(_render(tmp_path, exam)))
    assert doc.sections[0]._sectPr.find(qn("w:pgBorders")) is None


def test_school_sample_round_trip_keeps_invisible_tables(tmp_path):
    counter = itertools.count()
    items = DocxExtractor(str(SAMPLES / "prova-padrao-escola.docx"), "s1",
                          lambda _blob: f"{next(counter):016x}.png").extract()
    exam = Exam(meta=ExamMeta(disciplina="Ciências", ano="4º ano", unidade="II"), blocks=segment(items).blocks)
    doc = Document(str(_render(tmp_path, exam)))
    poem = next(t for t in doc.tables if "A nuvem passa" in _cells_of(t))
    borders = poem._tbl.tblPr.find(qn("w:tblBorders"))
    assert all(b.get(qn("w:val")) == "nil" for b in borders)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "5. Quanto é 125 + 38?" in text and "A) 153\tB) 163\tC) 173\tD) 183" in text
    assert "Cálculo" in text and "Boa prova!!" in text


def _cells_of(table) -> str:
    return "\n".join(c.text for r in table.rows for c in r.cells)


def test_runs_keep_their_formatting(tmp_path):
    exam = _exam()
    exam.blocks[1].parts[0].runs = [Run(text="Calcule x"), Run(text="2", sup=True), Run(text=" e H"),
                                    Run(text="2", sub=True), Run(text="O "), Run(text="NÃO", bold=True, underline=True)]
    doc = Document(str(_render(tmp_path, exam)))
    paragraph = next(p for p in doc.paragraphs if "Calcule" in p.text)
    assert any(r.font.superscript and r.text == "2" for r in paragraph.runs)
    assert any(r.font.subscript and r.text == "2" for r in paragraph.runs)
    assert any(r.bold and r.underline and r.text == "NÃO" for r in paragraph.runs)


def test_old_builtin_template_is_upgraded_keeping_the_logo(settings):
    store = TemplateStore(settings)
    old = builtin_template().model_copy(update={"school_name": "ESCOLA ANTIGA", "revision": 1, "logo": "x.png"})
    store.file.write_text(json.dumps([old.model_dump()]), encoding="utf-8")
    current = store.get("padrao-madre")
    assert current.school_name == builtin_template().school_name and current.revision == BUILTIN_REVISION
    assert current.logo == "x.png"


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


@pytest.mark.converter
def test_school_limits_are_checked(tmp_path, settings):
    exam = _exam()
    for block in exam.blocks:
        if block.kind == "question":
            block.answer_space = AnswerSpace(kind="lines", lines=12)  # far beyond four pages
    exam.layout.font_size = 10
    exam.meta.ano = "3º ano"
    exam.blocks[2].parts.append(Part(kind="image", asset_id="missing.png"))
    exam.blocks[2].parts[0].runs = [Run(text="Pinte de azul o céu da figura.")]
    pdf_path = docx_to_pdf(_render(tmp_path, exam), tmp_path / "pdf", settings)
    checks = {c.code: c for c in run_checks(exam, str(pdf_path))}
    assert "page_limit" in checks and "4 páginas" in checks["page_limit"].message
    assert "font_too_small" in checks and "12 pt" in checks["font_too_small"].message
    assert "colour_in_grayscale" in checks
