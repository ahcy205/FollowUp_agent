"""답장 보내기 (설계 3.2절).

- 보내기 직전에 다시 확인한다: 받는 사람(코드가 정한 값과 같은지), 참조(후보 안인지), 빈칸 0, Google 연결.
- 두 번 발송 방지: draft → sending을 상태 조건이 걸린 UPDATE 한 줄로 선점하고 커밋한 뒤에 Gmail을 부른다.
- 결과가 불분명한 오류(타임아웃 · 5xx · 응답을 읽지 못함)는 unknown으로 둔다. 다시 보내면 두 번 나갈 수 있기 때문이다.
  동기화가 보낸 메일을 찾으면 lifecycle.record_sent_mail이 sent로 바꾼다. 5분이 지나면 사용자가 확인한다.
- Google이 확실히 거절한 오류(인증 만료 · 4xx)는 초안으로 되돌리고 이유를 알려 준다.
- 동기화가 앱이 보낸 메일을 먼저 저장했을 수 있다: 같은 gmail_message_id 행을 다시 넣지 않고 기록도 한 번만 남긴다.
- 로그 · 타임라인에는 수신자 수와 예외 타입 이름만 남긴다. 본문 · 제목 · 주소는 남기지 않는다.
- References: Email이 원래 References 머리글을 저장하지 않으므로, 이 업무에서 같은 스레드 메일들의
  rfc_message_id를 시간 순으로 이어 쓴다(마지막 10개). 앱이 보낸 사본도 만든 Message-ID를 저장해 이어지게 한다.
"""
import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import make_msgid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.agent.audit import log_action
from app.agent.orchestrator import CLOSED_TASK_STATUSES
from app.google.client import GoogleAuthError, GoogleClient, GoogleError, GoogleTransientError
from app.google.oauth import access_token_for, forget_access_token
from app.mail import lifecycle
from app.mail.errors import ActionError
from app.mail.inbox import _utc
from app.mail.reply_draft import _emails, _split, placeholders, reply_recipients
from app.models import Email, EmailDirection, GoogleAccount, GoogleAccountStatus, ReplyDraft, Task, User

log = logging.getLogger(__name__)

UNKNOWN_CONFIRM_AFTER = timedelta(minutes=5)
REFERENCES_MAX = 10

ALREADY = "이미 보내는 중이거나 보낸 초안입니다."
RECIPIENT_CHANGED = "받는 사람이 바뀌었습니다. 초안을 다시 만들어 주세요."
CC_CHANGED = "참조 후보가 바뀌었습니다. 초안을 다시 만들어 주세요."
NOT_CONNECTED = "Google이 연결되어 있지 않습니다. Google 연결을 다시 해 주세요."
RECONNECT = "Google 인증이 만료되어 보내지 못했습니다. Google 연결을 다시 해 주세요."
REJECTED = "Gmail이 메일을 보내지 않았습니다. 초안은 그대로 있으니 잠시 후 다시 보내 주세요."
TOKEN_FAILED = "Google에 연결하지 못해 보내지 않았습니다. 잠시 후 다시 보내 주세요."
CHECKING = "아직 발송 여부를 확인하는 중입니다. 잠시 후 다시 확인해 주세요."
NOT_UNKNOWN = "발송 여부를 확인할 초안이 아닙니다."
CLOSED = "닫혔거나 다른 업무에 합쳐진 업무에서는 답장을 보낼 수 없습니다."
ALREADY_REPLIED = "이미 답장을 보낸 것으로 확인됐습니다. 초안을 지우거나 다시 만들어 주세요."
FOUND_IN_GMAIL = "Gmail에서 이 답장이 이미 보내진 것으로 확인됩니다. 보낸편지함을 확인해 주세요."


# ---------------------------------------------------------------- MIME

def _reply_subject(subject: str) -> str:
    s = " ".join((subject or "").split())
    return s if s[:3].lower() == "re:" else f"Re: {s}".strip()


def new_message_id(from_addr: str) -> str:
    """보내는 사람 도메인으로 Message-ID를 만든다. 앱이 보낸 사본에 저장해 다음 답장의 References에 넣는다."""
    domain = (from_addr or "").rpartition("@")[2].strip().strip(">") or None
    return make_msgid(domain=domain)


