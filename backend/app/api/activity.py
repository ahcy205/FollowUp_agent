"""최근 업무 활동 API: 내 메일 업무의 처리 기록 (읽기 전용).

입력 · 출력 데이터(input_data · output_data)는 내보내지 않는다. 한 줄 요약(reason)까지만 보인다.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentActionLog, Task, User
from app.schemas import ActivityItemOut, ActivityOut
from app.security import get_current_user

router = APIRouter(prefix="/api", tags=["activity"])


@router.get("/activity", response_model=ActivityOut)
def recent_activity(limit: int = Query(50, ge=1, le=100), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    rows = db.execute(
        select(AgentActionLog, Task.title)
        .join(Task, Task.id == AgentActionLog.task_id)
        .where(AgentActionLog.user_id == user.id, Task.user_id == user.id, Task.origin == "mail")
        .order_by(AgentActionLog.created_at.desc(), AgentActionLog.id.desc())
        .limit(limit)
    ).all()
    return ActivityOut(items=[
        ActivityItemOut(id=log.id, task_id=log.task_id, task_title=title, action_type=log.action_type,
                        reason=log.reason or "", risk_level=log.risk_level, created_at=log.created_at)
        for log, title in rows
    ])
