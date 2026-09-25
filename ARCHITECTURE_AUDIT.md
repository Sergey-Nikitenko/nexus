# Nexus — Architecture Audit (post-Phase-4)

Adversarial review of the system before Phase 5 (hardening). Five passes:
dependency graph, event taxonomy, state/event transitions, concurrency, and
public contracts. Findings are ranked; none are feature requests — they are the
risk list Phase 5 must decide against.

---

## 1. Dependency graph

Generated from the actual `import` statements (layer-level):

```
core/
  contracts.py, events.py, state.py        -> (stdlib only)

control/       evaluator, policy, router, tools -> core
knowledge/     hybrid, inmemory, keyword, retrieval -> core
memory/        contracts, inmemory -> core
execution/     approvals, durable, fake, instrumented, models,
               orchestrator, queue, subprocess, worker -> core
integrations/  mcp -> core
observability/ trace -> core

apps/          runtime -> control + core + execution
               cli -> core + observability
               http -> core
               ws -> observability
               dashboard -> (pure functions, no project imports)
```

**The intended shape holds exactly** — nothing below `apps/` imports upward, and
`apps/` is the only layer that composes control + execution + observability. The
AST gates (`test_layer_boundaries`, `test_surface_boundary`,
`test_http_boundary`, `test_trace_projection_purity`,
`test_control_plane_purity`, `test_orchestrator_purity`, `test_worker_purity`,
`test_no_provider_leakage`) already enforce this mechanically.

Two notes, not violations:
- `knowledge/chroma.py` imports `chromadb` (a provider) — correct, it is the
  adapter. `integrations/mcp.py` does **not** import the `mcp` SDK; it implements
  a minimal JSON-RPC client in stdlib. Both are fine; only the SDK choice differs.
- `execution/orchestrator.py` imports core + `execution.instrumented` only — the
  `PolicyVerdict` contract was moved to core (see AD) precisely so the
  orchestrator never imports `control`.

---

## 2. Event taxonomy

Inventory (who emits / durable? / reconstructible? / correlation):

- **`run.*`** (`started` / `completed` / `failed` / `replanned`) — orchestrator. Durable ✓ · reconstructible ✓ (RunState) · correlation: run_id, task_id.
- **`step.*`** (`started` / `completed` / `failed`) — orchestrator. Durable ✓ · reconstructible ✓ (RunState) · correlation: step_id.
- **`retrieval.*`** (`requested` / `completed`) — orchestrator. Durable ✓ · correlation: run_id, query.
- **`model.*`** (`requested` / `completed`) — InstrumentedExecutor. Durable ✓ · correlation: request_id.
- **`tool.*`** (`requested` / `completed`) — InstrumentedExecutor. Durable ✓ · reconstructible ✓ (trace) · correlation: **call_id**.
- **`policy.decision`** — orchestrator. Durable ✓ · reconstructible ✓ (trace) · correlation: tool, verdict, risk, executed, reason, call_id.
- **`approval.*`** (`required` / `granted` / `denied` / `consumed`) — ApprovalStore. Durable ✓ · reconstructible ✓ (ApprovalState) · correlation: approval_id, task_id, run_id.
- **`evaluation.completed`** — orchestrator. Durable ✓ · reconstructible ✓ (RunState) · correlation: attempt, passed, reason.
- **`task.*`** (`queued` / `claimed` / `waiting` / `requeued` / `completed` / `failed`) — queue. Durable ✓ · reconstructible ✓ (TaskState) · correlation: task_id, worker_id.

**Findings (resolved in Phase 5):**

