"""Resolve Word automatic numbering into the labels the reader actually sees.

python-docx exposes paragraph text only; numbers produced by Word lists ("1.",
"a)", "II -") live in ``numbering.xml``. Teachers very often number questions and
alternatives this way, so ignoring it would silently drop question numbers. This
module replays Word's counters in document order to rebuild each label.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docx.document import Document as DocxDocument
from docx.oxml.ns import qn

_BULLET_MAP = {
    "": "•",
    "": "▪",
    "": "➢",
    "": "✓",
    "": "❖",
    "": "□",
    "o": "◦",
    "": "•",
}


def _to_letters(n: int, upper: bool) -> str:
    if n <= 0:
        return str(n)
    # Word repeats the letter: 27 -> "aa", 28 -> "bb"
    letter = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
    return letter.upper() if upper else letter


def _to_roman(n: int, upper: bool) -> str:
    if n <= 0 or n >= 4000:
        return str(n)
    values = [
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ]
    out = []
    for value, sym in values:
        while n >= value:
            out.append(sym)
            n -= value
    roman = "".join(out)
    return roman.upper() if upper else roman


def format_number(n: int, fmt: str) -> tuple[str, bool]:
    """Return (text, supported)."""
    match fmt:
        case "decimal":
            return str(n), True
        case "decimalZero":
            return f"{n:02d}", True
        case "lowerLetter":
            return _to_letters(n, False), True
        case "upperLetter":
            return _to_letters(n, True), True
        case "lowerRoman":
            return _to_roman(n, False), True
        case "upperRoman":
            return _to_roman(n, True), True
        case "ordinal":
            return f"{n}º", True
        case "none":
            return "", True
        case _:
            return str(n), False


@dataclass
class _Level:
    start: int = 1
    fmt: str = "decimal"
    text: str = "%1."
    is_lgl: bool = False


@dataclass
class _Num:
    abstract_id: str
    start_overrides: dict[int, int] = field(default_factory=dict)


@dataclass
class ListLabel:
    text: str
    level: int
    is_bullet: bool
    supported: bool


class NumberingResolver:
    def __init__(self, document: DocxDocument):
        self._abstract: dict[str, dict[int, _Level]] = {}
        self._nums: dict[str, _Num] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self._seen_nums: set[str] = set()
        self._style_numpr: dict[str, tuple[str | None, int | None, str | None]] = {}
        self._load_numbering(document)
        self._load_styles(document)

    # ---------------------------------------------------------------- loading
    def _load_numbering(self, document: DocxDocument) -> None:
        try:
            numbering = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        for absn in numbering.findall(qn("w:abstractNum")):
            abs_id = absn.get(qn("w:abstractNumId"))
            levels: dict[int, _Level] = {}
            for lvl in absn.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl"), "0"))
                level = _Level()
                if (el := lvl.find(qn("w:start"))) is not None:
                    level.start = int(el.get(qn("w:val"), "1"))
                if (el := lvl.find(qn("w:numFmt"))) is not None:
                    level.fmt = el.get(qn("w:val"), "decimal")
                if (el := lvl.find(qn("w:lvlText"))) is not None:
                    level.text = el.get(qn("w:val"), "")
                level.is_lgl = lvl.find(qn("w:isLgl")) is not None
                levels[ilvl] = level
            self._abstract[abs_id] = levels
        for num in numbering.findall(qn("w:num")):
            num_id = num.get(qn("w:numId"))
            abs_el = num.find(qn("w:abstractNumId"))
            if abs_el is None:
                continue
            entry = _Num(abstract_id=abs_el.get(qn("w:val")))
            for override in num.findall(qn("w:lvlOverride")):
                ilvl = int(override.get(qn("w:ilvl"), "0"))
                start = override.find(qn("w:startOverride"))
                if start is not None:
                    entry.start_overrides[ilvl] = int(start.get(qn("w:val"), "1"))
            self._nums[num_id] = entry

    def _load_styles(self, document: DocxDocument) -> None:
        try:
            styles = document.styles.element
        except AttributeError:
            return
        for style in styles.findall(qn("w:style")):
            sid = style.get(qn("w:styleId"))
            based = style.find(qn("w:basedOn"))
            num_id, ilvl = None, None
            ppr = style.find(qn("w:pPr"))
            if ppr is not None and (numpr := ppr.find(qn("w:numPr"))) is not None:
                if (el := numpr.find(qn("w:numId"))) is not None:
                    num_id = el.get(qn("w:val"))
                if (el := numpr.find(qn("w:ilvl"))) is not None:
                    ilvl = int(el.get(qn("w:val"), "0"))
            self._style_numpr[sid] = (num_id, ilvl, based.get(qn("w:val")) if based is not None else None)

    def _style_lookup(self, style_id: str | None) -> tuple[str | None, int | None]:
        seen: set[str] = set()
        while style_id and style_id not in seen:
            seen.add(style_id)
            num_id, ilvl, based = self._style_numpr.get(style_id, (None, None, None))
            if num_id is not None:
                return num_id, ilvl
            style_id = based
        return None, None

    # ---------------------------------------------------------------- labels
    def label_for(self, p_el) -> ListLabel | None:
        num_id: str | None = None
        ilvl: int | None = None
        ppr = p_el.find(qn("w:pPr"))
        style_id = None
        if ppr is not None:
            if (pstyle := ppr.find(qn("w:pStyle"))) is not None:
                style_id = pstyle.get(qn("w:val"))
            if (numpr := ppr.find(qn("w:numPr"))) is not None:
                if (el := numpr.find(qn("w:numId"))) is not None:
                    num_id = el.get(qn("w:val"))
                if (el := numpr.find(qn("w:ilvl"))) is not None:
                    ilvl = int(el.get(qn("w:val"), "0"))
        if num_id is None:
            num_id, style_ilvl = self._style_lookup(style_id)
            ilvl = ilvl if ilvl is not None else style_ilvl
        if not num_id or num_id == "0" or num_id not in self._nums:
            return None
        ilvl = ilvl or 0
        num = self._nums[num_id]
        levels = self._abstract.get(num.abstract_id, {})
        level = levels.get(ilvl, _Level())
        counters = self._counters.setdefault(num.abstract_id, {})

        if num_id not in self._seen_nums:
            self._seen_nums.add(num_id)
            for lvl_idx, start in num.start_overrides.items():
                counters[lvl_idx] = start - 1
                for deeper in [k for k in counters if k > lvl_idx]:
                    del counters[deeper]

        counters[ilvl] = counters.get(ilvl, level.start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]

        if level.fmt == "bullet":
            symbol = _BULLET_MAP.get(level.text, level.text if level.text.isprintable() else "•")
            return ListLabel(text=symbol or "•", level=ilvl, is_bullet=True, supported=True)

        supported = True
        text = level.text
        for ref in range(9, 0, -1):
            placeholder = f"%{ref}"
            if placeholder not in text:
                continue
            ref_level = levels.get(ref - 1, _Level())
            value = counters.get(ref - 1, ref_level.start)
            fmt = "decimal" if (level.is_lgl and ref - 1 != ilvl) else ref_level.fmt
            formatted, ok = format_number(value, fmt)
            supported = supported and ok
            text = text.replace(placeholder, formatted)
        return ListLabel(text=text, level=ilvl, is_bullet=False, supported=supported)
