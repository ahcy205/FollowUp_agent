"""받은 업무 동작의 오류. task_actions와 merge가 함께 쓴다(서로 import하지 않게 따로 둔다)."""


class ActionError(Exception):
    """사용자에게 보일 한국어 문구와 HTTP 상태."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
