"""Layout rules from the school's exam research (119 real exams, 2024–2026).

- Up to four A4 pages for the student's booklet.
- Body text of 14 pt in the 1st and 2nd years, at least 12 pt from the 3rd to the 5th and at
  least 11 pt from the 6th to the 9th (the school's own standard is Arial 12).
"""

from __future__ import annotations

import re

MAX_PAGES = 4


def school_year(ano: str) -> int | None:
    m = re.search(r"([1-9])", ano or "")
    return int(m.group(1)) if m else None


def recommended_font_size(ano: str) -> float:
    year = school_year(ano)
    return 14.0 if year in (1, 2) else 12.0


def minimum_font_size(ano: str) -> float | None:
    year = school_year(ano)
    if year is None:
        return None
    if year <= 2:
        return 14.0
    if year <= 5:
        return 12.0
    return 11.0
