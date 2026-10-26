import json
import os
import tempfile
import uuid
from collections.abc import Callable

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-1234567890"
os.environ["LLM_API_KEY"] = ""
os.environ["DEMO_USER_PASSWORD"] = ""
os.environ["MAX_AGENT_STEPS"] = "10"
os.environ["MAIL_SYNC_ENABLED"] = "false"  # no background sync thread in tests

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agent import orchestrator  # noqa: E402
from app.agent.llm import LLMResponse, set_llm_override  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.google import oauth  # noqa: E402
from app.google.client import set_google_override  # noqa: E402
from app.mail import sync as mail_sync  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import agent_limiter, login_limiter  # noqa: E402
from tests.fake_google import FakeGoogle  # noqa: E402

TEST_GOOGLE_TOKEN_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # base64 of 32 bytes


class ScriptedLLM:
    """Deterministic stand-in for Claude. Each step is a function of the conversation so far."""

    def __init__(self, steps: list[Callable[[list[dict]], LLMResponse]] | None = None, analysis: dict | None = None):
        self.steps = list(steps or [])
        self.analysis = analysis or {}
        self.calls = 0
        self.seen_messages: list[list[dict]] = []

    def agent_turn(self, system, tools, messages):
        self.calls += 1
        self.seen_messages.append(json.loads(json.dumps(messages)))
        if not self.steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        return self.steps.pop(0)(messages)

    def structured(self, system, prompt, schema):
        return self.analysis


def tool_call(*calls: tuple[str, dict], text: str = "") -> LLMResponse:
    content = [{"type": "text", "text": text}] if text else []
    for name, args in calls:
        content.append({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:12]}", "name": name, "input": args})
    return LLMResponse(stop_reason="tool_use", content=content)


def final(text: str) -> LLMResponse:
    return LLMResponse(stop_reason="end_turn", content=[{"type": "text", "text": text}])


def last_results(messages: list[dict]) -> list[dict]:
    """Parsed JSON of tool results in the latest user message."""
    out = []
    for block in messages[-1]["content"]:
        if block.get("type") == "tool_result" and not block.get("is_error"):
            out.append(json.loads(block["content"]))
    return out


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    orchestrator.set_dispatcher(orchestrator.execute_run)  # run the agent inline
    login_limiter.reset()
    agent_limiter.reset()
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
