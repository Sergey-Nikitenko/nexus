"""Phase 1 golden tasks — the router (explainable model selection).

G006  choose local model for a private task
G007  fall back to cloud when no local model fits

Plus a wiring check: the Decision plugs into Phase 0 (well-formed model.selected event).

Run:  py tests/golden/test_phase1_router.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Event, Run, Task, new_id, utcnow  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from control.models import ModelRegistry, ModelSpec  # noqa: E402
from control.router import Router, RoutingRequest  # noqa: E402


def build_registry() -> ModelRegistry:
    reg = ModelRegistry()
    reg.register(ModelSpec("ollama/qwen", "local", context_window=32_000,
                           cost_per_1k=0.0, private=True, supports_tools=True))
    reg.register(ModelSpec("gpt-4o", "cloud", context_window=128_000,
                           cost_per_1k=0.03, private=False, supports_tools=True))
    return reg


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 1 golden tasks: router (explainable model selection)")
    router = Router(build_registry())

    # G006: private coding task -> local model, with a defensible reason
    d = router.route(RoutingRequest(task="coding", context_needed=18_000,
                                    private=True, needs_tools=True))
    check(d.model == "ollama/qwen", "G006: private task -> local model")
    check(d.provider == "local", "G006: provider is local")
    check(any("private" in r for r in d.reasons), "G006: decision carries a 'why' (privacy)")

    # G007: huge context, private -> no local fits -> escalate to cloud
    d2 = router.route(RoutingRequest(task="analyze large document", context_needed=100_000,
                                     private=True))
    check(d2.model == "gpt-4o", "G007: no local fits -> fall back to cloud")
    check(any("escalated" in r for r in d2.reasons), "G007: decision carries the escalation reason")

    # Wiring: Decision -> well-formed model.selected event (Phase 0)
    task = Task(task_id=new_id("task"), title="x")
    run = Run(run_id=new_id("run"), task_id=task.task_id)
    bus = EventBus()
    ev = Event(event_id=new_id("evt"), event_type=EventType.MODEL_SELECTED,
               timestamp=utcnow(), run_id=run.run_id, task_id=task.task_id,
               component="router", status="success", payload={"model": d.model, "reasons": d.reasons})
    bus.publish(ev)
    check(bus.history[-1].event_type == EventType.MODEL_SELECTED and bus.history[-1].run_id == run.run_id,
          "Decision -> well-formed model.selected event")

    print("\nPASS: Phase 1 router holds.")


if __name__ == "__main__":
    main()
