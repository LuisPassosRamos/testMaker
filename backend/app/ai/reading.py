"""Glue between the reading pipeline and the AI organiser, with a safe fallback.

When AI is not configured, turned off for the project, or fails, the rule-based
organisation is kept and a short note explains it. The teacher never loses work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.base import AiError, AiUnavailable, JsonModel
from app.ai.factory import get_model
from app.ai.organize import organize_blocks
from app.config import Settings
from app.domain.models import Block, ChangeEntry, ExamMeta, Issue

log = logging.getLogger(__name__)

Progress = Callable[[str, float, str], None]


@dataclass
class ReadingOutcome:
    blocks: list[Block]
    notes: list[Issue] = field(default_factory=list)
    has_closing: bool = False
    meta_filled: list[str] = field(default_factory=list)
    change: ChangeEntry | None = None
    used_ai: bool = False


def organize_with_ai(blocks: list[Block], meta: ExamMeta, settings: Settings, use_ai: bool,
                     progress: Progress | None = None, model: JsonModel | None = None) -> ReadingOutcome:
    if not blocks or not use_ai or (model is None and not (settings.ai_enabled and settings.ai_reading)):
        return ReadingOutcome(blocks=blocks)
    if progress:
        progress("ai", 0.95, "Conferindo a organização com inteligência artificial…")
    try:
        model = model or get_model(settings, "read")
        result = organize_blocks(blocks, model, meta)
    except (AiError, AiUnavailable) as exc:
        return ReadingOutcome(blocks=blocks, notes=[_fallback_note(str(exc))])
    except Exception as exc:  # network or provider bugs must never lose the reading
        log.warning("AI organise failed: %s", exc.__class__.__name__)
        return ReadingOutcome(blocks=blocks, notes=[_fallback_note(
            "A organização com IA não funcionou agora; foi usada a leitura automática simples.")])
    summary = ("Organização conferida pela IA: nada precisou mudar." if not result.changed else
               f"Organização conferida pela IA: {result.changed} trecho(s) reorganizado(s).")
    return ReadingOutcome(blocks=result.blocks, has_closing=result.has_closing, meta_filled=result.meta_filled,
                          change=ChangeEntry(kind="structure", summary=summary), used_ai=True)


def _fallback_note(message: str) -> Issue:
    return Issue(code="ai_fallback", severity="info",
                 message=f"{message} Você pode tentar de novo em “Reorganizar com IA”.")
