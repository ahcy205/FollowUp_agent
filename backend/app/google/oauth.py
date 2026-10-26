"""OAuth helpers: PKCE, the signed state cookie, and the in-memory access-token cache.

Access tokens live only in this process's memory. Refresh tokens live only encrypted in the DB.
Never log tokens, authorization codes, or the state value.
"""

import base64
import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.google.client import AccessToken, GoogleAuthError, GoogleClient
from app.google.crypto import TokenDecryptError, decrypt_token
from app.models import GoogleAccount, GoogleAccountStatus

STATE_COOKIE = "google_oauth"
STATE_COOKIE_PATH = "/api/google"
STATE_TTL_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=60)
REAUTH_MESSAGE = "Google 재연결이 필요합니다."
_JWT_ALG = "HS256"
_STATE_AUDIENCE = "google-oauth-state"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- PKCE + state ---

def new_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43..128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def make_state_cookie(user_id: int, state: str, verifier: str) -> str:
    now = _now()
    payload = {"uid": user_id, "state": state, "verifier": verifier, "aud": _STATE_AUDIENCE,
               "iat": now, "exp": now + timedelta(seconds=STATE_TTL_SECONDS)}
    return jwt.encode(payload, get_settings().secret_key, algorithm=_JWT_ALG)


def read_state_cookie(value: str) -> dict | None:
    try:
        d = jwt.decode(value, get_settings().secret_key, algorithms=[_JWT_ALG], audience=_STATE_AUDIENCE)
    except jwt.PyJWTError:
        return None
    if not isinstance(d.get("uid"), int) or not isinstance(d.get("state"), str) or not isinstance(d.get("verifier"), str):
        return None
    return d


# States already used in a callback, kept until their cookie would have expired anyway.
# This makes a replayed (copied) cookie fail even though its JWT is still valid.
_used_states: dict[str, datetime] = {}
_used_lock = threading.Lock()


def consume_state(state: str) -> bool:
    """Mark a state as used. False if it was used before."""
    now = _now()
    with _used_lock:
        for k in [k for k, exp in _used_states.items() if exp <= now]:
            del _used_states[k]
        if state in _used_states:
            return False
        _used_states[state] = now + timedelta(seconds=STATE_TTL_SECONDS)
        return True


# --- access-token cache ---

_tokens: dict[int, AccessToken] = {}
_tokens_lock = threading.Lock()


def remember_access_token(user_id: int, token: AccessToken) -> None:
    with _tokens_lock:
        _tokens[user_id] = token


def forget_access_token(user_id: int) -> None:
    with _tokens_lock:
        _tokens.pop(user_id, None)


def clear_access_token_cache() -> None:
    with _tokens_lock:
        _tokens.clear()
    with _used_lock:
        _used_states.clear()


def _mark_reauth(db: Session, account: GoogleAccount) -> None:
    forget_access_token(account.user_id)
    account.status = GoogleAccountStatus.REAUTH_REQUIRED
    account.last_sync_error = REAUTH_MESSAGE
    db.commit()


def access_token_for(db: Session, account: GoogleAccount, client: GoogleClient) -> str:
    """A valid access token for this account: cached, or refreshed when it expires within 60 seconds.

    On GoogleAuthError (grant revoked/expired) or an undecryptable refresh token, the account is
    marked reauth_required, committed, and GoogleAuthError is raised.
    """
    with _tokens_lock:
        cached = _tokens.get(account.user_id)
    if cached is not None and cached.expires_at - REFRESH_MARGIN > _now():
        return cached.token
    try:
        refresh_token = decrypt_token(account.refresh_token_enc)
    except TokenDecryptError as e:
        _mark_reauth(db, account)
        raise GoogleAuthError("저장된 Google 토큰을 읽을 수 없습니다.") from e
    try:
        fresh = client.refresh_access_token(refresh_token)
    except GoogleAuthError:
        _mark_reauth(db, account)
        raise
    remember_access_token(account.user_id, fresh)
    return fresh.token
