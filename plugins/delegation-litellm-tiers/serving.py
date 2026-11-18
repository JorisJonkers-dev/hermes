"""Record which model served each call: one JSON line per model call of a tier provider.

LiteLLM answers every call with ``x-litellm-model-id`` (the deployment that served it, after
any fallback) and ``x-litellm-attempted-fallbacks``. Hermes' ``post_api_request`` hook does not
carry response headers, so :func:`install_header_capture` wraps httpx's ``send`` and keeps the last
pair per Hermes session, and :meth:`ServingRecorder.record_call` joins them to the hook's call.
The session is known at the HTTP layer because every tier provider in ``config/config.yaml`` sets
``session_affinity_header: x-hermes-session``, which makes Hermes send its session id on each
request. (A thread cannot be the key: Hermes streams from a worker thread.) A session's calls are
sequential, so its last response is the one a hook reports.
The body's ``model`` (``response_model``) is logged beside them, so a call whose headers could
not be matched is still attributed to the model that answered it.

No prompt, completion or key is ever written: only identifiers, counts and timings.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .tiers import tier_of_provider

SESSION_HEADER = "x-hermes-session"
HEADER_MODEL_ID = "x-litellm-model-id"
HEADER_ATTEMPTED_FALLBACKS = "x-litellm-attempted-fallbacks"


@dataclass(frozen=True)
class ServedHeaders:
    model_id: str | None
    attempted_fallbacks: int | None


def _stdout_sink(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class ServingRecorder:
    """Joins the response headers of a call to the hook that reports the call, and writes the line."""

    def __init__(
        self,
        sink: Callable[[str], None] = _stdout_sink,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._served: dict[str, ServedHeaders] = {}
        self._subagents: set[str] = set()
        self._lock = threading.Lock()

    def observe(self, request_headers: Any, response_headers: Any) -> None:
        """Keep the LiteLLM headers of a response, under the Hermes session that sent the request."""
        session_id = request_headers.get(SESSION_HEADER)
        if not session_id:
            return
        served = ServedHeaders(
            model_id=response_headers.get(HEADER_MODEL_ID),
            attempted_fallbacks=_as_int(response_headers.get(HEADER_ATTEMPTED_FALLBACKS)),
        )
        with self._lock:
            self._served[session_id] = served

    def note_subagent(self, session_id: object) -> None:
        """Remember a child session, so its calls are marked ``subagent``."""
        if isinstance(session_id, str) and session_id:
            with self._lock:
                self._subagents.add(session_id)

    def record_call(self, **hook: Any) -> dict[str, Any] | None:
        """Write the line for one finished call. Calls of a provider that is not a tier's are ignored."""
        tier = tier_of_provider(hook.get("provider"))
        if tier is None:
            return None
        session_id = hook.get("session_id") or ""
        with self._lock:
            seen = self._served.pop(session_id, None)
            is_subagent = session_id in self._subagents
        line = {
            "event": "serving_model",
            "ts": round(self._clock(), 3),
            "session_id": session_id,
            "subagent": is_subagent,
            "tier": tier,
            "requested_model": hook.get("model"),
            "response_model": hook.get("response_model"),
            "litellm_model_id": seen.model_id if seen else None,
            "attempted_fallbacks": seen.attempted_fallbacks if seen else None,
            "finish_reason": hook.get("finish_reason"),
            "duration_s": hook.get("api_duration"),
        }
        self._sink(json.dumps(line, sort_keys=True, separators=(",", ":")))
        return line


def install_header_capture(recorder: ServingRecorder, httpx_module: Any) -> None:
    """Wrap ``httpx.Client.send`` and ``httpx.AsyncClient.send`` so each response's headers reach ``recorder``.

    The OpenAI client Hermes uses for every provider sits on httpx, and a streamed response has
    its headers when ``send`` returns, before the body is read. Calling twice wraps once.
    """
    if getattr(httpx_module.Client.send, "_serving_capture", False):
        return
    original_sync = httpx_module.Client.send
    original_async = httpx_module.AsyncClient.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        response = original_sync(self, request, **kwargs)
        recorder.observe(request.headers, response.headers)
        return response

    async def send_async(self: Any, request: Any, **kwargs: Any) -> Any:
        response = await original_async(self, request, **kwargs)
        recorder.observe(request.headers, response.headers)
        return response

    send._serving_capture = True  # type: ignore[attr-defined]
    httpx_module.Client.send = send
    httpx_module.AsyncClient.send = send_async
