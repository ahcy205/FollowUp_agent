import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.agent.orchestrator import recover_stale_runs
from app.api import auth, demo, tasks
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import User
from app.security import CSRFMiddleware, SecurityHeadersMiddleware, hash_password, verify_password
from app.seed import seed_user_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _ensure_demo_user() -> None:
    s = get_settings()
    if not s.demo_user_password:
        return
    with SessionLocal() as db:
        email = s.demo_user_email.lower()
        existing = db.scalar(select(User).where(func.lower(User.email) == email))
        if existing is not None:
            if not verify_password(s.demo_user_password, existing.password_hash):
                existing.password_hash = hash_password(s.demo_user_password)
                db.commit()
                log.info("demo user password refreshed: %s", email)
            return
        user = User(email=email, password_hash=hash_password(s.demo_user_password))
        db.add(user)
        db.flush()
        seed_user_data(db, user)
        db.commit()
        log.info("demo user created: %s", email)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)  # local dev convenience; production uses Alembic migrations
    recover_stale_runs()
    _ensure_demo_user()
    log.info("started env=%s model=%s llm_configured=%s", s.app_env, s.llm_model, bool(s.llm_api_key))
    yield


settings = get_settings()
app = FastAPI(
    title="AI 업무 후속조치 Agent",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
if settings.is_production:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(demo.router)


@app.get("/api/health", include_in_schema=False)
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return JSONResponse({"detail": "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도하세요."}, status_code=500)


# ---- Single-page app (built React bundle) served from the same origin
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
