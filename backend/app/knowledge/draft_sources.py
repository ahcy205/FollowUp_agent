"""초안이 쓴 자료 출처 관리."""
from sqlalchemy.orm import Session


def purge_source_from_drafts(db: Session, user_id: int, source_id: int) -> int:
    """자료를 지우거나 내부용으로 바꿀 때 초안에서 그 출처를 걷어낸다. Task 6에서 채운다."""
    return 0
