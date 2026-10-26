"""메일 업무의 기한을 사용자의 Google 캘린더(primary) 일정으로 반영한다.

- 결정적 일정 ID("fu" + 계정별 salt + 업무 번호)로 만들므로, 번호를 저장하기 전에 죽어도 다음 주기에 409를 받고
  기존 일정을 이어 쓴다. 409 때는 일정의 비공개 표식(extendedProperties.private.followupTask)이 이 업무의 것인지
  확인한다. 표식이 없거나 다르면 남의 일정이므로 고치지도, 사용자 삭제로 보지도 않고 error로 둔다.
- 사용자가 캘린더에서 지운 일정(404 · 410 · status=cancelled)은 deleted_by_user로 두고 다시 만들지 않는다.
- 캘린더 오류는 업무 처리를 막지 않는다. 업무 1개마다 커밋하고 예외를 밖으로 내보내지 않는다.
- 로그에는 업무 번호, 건수, 예외 타입 이름만 남긴다. 요약 · 상대 이름 · 일정 내용은 남기지 않는다.

이 모듈은 app.agent.orchestrator를 가져오지 않는다 (orchestrator가 mark_calendar_dirty를 가져온다).
"""
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.agent.audit import log_action
from app.google.client import (
    CalendarEvent, GoogleAuthError, GoogleClient, GoogleConflict, GoogleError, GoogleNotFound, GoogleTransientError,
)
from app.google.oauth import access_token_for, forget_access_token
from app.mail.calendar_event import build_event, private_marker
from app.models import GoogleAccount, Task, TaskStatus

log = logging.getLogger(__name__)

_CLOSED = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)

MAX_TRANSIENT_ATTEMPTS = 5
TRANSIENT_MESSAGE = "Google 캘린더 연결이 불안정해 일정을 반영하지 못했습니다."
ERROR_MESSAGE = "Google 캘린더에 일정을 반영하지 못했습니다."
FOREIGN_EVENT_MESSAGE = "같은 번호의 다른 캘린더 일정이 있어 반영하지 못했습니다."

_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"  # 캘린더 일정 ID에 쓸 수 있는 글자
_SALT_LEN = 8
_MARKER_KEY = "followupTask"

NONE, PENDING, SYNCED, DELETED, ERROR = "none", "pending", "synced", "deleted_by_user", "error"


@dataclass
class CalendarSyncResult:
    created: int = 0
    updated: int = 0
    deleted_by_user: int = 0
    errors: int = 0
    skipped: int = 0


class _StopSync(Exception):
    """재연결이 필요하다. 이번 호출을 상태 변경 없이 멈춘다."""


def mark_calendar_dirty(task: Task) -> None:
    """메일 업무의 일정이 다시 반영되어야 함을 표시한다. 커밋은 호출하는 쪽이 한다."""
    if task.origin != "mail" or task.calendar_state == DELETED:
        return
    if task.deadline_at is not None or task.calendar_event_id:
        if task.calendar_state == ERROR:
            task.calendar_attempts = 0  # 다시 표시하면 "5번 연속" 횟수를 처음부터 센다
        task.calendar_state = PENDING


def _targets(db: Session, user_id: int, max_tasks: int) -> list[int]:
    return list(db.scalars(
        select(Task.id)
        .where(Task.user_id == user_id, Task.origin == "mail",
               or_(Task.calendar_state == PENDING,
                   # 닫힌 업무는 새 일정을 만들지 않으므로 고르지 않는다 (매 주기 한도를 차지하지 않게)
                   and_(Task.calendar_state == NONE, Task.deadline_at.is_not(None),
                        Task.status.not_in(_CLOSED))))
        .order_by(Task.updated_at, Task.id)
        .limit(max_tasks)
    ).all())


