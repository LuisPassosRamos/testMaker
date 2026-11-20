"""Options laid out side by side in PDFs: wide gaps, grids read by row or by column."""

from __future__ import annotations

from app.domain.models import Run, runs_text
from app.extraction.layout import Line, Word, order_page_lines
from app.structuring.paste import pasted_items
from app.structuring.segmenter import segment


def _w(text: str, x0: float, top: float, width: float | None = None) -> Word:
    return Word(text, x0, top, x0 + (width or len(text) * 5.0), top + 10)


def test_wide_gap_between_words_becomes_a_tab():
    line = Line(words=[_w("(", 50, 100, 3), _w(")", 56, 100, 3), _w("um", 62, 100), _w("lugar.", 75, 100),
                       _w("(", 200, 100, 3), _w(")", 206, 100, 3), _w("um", 212, 100), _w("animal.", 225, 100)],
                page=1)
    assert runs_text(line.runs()) == "( ) um lugar.\t( ) um animal."


def _segment_lines(*lines: str):
    paragraphs = [[Run(text=line)] for line in lines]
    return segment(pasted_items(paragraphs)).blocks


def test_options_by_column_are_split_and_put_back_in_order():
    blocks = _segment_lines(
        "3. A atitude de João mostra:",
        "A) Falta de respeito\tC) Desinteresse",
        "B) Amizade e solidariedade\tD) Preguiça",
    )
    question = blocks[0]
    alts = [(p.label, runs_text(p.runs).strip()) for p in question.parts if p.kind == "alternative"]
    assert alts == [("A)", "Falta de respeito"), ("B)", "Amizade e solidariedade"), ("C)", "Desinteresse"),
                    ("D)", "Preguiça")]
    assert any(i.code == "alternatives_reordered" for i in question.issues)
    assert not any(i.code == "alternatives_sequence" for i in question.issues)


def test_box_options_separated_by_tabs_are_split():
    blocks = _segment_lines("10. O substantivo nomeia:", "( ) um lugar.\t( ) um animal.\t( ) uma planta.")
    labels = [p.label for p in blocks[0].parts if p.kind == "alternative"]
    assert labels == ["( )", "( )", "( )"]


def test_a_real_sentence_with_a_letter_is_not_split():
    blocks = _segment_lines("1. Leia e marque.", "a) Ana comprou a) sua bola e b) seu livro.")
    alts = [runs_text(p.runs) for p in blocks[0].parts if p.kind == "alternative"]
    # Only the sequential "b)" splits; the repeated "a)" inside the sentence stays as text.
    assert alts[0].startswith("Ana comprou a) sua bola")


def test_option_grid_with_a_gutter_is_read_row_by_row():
    words: list[Word] = []
    # Two text lines crossing the middle make the page look like it has a gutter only around the grid.
    for top in range(0, 400, 20):
        if 100 <= top < 340:
            row = (top - 100) // 20
            letters = "ACEGIKMOQSUW"
            left = f"{letters[row % 12].lower()})"
            words += [_w(left, 50, top, 12), _w("esquerda", 66, top, 120)]
            words += [_w(f"{chr(ord(left[0]) + 1)})", 330, top, 12), _w("direita", 346, top, 120)]
        else:
            words += [_w("Texto", 50, top, 40), _w("corrido", 95, top, 220), _w("ocupando", 320, top, 150)]
    lines, two_columns = order_page_lines(words, 1, 600, 50, 500)
    texts = [ln.text for ln in lines if ") " in ln.text]
    # Each row's left option comes right before its right option (a, b, c, d…), not a, c, e… then b, d…
    assert texts[0].startswith("a)") and texts[1].startswith("b)") and texts[2].startswith("c)")
    assert two_columns is False
