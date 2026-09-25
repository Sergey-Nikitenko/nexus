"""The semantic fingerprint of a run — a deterministic digest of its events.

`canonical_projection` reduces a run's events to SEMANTIC fields, dropping
volatile identifiers (event_id, run_id, task_id, timestamps, worker/claim/call/
request/step/approval ids) — because **reproducible != byte-identical**.
`fingerprint` hashes that projection.

This is a pure function over the core Event contract, so both the execution layer
(which persists the fingerprint at a run's terminal state, 6.3) and the
observability layer (which compares fingerprints, 6.1) can use it without
violating the layer boundary.
"""
from __future__ import annotations

import hashlib
import json

from .events import EventType

# Volatile fields: intentionally different between two runs of the SAME inputs.
_VOLATILE = {
    "event_id", "run_id", "task_id", "timestamp", "parent_event_id",
    "worker_id", "claimed_at", "claim_generation", "call_id",
    "request_id", "step_id", "approval_id",
}


def _is_run_event(event_type: str) -> bool:
    """The manifest is the input record (compared separately); task.* and
    approval.* are queue/store lifecycle, not the run's semantic trace."""
    return not (
        event_type == EventType.RUN_MANIFEST
        or event_type.startswith("task.")
        or event_type.startswith("approval.")
    )


def canonical_projection(events) -> list[dict]:
    """Reduce a run's events to comparable semantic nodes, dropping volatile ids."""
    nodes: list[dict] = []
    for e in events:
        if not _is_run_event(e.event_type):
            continue
        node = {"type": e.event_type}
        for key, value in sorted(e.payload.items()):
            if key not in _VOLATILE:
                node[key] = value
        nodes.append(node)
    return nodes


def fingerprint(events) -> str:
    """A deterministic digest of the canonical projection."""
    return hashlib.sha256(
        json.dumps(canonical_projection(events), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
