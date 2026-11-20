"""Heroku Managed Inference and Agents (Claude models hosted on Amazon Bedrock).

The add-on sets INFERENCE_URL and INFERENCE_KEY on the app. Heroku does not store or log
prompts and does not use them for training. Requests are streamed because the platform
cuts non-streaming calls that take longer than ~29 seconds, and writing a whole exam takes
longer than that. The reply is plain JSON text (no tool calls: a single tool call is limited
to 55 seconds even when streaming).
"""

from __future__ import annotations

import base64
import json
import logging
import time

import httpx
from pydantic import BaseModel

from app.ai.base import AiBadRequest, AiError, AiUnavailable, schema_hint
from app.config import Settings

log = logging.getLogger(__name__)


class HerokuInferenceModel:
    def __init__(self, settings: Settings, task: str = "read", transport: httpx.BaseTransport | None = None):
        if not settings.heroku_ai_configured:
            raise AiUnavailable("A IA do Heroku não está configurada neste servidor.")
        self._url = settings.inference_url.rstrip("/") + "/v1/chat/completions"  # type: ignore[union-attr]
        self._key = settings.inference_key.get_secret_value()  # type: ignore[union-attr]
        write = task == "write"
        self.model = settings.ai_model_write if write else settings.ai_model_read
        self._max_tokens = settings.ai_max_tokens_write if write else settings.ai_max_tokens_read
        self._temperature = 0.6 if write else 0.0
        self._timeout = settings.ai_timeout_s
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
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{system}\n\n{schema_hint(schema)}"},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_completion_tokens": self._max_tokens,
            "stream": True,
        }
        started = time.monotonic()
        pieces: list[str] = []
        finish = None
        timeout = httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client, client.stream(
                "POST", self._url, json=body,
                headers={"Authorization": f"Bearer {self._key}", "Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    log.warning("inference HTTP %s (model %s)", response.status_code, self.model)
                    error = AiBadRequest if response.status_code in (400, 413, 422) else AiError
                    raise error(_http_message(response.status_code))
                for line in response.iter_lines():
                    if time.monotonic() - started > self._timeout:
                        raise AiError("A IA demorou demais para responder. Tente de novo em instantes.")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or choice.get("message") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str):
                            pieces.append(piece)
                        elif isinstance(piece, list):
                            pieces.extend(p.get("text", "") for p in piece if isinstance(p, dict))
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as exc:
            log.warning("inference request failed: %s", exc.__class__.__name__)
            raise AiError("Não foi possível falar com a IA agora. Tente de novo em instantes.") from exc
        log.info("inference %s finished in %.1fs (%s)", self.model, time.monotonic() - started, finish or "?")
        if finish == "length":
            raise AiError("A resposta da IA ficou longa demais e foi cortada. Tente com menos questões ou menos material.")
        return "".join(pieces)


def _http_message(status: int) -> str:
    if status in (401, 403):
        return "A chave da IA não foi aceita. Confira a configuração do add-on no Heroku."
    if status == 429:
        return "A IA recebeu pedidos demais agora. Espere um minuto e tente de novo."
    if status == 404:
        return "O modelo de IA configurado não foi encontrado. Confira AI_MODEL_READ e AI_MODEL_WRITE."
    return "A IA não respondeu agora. Tente de novo em instantes; nada foi alterado."
