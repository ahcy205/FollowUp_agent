"""자료 저장: 올리기 · 목록 · 공유 등급 · 지우기. 커밋은 호출하는 쪽이 한다."""
import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.audit import log_action
from app.agent.guard import detect_injection
from app.config import get_settings
from app.knowledge.chunking import chunk_blocks
from app.knowledge.draft_sources import purge_source_from_drafts, refresh_internal_warnings
from app.knowledge.extract import SUPPORTED, ExtractError, extract_text
from app.mail.errors import ActionError
from app.models import KnowledgeChunk, KnowledgeSource, User

VISIBILITIES = ("shareable", "internal")


def _check_visibility(visibility: str) -> None:
    if visibility not in VISIBILITIES:
        raise ActionError("공유 등급이 올바르지 않습니다. 공유 가능 또는 내부용 중에서 골라 주세요.", 400)


def add_document(db: Session, user: User, filename: str, data: bytes, visibility: str) -> KnowledgeSource:
    settings = get_settings()
    _check_visibility(visibility)
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if file_type not in SUPPORTED:
        raise ActionError("지원하지 않는 형식입니다. 엑셀 · CSV · 텍스트 · PDF · 워드 파일을 올려 주세요.", 400)
    if len(data) > settings.knowledge_max_file_mb * 1024 * 1024:
        raise ActionError(f"파일이 너무 큽니다. {settings.knowledge_max_file_mb}MB 이하로 올려 주세요.", 400)
    count = db.scalar(select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.user_id == user.id))
    if count >= settings.knowledge_max_sources:
        raise ActionError(
            f"자료는 {settings.knowledge_max_sources}개까지 올릴 수 있습니다. 쓰지 않는 자료를 지운 뒤 올려 주세요.",
            409,
        )
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(KnowledgeSource).where(KnowledgeSource.user_id == user.id, KnowledgeSource.content_hash == digest)
    )
    if existing:
        raise ActionError(f"이미 올린 자료입니다({existing.title}).", 409)

    source = KnowledgeSource(
        user_id=user.id,
        title=filename[:300],
        file_type=file_type,
        size=len(data),
        content_hash=digest,
        visibility=visibility,
        status="indexed",
    )
    chunks = []
    try:
        extracted = extract_text(file_type, data)
        source.notes = dict(extracted.notes)
        chunks = chunk_blocks(extracted.blocks)
    except ExtractError as e:
        source.status = "failed"
        source.error = str(e)
    db.add(source)
    db.flush()

    flagged = 0
    for i, c in enumerate(chunks):
        bad = bool(detect_injection(c.text))
        flagged += bad
        db.add(
            KnowledgeChunk(
                source_id=source.id,
                user_id=user.id,
                ordinal=i,
                text=c.text,
                lines=list(c.lines),
                content_hash=hashlib.sha256(c.text.encode("utf-8")).hexdigest(),
                flagged=bad,
            )
        )
    if source.status != "failed" and flagged:
        source.status = "flagged"
    log_action(
        db,
        user_id=user.id,
        task_id=None,
        run_id=None,
        action_type="KNOWLEDGE_ADDED",
        output_data={"source_id": source.id, "chunks": len(chunks), "flagged": flagged, "status": source.status},
        reason="자료를 올렸습니다",
    )
    return source


def list_sources(db: Session, user_id: int) -> list[KnowledgeSource]:
    return list(
        db.scalars(
            select(KnowledgeSource)
            .where(KnowledgeSource.user_id == user_id)
            .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc())
        )
    )


def get_source(db: Session, user_id: int, source_id: int) -> KnowledgeSource:
    source = db.scalar(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id, KnowledgeSource.user_id == user_id)
    )
    if source is None:
        raise ActionError("자료를 찾을 수 없습니다.", 404)
    return source


def set_visibility(db: Session, source: KnowledgeSource, visibility: str) -> None:
    _check_visibility(visibility)
    was = source.visibility
    source.visibility = visibility
    if was == "shareable" and visibility == "internal":
        purge_source_from_drafts(db, source.user_id, source.id)
    elif was == "internal" and visibility == "shareable":
        refresh_internal_warnings(db, source.user_id, source.id)  # 그 값은 이제 내부용이 아니다


def delete_source(db: Session, source: KnowledgeSource) -> None:
    user_id, source_id = source.user_id, source.id
    db.execute(KnowledgeChunk.__table__.delete().where(KnowledgeChunk.source_id == source_id))
    db.delete(source)
    # 지운 뒤에 걷어낸다: 경고를 다시 계산할 때 지운 자료가 내부용 값 색인에 남아 있으면 안 된다
    purge_source_from_drafts(db, user_id, source_id)
