"""보낸 답장 처리 공통과 '상대 답장 없음' 확인.

- mark_reply_sent: 답장을 보낸 업무를 보낸 업무(WAITING)로 옮긴다. 앱 발송과 Gmail에서 직접 보낸 경우가 함께 쓴다.
- check_no_reply: 동기화 주기마다 코드로 확인한다(LLM 없음). 영업일 3일 동안 상대 메일이 없으면 처리할 일로 올린다.
- 영업일은 사용자 시간대 기준 월~금이다. 공휴일은 따지지 않는다.
- mark_reply_sent · answer_no_reply는 커밋하지 않는다. check_no_reply는 커밋한다.
- 타임라인에는 수신자 수 같은 숫자만 남긴다. 주소 · 제목 · 본문은 남기지 않는다.
"""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.audit import log_action
from app.mail import task_actions
from app.mail.errors import ActionError
from app.mail.inbox import _utc
from app.mail.merge import resolve_merged
from app.models import Email, EmailDirection, ReplyDraft, Task, TaskStatus, User

NO_REPLY_BUSINESS_DAYS = 3
NO_REPLY_STEP = "상대 답장 없음 — 다시 연락할까요?"
NO_REPLY_CLOSE_REASON = "상대 답장 없음으로 닫음"
NOT_ASKED_MESSAGE = "다시 연락할지 묻는 업무가 아닙니다."
CHOICES = ("followup", "wait", "close")
SENT_MATCH_WINDOW = timedelta(minutes=10)
_CLOSED = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)


def add_business_days(start: datetime, days: int, tz: str) -> datetime:
    """같은 지역 시각을 유지하며 토 · 일을 건너뛰고 days 영업일 뒤를 돌려준다(UTC).

    시작이 주말이면 다음 월요일 00:00(지역)으로 옮긴 뒤 센다(토 10:00 + 3 → 목 00:00).
    """
    zone = ZoneInfo(tz)
    local = _utc(start).astimezone(zone)
    if local.weekday() >= 5:
        monday = local.date() + timedelta(days=7 - local.weekday())
        local = datetime.combine(monday, time(0, 0), tzinfo=zone)
    day = local.date()
    while days > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days -= 1
    return datetime.combine(day, local.time(), tzinfo=zone).astimezone(UTC)


def _recipient_count(email: Email | None) -> int:
    if email is None:
        return 0
    to = [a for a in str(email.receiver or "").split(",") if a.strip()]
    cc = [a for a in (email.cc or []) if str(a).strip()]
    return len(to) + len(cc)


def mark_reply_sent(db: Session, task: Task, email: Email | None, now: datetime, *, source: str) -> None:
    """답장을 보낸 업무를 보낸 업무로 옮기고 기다림 시계를 시작한다. 닫힌 · 합쳐진 업무는 건드리지 않는다."""
    if str(task.status) in _CLOSED or task.merged_into_id is not None:
        return
    now = _utc(now)
    task.status = TaskStatus.WAITING
    task.waiting_for = "상대 회신"
    task.current_step = "답장 보냄"
    task.reply_sent_at = now
    task.wait_from = now
    task.no_reply_asked_at = None
    task.snoozed_until = None
    task.snooze_returned_at = None
    log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="REPLY_SENT",
               output_data={"source": source, "recipients": _recipient_count(email)},
               reason="앱에서 답장을 보냈습니다" if source == "app" else "Gmail에서 보낸 답장을 확인했습니다")


def _inbound_after(db: Session, task: Task, since: datetime) -> bool:
    """wait_from 뒤에 온 상대 메일이 있는가. 아직 업무에 붙지 않은 같은 스레드 메일도 센다.

    답장은 intake(LLM 스위치 필요)를 거쳐야 task_id가 붙으므로, 업무에 연결된 스레드(합쳐진 업무의 스레드 포함)도 본다.
    """
    at = func.coalesce(Email.received_at, Email.created_at)
    threads = {t for t in db.scalars(select(Email.gmail_thread_id).where(
        Email.user_id == task.user_id, Email.task_id == task.id, Email.gmail_thread_id.is_not(None)))}
    if task.gmail_thread_id:
        threads.add(task.gmail_thread_id)
    for t in db.scalars(select(Task.gmail_thread_id).where(
            Task.user_id == task.user_id, Task.merged_into_id == task.id, Task.gmail_thread_id.is_not(None))):
        threads.add(t)
    match = Email.task_id == task.id
    if threads:
        match = match | Email.gmail_thread_id.in_(threads)
    return db.scalar(
        select(Email.id).where(Email.user_id == task.user_id, match,
                               Email.direction == EmailDirection.INBOUND.value, at > since).limit(1)
    ) is not None


