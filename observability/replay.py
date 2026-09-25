"""Replay report — comparing two runs against their manifests (AD-029).

A run is reproducible only when Nexus has persisted the INPUTS that define it
(the `RunManifest`) alongside the OUTPUTS (the event log). `compare` derives a
`ReplayStatus` from explicit conditions:

- REPRODUCIBLE iff inputs match AND semantic traces match;
- REPRODUCIBLE_WITH_DIFFERENCES iff an input changed (or the traces diverged);
- NON_REPRODUCIBLE iff a required input (manifest) is missing.

The semantic trace comparison uses `core.fingerprint.canonical_projection` — the
canonical, volatile-free projection established in 6.1. Pure — consumes core only,
so observability stays downstream of execution.
"""
from __future__ import annotations

from core.contracts import ReplayReport, ReplayStatus, RunManifest
from core.fingerprint import canonical_projection, fingerprint  # noqa: F401 (re-exported)

# Manifest fields that are INPUTS (run_id/task_id are identities, not inputs).
_MANIFEST_INPUT_FIELDS = (
    "task_title", "knowledge", "policy", "model", "tools", "router", "max_replans",
)


def _get(manifest, field):
    # a manifest may be a RunManifest object (in memory) or the persisted dict
    # (retrieved from RunRecordStore) — both answer the same "what inputs" question.
    if isinstance(manifest, dict):
        return manifest.get(field)
    return getattr(manifest, field)


def _inputs(manifest) -> dict:
    return {f: _get(manifest, f) for f in _MANIFEST_INPUT_FIELDS}


def compare(a_manifest, a_events, b_manifest, b_events) -> ReplayReport:
    """Derive a ReplayStatus from explicit conditions, never a guess."""
    if a_manifest is None or b_manifest is None:
        return ReplayReport(status=ReplayStatus.NON_REPRODUCIBLE)

    a_inputs = _inputs(a_manifest)
    b_inputs = _inputs(b_manifest)
    diffs = [(f, a_inputs[f], b_inputs[f]) for f in _MANIFEST_INPUT_FIELDS
             if a_inputs[f] != b_inputs[f]]

    a_nodes = canonical_projection(a_events)
    b_nodes = canonical_projection(b_events)

    first_divergence = None
    for i in range(min(len(a_nodes), len(b_nodes))):
        if a_nodes[i] != b_nodes[i]:
            first_divergence = a_nodes[i]["type"]
            break
    if first_divergence is None and len(a_nodes) != len(b_nodes):
        first_divergence = "event-count"

    if not diffs and a_nodes == b_nodes:
        status = ReplayStatus.REPRODUCIBLE
    else:
        status = ReplayStatus.REPRODUCIBLE_WITH_DIFFERENCES

    return ReplayReport(
        status=status,
        input_differences=diffs,
        first_divergent_event=first_divergence,
        event_count=(len(a_nodes), len(b_nodes)),
    )
