"""Deterministic segmentation rules (no AI) on pasted text and on tricky inputs."""

from __future__ import annotations

from app.domain.models import Run, runs_text
from app.extraction.docx_numbering import format_number
from app.extraction.omml import omml_to_runs
from app.structuring.paste import pasted_items, plain_text_paragraphs
from app.structuring.patterns import match_alternative, match_question
from app.structuring.segmenter import segment


def _segment(text: str):
    return segment(pasted_items(plain_text_paragraphs(text)))


def test_question_and_alternative_labels_are_kept_verbatim():
    assert match_question("01 – Leia o texto").label == "01 –"
    assert match_question("Questão 3: Observe").label == "Questão 3:"
    assert match_question("(4) Marque").number == 4
    assert match_alternative("(B) Rio").label == "(B)"
    assert match_alternative("( ) Verdadeiro").label == "( )"


def test_numbers_that_are_not_labels():
    assert match_question("10 - 3 = 7 é verdadeiro?") is None
    assert match_question("1.5 metro de tecido") is None
    assert match_question("2,5 kg de arroz") is None
    assert match_question("1990 foi um ano") is None
    assert match_question("1) 25 + 30 = ?").number == 1


def test_full_exam_structure_from_pasted_text():
    result = _segment(
        "ESCOLA MUNICIPAL EXEMPLO\n"
        "Nome: ______________ Turma: ____\n"
        "INSTRUÇÕES:\n"
        "1. Use caneta azul.\n"
        "2. Não use corretivo.\n"
        "Leia o texto para responder às questões 1 e 2.\n"
        "Era uma vez uma pequena semente que sonhava em ser árvore.\n"
        "1) Qual era o sonho da semente?\n"
        "a) Ser flor\nb) Ser árvore\nc) Ser fruto\n"
        "2) Explique com suas palavras.\n"
        + "_" * 40 + "\n" + "_" * 40 + "\n"
        "3) Quanto é 7 × 8? a) 54  b) 56  c) 58  d) 64\n"
    )
    kinds = [b.kind for b in result.blocks]
    assert kinds == ["original_header", "instructions", "support_text", "question", "question", "question"]
    instructions = result.blocks[1]
    assert [runs_text(p.runs) for p in instructions.parts][1:] == ["1. Use caneta azul.", "2. Não use corretivo."]
    q1, q2, q3 = [b for b in result.blocks if b.kind == "question"]
    assert [p.label for p in q1.parts if p.kind == "alternative"] == ["a)", "b)", "c)"]
    assert q2.answer_space.kind == "lines" and q2.answer_space.lines == 2
    assert runs_text(q3.parts[0].runs) == "Quanto é 7 × 8?"
    assert [runs_text(p.runs).strip() for p in q3.parts if p.kind == "alternative"] == ["54", "56", "58", "64"]


def test_doubts_are_flagged_not_fixed():
    result = _segment("1) Primeira\na) x\nb) y\n2) Segunda\na) x\nc) y\n4) Quarta\n9) Nona?")
    q2 = result.blocks[1]
    assert any(i.code == "alternatives_sequence" for i in q2.issues)
    q4 = result.blocks[2]
    assert q4.number == "4)" and any(i.code == "numbering_gap" for i in q4.issues)
    # "9)" is too far from 4 to be trusted: kept as text, flagged.
    assert len([b for b in result.blocks if b.kind == "question"]) == 3
    assert any(i.code == "numbering_out_of_sequence" for b in result.blocks for p in b.parts for i in p.issues)


def test_text_without_numbers_is_never_dropped():
    text = "Um texto qualquer sem questões.\nOutra linha."
    result = _segment(text)
    joined = " ".join(runs_text(p.runs) for b in result.blocks for p in b.parts)
    assert "Um texto qualquer sem questões." in joined and "Outra linha." in joined
    assert [i.code for i in result.issues] == ["no_questions"]


def test_formatting_survives_paste():
    items = pasted_items([[Run(text="1) Assinale a "), Run(text="INCORRETA", bold=True, underline=True)]])
    block = segment(items).blocks[0]
    assert [r.text for r in block.parts[0].runs if r.bold] == ["INCORRETA"]


def test_word_numbering_formats():
    assert format_number(3, "lowerLetter") == ("c", True)
    assert format_number(28, "lowerLetter") == ("bb", True)
    assert format_number(4, "upperRoman") == ("IV", True)
    assert format_number(7, "decimalZero") == ("07", True)
    assert format_number(2, "chineseCounting")[1] is False


def test_equations_become_linear_text_with_real_superscripts():
    from lxml import etree

    xml = (
        '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        "<m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr><m:deg/><m:e><m:r><m:t>x+1</m:t></m:r></m:e></m:rad>"
        "<m:r><m:t>=</m:t></m:r>"
        "<m:sSub><m:e><m:r><m:t>a</m:t></m:r></m:e><m:sub><m:r><m:t>n</m:t></m:r></m:sub></m:sSub>"
        "</m:oMath>"
    )
    runs, unsupported = omml_to_runs(etree.fromstring(xml))
    assert runs_text(runs) == "√(x+1)=an"
    assert any(r.sub and r.text == "n" for r in runs)
    assert unsupported == []
