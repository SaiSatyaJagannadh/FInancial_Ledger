"""Google's 503s.

"[503]: The service is currently unavailable" is Google saying it is busy, not
that anything is wrong with the sheet, the key or the data. It arrives at
random and clears in seconds. Before this, one of them dropped the whole app
into demo mode until the cache expired.

These tests drive the retry through `gspread`'s real `HTTPClient`, so what is
exercised is the class the live app actually installs.
"""

from __future__ import annotations

import pytest
import requests
from gspread.exceptions import APIError

from ledger import store


class FakeResponse:
    """Enough of requests.Response for gspread to build an APIError from."""

    def __init__(self, code: int):
        self.status_code = code
        self.ok = code < 400
        self.text = "The service is currently unavailable."

    def json(self):
        return {"error": {"code": self.status_code, "message": self.text,
                          "status": "UNAVAILABLE"}}


def client_over(answers, *, monkeypatch):
    """A retrying client whose underlying session replays `answers` in order.

    Each answer is a status code to return, or an exception to raise.
    """
    monkeypatch.setattr(store.time, "sleep", lambda _s: None)  # no real waiting
    calls: list[str] = []

    class Session:
        def request(self, *, method, url, **_kw):
            calls.append(method)
            answer = answers[min(len(calls) - 1, len(answers) - 1)]
            if isinstance(answer, Exception):
                raise answer
            response = FakeResponse(answer)
            if not response.ok:
                raise APIError(response)
            return response

    client = store._retrying_http_client()(auth=None, session=Session())
    return client, calls


@pytest.mark.parametrize("code", sorted(store.RETRY_CODES))
def test_a_transient_google_failure_is_retried_until_it_passes(code, monkeypatch):
    client, calls = client_over([code, code, 200], monkeypatch=monkeypatch)
    assert client.request("GET", "https://sheets/x").ok
    assert len(calls) == 3


def test_it_gives_up_and_raises_after_the_last_attempt(monkeypatch):
    """A real outage still has to reach the page — quietly hanging is worse."""
    client, calls = client_over([503], monkeypatch=monkeypatch)
    with pytest.raises(APIError) as raised:
        client.request("GET", "https://sheets/x")
    assert raised.value.code == 503
    assert len(calls) == len(store.RETRY_WAITS) + 1


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_a_permanent_refusal_is_not_retried(code, monkeypatch):
    """A revoked key or a wrong sheet id will not pass on the fourth try, and
    sitting through four seconds of waiting to learn that helps nobody."""
    client, calls = client_over([code], monkeypatch=monkeypatch)
    with pytest.raises(APIError):
        client.request("GET", "https://sheets/x")
    assert len(calls) == 1


def test_a_dropped_read_is_retried(monkeypatch):
    client, calls = client_over(
        [requests.ConnectionError("connection reset"), 200], monkeypatch=monkeypatch
    )
    assert client.request("GET", "https://sheets/x").ok
    assert len(calls) == 2


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_a_dropped_write_is_never_repeated(method, monkeypatch):
    """The reply was lost, so the write may well have landed. Sending it again
    would append the same entry twice, which is worse than an error."""
    client, calls = client_over(
        [requests.Timeout("read timed out"), 200], monkeypatch=monkeypatch
    )
    with pytest.raises(requests.Timeout):
        client.request(method, "https://sheets/x")
    assert len(calls) == 1


def test_a_write_refused_by_google_is_retried(monkeypatch):
    """Different from the timeout above: a 503 is the API saying it did *not*
    act on the request, so repeating it cannot duplicate anything."""
    client, calls = client_over([503, 200], monkeypatch=monkeypatch)
    assert client.request("POST", "https://sheets/x").ok
    assert len(calls) == 2


def test_the_retrying_client_is_the_one_the_app_installs():
    """It is no use if the live path quietly builds a plain client instead."""
    from gspread.http_client import HTTPClient

    built = store._retrying_http_client()
    assert issubclass(built, HTTPClient)
    assert built is not HTTPClient
    assert built.request is not HTTPClient.request


def test_waiting_is_bounded_so_a_page_load_cannot_hang():
    assert sum(store.RETRY_WAITS) < 5, "a person is watching a blank page for this long"


def test_the_message_names_google_rather_than_a_python_class(monkeypatch):
    """`APIError: [503]: The service is currently unavailable` tells the reader
    nothing about whose fault it is or whether their data is safe."""
    def boom(_secrets):
        raise APIError(FakeResponse(503))

    monkeypatch.setattr(store, "_open_worksheet", boom)
    detail = store.load(
        secrets={"gcp_service_account": {"x": 1}, "sheet": {"id": "abc"}}
    ).detail
    assert "503" in detail and "Google" in detail
    assert "APIError" not in detail


def test_a_lost_share_reads_differently_from_an_outage():
    assert "revoked" in store._why(APIError(FakeResponse(403)))
    assert "cannot find" in store._why(APIError(FakeResponse(404)))


def test_the_reason_survives_however_gspread_wrapped_it():
    """gspread reports the same HTTP status three different ways, and only one
    of them is an `.code`. The reader gets the same sentence either way."""
    from gspread.exceptions import SpreadsheetNotFound

    assert "cannot find" in store._why(SpreadsheetNotFound(FakeResponse(404)))
    assert "refused access" in store._why(PermissionError())
    assert store._status_of(APIError(FakeResponse(503))) == 503
    assert store._status_of(ValueError("nothing http about it")) is None
