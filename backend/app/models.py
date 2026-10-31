"""Database models. Every business row carries user_id; all queries must filter on it."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"  # closed by the user; history is kept, nothing can restart it


class EmailDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    DRAFT = "DRAFT"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GoogleAccountStatus(StrEnum):
    CONNECTED = "connected"
    REAUTH_REQUIRED = "reauth_required"
    # No "disconnected": disconnecting deletes the row; the status API reports a missing row as disconnected.


Timestamp = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("user_id", "gmail_thread_id", name="uq_tasks_user_gmail_thread"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING, index=True)
    current_step: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    waiting_for: Mapped[str] = mapped_column(Text, default="")
    # 할 일 목록: [{"id": 1, "title": str, "done": bool, "evidence": str, "source": "agent" | "user", ...}]
    plan: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(10), default="NORMAL")
    origin: Mapped[str] = mapped_column(String(20), default="mail")  # 메일 업무만 있다(예전 PoC 값 user · agent · seed는 지웠다)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    deadline_at: Mapped[datetime | None] = mapped_column(Timestamp)
    deadline_all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_kind: Mapped[str] = mapped_column(String(10), default="none")  # explicit | estimated | none
    deadline_source: Mapped[str] = mapped_column(Text, default="")
    calendar_event_id: Mapped[str | None] = mapped_column(String(64))
    calendar_event_url: Mapped[str] = mapped_column(Text, default="")
    # none | pending | synced | deleted_by_user | error
    calendar_state: Mapped[str] = mapped_column(String(20), default="none", index=True)
    calendar_error: Mapped[str] = mapped_column(Text, default="")
    calendar_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 받은 업무 화면: 스누즈 · 중요도 출처(llm | user) · 합치기
    snoozed_until: Mapped[datetime | None] = mapped_column(Timestamp)
    snooze_returned_at: Mapped[datetime | None] = mapped_column(Timestamp)
    priority_source: Mapped[str] = mapped_column(String(10), default="llm")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    merge_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    merge_undo: Mapped[dict] = mapped_column(JSON, default=dict)
    # 6단계 답장과 수명주기
    reply_sent_at: Mapped[datetime | None] = mapped_column(Timestamp)
    wait_from: Mapped[datetime | None] = mapped_column(Timestamp)  # 상대 답장 없음 계산 기준(보낼 때 · 더 기다리기 때 now)
    no_reply_asked_at: Mapped[datetime | None] = mapped_column(Timestamp)
    change_log: Mapped[list] = mapped_column(JSON, default=list)
    changed_unseen: Mapped[bool] = mapped_column(Boolean, default=False)
    related: Mapped[dict] = mapped_column(JSON, default=dict)
    merge_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(Timestamp)


class ReplyDraft(Base):
    __tablename__ = "reply_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True)
    purpose: Mapped[str] = mapped_column(String(10), default="reply")  # reply | followup
    to_addr: Mapped[str] = mapped_column(String(255))
    cc_candidates: Mapped[list] = mapped_column(JSON, default=list)
    cc_selected: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | sending | sent | unknown
    warnings: Mapped[dict] = mapped_column(JSON, default=dict)
    user_input: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    gmail_message_id: Mapped[str | None] = mapped_column(String(64))
    sending_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_knowledge_sources_user_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="document")
    title: Mapped[str] = mapped_column(String(300))
    file_type: Mapped[str] = mapped_column(String(10))
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(String(10), default="internal")
    status: Mapped[str] = mapped_column(String(10))  # indexed | flagged | failed
    error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    lines: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)


class GoogleAccount(Base):
    __tablename__ = "google_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    google_email: Mapped[str] = mapped_column(String(255))
    refresh_token_enc: Mapped[str] = mapped_column(Text)  # AES-GCM encrypted; never stored in plain text
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    history_id: Mapped[str | None] = mapped_column(String(40))
    last_synced_at: Mapped[datetime | None] = mapped_column(Timestamp)
    last_sync_error: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=GoogleAccountStatus.CONNECTED)
    sync_lease_until: Mapped[datetime | None] = mapped_column(Timestamp)
    # 캘린더 일정 번호에 섞는 무작위 값 (base32hex 8자). 다른 DB · 다른 인스턴스와 번호가 겹치지 않게 한다.
    calendar_id_salt: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (UniqueConstraint("user_id", "gmail_message_id", name="uq_emails_user_gmail_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    sender: Mapped[str] = mapped_column(String(255))
    receiver: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # inbound: seen by the agent
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # Gmail sync fields
    gmail_message_id: Mapped[str | None] = mapped_column(String(64))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(500))
    cc: Mapped[list] = mapped_column(JSON, default=list)
    received_at: Mapped[datetime | None] = mapped_column(Timestamp)
    triage: Mapped[str | None] = mapped_column(String(20), index=True)
    triage_reason: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    # Values the rule filter needs: labels, list_unsubscribe, precedence, sender_name. Not raw headers.
    gmail_meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentActionLog(Base):
    __tablename__ = "agent_action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    tool_name: Mapped[str] = mapped_column(String(50), default="")
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    output_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(10), default=RiskLevel.LOW)
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, index=True)