1. ~~Four dead event types~~ — `TASK_CREATED` removed; `MODEL_SELECTED` /
   `MODEL_FALLBACK` reserved (Phase 6 model selection); `STEP_FAILED` is now
   emitted on normal exceptions. ✅ (#7)
2. ~~`tool.*` carry no per-call id~~ — `ToolCall.call_id` threaded through
   `tool.requested` / `tool.completed` / `policy.decision`; the projector pairs
   by call_id. ✅ (#3)
3. ~~Approval state not event-sourced~~ — `ApprovalState.reconstruct` added. ✅ (#4)
4. ~~`step.failed` never emitted~~ — a normal exception now reaches a terminal
   `step.failed`; process death still leaves no terminal event (that is how an
   interruption is detected). ✅ (#5)
5. ~~Double-recovery double-`requeued`~~ — `recover_abandoned` is now one atomic
   conditional `UPDATE … RETURNING`. ✅ (#6)


---

## 3. State / event transition graphs

### Task (queue/worker)
```
queued --claim--> claimed --complete--> done
                 |  \--fail--> failed
                 |  \--wait--> waiting_approval --requeue--> queued --> claimed ...
```
Every transition emits a `task.*` event and `TaskState.reconstruct` reproduces
the trail (verified in 3.6/3.7/4.5). **Sound.**

### Run (orchestrator)
```
run.started -> [step.started -> step.completed]* -> run.completed / run.failed
                                            (replan: run.replanned, more steps)
```
Reconstructible. **Sound** — a raising step now emits a terminal `step.failed`
(resolved, #5).

### Approval (store)
```
pending --approve--> approved --consume--> consumed
        \--deny--> denied
```
Each transition emits `approval.*`. **Event-sourced** — `ApprovalState.reconstruct`
reproduces the graph from events alone (resolved, #4).

---

## 4. Concurrency

**SQLite facts (resolved in 5.2):** each store (queue, approvals, durable events)
is a separate file. Each worker thread opens its OWN connection (thread-local,
owned by the store — `execution/sqlite.py`), configured deliberately:
`busy_timeout`, `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`.
`check_same_thread=False` remains only so `close()` can close connections opened
in (now-exited) worker threads — no connection is ever shared by two threads.

**What Nexus guarantees (5.2):**
- **Per-worker connections** — no shared SQLite connection across workers; the
  store owns the connection lifecycle, the worker thread never touches it.
- **Atomic claim** — `claim()` is one `UPDATE … WHERE task_id = (SELECT …)
  RETURNING`; two workers cannot claim the same task.
- **Atomic recovery** — `recover_abandoned` is one conditional `UPDATE …
  RETURNING`; two recoverers cannot both requeue the same task.
- **Explicit loser** — the loser of any exclusive transition gets `None` (an
  inspectable result), never an exception or a silent overwrite.
- **Deliberate SQLite** — `busy_timeout` + WAL, so concurrent writers wait instead
  of failing and readers never block the writer.

**Concurrency risk list (audit → Phase 5 resolutions):**

1. **Approval consumption races with resume** — ✅ resolved (5.1). `consume_approved`
   is now one conditional `UPDATE … WHERE status='approved'` gated by row count,
   so exactly one worker executes.
2. **Double recovery** — ✅ resolved (5.7). `recover_abandoned` is now one atomic
   conditional `UPDATE … RETURNING`; two recoverers cannot both requeue the same
   task (only one row is returned).
3. **Same-connection multi-thread writes** — ✅ resolved (5.2). Each worker thread
   now gets its own connection (thread-local, store-owned); no connection is ever
   written by two threads. Proven by `test_phase5_connections.py`.
4. **WebSocket teardown race.** `unsubscribe_all` during a publish can deliver
   one stray event to a just-disconnected subscriber. Harmless (bounded queue,
   downstream-only), but note it.
5. **No duplicate-event guard beyond the event_id PK.** `INSERT OR REPLACE` is
   idempotent per event_id; ids are UUIDs, so collisions are negligible.

**Bottom line:** Nexus is now correct under *concurrent* workers: each worker
writes through its own connection, exclusive transitions are single conditional
statements the database arbitrates, and SQLite is configured to wait rather than
fail. The remaining notes (#4, #5) are observational, not correctness risks.

---

## 5. Public contracts

| Contract | Producer | Consumers | Crossing? | Category |
|---|---|---|---|---|
| `Task` | apps (`ask`) | queue, worker | ✓ | contract |
| `Decision` | control.router | orchestrator, trace | ✓ | contract |
| `PolicyVerdict` | control.policy | orchestrator | ✓ | contract (in core) |
| `ToolCall` | model (`tool_calls`) | executor (subprocess/mcp/fake) | ✓ | contract |
| `ToolResult` | executor | orchestrator, trace | ✓ | contract |
| `ModelRequest`/`ModelResponse` | model executor | orchestrator | ✓ | contract |
| `RetrievalResult` | retriever | orchestrator | ✓ | contract |
| `Evaluation` | evaluator | orchestrator | ✓ | contract |
| `ApprovalRequest` | orchestrator | ApprovalStore, surfaces | ✓ | contract |
| `ToolDefinition` | integrations (mcp) | wiring → ToolSpec | ✓ | contract |
| `Event` | everyone | state, trace, surfaces | ✓ | contract |
| `Trace` | TraceProjector | surfaces | ✓ | contract |
| `RunState`/`TaskState` | core.state | surfaces | ✓ | projection |

**Category distinction (matters in Phase 5):**
- **contract** — a dataclass/Protocol crossing a boundary; changing a field is a
  breaking change to a producer or consumer.
- **implementation detail** — retriever ranking, embedder strategy, reranker,
  executor choice; MAY-DIFFER (see AD-002 and the MUST-MATCH/MAY-DIFFER table).
- **persisted schema** — the SQLite tables (`tasks`, `approvals`, `events`) and
  the event payloads. A field change here is a **migration**, not a refactor, and
  must preserve old-event reconstruction.

The one place these three overlap and need explicit semantics is the **event
payload** (persisted schema that is also a contract). Adding a payload key is
backward-compatible; renaming/removing one breaks reconstruction of old logs.

---

## Findings summary (ranked for Phase 5)

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | `find_approved` + `consume` not atomic | high | ✅ 5.1 — conditional UPDATE + rowcount |
| 2 | same-connection multi-thread writes unsafe | high | ✅ 5.2 — one connection per worker thread |
| 3 | `tool.*` events lack a per-call id | medium | ✅ 5.4 — `call_id` on ToolCall |
| 4 | approval state not event-sourced | medium | ✅ 5.5 — `ApprovalState.reconstruct` |
| 5 | `step.failed` never emitted | medium | ✅ 5.6 — emit on exception |
| 6 | double-recovery double-`requeued` event | low | ✅ 5.7 — atomic requeue (conditional UPDATE) |
| 7 | 4 dead event types | low | ✅ 5.8 — remove/reserve dead types |

*No finding is an architectural regression — every boundary held. These are the
concurrency and observability risks that a demo never hits and production will.*

## Resolution log

- **#1 atomic consume (5.1)** — `ApprovalStore.consume_approved` is one conditional
  `UPDATE … WHERE status='approved'` gated by row count; the orchestrator executes
  only on a win. Test: `tests/golden/test_phase5_concurrency.py` (two workers race,
  exactly one executes). ✅
- **#2 per-worker connections (5.2)** — each worker thread opens its own SQLite
  connection (thread-local, store-owned; `execution/sqlite.py`); SQLite is
  configured deliberately (`busy_timeout`, WAL, `synchronous=NORMAL`,
  `foreign_keys=ON`); concurrent independent transitions succeed. Tests:
  `tests/golden/test_phase5_connections.py` (connection policy) and
  `tests/golden/test_phase5_ownership.py` (5.3: claim race + recovery/claim race
  → exactly one owner). ✅
- **#3 per-call tool identity (5.4)** — `ToolCall.call_id` threaded through
  `tool.requested` / `tool.completed` / `policy.decision`; the trace projector
  pairs by call_id, so an interrupted call and a later completed call are never
  conflated. Test: `tests/golden/test_phase5_hardening.py`. ✅
- **#4 event-sourced approvals (5.5)** — `ApprovalState.reconstruct(approval_id,
  events)` reproduces the approval lifecycle from events alone; the projection is
  the source of truth, not a separate table read. Test:
  `tests/golden/test_phase5_hardening.py`. ✅
- **#5 terminal step semantics (5.6)** — a raising step now emits `step.failed`
  and marks the step FAILED before re-raising; process death still leaves no
  terminal event (that is how an interruption is detected). Test:
  `tests/golden/test_phase5_hardening.py`. ✅
- **#6 atomic recovery (5.7)** — `recover_abandoned` is one atomic conditional
  `UPDATE … RETURNING`; two recoverers cannot both requeue the same task. Test:
  `tests/golden/test_phase5_hardening.py`. ✅
- **#7 taxonomy cleanup (5.8)** — `TASK_CREATED` removed; `MODEL_SELECTED` /
  `MODEL_FALLBACK` reserved for Phase 6 model selection; `STEP_FAILED` is now
  live. Test: `tests/golden/test_phase5_hardening.py`. ✅

