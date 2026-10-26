from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(ORM):
    id: int
    email: str


class CustomerOut(ORM):
    id: int
    company_name: str
    contact_name: str
    email: str
    phone: str
    meta: dict


class DocumentOut(ORM):
    id: int
    customer_id: int | None
    title: str
    content: str
    document_type: str
    created_at: datetime


class EmailOut(ORM):
    id: int
    customer_id: int | None
    task_id: int | None
    sender: str
    receiver: str
    subject: str
    content: str
    direction: str
    processed: bool
    created_at: datetime
    sent_at: datetime | None


class TaskBrief(ORM):
    id: int
    title: str
    goal: str
    status: str
    current_step: str
    next_action: str
    waiting_for: str
    priority: str
    origin: str
    agent_enabled: bool
    parent_task_id: int | None
    customer_id: int | None
    plan: list
    result_summary: str
    brief: dict
    deadline_at: datetime | None
    deadline_all_day: bool
    deadline_kind: str
    deadline_source: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ActionLogOut(ORM):
    id: int
    run_id: int | None
    action_type: str
    tool_name: str
    input_data: dict
    output_data: dict
    reason: str
    risk_level: str
    status: str
    created_at: datetime


class ApprovalOut(ORM):
    id: int
    task_id: int
    tool_name: str
    tool_input: dict
    reason: str
    status: str
    user_comment: str
    created_at: datetime
    decided_at: datetime | None
    email: EmailOut | None = None


class RunOut(ORM):
    id: int
    trigger: str
    status: str
    steps: int
    usage: dict
    final_message: str
    error: str
    created_at: datetime
    finished_at: datetime | None


class TaskDetail(BaseModel):
    task: TaskBrief
    customer: CustomerOut | None
    parent: TaskBrief | None
    followups: list[TaskBrief]
    emails: list[EmailOut]
    logs: list[ActionLogOut]
    approvals: list[ApprovalOut]
    runs: list[RunOut]
    active_run: RunOut | None
    max_steps: int
    can_simulate_reply: bool


class GoalIn(BaseModel):
    goal: str = Field(min_length=5, max_length=1000)
    title: str | None = Field(default=None, max_length=120)


class CancelTaskIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class GoalUpdateIn(BaseModel):
    goal: str = Field(min_length=5, max_length=1000)


class CompleteTaskIn(BaseModel):
    note: str = Field(default="", max_length=1500)


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=500)
    # Optional rewrite of the draft, applied only when approving. The recipient is never user-editable.
    subject: str | None = Field(default=None, min_length=2, max_length=200)
    body: str | None = Field(default=None, min_length=10, max_length=5000)


class SimulateReplyIn(BaseModel):
    preset: Literal["delivery", "accept", "injection", "custom"] = "delivery"
    content: str = Field(default="", max_length=2000)


class DashboardOut(BaseModel):
    counts: dict[str, int]
    tasks: list[TaskBrief]
    pending_approvals: int
    agent_configured: bool
    model: str


class RecentMailOut(ORM):
    subject: str
    sender: str
    received_at: datetime | None


class GoogleStatusOut(BaseModel):
    enabled: bool
    status: Literal["disconnected", "connected", "reauth_required"]
    google_email: str | None = None
    scopes: list[str] = []
    last_synced_at: datetime | None = None
    last_sync_error: str = ""
    mail_count: int = 0
    recent: list[RecentMailOut] = []


class GoogleDisconnect(BaseModel):
    delete_mail: bool = False
