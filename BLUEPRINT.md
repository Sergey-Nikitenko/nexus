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

## Repository layout

```
nexus/
  apps/        api, worker, dashboard, cli
  core/        contracts, events, state, tasks, errors, ids
  control/     router, policy, evaluator, models, tools
  knowledge/   ingestion, parsing, chunking, embeddings, retrieval, memory
  execution/   orchestrator, planning, workers, queue, verification
  integrations/ mcp, ollama, openai, github
  observability/ tracing, events, metrics
  tests/       unit, integration, golden, fixtures
  deploy/      docker, oracle, nginx
  docs/        architecture, decisions, roadmap, api
```

The separation that matters: **contracts, control, knowledge, execution,
integrations** — not a flat pile of files.
