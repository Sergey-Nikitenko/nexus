# Nexus — Blueprint

> **Nexus is not an LLM wrapper. It is an observable execution system for AI agents.**

| Word | What it means here |
|---|---|
| Models | reasoning engines |
| Knowledge | information |
| Tools | capabilities |
| Orchestrator | execution |
| Router | resource selection |
| Policy | authority |
| Evaluator | verification |
| State | continuity |
| Events | history |
| Tracer | observability |
| Dashboard | transparency |
| Workers | scalability |
| MCP | interoperability |

## One principle

**Build the contracts before the implementations.** Every component is replaceable.
If you can swap Qdrant → pgvector, Ollama → OpenAI, Redis → another queue, or
FastAPI → another API layer *without rewriting the orchestrator*, it worked.

Components never touch another component's internal state. They communicate by
**interface** (`component → interface → component`) or by **event**
(`component → event → consumer`).

## Control plane vs execution plane (the distinction that matters)

The directory split (`control/` vs `execution/`) is secondary. The rule is:

- **Control plane** answers *"what SHOULD happen?"* — router, policy, evaluator,
  tool registry, model registry. These are **pure**: they take inputs and return
  a contract (`Decision` / `Verdict` / `Evaluation`). No state mutation, no tool
  calls, no network.
- **Execution plane** answers *"what DID happen?"* — orchestrator, workers, queue,
  tool execution, verification. These perform the actions and emit events.

Data flow:

    control  --Decision-->  execution
    execution --Event---->  observability/control

**The router proposes; the policy disposes.** The router never bypasses policy,
and control-plane components never execute — they only decide.

Side effects flow through one sanctioned interface — the `Executor` capability
(`execute_tool`, `run_model`). **Decision ≠ Action**: the control plane can
*request*; only the execution plane can *cause*.

**Phase 1 is frozen** as of this contract:

    User Request → Router → Decision → Policy → DENY / APPROVAL_REQUIRED / ALLOW → Execution

No new abstraction gets added unless a golden/conformance test demands it.

## The contracts (core/contracts.py)

`Task` → `Run` → `Step`, plus `State`, `Event`, `Decision`, `ToolCall`,
`ToolResult`, `RetrievalResult`, `ModelRequest`, `ModelResponse`,
`ApprovalRequest`, `Evaluation`, `Trace`.

The event envelope is deliberately boring:

```json
{
  "event_id": "evt_123",
  "event_type": "tool.call.completed",
  "timestamp": "...",
  "run_id": "run_456",
  "task_id": "task_789",
  "parent_event_id": "evt_122",
  "component": "mcp",
  "status": "success",
  "payload": {}
}
```

Boringness is the feature: every subsystem can consume it.

## Phases

### Phase 0 — Foundation
Repository, configuration, structured logging, event model, state model, test
harness. Layout: `core/{contracts,events,state,tasks,errors,ids}`.

**Done when:** a fake agent executes a multi-step task entirely against mocks,
producing a complete trace and recoverable state.

### Phase 1 — Control plane
Model registry → Router → Policy → Evaluator → Tool registry.

- **Policy is independent of the LLM.** The model can never talk itself into
  permission. `DENY / ALLOW / APPROVAL_REQUIRED`.
- The **router returns a decision object**, not just a model name:

```json
{
  "model": "ollama/qwen",
  "provider": "local",
  "reasons": ["task requires coding", "repository is private", "local model satisfies context"],
  "constraints": {"max_tokens": 4096, "timeout_ms": 30000}
}
```

### Phase 2 — Knowledge and Memory (two different things)
- **Knowledge:** ingestion → document store → embeddings → retrieval.
  `Retriever` abstraction = `VectorRetriever` + `KeywordRetriever` +
  `MetadataFilter` + `Reranker`. The orchestrator only calls
  `retriever.search(query, filters)`.
- **Memory:** episodic, semantic, user/task state. "Memory" is not a synonym
  for "vector database."

### Knowledge identity & version semantics

A chunk's **identity** is `(source, document, location)`. Two chunks with the
same identity but a different version are the *same knowledge at different
points in time* — the key to stale-embedding detection, updates, and
reproducible runs.

| source | document | location | version |
|---|---|---|---|
| `github` | repo/path | line range / section | commit SHA |
| `web` | canonical URL | heading / section | content hash or crawl timestamp |
| `filesystem` | relative path | page / line range | content hash |

