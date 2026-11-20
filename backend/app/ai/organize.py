"""Organise an extracted exam with AI: what is a question, an alternative, a support text…

Fidelity by construction: the model never returns text. It receives the exam as numbered
lines and answers one role per line (plus doubts and the exam's title data). We then regroup
the *same* parts into blocks deterministically and check that the text, read in order, is
exactly the same before and after. Anything unexpected (wrong count, unknown role, a
different text) discards the AI answer and keeps the rule-based organisation, so the teacher
is never worse off than without AI.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.base import DATA_GUARD, AiError, JsonModel, call_json
from app.domain.models import Block, ExamMeta, Issue, Part, Run, merge_runs, runs_text
from app.structuring.patterns import match_alternative, match_question
from app.structuring.segmenter import split_runs

log = logging.getLogger(__name__)

Role = Literal["cabecalho", "instrucao", "apoio_inicio", "apoio", "questao_inicio", "questao", "alternativa",
               "encerramento", "outro"]

ROLE_NAMES: dict[str, str] = {
    "cabecalho": "cabeçalho", "instrucao": "instrução", "apoio_inicio": "texto de apoio", "apoio": "texto de apoio",
    "questao_inicio": "início de questão", "questao": "enunciado de questão", "alternativa": "alternativa",
    "encerramento": "mensagem final", "outro": "outro trecho",
}

# Rule-based guesses about structure that the AI decides instead.
_GUESS_CODES = {"after_alternatives", "media_between_questions", "numbering_out_of_sequence",
                "inline_alternatives_split"}


class _Doubt(BaseModel):
    n: int
    pergunta: str = Field(max_length=400)


class TitleData(BaseModel):
    tipo: Literal["prova", "atividade", "revisao"] | None = None
    disciplina: str | None = Field(None, max_length=60)
    ano: str | None = Field(None, max_length=20)
    unidade: Literal["I", "II", "III", "IV"] | None = None
    periodo: Literal["unidade", "trimestre", "periodo"] | None = None
    modalidade: Literal["regular", "final", "parcial", "recuperacao", "segunda_chamada", "simulado"] | None = None


class OrganizeReply(BaseModel):
    papeis: list[Role]
    duvidas: list[_Doubt] = Field(default_factory=list)
    dados: TitleData = Field(default_factory=TitleData)


SYSTEM = f"""Você organiza provas escolares brasileiras (Ensino Fundamental) que foram lidas de arquivos Word,
PDF ou fotos. Você NÃO escreve, NÃO corrige e NÃO resolve nada: apenas diz qual é o papel de cada linha.

Você recebe as linhas numeradas na ordem do documento, no formato "[n] texto". Marcadores entre colchetes
como [IMAGEM], [TABELA: …] e [LINHAS PARA RESPOSTA] representam figuras, tabelas e espaços de resposta.
Devolva em "papeis" exatamente um papel por linha, na mesma ordem (a quantidade deve ser igual ao número
de linhas). Papéis:
- "cabecalho": cabeçalho da escola ou da prova (nome da escola, endereço, Nome/Turma/Data/Professor,
  título como "Avaliação de Ciências – II Unidade", logotipo no topo).
- "instrucao": orientações gerais da prova ("Leia com atenção", "Use caneta azul", "Não rasure").
- "apoio_inicio": começo de um texto de apoio (texto, poema, fábula, notícia, tirinha, gráfico, tabela, mapa
  ou imagem) que serve a uma ou mais questões. Inclui chamadas como "Leia o texto e responda às questões 3 e 4".
- "apoio": continuação do texto de apoio anterior (outros parágrafos, versos, a fonte/crédito do texto).
- "questao_inicio": a linha onde começa uma questão (normalmente com número: "1)", "02.", "Questão 3 –").
- "questao": continuação do enunciado da questão atual, inclusive imagens, tabelas, colunas para associar,
  afirmações de verdadeiro/falso escritas como frases, lacunas e espaços de resposta dessa questão.
