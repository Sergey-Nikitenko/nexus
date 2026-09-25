# Nexus

**Nexus is not an LLM wrapper. It is an observable execution system for AI agents.**

Nexus is a from-scratch execution platform for AI agents, built the way production
systems are built: contracts first, an event log as the source of truth, and every
architectural claim backed by a test that would fail if the claim regressed.

It answers the questions that decide whether an AI system survives contact with
reality, not just whether a demo runs once:

- **Can a crash lose work?** No — state reconstructs from a durable event log.
- **Can an approval be double-consumed?** No — authorization transitions are atomic.
- **Can two workers claim the same task?** No — ownership is generation-specific.
- **Can we explain why two runs differed?** Yes — the run manifest names the input that changed.
- **Can a provider be swapped without a rewrite?** Yes — providers disappear at the boundary.
- **Can an MCP client drive it, and can it drive an MCP server?** Yes — both directions.

---

## The one-sentence summary

An agent's decisions, capabilities, failures, approvals, and recovery stay
**observable and replaceable under change** — that's the product, not the model.

---

## What Nexus actually is

Nexus is a **runtime**, not a model call. It separates the parts that usually get
glued into one fragile script:

| Plane | Question it answers | Components |
|---|---|---|
| **Contracts** (`core/`) | *What shape is the data?* | `Task`, `Run`, `Step`, `ToolCall`, `ToolResult`, `Event`, `ApprovalRequest`, `RunManifest`, … |
| **Control** (`control/`) | *What **should** happen?* | router, policy, evaluator — pure, never execute |
| **Execution** (`execution/`) | *What **did** happen?* | orchestrator, worker, queue, approvals, recovery |
| **Knowledge** (`knowledge/`) | *What do we know?* | ingestion → retrieval (in-memory, persistent, Chroma) |
| **Memory** (`memory/`) | *What did we attempt?* | deterministic run → episode |
| **Observability** (`observability/`) | *What can we see?* | trace projection, replay fingerprint |
| **Surface** (`apps/`) | *How do humans/machines reach it?* | REST, WebSocket, dashboard, CLI, MCP server |

The rule that holds it together: **components communicate by contract or by event,
never by reaching into each other's internals.**

---

## The arc (Phases 0–6)

Nexus was built as a sequence of frozen phases, each with an acceptance statement
proven by golden and conformance tests:

- **Phase 0** — foundation: contracts, events, recoverable state.
- **Phase 1** — control plane: router proposes, policy disposes; *Decision ≠ Action*.
- **Phase 2** — knowledge & memory: replaceable retrieval, deterministic memory.
- **Phase 3** — execution plane: executor, orchestrator, durable worker, crash recovery.
- **Phase 4** — surface: REST, WebSocket, dashboard, CLI, approvals.
- **Phase 5** — hardening: atomic authorization and ownership under concurrent workers.
- **Phase 6** — differentiators: reproducible execution, schema versioning, durable run identity, multi-worker arbitration, MCP interop, multi-agent composition.

Full detail, including every architectural decision (AD-001…AD-034), is in
[`BLUEPRINT.md`](BLUEPRINT.md).

---

## Quick start

```bash
# Python 3.12+
pip install -r requirements.txt

# Run the entire suite — 52 golden + conformance tests
python scripts/check.py
```

`core/` is **stdlib-only** by design; the heavier dependencies (FastAPI, Chroma,
OpenAI) are adapters used only where a phase proves a provider boundary.

There is no "main.py" to run — Nexus is a library of contracts and components that
applications compose. The golden tests are the worked examples: each is a
self-contained, runnable script that wires a runtime and runs a real scenario.

```bash
python tests/golden/test_phase3_orchestrator.py   # Task → Answer, full loop
python tests/golden/test_phase5_system.py         # N workers, concurrent
python tests/golden/test_phase6_multiagent.py     # supervisor → research/coding/review
```

---

## Design principles

These are not slogans — each one has an enforcing test.

- **Build the contracts before the implementations.** The orchestrator doesn't care
  *how* retrieval or tool execution works, only what shape the data has.
- **Decision ≠ Action.** The control plane can *request*; only the execution plane
  (the `Executor` capability) can *cause* a side effect.
- **The provider disappears at the boundary.** Chroma, MCP, subprocesses, and model
  SDKs are adapters; only Nexus contracts cross.
- **At-least-once is honest.** Nexus never promises exactly-once; it documents that
  recovery may execute a tool twice — and makes it observable.
- **Reproducible ≠ byte-identical.** Replay compares a semantic fingerprint of the
  event log, not raw bytes; event ids and timestamps legitimately differ.
- **A principle without a test is not a principle — it is an intention.** Every
  architectural property has a conformance test that makes violating it a failing build.

---

## Testing

`python scripts/check.py` runs two kinds of tests, currently **52 total**:

- **Golden tests** (`tests/golden/`) — *does Nexus work?* Deterministic end-to-end
  scenarios, offline, against the stdlib reference implementations and the fake executor.
- **Conformance tests** (`tests/conformance/`) — *does Nexus still conform?* AST gates
  and boundary checks: layer direction, provider leakage, orchestrator/worker purity,
  contract-only crossings.

The suite auto-discovers `tests/{golden,conformance}/test_*.py`, so a new test needs
no harness edit.

---

## Documentation

- [`BLUEPRINT.md`](BLUEPRINT.md) — the architecture, the phase progression, and every
  architectural decision (AD-001…AD-034).
- [`ARCHITECTURE_AUDIT.md`](ARCHITECTURE_AUDIT.md) — two adversarial audits (finding →
  change → test → resolved), kept as a live engineering ledger.

---

## Status

**Phase 6 complete.** Six phases, 52 tests, two architecture audits, and a coherent
arc: ownership → reproduction → interpretation → identity → arbitration →
interoperability → composition.

Nexus is a portfolio project by [Sergey Nikitenko](https://github.com/Sergey-Nikitenko),
built to demonstrate applied-AI engineering — not just calling a model, but building
the durable, observable, replaceable substrate an agent runs on.
