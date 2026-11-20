"""Group extracted items into header, instructions, support texts and questions.

Deterministic and conservative: labels are kept exactly as written, nothing is
renumbered or reordered, and every doubtful decision (numbering gaps, alternatives
that skip a letter, an image that may belong to the next question…) is recorded as
an issue for the reviewer rather than silently "fixed".
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.domain.models import AnswerSpace, Block, Issue, Part, Run, merge_runs, runs_text
from app.extraction.items import AnswerLinesItem, Item, MediaItem, PageBreakItem, TextItem
from app.structuring.patterns import (
    COLUMN_HEADING_RE,
    HEADER_FIELD_RE,
    INLINE_ALT_RE,
    INSTRUCTION_HEADING_RE,
    INSTRUCTION_ITEM_RE,
    SCHOOL_NAME_RE,
    SOURCE_LINE_RE,
    SUPPORT_RE,
    LabelMatch,
    match_alternative,
    match_question,
)

_TITLE_RE = re.compile(
    r"\b(avalia[çc][ãa]o|prova|atividade|revis[ãa]o|simulado|exerc[íi]cios|teste|unidade|bimestre|trimestre|"
    r"recupera[çc][ãa]o|ficha)\b",
    re.IGNORECASE,
)
_LIST_QUESTION_RE = re.compile(r"^\(?(\d{1,3})\s*[ºª°]?[\.\)\-–—:]?\)?$")
_LIST_ALT_RE = re.compile(r"^\(?([a-hA-H])[\)\.\-–]?\)?$")
_FOLLOW_UP_RE = re.compile(r"^\s*(justifique|explique|responda|mostre|comente|demonstre|registre|apresente)\b", re.I)
# Caption of a work area under the alternatives ("Cálculo", "Resolução:") — part of the question.
_WORK_LABEL_RE = re.compile(
    r"^\s*(c[áa]lculos?|resolu[çc][ãa]o|rascunho|espa[çc]o\s+para\s+(o\s+|os\s+)?c[áa]lculos?|"
    r"(mostre|fa[çc]a|registre)\s+(o|os|seus?|aqui\s+o)\s+c[áa]lculos?(\s+aqui)?)\s*[:.]?\s*$",
    re.IGNORECASE,
)
# Encouraging lines that close the school's exams ("Boa prova!!", "Você é capaz!!").
_CLOSING_RE = re.compile(
    r"\b(boa\s+prova|boa\s+sorte|voc[êe]\s+(consegue|[ée]\s+capaz|vai\s+conseguir)|acredito\s+em\s+voc|"
    r"orgulho\s+de\s+voc|mantenha\s+a\s+calma|sucesso\s*!)",
    re.IGNORECASE,
)
_QUOTED_LINE_RE = re.compile(r"^\s*[“\"«].{3,150}[”\"»]\s*\.?\s*$")
# Several "( )" options written on one line, separated by tabs or wide spaces.
_INLINE_BOX_RE = re.compile(r"(?:\t+|\s{2,})(?P<label>\(\s{0,3}\)|\[\s{0,3}\]|[□☐❑○◯])\s*")


def _label_style(label: str) -> str:
    """Rough shape of a question label, used to tell "1." questions from "(1)" association items."""
    label = label.strip()
    if label[:1].lower() == "q":
        return "word"
    if label.startswith("("):
        return "paren"
    return label[-1:] if label[-1:] in ".)-–—:" else "plain"


@dataclass
class SegmentResult:
    blocks: list[Block] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    # The text already ends with its own encouraging words ("Boa prova!!").
    has_closing: bool = False


def split_runs(runs: list[Run], offset: int) -> tuple[list[Run], list[Run]]:
    before: list[Run] = []
    after: list[Run] = []
    pos = 0
    for run in runs:
        end = pos + len(run.text)
        if end <= offset:
            before.append(run)
        elif pos >= offset:
            after.append(run)
        else:
            cut = offset - pos
            before.append(run.model_copy(update={"text": run.text[:cut]}))
            after.append(run.model_copy(update={"text": run.text[cut:]}))
        pos = end
    return merge_runs(before), _lstrip_runs(merge_runs(after))


def _lstrip_runs(runs: list[Run]) -> list[Run]:
    out = list(runs)
    while out and not out[0].text.strip():
        out.pop(0)
    if out:
        out[0] = out[0].model_copy(update={"text": out[0].text.lstrip(" \t")})
    return out


def _issue(code: str, message: str, severity: str = "warning", **detail) -> Issue:
    return Issue(code=code, message=message, severity=severity, detail=detail or None)  # type: ignore[arg-type]


class Segmenter:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.current: Block | None = None
        self.phase = "start"  # start -> body
        self.last_number: int | None = None
        self.in_instructions = False
        self.pending_media: list[Part] = []
        self.doc_issues: list[Issue] = []
        self.question_style: str | None = None
        self.closing: Block | None = None

    # ------------------------------------------------------------------ API
    def run(self, items: list[Item]) -> SegmentResult:
        for index, item in enumerate(items):
            next_item = items[index + 1] if index + 1 < len(items) else None
            if isinstance(item, PageBreakItem):
                if item.new_source and self.blocks:
                    self.phase = "source_start"  # another file: it may start with its own header
                continue
            if isinstance(item, AnswerLinesItem):
                self._answer_lines(item)
            elif isinstance(item, MediaItem):
                self._media(item, next_item)
            elif isinstance(item, TextItem):
                self._text(item)
        if self.pending_media:
            target = self.current or self._open("free")
            target.parts.extend(self.pending_media)
            self.pending_media = []
        self._finalise()
        return SegmentResult(blocks=self.blocks, issues=self.doc_issues,
                             has_closing=self.closing is not None and bool(self.closing.parts))

    # -------------------------------------------------------------- helpers
    def _open(self, kind: str, number: str | None = None) -> Block:
        block = Block(kind=kind, number=number, original_number=number)  # type: ignore[arg-type]
        self.blocks.append(block)
        self.current = block
        if kind != "original_header":
            self.phase = "body"
        if self.pending_media and kind in ("question", "support_text"):
            block.parts.extend(self.pending_media)
            self.pending_media = []
        return block

    def _ensure(self, kind: str) -> Block:
        if self.current is None or self.current.kind != kind:
            return self._open(kind)
        return self.current

    @staticmethod
    def _part_from_text(item: TextItem, runs: list[Run] | None = None, kind: str = "paragraph",
                        label: str | None = None) -> Part:
        runs = runs if runs is not None else item.runs
        return Part(
            kind=kind,  # type: ignore[arg-type]
            runs=runs,
            original_runs=[r.model_copy() for r in runs],
            label=label,
            original_label=label,
            align=item.align if item.align in ("center", "right", "justify") else None,  # type: ignore[arg-type]
            provenance=item.prov,
            issues=list(item.issues),
        )

    # --------------------------------------------------------- classification
    def _question_label(self, item: TextItem) -> LabelMatch | None:
        if item.list_label and _LIST_QUESTION_RE.match(item.list_label.strip()):
            number = int(_LIST_QUESTION_RE.match(item.list_label.strip()).group(1))  # type: ignore[union-attr]
            return LabelMatch(label=item.list_label.strip(), number=number, letter=None, rest_offset=0)
        if item.list_label:
            return None
        return match_question(item.text)

    def _alternative_label(self, item: TextItem) -> LabelMatch | None:
        if item.list_label and _LIST_ALT_RE.match(item.list_label.strip()):
            letter = _LIST_ALT_RE.match(item.list_label.strip()).group(1)  # type: ignore[union-attr]
            return LabelMatch(label=item.list_label.strip(), number=None, letter=letter, rest_offset=0)
        if item.list_label:
            return None
        return match_alternative(item.text)

    def _is_header_text(self, item: TextItem) -> bool:
        if item.from_page_header:
            return True
        if self.phase not in ("start", "source_start"):
            return False
        text = item.text.strip()
        if HEADER_FIELD_RE.search(text) or SCHOOL_NAME_RE.match(text):
            return True
        is_title_like = len(text) < 110 and (
            item.align == "center" or _mostly_upper(text) or all(r.bold for r in item.runs if r.text.strip())
        )
        return is_title_like and bool(_TITLE_RE.search(text))

    def _accept_number(self, number: int, block_issues: list[Issue], label: str) -> bool:
        last = self.last_number
        if last is None or number == last + 1:
            return True
        if last + 1 < number <= last + 3:
            block_issues.append(_issue(
                "numbering_gap",
                f"A numeração pulou da questão {last} para a {number}. Confira se alguma questão se perdeu.",
            ))
            return True
        if number == last:
            block_issues.append(_issue(
                "numbering_repeated", f"O número {label} se repete. Confira se é uma questão nova ou erro de leitura.",
            ))
            return True
        if number == 1:
            block_issues.append(_issue(
                "numbering_restart", "A numeração recomeçou em 1. Confira se é uma nova parte da prova.", "info",
            ))
            return True
        return False

    # -------------------------------------------------------------- handlers
    def _text(self, item: TextItem) -> None:
        text = item.text.strip()
        if not text and not item.list_label:
            return

        if self._is_header_text(item):
            self._ensure("original_header").parts.append(self._part_from_text(item))
            return
        if self.phase == "source_start":
            self.phase = "body"

        if self.last_number is not None and len(text) < 80 and text.endswith("!") \
                and INSTRUCTION_HEADING_RE.match(text) and self.current is not None \
                and self.current.kind == "question":
            # "Leia com atenção!" after the last question is part of the closing words.
            self.closing = self._open("free")
            self.closing.parts.append(self._labelled_paragraph(item, None))
            return

        if INSTRUCTION_HEADING_RE.match(text) and not match_question(text) and not SUPPORT_RE.match(text):
            self.in_instructions = True
            self._ensure("instructions").parts.append(self._part_from_text(item))
            return

        q = self._question_label(item)
        if q is not None and self._is_inner_item(q):
            # "(1) Paisagem natural" inside "4. Relacione…": a column item, not question 1.
            self.current.parts.append(self._labelled_paragraph(item, None))  # type: ignore[union-attr]
            return
        if q is not None:
            rest = text[q.rest_offset:] if q.rest_offset else text
            if (self.in_instructions or self.phase == "start") and INSTRUCTION_ITEM_RE.match(rest):
                self.in_instructions = True
                self._ensure("instructions").parts.append(self._labelled_paragraph(item, q))
                return
            issues: list[Issue] = []
            if self._accept_number(q.number or 0, issues, q.label):
                self.in_instructions = False
                self.last_number = q.number
                if self.question_style is None:
                    self.question_style = _label_style(q.label)
                block = self._open("question", q.label)
                block.issues.extend(issues)
                _, after = split_runs(item.runs, q.rest_offset)
                statement, alt_item = self._split_trailing_alternatives(item, after)
                if statement:
                    block.parts.append(self._part_from_text(item, statement))
                elif item.issues:
                    block.issues.extend(item.issues)
                if alt_item is not None:
                    self._add_alternatives(alt_item, match_alternative(alt_item.text))  # type: ignore[arg-type]
                return
            item.issues.append(_issue(
                "numbering_out_of_sequence",
                f"“{q.label}” parece um número de questão fora de sequência (a anterior era "
                f"{self.last_number}). Foi mantido como texto; se for uma questão, use “Transformar em questão”.",
            ))

        if self.in_instructions and (INSTRUCTION_ITEM_RE.match(text) or (item.list_label and not q)):
            self.current.parts.append(self._labelled_paragraph(item, None))  # type: ignore[union-attr]
            return
        if self.phase == "start" and INSTRUCTION_ITEM_RE.match(text):
            self.in_instructions = True
            self._ensure("instructions").parts.append(self._labelled_paragraph(item, None))
            return

        if len(text) < 200 and SOURCE_LINE_RE.match(text) and self.current is not None \
                and self.current.kind in ("support_text", "free", "question") and self.current is not self.closing:
            # The credit of the text just read stays with it.
            self.current.parts.append(self._labelled_paragraph(item, None))
            return

        if SUPPORT_RE.match(text):
            self.in_instructions = False
            self._open("support_text").parts.append(self._part_from_text(item))
            return

        alt = self._alternative_label(item)
        if alt is not None and self.current is not None and self.current.kind == "question":
            self._add_alternatives(item, alt)
            return

        if self._is_closing(text):
            self.in_instructions = False
            if self.closing is None or self.current is not self.closing:
                self.closing = self._open("free")
            self.closing.parts.append(self._labelled_paragraph(item, None))
            return

        self.in_instructions = False
        current = self.current
        if current is None or current.kind in ("original_header", "instructions"):
            self._open("support_text" if len(text) > 60 else "free").parts.append(self._labelled_paragraph(item, None))
            return
        if current.kind == "question" and any(p.kind == "alternative" for p in current.parts):
            if len(text) < 140 and (_FOLLOW_UP_RE.match(text) or _WORK_LABEL_RE.match(text)
                                    or COLUMN_HEADING_RE.match(text)):
                current.parts.append(self._labelled_paragraph(item, None))
                return
            block = self._open("support_text")
            part = self._labelled_paragraph(item, None)
            block.parts.append(part)
            block.issues.append(_issue(
                "after_alternatives",
                "Este trecho veio depois das alternativas da questão anterior. Confira se é um texto de apoio "
                "ou se pertence à questão de cima (use “Juntar com o anterior”).",
            ))
            return
        if current.kind == "free" and current is not self.closing and len(text) > 60:
            current.kind = "support_text"  # a short heading followed by a text to read
        current.parts.append(self._labelled_paragraph(item, None))

    def _is_inner_item(self, q: LabelMatch) -> bool:
        """A numbered line whose label looks different from the questions' and breaks the sequence."""
        current = self.current
        if current is None or current.kind != "question" or self.question_style is None:
            return False
        style = _label_style(q.label)
        if style == self.question_style:
            return False
        if style == "paren":
            return True  # "(1)", "(2)" under a question numbered "1." are its column items
        return q.number != (self.last_number or 0) + 1

    def _is_closing(self, text: str) -> bool:
        if self.phase != "body" or len(text) > 160 or text.rstrip().endswith(("?", ":")):
            return False
        current = self.current
        if current is None or current.kind == "original_header":
            return False
        if self.closing is not None and current is self.closing:
            return True  # "Boa prova!!" followed by "Eu acredito em você!"
        if _CLOSING_RE.search(text):
            return True
        # A short quotation right after a finished question: “Tudo posso naquele que me fortalece”.
        finished = current.kind == "question" and any(p.kind in ("alternative", "answer_space") for p in current.parts)
        return finished and bool(_QUOTED_LINE_RE.match(text))

    def _split_trailing_alternatives(self, item: TextItem, runs: list[Run]) -> tuple[list[Run], TextItem | None]:
        """Detect "Quantas sílabas…? a) 2  b) 3  c) 4" written on the question line itself."""
        text = runs_text(runs)
        starts = [m for m in INLINE_ALT_RE.finditer(text) if m.start() > 0]
        first = next((m for m in starts if m.group("letter").lower() == "a"
                      and (text[:m.start()].endswith(("  ", "\t", "?", ":")) or text[:m.start()].rstrip()[-1:] in "?:.")),
                     None)
        if first is None:
            return runs, None
        expected = "b"
        found = 1
        for m in starts:
            if m.start() > first.start() and m.group("letter").lower() == expected:
                found += 1
                expected = chr(ord(expected) + 1)
        if found < 2:
            return runs, None
        statement, tail = split_runs(runs, first.start())
        alt_item = TextItem(runs=tail, prov=item.prov, issues=[_issue(
            "inline_alternatives_split",
            "As alternativas estavam na mesma linha do enunciado e foram separadas.", "info",
        )])
        return _rstrip_runs(statement), alt_item

    def _labelled_paragraph(self, item: TextItem, q: LabelMatch | None) -> Part:
        runs = list(item.runs)
        if item.list_label:
            runs = [Run(text=f"{item.list_label} "), *runs]
        return self._part_from_text(item, merge_runs(runs))

    def _add_alternatives(self, item: TextItem, alt: LabelMatch) -> None:
        assert self.current is not None
        _, after = split_runs(item.runs, alt.rest_offset)
        pieces: list[tuple[str, list[Run]]] = [(alt.label, after)]
        # Several alternatives on the same line: "a) 2   b) 4   c) 6"
        if alt.letter and not item.list_label:
            text = runs_text(after)
            expected = chr(ord(alt.letter) + 1)
            cuts: list[tuple[int, int, str]] = []
            for m in INLINE_ALT_RE.finditer(text):
                if m.start() == 0:
                    continue
                # A label after a tab or a wide gap is always a new option, even out of order: options in
                # a grid are often written by column ("A) …⇥C) …" over "B) …⇥D) …").
                gap_before = text[:m.start()].endswith(("\t", "  "))
                if m.group("letter").lower() == expected.lower() or gap_before:
                    cuts.append((m.start(), m.end(), text[m.start():m.end()].strip()))
                    expected = chr(ord(m.group("letter").lower()) + 1)
            if cuts:
                pieces = []
                label = alt.label
                remaining = after
                consumed = 0
                for start, end, next_label in cuts:
                    head, tail = split_runs(remaining, start - consumed)
                    pieces.append((label, _rstrip_runs(head)))
                    _, remaining = split_runs(tail, end - start)
                    consumed = end
                    label = next_label
                pieces.append((label, remaining))
        elif alt.letter is None and not item.list_label:
            # "( ) um lugar.	( ) um animal.	( ) uma planta." — options for marking side by side.
            text = runs_text(after)
            matches = list(_INLINE_BOX_RE.finditer(text))
            if matches:
                pieces = []
                label = alt.label
                remaining = after
                consumed = 0
                for m in matches:
                    start, end = m.start("label"), m.end()
                    head, tail = split_runs(remaining, start - consumed)
                    pieces.append((label, _rstrip_runs(head)))
                    _, remaining = split_runs(tail, end - start)
                    consumed = end
                    label = m.group("label").strip()
                pieces.append((label, remaining))
        for i, (label, runs) in enumerate(pieces):
            part = self._part_from_text(item, runs, kind="alternative", label=label)
            if i:
                part.issues = []
            if re.fullmatch(r"[a-hA-H]{2}\)", label):
                part.issues.append(_issue(
                    "ocr_label_doubtful",
                    f"O rótulo foi lido como “{label}” — provavelmente “{label[-2].lower()})”. Confira e corrija.",
                ))
            # Tabs between the options (the school's Word layout) are unambiguous: no notice needed.
            if len(pieces) > 1 and i == 0 and "\t" not in runs_text(after) \
                    and not any(x.code == "inline_alternatives_split" for x in part.issues):
                part.issues.append(_issue(
                    "inline_alternatives_split",
                    "As alternativas estavam na mesma linha e foram reconhecidas uma a uma. Na impressão, as "
                    "curtas continuam lado a lado.", "info",
                ))
            self.current.parts.append(part)

    def _media(self, item: MediaItem, next_item: Item | None) -> None:
        part = Part(
            kind=item.kind if item.kind in ("image", "crop", "table") else "crop",  # type: ignore[arg-type]
            asset_id=item.asset_id,
            width_cm=item.width_cm,
            rows=item.rows,
            table_borders=item.table_borders,
            provenance=item.prov,
            issues=list(item.issues),
        )
        if item.from_page_header or (self.phase in ("start", "source_start") and (
            self.current is None or self.current.kind == "original_header")
                and self._media_is_header(item, next_item)):
            self._ensure("original_header").parts.append(part)
            return
        current = self.current
        next_starts_question = isinstance(next_item, TextItem) and self._question_label(next_item) is not None
        if current is not None and current.kind == "question" and next_starts_question and any(
            p.kind == "alternative" for p in current.parts
        ):
            part.issues.append(_issue(
                "media_between_questions",
                f"Esta imagem estava entre a questão {current.number} e a seguinte. Ficou com a questão seguinte; "
                "se for da anterior, mude em “Pertence à questão”.",
            ))
            self.pending_media.append(part)
            return
        if current is None or current.kind in ("original_header", "instructions"):
            self.pending_media.append(part)
            if not next_starts_question:
                self._open("support_text")  # _open attaches the pending media
            return
        current.parts.append(part)

    def _media_is_header(self, item: MediaItem, next_item: Item | None) -> bool:
        if item.kind == "table":
            text = " ".join(runs_text(c.runs) for row in item.rows or [] for cell in row for c in cell.content)
            return bool(HEADER_FIELD_RE.search(text))
        # A small image at the very top (logo) followed by header text.
        return isinstance(next_item, TextItem) and self._is_header_text(next_item)

    def _answer_lines(self, item: AnswerLinesItem) -> None:
        if self.current is None or self.current.kind == "original_header":
            return
        last = self.current.parts[-1] if self.current.parts else None
        if last is not None and last.kind == "answer_space" and last.answer is not None:
            if item.blank and last.answer.kind == "blank":
                last.answer.height_cm = round(min(25.0, last.answer.height_cm + item.count * 0.6), 1)
                return
            if not item.blank and last.answer.kind == "lines":
                last.answer.lines = min(40, last.answer.lines + item.count)
                return
        if item.blank:
            space = AnswerSpace(kind="blank", height_cm=round(min(25.0, max(1.5, item.count * 0.6)), 1))
        else:
            space = AnswerSpace(kind="lines", lines=min(40, max(1, item.count)))
        self.current.parts.append(Part(kind="answer_space", answer=space, provenance=item.prov))

    # --------------------------------------------------------------- checks
    def _finalise(self) -> None:
        questions = [b for b in self.blocks if b.kind == "question"]
        # Compare only lettered alternatives: "( )" options (true/false, association) vary by design.
        alt_counts = Counter(
            sum(1 for p in b.parts if p.kind == "alternative") for b in questions
            if any(p.kind == "alternative" and _letter(p.label) for p in b.parts)
        )
        typical = alt_counts.most_common(1)[0][0] if alt_counts else None
        for block in self.blocks:
            # "Cálculo" followed by lines: the caption becomes the label of the answer space.
            if block.kind == "question" and len(block.parts) >= 2 and block.parts[-1].kind == "answer_space":
                spaces = []
                while block.parts and block.parts[-1].kind == "answer_space":
                    spaces.insert(0, block.parts.pop())
                caption = block.parts[-1] if block.parts else None
                if caption is not None and caption.kind == "paragraph" and _WORK_LABEL_RE.match(runs_text(caption.runs)) \
                        and spaces[-1].answer is not None:
                    block.parts.pop()
                    main = next((s for s in reversed(spaces) if s.answer and s.answer.kind == "lines"), spaces[-1])
                    label = runs_text(caption.runs).strip()[:40]
                    block.parts.append(main.model_copy(update={"answer": main.answer.model_copy(  # type: ignore[union-attr]
                        update={"label": label})}))
                else:
                    block.parts.extend(spaces)
            # A trailing answer space becomes the question's answer space.
            if block.parts and block.parts[-1].kind == "answer_space" and block.kind == "question":
                tail = block.parts.pop()
                block.answer_space = tail.answer or AnswerSpace()
            if block.kind != "question":
                continue
            _restore_grid_order(block)
            alternatives = [p for p in block.parts if p.kind == "alternative"]
            letters = [_letter(p.label) for p in alternatives]
            letters = [x for x in letters if x]
            if letters:
                expected = ord(letters[0].lower())
                if letters[0].lower() != "a":
                    block.issues.append(_issue(
                        "alternatives_start", f"As alternativas começam em “{letters[0]})”. Confira se faltou alguma.",
                    ))
                for letter in letters[1:]:
                    expected += 1
                    if ord(letter.lower()) != expected:
                        block.issues.append(_issue(
                            "alternatives_sequence",
                            f"As alternativas não seguem a ordem esperada (encontrada “{letter}” onde se esperava "
                            f"“{chr(expected)}”). Confira se alguma se perdeu ou se repetiu.",
                        ))
                        break
            if typical and letters and len(alternatives) < typical and alt_counts[typical] >= 2:
                block.issues.append(_issue(
                    "alternatives_count",
                    f"Esta questão tem {len(alternatives)} alternativas; a maioria tem {typical}. "
                    "Confira se alguma se perdeu.",
                ))
            if not [p for p in block.parts if p.kind != "answer_space"]:
                block.issues.append(_issue("empty_question", "Esta questão ficou sem enunciado. Confira o original."))
            has_space = block.answer_space.kind != "none" or any(p.kind == "answer_space" for p in block.parts)
            if not alternatives and not has_space:
                block.issues.append(_issue(
                    "no_answer_space",
                    "Questão sem alternativas e sem espaço de resposta. Deseja adicionar linhas para a resposta?",
                    "info",
                ))
        if not questions:
            self.doc_issues.append(_issue(
                "no_questions",
                "Não encontramos questões numeradas automaticamente. Selecione um bloco e use "
                "“Transformar em questão” para organizar.",
            ))


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha() and c not in "ºª"]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) >= 0.9


