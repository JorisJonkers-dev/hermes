"""The serving-model log: one JSON line per tier call, with the model LiteLLM says served it."""

import asyncio
import json

import httpx
import pytest


@pytest.fixture
def lines():
    return []


@pytest.fixture
def recorder(serving, lines):
    return serving.ServingRecorder(sink=lines.append, clock=lambda: 1700000000.0)


def seen(recorder, response_headers, session="child-1"):
    recorder.observe({"x-hermes-session": session}, response_headers)


HOOK = {
    "session_id": "child-1",
    "provider": "litellm-tier-free",
    "model": "tier-free",
    "response_model": "hy4-flash-free",
    "finish_reason": "stop",
    "api_duration": 1.25,
}


def test_the_serving_model_of_a_call_is_logged_as_one_json_line(recorder, lines):
    seen(recorder, {"x-litellm-model-id": "free/hy4-flash-free", "x-litellm-attempted-fallbacks": "1"})
    recorder.record_call(**HOOK)
    assert json.loads(lines[0]) == {
        "event": "serving_model",
        "ts": 1700000000.0,
        "session_id": "child-1",
        "subagent": False,
        "tier": "tier-free",
        "requested_model": "tier-free",
        "response_model": "hy4-flash-free",
        "litellm_model_id": "free/hy4-flash-free",
        "attempted_fallbacks": 1,
        "finish_reason": "stop",
        "duration_s": 1.25,
    }
    assert len(lines) == 1


def test_a_call_of_a_child_session_is_marked_as_a_subagent(recorder, lines):
    recorder.note_subagent("child-1")
    recorder.record_call(**HOOK)
    assert json.loads(lines[0])["subagent"] is True


@pytest.mark.parametrize("session_id", ["", None, 5])
def test_a_child_without_a_usable_session_id_marks_nothing(recorder, lines, session_id):
    recorder.note_subagent(session_id)
    recorder.record_call(**{**HOOK, "session_id": ""})
    assert json.loads(lines[0])["subagent"] is False


def test_a_call_whose_headers_were_not_seen_is_still_logged_by_the_body_model(recorder, lines):
    recorder.record_call(**HOOK)
    entry = json.loads(lines[0])
    assert (entry["response_model"], entry["litellm_model_id"], entry["attempted_fallbacks"]) == (
        "hy4-flash-free",
        None,
        None,
    )


def test_headers_serve_one_call_only(recorder, lines):
    seen(recorder, {"x-litellm-model-id": "a"})
    recorder.record_call(**HOOK)
    recorder.record_call(**HOOK)
    assert [json.loads(line)["litellm_model_id"] for line in lines] == ["a", None]


def test_a_malformed_fallback_count_is_logged_as_unknown(recorder, lines):
    seen(recorder, {"x-litellm-model-id": "a", "x-litellm-attempted-fallbacks": "many"})
    recorder.record_call(**HOOK)
    assert json.loads(lines[0])["attempted_fallbacks"] is None


def test_a_call_of_a_provider_that_is_not_a_tiers_is_not_logged(recorder, lines):
    assert recorder.record_call(**{**HOOK, "provider": "openrouter"}) is None
    assert lines == []


def test_two_sessions_answering_at_once_each_get_their_own_serving_model(recorder, lines):
    seen(recorder, {"x-litellm-model-id": "model-of-1"}, session="child-1")
    seen(recorder, {"x-litellm-model-id": "model-of-2"}, session="child-2")
    recorder.record_call(**{**HOOK, "session_id": "child-2"})
    recorder.record_call(**{**HOOK, "session_id": "child-1"})
    assert [json.loads(line)["litellm_model_id"] for line in lines] == ["model-of-2", "model-of-1"]


def test_a_response_to_a_request_without_a_session_header_is_not_kept(recorder, lines):
    recorder.observe({}, {"x-litellm-model-id": "orphan"})
    recorder.record_call(**{**HOOK, "session_id": ""})
    assert json.loads(lines[0])["litellm_model_id"] is None


def test_a_line_holds_no_prompt_or_completion_text(recorder, lines):
    recorder.record_call(**{**HOOK, "request": {"messages": ["secret prompt"]}, "response": {"text": "secret"}})
    assert "secret" not in lines[0]


def test_the_default_sink_writes_the_line_to_stdout(serving, capsys):
    serving.ServingRecorder().record_call(**HOOK)
    assert json.loads(capsys.readouterr().out)["tier"] == "tier-free"


def _answering(headers):
    return httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, json={"ok": True}))


@pytest.fixture
def captured(serving, recorder):
    original = (httpx.Client.send, httpx.AsyncClient.send)
    serving.install_header_capture(recorder, httpx)
    yield
    httpx.Client.send, httpx.AsyncClient.send = original


def test_the_headers_of_a_synchronous_response_reach_the_recorder(captured, recorder, lines):
    headers = {"x-litellm-model-id": "free/beam-free"}
    with httpx.Client(transport=_answering(headers)) as client:
        client.get("http://litellm.test/v1/chat/completions", headers={"x-hermes-session": "child-1"})
    recorder.record_call(**HOOK)
    assert json.loads(lines[0])["litellm_model_id"] == "free/beam-free"


def test_the_headers_of_an_asynchronous_response_reach_the_recorder(captured, recorder, lines):
    headers = {"x-litellm-model-id": "free/longcat-2.5-free"}

    async def call():
        async with httpx.AsyncClient(transport=_answering(headers)) as client:
            await client.get("http://litellm.test/v1/chat/completions", headers={"x-hermes-session": "child-1"})

    asyncio.run(call())
    recorder.record_call(**HOOK)
    assert json.loads(lines[0])["litellm_model_id"] == "free/longcat-2.5-free"


def test_installing_twice_wraps_send_once(serving, recorder):
    original = (httpx.Client.send, httpx.AsyncClient.send)
    try:
        serving.install_header_capture(recorder, httpx)
        wrapped = httpx.Client.send
        serving.install_header_capture(recorder, httpx)
        assert httpx.Client.send is wrapped
    finally:
        httpx.Client.send, httpx.AsyncClient.send = original
