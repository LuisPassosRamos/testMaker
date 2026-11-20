"""Read subject, year, unit and kind of exam from the header text (no AI needed).

School titles follow a few shapes: "Avaliação Final I Unidade - Ciências 1º ano",
"AVALIAÇÃO DE CIÊNCIAS 3º ANO III UNIDADE", "Prova de recuperação matemática 7º ano".
"""

from __future__ import annotations

import re
import unicodedata

from app.ai.organize import TitleData
from app.domain.models import Block, runs_text

_SUBJECTS = [
    ("educacao fisica", "Educação Física"),
    ("ensino religioso", "Ensino Religioso"),
    ("religiao", "Ensino Religioso"),
    ("lingua portuguesa", "Português"),
    ("portugues", "Português"),
    ("matematica", "Matemática"),
    ("ciencias", "Ciências"),
    ("historia", "História"),
    ("geografia", "Geografia"),
    ("ingles", "Inglês"),
    ("lingua inglesa", "Inglês"),
    ("arte", "Arte"),
    ("artes", "Arte"),
    ("projeto de leitura", "Projeto de Leitura"),
]
_YEAR_RE = re.compile(r"\b([1-9])\s*(?:º|°|o|ª)?\s*ano\b")
_UNIT_RE = re.compile(r"\b(iv|iii|ii|i|[1-4])\s*(?:ª|º|°|a)?\s*(unidade|trimestre|periodo|bimestre)\b")
_UNIT_RE_AFTER = re.compile(r"\b(unidade|trimestre|periodo)\s*(iv|iii|ii|i|[1-4])\b")
_ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "i": "I", "ii": "II", "iii": "III", "iv": "IV"}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def header_text(blocks: list[Block], limit: int = 12) -> str:
    """Text of the original header (or the first lines when there is none)."""
    pieces: list[str] = []
    for block in blocks[:4]:
        if block.kind not in ("original_header", "free", "instructions") and pieces:
            break
        for part in block.parts:
            text = runs_text(part.runs).strip()
            if text:
                pieces.append(text)
            for row in part.rows or []:
                for cell in row:
                    pieces.extend(runs_text(c.runs) for c in cell.content)
        if len(pieces) >= limit:
            break
    return " \n ".join(pieces[:limit])


def parse_title(text: str) -> TitleData:
    folded = _fold(text)
    data = TitleData()
    for key, name in _SUBJECTS:
        if re.search(rf"\b{key}\b", folded):
            data.disciplina = name
            break
    if m := _YEAR_RE.search(folded):
        data.ano = f"{m.group(1)}º ano"
    m = _UNIT_RE.search(folded)
    if m:
        data.unidade = _ROMAN.get(m.group(1))  # type: ignore[assignment]
        word = m.group(2)
        data.periodo = "trimestre" if word == "trimestre" else "periodo" if word == "periodo" else "unidade"
    elif m := _UNIT_RE_AFTER.search(folded):
        data.unidade = _ROMAN.get(m.group(2))  # type: ignore[assignment]
        word = m.group(1)
        data.periodo = "trimestre" if word == "trimestre" else "periodo" if word == "periodo" else "unidade"
    if "recuperacao" in folded:
        data.modalidade = "recuperacao"
    elif "segunda chamada" in folded:
        data.modalidade = "segunda_chamada"
    elif "simulado" in folded:
        data.modalidade = "simulado"
    elif re.search(r"\bparcial\b", folded):
        data.modalidade = "parcial"
    elif re.search(r"\b(avaliacao|prova)\s+final\b|\bfinal\b", folded):
        data.modalidade = "final"
    if re.search(r"\b(avaliacao|prova|teste)\b", folded):
        data.tipo = "prova"
    elif re.search(r"\batividade", folded):
        data.tipo = "atividade"
    elif re.search(r"\brevisao\b", folded):
        data.tipo = "revisao"
    return data