def check_no_reply(db: Session, user_id: int, now: datetime, tz: str) -> int:
    """보낸 지 영업일 3일이 지나도 상대 메일이 없는 업무를 처리할 일로 올린다. 바꾼 수를 돌려주고 커밋한다."""
    now = _utc(now)
    tasks = db.scalars(select(Task).where(
        Task.user_id == user_id, Task.origin == "mail", Task.merged_into_id.is_(None),
        Task.status == TaskStatus.WAITING, Task.wait_from.is_not(None))).all()
    changed = 0
    for task in tasks:
        since = _utc(task.wait_from)
        if add_business_days(since, NO_REPLY_BUSINESS_DAYS, tz) > now or _inbound_after(db, task, since):
            continue
        task.status = TaskStatus.ACTION_REQUIRED
        task.current_step = NO_REPLY_STEP
        task.waiting_for = ""
        task.no_reply_asked_at = now
        log_action(db, user_id=user_id, task_id=task.id, run_id=None, action_type="NO_REPLY_ASKED",
                   output_data={"business_days": NO_REPLY_BUSINESS_DAYS},
                   reason="보낸 지 영업일 3일째 답이 없어 다시 연락할지 묻습니다")
        changed += 1
    if changed:
        db.commit()
    return changed


def answer_no_reply(db: Session, user: User, task: Task, choice: str, now: datetime) -> None:
    """followup은 상태를 그대로 둔다(초안은 답장 초안 API가 만든다). wait은 시계를 다시 시작하고, close는 닫는다."""
    if task.no_reply_asked_at is None or str(task.status) in _CLOSED or task.merged_into_id is not None:
        raise ActionError(NOT_ASKED_MESSAGE, 409)
    if choice not in CHOICES:
        raise ActionError("다시 연락 · 더 기다리기 · 완료로 닫기 중 하나를 고르세요.", 400)
    now = _utc(now)
    if choice == "wait":
        task.status = TaskStatus.WAITING
        task.waiting_for = "상대 회신"
        task.current_step = "답장 보냄"
        task.wait_from = now
        task.no_reply_asked_at = None
        log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="NO_REPLY_WAIT",
                   output_data={}, reason="더 기다리기로 했습니다")
    elif choice == "close":
        task_actions.close_without_reply(db, user, task, NO_REPLY_CLOSE_REASON)
        task.no_reply_asked_at = None


def clear_wait_on_inbound(task: Task) -> None:
    """상대 메일이 오면 '답 없음' 물음을 거둔다."""
    task.no_reply_asked_at = None


def task_for_thread(db: Session, user_id: int, thread_id: str | None) -> Task | None:
    """이 스레드에 메일 업무가 있으면 그 업무(합쳐졌으면 최종 대상)를 돌려준다. 합쳐진 업무는 스레드를 여러 개 가진다."""
    if not thread_id:
        return None
    task = db.scalar(select(Task).where(Task.user_id == user_id, Task.origin == "mail",
                                        Task.gmail_thread_id == thread_id).order_by(Task.id).limit(1))
    if task is None:
        task = db.scalar(select(Task).join(Email, Email.task_id == Task.id).where(
            Task.user_id == user_id, Task.origin == "mail", Email.user_id == user_id,
            Email.gmail_thread_id == thread_id).order_by(Task.id).limit(1))
    return resolve_merged(db, task) if task is not None else None


def _reply_subject(subject: str | None) -> str:
    s = (subject or "").strip()
    while s[:3].lower() == "re:":
        s = s[3:].strip()
    return s


def _addresses(field: str | None) -> set[str]:
    out = set()
    for part in str(field or "").split(","):
        part = part.strip().lower()
        if "<" in part and ">" in part:
            part = part[part.rindex("<") + 1:part.index(">", part.rindex("<"))].strip()
        if part:
            out.add(part)
    return out


def _resolves_draft(draft: ReplyDraft, email: Email) -> bool:
    if draft.gmail_message_id and draft.gmail_message_id == email.gmail_message_id:
        return True
    if draft.sending_at is None:
        return False
    when = _utc(email.received_at or email.sent_at or draft.sending_at)
    start = _utc(draft.sending_at)
    return (start <= when <= start + SENT_MATCH_WINDOW
            and _reply_subject(draft.subject) == _reply_subject(email.subject)
            and (draft.to_addr or "").strip().lower() in _addresses(email.receiver))


def record_sent_mail(db: Session, user_id: int, email: Email, now: datetime) -> None:
    """Gmail에서 보낸 메일(OUTBOUND)을 스레드 업무에 붙이고 답장 보냄으로 옮긴다. 커밋하지 않는다.

    같은 gmail_message_id의 메일이 이미 다른 행으로 저장돼 있으면(앱이 보낸 사본) 업무 상태는 건드리지 않는다.
    """
    task = task_for_thread(db, user_id, email.gmail_thread_id)
    if task is None:
        return
    duplicate = email.gmail_message_id is not None and db.scalar(
        select(Email.id).where(Email.user_id == user_id, Email.gmail_message_id == email.gmail_message_id,
                               Email.id != email.id).limit(1)) is not None
    email.task_id = task.id
    email.processed = True
    email.triage = "sent"
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.user_id == user_id, ReplyDraft.task_id == task.id))
    if draft is not None and draft.status in ("sending", "unknown") and _resolves_draft(draft, email):
        draft.status = "sent"
        draft.gmail_message_id = email.gmail_message_id
    if not duplicate:
        mark_reply_sent(db, task, email, now, source="gmail")
