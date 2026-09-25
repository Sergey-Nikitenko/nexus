"""Deterministic execution fixtures — a local fake model.

Not auto-run by check.py (doesn't match test_*.py). Provides a fake model for
SubprocessModelExecutor: it is an inline `python -c` command (NOT a file written
to disk), so it is deterministic and offline — and it never writes an executable
into a temp directory, which is exactly what trips AV/EDR "dropper" heuristics.

The fake model reads a JSON request on stdin and writes a provider-shaped JSON
response on stdout, keyed off the last message content (a test-double convention):
- default        -> a successful provider response carrying provider-specific
                    fields (`usage.prompt_tokens`, `finish_reason`,
                    `system_fingerprint`) that Nexus must translate away.
- "__REJECT__"   -> a provider rejection `{"error": ...}`.
- "__BIG__"      -> an oversized response, to prove bounded output.
"""
import os
import sys
import time

from core.contracts import ModelResponse, ToolCall, ToolResult

# Single-line, single-quoted Python (no double quotes, no newlines) so it passes
# cleanly through subprocess argv as a `-c` argument on Windows.
FAKE_MODEL_CODE = (
    "import json,sys;"
    "req=json.load(sys.stdin);"
    "c=req['messages'][-1]['content'];"
    "print(json.dumps({'error':'rate limited'}) if c=='__REJECT__' "
    "else json.dumps({'model':'m','content':'x'*200000,'usage':{}}) if c=='__BIG__' "
    "else json.dumps({'model':'fake-local-llm','content':'echo: '+c,"
    "'usage':{'prompt_tokens':len(c.split()),'completion_tokens':2},"
    "'finish_reason':'stop','system_fingerprint':'fp_123'}))"
)


def fake_model_argv() -> list[str]:
    """The argv for a deterministic, disk-less fake model provider."""
    return [sys.executable, "-c", FAKE_MODEL_CODE]


class KillableExecutor:
    """Deterministic test double for failure injection.

    `run_model` is scripted (suggest a tool, then answer). `execute_tool` writes
    a "side effect" line (the external mutation). Depending on `sleep_phase` it
    sleeps so a killer process can strike at a chosen point:

    - "model": signal + sleep BEFORE the tool (dies before its side effect).
    - "tool":  side effect + signal + sleep (dies AFTER the side effect, before
               the completion event) — the at-least-once case.
    """

    def __init__(self, signal_path, side_effect_path, sleep_seconds=60.0,
                 sleep_phase="tool"):
        self.signal_path = signal_path
        self.side_effect_path = side_effect_path
        self.sleep_seconds = sleep_seconds
        self.sleep_phase = sleep_phase
        self._model_calls = 0

    def _append(self, path, text):
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

    def run_model(self, request):
        self._model_calls += 1
        if self.sleep_phase == "model" and self._model_calls == 1:
            self._append(self.signal_path, "model\n")
            time.sleep(self.sleep_seconds)
        if self._model_calls == 1:
            return ModelResponse(model="fake", content="", success=True, tool_calls=[
                ToolCall(tool_name="github.read_file", arguments={"path": "x.py"}),
            ])
        return ModelResponse(model="fake", content="the answer", success=True)

    def execute_tool(self, call):
        self._append(self.side_effect_path, call.tool_name + "\n")  # external side effect
        if self.sleep_phase == "tool":
            self._append(self.signal_path, "tool\n")
            time.sleep(self.sleep_seconds)
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def run_worker_from_env():
    """Build a queue + orchestrator + worker from environment variables and run
    one task. A subprocess entry point for the failure-injection test."""
    from core.contracts import Risk
    from control.evaluator import Evaluator
    from control.policy import PolicyEngine, PolicyRules
    from control.tools import ToolRegistry, ToolSpec
    from execution.durable import DurableEventBus
    from execution.orchestrator import Orchestrator
    from execution.queue import TaskQueue
    from execution.worker import Worker
    from knowledge.inmemory import ComposedRetriever

    bus = DurableEventBus(os.environ["EVENTS_PATH"])
    queue = TaskQueue(os.environ["QUEUE_PATH"], bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    executor = KillableExecutor(
        signal_path=os.environ["SIGNAL_PATH"],
        side_effect_path=os.environ["SIDE_EFFECT_PATH"],
        sleep_seconds=float(os.environ.get("SLEEP_SECONDS", "60")),
        sleep_phase=os.environ.get("KILL_PHASE", "tool"),
    )
    orchestrator = Orchestrator(
        retriever=retriever, executor=executor,
        policy=PolicyEngine(PolicyRules()), tools=tools,
        evaluator=Evaluator(), bus=bus)
    Worker(worker_id=os.environ["WORKER_ID"], queue=queue,
           orchestrator=orchestrator).run_one()