def _set_state(db: Session, task: Task, state: str, action_type: str, *, reason: str, **extra) -> None:
    """상태를 바꾸고, 실제로 바뀌었을 때만 타임라인에 남긴다."""
    before = task.calendar_state
    task.calendar_state = state
    if before != state:
        log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type=action_type,
                   output_data={"task_id": task.id, "state": state, **extra}, reason=reason)


def _synced(db: Session, task: Task, event: CalendarEvent, *, created: bool) -> None:
    task.calendar_event_id = event.id
    if event.html_link:
        task.calendar_event_url = event.html_link
    task.calendar_attempts = 0
    task.calendar_error = ""
    _set_state(db, task, SYNCED, "CALENDAR_SYNCED", created=created,
               reason="Google 캘린더에 일정을 만들었습니다" if created else "Google 캘린더 일정을 고쳤습니다")


def _deleted(db: Session, task: Task) -> None:
    task.calendar_attempts = 0
    task.calendar_error = ""
    _set_state(db, task, DELETED, "CALENDAR_DELETED_BY_USER",
               reason="사용자가 Google 캘린더에서 일정을 지웠습니다. 다시 만들지 않습니다")


def _error(db: Session, task: Task, message: str) -> None:
    task.calendar_error = message
    _set_state(db, task, ERROR, "CALENDAR_ERROR", reason=message)


def _ensure_salt(db: Session, account: GoogleAccount) -> str:
    """계정의 일정 번호 salt를 돌려준다. 없으면 한 번 만들어 커밋한다 (재연결해도 유지, 연결 해제 시 함께 삭제)."""
    if not account.calendar_id_salt:
        account.calendar_id_salt = "".join(secrets.choice(_BASE32HEX) for _ in range(_SALT_LEN))
        db.commit()
    return account.calendar_id_salt


def _patch(db: Session, task: Task, client: GoogleClient, token: str, body: dict) -> str:
    patch_body = {k: v for k, v in body.items() if k != "id"}
    # Google PATCH는 start/end 같은 중첩 객체를 합친다. 종일 ↔ 시간 일정으로 바뀌면 이전 필드가 남아
    # date와 dateTime이 함께 있게 되어 400이 난다. 반대쪽 필드를 None(JSON null)으로 보내 지운다.
    for key in ("start", "end"):
        part = dict(patch_body[key])
        if "dateTime" in part:
            part["date"] = None
        else:
            part["dateTime"] = None
            part["timeZone"] = None
        patch_body[key] = part
    try:
        event = client.calendar_patch(token, task.calendar_event_id, patch_body)
    except GoogleNotFound:
        _deleted(db, task)
        return "deleted"
    if event.status == "cancelled":
        _deleted(db, task)
        return "deleted"
    _synced(db, task, event, created=False)
    return "updated"


def _process(db: Session, task: Task, client: GoogleClient, token: str, tz: str, public_url: str, salt: str) -> str:
    """업무 1개를 반영한다. 결과: created | updated | deleted | error | skipped. GoogleError는 호출한 쪽이 처리한다."""
    if not task.calendar_event_id:
        if task.deadline_at is None or task.status in _CLOSED:
            # 완료 · 취소된 업무는 이미 있는 일정에만 ✓를 붙인다. 새 일정은 만들지 않는다.
            task.calendar_state = NONE
            return "skipped"
        body = build_event(task, tz=tz, public_url=public_url, salt=salt)
        try:
            event = client.calendar_insert(token, body)
        except GoogleConflict:
            existing = client.calendar_get(token, body["id"])
            if existing.private.get(_MARKER_KEY) != private_marker(task, salt):
                # 다른 DB · 다른 인스턴스가 만든 일정이다. 건드리지 않는다.
                _error(db, task, FOREIGN_EVENT_MESSAGE)
                return "error"
            # 지난 주기에 만들고 번호를 저장하기 전에 멈췄다. 기존 일정을 찾아 이어 쓴다.
            task.calendar_event_id = existing.id or body["id"]
            if existing.html_link:
                task.calendar_event_url = existing.html_link
            if existing.status == "cancelled":
                _deleted(db, task)
                return "deleted"
            return _patch(db, task, client, token, body)
        _synced(db, task, event, created=True)
        return "created"

    if task.deadline_at is None:
        # 기한 삭제에 따른 일정 정리는 이 단계 범위가 아니다. 일정은 그대로 둔다.
        task.calendar_state = SYNCED
        return "skipped"
    return _patch(db, task, client, token, build_event(task, tz=tz, public_url=public_url, salt=salt))