def _rstrip_runs(runs: list[Run]) -> list[Run]:
    out = list(runs)
    while out and not out[-1].text.strip():
        out.pop()
    if out:
        out[-1] = out[-1].model_copy(update={"text": out[-1].text.rstrip(" \t")})
    return out


def _letter(label: str | None) -> str | None:
    if not label:
        return None
    m = re.search(r"[a-hA-H]", label)
    return m.group(0) if m else None


def _restore_grid_order(block: Block) -> None:
    """Options written in a grid by column ("A) …  C) …" over "B) …  D) …") are read row by row as
    A, C, B, D. When the letters are exactly a, b, c… just shuffled, put them back in order."""
    slots = [i for i, p in enumerate(block.parts) if p.kind == "alternative"]
    if len(slots) < 3 or slots != list(range(slots[0], slots[0] + len(slots))):
        return
    letters = [(_letter(block.parts[i].label) or "").lower() for i in slots]
    expected = [chr(ord("a") + k) for k in range(len(slots))]
    if letters == expected or sorted(letters) != expected:
        return
    ordered = sorted((block.parts[i] for i in slots), key=lambda p: (_letter(p.label) or "").lower())
    for i, part in zip(slots, ordered, strict=True):
        block.parts[i] = part
    block.issues.append(_issue(
        "alternatives_reordered",
        "As alternativas estavam em colunas no original e foram colocadas na ordem A, B, C, D.", "info",
    ))


def segment(items: list[Item]) -> SegmentResult:
    return Segmenter().run(items)
