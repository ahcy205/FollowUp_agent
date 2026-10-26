"""받은 업무 화면 API: 목록과 브라우저 알림. 읽기 전용이다."""
from datetime import datetime, timedelta
from email.utils import parseaddr

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.mail import inbox
from app.models import Email, EmailDirection, Task, User
from app.schemas import (
    InboxAlertsOut,
    InboxBuckets,
    InboxCard,
    InboxCounts,
    InboxOut,
    InboxTabs,
    NotWorkMail,
    SnoozedCard,
)
from app.security import get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

RECENT = timedelta(days=30)
LIST_MAX = 50


def _inbound_times(db: Session, user_id: int, task_ids: list[int]) -> dict[int, dict]:
    """업무별 가장 최근 상대 메일(번호, 시각)과 가장 이른 상대 메일 시각. 쿼리 한 번."""
    out: dict[int, dict] = {}
    if not task_ids:
        return out
    at = func.coalesce(Email.received_at, Email.created_at)
    rows = db.execute(
        select(Email.task_id, Email.id, at.label("at"))
        .where(Email.user_id == user_id, Email.task_id.in_(task_ids),
               Email.direction == EmailDirection.INBOUND.value)
    ).all()
    for task_id, email_id, when in rows:
        when = inbox._utc(when)
        cur = out.setdefault(task_id, {"id": None, "latest": None, "first": None})
        if cur["latest"] is None or (when, email_id) > (cur["latest"], cur["id"]):
            cur["id"], cur["latest"] = email_id, when
        if cur["first"] is None or when < cur["first"]:
            cur["first"] = when
    return out


def _todo_counts(task: Task) -> tuple[int, int]:
    items = [p for p in (task.plan or []) if isinstance(p, dict)]
    return sum(1 for p in items if p.get("done")), len(items)


def _sender_fallback(db: Session, task: Task) -> str:
    brief_id = (task.brief or {}).get("email_id")
    sender = None
    if isinstance(brief_id, int):
        sender = db.scalar(select(Email.sender).where(Email.id == brief_id, Email.user_id == task.user_id))
    if sender is None:
        sender = db.scalar(select(Email.sender).where(
            Email.user_id == task.user_id, Email.task_id == task.id,
            Email.direction == EmailDirection.INBOUND.value).order_by(Email.id).limit(1))
    return parseaddr(sender or "")[1] or (sender or "")


def _card(db: Session, task: Task, times: dict, now: datetime, tz: str, *, candidates: bool) -> InboxCard:
    brief = task.brief or {}
    cp = brief.get("counterpart") if isinstance(brief.get("counterpart"), dict) else {}
    name = str(cp.get("name") or "").strip()
    org = str(cp.get("organization") or "").strip()
    if not name:
        name = _sender_fallback(db, task)
    t = times.get(task.id) or {}
    done, total = _todo_counts(task)
    return InboxCard(
        task_id=task.id, title=task.title, summary=str(brief.get("summary") or task.title),
        sender_name=name, sender_org=org, priority=task.priority,
        deadline_badge=inbox.deadline_badge(task, now, tz), todo_done=done, todo_total=total,
        nudge_days=inbox.nudge_days(task, t.get("latest"), now, tz),
        returned=inbox.returned_at(task, now, tz) is not None,
        has_merge_candidates=bool(candidates and inbox.merge_candidates(db, task, now)),
        analysis=str(brief.get("analysis") or ""),
        received_at=t.get("latest") or inbox._utc(task.created_at),
    )


def _mail_tasks(db: Session, user: User) -> list[Task]:
    return list(db.scalars(select(Task).where(Task.user_id == user.id, Task.origin == "mail",
                                              Task.merged_into_id.is_(None))).all())


