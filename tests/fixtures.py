"""Deterministic execution fixtures — a local fake model process.

Not auto-run by check.py (doesn't match test_*.py). Provides a fake model for
SubprocessModelExecutor: reads a JSON request on stdin, writes a provider-shaped
JSON response on stdout. Deterministic and offline, so `py scripts/check.py`
never depends on a live cloud model.

Behavior is keyed off the last message content (a test-double convention):
- default        -> a successful provider response carrying provider-specific
                    fields (`usage.prompt_tokens`, `finish_reason`,
                    `system_fingerprint`) that Nexus must translate away.
- "__REJECT__"   -> a provider rejection `{"error": ...}`.
- "__BIG__"      -> an oversized response, to prove bounded output.
"""
import os

FAKE_MODEL_SOURCE = '''\
import json, sys
req = json.load(sys.stdin)
content = req["messages"][-1]["content"]
if content == "__REJECT__":
    print(json.dumps({"error": "rate limited"}))
elif content == "__BIG__":
    print(json.dumps({"model": "m", "content": "x" * 200000, "usage": {}}))
else:
    print(json.dumps({
        "model": "fake-local-llm",
        "content": "echo: " + content,
        "usage": {"prompt_tokens": len(content.split()), "completion_tokens": 2},
        "finish_reason": "stop",
        "system_fingerprint": "fp_123",
    }))
'''


def write_fake_model(directory: str) -> str:
    """Write the fake model script and return its path."""
    path = os.path.join(directory, "fake_model.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(FAKE_MODEL_SOURCE)
    return path
