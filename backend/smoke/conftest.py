"""Real-LLM smoke tests. Opt-in: they spend provider quota and need LLM_API_KEY (backend/.env or env).

    RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke -v -s

Never run together with tests/ (that conftest blanks LLM_API_KEY for the offline suite).
"""

import os
import tempfile

OPTED_IN = os.environ.get("RUN_LLM_SMOKE") == "1"

if not OPTED_IN:
    collect_ignore_glob = ["test_*.py"]
else:
    _tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/smoke.db"
    os.environ["APP_ENV"] = "test"
    os.environ.setdefault("SECRET_KEY", "smoke-secret-key-that-is-long-enough-1234567890")
    os.environ["DEMO_USER_PASSWORD"] = ""

    from app.config import get_settings

    if not get_settings().llm_api_key:
        raise RuntimeError("RUN_LLM_SMOKE=1 but LLM_API_KEY is not configured (backend/.env or environment)")


def pytest_report_header(config):
    return None if OPTED_IN else "smoke: skipped (set RUN_LLM_SMOKE=1 to call the real LLM)"