def build_mime(*, from_addr: str, to_addr: str, cc: list[str], subject: str, body: str, in_reply_to: str | None,
               references: list[str], message_id: str | None = None) -> bytes:
    """일반 텍스트 utf-8 답장. 머리글에 줄바꿈이 있으면 ValueError(머리글 주입 방지).

    message_id를 주지 않으면 보내는 사람 도메인으로 새로 만든다.
    """
    m = EmailMessage(policy=default_policy)
    m["Message-ID"] = message_id or new_message_id(from_addr)
    m["From"] = from_addr
    m["To"] = to_addr
    if cc:
        m["Cc"] = ", ".join(cc)
    m["Subject"] = _reply_subject(subject)
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        refs = [r for r in references if r != in_reply_to] + [in_reply_to]
    else:
        refs = list(references)
    if refs:
        m["References"] = " ".join(refs)
    m.set_content(body, charset="utf-8")
    return m.as_bytes()


# ---------------------------------------------------------------- 다시 확인

def _account(db: Session, user_id: int) -> GoogleAccount | None:
    return db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user_id))


def replied_after_draft(db: Session, task: Task, draft: ReplyDraft) -> bool:
    """초안을 만들거나 고치거나 되돌린(updated_at) 뒤에 보낸 답장이 확인됐는가.

    답장 보냄 시각(reply_sent_at)은 Gmail에서 보낸 메일이면 메일 자체의 시각이라 초안을 되돌린 시각보다 이를 수 있다.
    그래서 보낸 메일을 저장한(확인한) 시각(created_at)도 본다.
    """
    if draft.updated_at is None:
        return False
    since = _utc(draft.updated_at)
    if task.reply_sent_at is not None and _utc(task.reply_sent_at) > since:
        return True
    found = db.scalar(select(func.max(Email.created_at)).where(
        Email.user_id == task.user_id, Email.task_id == task.id, Email.direction == EmailDirection.OUTBOUND))
    return found is not None and _utc(found) > since


def ensure_can_send(db: Session, user: User, task: Task, draft: ReplyDraft) -> GoogleAccount:
    """보내기 직전 확인(Google을 부르지 않는다). API는 호출 횟수 제한보다 먼저 부른다."""
    if task.status in CLOSED_TASK_STATUSES or task.merged_into_id is not None:
        raise ActionError(CLOSED, 409)
    if draft.status != "draft":
        raise ActionError(ALREADY, 409)
    if replied_after_draft(db, task, draft):
        # 초안을 만들거나 되돌린 뒤 보낸 답장이 확인됐다('안 보내졌어요' 뒤 동기화가 찾은 경우 등). 두 번 보내지 않는다.
        raise ActionError(ALREADY_REPLIED, 409)
    account = _account(db, user.id)
    if account is None or account.status != GoogleAccountStatus.CONNECTED:
        raise ActionError(NOT_CONNECTED, 409)
    blanks = placeholders(draft.body)
    if blanks:
        raise ActionError(f"채우지 않은 빈칸 [[ ]]이 {len(blanks)}개 있습니다. 빈칸을 채운 뒤 보내 주세요.", 409)
    to, candidates = reply_recipients(db, task, account.google_email)
    if not to or to != (draft.to_addr or "").strip().lower():
        raise ActionError(RECIPIENT_CHANGED, 409)
    if not {str(c).strip().lower() for c in (draft.cc_selected or [])} <= set(candidates):
        raise ActionError(CC_CHANGED, 409)
    return account


def _reply_target(db: Session, task: Task, me: str) -> tuple[str | None, str | None, list[str]]:
    """(thread_id, in_reply_to, references). 받는 사람을 정한 메일(내가 아닌 사람의 가장 최근 받은 메일) 기준."""
    emails = _emails(db, task)  # 최근 것부터
    me = (me or "").strip().lower()
    latest = next((e for e in emails if e.direction == EmailDirection.INBOUND
                   and (_split(e.sender) or [""])[0] not in ("", me)), None)
    thread = (latest.gmail_thread_id if latest else None) or task.gmail_thread_id
    in_reply_to = latest.rfc_message_id if latest else None
    chain = [e.rfc_message_id for e in reversed(emails)
             if e.rfc_message_id and e.gmail_thread_id == thread and e.rfc_message_id != in_reply_to]
    return thread, in_reply_to, chain[-(REFERENCES_MAX - 1):]


# ---------------------------------------------------------------- 상태 바꾸기

