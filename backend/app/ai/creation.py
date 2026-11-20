"""Background work for AI features: create an exam, redo one question, reorganise a reading."""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.ai.base import AiBadRequest, AiError, JsonModel
from app.ai.factory import get_model
from app.ai.generate import (
    block_text,
    generate_exam,
    key_entry,
    question_block,
    regenerate_question,
    to_exam_parts,
)
from app.ai.materials import collect_materials
from app.ai.organize import organize_blocks
from app.config import Settings
from app.domain.models import ChangeEntry, GenerationRequest, Project
from app.importing.storage import ProjectStore

log = logging.getLogger(__name__)

Progress = Callable[[str, float, str], None]

ACTION_LABELS = {"trocar": "trocada por outra", "mais_facil": "deixada mais fácil", "mais_dificil": "deixada mais difícil"}


def run_generation(store: ProjectStore, settings: Settings, pid: str, progress: Progress,
                   model: JsonModel | None = None) -> None:
    project = store.load(pid)
    request = project.generation or GenerationRequest()
    texts, images = collect_materials(store, pid, project, settings, progress)
    progress("ai", 0.25, "Criando as questões com inteligência artificial (leva de 1 a 2 minutos)…")
    model = model or get_model(settings, "write")
    meta = project.exam.meta
    try:
        result = generate_exam(model, meta, request, texts, images)
    except AiBadRequest:
        if not images:
            raise
        log.info("provider refused the images; retrying with text only")
        result = generate_exam(model, meta, request, texts, [])
    progress("build", 0.92, "Montando a prova…")
    blocks, key, notes = to_exam_parts(result)
    questions = sum(1 for b in blocks if b.kind == "question")

    def apply(p: Project) -> None:
        p.exam.blocks = blocks
        p.exam.answer_key = key
        p.exam.document_issues = notes
        if p.exam.meta.doc_type == "prova":
            p.exam.meta.mensagem_final = (result.mensagem_final or "Boa prova!").strip()[:200]
        p.exam.changes.append(ChangeEntry(kind="content", summary=f"Prova criada com IA ({questions} questões)."))
        for source in p.sources:
            if source.role == "material":
                source.extracted = True

    store.mutate(pid, apply)


def run_regenerate(store: ProjectStore, settings: Settings, pid: str, block_id: str, action: str,
                   progress: Progress, model: JsonModel | None = None) -> None:
    project = store.load(pid)
    block = next((b for b in project.exam.blocks if b.id == block_id and b.kind == "question"), None)
    if block is None:
        raise AiError("Esta questão não existe mais. Recarregue a página.")
    others = [block_text(b) for b in project.exam.blocks if b.kind == "question" and b.id != block_id]
    progress("ai", 0.2, "Escrevendo uma nova versão da questão…")
    item = regenerate_question(model or get_model(settings, "write"), project.exam.meta, project.generation,
                               block_text(block), action, others)  # type: ignore[arg-type]
    new = question_block(item, block.number or "")
    new.id = block.id
    new.page_break_before = block.page_break_before
    entry = key_entry(item, new)

    def apply(p: Project) -> None:
        for index, existing in enumerate(p.exam.blocks):
            if existing.id == block_id:
                p.exam.blocks[index] = new
                break
        else:
            raise AiError("Esta questão não existe mais. Recarregue a página.")
        p.exam.answer_key = [e for e in p.exam.answer_key if e.block_id != block_id]
        order = {b.id: i for i, b in enumerate(p.exam.blocks)}
        p.exam.answer_key.append(entry)
        p.exam.answer_key.sort(key=lambda e: order.get(e.block_id, 10_000))
        p.exam.changes.append(ChangeEntry(kind="content", block_id=block_id,
                                          summary=f"Questão {block.number or ''} {ACTION_LABELS.get(action, 'alterada')} (IA)."))

    store.mutate(pid, apply)


def run_organize(store: ProjectStore, settings: Settings, pid: str, progress: Progress,
                 model: JsonModel | None = None) -> None:
    project = store.load(pid)
    progress("ai", 0.2, "Conferindo a organização com inteligência artificial…")
    meta = project.exam.meta.model_copy()
    result = organize_blocks(project.exam.blocks, model or get_model(settings, "read"), meta)
    summary = ("Organização conferida pela IA: nada precisou mudar." if not result.changed else
               f"Organização conferida pela IA: {result.changed} trecho(s) reorganizado(s).")

    def apply(p: Project) -> None:
        p.exam.blocks = result.blocks
        p.exam.document_issues = [i for i in p.exam.document_issues if i.code != "ai_fallback"]
        for key in result.meta_filled + (["periodo"] if "unidade" in result.meta_filled else []):
            setattr(p.exam.meta, key, getattr(meta, key))
        if result.has_closing:
            p.exam.meta.mensagem_final = ""
        p.exam.changes.append(ChangeEntry(kind="structure", summary=summary))

    store.mutate(pid, apply)