- "alternativa": uma opção para marcar da questão atual ("a) …", "(B) …", "( ) …").
- "encerramento": frase final de incentivo ("Boa prova!", "Você é capaz!").
- "outro": qualquer outra coisa (numeração de página, sobras de leitura).
Regras: um texto de apoio colocado antes de uma questão nunca é enunciado da questão anterior; imagens e
tabelas logo depois de "Observe…" pertencem a esse comando; não invente questões onde não há comando.

Em "duvidas", aponte só os casos realmente ambíguos (no máximo 5), com "n" da linha e uma pergunta curta e
simples para o professor, por exemplo: "A linha 'Leia o poema' começa a questão 4?".
Em "dados", preencha o que aparecer no título/cabeçalho (tipo, disciplina, ano como "5º ano", unidade em
algarismos romanos, período unidade/trimestre/periodo e modalidade); deixe null o que não aparecer.

{DATA_GUARD}"""


@dataclass
class _Entry:
    block: Block
    part: Part
    index: int  # position of the part inside its original block


@dataclass
class OrganizeResult:
    blocks: list[Block]
    changed: int
    doubts: int
    meta_filled: list[str] = field(default_factory=list)
    has_closing: bool = False


def _prepare(blocks: list[Block]) -> tuple[list[Block], set[str]]:
    """Deep copy (the input is never touched) and give empty blocks a placeholder line."""
    copies = [b.model_copy(deep=True) for b in blocks]
    placeholders: set[str] = set()
    for block in copies:
        if block.kind != "page_break" and not block.parts:
            part = Part(kind="paragraph", runs=[])
            placeholders.add(part.id)
            block.parts.append(part)
    return copies, placeholders


def _entries(blocks: list[Block]) -> list[_Entry]:
    return [_Entry(b, p, i) for b in blocks if b.kind != "page_break" for i, p in enumerate(b.parts)]


def _line(entry: _Entry) -> str:
    part, block = entry.part, entry.block
    if part.kind in ("image", "crop"):
        return "[IMAGEM]"
    if part.kind == "table":
        cells = [runs_text(c.runs) for row in (part.rows or []) for cell in row for c in cell.content]
        text = " | ".join(t.strip() for t in cells if t.strip())
        return f"[TABELA: {text[:200]}]"
    if part.kind == "answer_space":
        return "[LINHAS PARA RESPOSTA]"
    text = runs_text(part.runs).replace("\n", " ").strip()
    if part.kind == "alternative" and part.label:
        text = f"{part.label} {text}"
    if entry.index == 0 and block.kind == "question" and block.number:
        text = f"{block.number} {text}"
    return text[:300] or "(linha vazia)"


def _current_role(entry: _Entry) -> str:
    kind = entry.block.kind
    if kind == "original_header":
        return "cabecalho"
    if kind == "instructions":
        return "instrucao"
    if kind == "support_text":
        return "apoio_inicio" if entry.index == 0 else "apoio"
    if kind == "question":
        if entry.part.kind == "alternative":
            return "alternativa"
        return "questao_inicio" if entry.index == 0 else "questao"
    return "outro"


def build_prompt(blocks: list[Block]) -> tuple[list[_Entry], str]:
    entries = _entries(blocks)
    lines = [f"[{i}] {_line(e)}" for i, e in enumerate(entries, 1)]
    return entries, f"A prova tem {len(lines)} linhas:\n\n" + "\n".join(lines)


def organize_blocks(blocks: list[Block], model: JsonModel, meta: ExamMeta | None = None) -> OrganizeResult:
    work, placeholders = _prepare(blocks)
    entries, prompt = build_prompt(work)
    if not entries:
        return OrganizeResult(blocks=blocks, changed=0, doubts=0)
    reply = call_json(model, SYSTEM, [prompt], OrganizeReply)
    roles = list(reply.papeis)
    if len(roles) != len(entries):
        raise AiError("A IA devolveu uma organização incompleta. Foi mantida a leitura automática.")

    # Notes go on the parts before regrouping (regrouping may copy them).
    changed = 0
    for entry, after in zip(entries, roles, strict=True):
        before = _current_role(entry)
        if before == after or {before, after} <= {"apoio_inicio", "apoio"} or entry.part.id in placeholders:
            continue
        changed += 1
        entry.part.issues = [i for i in entry.part.issues if i.code not in _GUESS_CODES]
        entry.part.issues.append(Issue(
            code="ai_reorganized", severity="info",
            message=f"A IA reorganizou este trecho: antes era {ROLE_NAMES[before]}, agora é {ROLE_NAMES[after]}.",
        ))
    doubts = 0
    seen: set[int] = set()
    for doubt in reply.duvidas[:5]:
        if not 1 <= doubt.n <= len(entries) or doubt.n in seen or not doubt.pergunta.strip():
            continue
        seen.add(doubt.n)
        doubts += 1
        entries[doubt.n - 1].part.issues.append(Issue(
            code="ai_doubt", severity="warning", message=_clean(doubt.pergunta)[:300],
        ))

    new_blocks = regroup(work, entries, roles)
    if _signature(new_blocks) != _signature(blocks):
        log.warning("AI regrouping changed the text signature; discarded")
        raise AiError("A organização da IA não conferiu com o texto original. Foi mantida a leitura automática.")
    for block in new_blocks:
        block.issues = [i for i in block.issues if i.code not in _GUESS_CODES]
        if len(block.parts) > 1:
            block.parts = [p for p in block.parts if p.id not in placeholders]
    filled = fill_meta(meta, reply.dados) if meta is not None else []
    return OrganizeResult(blocks=new_blocks, changed=changed, doubts=doubts, meta_filled=filled,
                          has_closing="encerramento" in roles)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fill_meta(meta: ExamMeta, data: TitleData) -> list[str]:
    """Fill only what the teacher left empty: their own answers always win."""
    filled: list[str] = []
    if data.disciplina and not meta.disciplina.strip():
        meta.disciplina = _clean(data.disciplina)[:60]
        filled.append("disciplina")
    if data.ano and not meta.ano.strip():
        m = re.search(r"([1-9])", data.ano)
        if m:
            meta.ano = f"{m.group(1)}º ano"
            filled.append("ano")
    if data.unidade and not meta.unidade.strip():
        meta.unidade = data.unidade
        if data.periodo:
            meta.periodo = data.periodo
        filled.append("unidade")
    if data.modalidade and meta.modalidade == "regular" and data.modalidade != "regular":
        meta.modalidade = data.modalidade
        filled.append("modalidade")
    return filled


# ------------------------------------------------------------------ regrouping


def regroup(original: list[Block], entries: list[_Entry], roles: list[str]) -> list[Block]:
    """Rebuild blocks from per-part roles, moving labels between block numbers and texts only."""
    out: list[Block] = []
    current: Block | None = None
    last_home: dict[str, Block] = {}  # original block id -> new block holding its last part
    first_home: dict[str, Block] = {}
    used_ids: set[str] = set()

    def open_block(kind: str, origin: Block | None, number: str | None = None) -> Block:
        nonlocal current
        block = Block(kind=kind, number=number, original_number=number)  # type: ignore[arg-type]
        if origin is not None and origin.kind == kind and origin.id not in used_ids:
            used_ids.add(origin.id)
            block.id = origin.id
            block.original_number = origin.original_number if number == origin.number else number
            block.page_break_before = origin.page_break_before
            block.allow_split = origin.allow_split
            block.alternatives_layout = origin.alternatives_layout
            block.issues = [i.model_copy() for i in origin.issues]
            block.reviewed = origin.reviewed
        out.append(block)
        current = block
        return block

    for entry, role in zip(entries, roles, strict=True):
        origin, part, first = entry.block, entry.part, entry.index == 0
        anchor = origin if first else None
        numbered_start = first and origin.kind == "question" and bool(origin.number)
        if numbered_start and role != "questao_inicio":
            # The number of a question the AI did not keep as a question stays in the text.
            part = _with_prefix(part, origin.number or "")
        if role == "cabecalho":
            target = current if current is not None and current.kind == "original_header" else open_block(
                "original_header", anchor)
            target.parts.append(_as_paragraph(part))
        elif role == "instrucao":
            target = current if current is not None and current.kind == "instructions" else open_block(
                "instructions", anchor)
            target.parts.append(_as_paragraph(part))
        elif role in ("apoio_inicio", "apoio"):
            if role == "apoio" and current is not None and current.kind == "support_text":
                target = current
            else:
                target = open_block("support_text", anchor)
            target.parts.append(_as_paragraph(part))
        elif role == "questao_inicio":
            if numbered_start:
                target = open_block("question", origin, origin.number)
                target.parts.append(_as_paragraph(part))
            else:
                part_out, number = _take_question_label(part)
                target = open_block("question", None, number)
                if number is None:
                    target.issues.append(Issue(code="question_without_number", severity="info",
                                               message="A IA identificou esta questão, mas ela não tem número."))
                target.parts.append(part_out)
        elif role == "questao":
            if current is not None and current.kind == "question":
                target = current
            else:
                target = open_block("question", None)
            target.parts.append(_as_paragraph(part))
        elif role == "alternativa":
            if current is not None and current.kind == "question":
                target = current
            else:
                target = open_block("question", None)
                target.issues.append(Issue(code="alternatives_without_question", severity="warning",
                                           message="Estas alternativas ficaram sem enunciado. Confira."))
            target.parts.append(_as_alternative(part))
        else:  # encerramento / outro
            if current is not None and current.kind == "free" and current.parts:
                target = current
            else:
                target = open_block("free", anchor)
            target.parts.append(_as_paragraph(part))
        first_home.setdefault(origin.id, target)
        last_home[origin.id] = target

    # A trailing answer space came after the original block's last part.
    for block in original:
        if block.kind != "question" or block.answer_space.kind == "none":
            continue
        home = last_home.get(block.id)
        if home is None or home.kind != "question":
            home = first_home.get(block.id)
        if home is not None and home.kind == "question" and home.answer_space.kind == "none":
            home.answer_space = block.answer_space.model_copy()

    # Manual page breaks stay after the content that preceded them.
    result = [b for b in out if b.parts]
    previous_id: str | None = None
    for block in original:
        if block.kind == "page_break":
            home = last_home.get(previous_id) if previous_id else None
            position = result.index(home) + 1 if home in result else 0
            result.insert(position, block.model_copy(deep=True))
        else:
            previous_id = block.id
    return result


def _with_prefix(part: Part, prefix: str) -> Part:
    if part.kind not in ("paragraph", "alternative") or not prefix:
        return part
    if part.kind == "alternative":
        part = _as_paragraph(part)
    return part.model_copy(update={"runs": merge_runs([Run(text=f"{prefix} ", bold=True), *part.runs])})


def _as_paragraph(part: Part) -> Part:
    if part.kind != "alternative":
        return part
    runs = list(part.runs)
    if part.label:
        runs = merge_runs([Run(text=f"{part.label} "), *runs])
    return part.model_copy(update={"kind": "paragraph", "runs": runs, "label": None})


def _as_alternative(part: Part) -> Part:
    if part.kind != "paragraph":
        return part
    m = match_alternative(runs_text(part.runs))
    if m is None:
        return part.model_copy(update={"kind": "alternative", "label": None})
    _, rest = split_runs(part.runs, m.rest_offset)
    return part.model_copy(update={"kind": "alternative", "label": m.label, "runs": rest})


def _take_question_label(part: Part) -> tuple[Part, str | None]:
    part = _as_paragraph(part)
    if part.kind != "paragraph":
        return part, None
    m = match_question(runs_text(part.runs))
    if m is None:
        return part, None
    _, rest = split_runs(part.runs, m.rest_offset)
    return part.model_copy(update={"runs": rest}), m.label


def _signature(blocks: list[Block]) -> str:
    """All visible text in reading order, without spaces: must not change when regrouping."""
    pieces: list[str] = []
    for block in blocks:
        if block.kind == "page_break":
            continue
        if block.number:
            pieces.append(block.number)
        for part in block.parts:
            if part.label:
                pieces.append(part.label)
            pieces.append(runs_text(part.runs))
            if part.kind in ("image", "crop"):
                pieces.append(f"<{part.asset_id}>")
            if part.rows:
                pieces.extend(runs_text(c.runs) for row in part.rows for cell in row for c in cell.content)
            if part.kind == "answer_space":
                pieces.append("<answer>")
    return re.sub(r"\s+", "", "".join(pieces))
