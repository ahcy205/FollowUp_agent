from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.google import oauth
from app.google.client import REQUIRED_SCOPES, GoogleAuthError, TokenGrant
from app.google.crypto import decrypt_token
from app.mail import sync as mail_sync
from app.models import Email, GoogleAccount, GoogleAccountStatus
from tests.conftest import make_client, register


def _connect(c) -> str:
    r = c.get("/api/google/connect", follow_redirects=False)
    assert r.status_code == 302, r.text
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def _callback(c, state: str, code: str = "auth-code"):
    return c.get("/api/google/callback", params={"code": code, "state": state}, follow_redirects=False)


def _reason(r) -> str | None:
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert loc.path == "/settings"
    q = parse_qs(loc.query)
    assert q["google"][0] in ("connected", "error")
    return q.get("reason", [None])[0]


def _accounts() -> list[GoogleAccount]:
    with SessionLocal() as db:
        return list(db.scalars(select(GoogleAccount)))


def test_status_disabled_when_not_configured(client):
    register(client)
    r = client.get("/api/google/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["status"] == "disconnected"
    assert body["mail_count"] == 0 and body["recent"] == []


def test_connect_404_when_disabled(client):
    register(client)
    assert client.get("/api/google/connect", follow_redirects=False).status_code == 404


def test_connect_redirects_with_state_cookie(client, google_fake):
    register(client)
    r = client.get("/api/google/connect", follow_redirects=False)
    assert r.status_code == 302
    assert urlparse(r.headers["location"]).netloc == "accounts.google.com"
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"][0] and q["code_challenge"][0]
    set_cookie = r.headers["set-cookie"]
    assert "google_oauth=" in set_cookie
    assert "Path=/api/google" in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie.replace("Lax", "lax")
    assert client.get("/api/google/status").json()["enabled"] is True


def test_callback_stores_encrypted_token(client, google_fake):
    register(client)
    r = _callback(client, _connect(client))
    assert _reason(r) is None
    assert parse_qs(urlparse(r.headers["location"]).query)["google"] == ["connected"]
    [acc] = _accounts()
    assert acc.refresh_token_enc != "1//fake-refresh"
    assert "1//fake-refresh" not in acc.refresh_token_enc
    assert decrypt_token(acc.refresh_token_enc) == "1//fake-refresh"
    assert acc.status == GoogleAccountStatus.CONNECTED
    assert acc.google_email == "me@gmail.com"
    assert acc.history_id is None
    st = client.get("/api/google/status").json()
    assert st["status"] == "connected" and st["google_email"] == "me@gmail.com"
    assert set(st["scopes"]) == set(REQUIRED_SCOPES)
    # state cookie cleared
    assert "google_oauth" not in client.cookies


def test_callback_calls_wake(client, google_fake, monkeypatch):
    calls = []
    monkeypatch.setattr("app.mail.sync.wake", lambda: calls.append(1))
    register(client)
    _callback(client, _connect(client))
    assert calls == [1]


def test_callback_state_mismatch_rejected(client, google_fake):
    register(client)
    _connect(client)
    assert _reason(_callback(client, "wrong-state")) == "state"
    assert _accounts() == []


def test_callback_state_reuse_rejected(client, google_fake):
    register(client)
    state = _connect(client)
    cookie = client.cookies.get("google_oauth")
    assert _reason(_callback(client, state)) is None
    # replaying the exact same cookie must still fail: the state is single-use
    client.cookies.set("google_oauth", cookie, path="/api/google")
    assert _reason(_callback(client, state)) == "state"
    # and without the cookie (it was deleted) it fails too
    client.cookies.delete("google_oauth", path="/api/google")
    assert _reason(_callback(client, state)) == "state"


def test_callback_other_user_session_rejected(client, google_fake):
    register(client, "a@example.com")
    state = _connect(client)
    cookie = client.cookies.get("google_oauth")
    with make_client() as other:
        register(other, "b@example.com")
        other.cookies.set("google_oauth", cookie, path="/api/google")
        assert _reason(_callback(other, state)) == "state"
    assert _accounts() == []


def test_callback_without_session_redirects_to_login(client, google_fake):
    register(client)
    state = _connect(client)
    client.cookies.delete("agent_session")
    r = _callback(client, state)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_callback_missing_scope(client, google_fake):
    google_fake.grant = TokenGrant(access_token="at-x", refresh_token="1//r", expires_in=3600,
                                   scopes=[s for s in REQUIRED_SCOPES if not s.endswith("gmail.send")])
    register(client)
    assert _reason(_callback(client, _connect(client))) == "scopes"
    assert _accounts() == []
    assert google_fake.revoked == ["at-x"]  # first connect: the unused grant is dropped


def test_callback_accepts_userinfo_email_alias(client, google_fake):
    # Google reports the "email" scope back as userinfo.email
    scopes = [s for s in REQUIRED_SCOPES if s != "email"] + ["https://www.googleapis.com/auth/userinfo.email"]
    google_fake.grant = TokenGrant(access_token="at-x", refresh_token="1//r", expires_in=3600, scopes=scopes)
    register(client)
    assert _reason(_callback(client, _connect(client))) is None
    assert len(_accounts()) == 1


def test_callback_without_refresh_token(client, google_fake):
    google_fake.grant = TokenGrant(access_token="at-x", refresh_token=None, expires_in=3600,
                                   scopes=list(REQUIRED_SCOPES))
    register(client)
    assert _reason(_callback(client, _connect(client))) == "no_refresh_token"
    assert _accounts() == []
    assert google_fake.revoked == ["at-x"]


@pytest.mark.parametrize("grant, reason", [
    (TokenGrant(access_token="at-bad", refresh_token="1//other", expires_in=3600,
                scopes=[s for s in REQUIRED_SCOPES if not s.endswith("gmail.send")]), "scopes"),
    (TokenGrant(access_token="at-bad", refresh_token=None, expires_in=3600,
                scopes=list(REQUIRED_SCOPES)), "no_refresh_token"),
])
def test_failed_reconnect_keeps_existing_connection(client, google_fake, grant, reason):
    # Google keeps one grant per user+client: revoking the new token would kill the stored refresh token.
    register(client)
    assert _reason(_callback(client, _connect(client))) is None
    [before] = _accounts()
    google_fake.grant = grant
    assert _reason(_callback(client, _connect(client))) == reason
    assert google_fake.revoked == []
    [after] = _accounts()
    assert after.status == GoogleAccountStatus.CONNECTED
    assert after.refresh_token_enc == before.refresh_token_enc
    assert after.scopes == before.scopes and after.last_sync_error == ""
    assert client.get("/api/google/status").json()["status"] == "connected"


def test_callback_user_denied(client, google_fake):
    register(client)
    _connect(client)
    r = client.get("/api/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert _reason(r) == "denied"
    assert "access_denied" not in r.headers["location"]
    assert _accounts() == []


def test_callback_exchange_failure(client, google_fake, monkeypatch):
    from app.google.client import GoogleError

    def boom(**kw):
        raise GoogleError("secret detail from google")

    monkeypatch.setattr(google_fake, "exchange_code", boom)
    register(client)
    r = _callback(client, _connect(client))
    assert _reason(r) == "exchange"
    assert "secret" not in r.headers["location"]
    assert _accounts() == []


def _reconnect_setup(client, google_fake) -> int:
    """Connect once, record sync progress, then mark the account for reauth. Returns the account id."""
    register(client)
    _callback(client, _connect(client))
    with SessionLocal() as db:
        acc = db.scalar(select(GoogleAccount))
        acc.history_id = "555"
        acc.last_synced_at = datetime.now(timezone.utc)
        acc.status = GoogleAccountStatus.REAUTH_REQUIRED
        acc.last_sync_error = "Google 재연결이 필요합니다."
        db.commit()
        acc_id = acc.id
    mail_sync._mark_skipped(acc_id, "old-msg")
    google_fake.grant = TokenGrant(access_token="at-2", refresh_token="1//second", expires_in=3600,
                                   scopes=list(REQUIRED_SCOPES))
    return acc_id


def test_reconnect_same_address_keeps_history(client, google_fake):
    acc_id = _reconnect_setup(client, google_fake)
    google_fake.email = "Me@Gmail.com"  # same mailbox, different case
    assert _reason(_callback(client, _connect(client))) is None
    [acc] = _accounts()
    assert decrypt_token(acc.refresh_token_enc) == "1//second"
    assert acc.status == GoogleAccountStatus.CONNECTED
    assert acc.last_sync_error == ""
    assert acc.history_id == "555"
    assert acc.last_synced_at is not None
    assert mail_sync._skipped_ids(acc_id) == {"old-msg"}
    assert google_fake.revoked == []


def test_reconnect_different_address_resets_sync_point(client, google_fake):
    acc_id = _reconnect_setup(client, google_fake)
    me = client.get("/api/auth/me").json()
    _add_gmail(me["id"], "old-gid", "이전 주소 메일", datetime.now(timezone.utc))
    google_fake.email = "new@gmail.com"
    assert _reason(_callback(client, _connect(client))) is None
    [acc] = _accounts()
    assert acc.id == acc_id
    assert acc.google_email == "new@gmail.com"
    assert decrypt_token(acc.refresh_token_enc) == "1//second"
    assert acc.status == GoogleAccountStatus.CONNECTED
    assert acc.history_id is None
    assert acc.last_synced_at is None
    assert mail_sync._skipped_ids(acc_id) == set()
    assert google_fake.revoked == ["1//fake-refresh"]  # the old mailbox's grant, never the new one
    with SessionLocal() as db:  # mail already stored from the old address is kept
        assert db.scalar(select(Email).where(Email.gmail_message_id == "old-gid")) is not None


def test_reconnect_different_address_when_old_revoke_fails(client, google_fake):
    _reconnect_setup(client, google_fake)
    google_fake.revoke_fails = True
    google_fake.email = "new@gmail.com"
    assert _reason(_callback(client, _connect(client))) is None
    [acc] = _accounts()
    assert acc.google_email == "new@gmail.com" and acc.history_id is None
    assert decrypt_token(acc.refresh_token_enc) == "1//second"


def test_reconnect_different_address_with_undecryptable_old_token(client, google_fake):
    _reconnect_setup(client, google_fake)
    with SessionLocal() as db:
        db.scalar(select(GoogleAccount)).refresh_token_enc = "garbage"
        db.commit()
    google_fake.email = "new@gmail.com"
    assert _reason(_callback(client, _connect(client))) is None
    [acc] = _accounts()
    assert acc.history_id is None and decrypt_token(acc.refresh_token_enc) == "1//second"
    assert google_fake.revoked == []


def test_disconnect_revokes_and_deletes(client, google_fake):
    register(client)
    _callback(client, _connect(client))
    r = client.post("/api/google/disconnect", json={"delete_mail": False})
    assert r.status_code == 204
    assert "1//fake-refresh" in google_fake.revoked
    assert _accounts() == []
    assert client.get("/api/google/status").json()["status"] == "disconnected"


def test_disconnect_when_revoke_fails(client, google_fake):
    register(client)
    _callback(client, _connect(client))
    google_fake.revoke_fails = True
    assert client.post("/api/google/disconnect", json={"delete_mail": False}).status_code == 204
    assert _accounts() == []


def test_disconnect_when_not_connected(client, google_fake):
    register(client)
    assert client.post("/api/google/disconnect", json={"delete_mail": False}).status_code == 204


def _add_gmail(user_id: int, gid: str, subject: str, received_at: datetime) -> None:
    with SessionLocal() as db:
        db.add(Email(user_id=user_id, sender="x@y.com", receiver="me@gmail.com", subject=subject, content="c",
                     direction="INBOUND", gmail_message_id=gid, received_at=received_at))
        db.commit()


def test_disconnect_delete_mail(client, google_fake):
    me = register(client)
    _callback(client, _connect(client))
    with SessionLocal() as db:
        seed_count = len(list(db.scalars(select(Email).where(Email.user_id == me["id"]))))
    assert seed_count > 0
    _add_gmail(me["id"], "g1", "hello", datetime.now(timezone.utc))
    assert client.post("/api/google/disconnect", json={"delete_mail": True}).status_code == 204
    with SessionLocal() as db:
        left = list(db.scalars(select(Email).where(Email.user_id == me["id"])))
    assert len(left) == seed_count
    assert all(e.gmail_message_id is None for e in left)


def test_disconnect_keeps_mail_by_default(client, google_fake):
    me = register(client)
    _callback(client, _connect(client))
    _add_gmail(me["id"], "g1", "hello", datetime.now(timezone.utc))
    assert client.post("/api/google/disconnect", json={"delete_mail": False}).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(Email).where(Email.gmail_message_id == "g1")) is not None


def test_status_mail_count_and_recent(client, google_fake):
    me = register(client)
    with make_client() as other:
        them = register(other, "b@example.com")
    now = datetime.now(timezone.utc)
    for i in range(7):
        _add_gmail(me["id"], f"g{i}", f"s{i}", now - timedelta(minutes=10 - i))
    _add_gmail(them["id"], "gx", "theirs", now)
    st = client.get("/api/google/status").json()
    assert st["mail_count"] == 7
    assert [m["subject"] for m in st["recent"]] == ["s6", "s5", "s4", "s3", "s2"]
    assert set(st["recent"][0]) >= {"subject", "sender", "received_at"}


def test_status_triage_fields(client, google_fake, monkeypatch):
    from app.config import get_settings

    me = register(client)
    with make_client() as other:
        them = register(other, "b@example.com")
    now = datetime.now(timezone.utc)
    st = client.get("/api/google/status").json()
    assert st["triage_counts"] == {"pending": 0, "work": 0, "not_work": 0, "filtered": 0}
    assert st["triage_enabled"] is False

    def add(uid, gid, triage, reason="", minutes=0):
        with SessionLocal() as db:
            db.add(Email(user_id=uid, sender="x@y.com", receiver="me@gmail.com", subject=gid, content="c",
                         direction="INBOUND", gmail_message_id=gid, received_at=now - timedelta(minutes=minutes),
                         triage=triage, triage_reason=reason))
            db.commit()

    add(me["id"], "w1", "work", "견적 요청", 1)
    add(me["id"], "w2", "work", "", 2)
    add(me["id"], "n1", "not_work", "", 3)
    add(me["id"], "p1", "pending", "", 4)
    add(them["id"], "t1", "work", "", 0)  # another user's mail is not counted
    with SessionLocal() as db:  # seed mail without a Gmail id is not counted either
        db.add(Email(user_id=me["id"], sender="s", receiver="r", subject="seed", content="c",
                     direction="INBOUND", triage="work"))
        db.commit()
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    st = client.get("/api/google/status").json()
    assert st["triage_counts"] == {"pending": 1, "work": 2, "not_work": 1, "filtered": 0}
    assert st["triage_enabled"] is True
    first = st["recent"][0]
    assert first["triage"] == "work" and first["triage_reason"] == "견적 요청" and first["task_id"] is None


def test_status_requires_login():
    with make_client() as c:
        assert c.get("/api/google/status").status_code == 401


# --- access_token_for ---

def _stored_account(client) -> GoogleAccount:
    _callback(client, _connect(client))
    with SessionLocal() as db:
        return db.scalar(select(GoogleAccount))


def test_access_token_cached_and_refreshed(client, google_fake):
    register(client)
    _stored_account(client)
    with SessionLocal() as db:
        acc = db.scalar(select(GoogleAccount))
        t1 = oauth.access_token_for(db, acc, google_fake)
        t2 = oauth.access_token_for(db, acc, google_fake)
        assert t1 == t2  # cached (seeded from the code exchange)
        oauth.forget_access_token(acc.user_id)
        t3 = oauth.access_token_for(db, acc, google_fake)
        assert t3 != t1 and t3.startswith("at-")


def test_access_token_refreshes_near_expiry(client, google_fake, monkeypatch):
    register(client)
    _stored_account(client)
    with SessionLocal() as db:
        acc = db.scalar(select(GoogleAccount))
        first = oauth.access_token_for(db, acc, google_fake)
        real_now = oauth._now
        monkeypatch.setattr(oauth, "_now", lambda: real_now() + timedelta(seconds=3600 - 59))
        assert oauth.access_token_for(db, acc, google_fake) != first


def test_access_token_auth_error_marks_reauth(client, google_fake):
    register(client)
    _stored_account(client)
    google_fake.valid_refresh_tokens.clear()  # Google revoked the grant
    with SessionLocal() as db:
        acc = db.scalar(select(GoogleAccount))
        oauth.forget_access_token(acc.user_id)
        with pytest.raises(GoogleAuthError):
            oauth.access_token_for(db, acc, google_fake)
    [acc] = _accounts()
    assert acc.status == GoogleAccountStatus.REAUTH_REQUIRED
    assert acc.last_sync_error == "Google 재연결이 필요합니다."
    assert client.get("/api/google/status").json()["status"] == "reauth_required"


def test_pkce_pair():
    import base64
    import hashlib

    verifier, challenge = oauth.new_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_state_cookie_roundtrip_and_tamper():
    v = oauth.make_state_cookie(7, "st", "ver")
    d = oauth.read_state_cookie(v)
    assert {k: d[k] for k in ("uid", "state", "verifier")} == {"uid": 7, "state": "st", "verifier": "ver"}
    assert oauth.read_state_cookie(v[:-2] + "xx") is None
    assert oauth.read_state_cookie("garbage") is None
