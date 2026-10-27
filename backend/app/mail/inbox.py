"""받은 업무 화면과 브라우저 알림이 함께 쓰는 판단.

탭 · 묶음(지금 처리 / 이번 주 / 나중에) · 정렬 · 기한 배지 · 스누즈 해제 시각 · 답장 넛지 · 알림 · 합치기 후보.
순수 함수는 `now`(UTC aware)와 `tz`(사용자 시간대 이름)를 받는다. 안에서 시계를 읽지 않는다.
날짜 판단(오늘 · 내일 · N일, 09:00)은 사용자 시간대 달력 날짜로 한다. 시간대 없는 값은 UTC로 본다.
"""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Email, EmailDirection, Task, TaskStatus

TAB_TODO, TAB_SENT, TAB_REFERENCE, TAB_DONE = "todo", "sent", "reference", "done"
BUCKET_NOW, BUCKET_WEEK, BUCKET_LATER = "now", "week", "later"

HIGH_NOW_DAYS = 3  # 중요도 높음이면 기한 3일 안(오늘 + 3일 날짜까지)도 지금 처리
WEEK_DAYS = 7
MORNING = time(9, 0)  # 기한 전날 · 당일 알림, 스누즈 안전장치 시각
RETURNED_FOR = timedelta(hours=24)  # "다시 표시됨" 유지 시간
NUDGE_AFTER = timedelta(hours=48)
MERGE_WINDOW = timedelta(days=14)
MERGE_MAX = 3