**Version semantics.** *"current"* is scoped to `(source, document, location)`.
Different knowledge identities may legitimately have different current versions.
Staleness is **never** inferred globally — a store that collapses to a single
global version is an architectural regression, not an optimization.

### Store lifecycle semantics (AD-006)

Ingestion never deletes; it only ever supersedes. The rules, so a retrying
backend and a replacing store behave identically:

- **Replacement** — adding an existing identity with a *new* version makes that
  version **current**; the old version is retained for audit, never surfaced by
  search.
- **Idempotency** — re-adding the *same* `(identity, version)` is a no-op
  (deterministic stable id ⇒ replace, not duplicate).
- **Source disappearance** — a source absent from a later ingest is **not**
  auto-deleted; removal is an explicit operation, never an implicit side effect.
- **Stale deletion** — stale versions are retained; pruning is a separate,
  explicit concern, not something search or ingest does on its own.
- **Metadata-only change** — metadata is *not* identity. A metadata change
  without a version bump updates the record in place (same id, same version).
- **Concurrent writers** — last-write-wins for "current", per identity. No
  merge: ordering is the only tiebreak, and it is atomic per record.

### Phase 2.2 — ingestion as a boundary

Each stage is independently replaceable behind a contract:

    Source → DocumentLoader → Document → Parser → Chunker → Chunk → Embedder → Vector → KnowledgeStore

No stage knows the concrete provider. The reference implementation
(`knowledge/inmemory.py`) is stdlib-only; a production adapter (Chroma, OpenAI
embeddings) is just another implementation of the same contracts.

**Update semantics:** a chunk's identity is `(source, document, location)`. The
store retrieves only the **current** version and never surfaces stale ones — so
the model never receives v1 and v2 at once without knowing why.

**Phase 2 acceptance (frozen):** *Knowledge and memory are replaceable,
persistent capabilities exposed through stable contracts; provenance, identity,
versioning, filtering, and lifecycle semantics survive implementation changes
and process restarts.*

