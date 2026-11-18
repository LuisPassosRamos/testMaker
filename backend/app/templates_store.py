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
from app.persistence import Database

FieldKey = Literal["aluno", "numero", "professor", "disciplina", "ano_turma", "turno", "unidade", "data", "nota",
                   "valor", "data_entrega"]

FIELD_LABELS: dict[str, str] = {
    "aluno": "Aluno(a)",
    "numero": "Nº",
    "professor": "Professor(a)",
    "disciplina": "Disciplina",
    "ano_turma": "Turma",
    "turno": "Turno",
    "unidade": "Unidade",
    "data": "Data",
    "nota": "Nota",
    "valor": "Valor",
    "data_entrega": "Entrega",
}

DEFAULT_LOGO = BACKEND_DIR / "app" / "assets" / "logo-madre.png"
_ID = re.compile(r"^[a-z0-9-]{3,40}$")
_LOGO = re.compile(r"^[a-z0-9-]{3,40}-[0-9a-f]{6}\.png$")
# Bumped when the built-in template changes, so an untouched saved copy is upgraded.
BUILTIN_REVISION = 2


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
    revision: int = 1


def default_fields() -> list[HeaderField]:
    """The school's identification lines: ALUNO(A) · DATA + TURMA · PROFESSOR(A).

    The other fields stay available (turned off) for teachers who want them.
    """
    enabled = {"aluno", "data", "ano_turma", "professor", "data_entrega"}
    order = ["aluno", "numero", "data", "ano_turma", "professor", "disciplina", "turno", "unidade", "nota", "valor",
             "data_entrega"]
    return [HeaderField(key=k, label=FIELD_LABELS[k], enabled=k in enabled) for k in order]  # type: ignore[arg-type]


def builtin_template() -> HeaderTemplate:
    """Header used in the school's formatted exams (logo box + identification lines)."""
    return HeaderTemplate(
        id="padrao-madre",
        name="Padrão Madre",
        school_name="ESCOLA MUNICIPALIZADA MADRE MARIA DO ROSÁRIO DE ALMEIDA II",
        school_lines=[],
        show_logo=True,
        logo=None,
        show_title=True,
        fields=default_fields(),
        builtin=True,
        revision=BUILTIN_REVISION,
    )


_DB_KEY = "header_templates"
_DB_OWNER = "_templates"


class TemplateStore:
    def __init__(self, settings: Settings, db: Database | None = None):
        self.dir = settings.templates_dir
        self.logos = self.dir / "logos"
        self.logos.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "templates.json"
        self._lock = threading.Lock()
        self.db = db
        if db is not None:
            self._restore()

    def _restore(self) -> None:
        """Templates are school configuration: the database copy wins over the local cache."""
        assert self.db is not None
        data = self.db.get_setting(_DB_KEY)
        if data:
            self.file.write_text(data, encoding="utf-8")
        for rel, content, _mtime in self.db.iter_files(_DB_OWNER):
            name = rel.split("/")[-1]
            if _LOGO.match(name):
                (self.logos / name).write_bytes(content)

    def _read(self) -> list[HeaderTemplate]:
        if not self.file.exists():
            return [builtin_template()]
        data = json.loads(self.file.read_text(encoding="utf-8"))
        templates = [HeaderTemplate.model_validate(t) for t in data]
        for i, template in enumerate(templates):
            if template.id == "padrao-madre" and template.revision < BUILTIN_REVISION:
                # Older built-in copy: adopt the school's current standard, keeping a custom logo.
                templates[i] = builtin_template().model_copy(update={"logo": template.logo})
        if not any(t.id == "padrao-madre" for t in templates):
            templates.insert(0, builtin_template())
        return templates

    def _write(self, templates: list[HeaderTemplate]) -> None:
        data = json.dumps([t.model_dump() for t in templates], ensure_ascii=False, indent=2)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.file)
        if self.db is not None:
            self.db.put_setting(_DB_KEY, data)

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
            if template.builtin:
                template.revision = BUILTIN_REVISION
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
        if self.db is not None:
            self.db.put_file(_DB_OWNER, f"logos/{name}", png_bytes, 0.0)
        return name
