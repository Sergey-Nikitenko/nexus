"""Model execution — the real model adapter, anticlimactic by design.

    ModelRequest ──► SubprocessModelExecutor ──► provider process ──► ModelResponse

The adapter translates provider -> Nexus. The provider wire format is NOT
Nexus's shape: a provider response may carry `usage.prompt_tokens`, a
`finish_reason`, a `system_fingerprint` — the adapter maps what Nexus needs
(content, model, tokens, latency) and drops the rest. The caller only ever sees
a ModelResponse.

Failure semantics (AD-010's model twin):
- valid provider response                -> ModelResponse(success=True, ...)
- provider rejection ({"error": ...} or
  non-zero exit or malformed response)    -> ModelResponse(success=False, ...)
- launch/config failure (no model, missing
  executable, OS error)                  -> raise

The provider here is a local process (reads JSON on stdin, writes JSON on
stdout) so the suite is deterministic and offline; an HTTP model client is a
later adapter behind the same Executor contract.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from core.contracts import ModelRequest, ModelResponse


@dataclass
class ModelSpec:
    """How one logical model maps to a local provider process."""
    name: str
    argv: list[str]      # argv[0] = model executable; JSON request on stdin, JSON response on stdout
    timeout_seconds: float = 30.0
    max_content_bytes: int = 64 * 1024


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated at {limit} bytes]"


class SubprocessModelExecutor:
    """Runs a model as a local process; translates its JSON response to Nexus."""

    def __init__(self, spec: ModelSpec | None = None) -> None:
        self._spec = spec

    def run_model(self, request: ModelRequest) -> ModelResponse:
        spec = self._spec
        if spec is None:
            raise ValueError("no model configured")  # launch/config failure

        req_json = json.dumps({
            "messages": request.messages,
            "max_tokens": request.max_tokens,
        })

        # launch failure (missing executable / OS error) propagates as an exception
        proc = subprocess.Popen(
            spec.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False,
        )
        try:
            out, err = proc.communicate(input=req_json, timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return ModelResponse(model=spec.name, content="", success=False,
                                 error=f"timeout after {spec.timeout_seconds:g}s")

        # provider rejection = non-zero exit OR a malformed/error payload
        if proc.returncode != 0:
            return ModelResponse(
                model=spec.name, content="", success=False,
                error=f"provider exit status {proc.returncode}"
                      + (f": {err[:200]}" if err else ""),
            )

        try:
            payload = json.loads(out or "{}")
        except json.JSONDecodeError:
            return ModelResponse(model=spec.name, content="", success=False,
                                 error="invalid provider response")

        if not isinstance(payload, dict) or "error" in payload:
            return ModelResponse(
                model=spec.name, content="", success=False,
                error=str(payload.get("error") if isinstance(payload, dict) else "invalid provider response"),
            )

        # translate provider -> Nexus; drop provider-specific concepts
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ModelResponse(
            model=str(payload.get("model", spec.name)),
            content=_bound(str(payload.get("content", "")), spec.max_content_bytes),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=int(payload.get("latency_ms", 0) or 0),
            cost=0.0,
            success=True,
        )
