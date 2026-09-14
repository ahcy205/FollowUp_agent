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


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=500)


class SimulateReplyIn(BaseModel):
    preset: Literal["delivery", "accept", "injection", "custom"] = "delivery"
    content: str = Field(default="", max_length=2000)


class DashboardOut(BaseModel):
    counts: dict[str, int]
    tasks: list[TaskBrief]
    pending_approvals: int
    agent_configured: bool
    model: str
