"""Reading order for positioned text (digital PDFs and OCR).

Turns positioned words into lines, detects two-column pages (so the left column is
read before the right one instead of interleaving them), and merges lines back into
paragraphs. Coordinates are in page units; boxes are normalised when stored.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.domain.models import Run, merge_runs
from app.structuring.patterns import ALT_RE, is_structural_start


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    bold: bool = False
    italic: bool = False
    underline: bool = False
    sup: bool = False
    sub: bool = False
    size: float = 0.0
    conf: float | None = None
    block: int | None = None
    blank: bool = False  # a fill-in blank ("____") reconstructed from a drawn rule

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.1)


@dataclass
class Line:
    words: list[Word]
    page: int
    column: int = 0  # 0 = full width, 1 = left, 2 = right
    col_x0: float = 0.0
    col_x1: float = 0.0

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def height(self) -> float:
        heights = [w.height for w in self.words if not (w.sup or w.sub)]
        return statistics.median(heights) if heights else self.bottom - self.top

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def confidence(self) -> float | None:
        confs = [w.conf for w in self.words if w.conf is not None and w.conf >= 0]
        return sum(confs) / len(confs) if confs else None

    def runs(self) -> list[Run]:
        runs: list[Run] = []
        for i, w in enumerate(self.words):
            if i:
                prev = self.words[i - 1]
                # No space before a superscript/subscript glued to the previous word (x², H₂O).
                glued = (w.sup or w.sub or prev.sup or prev.sub) and w.x0 - prev.x1 < w.height * 0.25
                # Fill-in blanks inside a word ("pa__aro") keep no spaces around them.
                glued = glued or ((w.blank or prev.blank) and w.x0 - prev.x1 < max(w.height, prev.height) * 0.2)
                if not glued:
                    runs.append(Run(text=" ", bold=w.bold and prev.bold, italic=w.italic and prev.italic,
                                    underline=w.underline and prev.underline))
            runs.append(Run(text=w.text, bold=w.bold, italic=w.italic, underline=w.underline, sup=w.sup, sub=w.sub))
        return merge_runs(runs)


@dataclass
class Paragraph:
    lines: list[Line] = field(default_factory=list)
    two_columns: bool = False

    @property
    def page(self) -> int:
        return self.lines[0].page

    def runs(self) -> tuple[list[Run], list[str]]:
        """Join lines with spaces; re-join words hyphenated at line ends. Returns (runs, joined_words)."""
        out: list[Run] = []
        joined: list[str] = []
        for i, line in enumerate(self.lines):
            line_runs = line.runs()
            if i and out:
                last = out[-1]
                first_text = line_runs[0].text if line_runs else ""
                if (
                    last.text.endswith("-")
                    and len(last.text) >= 2
                    and last.text[-2].isalpha()
                    and first_text[:1].islower()
                ):
                    tail = last.text.rstrip("-").split(" ")[-1]
                    head = first_text.split(" ")[0]
                    joined.append(f"{tail}-{head}")
                    out[-1] = last.model_copy(update={"text": last.text[:-1]})
                else:
                    out.append(Run(text=" "))
            out.extend(line_runs)
        return merge_runs(out), joined

    def bbox(self) -> tuple[float, float, float, float]:
        page_lines = [ln for ln in self.lines if ln.page == self.page]
        return (
            min(ln.x0 for ln in page_lines),
            min(ln.top for ln in page_lines),
            max(ln.x1 for ln in page_lines),
            max(ln.bottom for ln in page_lines),
        )

    @property
    def confidence(self) -> float | None:
        confs = [c for ln in self.lines if (c := ln.confidence) is not None]
        return sum(confs) / len(confs) if confs else None

    @property
    def align(self) -> str | None:
        return "center" if self.lines and all(_is_centered(ln) for ln in self.lines) else None

    @property
    def low_conf_words(self) -> list[str]:
        return [w.text for ln in self.lines for w in ln.words if w.conf is not None and 0 <= w.conf < 60]


# ----------------------------------------------------------------------- lines


def group_lines(words: list[Word], page: int) -> list[Line]:
    """Cluster words into lines by vertical overlap, then sort each line left to right."""
    lines: list[Line] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        target = None
        for line in reversed(lines[-6:]):
            overlap = min(line.bottom, word.bottom) - max(line.top, word.top)
            if overlap > 0.5 * min(line.height, word.height) or (
                (word.sup or word.sub) and overlap > 0 and word.x0 >= line.x0
            ):
                target = line
                break
        if target is None:
            lines.append(Line(words=[word], page=page))
        else:
            target.words.append(word)
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: (ln.top, ln.x0))
    return lines


def detect_gutter(words: list[Word], page_width: float) -> tuple[float, float] | None:
    """Find an empty vertical band near the middle of the page separating two text columns."""
    if len(words) < 40:
        return None
    bins = 200
    # Count, for each vertical strip, how many text lines cross it. A gutter is crossed only by
    # the few full-width lines (title, header table), so this works even below a wide header.
    lines = group_lines(words, 0)
    coverage = [0] * bins
    for line in lines:
        hit: set[int] = set()
        for w in line.words:
            start = max(0, int(w.x0 / page_width * bins))
            end = min(bins - 1, int(w.x1 / page_width * bins))
            hit.update(range(start, end + 1))
        for b in hit:
            coverage[b] += 1
    lo, hi = int(bins * 0.3), int(bins * 0.7)
    floor = min(coverage[lo:hi + 1])
    if floor > len(lines) * 0.22:
        return None
    # The gutter is the emptiest band, not merely a sparse one (short lines leave sparse areas).
    threshold = floor + max(1, int(len(lines) * 0.03))
    best: tuple[int, int] | None = None
    run_start = None
    for b in range(lo, hi + 1):
        empty = coverage[b] <= threshold
        if empty and run_start is None:
            run_start = b
        if (not empty or b == hi) and run_start is not None:
            run_end = b if empty else b - 1
            if best is None or run_end - run_start > best[1] - best[0]:
                best = (run_start, run_end)
            run_start = None
    if best is None or best[1] - best[0] + 1 < 3:
        return None
    g0 = best[0] / bins * page_width
    g1 = (best[1] + 1) / bins * page_width
    left = [w for w in words if w.x1 <= g0]
    right = [w for w in words if w.x0 >= g1]
    if len(left) < len(words) * 0.15 or len(right) < len(words) * 0.15:
        return None
    # Both columns must share vertical space (otherwise it is just indentation).
    left_rows = {int(w.top // 10) for w in left}
    right_rows = {int(w.top // 10) for w in right}
    if len(left_rows & right_rows) < 5:
        return None
    return g0, g1


def order_page_lines(
    words: list[Word], page: int, page_width: float, text_x0: float, text_x1: float
) -> tuple[list[Line], bool]:
    """Return lines in reading order and whether the page was read as two columns."""
    lines = group_lines(words, page)
    for line in lines:
        line.col_x0, line.col_x1 = text_x0, text_x1
    gutter = detect_gutter(words, page_width)
    if gutter is None:
        return lines, False
    g0, g1 = gutter
    gmid = (g0 + g1) / 2
    ordered: list[Line] = []
    left_buf: list[Line] = []
    right_buf: list[Line] = []

    def flush() -> None:
        ordered.extend(sorted(left_buf, key=lambda ln: ln.top))
        ordered.extend(sorted(right_buf, key=lambda ln: ln.top))
        left_buf.clear()
        right_buf.clear()

    for line in lines:
        left_words = [w for w in line.words if w.x1 <= gmid]
        right_words = [w for w in line.words if w.x0 >= gmid]
        crossing = [w for w in line.words if w.x0 < gmid < w.x1]
        is_full = bool(crossing)
        if not is_full and left_words and right_words:
            gap = right_words[0].x0 - left_words[-1].x1
            is_full = gap < max(w.height for w in line.words) * 1.2
        if is_full:
            flush()
            line.column = 0
            ordered.append(line)
            continue
        if left_words:
            left_buf.append(Line(words=left_words, page=page, column=1, col_x0=text_x0, col_x1=g0))
        if right_words:
            right_buf.append(Line(words=right_words, page=page, column=2, col_x0=g1, col_x1=text_x1))
    flush()
    return ordered, True


# ------------------------------------------------------------------ paragraphs


def _all_bold(line: Line) -> bool:
    return all(w.bold for w in line.words if w.text.strip())


def _is_centered(line: Line) -> bool:
    width = line.col_x1 - line.col_x0
    if width <= 0:
        return False
    left = line.x0 - line.col_x0
    right = line.col_x1 - line.x1
    return left > width * 0.12 and abs(left - right) < width * 0.06


def lines_to_paragraphs(lines: list[Line]) -> list[Paragraph]:
    if not lines:
        return []
    heights = [ln.height for ln in lines]
    median_h = statistics.median(heights) if heights else 10
    paragraphs: list[Paragraph] = []
    current = Paragraph()
    for line in lines:
        if not current.lines:
            current.lines.append(line)
            continue
        prev = current.lines[-1]
        same_flow = prev.page == line.page and prev.column == line.column
        gap = line.top - prev.bottom
        width = max(prev.col_x1 - prev.col_x0, 1)
        prev_text = prev.text.rstrip()
        prev_is_full = prev.x1 > prev.col_x1 - width * 0.08
        wrapped = (same_flow and prev_is_full and not prev_text.endswith((".", ":", "?", "!", ";", ")"))
                   and gap < median_h * 0.45)
        new = False
        if is_structural_start(line.text) and not wrapped:
            # A full line without final punctuation followed by "02." is a wrapped sentence, not a question.
            new = True
        elif same_flow and gap > median_h * 0.85:
            new = True
        elif prev_text.endswith((".", ":", "?", "!", ";")) and prev.x1 < prev.col_x1 - width * 0.15:
            new = True
        elif prev_text.endswith((".", ":", "?", "!")) and line.x0 > prev.x0 + width * 0.03:
            new = True
        elif _all_bold(prev) != _all_bold(line) and (_all_bold(prev) or prev_text.endswith(":")):
            new = True
        elif _is_centered(prev) or _is_centered(line):
            new = True
        elif ALT_RE.match(prev.text) and line.x0 < prev.x0 - width * 0.02:
            new = True
        elif not same_flow and prev_text.endswith((".", ":", "?", "!")) and not line.text[:1].islower():
            new = True
        if new:
            paragraphs.append(current)
            current = Paragraph()
        current.lines.append(line)
    if current.lines:
        paragraphs.append(current)
    return paragraphs
