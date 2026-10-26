"""Google account connection: OAuth connect/callback, status, disconnect."""

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.google import oauth
from app.google.client import REQUIRED_SCOPES, AccessToken, GoogleError, get_google_client
from app.google.crypto import TokenDecryptError, decrypt_token, encrypt_token
from app.mail import sync as mail_sync
from app.models import Email, GoogleAccount, GoogleAccountStatus, User
from app.schemas import GoogleDisconnect, GoogleStatusOut, RecentMailOut
from app.security import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/google", tags=["google"])

SETTINGS_PATH = "/settings"
RECENT_LIMIT = 5

# Google echoes some scopes back under their long names.
_SCOPE_ALIASES = {
    "https://www.googleapis.com/auth/userinfo.email": "email",
    "https://www.googleapis.com/auth/userinfo.profile": "profile",
}


def _normalize_scopes(scopes: list[str]) -> set[str]:
    return {_SCOPE_ALIASES.get(s, s) for s in scopes}


def _session_user(request: Request, db: Session) -> User | None:
    """Like get_current_user, but returns None instead of raising 401 (for browser redirects)."""
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def _account(db: Session, user_id: int) -> GoogleAccount | None:
    return db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user_id))


def _gmail_mail(user_id: int):
    return (Email.user_id == user_id, Email.gmail_message_id.is_not(None))


def build_status(db: Session, user: User) -> GoogleStatusOut:
    acc = _account(db, user.id)
    where = _gmail_mail(user.id)
    mail_count = db.scalar(select(func.count()).select_from(Email).where(*where)) or 0
    recent = db.scalars(
        select(Email).where(*where).order_by(Email.received_at.desc().nulls_last(), Email.id.desc()).limit(RECENT_LIMIT)
    ).all()
    out = GoogleStatusOut(
        enabled=get_settings().google_enabled,
        status="disconnected",
        mail_count=mail_count,
        recent=[RecentMailOut.model_validate(e) for e in recent],
    )
    if acc is not None:
        out.status = "reauth_required" if acc.status == GoogleAccountStatus.REAUTH_REQUIRED else "connected"
        out.google_email = acc.google_email
        out.scopes = list(acc.scopes or [])
        out.last_synced_at = acc.last_synced_at
        out.last_sync_error = acc.last_sync_error or ""
    return out


def _settings_redirect(*, reason: str | None = None) -> RedirectResponse:
    url = f"{SETTINGS_PATH}?google=connected" if reason is None else f"{SETTINGS_PATH}?google=error&reason={reason}"
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(oauth.STATE_COOKIE, path=oauth.STATE_COOKIE_PATH)
    return resp


def _best_effort_revoke(token: str) -> None:
    try:
        get_google_client().revoke(token)
    except GoogleError as e:
        log.warning("google revoke failed: %s", type(e).__name__)


@router.get("/status", response_model=GoogleStatusOut)
def google_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return build_status(db, user)


@router.get("/connect")
def connect(user: User = Depends(get_current_user)):
    s = get_settings()
    if not s.google_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google 연결이 설정되어 있지 않습니다.")
    state = secrets.token_urlsafe(32)
    verifier, challenge = oauth.new_pkce()
    url = get_google_client().authorization_url(state=state, code_challenge=challenge,
                                                redirect_uri=s.google_redirect_uri)
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        oauth.STATE_COOKIE,
        oauth.make_state_cookie(user.id, state, verifier),
        max_age=oauth.STATE_TTL_SECONDS,
        httponly=True,
        secure=s.is_production,
        samesite="lax",
        path=oauth.STATE_COOKIE_PATH,
    )
    return resp


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
             db: Session = Depends(get_db)):
    user = _session_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    s = get_settings()
    if not s.google_enabled:
        return _settings_redirect(reason="exchange")

    # 1. The user declined on Google's consent screen. Never echo Google's error text.
    if error:
        return _settings_redirect(reason="denied")

    # 2. State: signed cookie, bound to this session's user, matching the query, used once.
    cookie = oauth.read_state_cookie(request.cookies.get(oauth.STATE_COOKIE, ""))
    if (
        cookie is None
        or not code
        or not state
        or cookie["uid"] != user.id
        or not hmac.compare_digest(cookie["state"].encode(), state.encode())
        or not oauth.consume_state(cookie["state"])
    ):
        log.info("google callback rejected: state check failed user=%s", user.id)
        return _settings_redirect(reason="state")
    # 3. The cookie is deleted on every response from here on (_settings_redirect).

    # 4. Exchange the code.
    client = get_google_client()
    try:
        grant = client.exchange_code(code=code, code_verifier=cookie["verifier"], redirect_uri=s.google_redirect_uri)
    except GoogleError as e:
        log.warning("google code exchange failed user=%s: %s", user.id, type(e).__name__)
        return _settings_redirect(reason="exchange")

    # 5. Every required permission must be granted (users can untick boxes on the consent screen).
    if not set(REQUIRED_SCOPES) <= _normalize_scopes(grant.scopes):
        log.info("google callback rejected: missing scopes user=%s", user.id)
        _best_effort_revoke(grant.access_token)
        return _settings_redirect(reason="scopes")

    # 6. Without a refresh token we cannot sync in the background.
    if not grant.refresh_token:
        log.info("google callback rejected: no refresh token user=%s", user.id)
        _best_effort_revoke(grant.access_token)  # drop the lingering grant so the next consent issues one
        return _settings_redirect(reason="no_refresh_token")

    # 7. Which Gmail address this is.
    try:
        profile = client.gmail_profile(grant.access_token)
    except GoogleError as e:
        log.warning("google profile fetch failed user=%s: %s", user.id, type(e).__name__)
        return _settings_redirect(reason="exchange")

    # 8. Upsert. A reconnect keeps history_id so sync continues where it left off.
    acc = _account(db, user.id)
    if acc is None:
        acc = GoogleAccount(user_id=user.id, history_id=None)
        db.add(acc)
    acc.google_email = profile.email[:255]
    acc.refresh_token_enc = encrypt_token(grant.refresh_token)
    acc.scopes = sorted(_normalize_scopes(grant.scopes))
    acc.status = GoogleAccountStatus.CONNECTED
    acc.last_sync_error = ""
    db.commit()
    oauth.remember_access_token(
        user.id,
        AccessToken(token=grant.access_token,
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=grant.expires_in)),
    )
    log.info("google account connected user=%s", user.id)

    # 9. Start the first sync without waiting for the next cycle.
    mail_sync.wake()
    return _settings_redirect()


@router.post("/disconnect", status_code=204)
def disconnect(body: GoogleDisconnect | None = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    acc = _account(db, user.id)
    if acc is not None:
        # Revoke at Google first, best-effort: the local token is deleted no matter what.
        try:
            refresh_token = decrypt_token(acc.refresh_token_enc)
        except TokenDecryptError:
            refresh_token = None
        if refresh_token:
            _best_effort_revoke(refresh_token)
        db.delete(acc)
    oauth.forget_access_token(user.id)
    if body is not None and body.delete_mail:
        db.execute(delete(Email).where(*_gmail_mail(user.id)))
    db.commit()
    log.info("google account disconnected user=%s delete_mail=%s", user.id, bool(body and body.delete_mail))
    return Response(status_code=204)