_TODO_STATUSES = {TaskStatus.ACTION_REQUIRED.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value}
_CLOSED_STATUSES = {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
_MERGE_TARGET_STATUSES = _TODO_STATUSES | {TaskStatus.WAITING.value}
_PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
_MAX_DT = datetime.max.replace(tzinfo=UTC)


def now_utc() -> datetime:
    """API가 시간을 읽는 유일한 곳. 테스트가 monkeypatch한다."""
    return datetime.now(UTC)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _local_date(dt: datetime, tz: str) -> date:
    return _utc(dt).astimezone(ZoneInfo(tz)).date()


def _local_at(day: date, at: time, tz: str) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _brief(task: Task) -> dict:
    return task.brief or {}


def _analysis_waiting(task: Task) -> bool:
    return _brief(task).get("analysis") in ("pending", "failed")


def _summary(task: Task) -> str:
    text = str(_brief(task).get("summary") or task.title or "")
    return " ".join(text.split())


# ---------- 탭 · 기한 ----------

def tab_of(task: Task) -> str | None:
    """합쳐진 업무이거나 메일 업무가 아니면 None."""
    if task.origin != "mail" or task.merged_into_id is not None:
        return None
    status = str(task.status)
    if status in _TODO_STATUSES:
        return TAB_TODO
    if status == TaskStatus.PENDING.value:
        return TAB_TODO if _analysis_waiting(task) else TAB_REFERENCE
    if status == TaskStatus.WAITING.value:
        return TAB_SENT
    if status in _CLOSED_STATUSES:
        return TAB_DONE
    return None


def _deadline_days(task: Task, now: datetime, tz: str) -> tuple[int, bool] | None:
    """(기한 날짜 − 오늘 날짜, 지났는지). 기한이 없으면 None."""
    if task.deadline_at is None:
        return None
    days = (_local_date(task.deadline_at, tz) - _local_date(now, tz)).days
    late = days < 0 or (not task.deadline_all_day and _utc(task.deadline_at) < _utc(now))
    return days, late


def deadline_badge(task: Task, now: datetime, tz: str) -> dict:
    estimated = task.deadline_at is not None and task.deadline_kind == "estimated"
    dd = _deadline_days(task, now, tz)
    if dd is None:
        return {"text": "기한 없음", "late": False, "estimated": False}
    days, late = dd
    if late:
        text = "오늘 지남" if days == 0 else f"D+{-days} 지남"  # 오늘 지난 시간 지정 기한은 "오늘 지남"
    elif days == 0:
        text = "오늘"
    elif days == 1:
        text = "D-1 내일"
    else:
        text = f"D-{days}"
    return {"text": text, "late": late, "estimated": estimated}


def bucket_of(task: Task, now: datetime, tz: str) -> str:
    """처리할 일 탭 업무에만 의미가 있다. 분석 대기 · 실패 업무는 기한을 모르므로 나중에."""
    if _analysis_waiting(task):
        return BUCKET_LATER
    high = task.priority == "HIGH"
    dd = _deadline_days(task, now, tz)
    if dd is None:
        return BUCKET_WEEK if high else BUCKET_LATER
    days, late = dd
    if late or days <= 1 or (high and days <= HIGH_NOW_DAYS):
        return BUCKET_NOW
    if days <= WEEK_DAYS:
        return BUCKET_WEEK
    return BUCKET_LATER


def sort_key(task: Task, received_at: datetime | None) -> tuple:
    """오름차순 정렬 키: 분석 대기 먼저 → 중요도 → 기한 이른 순(없음 마지막) → 원본 메일 받은 순(없음 마지막)."""
    deadline = _utc(task.deadline_at)
    received = _utc(received_at)
    return (
        0 if _analysis_waiting(task) else 1,
        _PRIORITY_RANK.get(task.priority, 1),
        0 if deadline else 1, deadline or _MAX_DT,
        0 if received else 1, received or _MAX_DT,
    )


# ---------- 스누즈 ----------

def snooze_wake_at(task: Task, tz: str) -> datetime | None:
    """실제 해제 시각 = min(정한 시각, 기한 전날 09:00). 스누즈가 없으면 None."""
    until = _utc(task.snoozed_until)
    if until is None:
        return None
    if task.deadline_at is None:
        return until
    safety = _local_at(_local_date(task.deadline_at, tz) - timedelta(days=1), MORNING, tz)
    return min(until, safety)


def is_snoozed(task: Task, now: datetime, tz: str) -> bool:
    wake = snooze_wake_at(task, tz)
    return wake is not None and _utc(now) < wake


def finalize_snooze(task: Task, now: datetime, tz: str) -> None:
    """이미 풀린 스누즈를 끝난 것으로 기록한다: snoozed_until을 비우고 해제 시각을 snooze_returned_at에 남긴다.

    기한을 바꾸기 전에 부른다. 기한이 늦춰지면 해제 시각이 다시 미래로 가서 업무가 또 숨고 알림이 또 울리기 때문이다.
    남기는 시각은 원래 해제 시각이므로 '다시 표시' 알림 키도 그대로다.
    """
    wake = snooze_wake_at(task, tz)
    if wake is not None and wake <= _utc(now):
        task.snooze_returned_at = wake
        task.snoozed_until = None


def returned_at(task: Task, now: datetime, tz: str) -> datetime | None:
    """스누즈에서 돌아온 시각. 돌아온 지 24시간이 지났으면 None."""
    now = _utc(now)
    back = _utc(task.snooze_returned_at)
    if back is not None and now - back < RETURNED_FOR:
        return back
    wake = snooze_wake_at(task, tz)
    if wake is not None and wake <= now < wake + RETURNED_FOR:
        return wake
    return None


# ---------- 넛지 · 알림 ----------

def nudge_days(task: Task, latest_inbound_at: datetime | None, now: datetime, tz: str) -> int | None:
    """가장 최근 상대 메일로부터 48시간 이상 답장하지 않았으면 경과 일수(내림)."""
    if tab_of(task) != TAB_TODO or _brief(task).get("analysis") != "done":
        return None
    if _brief(task).get("needs_reply") is not True or latest_inbound_at is None:
        return None
    if is_snoozed(task, now, tz):
        return None
    elapsed = _utc(now) - _utc(latest_inbound_at)
    if elapsed < NUDGE_AFTER:
        return None
    return elapsed.days


def no_reply_active(task: Task) -> bool:
    """상대 답장이 없어 다시 연락할지 묻는 중인 처리할 일 업무."""
    return task.no_reply_asked_at is not None and tab_of(task) == TAB_TODO


def _alert(kind: str, key_suffix: str, task: Task, text: str) -> dict:
    return {"key": f"{kind}:{task.id}:{key_suffix}", "kind": kind, "task_id": task.id, "text": text}


def alerts_for(task: Task, latest_inbound_id: int | None, latest_inbound_at: datetime | None,
               now: datetime, tz: str) -> list[dict]:
    """처리할 일 탭 업무의 브라우저 알림. 같은 키는 화면이 한 번만 띄운다.

    기한 알림은 스누즈 중에도 낸다. 스누즈는 기한 전날 09:00에 풀리므로 실제로 숨겨진 채 울리지 않는다.
    기한 알림을 낸 업무에는 '다시 표시' 알림을 함께 내지 않는다(같은 업무 알림 두 개).
    """
    if tab_of(task) != TAB_TODO:
        return []
    summary = _summary(task)
    out: list[dict] = []
    now = _utc(now)
    if task.deadline_at is not None:
        due = _local_date(task.deadline_at, tz)
        today = _local_date(now, tz)
        if due == today and now >= _local_at(today, MORNING, tz):
            passed = not task.deadline_all_day and _utc(task.deadline_at) < now
            label = "[마감 지남]" if passed else "[오늘 마감]"
            out.append(_alert("deadline-d0", due.isoformat(), task, f"{label} {summary}"))
        elif due == today + timedelta(days=1) and now >= _local_at(today, MORNING, tz):
            out.append(_alert("deadline-d1", due.isoformat(), task, f"[D-1] {summary}"))
    back = returned_at(task, now, tz)
    if back is not None and not out:
        out.append(_alert("snooze", back.isoformat(timespec="seconds"), task, f"[다시 표시] {summary}"))
    days = nudge_days(task, latest_inbound_at, now, tz)
    if days is not None and latest_inbound_id is not None:
        out.append(_alert("nudge", str(latest_inbound_id), task, f"[답장 {days}일째 없음] {summary}"))
    if no_reply_active(task):
        since = _utc(task.wait_from or task.no_reply_asked_at)
        out.append(_alert("no-reply", since.isoformat(timespec="seconds"), task, f"[답 없음] {summary}"))
    return out


# ---------- DB 조회 ----------

def _received():
    return func.coalesce(Email.received_at, Email.created_at)


def latest_inbound(db: Session, task: Task) -> tuple[int | None, datetime | None]:
    """이 업무의 가장 최근 상대 메일 (번호, 받은 시각). 받은 시각이 없으면 만든 시각."""
    row = db.execute(
        select(Email.id, _received().label("at"))
        .where(Email.user_id == task.user_id, Email.task_id == task.id,
               Email.direction == EmailDirection.INBOUND.value)
        .order_by(_received().desc(), Email.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return row.id, _utc(row.at)


def _norm_addr(addr: str | None) -> str:
    return (addr or "").strip().lower()


def merge_evidence(task: Task, candidate: Task) -> str | None:
    """후보가 "수정 요청으로 보임" 판단의 대상이면 그 근거 문장."""
    judged = task.related if isinstance(task.related, dict) else {}
    if judged.get("state") == "suggested" and judged.get("task_id") == candidate.id:
        evidence = judged.get("evidence")
        return evidence if isinstance(evidence, str) else None
    return None


def _source_sender(db: Session, task: Task) -> str:
    """원본 메일 보낸 사람 주소(소문자). brief.email_id, 없으면 이 업무의 가장 이른 상대 메일."""
    email_id = _brief(task).get("email_id")
    if isinstance(email_id, int):
        sender = db.scalar(select(Email.sender).where(Email.id == email_id, Email.user_id == task.user_id))
        if sender is not None:
            return _norm_addr(sender)
    sender = db.scalar(
        select(Email.sender)
        .where(Email.user_id == task.user_id, Email.task_id == task.id,
               Email.direction == EmailDirection.INBOUND.value)
        .order_by(_received().asc(), Email.id.asc())
        .limit(1)
    )
    return _norm_addr(sender)


def merge_candidates(db: Session, task: Task, now: datetime) -> list[Task]:
    """합치기 후보 (설계 7.3).

    - 접수 때 "수정 요청으로 보임" 판단(task.related)이 있으면 그 업무 하나(지금도 후보 조건을 만족할 때).
    - 관계없다고 판단했으면 없음. 같은 보낸 사람이어도 띄우지 않는다.
    - 판단을 못 했으면(분석 실패 · 판별 꺼짐 · 기록 없음) 5단계 규칙: 같은 보낸 사람의 더 먼저 만들어진 진행 중 메일 업무,
      최근 것부터 최대 3개.
    분석 대기 중인 업무는 합칠 수 없으므로(합치기가 409) 후보를 내지 않는다.
    """
    if (task.origin != "mail" or task.merged_into_id is not None or task.merge_dismissed
            or str(task.status) in _CLOSED_STATUSES or _brief(task).get("analysis") == "pending"):
        return []
    judged = task.related if isinstance(task.related, dict) else {}
    if judged.get("state") == "suggested":
        from app.mail.related import qualifies  # related → inbox 순환 import를 피한다
        target_id = judged.get("task_id")
        target = None
        if isinstance(target_id, int):
            target = db.scalar(select(Task).where(Task.id == target_id, Task.user_id == task.user_id))
        return [target] if target is not None and qualifies(task, target, now) else []
    if judged.get("state") == "none":
        return []
    sender = _source_sender(db, task)
    if not sender:
        return []
    since = _utc(now) - MERGE_WINDOW
    created = _utc(task.created_at)
    rows = db.scalars(
        select(Task)
        .where(Task.user_id == task.user_id, Task.id != task.id, Task.origin == "mail",
               Task.merged_into_id.is_(None), Task.status.in_(_MERGE_TARGET_STATUSES),
               Task.created_at > since, Task.created_at < created)
        .order_by(Task.created_at.desc(), Task.id.desc())
    ).all()
    out: list[Task] = []
    for cand in rows:
        if _source_sender(db, cand) == sender:
            out.append(cand)
            if len(out) == MERGE_MAX:
                break
    return out
