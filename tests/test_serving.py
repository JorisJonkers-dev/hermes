"""The serving-model log: one JSON line per model HTTP response, with the model LiteLLM says served it."""

import asyncio
import json

import httpx
import pytest

URL = "http://litellm.test/v1/chat/completions"
LITELLM = {"x-litellm-model-id": "free/hy4-flash-free", "x-litellm-attempted-fallbacks": "1"}


@pytest.fixture
def lines():
    return []


@pytest.fixture
def recorder(serving, lines):
    ids = iter(f"req-{n}" for n in range(100))
    return serving.ServingRecorder(sink=lines.append, clock=lambda: 1700000000.0, new_id=lambda: next(ids))


def respond(recorder, *, url=URL, body=None, headers=None, session="child-1", status=200, duration=1.25):
    request_headers = {"x-hermes-session": session} if session else {}
    request = httpx.Request("POST", url, headers=request_headers, content=json.dumps(body or {"model": "tier-free"}))
    return recorder.record_response(request, httpx.Response(status, headers=headers or {}), duration)


def test_the_serving_model_of_a_call_is_logged_as_one_json_line(recorder, lines):
    respond(recorder, headers=LITELLM)
    assert json.loads(lines[0]) == {
        "event": "serving_model",
        "ts": 1700000000.0,
        "request_id": "req-0",
        "session_id": "child-1",
        "subagent": False,
        "host": "litellm.test",
        "requested_model": "tier-free",
        "in_tier_map": True,
        "via_litellm": True,
        "litellm_model_id": "free/hy4-flash-free",
        "attempted_fallbacks": 1,
        "status": 200,
        "duration_s": 1.25,
    }
    assert len(lines) == 1


def test_a_call_of_a_child_session_is_marked_as_a_subagent(recorder, lines):
    recorder.note_subagent("child-1")
    respond(recorder, headers=LITELLM)
    assert json.loads(lines[0])["subagent"] is True


@pytest.mark.parametrize("session_id", ["", None, 5])
def test_a_child_without_a_usable_session_id_marks_nothing(recorder, lines, session_id):
    recorder.note_subagent(session_id)
    respond(recorder, headers=LITELLM, session=None)
    entry = json.loads(lines[0])
    assert (entry["subagent"], entry["session_id"]) == (False, None)


def test_overlapping_calls_of_one_session_each_log_their_own_model(recorder, lines):
    respond(recorder, headers={"x-litellm-model-id": "first"})
    respond(recorder, headers={"x-litellm-model-id": "second"})
    assert [json.loads(line)["litellm_model_id"] for line in lines] == ["first", "second"]
    assert [json.loads(line)["request_id"] for line in lines] == ["req-0", "req-1"]


def test_a_model_outside_the_tier_map_is_still_logged_and_flagged(recorder, lines):
    respond(recorder, body={"model": "gpt-9"}, headers=LITELLM)
    entry = json.loads(lines[0])
    assert (entry["requested_model"], entry["in_tier_map"]) == ("gpt-9", False)


def test_a_call_that_did_not_go_through_litellm_is_logged_and_flagged(recorder, lines):
    respond(recorder, url="https://api.example.org/v1/chat/completions", body={"model": "x"})
    entry = json.loads(lines[0])
    assert (entry["via_litellm"], entry["litellm_model_id"], entry["host"]) == (False, None, "api.example.org")


@pytest.mark.parametrize("value", ["many", ""])
def test_a_malformed_fallback_count_is_logged_as_unknown(recorder, lines, value):
    respond(recorder, headers={**LITELLM, "x-litellm-attempted-fallbacks": value})
    assert json.loads(lines[0])["attempted_fallbacks"] is None


@pytest.mark.parametrize("content", [b"not json", b"[1]", b'{"model": 3}'])
def test_a_body_without_a_model_string_logs_no_requested_model(recorder, lines, content):
    request = httpx.Request("POST", URL, content=content)
    recorder.record_response(request, httpx.Response(200, headers=LITELLM), 0.1)
    assert json.loads(lines[0])["requested_model"] is None


def test_a_streamed_request_body_that_was_not_read_logs_no_requested_model(recorder, lines):
    request = httpx.Request("POST", URL, content=iter([b"{}"]))
    recorder.record_response(request, httpx.Response(200, headers=LITELLM), 0.1)
    assert json.loads(lines[0])["requested_model"] is None


@pytest.mark.parametrize("url", ["http://litellm.test/v1/models", "https://github.com/org/repo.git/info/refs"])
def test_a_request_that_is_not_a_model_call_is_not_logged(recorder, lines, url):
    assert respond(recorder, url=url) is None
    assert lines == []


def test_a_line_holds_no_prompt_or_completion_text(recorder, lines):
    respond(recorder, body={"model": "tier-free", "messages": [{"content": "secret prompt"}]}, headers=LITELLM)
    assert "secret" not in lines[0]


def test_the_default_sink_writes_the_line_to_stdout(serving, capsys):
    respond(serving.ServingRecorder(), headers=LITELLM)
    assert json.loads(capsys.readouterr().out)["requested_model"] == "tier-free"


def _answering(headers):
    return httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, json={"ok": True}))


@pytest.fixture
def installed(serving, recorder):
    original = (httpx.Client.send, httpx.AsyncClient.send)
    serving.install_response_log(recorder, httpx)
    yield
    httpx.Client.send, httpx.AsyncClient.send = original


def test_a_synchronous_response_is_logged_as_it_arrives(installed, lines):
    with httpx.Client(transport=_answering(LITELLM)) as client:
        client.post(URL, json={"model": "tier-vibe"}, headers={"x-hermes-session": "s"})
    entry = json.loads(lines[0])
    assert (entry["requested_model"], entry["litellm_model_id"], entry["session_id"]) == (
        "tier-vibe",
        "free/hy4-flash-free",
        "s",
    )


def test_an_asynchronous_response_is_logged_as_it_arrives(installed, lines):
    async def call():
        async with httpx.AsyncClient(transport=_answering(LITELLM)) as client:
            await client.post(URL, json={"model": "tier-review"}, headers={"x-hermes-session": "s"})

    asyncio.run(call())
    assert json.loads(lines[0])["requested_model"] == "tier-review"


def test_installing_twice_wraps_send_once(serving, recorder):
    original = (httpx.Client.send, httpx.AsyncClient.send)
    try:
        serving.install_response_log(recorder, httpx)
        wrapped = httpx.Client.send
        serving.install_response_log(recorder, httpx)
        assert httpx.Client.send is wrapped
    finally:
        httpx.Client.send, httpx.AsyncClient.send = original