@router.get("", response_model=InboxOut)
def get_inbox(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = get_settings()
    tz = s.user_timezone
    now = inbox.now_utc()
    tasks = _mail_tasks(db, user)
    times = _inbound_times(db, user.id, [t.id for t in tasks])
    by_tab: dict[str, list[Task]] = {inbox.TAB_TODO: [], inbox.TAB_SENT: [], inbox.TAB_REFERENCE: [],
                                     inbox.TAB_DONE: []}
    for t in tasks:
        tab = inbox.tab_of(t)
        if tab is not None:
            by_tab[tab].append(t)

    buckets: dict[str, list[Task]] = {inbox.BUCKET_NOW: [], inbox.BUCKET_WEEK: [], inbox.BUCKET_LATER: []}
    snoozed: list[Task] = []
    for t in by_tab[inbox.TAB_TODO]:
        if inbox.is_snoozed(t, now, tz):
            snoozed.append(t)
        else:
            buckets[inbox.bucket_of(t, now, tz)].append(t)
    for items in buckets.values():
        items.sort(key=lambda t: inbox.sort_key(t, (times.get(t.id) or {}).get("first")))
    snoozed.sort(key=lambda t: inbox.snooze_wake_at(t, tz))

    def cards(items: list[Task], candidates: bool = False) -> list[InboxCard]:
        return [_card(db, t, times, now, tz, candidates=candidates) for t in items]

    def newest(items: list[Task], key) -> list[Task]:
        return sorted(items, key=lambda t: (inbox._utc(key(t)), t.id), reverse=True)

    def latest(t: Task):
        return (times.get(t.id) or {}).get("latest") or t.created_at

    since = now - RECENT
    done_items = [t for t in by_tab[inbox.TAB_DONE] if inbox._utc(t.completed_at or t.updated_at) >= since]
    done_items = newest(done_items, lambda t: t.completed_at or t.updated_at)[:LIST_MAX]
    sent = newest(by_tab[inbox.TAB_SENT], latest)
    reference = newest(by_tab[inbox.TAB_REFERENCE], latest)

    snoozed_out = []
    for t in snoozed:
        card = _card(db, t, times, now, tz, candidates=True)
        snoozed_out.append(SnoozedCard(**card.model_dump(), wake_at=inbox.snooze_wake_at(t, tz)))

    tabs = InboxTabs(
        todo=InboxBuckets(**{k: cards(v, True) for k, v in buckets.items()}),
        sent=cards(sent), reference=cards(reference), done=cards(done_items))
    counts = InboxCounts(
        now=len(buckets["now"]), week=len(buckets["week"]), later=len(buckets["later"]),
        todo=sum(len(v) for v in buckets.values()), sent=len(sent), reference=len(reference),
        done=len(done_items))
    return InboxOut(counts=counts, tabs=tabs, snoozed=snoozed_out, not_work=_not_work(db, user, now),
                    triage_enabled=bool(s.mail_triage_enabled))


def _not_work(db: Session, user: User, now: datetime) -> list[NotWorkMail]:
    at = func.coalesce(Email.received_at, Email.created_at)
    rows = db.scalars(
        select(Email)
        .where(Email.user_id == user.id, Email.direction == EmailDirection.INBOUND.value,
               Email.gmail_message_id.is_not(None), Email.triage.in_(("not_work", "filtered")),
               Email.task_id.is_(None), at >= now - RECENT)
        .order_by(at.desc(), Email.id.desc()).limit(LIST_MAX)
    ).all()
    return [NotWorkMail(email_id=e.id, sender=parseaddr(e.sender or "")[1] or (e.sender or ""),
                        subject=(e.subject or "")[:200], triage=e.triage or "", triage_reason=e.triage_reason or "",
                        received_at=inbox._utc(e.received_at or e.created_at)) for e in rows]


@router.get("/alerts", response_model=InboxAlertsOut)
def get_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tz = get_settings().user_timezone
    now = inbox.now_utc()
    todo = [t for t in _mail_tasks(db, user) if inbox.tab_of(t) == inbox.TAB_TODO]
    times = _inbound_times(db, user.id, [t.id for t in todo])
    alerts: list[dict] = []
    now_count = 0
    for t in todo:
        lt = times.get(t.id) or {}
        alerts.extend(inbox.alerts_for(t, lt.get("id"), lt.get("latest"), now, tz))
        if not inbox.is_snoozed(t, now, tz) and inbox.bucket_of(t, now, tz) == inbox.BUCKET_NOW:
            now_count += 1
    return InboxAlertsOut(now_count=now_count, alerts=alerts)
