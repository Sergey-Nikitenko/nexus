"""Reference (stdlib-only) memory implementation.

- extract_episode: deterministic projection of a finished run into an Episode.
- InMemoryEpisodeStore: keyword-overlap retrieval over episode summaries and
  entities. No embeddings yet — semantic recall is a later adapter, not a
  change to the Episode contract.
"""
from __future__ import annotations

import re

from core.contracts import Episode, new_id, utcnow

_WORD = re.compile(r"[a-z0-9]+")

# Payload keys that name a resource the run touched (a file, repo, URL, ...).
_RESOURCE_KEYS = ("path", "file", "repo", "resource", "url")


def _terms(*texts: str) -> set[str]:
    return set(_WORD.findall(" ".join(t.lower() for t in texts)))


def extract_episode(task, run, events, evaluation=None) -> Episode:
    """Deterministic projection: the same (run, events) always yields the same
    summary / outcome / entities / provenance. episode_id and timestamp are the
    only fresh-per-record fields, consistent with how every Nexus id is minted.

    The extractor READS a finished run and RETURNS a contract — it never writes
    to a store (projection, not side effect).
    """
    outcome = "unknown"
    tool_names: set[str] = set()
    resources: set[str] = set()

    for ev in events:
        t = ev.event_type
        if t == "run.completed":
            outcome = "success"
        elif t == "run.failed":
            outcome = "failed"
        elif t == "tool.requested":
            tool = ev.payload.get("tool") or ev.payload.get("tool_name") or ""
            if tool:
                tool_names.add(tool)
            args = ev.payload.get("arguments")
            if isinstance(args, dict):
                for key in _RESOURCE_KEYS:
                    value = args.get(key)
                    if isinstance(value, str) and value:
                        resources.add(value)
            for key in _RESOURCE_KEYS:
                value = ev.payload.get(key)
                if isinstance(value, str) and value:
                    resources.add(value)

    checks = dict(evaluation.checks) if evaluation is not None else {}
    return Episode(
        episode_id=new_id("ep"),
        task_id=task.task_id,
        summary=task.title,
        outcome=outcome,
        relevant_entities=sorted(tool_names | resources),
        timestamp=utcnow(),
        provenance={
            "run_id": run.run_id,
            "step_count": len(run.steps),
            "evaluation": checks,
        },
    )


def _matches(episode: Episode, filters: dict | None) -> bool:
    for key, value in (filters or {}).items():
        if key == "outcome":
            if episode.outcome != value:
                return False
        elif key == "task_id":
            if episode.task_id != value:
                return False
        elif episode.provenance.get(key) != value:
            return False
    return True


class InMemoryEpisodeStore:
    """Append-only episode store keyed by episode_id (re-adding is a no-op).
    Retrieval ranks by keyword overlap over summary + entities — deterministic,
    no embeddings. Filters match outcome / task_id / any provenance key."""

    def __init__(self) -> None:
        self._episodes: dict[str, Episode] = {}

    def add(self, episode: Episode) -> None:
        self._episodes[episode.episode_id] = episode

    def get(self, episode_id: str) -> Episode | None:
        return self._episodes.get(episode_id)

    def search(self, query: str, k: int = 5,
               filters: dict | None = None) -> list[Episode]:
        q = _terms(query)
        if not q:
            return []
        scored: list[tuple[Episode, float]] = []
        for episode in self._episodes.values():
            if not _matches(episode, filters):
                continue
            doc = _terms(episode.summary, " ".join(episode.relevant_entities))
            overlap = len(q & doc) / len(q)
            if overlap > 0:
                scored.append((episode, overlap))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [episode for episode, _ in scored[:k]]
