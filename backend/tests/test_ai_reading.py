"""AI reading: roles by line number, faithful regrouping, safe fallback, provider client."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from pydantic import SecretStr

from app.ai.base import AiError, call_json, extract_json
from app.ai.heroku import HerokuInferenceModel
from app.ai.organize import OrganizeReply, _signature, organize_blocks
from app.ai.reading import organize_with_ai
from app.domain.models import ExamMeta, runs_text
from app.structuring.paste import pasted_items, plain_text_paragraphs
from app.structuring.segmenter import segment
from app.structuring.title import parse_title

EXAM = (
    "ESCOLA MUNICIPALIZADA MADRE\n"
    "Avaliação Final III Trimestre - Ciências 4º ano\n"
    "1) Observe a planta e responda.\n"
    "a) Quantas folhas ela tem?\n"
    "b) Qual é a cor da flor?\n"
    "A água\n"
    "A água é essencial para todos os seres vivos e cobre grande parte do planeta Terra.\n"
    "2) Por que a água é importante?\n"
    "Boa prova!\n"
)


def _blocks():
    return segment(pasted_items(plain_text_paragraphs(EXAM))).blocks


class RoleModel:
    """Answers roles from the numbered lines it receives, like the real model would."""

    def __init__(self, rule, extra: dict | None = None):
        self.rule = rule
        self.extra = extra or {}
        self.prompts: list[str] = []

    def generate_json(self, system, contents, schema):
        self.prompts.append(contents[0])
        lines = re.findall(r"^\[(\d+)\] (.*)$", contents[0], flags=re.M)
        return json.dumps({"papeis": [self.rule(text) for _, text in lines], **self.extra})


def _rule(text: str) -> str:
    if text.startswith(("ESCOLA", "Avaliação")):
        return "cabecalho"
    if re.match(r"\d\)", text):
        return "questao_inicio"
    if re.match(r"[ab]\)", text):
        return "questao"  # sub-items to answer in writing, not options to mark
    if text == "A água":
        return "apoio_inicio"
    if text.startswith("A água é"):
        return "apoio"
    if text.startswith("Boa prova"):
        return "encerramento"
    return "outro"


def test_ai_roles_regroup_the_same_text_without_rewriting():
    blocks = _blocks()
    before = _signature(blocks)
    model = RoleModel(_rule, {"duvidas": [{"n": 6, "pergunta": "“A água” é o título de um texto de apoio?"}],
                              "dados": {"disciplina": "Ciências", "ano": "4º ano", "unidade": "III",
                                        "periodo": "trimestre", "modalidade": "final"}})
    meta = ExamMeta(disciplina="", ano="", professor="Ana")
    result = organize_blocks(blocks, model, meta)

    assert _signature(result.blocks) == before, "the text must be exactly the same"
    kinds = [b.kind for b in result.blocks]
    assert kinds == ["original_header", "question", "support_text", "question", "free"]
    q1 = result.blocks[1]
    assert q1.number == "1)"
    assert [p.kind for p in q1.parts] == ["paragraph", "paragraph", "paragraph"]
    assert runs_text(q1.parts[1].runs).startswith("a) Quantas"), "the label goes back into the text"
    assert result.changed >= 2 and result.doubts == 1
    doubt = [i for b in result.blocks for p in b.parts for i in p.issues if i.code == "ai_doubt"]
    assert doubt and "texto de apoio" in doubt[0].message
    assert (meta.disciplina, meta.ano, meta.unidade, meta.periodo, meta.modalidade) == (
        "Ciências", "4º ano", "III", "trimestre", "final")
    assert result.has_closing


def test_document_text_is_sent_as_data_and_ids_are_not_exposed():
    blocks = _blocks()
    model = RoleModel(_rule)
    organize_blocks(blocks, model)
    prompt = model.prompts[0]
    assert "[1] ESCOLA MUNICIPALIZADA MADRE" in prompt
    assert not any(b.id in prompt for b in blocks)


def test_teacher_answers_win_over_title_data():
    meta = ExamMeta(disciplina="Geografia", ano="5º ano")
    organize_blocks(_blocks(), RoleModel(_rule, {"dados": {"disciplina": "Ciências", "ano": "4º ano"}}), meta)
    assert (meta.disciplina, meta.ano) == ("Geografia", "5º ano")


@pytest.mark.parametrize("bad", [
    lambda text: "questao",  # same count but we will cut it below
])
def test_incomplete_roles_are_rejected_and_input_is_untouched(bad):
    blocks = _blocks()
    snapshot = [b.model_dump() for b in blocks]

    class Short(RoleModel):
        def generate_json(self, system, contents, schema):
            reply = json.loads(super().generate_json(system, contents, schema))
            reply["papeis"] = reply["papeis"][:-1]
            return json.dumps(reply)

    with pytest.raises(AiError):
        organize_blocks(blocks, Short(bad))
    assert [b.model_dump() for b in blocks] == snapshot


def test_unknown_roles_are_retried_then_rejected():
    class Weird:
        calls = 0

        def generate_json(self, system, contents, schema):
            Weird.calls += 1
            return '{"papeis": ["questao", "pergunta-secreta"]}'

    with pytest.raises(AiError):
        organize_blocks(_blocks(), Weird())
    assert Weird.calls == 2, "one retry explaining the error, then give up"


def test_numbered_question_kept_as_text_when_ai_says_it_is_not_a_question():
    blocks = _blocks()
    before = _signature(blocks)

    def rule(text):
        return "apoio" if text.startswith("2)") else _rule(text)

    result = organize_blocks(blocks, RoleModel(rule))
    assert _signature(result.blocks) == before
    assert sum(1 for b in result.blocks if b.kind == "question") == 1


def test_fallback_keeps_rule_based_reading_with_a_note(settings):
    blocks = _blocks()

    class Broken:
        def generate_json(self, *args):
            raise RuntimeError("network down")

    outcome = organize_with_ai(blocks, ExamMeta(), settings, True, model=Broken())
    assert outcome.blocks is blocks and not outcome.used_ai
    assert outcome.notes and outcome.notes[0].code == "ai_fallback"
    # Not configured or turned off: nothing happens and nothing is said.
    assert organize_with_ai(blocks, ExamMeta(), settings, True).notes == []
    assert organize_with_ai(blocks, ExamMeta(), settings, False, model=RoleModel(_rule)).blocks is blocks


def test_extract_json_tolerates_fences_and_call_json_validates():
    assert extract_json('```json\n{"papeis": []}\n```') == '{"papeis": []}'

    class Once:
        def __init__(self):
            self.replies = ['Aqui está: {"papeis": ["outro"]} Obrigado!']

        def generate_json(self, *args):
            return self.replies.pop(0)

    assert call_json(Once(), "s", ["x"], OrganizeReply).papeis == ["outro"]


@pytest.mark.parametrize(("title", "expected"), [
    ("Avaliação Final I Unidade - Ciências 1º ano", ("Ciências", "1º ano", "I", "unidade", "final")),
    ("AVALIAÇÃO DE CIÊNCIAS 3º ANO III UNIDADE", ("Ciências", "3º ano", "III", "unidade", None)),
    ("prova de recuperação matemática 7º ano", ("Matemática", "7º ano", None, None, "recuperacao")),
    ("Avaliação II Trimestre - Português 2º ano", ("Português", "2º ano", "II", "trimestre", None)),
    ("6 ano A religião - III unidade", ("Ensino Religioso", "6º ano", "III", "unidade", None)),
])
def test_title_parser_reads_school_titles(title, expected):
    data = parse_title(title)
    assert (data.disciplina, data.ano, data.unidade, data.periodo, data.modalidade) == expected


# ------------------------------------------------------------- Heroku client


def _heroku(settings, handler):
    configured = settings.model_copy(update={
        "inference_url": "https://inference.example", "inference_key": SecretStr("k"), "ai_provider": "heroku"})
    return HerokuInferenceModel(configured, "read", transport=httpx.MockTransport(handler))


def test_heroku_client_streams_and_joins_the_reply(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        chunks = [{"choices": [{"delta": {"content": '{"papeis": '}}]},
                  {"choices": [{"delta": {"content": '["outro"]}'}, "finish_reason": "stop"}]}]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    model = _heroku(settings, handler)
    assert model.generate_json("sistema", ["[1] Boa prova!"], OrganizeReply) == '{"papeis": ["outro"]}'
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "claude-haiku-5-5" and seen["body"]["stream"] is True
    assert seen["body"]["messages"][1]["content"] == "[1] Boa prova!"
    assert "JSON Schema" in seen["body"]["messages"][0]["content"]


@pytest.mark.parametrize(("status", "words"), [(401, "chave"), (429, "pedidos demais"), (500, "não respondeu")])
def test_heroku_errors_become_teacher_friendly_messages(settings, status, words):
    model = _heroku(settings, lambda request: httpx.Response(status, text="erro"))
    with pytest.raises(AiError, match=words):
        model.generate_json("s", ["x"], OrganizeReply)


def test_truncated_reply_is_reported(settings):
    def handler(request):
        chunk = {"choices": [{"delta": {"content": '{"papeis": ['}, "finish_reason": "length"}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    with pytest.raises(AiError, match="longa demais"):
        _heroku(settings, handler).generate_json("s", ["x"], OrganizeReply)
