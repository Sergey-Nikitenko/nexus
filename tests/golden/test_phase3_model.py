"""Phase 3.3 golden task — model execution (the real model adapter, offline).

ModelRequest -> Executor.run_model() -> ModelResponse. The adapter translates a
provider-shaped JSON response into Nexus semantics and drops the rest: no
`usage` dict, `finish_reason`, or `system_fingerprint` escapes into the caller's
ModelResponse. The suite is deterministic and offline (a local fake model
process) — a live cloud API would be an integration test, never what makes
`py scripts/check.py` pass.

Run:  py tests/golden/test_phase3_model.py
"""
import dataclasses
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fixtures import fake_model_argv  # noqa: E402

from core.contracts import ModelRequest, ModelResponse  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from execution.instrumented import InstrumentedExecutor  # noqa: E402
from execution.models import ModelSpec, SubprocessModelExecutor  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 3.3 golden task: model execution (provider -> Nexus, offline)")
    tmp = tempfile.mkdtemp()
    executor = SubprocessModelExecutor(ModelSpec(
        name="local-llm", argv=fake_model_argv(), max_content_bytes=256))

    # 1. the contract + provider -> Nexus translation
    resp = executor.run_model(ModelRequest(messages=[{"role": "user", "content": "hello world"}]))
    check(isinstance(resp, ModelResponse) and resp.success,
          "ModelRequest -> ModelResponse (success)")
    check(resp.content == "echo: hello world", "content is translated from the provider")
    check(resp.model == "fake-local-llm", "model identity survives")
    check(resp.tokens_in == 2 and resp.tokens_out == 2,
          "usage is translated: prompt_tokens -> tokens_in, completion_tokens -> tokens_out")
    d = dataclasses.asdict(resp)
    check("finish_reason" not in d and "system_fingerprint" not in d and "usage" not in d,
          "provider-specific concepts (finish_reason, system_fingerprint, usage) do NOT leak")

    # 2. request identity: requested/completed unambiguously the same run/step
    bus = EventBus()
    instr = InstrumentedExecutor(executor, bus, run_id="r1", task_id="t1")
    req = ModelRequest(messages=[{"role": "user", "content": "hi"}])
    instr.run_model(req)
    req_ev = [e for e in bus.history if e.event_type == EventType.MODEL_REQUESTED][0]
    done_ev = [e for e in bus.history if e.event_type == EventType.MODEL_COMPLETED][0]
    check(req_ev.payload["request_id"] == done_ev.payload["request_id"] == req.request_id,
          "model.requested and model.completed carry the same request_id")
    check(req_ev.payload["request_id"] and req.request_id,
          "the request carries a stable identity by default")

    # 3. provider rejection -> defined failure representation, not an exception
    rej = executor.run_model(ModelRequest(messages=[{"role": "user", "content": "__REJECT__"}]))
    check(rej.success is False and "rate limited" in rej.error,
          "provider rejection -> ModelResponse(success=False, error) (defined failure, no exception)")

    # 4. launch/config failure -> raise
    try:
        SubprocessModelExecutor().run_model(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
        check(False, "no model configured -> raise")
    except ValueError:
        check(True, "no model configured raises (launch/config failure)")
    try:
        SubprocessModelExecutor(ModelSpec("x", [os.path.join(tmp, "nope.exe")])).run_model(
            ModelRequest(messages=[{"role": "user", "content": "hi"}]))
        check(False, "missing executable -> raise")
    except OSError:
        check(True, "missing executable raises (launch failure, not a tool outcome)")

    # 5. bounded output
    big = executor.run_model(ModelRequest(messages=[{"role": "user", "content": "__BIG__"}]))
    check(big.success and len(big.content) <= 256 + 40, "response content is bounded")
    check("truncated" in big.content, "truncation is explicit, not silent")

    # 6. determinism
    a = executor.run_model(ModelRequest(messages=[{"role": "user", "content": "hello"}]))
    b = executor.run_model(ModelRequest(messages=[{"role": "user", "content": "hello"}]))
    check(a.content == b.content, "deterministic (same request -> same response)")

    # 7. the invariant, failure path: launch failure leaves no completed event
    bus3 = EventBus()
    instr3 = InstrumentedExecutor(SubprocessModelExecutor(), bus3, run_id="r3", task_id="t3")
    try:
        instr3.run_model(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
    except ValueError:
        pass
    k3 = [e.event_type for e in bus3.history]
    check(EventType.MODEL_REQUESTED in k3 and EventType.MODEL_COMPLETED not in k3,
          "a launch failure leaves model.requested but NO model.completed")

    print("\nPASS: Phase 3.3 model execution holds.")


if __name__ == "__main__":
    main()
