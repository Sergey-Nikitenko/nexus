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

| Event | Emitter | Durable | Reconstructible | Correlation |
|---|---|---|---|---|
| `run.started/completed/failed/replanned` | orchestrator | ✓ | ✓ RunState | run_id, task_id |
| `step.started/completed` | orchestrator | ✓ | ✓ RunState | step_id |
| `retrieval.requested/completed` | orchestrator | ✓ | — | run_id, query |
| `model.requested/completed` | InstrumentedExecutor | ✓ | — | request_id |
| `tool.requested/completed` | InstrumentedExecutor | ✓ | ✓ (partial) | **tool name only** |
| `policy.decision` | orchestrator | ✓ | ✓ trace | tool, verdict, risk, executed, reason |
| `approval.required/granted/denied/consumed` | ApprovalStore | ✓ | **✗** | approval_id, task_id, run_id |
| `evaluation.completed` | orchestrator | ✓ | ✓ RunState | attempt, passed, reason |
| `task.queued/claimed/waiting/requeued/completed/failed` | queue | ✓ | ✓ TaskState | task_id, worker_id |

**Findings:**

1. **Four dead event types** (`defined`, never emitted): `TASK_CREATED`,
   `MODEL_SELECTED`, `MODEL_FALLBACK`, `STEP_FAILED`.
   - `MODEL_SELECTED` / `MODEL_FALLBACK`: the router (model selection) is not
     wired into the orchestrator — it uses the injected executor directly. Either
     wire it in Phase 5+ or drop the event types.
   - `STEP_FAILED`: the orchestrator's `step()` never emits it; an exception
     propagates with no terminal step event. A crashed step is observable as
     "started, never completed", but there is no explicit failure marker.
   - `TASK_CREATED`: superseded by `task.queued`.

2. **`tool.requested` / `tool.completed` carry no per-call id.** Only the tool
   name correlates them. The TraceProjector pairs them FIFO-per-name, which is
   correct today but becomes ambiguous if one attempt invokes the same tool name
   twice. → Add a `call_id` to `ToolCall` and thread it into both events.

3. **Approval state is not event-sourced.** `ApprovalStore` is a mutable SQLite
   table + events, but there is no `ApprovalState.reconstruct`. The queue has an
   event-sourced `TaskState`; approvals do not. "Approval state reconstructible
   from events alone" is therefore unverified.

4. **A "waiting for approval" run has no run-level terminal event.** The run
   stops emitting after `approval.required`; `RunState.reconstruct` yields
   `RUNNING`. The pause is captured at the task level (`task.waiting`), which is
   authoritative, but the run-level "paused" state is implicit.

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
Reconstructible. **Sound**, except a raising step never emits `step.failed`
(finding 2.1).

### Approval (store)
```
pending --approve--> approved --consume--> consumed
        \--deny--> denied
```
Each transition emits `approval.*`. **No event-sourced projection** (finding 2.3):
the transition graph is in the SQLite table, not reconstructible from events
alone.

---

## 4. Concurrency

**SQLite facts:** each store (queue, approvals, durable events) is a separate
file; all connect with `check_same_thread=False`. That flag allows cross-thread
use but does **not** add thread-safety — a single connection must not be written
by two threads at once.

**What Nexus guarantees:**
- Claim is atomic: `claim()` is one `UPDATE … WHERE task_id = (SELECT …) RETURNING`.
  Two workers on *separate* connections cannot claim the same task (SQLite
  serializes writers).

**What Nexus does NOT guarantee (the risk list):**

1. **Approval consumption races with resume.** The orchestrator's
   `find_approved` (SELECT) + `consume` (UPDATE) are not one transaction. Two
   workers resuming the same task could both read "approved" and both execute the
   tool — breaking single-use. → Make consume a conditional UPDATE
   (`UPDATE … WHERE status='approved'`) and check the row count.
2. **Double recovery.** `recover_abandoned` SELECTs expired tasks then UPDATEs
   each. Two recoverers can both select the same task and both emit
   `task.requeued`. Mostly idempotent, but the duplicate event pollutes the log.
3. **Same-connection multi-thread writes.** With `check_same_thread=False`, two
   threads writing one connection corrupt it. Today the worker is single-threaded
   (tests are sequential processes), so this is latent, not exercised. → Phase 5:
   one connection per thread, or a write lock, or serialize through a single
   writer.
4. **WebSocket teardown race.** `unsubscribe_all` during a publish can deliver
   one stray event to a just-disconnected subscriber. Harmless (bounded queue,
   downstream-only), but note it.
5. **No duplicate-event guard beyond the event_id PK.** `INSERT OR REPLACE` is
   idempotent per event_id; ids are UUIDs, so collisions are negligible.

**Bottom line:** Nexus is correct today under *sequential* processes (the
recovery test). It is **not** yet safe under concurrent workers. Phase 5 must
decide: single-writer serialization, per-thread connections, or a real store.

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

| # | Finding | Severity | Suggested fix |
|---|---|---|---|
| 1 | `find_approved` + `consume` not atomic | high | conditional UPDATE + rowcount ✅ |
| 2 | same-connection multi-thread writes unsafe | high | serialize writes / per-thread conns |
| 3 | `tool.*` events lack a per-call id | medium | add `call_id` to ToolCall |
| 4 | approval state not event-sourced | medium | add `ApprovalState.reconstruct` |
| 5 | `step.failed` never emitted | medium | emit on exception, or drop it |
| 6 | double-recovery double-`requeued` event | low | atomic requeue (conditional UPDATE) |
| 7 | 4 dead event types | low | wire router or delete types |

*No finding is an architectural regression — every boundary held. These are the
concurrency and observability risks that a demo never hits and production will.*

## Resolution log

| Finding | Change | Test | Status |
|---|---|---|---|
| #1 atomic consume | `ApprovalStore.consume_approved` — one conditional `UPDATE … WHERE status='approved'`, rowcount gate; orchestrator executes only on a win | `tests/golden/test_phase5_concurrency.py` (two workers race, exactly one executes) | ✅ resolved (5.1) |

