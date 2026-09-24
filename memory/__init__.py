"""Memory — the sequence plane of Nexus.

Knowledge (knowledge/) is the CONTENT plane: chunks of documents, identity and
version. Memory is the SEQUENCE plane: what did we attempt, and how did it end.

A completed run is projected (deterministically) into an Episode and stored for
retrieval. Memory is deliberately a top-level package — it is not a sub-package
of knowledge, because "memory" is not a synonym for "vector database".
"""
