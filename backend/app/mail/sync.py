"""Gmail sync. The worker and sync cycle arrive in later tasks."""


def wake() -> None:
    """Wake the background sync worker. Implemented in a later task; a no-op for now."""
