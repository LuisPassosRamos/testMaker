"""One streaming client for every provider that speaks the OpenAI chat-completions protocol.

Heroku Managed Inference, Google Gemini (OpenAI-compatible endpoint), Groq, Mistral and
OpenRouter all accept the same request and stream the same server-sent events. Replies are
streamed (some platforms cut long non-streaming calls) and asked for as plain JSON text.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.ai.base import AiBadRequest, AiError, AiRateLimited, schema_hint

log = logging.getLogger(__name__)


class OpenAICompatibleModel:
    def __init__(self, *, name: str, label: str, url: str, key: str, model: str, max_tokens: int,
                 temperature: float = 0.0, timeout_s: int = 240, token_param: str = "max_tokens",
                 vision: bool = False, extra_body: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None):
        self.name = name  # provider id, e.g. "gemini"
        self.label = label  # shown to people, e.g. "Gemini (Google) · gemini-3.6-flash"
        self.model = model
        self.vision = vision
        self._url = url.rstrip("/") + "/chat/completions"
        self._key = key
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout_s
        self._token_param = token_param
        self._extra = extra_body or {}
        self._headers = headers or {}
        self._transport = transport

    def generate_json(self, system: str, contents: list, schema: type[BaseModel]) -> str:
        texts: list[str] = []
        content: list[dict] = []
        for item in contents:
            if isinstance(item, tuple):
                mime, data = item
                url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                content.append({"type": "image_url", "image_url": {"url": url}})
            else:
                texts.append(str(item))
                content.append({"type": "text", "text": str(item)})
        user: str | list[dict] = "\n\n".join(texts) if len(content) == len(texts) else content
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{system}\n\n{schema_hint(schema)}"},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            self._token_param: self._max_tokens,
            "stream": True,
            **self._extra,
        }
        started = time.monotonic()
        pieces: list[str] = []
        finish = None
        timeout = httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "text/event-stream", **self._headers}
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client, client.stream(
                "POST", self._url, json=body, headers=headers,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    log.warning("AI %s HTTP %s (model %s)", self.name, response.status_code, self.model)
                    raise _http_error(response)
                for line in response.iter_lines():
                    if time.monotonic() - started > self._timeout:
                        raise AiError("A IA demorou demais para responder. Tente de novo em instantes.")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk, dict) and chunk.get("error"):
                        # Some providers report quota errors inside the stream.
                        raise _stream_error(chunk["error"])
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or choice.get("message") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str):
                            pieces.append(piece)
                        elif isinstance(piece, list):
                            pieces.extend(p.get("text", "") for p in piece if isinstance(p, dict))
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as exc:
            log.warning("AI %s request failed: %s", self.name, exc.__class__.__name__)
            raise AiError("Não foi possível falar com a IA agora. Tente de novo em instantes.") from exc
        log.info("AI %s/%s finished in %.1fs (%s)", self.name, self.model, time.monotonic() - started, finish or "?")
        if finish == "length":
            raise AiError("A resposta da IA ficou longa demais e foi cortada. Tente com menos questões ou menos material.")
        return "".join(pieces)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _http_error(response: httpx.Response) -> AiError:
    status = response.status_code
    if status == 429:
        return AiRateLimited("Esta IA atingiu o limite de uso por agora.", retry_after=_retry_after(response))
    if status in (401, 403):
        return AiError("A chave da IA não foi aceita. Confira a configuração.")
    if status in (400, 404, 413, 422):
        # 413: request bigger than the free plan allows per minute (e.g. Groq tokens per minute).
        return AiBadRequest("Esta IA não aceitou o pedido (modelo, tamanho ou formato).")
    return AiError("A IA não respondeu agora. Tente de novo em instantes; nada foi alterado.")


def _stream_error(error: Any) -> AiError:
    code = error.get("code") if isinstance(error, dict) else None
    if code in (429, "429", "rate_limit_exceeded", "RESOURCE_EXHAUSTED"):
        return AiRateLimited("Esta IA atingiu o limite de uso por agora.")
    return AiError("A IA interrompeu a resposta. Tente de novo em instantes.")
