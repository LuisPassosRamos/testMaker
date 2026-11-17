"""Send the finished document to the school's print inbox.

``MAIL_MODE=outbox`` (default) writes the e-mail as an .eml file under data/outbox
so the whole flow can be tested without sending anything; the UI says so clearly.
``MAIL_MODE=smtp`` sends through the configured server (e.g. Gmail with an app
password). Credentials only come from the environment, never from the browser.
"""

from __future__ import annotations

import html
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.domain.models import DOC_TYPE_LABELS, Exam

log = logging.getLogger(__name__)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


class MailError(Exception):
    pass


class PrintRequest(BaseModel):
    professor: str = Field(min_length=2, max_length=120)
    disciplina: str = Field(min_length=2, max_length=80)
    ano: str = Field(min_length=1, max_length=40)
    turma: str = Field(min_length=1, max_length=40)
    turno: str = Field("", max_length=40)
    unidade: str = Field("", max_length=40)
    data_aplicacao: str = Field("", max_length=40)
    copias: int = Field(ge=1, le=500)
    frente_verso: bool = True
    observacoes: str = Field("", max_length=1000)
    email_professor: str = Field("", max_length=200)

    @field_validator("email_professor")
    @classmethod
    def _check_email(cls, value: str) -> str:
        value = value.strip()
        if value and not _EMAIL.match(value):
            raise ValueError("E-mail inválido")
        return value

    @field_validator("professor", "disciplina", "ano", "turma", "turno", "unidade", "data_aplicacao")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return re.sub(r"[\r\n\t]+", " ", value).strip()


class SendOutcome(BaseModel):
    mode: str
    to: str
    stored_as: str | None = None


def build_message(exam: Exam, request: PrintRequest, pdf: Path, docx: Path, pdf_name: str, docx_name: str,
                  settings: Settings) -> EmailMessage:
    kind = DOC_TYPE_LABELS.get(exam.meta.doc_type, "Prova")
    subject = f"[Impressão] {kind} de {request.disciplina} – {request.ano} {request.turma} – Prof.(a) {request.professor}"
    rows = [
        ("Tipo", kind),
        ("Professor(a)", request.professor),
        ("Disciplina", request.disciplina),
        ("Ano / Turma", f"{request.ano} {request.turma}"),
        ("Turno", request.turno or "—"),
        ("Unidade", request.unidade or "—"),
        ("Data de aplicação", request.data_aplicacao or "—"),
        ("Quantidade de cópias", str(request.copias)),
        ("Frente e verso", "Sim" if request.frente_verso else "Não"),
        ("Observações", request.observacoes or "—"),
    ]
    text = "Olá, equipe de impressão!\n\nSegue documento para impressão:\n\n" + "\n".join(
        f"- {k}: {v}" for k, v in rows
    ) + "\n\nArquivos anexos: PDF pronto para imprimir e versão em Word.\nEnviado pelo Prova Pronta."
    html_rows = "".join(
        f"<tr><th align='left' style='padding:4px 12px 4px 0'>{html.escape(k)}</th>"
        f"<td style='padding:4px 0'>{html.escape(v).replace(chr(10), '<br>')}</td></tr>"
        for k, v in rows
    )
    body_html = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;color:#0f172a'>"
        "<p>Olá, equipe de impressão!</p><p>Segue documento para impressão:</p>"
        f"<table style='border-collapse:collapse'>{html_rows}</table>"
        "<p>Arquivos anexos: <b>PDF pronto para imprimir</b> e versão em Word.</p>"
        "<p style='color:#64748b;font-size:12px'>Enviado pelo Prova Pronta.</p></div>"
    )
    msg = EmailMessage()
    sender = settings.mail_from or settings.smtp_username or "prova-pronta@localhost"
    msg["From"] = formataddr(("Prova Pronta", sender))
    msg["To"] = settings.mail_to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="prova-pronta.local")
    if request.email_professor:
        msg["Reply-To"] = request.email_professor
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf_name)
    msg.add_attachment(
        docx.read_bytes(), maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document", filename=docx_name,
    )
    size = pdf.stat().st_size + docx.stat().st_size
    if size > settings.max_attachment_mb * 1024 * 1024:
        raise MailError(f"Os anexos passam de {settings.max_attachment_mb} MB. Reduza as imagens e tente de novo.")
    return msg


def send_message(msg: EmailMessage, settings: Settings) -> SendOutcome:
    if settings.mail_mode == "outbox":
        settings.outbox_dir.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{make_msgid().strip('<>').split('@')[0][-8:]}.eml"
        path = settings.outbox_dir / name
        path.write_bytes(bytes(msg))
        log.info("mail stored in outbox (%s)", name)
        return SendOutcome(mode="outbox", to=settings.mail_to, stored_as=name)

    if not settings.smtp_username or not settings.smtp_password:
        raise MailError("O envio por e-mail não está configurado. Peça ao responsável para preencher o arquivo .env.")
    try:
        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("O servidor de e-mail recusou o login. Confira o usuário e a senha de app no .env.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("smtp send failed: %s", exc.__class__.__name__)
        raise MailError("Não foi possível enviar o e-mail agora. Verifique a internet e tente de novo; "
                        "você também pode baixar o PDF e enviar manualmente.") from exc
    log.info("mail sent via smtp")
    return SendOutcome(mode="smtp", to=settings.mail_to)
