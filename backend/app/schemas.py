from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(ORM):
    id: int
    email: str
    demo_mode: bool = False


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
    calendar_state: str
    calendar_event_url: str
    calendar_error: str
    snoozed_until: datetime | None
    priority_source: str
    merged_into_id: int | None
    change_log: list = []  # 상대 재답장으로 바뀐 내용 (설계 6.4)
    changed_unseen: bool = False  # "내용 바뀜" 표시: 상세를 열 때 POST /seen으로 끈다
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


class MergeCandidateOut(BaseModel):
    task_id: int
    title: str
    evidence: str | None = None  # "수정 요청으로 보임" 판단의 근거 문장(설계 7.3). 5단계 규칙 후보는 None


class UndoableMergeOut(BaseModel):
    source_id: int
    title: str


class MergedSourceOut(BaseModel):
    task_id: int
    title: str
    summary: str
    merged_at: datetime | None


class ReplyDraftOut(BaseModel):
    purpose: str
    to_addr: str
    cc_candidates: list[str]
    cc_selected: list[str]
    subject: str
    body: str
    status: str
    warnings: dict
    can_send: bool  # 빈칸이 없고 상태가 draft일 때만
    unknown_since: datetime | None  # 발송 결과가 불분명해진 시각 (status == "unknown"일 때만)
    updated_at: datetime  # 만들기 · 고치기 · 초안으로 되돌리기 시각. reply_sent_at이 더 늦으면 이미 답장을 보낸 것


class MailExtras(BaseModel):
    is_snoozed: bool
    wake_at: datetime | None
    returned: bool
    nudge_days: int | None
    merge_candidates: list[MergeCandidateOut]
    undoable_merges: list[UndoableMergeOut]
    merged_sources: list[MergedSourceOut]
    merged_into_id: int | None
    deadline_badge: dict
    priority_source: str
    no_reply: bool = False
    reply_sent_at: datetime | None = None
    draft: ReplyDraftOut | None = None
    remaining_todos_prompt: bool = False  # 답장을 보냈는데(WAITING) 남은 할 일이 있다: "모두 완료" 안내
    merge_analyzing: bool = False  # 합친 내용을 분석하는 중(설계 7.3)


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
    mail: MailExtras | None = None


class GoalIn(BaseModel):
    goal: str = Field(min_length=5, max_length=1000)
    title: str | None = Field(default=None, max_length=120)


class CancelTaskIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class GoalUpdateIn(BaseModel):
    goal: str = Field(min_length=5, max_length=1000)


class CompleteTaskIn(BaseModel):
    note: str = Field(default="", max_length=1500)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnoozeIn(_Strict):
    preset: str | None = Field(default=None, max_length=40)  # 알 수 없는 값은 task_actions가 한국어 400으로
    until: datetime | None = None


class PriorityIn(_Strict):
    priority: Literal["HIGH", "NORMAL", "LOW"]


class DeadlineIn(_Strict):
    date: str | None = Field(default=None, max_length=20)
    time: str | None = Field(default=None, max_length=20)  # HH:MM 또는 HH:MM:SS, 검증은 task_actions
    clear: bool = False
    confirm: bool = False


class TodoItemIn(_Strict):
    id: int
    title: str = Field(max_length=2000)
    done: bool = False
    evidence: str = Field(default="", max_length=2000)
    due_at: str | None = Field(default=None, max_length=40)
    source: Literal["agent", "user"] = "user"
    due_note: str | None = Field(default=None, max_length=2000)
    merged_from: int | None = None  # 합쳐 온 할 일 표시. 지웠다 되살려도 합치기 되돌리기가 원래 업무로 보낸다


class TodoOpIn(_Strict):
    """할 일 작업 하나. 길이 · 필수 항목 검사는 task_actions가 한국어 문구로 한다."""
    op: Literal["add", "toggle", "memo", "delete", "restore"]
    id: int | None = None
    title: str | None = Field(default=None, max_length=2000)
    done: bool | None = None
    memo: str | None = Field(default=None, max_length=2000)
    item: TodoItemIn | None = None
    index: int | None = None


class CloseTaskIn(_Strict):
    reason: str = Field(default="", max_length=500)


class NoReplyIn(_Strict):
    choice: Literal["followup", "wait", "close"]


class ReplyConfirmIn(_Strict):
    """발송 결과가 불분명한 초안: 보낸편지함에서 확인한 결과."""
    sent: StrictBool


class ReplyDraftIn(_Strict):
    purpose: Literal["reply", "followup"]
    user_input: str = Field(default="", max_length=2000)


class ReplyDraftUpdateIn(_Strict):
    """길이 · 빈 값 검사는 reply_draft가 한국어 문구로 한다."""
    subject: str = Field(max_length=1000)
    body: str = Field(max_length=20000)
    cc_selected: list[str] = Field(default_factory=list, max_length=20)


class MergeIn(_Strict):
    into_task_id: int


class MergeUndoIn(_Strict):
    source_id: int


class PromoteOut(BaseModel):
    task_id: int


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
    triage: str | None = None
    triage_reason: str = ""
    task_id: int | None = None


class GoogleStatusOut(BaseModel):
    enabled: bool
    status: Literal["disconnected", "connected", "reauth_required"]
    google_email: str | None = None
    scopes: list[str] = []
    last_synced_at: datetime | None = None
    last_sync_error: str = ""
    mail_count: int = 0
    recent: list[RecentMailOut] = []
    triage_counts: dict[str, int] = {}
    triage_enabled: bool = False


class GoogleDisconnect(BaseModel):
    delete_mail: bool = False


class InboxCard(BaseModel):
    task_id: int
    title: str
    summary: str
    sender_name: str
    sender_org: str
    priority: str
    deadline_badge: dict
    todo_done: int
    todo_total: int
    nudge_days: int | None
    returned: bool
    has_merge_candidates: bool
    analysis: str
    received_at: datetime | None
    no_reply: bool = False  # 보낸 지 영업일 3일째 답이 없어 다시 연락할지 묻는 중
    changed_unseen: bool = False  # 상대 재답장으로 바뀐 내용을 아직 보지 않음("내용 바뀜")


class SnoozedCard(InboxCard):
    wake_at: datetime


class InboxCounts(BaseModel):
    now: int
    week: int
    later: int
    todo: int
    sent: int
    reference: int
    done: int


class InboxBuckets(BaseModel):
    now: list[InboxCard]
    week: list[InboxCard]
    later: list[InboxCard]


class InboxTabs(BaseModel):
    todo: InboxBuckets
    sent: list[InboxCard]
    reference: list[InboxCard]
    done: list[InboxCard]


class NotWorkMail(BaseModel):
    email_id: int
    sender: str
    subject: str
    triage: str
    triage_reason: str
    received_at: datetime | None


class InboxOut(BaseModel):
    counts: InboxCounts
    tabs: InboxTabs
    snoozed: list[SnoozedCard]
    not_work: list[NotWorkMail]
    triage_enabled: bool


class InboxAlert(BaseModel):
    key: str
    kind: str
    task_id: int
    text: str


class InboxAlertsOut(BaseModel):
    now_count: int
    alerts: list[InboxAlert]
