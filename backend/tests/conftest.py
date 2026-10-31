import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-1234567890"
os.environ["LLM_API_KEY"] = ""
os.environ["MAIL_SYNC_ENABLED"] = "false"  # no background sync thread in tests
# Real values in backend/.env must never reach tests: environment variables beat env_file in pydantic-settings.
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["GOOGLE_TOKEN_KEY"] = ""
os.environ["PUBLIC_URL"] = "http://localhost:5173"  # Origin/CSRF checks, redirect URI
os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1"
os.environ["LLM_PROVIDER"] = "gemini"
os.environ["LLM_MODEL"] = ""
os.environ["LLM_ANALYSIS_MODEL"] = ""
os.environ["LLM_RPM_LIMIT"] = "0"  # no client-side pacing sleeps
os.environ["MAIL_BACKFILL_DAYS"] = "3"
os.environ["USER_TIMEZONE"] = "Asia/Seoul"
os.environ["MAIL_TRIAGE_ENABLED"] = "false"  # mail text must never reach an LLM unless a test turns it on
os.environ["KNOWLEDGE_ENABLED"] = "false"  # uploaded documents must never reach an LLM unless a test turns it on
os.environ["KNOWLEDGE_MAX_FILE_MB"] = "10"
os.environ["KNOWLEDGE_MAX_SOURCES"] = "200"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agent.llm import set_llm_override  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.google import oauth  # noqa: E402
from app.google.client import set_google_override  # noqa: E402
from app.mail import sync as mail_sync  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import ai_action_limiter, calendar_retry_limiter, google_sync_limiter, login_limiter, send_limiter, upload_limiter  # noqa: E402
from tests.fake_google import FakeGoogle  # noqa: E402

TEST_GOOGLE_TOKEN_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # base64 of 32 bytes


class ScriptedLLM:
    """Deterministic stand-in for the LLM's structured() calls."""

    def __init__(self, analysis: dict | None = None, structured_steps: list | None = None):
        self.analysis = analysis or {}
        # structured() answers in order: a dict is returned, an Exception is raised,
        # a callable(prompt, schema) is called. Once empty, `analysis` is returned.
        self.structured_steps = list(structured_steps or [])
        self.structured_prompts: list[str] = []

    def structured(self, system, prompt, schema):
        self.structured_prompts.append(prompt)
        if not self.structured_steps:
            return self.analysis
        step = self.structured_steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(prompt, schema)
        return step


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    login_limiter.reset()
    google_sync_limiter.reset()
    calendar_retry_limiter.reset()
    ai_action_limiter.reset()
    send_limiter.reset()
    upload_limiter.reset()
    oauth.clear_access_token_cache()
    mail_sync.clear_skipped()
    yield
    set_llm_override(None)
    set_google_override(None)
    oauth.clear_access_token_cache()
    mail_sync.clear_skipped()


def make_client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


@pytest.fixture
def google_fake(monkeypatch) -> FakeGoogle:
    """Turn Google on (fake credentials) and route every Google call to a fresh FakeGoogle."""
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "test-client-id")
    monkeypatch.setattr(s, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(s, "google_token_key", TEST_GOOGLE_TOKEN_KEY)
    fake = FakeGoogle()
    set_google_override(fake)
    return fake


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def register(c: TestClient, email: str = "user@example.com", password: str = "password123") -> dict:
    r = c.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()