def _set(db: Session, draft: ReplyDraft, statuses: tuple[str, ...], **values) -> bool:
    result = db.execute(update(ReplyDraft).where(ReplyDraft.id == draft.id, ReplyDraft.status.in_(statuses))
                        .values(**values).execution_options(synchronize_session=False))
    return result.rowcount == 1


def _sent_reason(draft: ReplyDraft) -> str:
    return f"답장을 보냈습니다 (받는 사람 1명, 참조 {len(draft.cc_selected or [])}명)"


def _back_to_draft(db: Session, draft: ReplyDraft, error: Exception) -> None:
    _set(db, draft, ("sending",), status="draft", sending_at=None, updated_at=datetime.now(UTC))
    log_action(db, user_id=draft.user_id, task_id=draft.task_id, run_id=None, action_type="REPLY_SEND_FAILED",
               output_data={"error": type(error).__name__}, reason="답장을 보내지 못했습니다. 초안은 그대로 있습니다")
    db.commit()
    db.refresh(draft)


def _to_unknown(db: Session, draft: ReplyDraft, error: Exception) -> None:
    log.warning("reply send %s: result unknown: %s", draft.id, type(error).__name__)
    if _set(db, draft, ("sending",), status="unknown"):
        log_action(db, user_id=draft.user_id, task_id=draft.task_id, run_id=None, action_type="REPLY_SEND_UNKNOWN",
                   output_data={"error": type(error).__name__},
                   reason="발송 결과를 확인하지 못했습니다. 보낸편지함과 맞춰 보는 중입니다")
    db.commit()
    db.refresh(draft)


def _finish_sent(db: Session, task: Task, draft: ReplyDraft, account: GoogleAccount, message_id: str,
                 thread_id: str | None, subject: str, now: datetime, rfc_message_id: str | None = None) -> None:
    """보낸 메일 저장 · 초안 sent · 답장 보냄. 동기화가 먼저 저장했으면(같은 gmail_message_id) 다시 넣지 않는다."""
    inserted = False
    exists = db.scalar(select(Email.id).where(Email.user_id == task.user_id, Email.gmail_message_id == message_id))
    if exists is None:
        row = Email(user_id=task.user_id, task_id=task.id, sender=account.google_email, receiver=draft.to_addr,
                    cc=list(draft.cc_selected or []), subject=subject, content=draft.body,
                    direction=EmailDirection.OUTBOUND, processed=True, triage="sent", sent_at=now, received_at=now,
                    gmail_message_id=message_id, gmail_thread_id=thread_id,
                    rfc_message_id=rfc_message_id[:500] if rfc_message_id else None,
                    gmail_meta={"labels": ["SENT"], "sent_by_app": True})
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            inserted = True
        except IntegrityError:  # 동기화가 같은 메일을 방금 저장했다
            log.info("reply send %s: sent copy already stored by sync", draft.id)
    resolved = _set(db, draft, ("sending", "unknown"), status="sent", gmail_message_id=message_id)
    db.refresh(draft)
    # 동기화가 먼저 저장했다면 record_sent_mail이 이미 답장 보냄으로 옮기고 기록했다.
    if resolved and inserted:
        lifecycle.mark_reply_sent(db, task, None, now, source="app", reason=_sent_reason(draft),
                                  recipients=1 + len(draft.cc_selected or []))
    db.commit()


# ---------------------------------------------------------------- 보내기

