"""Database models. Every business row carries user_id; all queries must filter on it."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FINISHED = "FINISHED"
    STOPPED = "STOPPED"  # safety limit (steps / timeout)
    FAILED = "FAILED"  # LLM / system error


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


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


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(50), default="")
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    customer: Mapped[Customer | None] = relationship()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("user_id", "gmail_thread_id", name="uq_tasks_user_gmail_thread"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING, index=True)
    current_step: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    waiting_for: Mapped[str] = mapped_column(Text, default="")
    # Sub-goals decided by the agent: [{"id": 1, "title": str, "done": bool, "evidence": str}]
    plan: Mapped[list] = mapped_column(JSON, default=list)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="NORMAL")
    origin: Mapped[str] = mapped_column(String(20), default="user")  # user | agent | seed
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    deadline_at: Mapped[datetime | None] = mapped_column(Timestamp)
    deadline_all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_kind: Mapped[str] = mapped_column(String(10), default="none")  # explicit | estimated | none
    deadline_source: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(Timestamp)

    customer: Mapped[Customer | None] = relationship()


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
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (UniqueConstraint("user_id", "gmail_message_id", name="uq_emails_user_gmail_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    sender: Mapped[str] = mapped_column(String(255))
    receiver: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # inbound: seen by the agent
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # Gmail sync fields (null / empty for seed and agent-written mail)
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

    customer: Mapped[Customer | None] = relationship()


class AgentRun(Base):
    """One execution of the agent loop. Messages are persisted so a run can pause and resume."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING, index=True)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    # Tool results already produced in the paused assistant turn (sent together on resume).
    pending_results: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    # Normalized LLM token usage for this run: {"input", "output", "cache_read", "total", "turns"}
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    final_message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    tool_use_id: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(50))
    tool_input: Mapped[dict] = mapped_column(JSON, default=dict)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING, index=True)
    user_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(Timestamp)


class AgentActionLog(Base):
    __tablename__ = "agent_action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    action_type: Mapped[str] = mapped_column(String(40))
    tool_name: Mapped[str] = mapped_column(String(50), default="")
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    output_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(10), default=RiskLevel.LOW)
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, index=True)
