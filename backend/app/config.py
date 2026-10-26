"""Application settings. Every secret comes from the environment — nothing is hardcoded."""

import base64
import binascii
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent

DEFAULT_MODELS = {"gemini": "gemini-3.6-flash", "anthropic": "claude-opus-5"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    app_env: str = "development"
    secret_key: str = ""
    database_url: str = "sqlite:///./dev.db"

    llm_provider: str = "gemini"  # gemini | anthropic
    llm_api_key: str = ""
    llm_model: str = ""  # empty → provider default below
    llm_analysis_model: str = ""  # model for analyze_email; empty → llm_model (a separate model has its own quota)
    llm_effort: str = "medium"
    llm_refusal_fallback: bool = True
    llm_request_timeout: float = 120.0
    llm_rpm_limit: int = Field(default=0, ge=0, le=1000)  # 0 = no client-side pacing (Gemini free tier: 5)

    max_agent_steps: int = Field(default=10, ge=1, le=30)
    llm_transient_retries: int = Field(default=2, ge=0, le=5)  # in-run retries for server errors / timeouts
    agent_timeout_seconds: int = Field(default=240, ge=10, le=900)

    public_url: str = "http://localhost:5173"
    allowed_hosts: str = "localhost,127.0.0.1"

    demo_user_email: str = "demo@example.com"
    demo_user_password: str = ""
    allow_registration: bool = True

    session_hours: int = 12

    google_client_id: str = ""
    google_client_secret: str = ""
    google_token_key: str = ""  # base64 of 32 random bytes; encrypts refresh tokens (AES-GCM)
    mail_sync_interval_seconds: int = Field(default=90, ge=30)
    mail_backfill_days: int = Field(default=3, ge=1, le=30)
    mail_sync_max_per_cycle: int = 200
    mail_sync_enabled: bool = True
    user_timezone: str = "Asia/Seoul"

    # Injected automatically by Railway
    railway_public_domain: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_token_key)

    @property
    def google_redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/api/google/callback"

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if self.railway_public_domain:
            hosts += [self.railway_public_domain, "healthcheck.railway.app"]
        return hosts

    @property
    def allowed_origins(self) -> list[str]:
        origins = {self.public_url.rstrip("/")}
        if self.railway_public_domain:
            origins.add(f"https://{self.railway_public_domain}")
        if not self.is_production:
            origins |= {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}
        return sorted(origins)

    @field_validator("llm_api_key")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        return v.strip().strip('"').strip("'")

    @field_validator("google_token_key")
    @classmethod
    def _check_token_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        try:
            ok = len(base64.b64decode(v, validate=True)) == 32
        except (binascii.Error, ValueError):
            ok = False
        if not ok:
            raise ValueError("GOOGLE_TOKEN_KEY must be 32 bytes, base64-encoded")
        return v

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Relative SQLite paths resolve against backend/, regardless of the launch directory.
        if v.startswith("sqlite:///./"):
            v = f"sqlite:///{(BACKEND_DIR / v[len('sqlite:///./'):]).as_posix()}"
        # Railway provides postgres:// or postgresql:// — route through psycopg v3.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        self.llm_provider = self.llm_provider.strip().lower()
        if self.llm_provider not in DEFAULT_MODELS:
            raise ValueError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}")
        if not self.llm_model:
            self.llm_model = DEFAULT_MODELS[self.llm_provider]
        if not self.llm_analysis_model:
            self.llm_analysis_model = self.llm_model
        if self.is_production:
            if len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set (>= 32 chars) in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("DATABASE_URL must point to PostgreSQL in production")
            if "*" in self.allowed_host_list:
                raise ValueError("ALLOWED_HOSTS must not contain '*' in production")
        elif not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)
            log.warning("SECRET_KEY not set; using an ephemeral development key (sessions reset on restart)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
