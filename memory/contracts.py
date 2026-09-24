"""Memory contracts — turning completed runs into retrievable episodes.

Memory and knowledge are two different information planes (BLUEPRINT Phase 2):

- knowledge/ — the CONTENT plane: chunks of documents, identity + version.
- memory/    — the SEQUENCE plane: what did we attempt, and how did it end.

A run becomes an Episode through a DETERMINISTIC projection (no LLM
summarization yet — that is an implementation detail a later adapter may swap
in behind this contract). The reference implementation (memory/inmemory.py) is
stdlib-only; a production adapter (semantic recall, vector memory) is just
another implementation of these protocols.
"""
from __future__ import annotations

from typing import Protocol

from core.contracts import Episode


class EpisodeExtractor(Protocol):
    """(task, run, events, evaluation) -> Episode, deterministically.

    The extractor READS a finished run and RETURNS a contract; it never writes
    to a store and never emits events (the sequence plane mirrors the control
    plane's discipline: projection, not side effect)."""
    def extract(self, task, run, events, evaluation=None) -> Episode: ...


class EpisodeStore(Protocol):
    """Stores episodes and retrieves them by query.

    Episodes are append-only records keyed by episode_id, so re-adding the same
    episode does not duplicate it. Retrieval returns Episode contracts (each
    self-describing via its provenance) — never a provider object."""
    def add(self, episode: Episode) -> None: ...

    def search(self, query: str, k: int = 5,
               filters: dict | None = None) -> list[Episode]: ...

    def get(self, episode_id: str) -> Episode | None: ...
