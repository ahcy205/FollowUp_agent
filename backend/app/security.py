"""Authentication, session cookies, CSRF protection and simple rate limiting."""

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings
from app.db import get_db
from app.models import User

COOKIE_NAME = "agent_session"
JWT_ALG = "HS256"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=s.session_hours)}
    return jwt.encode(payload, s.secret_key, algorithm=JWT_ALG)


def set_session_cookie(response, user_id: int) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user_id),
        max_age=s.session_hours * 3600,
        httponly=True,
        secure=s.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[JWT_ALG])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "세션이 만료되었습니다. 다시 로그인하세요.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자를 찾을 수 없습니다.")
    return user


class CSRFMiddleware(BaseHTTPMiddleware):
    """State-changing API calls must come from our own origin and carry a custom header.

    A cross-site form or image cannot set custom headers, and a cross-site fetch with one
    triggers a CORS preflight that we never approve.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in get_settings().allowed_origins:
                return JSONResponse({"detail": "허용되지 않은 출처의 요청입니다."}, status_code=403)
            if request.headers.get("x-requested-with") != "XMLHttpRequest":
                return JSONResponse({"detail": "CSRF 검증에 실패했습니다."}, status_code=403)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if get_settings().is_production:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        return response


class RateLimiter:
    """In-memory sliding window limiter (single-instance PoC)."""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.max_calls:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter(max_calls=10, window_seconds=300)
agent_limiter = RateLimiter(max_calls=30, window_seconds=600)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
