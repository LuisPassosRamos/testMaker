"""Pick the configured AI provider for a task ("read": organise exams, "write": create exams)."""

from __future__ import annotations

from app.ai.base import AiUnavailable, JsonModel
from app.config import Settings


def get_model(settings: Settings, task: str = "read") -> JsonModel:
    backend = settings.ai_backend
    if backend == "heroku":
        from app.ai.heroku import HerokuInferenceModel

        return HerokuInferenceModel(settings, task)
    if backend == "gemini":
        from app.ai.gemini import GeminiModel

        return GeminiModel(settings)
    raise AiUnavailable("A inteligência artificial ainda não foi configurada neste site.")


def ai_description(settings: Settings) -> dict:
    """What the UI tells teachers about where exam content goes."""
    backend = settings.ai_backend
    if backend == "heroku":
        return {"enabled": True, "provider": "heroku", "model": settings.ai_model_read,
                "model_write": settings.ai_model_write,
                "notice": "A leitura e a criação usam IA (Claude) pelo Heroku. O conteúdo não é guardado nem usado "
                          "para treinar a IA.",
                "reading": settings.ai_reading}
    if backend == "gemini":
        return {"enabled": True, "provider": "gemini", "model": settings.gemini_model,
                "model_write": settings.gemini_model,
                "notice": "A leitura e a criação usam IA (Gemini, do Google).",
                "reading": settings.ai_reading}
    return {"enabled": False, "provider": None, "model": None, "model_write": None, "notice": "", "reading": False}
