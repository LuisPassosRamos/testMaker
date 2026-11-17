"""Extraction fidelity on the synthetic samples (see samples/generate_samples.py)."""

from __future__ import annotations

import itertools
from collections import Counter

import pytest

from app.domain.models import Block, ExamMeta, runs_text
from app.extraction.docx_reader import DocxExtractor
from app.extraction.pipeline import ExtractionPipeline
from app.importing.storage import ProjectStore
from app.importing.validation import validate_upload
from app.structuring.segmenter import segment
from tests.conftest import SAMPLES


def _docx_blocks(name: str) -> list[Block]:
    counter = itertools.count()
    extractor = DocxExtractor(str(SAMPLES / name), "s1", lambda _blob: f"{next(counter):016x}.png")
    return segment(extractor.extract()).blocks


def _pipeline_blocks(settings, name: str) -> tuple[list[Block], list[str]]:
    store = ProjectStore(settings)
    project = store.create("upload", ExamMeta())
    valid = validate_upload(name, (SAMPLES / name).read_bytes(), settings)
    source = store.add_source(project.id, valid)
    store.mutate(project.id, lambda p: p.sources.append(source))
    ExtractionPipeline(store, settings).run(project.id, lambda *_: None)
    loaded = store.load(project.id)
    return loaded.exam.blocks, [i.code for i in loaded.exam.document_issues]


def _questions(blocks: list[Block]) -> list[Block]:
    return [b for b in blocks if b.kind == "question"]


def _all_text(blocks: list[Block]) -> str:
    return "\n".join(
        " ".join(filter(None, [b.number or "", *[f"{p.label or ''} {runs_text(p.runs)}" for p in b.parts]]))
        for b in blocks
    )


def _check_expected(blocks: list[Block], spec: dict) -> None:
    questions = _questions(blocks)
    assert [q.number for q in questions] == spec["questions"], "questions lost, duplicated or reordered"
    for label, count in spec.get("alternatives", {}).items():
        question = next(q for q in questions if q.number == label)
        alternatives = [p for p in question.parts if p.kind == "alternative"]
        assert len(alternatives) == count, f"question {label}"
        labels = [p.label for p in alternatives]
        assert len(set(labels)) == len(labels) or set(labels) == {"( )"}, "alternative duplicated"
    text = _all_text(blocks)
    for snippet in spec.get("must_contain", []):
        assert snippet in text, f"missing text: {snippet!r}"


@pytest.mark.parametrize("name", ["prova-matematica.docx", "atividade-portugues.docx"])
def test_docx_keeps_every_question_alternative_and_symbol(expected, name):
    _check_expected(_docx_blocks(name), expected[name])


def test_docx_resolves_word_numbering_and_formatting():
    blocks = _docx_blocks("prova-matematica.docx")
    q2 = _questions(blocks)[1]
    assert q2.number == "2."  # from Word automatic numbering, not typed text
    bold_underlined = [r.text for p in q2.parts for r in p.runs if r.bold and r.underline]
    assert bold_underlined == ["INCORRETA"]
    # Equation kept as linear text with a real superscript and flagged for review.
    q5 = _questions(blocks)[4]
    first = q5.parts[0]
    assert "3/4+x" in runs_text(first.runs)
    assert any(r.sup and r.text == "2" for r in first.runs)
    assert any(i.code == "docx_equation" for i in first.issues)


def test_docx_image_and_table_stay_with_the_right_content():
    blocks = _docx_blocks("prova-matematica.docx")
    support = next(b for b in blocks if b.kind == "support_text")
    assert "questões 3 e 4" in runs_text(support.parts[0].runs)
    assert any(p.kind == "image" for p in support.parts)
    q6 = _questions(blocks)[5]
    kinds = [p.kind for p in q6.parts]
    assert kinds.index("table") < kinds.index("alternative")
    table = next(p for p in q6.parts if p.kind == "table")
    assert runs_text(table.rows[1][1].content[0].runs) == "R$ 12,50"


def test_docx_answer_lines_and_header_detection():
    blocks = _docx_blocks("atividade-portugues.docx")
    assert blocks[0].kind == "original_header"
    q1 = _questions(blocks)[0]
    assert q1.answer_space.kind == "lines" and q1.answer_space.lines == 3
    vf = _questions(blocks)[2]
    assert [p.label for p in vf.parts if p.kind == "alternative"] == ["( )", "( )", "( )"]
    q5 = _questions(blocks)[4]
    assert [p.label for p in q5.parts if p.kind == "alternative"] == ["a)", "b)", "c)", "d)"]


def test_text_pdf_matches_the_word_original(settings, expected):
    blocks, _ = _pipeline_blocks(settings, "prova-matematica.pdf")
    _check_expected(blocks, expected["prova-matematica.pdf"])
    q5 = _questions(blocks)[4]
    assert any(i.code == "formula_linearised" for p in q5.parts for i in p.issues)


def test_two_column_pdf_is_read_column_by_column(settings, expected):
    blocks, doc_issues = _pipeline_blocks(settings, "revisao-ciencias-duas-colunas.pdf")
    _check_expected(blocks, expected["revisao-ciencias-duas-colunas.pdf"])
    issue_codes = doc_issues + [i.code for b in blocks for p in b.parts for i in p.issues]
    assert "two_columns" in issue_codes


@pytest.mark.ocr
def test_scanned_pdf_finds_all_questions_and_flags_doubts(settings, expected):
    blocks, doc_issues = _pipeline_blocks(settings, "prova-digitalizada.pdf")
    questions = _questions(blocks)
    assert [q.number for q in questions] == expected["prova-digitalizada.pdf"]["questions"]
    alternatives = Counter(len([p for p in q.parts if p.kind == "alternative"]) for q in questions)
    assert alternatives[4] >= 4, "most questions keep their four alternatives"
    assert "ocr_formatting" in doc_issues
    assert any(i.code == "ocr_low_confidence" for b in blocks for p in b.parts for i in p.issues)
    assert all(p.provenance.bbox for b in blocks for p in b.parts if p.provenance.method == "ocr")


@pytest.mark.ocr
def test_phone_photo_is_structured(settings, expected):
    blocks, _ = _pipeline_blocks(settings, "foto-pagina-1.jpg")
    numbers = [q.number for q in _questions(blocks)]
    assert len(numbers) >= 5
    q2 = next(q for q in _questions(blocks) if q.number and q.number.startswith("02"))
    assert len([p for p in q2.parts if p.kind == "alternative"]) == 4


@pytest.mark.ocr
def test_unreadable_photo_is_kept_as_image_not_as_guesses(settings):
    blocks, _ = _pipeline_blocks(settings, "foto-baixa-qualidade.jpg")
    parts = [p for b in blocks for p in b.parts]
    assert len(parts) == 1 and parts[0].kind == "crop" and parts[0].asset_id
    assert parts[0].issues[0].code in ("ocr_unreadable_page", "ocr_no_text")
