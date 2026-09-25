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
    CLAIMED = "claimed"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class PolicyVerdict(str, Enum):
    """A policy decision's outcome — a contract, like `Decision`, that crosses the
    control -> execution boundary. The policy ENGINE lives in control/; this enum
    is what the execution plane consumes."""
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


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
    user: str = ""              # user_id — who submitted this task (AD-035)
    agent: str = ""             # agent_id — which ROLE this task belongs to (AD-034)
    parent_run_id: str = ""     # the delegating run, if this task was delegated


@dataclass
class Claim:
    """A worker's exclusive lease on ONE claim-generation of a task.

    Ownership is generation-specific, not merely worker-specific (AD-028): a
    worker may legitimately re-claim a later generation, so a terminal transition
    must prove it holds the exact (worker_id, generation) it acquired — not just
    that it is "some worker" named by worker_id.
    """
    task: Task
    worker_id: str
    generation: int


class ReplayStatus(str, Enum):
    """A run's reproducibility, DERIVED from explicit conditions — never a
    subjective assessment (AD-029)."""
    REPRODUCIBLE = "reproducible"
    REPRODUCIBLE_WITH_DIFFERENCES = "reproducible_with_differences"
    NON_REPRODUCIBLE = "non_reproducible"


@dataclass
class UserIdentity:
    """Who is interacting with Nexus — a stable logical identity (AD-035).

    No authentication or provider-specific fields: an API key, OAuth token,
    endpoint URL, or SDK object is a secret/config, never identity."""
    user_id: str = ""

    @property
    def key(self) -> str:
        return f"user/{self.user_id}"


@dataclass
class AgentIdentity:
    """Which agent ROLE is acting — a stable role/configuration identity (AD-035).

    Distinct from worker_id (who ran it), task_id (which task), run_id (which
    attempt), and ModelIdentity (which engine). The agent survives model
    replacement."""
    agent_id: str = ""
    role: str = ""
    version: str = "1"

    @property
    def key(self) -> str:
        return f"agent/{self.agent_id}@{self.version}"


@dataclass
class ModelIdentity:
    """Which logical model configuration participated (AD-035).

    Deliberately NOT the provider configuration — an API key, endpoint URL, SDK
    object, or instantiated client is an implementation detail, never identity.
    Equality is on (model_id, family, version) only, so a model swap changes the
    identity while the concrete SDK object behind it is irrelevant."""
    model_id: str = "unknown"
    family: str = ""
    version: str = "1"

    @property
    def key(self) -> str:
        return f"model/{self.model_id}@{self.version}"


@dataclass
class RunManifest:
    """The inputs that define a run, captured BEFORE execution (AD-029).

    The manifest answers "what configuration/snapshots defined this run?" —
    distinct from the event log, which answers "what actually happened?". Both
    together give reproducibility; the events alone do not. Every field is Nexus
    vocabulary — never a provider object or SDK config.
    """
    run_id: str
    task_id: str
    task_title: str
    knowledge: str      # knowledge snapshot identity/version
    policy: str         # policy identity/version
    model: ModelIdentity  # which logical model configuration participated
    tools: str          # tool registry identity/version
    router: str = ""    # router configuration (reserved until model selection)
    max_replans: int = 2
    user: UserIdentity = field(default_factory=UserIdentity)     # who (AD-035)
    agent: AgentIdentity = field(default_factory=AgentIdentity)  # which role (AD-034/035)
    parent_run_id: str = ""     # the delegating run (empty = top-level)


@dataclass
class ReplayReport:
    """The outcome of replaying a run against its manifest.

    `status` is DERIVED: REPRODUCIBLE iff the inputs match AND the semantic
    traces match; REPRODUCIBLE_WITH_DIFFERENCES iff an input changed (or the
    traces diverged); NON_REPRODUCIBLE iff a required input is missing.
    """
    status: ReplayStatus
    input_differences: list[tuple[str, str, str]] = field(default_factory=list)
    first_divergent_event: str | None = None
    event_count: tuple[int, int] = (0, 0)


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
    call_id: str = field(default_factory=lambda: new_id("call"))


@dataclass
class ToolResult:
    tool_call: ToolCall
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class ToolDefinition:
    """A discovered tool's Nexus-level definition — what an adapter produces and
    the registry consumes. Carries no provider objects (no MCP/OpenAI shapes)."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    """A retrieved chunk with full provenance, so a consumer never reaches back
    into the vector store to answer "where did this come from?"."""
    text: str
    source: str            # file path / URL / repo
    document: str          # parent document id or title
    location: str = ""     # section / line range within the document
    version: str = ""      # commit / version stamp
    relevance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)


@dataclass
class Episode:
    """One completed run, distilled into a retrievable memory record.

    Memory is the SEQUENCE plane — "what did we attempt, and how did it end" —
    as distinct from knowledge (the CONTENT plane: chunks of documents). An
    Episode is a deterministic projection of a run, not a raw dump of its event
    log: a future agent retrieves it to answer "has this been tried before?"
    """
    episode_id: str
    task_id: str
    summary: str
    outcome: str                      # "success" | "failed" | "unknown"
    relevant_entities: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=utcnow)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRequest:
    """A model request with its own identity, so `model.requested` and
    `model.completed` can unambiguously belong to the same run/step."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 4096
    timeout_ms: int = 30000
    request_id: str = field(default_factory=lambda: new_id("req"))


@dataclass
class ModelResponse:
    """Nexus-shaped, NOT provider-shaped. The adapter translates the provider
    (OpenAI/Ollama/local) into these Nexus semantics and drops the rest.

    `success`/`error` mirror ToolResult: a valid response is success=True; a
    provider rejection is success=False with an error (AD-010's model twin)."""
    model: str
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost: float = 0.0
    success: bool = True
    error: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    """A request for human authorization of ONE specific proposed tool call.

    Bound to its identity (task/run/tool/risk) so approving one action cannot
    authorize another. Single-use: pending -> approved/denied -> consumed."""
    approval_id: str
    task_id: str
    run_id: str
    tool_name: str
    risk: Risk
    status: str = "pending"  # pending / approved / denied / consumed / expired


@dataclass
class Evaluation:
    """A verification outcome — evidence, not an evaluator-specific object.

    `passed` / `reason` / `replan_required` are the structured verdict the
    orchestrator interprets; `checks` is the raw evidence (Phase 1). The
    evaluator OBSERVES and JUDGES; the orchestrator DECIDES what happens next."""
    checks: dict[str, Any] = field(default_factory=dict)
    passed: bool = True
    reason: str = ""
    replan_required: bool = False


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