That claim is demonstrated, not asserted, by the progression that shipped:
reference implementation → second implementation → real source → persistent
store → restart → idempotent ingestion → memory → hybrid retrieval → Chroma
(implementation #3). Chroma passes the conformance suite **unchanged** — the
anticlimax is the point. The stdlib implementation (`knowledge/inmemory.py`) is
**permanent** — the reference implementation and the fast test fixture — never
disposable scaffolding.

### Phase 2.5 — memory as the sequence plane

Knowledge is the *content* plane (chunks of documents, identity + version);
memory is the *sequence* plane (what did we attempt, and how did it end). The
two are different things — "memory" is not a synonym for "vector database."

A finished run is projected — deterministically, no LLM summarization yet —
into an **Episode**:

    Run + events + evaluation  ->  Episode  ->  EpisodeStore  ->  retrieve

- **Episode** (`core/contracts.py`): episode_id / task_id / summary / outcome /
  relevant_entities / timestamp / provenance. Provenance keeps the run_id and
  the evaluation evidence, so a retrieved memory is self-describing.
- **Extraction is deterministic**: the same run always projects to the same
  summary / outcome / entities / provenance. episode_id and timestamp are the
  only fresh-per-record fields.
- **Outcome is derived from events** (run.completed → success, run.failed →
  failed), never from the model's opinion of itself.
- **`memory/` is a top-level package** (not under `knowledge/`): it imports core
  only. The reference store ranks by keyword overlap — no embeddings yet; a
  semantic-recall adapter is a later implementation behind the same contract.

**Phase 2.5 acceptance:** *Memory is the sequence plane — deterministic and
replaceable without touching the orchestrator.*

### Phase 2.6 — hybrid retrieval (fusion, not replacement)

The orchestrator calls one method — `retriever.search(query, filters)` — and
gets one `RetrievalResult`, whether one source or many produced it. A hybrid
retriever fans the query out to its candidate sources (vector + keyword + ...),
merges their results **by chunk identity**, and reranks the union:

    query ──► vector source ──┐
    query ──► keyword source ─┼─► merge (identity) ─► rerank ─► RetrievalResult
    query ──► future source ──┘

- **Fusion is by identity**, not by index: a chunk both sources found surfaces
  *once* — the same `(source, document, location, version)` discipline as the
  store.
- **The reranker is an internal stage**, deliberately absent from the Retriever
  contract. Callers never touch it; swapping it changes only ranking, never the
  boundary. (The contract stays `search(query, filters) -> RetrievalResult`.)
- **The metadata filter is a named stage**, applied at the boundary even if a
  source forgot to.
- A real semantic source (Chroma + embeddings) drops in as one more entry in
  `sources` — nothing else moves. Hybrid retrieval is an *implementation
  detail*, not an interface.

**Phase 2.6 acceptance:** *Replacing a single retriever with a hybrid changes
nothing for the caller — same contract, same result shape, reranking hidden.*

### Phase 3 — Execution plane
The loop, with a REPLAN branch and hard budgets:

```
plan → retrieve → act → verify → PASS → done
                              └→ FAIL → replan
```

Budgets: `max_steps`, `max_retries`, `max_cost`, `max_execution_time`.

Task queue + workers are first-class from day one (even one worker), so
multi-worker in Phase 6 is not a redesign.

### Phase 3.1 — execution contracts + the reference executor

The `Executor` capability (already a Phase 0 contract) is the ONLY sanctioned
path to a side effect: `execute_tool(ToolCall) -> ToolResult` and
`run_model(ModelRequest) -> ModelResponse`. Phase 3.1 lands its reference
implementation — a deterministic, side-effect-free `FakeExecutor` — so the
*execution semantics* are provable before any real side effect (subprocess,
network, MCP, model SDK) exists.

**The invariant (AD-009).** Every externally observable execution step emits an
event BEFORE it is considered complete:

    ToolCall ──► tool.requested ──► execute ──► ToolResult ──► tool.completed
    ModelRequest ──► model.requested ──► run ──► ModelResponse ──► model.completed

`InstrumentedExecutor` (an `Executor` wrapper) enforces it: the `*.requested`
event fires first, unconditionally; the `*.completed` event fires only on
return. If the inner executor raises, the log shows `*.requested` with no
`*.completed` — the step was never complete. That is what makes "kill a worker
at any point and ask what Nexus knows happened" answerable: the event/state
foundation from Phase 0 finally gets exercised by real execution.

**Phase 3.1 acceptance:** *A tool/model step is observable in the event log
before it is complete — and a step that never returns is observably incomplete.*

### Phase 3.2 — subprocess tool execution (the first real Executor)

`ToolCall` names a LOGICAL tool ("formatter"); it never names an executable. The
execution-layer registry maps that name to a `SubprocessSpec` (argv template +
limits), so the model is never the authority that chooses an arbitrary
executable. The path is:

    CONTROL → ToolCall → Executor → tool registry → subprocess adapter → OS process

- **`SubprocessToolExecutor`** runs the spec as an argv list with `shell=False`
  — no shell interpretation, no `shell(command_from_model)`.
- **Failure semantics (AD-010):** an *expected* tool outcome (non-zero exit,
  timeout) is a `ToolResult(success=False, error=…)`; a *launch* failure
  (unknown tool, missing executable, malformed spec) raises. The former is data
  about the tool; the latter is a bug in the system.
- **Timeout is contract semantics** (declared on the spec): a non-exiting
  process is killed and reported, never allowed to hang Nexus.
- **Output is bounded** (spec-declared cap): no unlimited stdout/stderr into an
  event or trace; truncation is explicit.
- **Only contracts cross the boundary**: no Popen, pipe, or raw return code
  escapes — the caller gets a `ToolResult` of plain, serializable data.

`FakeExecutor` stays permanent as the reference and fast test fixture; the
Subprocess adapter and the future MCP adapter both produce the same `ToolResult`
behind the same `Executor` protocol.

**Phase 3.2 acceptance:** *A real subprocess executor satisfies the execution
semantics FakeExecutor established, without changing Executor, the orchestrator,
or the control plane.*

### Phase 3.3 — model execution (provider → Nexus, offline)

`ModelRequest -> Executor.run_model() -> ModelResponse`, with one real adapter
(`SubprocessModelExecutor`, a local process speaking JSON on stdin/stdout) and
`FakeExecutor` kept permanent as the reference. The model side proves the same
boundary pattern 3.2 did for tools.

- **`ModelResponse` is Nexus-shaped, not provider-shaped (AD-012).** The adapter
  translates the provider and drops the rest: `usage.prompt_tokens` becomes
  `tokens_in`, `completion_tokens` becomes `tokens_out`, and `finish_reason` /
  `system_fingerprint` / the `usage` dict never reach the caller. `ModelResponse`
  = content + model identity + usage + execution metadata, plus the
  `success`/`error` status pair it shares with `ToolResult`.
- **Failure taxonomy (AD-010's model twin):** a valid response is `success=True`;
  a provider rejection (error payload, non-zero exit, malformed response) is
  `ModelResponse(success=False, error=…)`; a launch/config failure (no model,
  missing executable) raises.
- **Request identity:** `ModelRequest` carries a `request_id`, so
  `model.requested` and `model.completed` unambiguously belong to the same
  run/step — even when one step makes several model calls.
- **Bounded output:** response content is capped (spec-declared), so a runaway
  provider response cannot become an enormous event/trace payload.
- **Deterministic test path:** the golden test drives a local fake model process
  — no live cloud API is ever required to make `py scripts/check.py` pass.

**Phase 3.3 acceptance:** *A real model adapter satisfies the semantics
FakeExecutor established, and the caller only ever sees a Nexus-shaped
ModelResponse — never a provider object.*

### Phase 3.4 — the orchestrator (the central nervous system)

The first composition of everything built so far:

                 ORCHESTRATOR
                      │
              ┌───────┼───────┐
              ▼       ▼       ▼
          Retriever  Executor  State
              │       │        │
              │       ▼        │
              │  Model / Tool  │
              └───────┴────────┘
                      │
                   Events

- **The orchestrator owns sequencing and correlation; capabilities own
  execution (AD-013).** It may call `retriever.search`, `executor.run_model`,
  `executor.execute_tool`, `policy.decide_tool`, `state`, `emit` — never
  `subprocess.Popen`, `open`, `requests.get`, a vector store, a model SDK, or an
  MCP client. `tests/conformance/test_orchestrator_purity.py` enforces this with
  an AST gate on `execution/orchestrator.py`.
- **The deterministic loop:** `Task → plan → retrieve → model → [tool] → model →
  verify → answer`. No replanning, retries, or real verification yet (that's
  3.5); 3.4 establishes *coordination semantics*.
- **A model SUGGESTS a tool; the orchestrator DECIDES.** `ModelResponse.tool_calls`
  is structured tool intent (Nexus `ToolCall` contracts, not provider-shaped).
  The orchestrator runs each suggestion through the Phase 1 policy —
  `DENY / APPROVAL_REQUIRED / ALLOW` — so `Model → Tool` never bypasses
  `Router → Policy → Execution`. The golden test proves a READ tool executes, a
  WRITE tool is approval-gated, and a DESTRUCTIVE tool is denied, all inside the
  loop.
- **`PolicyVerdict` moved to core** — it is a contract (like `Decision`/`Risk`)
  that crosses control → execution; the policy *engine* stays in control/. That
  is what keeps `execution/` importing core only, per the layer boundaries.
- **Recovery reconnects:** the run returns both its live `RunState` and its event
  log, and the golden test asserts `reconstruct(events) == live_state` — the
  Phase 0 crash-recovery guarantee, now exercised by a real loop.

**Phase 3.4 acceptance:** *Given deterministic capabilities, Nexus coordinates a
complete multi-step task, emits a causally ordered trace, preserves recoverable
state, and never performs capability work itself.*

### Phase 3.5 — verification + bounded replanning (the controlled feedback loop)

The loop gains a feedback branch:

    plan → retrieve → act → verify
                            ├─ PASS ──────────────► answer
                            └─ FAIL (replan_required)
                                    └─► run.replanned ─► act again (bounded)

- **The evaluator is not another executor (AD-016).** It observes and judges —
  `evaluator.evaluate(...) -> Evaluation` — and the orchestrator interprets the
  verdict. The control-plane purity gate now also bans `core.state`, so the
  evaluator can neither execute a capability nor mutate execution state.
- **`Evaluation` carries a structured verdict:** `passed` / `reason` /
  `replan_required` (plus the Phase 1 `checks` evidence). `reason`/`evidence`
  are data, never an evaluator-specific object.
- **Verification is explicit (AD-014):** `tool succeeded != task succeeded`. A
  tool can return success while the attempt still fails the task — the two
  propositions are decoupled, and the golden test pins it (attempt 1's tool
  succeeds, the evaluation still FAILs).
- **Replanning is bounded and event-sourced (AD-015):** `max_replans` is part of
  the plan/run state; exhaustion is a terminal `FAILED` outcome, never an
  infinite loop. Every attempt's `evaluation.completed` and every
  `run.replanned` is an event, and `RunState.reconstruct` reproduces the exact
  attempt/replan trail.
- **Policy stays authoritative across replans:** each newly proposed tool
  re-enters the same `DENY / APPROVAL_REQUIRED / ALLOW` gate; replanning changes
  the plan, never the authority (a separate golden test proves a DESTRUCTIVE
  tool is denied on every attempt).

**Phase 3.5 acceptance:** *One deterministic task shows attempt-1 FAIL →
replan → attempt-2 PASS → completed, while no capability bypasses the Executor,
every proposed tool still passes policy, the replan count is bounded, all
attempts are observable, and state reconstructed from events equals live state.*

### Phase 3.6 — durable task execution (queue + worker)

The execution plane gains a durable boundary between "accepted" and "run":

    Task → Queue → Worker → Orchestrator → Events → State

- **Ownership is a durable event (AD-017).** `TaskQueue.claim` emits
  `task.claimed` (with `worker_id`); `enqueue` emits `task.queued`; the worker's
  ack emits `task.completed` / `task.failed`. A worker never owns execution
  solely in memory — a crash leaves "last durable event = X", never "the worker
  died". `TaskState` is the projection of those `task.*` events.
- **At-least-once, never exactly-once (AD-018).** A claimed-but-uncompleted task
  stays `CLAIMED` and is recoverable; re-running may execute a tool twice. That
  is explicit and observable — the same idempotency discipline the knowledge
  store already has (stable chunk ids), applied here to delivery semantics.
- **Queue / worker / orchestrator stay separate:** the queue does enqueue/claim/
  ack (SQLite, atomic single-`UPDATE` claim); the worker composes queue +
  orchestrator and knows nothing about retrieval or providers (AST-gated); the
  orchestrator is unchanged and knows nothing about how tasks are queued.
- **A `DurableEventBus`** persists every event to a SQLite log, so `RunState`
  and `TaskState` reconstruct from a fresh connection — the whole event/state
  system is now crash-surviving, not just in-memory.

**Phase 3.6 acceptance:** *the queue accepts a Task, the worker claims it, runs
the unchanged Orchestrator, ownership/completion/failure are durable and
observable, state reconstructs from the log, the worker has no provider
knowledge, and duplicate-delivery semantics are explicit.*

### Phase 3.7 — crash recovery (failure injection, not feature expansion)

3.6 built the machinery; 3.7 proves it under actual process death.

    enqueue -> w1 claims -> orchestrator runs -> KILL PROCESS
    -> fresh process/connection -> reconstruct -> recover abandoned CLAIMED task
    -> w2 claims -> orchestrator re-runs -> task.completed

- **Recovery is a lease rule on durable evidence (AD-019).** A claim records
  `claimed_at`; `CLAIMED + lease expired → recoverable`. `RecoveryManager`
  requeues such tasks based purely on status + `claimed_at` — never on knowing
  which worker died. The clock is injectable and deliberately simple.
- **Recovery belongs to queue infrastructure, never the orchestrator (AD-019).**
  The orchestrator has no idea whether it was started normally or because a
  previous worker died — it just runs the task. Recovery is an execution
  concern, not an agent-intelligence concern.
- **At-least-once is demonstrated, not asserted.** Case B kills the worker
  *after* the tool's side effect, before the completion event: the tool
  executes again on re-run (side effect count 2), exactly as AD-018 documents.
- **Reconstruction survives death:** `TaskState.reconstruct(all_events)` and
  `RunState.reconstruct(run, w2_events)` reproduce the recovered state from the
  durable log, closing the loop Phase 0 → 3.5 → 3.6 → 3.7.

**Phase 3.7 acceptance:** *a task, under real worker death, is never silently
lost — durable evidence identifies it, the queue recovers it, a fresh worker
finishes it, and the event log reconstructs the recovered state.*

### Phase 3.8 — MCP as another tool transport (implementation #3)

    ToolCall -> Executor -> (Subprocess adapter | MCP adapter) -> ToolResult

- **The adapter owns MCP's vocabulary (AD-020).** `integrations/mcp.py` speaks
  JSON-RPC 2.0 over stdio (`initialize` / `tools/list` / `tools/call`) and
  translates: `tools/list` → `ToolDefinition` (a Nexus contract), `tools/call`
  result → `ToolResult`. Nothing MCP-shaped crosses the boundary — the
  provider-specific envelope (`isError`, `content` blocks, `_meta`,
  `structuredContent`) is dropped.
- **MCP failures follow AD-010:** an MCP `isError` result is an expected failure
  (`ToolResult(success=False, …)`); transport/config/launch failure raises. No
  third category is invented.
- **Instrumentation is universal:** the MCP adapter runs through the same
  `InstrumentedExecutor`, so `tool.requested → MCP call → tool.completed` holds,
  and a transport exception leaves an observably incomplete step.
- **Policy stays above MCP:** the path is model → ToolCall → policy → Executor →
  MCP, never model → MCP client → tool. The golden test proves a DENIED tool
  never reaches the MCP server.
- **No MCP imports above the adapter:** `mcp` is now in the provider-leakage
  gate, confined to `integrations/`.
- **Progression complete:** FakeExecutor → SubprocessToolExecutor →
  McpToolExecutor — three implementations, one contract. The end-to-end golden
  task runs the unchanged Orchestrator with the MCP executor, then with the fake
  executor: the only thing that changes is dependency injection.

**Phase 3.8 acceptance:** *an MCP-backed tool executes inside the unchanged
orchestrator; discovery yields Nexus contracts, failures follow AD-010, policy
gates every call, and nothing provider-shaped leaks above the adapter.*

### Phase 4 — Surface
API (REST + WebSocket), dashboard (run view, trace tree, approval queue, *Why*
panel), CLI. The dashboard is driven directly off the decision objects.

Sequencing: 4.1 runtime composition → 4.2 REST `/ask` + status → 4.3 trace
projection → 4.4 WebSocket event stream → 4.5 approval lifecycle → 4.6 dashboard
→ 4.7 CLI. (Not seven commits — these are the conceptual boundaries.)

### Phase 4.1 — runtime composition (the composition root)

One root wires the real system together:

    NexusRuntime
        ├── Control (router, policy, evaluator)
        ├── Execution (worker, queue, executor)
        ├── Knowledge (retriever, store)
        └── Durable events -> State

- **Applications compose Nexus; components never discover each other through
  global state (AD-021).** `NexusRuntime` takes everything as constructor
  arguments and is the ONLY place that wires orchestrator + worker + queue + bus.
- **The application surface is uniform:** `ask` (async, returns a task id),
  `run_one`, `task`, `events` — identical whether the injected components are
  fakes (FakeExecutor, reference retriever, SQLite) or real (MCP + model adapter,
  Chroma, durable events, real workers). The golden test runs both and asserts
  only the components differ.
- **The surface is a leaf:** nothing below `apps/` may import `apps/` —
  execution never depends on the surface. Enforced by
  `tests/conformance/test_surface_boundary.py`.

**Phase 4.1 acceptance:** *one runtime composes fake or real components behind
the same application surface, with no global-state discovery, and the execution
plane remains surface-agnostic.*

### Phase 5 — Hardening
- **Secrets:** never enter prompts, traces, or model-visible logs.
- **Tool execution:** timeouts, resource limits, filesystem boundaries,
  network restrictions, permission checks (a sandbox for shell/code execution).
- **Idempotency:** `idempotency_key` on every externally mutating operation,
  so a crash-and-retry can't create two PRs.

### Phase 6 — Differentiators (in this order)
1. **Reproducible runs** — from a `run_id`, reconstruct models, prompts,
   retrieval, tool calls, config, decisions, events.
2. **Multi-worker** — local / cloud / GPU workers behind the task queue.
3. **Nexus as an MCP server** — `nexus.ask`, `nexus.run_task`,
   `nexus.search_knowledge`, `nexus.get_trace`, `nexus.approve`; another agent
   can drive Nexus as a service.
4. **Multi-agent** — supervisor → research/coding/review agents, all on the
   same event/state/policy infrastructure.

## Golden tasks

20–50 deterministic tasks that must pass after every architectural change:

```
G001 retrieve information from a document
G002 answer using two sources
G003 use a read-only GitHub tool
G004 attempt a prohibited write → denied
G005 request approval for a permitted write
G006 choose local model for a private task
G007 fall back to cloud model
G008 recover after tool failure
G009 recover after worker crash
G010 detect failed verification and replan
```

This is an engineering regression suite, not a marketing score.

## Architectural decisions (AD)

Decisions whose wrong interpretation could cause regressions. Not a changelog.

- **AD-001** — Knowledge versioning is per identity `(source, document, location)`, never global.
- **AD-002** — Retriever implementations may rank differently; only the contract must match.
- **AD-003** — Retrieved provenance (source/document/location/version/metadata) crosses the boundary with the chunk.
- **AD-004** — Provider objects never cross the boundary; adapters produce contracts.
- **AD-005** — Control plane is pure (decides, never executes); side effects require the `Executor` capability.
- **AD-006** — Store lifecycle: ingestion supersedes, never deletes; last-write-wins per identity; idempotent re-add; metadata is not identity.
- **AD-007** — Memory is the sequence plane, separate from knowledge: a finished run projects into an Episode deterministically (no LLM); outcome derives from events.
- **AD-008** — The reranker is internal to a Retriever implementation, never part of the Retriever contract (`search(query, filters) -> RetrievalResult`).
- **AD-009** — Every externally observable execution step emits an event *before* it is considered complete (`*.requested` first, `*.completed` only on return); a step that never returns is observably incomplete.
- **AD-010** — Execution failure semantics: an *expected* tool outcome (non-zero exit, timeout) is `ToolResult(success=False, …)`; a *launch* failure (unknown tool, missing executable, malformed spec) raises. Expected failures are data; launch failures are bugs.
- **AD-011** — The model is never the authority for executables: `ToolCall` names a logical tool, and only the execution-layer registry maps it to an executable. No arbitrary shell execution by default.
- **AD-012** — `ModelResponse` is Nexus-shaped, not provider-shaped: the adapter translates provider → Nexus and drops provider-specific concepts (`finish_reason`, `system_fingerprint`, the `usage` dict); the caller only ever sees Nexus semantics.
- **AD-013** — The orchestrator owns sequencing and correlation; capabilities own execution. It composes injected capabilities (retriever, executor, policy, tools, state, bus) and never performs capability work itself (no subprocess/file/network/vector-store/model-SDK/MCP).
- **AD-014** — Verification is explicit: `tool succeeded != task succeeded`. The evaluator returns an `Evaluation` (`passed` / `reason` / `replan_required`), and the orchestrator interprets it — the evaluator never decides what happens next.
- **AD-015** — Replanning is bounded and event-sourced: `max_replans` is run/plan state, exhaustion is a terminal outcome, and every attempt/replan is an event reconstructible into the same attempt trail.
- **AD-016** — The evaluator observes and judges; it never executes a capability or mutates execution state (returns `Evaluation`; the orchestrator coordinates).
- **AD-017** — Task ownership is a durable event (`task.claimed` with `worker_id`), never an in-memory flag; the event/state log is the source of truth for the task lifecycle (`queued → claimed → completed/failed`).
- **AD-018** — Task delivery is at-least-once, never exactly-once; duplicate tool execution after recovery is explicit and observable, and idempotency/recovery is applied where required (as with stable chunk ids).
- **AD-019** — Recovery is a lease rule on durable evidence (`CLAIMED` + expired `claimed_at` → recoverable), and it lives in queue infrastructure — never the orchestrator, which has no idea whether it is running normally or after a previous worker died.
- **AD-020** — MCP is implementation #N: the adapter owns MCP's vocabulary (JSON-RPC, `tools/list`, `tools/call`); `ToolCall`/`ToolResult`/`ToolDefinition` stay Nexus contracts, and MCP libraries are confined to the integration layer.
- **AD-021** — Applications compose Nexus; Nexus components do not discover each other through global state. The surface observes and commands through contracts/events, and execution never depends on the surface.

## Contract conformance: MUST MATCH vs MAY DIFFER

| MUST MATCH (the boundary) | MAY DIFFER (implementation detail) |
|---|---|
| contract shape | ranking |
| identity | scoring |
| provenance | chunk boundaries |
| version | embedding strategy |
| metadata | retrieval algorithm |
| filters | |
| error semantics | |

A conformance test that asserts a MAY-DIFFER property turns the abstraction into
a disguised implementation spec. Don't.

## The progression (every capability)

```
Contract → stdlib reference → second independent implementation → conformance → production adapter
```

Chroma is **implementation #3** — landed in `knowledge/chroma.py` — and it is not
an architectural event: it satisfies the same `KnowledgeStore` protocol, and the
conformance suite passed with it **unchanged**. If adding it had required
modifying the orchestrator, the boundary — not the new implementation — would be
what was wrong.

## Repository layout

```
nexus/
  apps/        api, worker, dashboard, cli
  core/        contracts, events, state, tasks, errors, ids
  control/     router, policy, evaluator, models, tools
  knowledge/   ingestion, parsing, chunking, embeddings, retrieval
  memory/      episodes, extraction, episodic recall
  execution/   orchestrator, planning, workers, queue, verification
  integrations/ mcp, ollama, openai, github
  observability/ tracing, events, metrics
  tests/       unit, integration, golden, fixtures
  deploy/      docker, oracle, nginx
  docs/        architecture, decisions, roadmap, api
```

The separation that matters: **contracts, control, knowledge, execution,
integrations** — not a flat pile of files.

## Architecture conformance — principles → their enforcing tests

Every architectural principle has a test that makes violating it a failing build.
The suite answers two questions: *"does Nexus work?"* (golden tasks) and
*"does Nexus still conform to the architecture?"* (conformance tests).

| Principle | Enforcing test |
|---|---|
| Core is dependency-light (stdlib only) | `tests/conformance/test_layer_boundaries.py` |
| Nothing imports upward (control/execution → core only) | `tests/conformance/test_layer_boundaries.py` |
| Control plane is pure (decides, never executes/emits) | `tests/conformance/test_control_plane_purity.py` |
| Side effects require the `Executor` capability (Decision ≠ Action) | `tests/conformance/test_control_plane_purity.py` |
| No provider leakage (core consumes contracts, adapters produce them) | `tests/conformance/test_no_provider_leakage.py` |
| Retriever boundary holds across implementations (same contract, no ranking assumption) | `tests/conformance/test_retriever_contract.py` |
| Persisted store is observationally equivalent across a restart | `tests/conformance/test_persistence_contract.py` |
| Chroma (implementation #3) persists across a real process restart, contract unchanged | `tests/conformance/test_chroma_persistence.py` |
| Ingestion is idempotent (ingest x3 = one logical chunk) | `tests/golden/test_phase2_idempotency.py` |
| Memory is the sequence plane (deterministic run→episode projection, no LLM) | `tests/golden/test_phase2_memory.py` |
| Hybrid retrieval is an implementation detail (one contract, reranker internal) | `tests/golden/test_phase2_hybrid.py` |
| Execution steps emit an event before completion (requested first, completed only on return) | `tests/golden/test_phase3_executor.py` |
| Executor boundary holds across implementations (only serializable contracts cross) | `tests/conformance/test_executor_boundary.py` |
| Real subprocess execution: bounded output, timeout, expected-fail-as-ToolResult, no shell | `tests/golden/test_phase3_subprocess.py` |
| Model execution: provider→Nexus translation, request identity, rejection-as-failure, bounded output | `tests/golden/test_phase3_model.py` |
| Orchestrator coordinates, never performs capability work (AST gate) | `tests/conformance/test_orchestrator_purity.py` |
| The full deterministic loop: Task→Answer, ordered trace, recoverable state, policy authoritative | `tests/golden/test_phase3_orchestrator.py` |
| Verification + bounded replanning: FAIL→replan→PASS, tool-success≠task-success, reconstructible attempts | `tests/golden/test_phase3_replan.py` |
| Policy stays authoritative across replans (DENY persists on every attempt) | `tests/golden/test_phase3_replan_policy.py` |
| The worker has no provider/retrieval knowledge (only composes queue + orchestrator) | `tests/conformance/test_worker_purity.py` |
| Durable task execution: ownership/completion durable + observable, state reconstructible from the log | `tests/golden/test_phase3_worker.py` |
| Crash recovery under real process death (two failure points, lease-based requeue, reconstruct) | `tests/golden/test_phase3_recovery.py` |
| MCP adapter boundary: only Nexus contracts cross (provider-specific fields dropped, AD-010) | `tests/conformance/test_mcp_boundary.py` |
| MCP end-to-end: unchanged orchestrator, policy above MCP, DI-only swap | `tests/golden/test_phase3_mcp.py` |
| The surface is a leaf; execution never depends on it | `tests/conformance/test_surface_boundary.py` |
| Runtime composition: applications compose Nexus, no global-state discovery, uniform surface | `tests/golden/test_phase4_runtime.py` |
| Router never bypasses policy | `tests/golden/test_phase1_composition.py` |
| State is recoverable (a projection of events) | `tests/golden/test_phase0_foundation.py` |
| The boring event envelope (one uniform shape) | enforced by the `Event` dataclass itself |

As each phase ships, its architectural principles get added here with their test.
A principle without a test is not a principle — it is an intention.

A conformance test exists only because there is an architectural property to
preserve — never because a code pattern "feels nicer." The suite is a map of
intent, not a second linter.
