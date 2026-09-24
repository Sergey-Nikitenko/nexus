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

**Phase 2 acceptance:** *Knowledge can be replaced without changing the
orchestrator.* That is the victory — not "Chroma works." The stdlib
implementation (`knowledge/inmemory.py`) is **permanent** — the reference
implementation and the fast test fixture — never disposable scaffolding.

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

### Phase 4 — Surface
API (REST + WebSocket), dashboard (run view, trace tree, approval queue, *Why*
panel), CLI. The dashboard is driven directly off the decision objects.

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

Chroma, when it arrives, is **implementation #3**, not an architectural event.
If adding it requires modifying the orchestrator, the boundary — not the new
implementation — is what's wrong.

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
| Ingestion is idempotent (ingest x3 = one logical chunk) | `tests/golden/test_phase2_idempotency.py` |
| Memory is the sequence plane (deterministic run→episode projection, no LLM) | `tests/golden/test_phase2_memory.py` |
| Hybrid retrieval is an implementation detail (one contract, reranker internal) | `tests/golden/test_phase2_hybrid.py` |
| Router never bypasses policy | `tests/golden/test_phase1_composition.py` |
| State is recoverable (a projection of events) | `tests/golden/test_phase0_foundation.py` |
| The boring event envelope (one uniform shape) | enforced by the `Event` dataclass itself |

As each phase ships, its architectural principles get added here with their test.
A principle without a test is not a principle — it is an intention.

A conformance test exists only because there is an architectural property to
preserve — never because a code pattern "feels nicer." The suite is a map of
intent, not a second linter.
