"""Patterns used to recognise the structure of Brazilian school exams.

Deterministic rules (no AI): question labels ("1)", "01.", "Questão 3 –"),
alternatives ("a)", "(B)", "( )"), instruction sections, header fields and support
texts ("Leia o texto…", "Texto para as questões 3 e 4"). Labels are captured exactly
as written so they can be reproduced without renumbering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

QUESTION_RE = re.compile(
    r"""^\s*(?P<label>
        (?:QUEST[ÃA]O|Quest[ãa]o|questao|QUESTAO)\s*(?:n[º°o]\.?\s*)?(?P<n1>\d{1,3})\s*[\.\)\-–—:]?
      | (?P<n2>\d{1,3})\s*[ºª°]?\s*(?:\)|\.(?=\s|$)|\s?[-–—](?=\s))
      | \(\s*(?P<n3>\d{1,3})\s*\)
    )\s*""",
    re.VERBOSE,
)

ALT_RE = re.compile(
    r"""^\s*(?P<label>
        \(\s*(?P<l1>[a-hA-H])\s*\)
      | (?P<l2>[a-hA-H])\s*\)
      | (?P<l4>[a-hA-H])(?P<dup>[a-hA-H])\)   # OCR misread such as "Cc)" (validated below)
      | (?P<l3>[a-eA-E])\s*[\.\-–—](?=\s)
      | \(\s{0,3}\)
      | \[\s{0,3}\]
      | [□☐❑○◯]
    )\s*""",
    re.VERBOSE,
)

# Several alternatives written on the same line: "a) 2   b) 4   c) 6"
INLINE_ALT_RE = re.compile(r"(?:(?<=\s)|^)\(?(?P<letter>[a-hA-H])\)\s*")

INSTRUCTION_HEADING_RE = re.compile(
    r"^\s*(instru[çc][õo]es|orienta[çc][õo]es|leia\s+(com\s+aten[çc][ãa]o|atentamente)\b|aten[çc][ãa]o\s*[:!])",
    re.IGNORECASE,
)
INSTRUCTION_ITEM_RE = re.compile(
    r"^\s*(use|utilize|leia\s+(com|atentamente|todas)|responda|n[ãa]o\s|[ée]\s+proibid|a\s+prova|esta\s+prova|"
    r"marque|preencha|confira|escreva\s+(seu|o\s+seu)\s+nome|evite|mantenha|desligue|o\s+uso|as\s+respostas|"
    r"rasura|quest[õo]es\s+rasurad|tempo|dura[çc][ãa]o|boa\s+prova|caneta|l[áa]pis)",
    re.IGNORECASE,
)
HEADER_FIELD_RE = re.compile(
    r"\b(escola|col[ée]gio|aluno\(?a?\)?|estudante|nome|professor\(?a?\)?|prof[ªa]?\.?|data|turma|"
    r"s[ée]rie|ano|disciplina|componente(\s+curricular)?|nota|valor|unidade|bimestre|trimestre|turno|"
    r"matr[íi]cula|n[º°])\s*:",
    re.IGNORECASE,
)
SCHOOL_NAME_RE = re.compile(r"^\s*(escola|col[ée]gio|centro\s+educacional|creche|prefeitura|secretaria)\b", re.I)

SUPPORT_RE = re.compile(
    r"""^\s*(
        texto\s*(\d+|[ivx]+)?\s*(?:[:\-–—]|$)
      | leia\s+(?:atentamente\s+|com\s+aten[çc][ãa]o\s+)?(?:o|os|a|as)\s+(?:seguinte\s+)?(?:texto|trecho|poema|f[áa]bula|fragmento|tirinha|
            charge|tabela|gr[áa]fico|not[íi]cia|reportagem|poesia|hist[óo]ria|conto|letra|cr[ôo]nica|an[úu]ncio)
      | observe\s+(?:atentamente\s+|com\s+aten[çc][ãa]o\s+)?(?:a|o|as|os)\s+(?:seguinte\s+)?(?:imagem|imagens|figura|figuras|
            tirinha|charge|tabela|gr[áa]fico|mapa|ilustra)
      | analise\s+(?:a|o|as|os)\s+(?:imagem|figura|tabela|gr[áa]fico|mapa)
      | (?:leia|observe|analise)\s*[:.]?\s*$
      | .*\bpara\s+(?:responder\s+)?(?:[àa]s?|as)\s+quest[õo]es\b
      | .*\bquest[õo]es\s+\d+\s*(?:a|e|-|–|at[ée])\s*\d+
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Credit line of a support text: "Fonte: …", "Texto: Tudo Sala de Aula, 2021", "Disponível em: …".
SOURCE_LINE_RE = re.compile(
    r"^\s*\(?(fonte\s*:|texto\s*:\s*\S|dispon[íi]vel\s+(em|no|na)\b|adaptado\s+de\b|extra[íi]do\s+de\b|"
    r"adaptado\)?\s*\.?$)",
    re.IGNORECASE,
)
# "COLUNA 2:" heading inside an association question.
COLUMN_HEADING_RE = re.compile(r"^\s*coluna\s*(\d+|[a-d]|[ivx]+)?\s*[:.]?\s*$", re.IGNORECASE)

PAGE_NUMBER_RE = re.compile(r"^\s*((p[áa]gina|p[áa]g\.?)\s*)?\d{1,3}(\s*(de|/)\s*\d{1,3})?\s*$", re.IGNORECASE)
UNDERSCORE_LINE_RE = re.compile(r"^[\s_.\-–—]{10,}$")
BULLET_RE = re.compile(r"^\s*[•▪◦●■➢\-–]\s+")


@dataclass
class LabelMatch:
    label: str
    number: int | None
    letter: str | None
    rest_offset: int  # index in the text where the content after the label starts


def match_question(text: str) -> LabelMatch | None:
    m = QUESTION_RE.match(text)
    if not m:
        return None
    number = next(int(g) for g in (m.group("n1"), m.group("n2"), m.group("n3")) if g)
    label = m.group("label").strip()
    rest = text[m.end():]
    # "10 - 3 = 7" is arithmetic, not question 10.
    if m.group("n2") and label[-1:] in "-–—" and _ARITHMETIC.match(rest):
        return None
    return LabelMatch(label=label, number=number, letter=None, rest_offset=m.end())


_ARITHMETIC = re.compile(r"^\s*\d+([.,]\d+)?\s*([=+\-×x÷*/:]|$)")


def match_alternative(text: str) -> LabelMatch | None:
    m = ALT_RE.match(text)
    if not m:
        return None
    if m.group("l4") and m.group("l4").lower() != m.group("dup").lower():
        return None
    letter = next((g for g in (m.group("l1"), m.group("l2"), m.group("l3"), m.group("l4")) if g), None)
    rest = text[m.end():]
    if not rest.strip() and letter is not None and m.group("l3"):
        return None
    return LabelMatch(label=m.group("label").strip(), number=None, letter=letter, rest_offset=m.end())


def is_structural_start(text: str) -> bool:
    """True when a line clearly starts a new question, alternative or bullet."""
    return bool(match_question(text) or match_alternative(text) or BULLET_RE.match(text))
