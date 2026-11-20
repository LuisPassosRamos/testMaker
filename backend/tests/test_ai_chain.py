"""Free AI services in a fallback chain: order, quotas, failures, images and the privacy notice."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.ai.base import AiError, AiRateLimited, ModelChain, call_json, reset_cooldowns
from app.ai.factory import ai_description, get_model
from app.ai.organize import OrganizeReply
from app.ai.providers import PROVIDERS, build_entry, chain_spec


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    reset_cooldowns()
    yield
    reset_cooldowns()


class Fake:
    def __init__(self, name, replies, vision=True):
        self.name, self.model, self.label, self.vision = name, f"{name}-model", f"{name.title()}", vision
        self.replies = list(replies)
        self.calls: list[list] = []

    def generate_json(self, system, contents, schema):
        self.calls.append(list(contents))
        reply = self.replies.pop(0) if self.replies else '{"papeis": ["outro"]}'
        if isinstance(reply, Exception):
            raise reply
        return reply


OK = '{"papeis": ["outro"]}'


def test_quota_hands_over_to_the_next_service_and_rests():
    first = Fake("gemini", [AiRateLimited("limite"), OK])
    second = Fake("groq", [OK, OK])
    chain = ModelChain([first, second])
    assert call_json(chain, "s", ["x"], OrganizeReply).papeis == ["outro"]
    assert chain.last_used == "Groq"
    # The service that hit its quota rests: the next request goes straight to the second one.
    call_json(chain, "s", ["x"], OrganizeReply)
    assert len(first.calls) == 1 and len(second.calls) == 2


def test_unusable_answers_also_move_on():
    first = Fake("mistral", ["não sei", "ainda não sei"])
    second = Fake("openrouter", [OK])
    chain = ModelChain([first, second])
    call_json(chain, "s", ["x"], OrganizeReply)
    assert len(first.calls) == 2, "one retry explaining the format, then the next service"
    assert chain.last_used == "Openrouter"


def test_services_without_vision_get_text_only():
    blind = Fake("groq", [AiError("falhou")], vision=False)
    seeing = Fake("gemini", [OK], vision=True)
    call_json(ModelChain([blind, seeing]), "s", ["texto", ("image/jpeg", b"img")], OrganizeReply)
    assert not any(isinstance(c, tuple) for c in blind.calls[0])
    assert "não as recebe" in blind.calls[0][-1]
    assert any(isinstance(c, tuple) for c in seeing.calls[0])


def test_when_every_service_is_out_of_quota_the_teacher_is_told():
    chain = ModelChain([Fake("gemini", [AiRateLimited("x")]), Fake("groq", [AiRateLimited("y")])])
    with pytest.raises(AiError, match="limite de uso"):
        call_json(chain, "s", ["x"], OrganizeReply)
    # Everything resting: the chain still tries again later instead of refusing forever.
    assert len(chain.available()) == 2


def _settings(settings, **keys):
    update = {"ai_provider": "auto"}
    update.update({k: SecretStr(v) if k.endswith("_key") else v for k, v in keys.items()})
    return settings.model_copy(update=update)


def test_chain_follows_the_configured_order_and_skips_missing_keys(settings):
    s = _settings(settings, gemini_api_key="g", groq_api_key="q")
    read = [(p.name, m) for p, m in chain_spec(s, "read")]
    write = [(p.name, m) for p, m in chain_spec(s, "write")]
    assert read == [("gemini", "gemini-3.5-flash-lite"), ("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-3.8-flash")]
    assert write[0] == ("gemini", "gemini-3.8-flash")
    assert all(name in ("gemini", "groq") for name, _ in write)
    only = settings.model_copy(update={"ai_provider": "groq", "gemini_api_key": SecretStr("g"),
                                       "groq_api_key": SecretStr("q")})
    assert {p.name for p, _ in chain_spec(only, "read")} == {"groq"}
    assert chain_spec(settings, "read") == []
    with_heroku = _settings(settings, inference_url="https://inference.example", inference_key="k")
    assert chain_spec(with_heroku, "write")[0][1] == "claude-sonnet-5-5"
    assert isinstance(get_model(s, "read"), ModelChain)


def test_notice_says_free_services_may_use_the_content(settings):
    free = ai_description(_settings(settings, gemini_api_key="g", groq_api_key="q"))
    assert free["enabled"] and free["providers"] == ["gemini", "groq"]
    assert "gratuitos" in free["notice"] and "nomes de alunos" in free["notice"]
    paid = ai_description(_settings(settings, inference_url="https://i.example", inference_key="k"))
    assert "não é guardado" in paid["notice"]
    assert ai_description(settings)["enabled"] is False


def _capture(provider: str, task: str, settings, status=200, body=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, text="erro", headers={"retry-after": "7"})
        return httpx.Response(200, text=body or f"data: {json.dumps({'choices': [{'delta': {'content': OK}}]})}\n\n"
                                                "data: [DONE]\n\n")

    s = _settings(settings, **{PROVIDERS[provider].key_setting: "key"})
    entry = build_entry(s, PROVIDERS[provider], "m", task, transport=httpx.MockTransport(handler))
    return entry, seen


def test_each_service_gets_its_own_address_and_limits(settings):
    gemini, seen = _capture("gemini", "read", settings)
    gemini.generate_json("s", ["x"], OrganizeReply)
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen["body"]["reasoning_effort"] == "low" and seen["body"]["max_tokens"] == 8192
    groq, seen = _capture("groq", "read", settings)
    groq.generate_json("s", ["x"], OrganizeReply)
    assert seen["url"].startswith("https://api.groq.com/openai/v1/") and seen["body"]["max_tokens"] == 2048
    router, seen = _capture("openrouter", "write", settings)
    router.generate_json("s", ["x"], OrganizeReply)
    assert seen["headers"]["x-title"] == "Prova Pronta" and seen["body"]["temperature"] == 0.6


def test_rate_limit_and_quota_errors_inside_the_stream(settings):
    limited, _ = _capture("groq", "read", settings, status=429)
    with pytest.raises(AiRateLimited) as info:
        limited.generate_json("s", ["x"], OrganizeReply)
    assert info.value.retry_after == 7
    stream = f"data: {json.dumps({'error': {'code': 429, 'message': 'quota'}})}\n\n"
    in_stream, _ = _capture("gemini", "read", settings, body=stream)
    with pytest.raises(AiRateLimited):
        in_stream.generate_json("s", ["x"], OrganizeReply)
