from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import orchestrator
from app.agent.audit import log_action
from app.api.tasks import get_owned
from app.db import get_db
from app.models import Customer, Email, EmailDirection, Task, TaskStatus, User
from app.schemas import EmailOut, SimulateReplyIn
from app.security import agent_limiter, get_current_user
from app.seed import REPLY_PRESETS, reset_user_data

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/tasks/{task_id}/simulate-reply", response_model=EmailOut, status_code=201)
def simulate_reply(task_id: int, body: SimulateReplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Demo event source: the customer replies to the last email the agent sent for this task."""
    agent_limiter.check(f"agent:{user.id}")
    task = get_owned(db, Task, task_id, user)
    last_sent = db.scalar(select(Email).where(Email.user_id == user.id, Email.task_id == task.id,
                                              Email.direction == EmailDirection.OUTBOUND).order_by(Email.sent_at.desc()))
    if last_sent is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이 업무에서 발송된 메일이 없어 답변을 시뮬레이션할 수 없습니다.")
    customer = db.scalar(select(Customer).where(Customer.id == last_sent.customer_id, Customer.user_id == user.id))
    if customer is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "메일 수신 고객 정보를 찾을 수 없습니다.")
    if body.preset == "custom":
        text = body.content.strip()
        if not text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "답변 내용을 입력하세요.")
    else:
        text = REPLY_PRESETS[body.preset]
    content = f"{text}\n\n{customer.company_name} {customer.contact_name} 드림"

    now = datetime.now(timezone.utc)
    subject = last_sent.subject if last_sent.subject.startswith("RE:") else f"RE: {last_sent.subject}"
    email = Email(user_id=user.id, customer_id=customer.id, task_id=task.id, sender=customer.email, receiver=user.email,
                  subject=subject, content=content, direction=EmailDirection.INBOUND, processed=False,
                  created_at=now, sent_at=now)
    db.add(email)
    db.flush()
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type="EVENT_RECEIVED", tool_name="",
               input_data={"email_id": email.id, "preset": body.preset},
               reason=f"고객 답변 도착: {customer.company_name} — {subject}")
    db.commit()

    if orchestrator.active_run(db, task) is None and task.status != TaskStatus.COMPLETED and task.agent_enabled:
        orchestrator.start_run(db, user, task, "email_received",
                               {"email_id": email.id, "sender": email.sender, "subject": email.subject})
    # Otherwise the email stays unprocessed and the agent picks it up when its current run finishes.
    db.refresh(email)
    return email


@router.post("/reset", status_code=204)
def reset(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Wipe and regenerate only the current user's workspace."""
    reset_user_data(db, user)
