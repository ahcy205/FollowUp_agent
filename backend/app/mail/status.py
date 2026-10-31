"""메일 업무 상태 상수. 여러 mail 모듈이 함께 쓴다(서로 import하지 않게 따로 둔다)."""
from app.models import TaskStatus

# 닫힌 업무: 완료 · 취소. 새 메일이 오면 다시 열 수 있지만, 닫힌 동안에는 동작 · 캘린더 반영 대상이 아니다.
CLOSED_TASK_STATUSES = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