def _sync_one(db: Session, task_id: int, client: GoogleClient, token: str, tz: str, public_url: str, salt: str,
              result: CalendarSyncResult) -> None:
    task = db.get(Task, task_id)
    if task is None:
        return
    try:
        outcome = _process(db, task, client, token, tz, public_url, salt)
    except GoogleAuthError as e:
        db.rollback()
        forget_access_token(task.user_id)
        log.warning("calendar sync for task %s stopped: %s", task_id, type(e).__name__)
        raise _StopSync from e
    except GoogleTransientError as e:
        db.rollback()
        task = db.get(Task, task_id)
        if task is None:
            return
        task.calendar_attempts = (task.calendar_attempts or 0) + 1
        if task.calendar_attempts >= MAX_TRANSIENT_ATTEMPTS:
            _error(db, task, TRANSIENT_MESSAGE)
        else:
            task.calendar_state = PENDING
        db.commit()
        result.errors += 1
        log.warning("calendar sync for task %s failed (attempt %d): %s", task_id, task.calendar_attempts,
                    type(e).__name__)
        return
    except Exception as e:  # noqa: BLE001 - GoogleError든 예상 밖 오류든 이 업무만 error로 두고 다음으로 넘어간다
        db.rollback()
        task = db.get(Task, task_id)
        if task is None:
            return
        _error(db, task, ERROR_MESSAGE)
        db.commit()
        result.errors += 1
        log.warning("calendar sync for task %s failed: %s", task_id, type(e).__name__)
        return
    db.commit()
    if outcome == "created":
        result.created += 1
    elif outcome == "updated":
        result.updated += 1
    elif outcome == "deleted":
        result.deleted_by_user += 1
    elif outcome == "error":
        result.errors += 1
    else:
        result.skipped += 1


def sync_calendars(db: Session, account: GoogleAccount, client: GoogleClient, *, max_tasks: int, tz: str,
                   public_url: str) -> CalendarSyncResult:
    """이 계정 사용자의 메일 업무 중 반영할 것을 오래된 순으로 최대 max_tasks개 처리한다. 예외를 내보내지 않는다.

    호출하는 쪽은 세션의 바뀐 내용을 먼저 커밋해야 한다. 업무 하나가 실패하면 세션을 rollback하므로
    커밋하지 않은 변경은 함께 사라진다.
    """
    result = CalendarSyncResult()
    user_id = account.user_id
    try:
        task_ids = _targets(db, user_id, max_tasks)
        if not task_ids:
            return result
        try:
            token = access_token_for(db, account, client)
        except (GoogleAuthError, GoogleTransientError) as e:
            log.info("calendar sync for user %s skipped: %s", user_id, type(e).__name__)
            return result
        salt = _ensure_salt(db, account)
        for task_id in task_ids:
            _sync_one(db, task_id, client, token, tz, public_url, salt, result)
    except _StopSync:
        pass
    except Exception as e:  # noqa: BLE001 - 캘린더 오류가 메일 동기화 주기를 멈추면 안 된다
        db.rollback()
        log.warning("calendar sync for user %s aborted: %s", user_id, type(e).__name__)
    log.info("calendar sync for user %s: created=%d updated=%d deleted_by_user=%d errors=%d skipped=%d",
             user_id, result.created, result.updated, result.deleted_by_user, result.errors, result.skipped)
    return result
