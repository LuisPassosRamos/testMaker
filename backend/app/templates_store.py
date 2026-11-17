"""Reusable header templates (school identity + which fields appear).

Stored as JSON under ``data/templates`` and kept out of the retention cleanup, since
they are configuration rather than exam content. The built-in "Padrão Madre" template
reproduces the school's official header and can be edited or restored.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import BACKEND_DIR, Settings

FieldKey = Literal["aluno", "numero", "professor", "disciplina", "ano_turma", "turno", "unidade", "data", "nota",
                   "valor", "data_entrega"]

FIELD_LABELS: dict[str, str] = {
    "aluno": "Aluno(a)",
    "numero": "Nº",
    "professor": "Professor(a)",
    "disciplina": "Disciplina",
    "ano_turma": "Ano/Turma",
    "turno": "Turno",
    "unidade": "Unidade",
    "data": "Data",
    "nota": "Nota",
    "valor": "Valor",
    "data_entrega": "Entrega",
}

DEFAULT_LOGO = BACKEND_DIR / "app" / "assets" / "logo-madre.png"
_ID = re.compile(r"^[a-z0-9-]{3,40}$")


class HeaderField(BaseModel):
    key: FieldKey
    label: str
    enabled: bool = True


class HeaderTemplate(BaseModel):
    id: str
    name: str
    school_name: str = ""
    school_lines: list[str] = Field(default_factory=list)
    show_logo: bool = True
    logo: str | None = None  # file name in templates/logos; None = default school logo
    show_title: bool = True
    fields: list[HeaderField] = Field(default_factory=list)
    builtin: bool = False


def default_fields() -> list[HeaderField]:
    enabled = {"aluno", "numero", "professor", "disciplina", "ano_turma", "unidade", "data", "nota", "data_entrega"}
    order = ["aluno", "numero", "professor", "disciplina", "ano_turma", "turno", "unidade", "data", "nota", "valor",
             "data_entrega"]
    return [HeaderField(key=k, label=FIELD_LABELS[k], enabled=k in enabled) for k in order]  # type: ignore[arg-type]


def builtin_template() -> HeaderTemplate:
    return HeaderTemplate(
        id="padrao-madre",
        name="Padrão Madre (econômico)",
        school_name="ESCOLA MUNICIPALIZADA MADRE Mª DO ROSÁRIO DE ALMEIDA II",
        school_lines=[
            "Rua Justiniano Rocha Galvão s/n - Centro",
            "E-mail: escolamadrerosario2019@gmail.com",
            "Santo Antonio de Jesus – Bahia",
            "Portaria nº 2443 - D.O.E. 10/05/1972",
        ],
        show_logo=True,
        logo=None,
        show_title=True,
        fields=default_fields(),
        builtin=True,
    )


class TemplateStore:
    def __init__(self, settings: Settings):
        self.dir = settings.templates_dir
        self.logos = self.dir / "logos"
        self.logos.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "templates.json"
        self._lock = threading.Lock()

    def _read(self) -> list[HeaderTemplate]:
        if not self.file.exists():
            return [builtin_template()]
        data = json.loads(self.file.read_text(encoding="utf-8"))
        templates = [HeaderTemplate.model_validate(t) for t in data]
        if not any(t.id == "padrao-madre" for t in templates):
            templates.insert(0, builtin_template())
        return templates

    def _write(self, templates: list[HeaderTemplate]) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps([t.model_dump() for t in templates], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def list(self) -> list[HeaderTemplate]:
        with self._lock:
            return self._read()

    def get(self, template_id: str) -> HeaderTemplate:
        for template in self.list():
            if template.id == template_id:
                return template
        return builtin_template()

    def save(self, template: HeaderTemplate) -> HeaderTemplate:
        with self._lock:
            templates = self._read()
            if not template.id or not _ID.match(template.id):
                template.id = f"modelo-{secrets.token_hex(3)}"
            template.builtin = template.id == "padrao-madre"
            for i, existing in enumerate(templates):
                if existing.id == template.id:
                    templates[i] = template
                    break
            else:
                templates.append(template)
            self._write(templates)
            return template

    def delete(self, template_id: str) -> None:
        with self._lock:
            templates = [t for t in self._read() if t.id != template_id or t.builtin]
            self._write(templates)

    def restore_builtin(self) -> HeaderTemplate:
        return self.save(builtin_template())

    def logo_path(self, template: HeaderTemplate) -> Path | None:
        if not template.show_logo:
            return None
        if template.logo:
            path = self.logos / template.logo
            if path.exists() and path.parent == self.logos:
                return path
        return DEFAULT_LOGO if DEFAULT_LOGO.exists() else None

    def save_logo(self, template_id: str, png_bytes: bytes) -> str:
        name = f"{template_id}-{secrets.token_hex(3)}.png"
        (self.logos / name).write_bytes(png_bytes)
        return name
