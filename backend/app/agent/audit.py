import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentActionLog, RiskLevel

_MAX_LOG_CHARS = 6000


def _clip(data: Any) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        data = {"value": data}
    raw = json.dumps(data, ensure_ascii=False, default=str)
    if len(raw) <= _MAX_LOG_CHARS:
        return json.loads(raw)
    return {"truncated": True, "preview": raw[:_MAX_LOG_CHARS]}


def log_action(
    db: Session,
    *,
    user_id: int,
    task_id: int | None,
    run_id: int | None,
    action_type: str,
    tool_name: str = "",
    input_data: Any = None,
    output_data: Any = None,
    reason: str = "",
    risk_level: str = RiskLevel.LOW,
    status: str = "SUCCESS",
) -> AgentActionLog:
    entry = AgentActionLog(
        user_id=user_id, task_id=task_id, run_id=run_id, action_type=action_type, tool_name=tool_name,
        input_data=_clip(input_data), output_data=_clip(output_data), reason=(reason or "")[:2000],
        risk_level=risk_level, status=status,
    )
    db.add(entry)
    db.flush()
    return entry
