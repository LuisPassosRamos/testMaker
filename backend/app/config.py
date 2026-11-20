"""Application settings, read from environment variables or a `.env` file.

Every setting has a safe default so the app runs locally without configuration:
e-mails go to a local outbox and the optional AI assistant stays disabled.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage and retention
    data_dir: Path = REPO_DIR / "data"
    # PostgreSQL (e.g. a free Neon database) makes projects and templates survive restarts and
    # ephemeral disks; the local folder then works as a cache. Empty = local files only.
    database_url: SecretStr | None = None
    retention_hours: int = Field(72, ge=1, description="Projects untouched for longer are deleted.")

    # Upload limits (uploaded files are treated as untrusted input)
    max_upload_mb: int = Field(25, ge=1, le=200)
    max_files_per_project: int = Field(30, ge=1, le=100)
    max_pdf_pages: int = Field(40, ge=1, le=300)
    max_image_megapixels: int = Field(60, ge=5, le=200)

    # DOCX -> PDF conversion ("auto" tries LibreOffice first, then Microsoft Word on Windows)
    converter: Literal["auto", "libreoffice", "word"] = "auto"
    libreoffice_path: str | None = None
    conversion_timeout_s: int = 180

    # OCR (Tesseract)
    tesseract_cmd: str | None = None
    tessdata_dir: Path | None = None
    ocr_languages: str = "por"
    ocr_low_confidence: int = Field(70, ge=0, le=100)
    ocr_timeout_s: int = 180

    # E-mail for printing
    mail_mode: Literal["outbox", "smtp"] = "outbox"
    mail_to: str = "escolamadre2impressoes@gmail.com"
    mail_from: str | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    max_attachment_mb: int = 20

    # AI. "auto" uses Heroku Managed Inference when its add-on is attached (it sets INFERENCE_URL and
    # INFERENCE_KEY), otherwise Google Gemini when GEMINI_API_KEY is set. Without either, the app works
    # with the deterministic reader and AI features are hidden.
    ai_provider: Literal["auto", "heroku", "gemini", "off"] = "auto"
    inference_url: str | None = None
    inference_key: SecretStr | None = None
    # Fast model to organise uploaded exams; stronger model to write new exams.
    ai_model_read: str = "claude-haiku-5-5"
    ai_model_write: str = "claude-sonnet-5-5"
    ai_max_tokens_read: int = Field(4096, ge=256, le=64000)
    ai_max_tokens_write: int = Field(8192, ge=256, le=64000)
    ai_timeout_s: int = Field(240, ge=10, le=900)
    # Organise every uploaded exam with AI automatically (teachers can still choose "ler sem IA").
    ai_reading: bool = True
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_s: int = 90

    # Access: when set, teachers type this school code once per device before using the app.
    access_code: SecretStr | None = None
    session_days: int = Field(60, ge=1, le=400)
    max_sends_per_hour: int = Field(20, ge=1, le=500)
    # Parallel background jobs (OCR, PDF). Use 1 on hosts with 512 MB of RAM.
    job_workers: int = Field(2, ge=1, le=8)

    # Web
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_dist: Path = REPO_DIR / "frontend" / "dist"
    log_level: str = "INFO"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def access_required(self) -> bool:
        return bool(self.access_code and self.access_code.get_secret_value().strip())

    @property
    def heroku_ai_configured(self) -> bool:
        return bool(self.inference_url and self.inference_key and self.inference_key.get_secret_value().strip())

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())

    @property
    def ai_backend(self) -> Literal["heroku", "gemini"] | None:
        if self.ai_provider == "off":
            return None
        if self.ai_provider in ("auto", "heroku") and self.heroku_ai_configured:
            return "heroku"
        if self.ai_provider in ("auto", "gemini") and self.gemini_configured:
            return "gemini"
        return None

    @property
    def ai_enabled(self) -> bool:
        return self.ai_backend is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
