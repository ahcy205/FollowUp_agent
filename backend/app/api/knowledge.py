"""자료실 API: 목록 · 상세 · 올리기 · 공유 등급 · 지우기 · 검색 시험."""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.knowledge import search, store
from app.knowledge.draft_sources import _cites
from app.mail.errors import ActionError
from app.models import KnowledgeChunk, KnowledgeSource, ReplyDraft, Task, User
from app.schemas import (
    KnowledgeChunkOut,
    KnowledgeHitOut,
    KnowledgeReferenceOut,
    KnowledgeSearchIn,
    KnowledgeSearchOut,
    KnowledgeSourceDetailOut,
    KnowledgeSourceOut,
    VisibilityIn,
)
from app.security import get_current_user, knowledge_search_limiter, upload_limiter

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
log = logging.getLogger(__name__)

OFF_MESSAGE = "자료 기능이 꺼져 있습니다."
UPLOAD_FAILED = "파일을 처리하지 못했습니다. 잠시 후 다시 올려 주세요."
HIT_LINES = 5  # 검색 시험 결과에 보여 줄 덩어리 앞부분 줄 수


def _require_enabled() -> None:
    if not get_settings().knowledge_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, OFF_MESSAGE)


def _counts(db: Session, source_ids: list[int]) -> dict[int, tuple[int, int]]:
    if not source_ids:
        return {}
    rows = db.execute(
        select(KnowledgeChunk.source_id, func.count(), func.coalesce(func.sum(KnowledgeChunk.flagged.cast(Integer)), 0))
        .where(KnowledgeChunk.source_id.in_(source_ids))
        .group_by(KnowledgeChunk.source_id)
    ).all()
    return {sid: (int(n), int(f)) for sid, n, f in rows}


def _out(source: KnowledgeSource, counts: dict[int, tuple[int, int]]) -> KnowledgeSourceOut:
    out = KnowledgeSourceOut.model_validate(source)
    out.chunk_count, out.flagged_count = counts.get(source.id, (0, 0))
    return out


def _basename(name: str) -> str:
    return (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


@router.get("/sources")
def list_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sources = store.list_sources(db, user.id)
    counts = _counts(db, [s.id for s in sources])
    return {"enabled": get_settings().knowledge_enabled, "sources": [_out(s, counts) for s in sources]}


@router.post("/sources")
def upload_sources(
    files: list[UploadFile] = File(...),
    visibility: str = Form("internal"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_enabled()
    upload_limiter.check(f"upload:{user.id}")
    cap = get_settings().knowledge_max_file_mb * 1024 * 1024 + 1  # 한도보다 1바이트 더 읽어 초과만 알아낸다
    results = []
    for f in files:
        name = _basename(f.filename or "")
        entry = {"filename": name, "ok": False, "error": "", "source": None}
        try:
            data = f.file.read(cap)  # 일반 def: FastAPI가 스레드에서 돌려 파일 읽기 · 추출이 이벤트 루프를 막지 않는다
            source = store.add_document(db, user, name, data, visibility)
            db.commit()
            entry["source"] = _out(source, _counts(db, [source.id]))
            entry["ok"] = True
        except ActionError as e:
            db.rollback()
            entry["error"] = str(e)
        except IntegrityError:
            db.rollback()
            entry["error"] = "이미 올린 자료입니다."
        except Exception as e:  # noqa: BLE001 - 마지막 안전망: 이 파일만 실패로 알리고 다음 파일을 계속 올린다(설계 3.3)
            db.rollback()
            log.warning("knowledge upload: file failed: %s", type(e).__name__)
            entry.update(ok=False, error=UPLOAD_FAILED, source=None)
        results.append(entry)
    return {"results": results}


def _get(db: Session, user: User, source_id: int) -> KnowledgeSource:
    try:
        return store.get_source(db, user.id, source_id)
    except ActionError as e:
        raise HTTPException(e.status, str(e))


@router.post("/sources/{source_id}/visibility", response_model=KnowledgeSourceOut)
def change_visibility(source_id: int, body: VisibilityIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    _require_enabled()
    source = _get(db, user, source_id)
    try:
        store.set_visibility(db, source, body.visibility)
    except ActionError as e:
        raise HTTPException(e.status, str(e))
    db.commit()
    db.refresh(source)
    return _out(source, _counts(db, [source.id]))


@router.post("/sources/{source_id}/delete")
def delete_source(source_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source = _get(db, user, source_id)
    store.delete_source(db, source)
    db.commit()
    return {"ok": True}


@router.get("/sources/{source_id}", response_model=KnowledgeSourceDetailOut)
def source_detail(source_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """자료 정보 + 덩어리 + 이 자료를 참고한 내 초안. 스위치가 꺼져도 볼 수 있다(목록과 같음)."""
    source = _get(db, user, source_id)
    chunks = db.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.source_id == source.id, KnowledgeChunk.user_id == user.id)
        .order_by(KnowledgeChunk.ordinal)
    ).all()
    # sources는 JSON이라 내 초안을 한 번에 읽어 파이썬에서 거른다(개인 규모, purge_source_from_drafts와 같음).
    drafts = db.execute(
        select(ReplyDraft, Task.title)
        .join(Task, Task.id == ReplyDraft.task_id)
        .where(ReplyDraft.user_id == user.id, Task.user_id == user.id)
        .order_by(ReplyDraft.updated_at.desc(), ReplyDraft.id.desc())
    ).all()
    base = _out(source, _counts(db, [source.id]))
    return KnowledgeSourceDetailOut(
        **base.model_dump(),
        chunks=[KnowledgeChunkOut(ordinal=c.ordinal, lines=[str(x) for x in (c.lines or [])], flagged=bool(c.flagged))
                for c in chunks],
        referenced_by=[KnowledgeReferenceOut(task_id=d.task_id, task_title=title, draft_status=d.status,
                                             updated_at=d.updated_at)
                       for d, title in drafts if _cites(d.sources, source.id)],
    )


@router.post("/search", response_model=KnowledgeSearchOut)
def search_preview(body: KnowledgeSearchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """답장 초안과 같은 검색(답장에 써도 되는 자료 · 의심 없는 덩어리)을 미리 본다.
    검색어가 주소 · 접속 기록에 남지 않게 POST로 받는다. 검색어는 로그에 남기지 않는다."""
    _require_enabled()
    knowledge_search_limiter.check(f"ksearch:{user.id}")
    hits = search.search_shareable(db, user.id, body.q)
    return KnowledgeSearchOut(hits=[
        KnowledgeHitOut(source_id=h.source_id, title=h.title, chunk_ordinal=h.ordinal, score=round(h.score, 3),
                        lines=[str(x) for x in h.lines[:HIT_LINES]])
        for h in hits
    ])
