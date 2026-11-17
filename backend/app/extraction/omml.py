"""Linearise Word equations (OMML) into text runs.

Equations are not exposed by python-docx. Dropping them would delete content, so we
convert the common constructs (fractions, powers, indices, roots, delimiters, n-ary
operators) to linear text with real superscript/subscript runs — e.g. ``x²``,
``(a+b)/(c)``, ``√(x+1)``. The caller always flags the result for human review
because a linear form is never a perfect substitute for a typeset formula.
"""

from __future__ import annotations

import re

from app.domain.models import Run, merge_runs

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _m(tag: str) -> str:
    return f"{{{M_NS}}}{tag}"


_CONTAINERS = {"oMath", "e", "num", "den", "sub", "sup", "deg", "fName", "lim", "box", "borderBox", "phant"}
_SIMPLE = re.compile(r"[\w.,]+|.", re.UNICODE)


class _Ctx:
    def __init__(self) -> None:
        self.unsupported: set[str] = set()


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _child(el, name: str):
    return el.find(_m(name))


def _prop(el, prop: str, attr: str, default: str | None) -> str | None:
    pr = el.find(_m(prop))
    if pr is None:
        return default
    node = pr.find(_m(attr))
    if node is None:
        return default
    return node.get(_m("val"), default if default is not None else "")


def _text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


def _styled(runs: list[Run], *, sup: bool = False, sub: bool = False) -> list[Run]:
    return [r.model_copy(update={"sup": sup or r.sup, "sub": sub or r.sub}) for r in runs]


def _wrap(runs: list[Run]) -> list[Run]:
    text = _text(runs).strip()
    if not text or _SIMPLE.fullmatch(text):
        return runs
    return [Run(text="("), *runs, Run(text=")")]


def _conv(el, ctx: _Ctx) -> list[Run]:
    name = _local(el.tag)
    if el.tag.startswith(f"{{{W_NS}}}"):
        # Word runs embedded in math (rare) — take their text.
        return [Run(text="".join(t.text or "" for t in el.iter(f"{{{W_NS}}}t")))]
    if name.endswith("Pr") or name == "ctrlPr":
        return []
    if name == "r":
        return [Run(text="".join(t.text or "" for t in el.iter(_m("t"))))]
    if name in _CONTAINERS:
        return _concat(el, ctx)
    if name == "oMathPara":
        parts: list[Run] = []
        for i, math in enumerate(el.findall(_m("oMath"))):
            if i:
                parts.append(Run(text="\n"))
            parts.extend(_conv(math, ctx))
        return parts
    if name == "f":
        num = _conv(_child(el, "num"), ctx) if _child(el, "num") is not None else []
        den = _conv(_child(el, "den"), ctx) if _child(el, "den") is not None else []
        if _prop(el, "fPr", "type", "bar") == "noBar":
            ctx.unsupported.add("binomial")
            return [Run(text="("), *num, Run(text=" "), *den, Run(text=")")]
        return [*_wrap(num), Run(text="/"), *_wrap(den)]
    if name in ("sSup", "sSub", "sSubSup", "sPre"):
        base = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        sub = _styled(_conv(_child(el, "sub"), ctx), sub=True) if _child(el, "sub") is not None else []
        sup = _styled(_conv(_child(el, "sup"), ctx), sup=True) if _child(el, "sup") is not None else []
        if name == "sPre":
            return [*sub, *sup, *base]
        return [*base, *sub, *sup]
    if name == "rad":
        body = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        deg_el = _child(el, "deg")
        deg = _text(_conv(deg_el, ctx)).strip() if deg_el is not None else ""
        if _prop(el, "radPr", "degHide", "0") in ("1", "on", "true"):
            deg = ""
        symbol = {"": "√", "2": "√", "3": "∛", "4": "∜"}.get(deg)
        prefix = [Run(text=symbol)] if symbol else [Run(text=deg, sup=True), Run(text="√")]
        return [*prefix, *_wrap(body)]
    if name == "d":
        beg = _prop(el, "dPr", "begChr", "(")
        end = _prop(el, "dPr", "endChr", ")")
        sep = _prop(el, "dPr", "sepChr", "|")
        out: list[Run] = [Run(text=beg or "")]
        for i, e in enumerate(el.findall(_m("e"))):
            if i:
                out.append(Run(text=sep or ""))
            out.extend(_conv(e, ctx))
        out.append(Run(text=end or ""))
        return out
    if name == "nary":
        symbol = _prop(el, "naryPr", "chr", "∫") or "∫"
        sub = _styled(_conv(_child(el, "sub"), ctx), sub=True) if _child(el, "sub") is not None else []
        sup = _styled(_conv(_child(el, "sup"), ctx), sup=True) if _child(el, "sup") is not None else []
        body = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        return [Run(text=symbol), *sub, *sup, Run(text=" "), *body]
    if name == "func":
        fname = _conv(_child(el, "fName"), ctx) if _child(el, "fName") is not None else []
        body = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        spacer = [] if _text(body).startswith("(") else [Run(text=" ")]
        return [*fname, *spacer, *body]
    if name == "acc":
        body = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        mark = _prop(el, "accPr", "chr", "̂") or "̂"
        return [*body, Run(text=mark)]
    if name in ("bar", "groupChr"):
        ctx.unsupported.add(name)
        return _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
    if name in ("limLow", "limUpp"):
        body = _conv(_child(el, "e"), ctx) if _child(el, "e") is not None else []
        lim = _conv(_child(el, "lim"), ctx) if _child(el, "lim") is not None else []
        return [*body, *_styled(lim, sub=name == "limLow", sup=name == "limUpp")]
    if name == "eqArr":
        out = []
        for i, e in enumerate(el.findall(_m("e"))):
            if i:
                out.append(Run(text="\n"))
            out.extend(_conv(e, ctx))
        return out
    if name == "m":
        ctx.unsupported.add("matriz")
        rows = []
        for mr in el.findall(_m("mr")):
            rows.append(" ".join(_text(_conv(e, ctx)) for e in mr.findall(_m("e"))))
        return [Run(text="[" + "; ".join(rows) + "]")]
    ctx.unsupported.add(name)
    return _concat(el, ctx)


def _concat(el, ctx: _Ctx) -> list[Run]:
    out: list[Run] = []
    for child in el:
        out.extend(_conv(child, ctx))
    return out


def omml_to_runs(el) -> tuple[list[Run], list[str]]:
    ctx = _Ctx()
    runs = merge_runs(_conv(el, ctx))
    return runs, sorted(ctx.unsupported)
