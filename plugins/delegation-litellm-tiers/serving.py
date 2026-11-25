"""Log one JSON line per model HTTP response: which model served it, after any LiteLLM fallback.

LiteLLM answers every call with ``x-litellm-model-id`` (the deployment that served it) and
``x-litellm-attempted-fallbacks``. Hermes' hooks carry no response headers, so
:func:`install_response_log` wraps httpx's ``send``: each response to a model endpoint is logged
where it is received, with a request id generated per call. Nothing is shared between calls, so
overlapping calls (children, retries, auxiliary tasks) cannot be attributed to one another.

A call whose model is not in the tier map, or whose answer carries no LiteLLM header, is logged too,
with ``in_tier_map`` or ``via_litellm`` false: a route outside the tiers must be visible, not absent.
``session_id`` comes from the ``x-hermes-session`` header every tier provider sends
(``session_affinity_header`` in ``config/config.yaml``); it is null for a call that sends none.

No prompt, completion or key is ever written: only identifiers, counts and timings.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .tiers import TIERS

SESSION_HEADER = "x-hermes-session"
HEADER_MODEL_ID = "x-litellm-model-id"
HEADER_ATTEMPTED_FALLBACKS = "x-litellm-attempted-fallbacks"
#: The paths of a model call. Anything else (model listings, GitHub) is not logged.
MODEL_CALL_SUFFIXES = ("/chat/completions", "/completions", "/responses", "/messages")


def _stdout_sink(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


#: The log writer, public so another module that logs to the same place (the loops) reuses it.
write_line = _stdout_sink


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _requested_model(request: Any) -> str | None:
    """The ``model`` of a JSON request body, and nothing else from it."""
    try:
        body = json.loads(request.content)
    except (ValueError, AttributeError, RuntimeError):  # not JSON, or a streamed body not read
        return None
    model = body.get("model") if isinstance(body, dict) else None
    return model if isinstance(model, str) else None


class ServingRecorder:
    def __init__(
        self,
        sink: Callable[[str], None] = _stdout_sink,
        clock: Callable[[], float] = time.time,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._new_id = new_id
        self._subagents: set[str] = set()
        self._lock = threading.Lock()

    def note_subagent(self, session_id: object) -> None:
        """Remember a child session, so its calls are marked ``subagent``."""
        if isinstance(session_id, str) and session_id:
            with self._lock:
                self._subagents.add(session_id)

    def record_response(self, request: Any, response: Any, duration_s: float) -> dict[str, Any] | None:
        """Write the line for one response to a model endpoint; ``None`` for any other request."""
        url = urlsplit(str(request.url))
        if not url.path.endswith(MODEL_CALL_SUFFIXES):
            return None
        session_id = request.headers.get(SESSION_HEADER)
        model = _requested_model(request)
        model_id = response.headers.get(HEADER_MODEL_ID)
        with self._lock:
            is_subagent = session_id in self._subagents
        line = {
            "event": "serving_model",
            "ts": round(self._clock(), 3),
            "request_id": self._new_id(),
            "session_id": session_id,
            "subagent": is_subagent,
            "host": url.hostname,
            "requested_model": model,
            "in_tier_map": model in TIERS,
            "via_litellm": model_id is not None,
            "litellm_model_id": model_id,
            "attempted_fallbacks": _as_int(response.headers.get(HEADER_ATTEMPTED_FALLBACKS)),
            "status": response.status_code,
            "duration_s": round(duration_s, 3),
        }
        self._sink(json.dumps(line, sort_keys=True, separators=(",", ":")))
        return line


def install_response_log(recorder: ServingRecorder, httpx_module: Any) -> None:
    """Wrap ``httpx.Client.send`` and ``httpx.AsyncClient.send`` so each model response is logged.

    The OpenAI client Hermes uses sits on httpx, and a streamed response has its headers when
    ``send`` returns, before the body is read. Calling twice wraps once.
    """
    if getattr(httpx_module.Client.send, "_serving_log", False):
        return
    original_sync = httpx_module.Client.send
    original_async = httpx_module.AsyncClient.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        response = original_sync(self, request, **kwargs)
        recorder.record_response(request, response, time.monotonic() - started)
        return response

    async def send_async(self: Any, request: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        response = await original_async(self, request, **kwargs)
        recorder.record_response(request, response, time.monotonic() - started)
        return response

    send._serving_log = True  # type: ignore[attr-defined]
    httpx_module.Client.send = send
    httpx_module.AsyncClient.send = send_async
