"""Network failures must leave this module as AssistantError, never raw.

A requests exception escaping put a red traceback on the page instead of
something the reader could act on, which is what "the chatbot is broken"
looked like from the outside.
"""

from __future__ import annotations

import pytest
import requests

from ledger import assistant
from ledger.assistant import ATTEMPTS, AssistantError, _post

PAYLOAD = {"model": "m", "messages": []}


class FakeResponse:
    def __init__(self, status=200, content="{}", body=None):
        self.status_code = status
        self.text = content
        self._body = body if body is not None else {
            "choices": [{"message": {"content": content}}]
        }

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Retries are real; waiting for them in a test is not."""
    monkeypatch.setattr("time.sleep", lambda _s: None)


def posts(monkeypatch, *outcomes):
    """Queue up what requests.post does on each successive call."""
    calls = {"n": 0}

    def fake_post(*_a, **_kw):
        index = min(calls["n"], len(outcomes) - 1)
        calls["n"] += 1
        outcome = outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


# ---------------------------------------------------------------- containment

@pytest.mark.parametrize("blowup", [
    requests.exceptions.Timeout("read timed out"),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectionError("dns failure"),
    requests.exceptions.RequestException("something else"),
])
def test_every_network_failure_becomes_an_assistant_error(monkeypatch, blowup):
    posts(monkeypatch, blowup)
    with pytest.raises(AssistantError):
        _post(PAYLOAD, "key", timeout=1)


def test_a_timeout_says_what_happened_in_plain_words(monkeypatch):
    posts(monkeypatch, requests.exceptions.Timeout("x"))
    with pytest.raises(AssistantError, match="did not answer within"):
        _post(PAYLOAD, "key", timeout=7)


def test_an_unreachable_host_says_so(monkeypatch):
    posts(monkeypatch, requests.exceptions.ConnectionError("x"))
    with pytest.raises(AssistantError, match="Could not reach"):
        _post(PAYLOAD, "key", timeout=1)


def test_read_note_does_not_leak_a_requests_exception(monkeypatch):
    """The view catches AssistantError; anything else reaches the page."""
    posts(monkeypatch, requests.exceptions.ReadTimeout("x"))
    with pytest.raises(AssistantError):
        assistant.read_note("gave 500 to amma", api_key="key")


# -------------------------------------------------------------------- retries

def test_it_retries_and_succeeds(monkeypatch):
    calls = posts(
        monkeypatch,
        requests.exceptions.Timeout("first"),
        FakeResponse(content='{"entries": []}'),
    )
    assert _post(PAYLOAD, "key", timeout=1) == '{"entries": []}'
    assert calls["n"] == 2


def test_it_gives_up_after_the_agreed_number_of_attempts(monkeypatch):
    calls = posts(monkeypatch, requests.exceptions.Timeout("always"))
    with pytest.raises(AssistantError, match=f"tried {ATTEMPTS} times"):
        _post(PAYLOAD, "key", timeout=1)
    assert calls["n"] == ATTEMPTS


def test_a_server_error_is_retried(monkeypatch):
    """500 means their end is unwell; the same request may well work."""
    calls = posts(monkeypatch, FakeResponse(status=503, content="unavailable"),
                  FakeResponse(content="ok"))
    assert _post(PAYLOAD, "key", timeout=1) == "ok"
    assert calls["n"] == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_client_error_is_not_retried(monkeypatch, status):
    """A bad key fails identically every time; retrying just wastes the wait."""
    calls = posts(monkeypatch, FakeResponse(status=status, content="nope"))
    with pytest.raises(AssistantError, match=str(status)):
        _post(PAYLOAD, "key", timeout=1)
    assert calls["n"] == 1


def test_a_successful_call_is_made_once(monkeypatch):
    calls = posts(monkeypatch, FakeResponse(content="fine"))
    assert _post(PAYLOAD, "key", timeout=1) == "fine"
    assert calls["n"] == 1


def test_a_malformed_body_is_not_retried(monkeypatch):
    """Valid HTTP but nonsense inside is not transient."""
    calls = posts(monkeypatch, FakeResponse(body={"unexpected": True}))
    with pytest.raises(AssistantError, match="Unexpected response shape"):
        _post(PAYLOAD, "key", timeout=1)
    assert calls["n"] == 1
