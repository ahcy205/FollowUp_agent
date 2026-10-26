import threading
import time
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.google.crypto import encrypt_token
from app.mail import sync
from app.mail.sync import SyncResult, run_sync_cycle
from app.models import GoogleAccount, GoogleAccountStatus
from tests.conftest import register
from tests.fake_google import make_raw


def _add_account(user_id: int, status=GoogleAccountStatus.CONNECTED) -> int:
    with SessionLocal() as db:
        acc = GoogleAccount(user_id=user_id, google_email=f"u{user_id}@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=status)
        db.add(acc)
        db.commit()
        return acc.id


def _two_users(client):
    a = register(client, "a@example.com")
    client.post("/api/auth/logout")
    b = register(client, "b@example.com")
    return a["id"], b["id"]


def test_cycle_skips_reauth_and_other_users(client, google_fake, monkeypatch):
    a, b = _two_users(client)
    ida = _add_account(a)
    _add_account(b, GoogleAccountStatus.REAUTH_REQUIRED)
    seen = []

    def fake_sync(db, account, client_, now=None):
        seen.append(account.id)
        return SyncResult(0, 0, 0, True)

    monkeypatch.setattr(sync, "sync_account", fake_sync)
    assert run_sync_cycle() == 1
    assert seen == [ida]
    seen.clear()
    assert run_sync_cycle(user_id=b) == 0
    assert run_sync_cycle(user_id=a) == 1
    # lease released after each run
    with SessionLocal() as db:
        assert db.get(GoogleAccount, ida).sync_lease_until is None


def test_cycle_continues_after_account_error(client, google_fake, monkeypatch):
    a, b = _two_users(client)
    ida, idb = _add_account(a), _add_account(b)
    seen = []

    def fake_sync(db, account, client_, now=None):
        seen.append(account.id)
        if len(seen) == 1:
            raise RuntimeError("boom")
        return SyncResult(0, 0, 0, True)

    monkeypatch.setattr(sync, "sync_account", fake_sync)
    assert run_sync_cycle() == 1  # the failed account is not counted
    assert sorted(seen) == sorted([ida, idb])
    with SessionLocal() as db:
        assert db.get(GoogleAccount, ida).sync_lease_until is None
        assert db.get(GoogleAccount, idb).sync_lease_until is None


def test_cycle_skips_account_with_held_lease(client, google_fake, monkeypatch):
    a, _ = _two_users(client)
    ida = _add_account(a)
    with SessionLocal() as db:
        assert sync.acquire_lease(db, ida, datetime.now(timezone.utc))
    monkeypatch.setattr(sync, "sync_account", lambda *a, **k: pytest.fail("must not run"))
    assert run_sync_cycle() == 0


def _connected(client, google_fake):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    _add_account(me["id"])


def test_sync_now_endpoint(client, google_fake):
    _connected(client, google_fake)
    google_fake.deliver(make_raw(sender="홍길동 <hong@acme.co.kr>", to="me@gmail.com",
                                 subject="견적 문의", body="본문"), labels=("INBOX",),
                        internal_date_ms=int(time.time() * 1000))
    r = client.post("/api/google/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mail_count"] == 1
    assert body["recent"][0]["subject"] == "견적 문의"
    assert body["status"] == "connected"


def test_sync_now_rate_limited(client, google_fake):
    _connected(client, google_fake)
    for _ in range(3):
        assert client.post("/api/google/sync").status_code == 200
    assert client.post("/api/google/sync").status_code == 429


def test_sync_now_not_connected(client, google_fake):
    register(client)
    assert client.post("/api/google/sync").status_code == 409


def test_sync_now_reauth_required_is_409(client, google_fake):
    me = register(client)
    _add_account(me["id"], GoogleAccountStatus.REAUTH_REQUIRED)
    assert client.post("/api/google/sync").status_code == 409


def test_sync_now_disabled_is_404(client):
    register(client)
    assert client.post("/api/google/sync").status_code == 404


def test_worker_wake_runs_cycle(monkeypatch):
    calls = []
    ran = threading.Event()

    def fake_cycle(*a, **k):
        calls.append(1)
        ran.set()
        return 0

    monkeypatch.setattr(sync, "run_sync_cycle", fake_cycle)
    monkeypatch.setattr(get_settings(), "mail_sync_interval_seconds", 3600)
    sync.start_worker()
    try:
        assert ran.wait(2)  # first cycle runs immediately
        ran.clear()
        sync.wake()
        assert ran.wait(2)
        assert len(calls) == 2
        thread = sync._worker._thread
    finally:
        sync.stop_worker()
    assert not thread.is_alive()
    assert sync._worker is None
    sync.wake()  # no-op without worker


def test_worker_survives_cycle_exception(monkeypatch):
    ran = threading.Event()
    n = []

    def fake_cycle(*a, **k):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("x")
        ran.set()
        return 0

    monkeypatch.setattr(sync, "run_sync_cycle", fake_cycle)
    monkeypatch.setattr(get_settings(), "mail_sync_interval_seconds", 3600)
    sync.start_worker()
    try:
        sync.wake()
        assert ran.wait(2)
    finally:
        sync.stop_worker()
