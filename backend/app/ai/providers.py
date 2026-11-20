"""The AI services the app can use, and how to build the fallback chain from configuration.

Each service needs only its key in the environment. The order of preference comes from
``AI_CHAIN_READ`` (organising uploaded exams) and ``AI_CHAIN_WRITE`` (creating exams), lists
like ``heroku,gemini:gemini-3.6-flash,groq:openai/gpt-oss-120b``. Entries whose key is not
configured are skipped, so the same defaults work with any subset of keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.ai.openai_compat import OpenAICompatibleModel
from app.config import Settings


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str  # empty: read from settings (Heroku)
    key_setting: str
    token_param: str = "max_tokens"
    vision: bool = False
    # Whether the free plan may use prompts to improve the provider's products (None: not stated).
    may_train: bool | None = None
    # Largest reply per task; small for plans with tight tokens-per-minute limits.
    max_tokens: dict[str, int] = field(default_factory=lambda: {"read": 4096, "write": 8192})
    extra: dict[str, dict[str, Any]] = field(default_factory=dict)  # per task
    headers: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, Provider] = {
    "heroku": Provider(
        "heroku", "Claude (Heroku)", "", "inference_key", token_param="max_completion_tokens", vision=True,
        may_train=False,
    ),
    "gemini": Provider(
        "gemini", "Gemini (Google)", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini_api_key",
        vision=True, may_train=True, max_tokens={"read": 8192, "write": 16384},
        extra={"read": {"reasoning_effort": "low"}},
    ),
    "groq": Provider(
        "groq", "Groq", "https://api.groq.com/openai/v1", "groq_api_key",
        # Free plan: 8K tokens per minute per model, so only short replies fit.
        max_tokens={"read": 2048, "write": 4096}, extra={"read": {"reasoning_effort": "low"}},
    ),
    "mistral": Provider(
        "mistral", "Mistral", "https://api.mistral.ai/v1", "mistral_api_key", may_train=True,
    ),
    "openrouter": Provider(
        "openrouter", "OpenRouter (modelos grátis)", "https://openrouter.ai/api/v1", "openrouter_api_key",
        # The free router may pick a reasoning model, whose thinking counts against the reply limit.
        max_tokens={"read": 8192, "write": 16384},
        extra={"read": {"reasoning": {"effort": "low"}}, "write": {"reasoning": {"effort": "low"}}},
        headers={"X-Title": "Prova Pronta"},
    ),
}

DEFAULT_MODELS = {
    "heroku": {"read": "claude-haiku-5-5", "write": "claude-sonnet-5-5"},
    "gemini": {"read": "gemini-3.5-flash-lite", "write": "gemini-3.6-flash"},
    "groq": {"read": "openai/gpt-oss-120b", "write": "openai/gpt-oss-120b"},
    "mistral": {"read": "mistral-small-latest", "write": "mistral-large-latest"},
    "openrouter": {"read": "openrouter/free", "write": "openrouter/free"},
}


def _secret(settings: Settings, attr: str) -> str:
    value = getattr(settings, attr, None)
    secret = value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else value
    return (secret or "").strip()


def configured(settings: Settings, name: str) -> bool:
    provider = PROVIDERS.get(name)
    if provider is None or not _secret(settings, provider.key_setting):
        return False
    return bool(settings.inference_url) if name == "heroku" else True


def parse_chain(text: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            continue
        name, _, model = item.partition(":")
        out.append((name.strip().lower(), model.strip() or None))
    return out


def chain_spec(settings: Settings, task: str) -> list[tuple[Provider, str]]:
    """(provider, model) pairs to try, in order, for this task and configuration."""
    if settings.ai_provider == "off":
        return []
    text = settings.ai_chain_write if task == "write" else settings.ai_chain_read
    out: list[tuple[Provider, str]] = []
    for name, model in parse_chain(text):
        if name not in PROVIDERS or not configured(settings, name):
            continue
        if settings.ai_provider not in ("auto", name):
            continue
        if name == "heroku" and model is None:
            model = settings.ai_model_write if task == "write" else settings.ai_model_read
        model = model or DEFAULT_MODELS[name]["write" if task == "write" else "read"]
        if (name, model) not in [(p.name, m) for p, m in out]:
            out.append((PROVIDERS[name], model))
    return out


def build_entry(settings: Settings, provider: Provider, model: str, task: str,
                transport: httpx.BaseTransport | None = None) -> OpenAICompatibleModel:
    write = task == "write"
    base = settings.inference_url if provider.name == "heroku" else provider.base_url
    return OpenAICompatibleModel(
        name=provider.name, label=f"{provider.label} · {model}", url=base or "", key=_secret(settings, provider.key_setting),
        model=model, max_tokens=provider.max_tokens["write" if write else "read"],
        temperature=0.6 if write else 0.0, timeout_s=settings.ai_timeout_s, token_param=provider.token_param,
        vision=provider.vision, extra_body=provider.extra.get("write" if write else "read"), headers=provider.headers,
        transport=transport,
    )
