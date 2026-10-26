"""업무 합치기.

합쳐진 업무(merged_into_id가 있음)로 오는 메일과 동작은 합쳐진 대상 업무로 간다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task

MAX_HOPS = 5


def resolve_merged(db: Session, task: Task) -> Task:
    """merged_into_id를 따라 최종 대상 업무를 돌려준다.

    최대 5번까지만 따라간다. 순환이 보이거나, 대상이 없거나, 다른 사용자의 업무이면 거기서 멈추고 마지막으로 찾은 업무를 돌려준다.
    """
    seen = {task.id}
    current = task
    for _ in range(MAX_HOPS):
        target_id = current.merged_into_id
        if target_id is None or target_id in seen:
            break
        target = db.scalar(select(Task).where(Task.id == target_id, Task.user_id == task.user_id))
        if target is None:
            break
        seen.add(target.id)
        current = target
    return current
