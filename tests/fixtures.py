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
import sys

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
