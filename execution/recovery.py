"""Recovery — re-claiming abandoned tasks, a queue-infrastructure concern.

Recovery belongs to the task infrastructure, NOT the orchestrator. The
orchestrator has no idea whether it was started normally or because a previous
worker died — recovery simply requeues a CLAIMED task whose lease expired, and
some worker picks it up again. It is based on durable evidence, never on knowing
which worker died.
"""
from __future__ import annotations


class RecoveryManager:
    """Wraps a TaskQueue with a lease rule: CLAIMED + lease expired -> recoverable."""

    def __init__(self, queue, lease_seconds: float = 30.0) -> None:
        self.queue = queue
        self.lease_seconds = lease_seconds

    def recover(self, now=None) -> list[str]:
        """Requeue every abandoned (lease-expired, CLAIMED) task. Returns their ids."""
        return self.queue.recover_abandoned(self.lease_seconds, now)
