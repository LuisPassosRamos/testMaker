"""Build the model (a fallback chain of providers) for a task: "read" organises exams, "write" creates them."""

from __future__ import annotations

from app.ai.base import AiUnavailable, ModelChain
from app.ai.providers import build_entry, chain_spec
from app.config import Settings


def get_model(settings: Settings, task: str = "read") -> ModelChain:
    spec = chain_spec(settings, task)
    if not spec:
        raise AiUnavailable("A inteligência artificial ainda não foi configurada neste site.")
    return ModelChain([build_entry(settings, provider, model, task) for provider, model in spec])


def ai_description(settings: Settings) -> dict:
    """What the UI tells teachers about where exam content goes."""
    read = chain_spec(settings, "read")
    write = chain_spec(settings, "write")
    providers: list[str] = []
    for provider, _ in [*read, *write]:
        if provider.name not in providers:
            providers.append(provider.name)
    if not providers:
        return {"enabled": False, "provider": None, "providers": [], "model": None, "model_write": None,
                "notice": "", "reading": False}
    from app.ai.providers import PROVIDERS

    labels = [PROVIDERS[name].label for name in providers]
    if all(PROVIDERS[name].may_train is False for name in providers):
        notice = (f"A leitura e a criação usam IA ({', '.join(labels)}). O conteúdo não é guardado nem usado "
                  "para treinar a IA.")
    else:
        notice = (f"A leitura e a criação usam IA de serviços gratuitos ({', '.join(labels)}). Alguns podem usar "
                  "o conteúdo para melhorar seus produtos: não coloque nomes de alunos nas provas.")
    return {
        "enabled": True,
        "provider": providers[0],
        "providers": providers,
        "model": read[0][1] if read else None,
        "model_write": write[0][1] if write else None,
        "notice": notice,
        "reading": settings.ai_reading and bool(read),
    }
