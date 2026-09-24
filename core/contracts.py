"""Nexus core contracts.

These are the boring, stable objects every component communicates through.
Deliberately stdlib-only (dataclasses) so the contracts have zero dependencies;
the API layer can adopt the same shapes with Pydantic later without touching them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


def new_id(prefix: str) -> str:
    """Uniform opaque id. Keep it boring."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# --------------------------------------------------------------------------
# Core objects
# --------------------------------------------------------------------------

@dataclass
class Task:
    task_id: str
    title: str
    created_at: datetime = field(default_factory=utcnow)
    status: TaskStatus = TaskStatus.QUEUED
    run_ids: list[str] = field(default_factory=list)


@dataclass
class Step:
    step_id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    result: Any = None


@dataclass
class Run:
    run_id: str
    task_id: str
    created_at: datetime = field(default_factory=utcnow)
    steps: list[Step] = field(default_factory=list)


@dataclass
class Decision:
    """The router's answer — model + provider + WHY + constraints."""
    model: str
    provider: str
    reasons: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass
class ToolResult:
    tool_call: ToolCall
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class RetrievalResult:
    query: str
    chunks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 4096
    timeout_ms: int = 30000


@dataclass
class ModelResponse:
    model: str
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost: float = 0.0


@dataclass
class ApprovalRequest:
    approval_id: str
    tool_name: str
    resource: str
    risk: Risk
    status: str = "pending"  # pending / approved / denied


@dataclass
class Evaluation:
    checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """The boring event envelope. Every subsystem can consume it."""
    event_id: str
    event_type: str
    timestamp: datetime
    run_id: str
    task_id: str
    component: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class Trace:
    """The execution tree. One node per decision/action, linked by parent ids."""
    run_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)


class Executor(Protocol):
    """The execution capability — the ONLY sanctioned path to a side effect.

    Decision != Action: control-plane components return Decisions (and other
    contracts); they never implement this. Only the execution plane turns a
    Decision into an action, and only through this interface.
    """

    def execute_tool(self, call: ToolCall) -> ToolResult: ...

    def run_model(self, request: ModelRequest) -> ModelResponse: ...
