"""Single boundary for every Google call (OAuth + Gmail). Tests swap in FakeGoogle via set_google_override."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TIMEOUT_SECONDS = 20

REQUIRED_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
)


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: list[str]


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class GmailProfile:
    email: str
    history_id: str


@dataclass(frozen=True)
class IdPage:
    message_ids: list[str]
    next_page_token: str | None
    history_id: str | None = None


@dataclass(frozen=True)
class RawMessage:
    id: str
    thread_id: str
    label_ids: list[str]
    internal_date_ms: int
    raw: bytes


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    html_link: str
    status: str  # "confirmed" | "tentative" | "cancelled"


class GoogleError(Exception):
    """Base class for Google call failures."""


class GoogleAuthError(GoogleError):
    """invalid_grant / 401: the user must reconnect."""


class GoogleHistoryGone(GoogleError):
    """history.list returned 404: the start history id is too old."""


class GoogleTransientError(GoogleError):
    """429, 5xx, or network failure: retry later."""


class GoogleNotFound(GoogleError):
    """404 / 410: the requested resource (e.g. a calendar event) does not exist."""


class GoogleConflict(GoogleError):
    """409: the resource already exists (e.g. a calendar event id is taken)."""


class GoogleClient(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...
    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenGrant: ...
    def refresh_access_token(self, refresh_token: str) -> AccessToken: ...
    def revoke(self, token: str) -> None: ...
    def gmail_profile(self, access_token: str) -> GmailProfile: ...
    def gmail_history(self, access_token: str, start_history_id: str, page_token: str | None) -> IdPage: ...
    def gmail_list(self, access_token: str, query: str, page_token: str | None) -> IdPage: ...
    def gmail_get_raw(self, access_token: str, message_id: str) -> RawMessage: ...
    def calendar_insert(self, access_token: str, body: dict) -> CalendarEvent: ...
    def calendar_get(self, access_token: str, event_id: str) -> CalendarEvent: ...
    def calendar_patch(self, access_token: str, event_id: str, body: dict) -> CalendarEvent: ...


_TRANSIENT_403_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "backendError"})


def _is_rate_limited(resp: httpx.Response) -> bool:
    """Gmail answers quota and backend hiccups with 403 too; those must be retried, not treated as final.

    Any body we cannot read as Google's JSON error shape counts as a real (non-transient) 403.
    """
    try:
        err = resp.json().get("error")
        if not isinstance(err, dict):
            return False
        if err.get("status") == "RESOURCE_EXHAUSTED":
            return True
        details = err.get("errors") or []
        return isinstance(details, list) and any(
            isinstance(d, dict) and d.get("reason") in _TRANSIENT_403_REASONS for d in details)
    except (ValueError, AttributeError, TypeError):
        return False


class HttpGoogleClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)

    def _send(self, method: str, url: str, *, history: bool = False, **kwargs) -> httpx.Response:
        try:
            resp = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise GoogleTransientError(f"Google 연결 오류: {type(e).__name__}") from e
        s = resp.status_code
        if s < 400:
            return resp
        if s == 429 or s >= 500:
            raise GoogleTransientError(f"Google 일시 오류({s})")
        if s == 401:
            raise GoogleAuthError("Google 인증이 만료되었습니다.")
        if s == 404 and history:
            raise GoogleHistoryGone("Gmail 기록 지점이 만료되었습니다.")
        if s == 403 and _is_rate_limited(resp):
            raise GoogleTransientError("Google 사용량 한도 일시 초과(403)")
        try:
            err = resp.json().get("error")
        except (ValueError, AttributeError):
            err = None
        if err == "invalid_grant":
            raise GoogleAuthError("Google 인증이 취소되었거나 만료되었습니다.")
        if s in (404, 410):
            raise GoogleNotFound(f"Google 대상을 찾을 수 없습니다({s})")
        if s == 409:
            raise GoogleConflict("Google 대상이 이미 있습니다(409)")
        raise GoogleError(f"Google 요청 실패({s})")

    def _gmail(self, access_token: str, path: str, params: dict | None = None, *, history: bool = False) -> dict:
        resp = self._send("GET", f"{GMAIL_BASE}/{path}", params=params,
                          headers={"Authorization": f"Bearer {access_token}"}, history=history)
        return resp.json()

    # --- OAuth ---
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": get_settings().google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def _token_request(self, data: dict) -> dict:
        s = get_settings()
        body = {"client_id": s.google_client_id, "client_secret": s.google_client_secret, **data}
        return self._send("POST", TOKEN_URL, data=body).json()

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenGrant:
        d = self._token_request({"grant_type": "authorization_code", "code": code,
                                 "code_verifier": code_verifier, "redirect_uri": redirect_uri})
        return TokenGrant(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
                          expires_in=int(d.get("expires_in", 3600)), scopes=(d.get("scope") or "").split())

    def refresh_access_token(self, refresh_token: str) -> AccessToken:
        d = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return AccessToken(token=d["access_token"],
                           expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600))))

    def revoke(self, token: str) -> None:
        self._send("POST", REVOKE_URL, data={"token": token})

    # --- Gmail ---
    def gmail_profile(self, access_token: str) -> GmailProfile:
        d = self._gmail(access_token, "profile")
        return GmailProfile(email=d["emailAddress"], history_id=str(d["historyId"]))

    def gmail_history(self, access_token: str, start_history_id: str, page_token: str | None) -> IdPage:
        params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded"}
        if page_token:
            params["pageToken"] = page_token
        d = self._gmail(access_token, "history", params, history=True)
        ids: list[str] = []
        for h in d.get("history", []):
            for added in h.get("messagesAdded", []):
                mid = added.get("message", {}).get("id")
                if mid and mid not in ids:
                    ids.append(mid)
        hid = d.get("historyId")
        return IdPage(message_ids=ids, next_page_token=d.get("nextPageToken"),
                      history_id=str(hid) if hid is not None else None)

    def gmail_list(self, access_token: str, query: str, page_token: str | None) -> IdPage:
        params = {"q": query}
        if page_token:
            params["pageToken"] = page_token
        d = self._gmail(access_token, "messages", params)
        return IdPage(message_ids=[m["id"] for m in d.get("messages", [])],
                      next_page_token=d.get("nextPageToken"))

    def gmail_get_raw(self, access_token: str, message_id: str) -> RawMessage:
        d = self._gmail(access_token, f"messages/{message_id}", {"format": "raw"})
        raw = d.get("raw", "")
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        return RawMessage(id=d["id"], thread_id=d.get("threadId", d["id"]), label_ids=list(d.get("labelIds", [])),
                          internal_date_ms=int(d.get("internalDate", 0)), raw=data)

    # --- Calendar ---
    def _calendar(self, method: str, access_token: str, event_id: str | None = None,
                  body: dict | None = None) -> CalendarEvent:
        url = CALENDAR_EVENTS_URL if event_id is None else f"{CALENDAR_EVENTS_URL}/{event_id}"
        kwargs: dict = {"headers": {"Authorization": f"Bearer {access_token}"}}
        if body is not None:
            kwargs["json"] = body
        d = self._send(method, url, **kwargs).json()
        return CalendarEvent(id=d["id"], html_link=d.get("htmlLink", ""), status=d.get("status", "confirmed"))

    def calendar_insert(self, access_token: str, body: dict) -> CalendarEvent:
        return self._calendar("POST", access_token, body=body)

    def calendar_get(self, access_token: str, event_id: str) -> CalendarEvent:
        return self._calendar("GET", access_token, event_id)

    def calendar_patch(self, access_token: str, event_id: str, body: dict) -> CalendarEvent:
        return self._calendar("PATCH", access_token, event_id, body)


_override: GoogleClient | None = None
_default: HttpGoogleClient | None = None


def set_google_override(client: GoogleClient | None) -> None:
    """Tests inject FakeGoogle here."""
    global _override
    _override = client


def get_google_client() -> GoogleClient:
    global _default
    if _override is not None:
        return _override
    if _default is None:
        _default = HttpGoogleClient()
    return _default