def send_draft(db: Session, user: User, task: Task, draft: ReplyDraft, client: GoogleClient, now: datetime) -> None:
    """확인 → 선점(커밋) → Gmail 발송. 결과가 불분명하면 unknown으로 두고 정상 반환한다. 커밋한다."""
    now = _utc(now)
    account = ensure_can_send(db, user, task, draft)
    thread_id, in_reply_to, references = _reply_target(db, task, account.google_email)
    subject = _reply_subject(draft.subject)
    rfc_id = new_message_id(account.google_email)
    raw = build_mime(from_addr=account.google_email, to_addr=draft.to_addr, cc=list(draft.cc_selected or []),
                     subject=subject, body=draft.body, in_reply_to=in_reply_to, references=references,
                     message_id=rfc_id)
    try:
        token = access_token_for(db, account, client)  # 보내기 전이므로 실패해도 나간 메일은 없다
    except GoogleAuthError:
        raise ActionError(RECONNECT, 409)
    except GoogleError as e:
        log.info("reply send %s: token refresh failed: %s", draft.id, type(e).__name__)
        raise ActionError(TOKEN_FAILED, 503)

    if not _set(db, draft, ("draft",), status="sending", sending_at=now):
        db.rollback()
        raise ActionError(ALREADY, 409)
    db.commit()
    db.refresh(draft)

    try:
        sent = client.gmail_send(token, raw, thread_id)
    except GoogleTransientError as e:
        _to_unknown(db, draft, e)
        return
    except GoogleAuthError as e:  # 401: 보내지 않았다
        forget_access_token(user.id)
        _back_to_draft(db, draft, e)
        raise ActionError(RECONNECT, 409)
    except GoogleError as e:  # 4xx: Google이 확실히 거절했다
        _back_to_draft(db, draft, e)
        raise ActionError(REJECTED, 409)
    except Exception as e:  # noqa: BLE001 - 응답을 읽지 못함: 나갔을 수도 있으므로 다시 보내게 두지 않는다
        db.rollback()
        _to_unknown(db, draft, e)
        return
    _finish_sent(db, task, draft, account, sent.id, sent.thread_id or thread_id, subject, now, rfc_id)


def is_unknown(draft: ReplyDraft, now: datetime) -> bool:
    """unknown이거나, sending인 채 5분이 지났다(발송 중 서버가 멈춘 경우)."""
    if draft.status == "unknown":
        return True
    return (draft.status == "sending" and draft.sending_at is not None
            and _utc(draft.sending_at) + UNKNOWN_CONFIRM_AFTER <= _utc(now))


def confirm_unknown(db: Session, user: User, task: Task, draft: ReplyDraft, *, sent: bool, now: datetime) -> None:
    """사용자가 보낸편지함을 보고 답한다. 5분이 지난 unknown만. 커밋하지 않는다."""
    now = _utc(now)
    if draft.status not in ("unknown", "sending"):
        raise ActionError(NOT_UNKNOWN, 409)
    if draft.sending_at is None or _utc(draft.sending_at) + UNKNOWN_CONFIRM_AFTER > now or not is_unknown(draft, now):
        raise ActionError(CHECKING, 409)
    if sent:
        if not _set(db, draft, (draft.status,), status="sent"):
            raise ActionError(NOT_UNKNOWN, 409)
        db.refresh(draft)
        lifecycle.mark_reply_sent(db, task, None, now, source="app", reason=_sent_reason(draft),
                                  recipients=1 + len(draft.cc_selected or []))
    else:
        if (task.reply_sent_at is not None
                and _utc(task.reply_sent_at) >= _utc(draft.sending_at) - lifecycle.SENT_MATCH_LEAD):
            # 동기화가 보낸 답장을 이미 확인했다(초안과 맞추지 못했을 뿐). 초안으로 되돌리면 다시 보낼 수 있게 된다.
            raise ActionError(FOUND_IN_GMAIL, 409)
        if not _set(db, draft, (draft.status,), status="draft", sending_at=None, updated_at=now):
            raise ActionError(NOT_UNKNOWN, 409)
        db.refresh(draft)
        log_action(db, user_id=draft.user_id, task_id=draft.task_id, run_id=None, action_type="REPLY_SEND_NOT_SENT",
                   output_data={}, reason="보내지지 않은 것으로 확인해 초안으로 되돌렸습니다")
    db.flush()


# ---------------------------------------------------------------- 남은 할 일

def remaining_todos(task: Task) -> int:
    return sum(1 for p in (task.plan or []) if isinstance(p, dict) and not p.get("done"))


def remaining_todos_prompt(task: Task) -> bool:
    return str(task.status) == "WAITING" and task.reply_sent_at is not None and remaining_todos(task) > 0


def complete_remaining_todos(task: Task) -> int:
    """남은 할 일을 모두 완료로 바꾸고 바꾼 수를 돌려준다. 커밋하지 않는다."""
    plan, changed = [], 0
    for p in task.plan or []:
        if isinstance(p, dict) and not p.get("done"):
            p = {**p, "done": True}
            changed += 1
        plan.append(p)
    if changed:
        task.plan = plan  # 새 리스트를 넣어야 JSON 컬럼 변경이 감지된다
        db = object_session(task)
        if db is not None:
            log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="TODOS_COMPLETED",
                       output_data={"count": changed}, reason=f"남은 할 일 {changed}개를 완료로 정리했습니다")
    return changed
