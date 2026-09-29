"""Staying signed in across a refresh, without becoming a way in for anybody else.

A Streamlit session lives inside one websocket connection, so a browser refresh
starts an empty one and the sign-in was gone every time — which is what this
cookie fixes. It is also the one piece of state in this app that a person can
edit and hand back, so the tests below are mostly about refusing it: a tampered
token, an expired one, one signed with another key, and one presented after the
person signed out.
"""

from __future__ import annotations

import json
import time

import pytest

from ledger import auth

KEY = b"a test key, thirty-two bytes ok!"
OTHER = b"a different key entirely........"

WITH_SECRET = {"accounts": {"cookie_secret": "configured-secret"}}
WITH_SERVICE_ACCOUNT = {
    "gcp_service_account": {"private_key": "-----BEGIN PRIVATE KEY-----\nabc\n"}
}


# ------------------------------------------------------------------ the token

def test_a_token_names_the_person_who_signed_in():
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    assert auth.read_token(token, KEY) == ("ravi@example.com", "Ravi")


def test_a_tampered_payload_is_nobody():
    """The address is readable — it is their own — but it must not be
    *changeable*, or the cookie is a login form for every other account."""
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    version, body, signature = token.split(".")
    swapped = auth._b64(
        json.dumps({"e": "amma@example.com", "n": "Amma", "x": 99999999999}).encode()
    )
    assert auth.read_token(f"{version}.{swapped}.{signature}", KEY) == ("", "")


@pytest.mark.parametrize("broken", [
    "", "nonsense", "v1.only-two-parts", "v2.abc.def",
    "v1.!!!not base64!!!.abc", "v1.abc.!!!",
])
def test_anything_malformed_is_nobody_rather_than_an_exception(broken):
    assert auth.read_token(broken, KEY) == ("", "")


def test_a_token_signed_with_another_key_is_nobody():
    token = auth.make_token("ravi@example.com", "Ravi", OTHER)
    assert auth.read_token(token, KEY) == ("", "")


def test_an_expired_token_is_nobody():
    token = auth.make_token("ravi@example.com", "Ravi", KEY, days=1)
    assert auth.read_token(token, KEY, now=time.time() + 2 * 86400) == ("", "")
    assert auth.read_token(token, KEY, now=time.time() + 3600)[0] == "ravi@example.com"


def test_the_token_stops_at_the_day_it_says():
    now = 1_000_000.0
    token = auth.make_token("ravi@example.com", "Ravi", KEY, now=now,
                            days=auth.REMEMBER_DAYS)
    lasts = auth.REMEMBER_DAYS * 86400
    assert auth.read_token(token, KEY, now=now + lasts - 5)[0] == "ravi@example.com"
    assert auth.read_token(token, KEY, now=now + lasts + 5) == ("", "")


def test_without_a_key_nothing_is_issued_and_nothing_is_accepted():
    """No secret, no remembering. An unsigned token is a forgeable one, which
    is far worse than signing in again after a refresh."""
    assert auth.make_token("ravi@example.com", "Ravi", b"") == ""
    assert auth.read_token(auth.make_token("r@x.com", "R", KEY), b"") == ("", "")


# -------------------------------------------------------------------- the key

def test_a_configured_secret_is_used_as_it_is():
    assert auth._remember_key(WITH_SECRET) == b"configured-secret"


def test_the_service_account_key_is_derived_from_never_used_directly():
    derived = auth._remember_key(WITH_SERVICE_ACCOUNT)
    private = WITH_SERVICE_ACCOUNT["gcp_service_account"]["private_key"]
    assert derived and derived != private.encode()
    assert private.encode() not in derived
    assert len(derived) == 32
    # Same secrets, same key — a key that moved between runs would sign
    # everybody out on every deploy.
    assert auth._remember_key(WITH_SERVICE_ACCOUNT) == derived


def test_a_configured_secret_wins_over_the_derived_one():
    both = {**WITH_SECRET, **WITH_SERVICE_ACCOUNT}
    assert auth._remember_key(both) == b"configured-secret"


def test_no_secret_at_all_means_no_key():
    assert auth._remember_key({}) == b""
    assert auth._remember_key({"accounts": {"enabled": True}}) == b""
    assert auth._remember_key({"gcp_service_account": {}}) == b""


def test_an_unreadable_section_is_no_key_rather_than_a_crash():
    class Hostile:
        def __iter__(self):
            raise RuntimeError("not a mapping")

    assert auth._remember_key({"accounts": Hostile()}) == b""


# ----------------------------------------------------------------- the cookie

def test_the_cookie_is_scoped_and_time_limited():
    script = auth._cookie_script("token-here", 1209600)
    assert "ledger_session=token-here" in script
    assert "Path=/" in script and "Max-Age=1209600" in script
    assert "SameSite=Lax" in script


def test_clearing_the_cookie_expires_it_immediately():
    assert "Max-Age=0" in auth._cookie_script("", 0)


def test_secure_is_set_on_https_and_not_on_a_local_run(monkeypatch):
    """A Secure cookie is dropped silently over plain http, which would make
    this look broken on localhost and work only once deployed."""
    class Context:
        url = "https://family-financialledger.streamlit.app/"

    monkeypatch.setattr(auth.st, "context", Context)
    assert "; Secure" in auth._cookie_script("t", 10)

    Context.url = "http://localhost:8501/"
    assert "; Secure" not in auth._cookie_script("t", 10)


# --------------------------------------------------- putting it back, or not

class FakeContext:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}
        self.url = "https://example.streamlit.app/"


def session(monkeypatch, cookies=None, **state):
    """A bare session and a browser, with no Streamlit runtime behind either."""
    monkeypatch.setattr(auth.st, "session_state", dict(state))
    monkeypatch.setattr(auth.st, "context", FakeContext(cookies))
    monkeypatch.setattr(auth, "_remember_key", lambda *a, **k: KEY)
    return auth.st.session_state


def test_a_valid_cookie_signs_the_session_back_in(monkeypatch):
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    state = session(monkeypatch, {auth.COOKIE: token})
    auth._restore()
    assert state[auth.SESSION] == "ravi@example.com"
    assert state[auth.SESSION_NAME] == "Ravi"


def test_a_forged_cookie_signs_nobody_in(monkeypatch):
    state = session(monkeypatch, {auth.COOKIE: auth.make_token("x@y.com", "X", OTHER)})
    auth._restore()
    assert auth.SESSION not in state


def test_no_cookie_leaves_the_session_empty(monkeypatch):
    state = session(monkeypatch, {})
    auth._restore()
    assert auth.SESSION not in state


def test_a_session_already_signed_in_is_left_alone(monkeypatch):
    token = auth.make_token("amma@example.com", "Amma", KEY)
    state = session(monkeypatch, {auth.COOKIE: token},
                    **{auth.SESSION: "ravi@example.com"})
    auth._restore()
    assert state[auth.SESSION] == "ravi@example.com", "the cookie must not take over"


def test_signing_out_cannot_be_undone_by_the_cookie_still_in_the_page(monkeypatch):
    """`st.context.cookies` holds what the page was *loaded* with, so the
    token is still there for the rest of this run. Without the latch the next
    rerun would sign them straight back in — the sign-out button would do
    nothing at all."""
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    state = session(monkeypatch, {auth.COOKIE: token},
                    **{auth.SESSION: "ravi@example.com",
                       auth.SESSION_NAME: "Ravi"})
    auth._sign_out()
    assert state.get(auth.SESSION_NAME) is None
    auth._restore()
    assert auth.SESSION not in state, "they must stay signed out"
    assert state[auth.SIGNED_OUT] is True


def test_restoring_is_attempted_once_per_session(monkeypatch):
    """The latch also means a cookie cleared mid-session is not re-read."""
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    state = session(monkeypatch, {auth.COOKIE: token})
    auth._restore()
    del state[auth.SESSION]
    auth._restore()
    assert auth.SESSION not in state


def test_a_sign_in_queues_the_cookie_rather_than_writing_it_mid_run(monkeypatch):
    """`st.rerun()` throws away everything the run had drawn, so a script
    rendered beside the sign-in would never reach the browser."""
    state = session(monkeypatch)
    state[auth._PENDING] = auth.make_token("ravi@example.com", "Ravi", KEY)
    written = []
    monkeypatch.setattr(auth, "_write_cookie", lambda v, s: written.append((v, s)))
    auth._flush_cookie()
    assert len(written) == 1
    value, seconds = written[0]
    assert auth.read_token(value, KEY)[0] == "ravi@example.com"
    assert seconds == auth.REMEMBER_DAYS * 86400
    assert auth._PENDING not in state, "and only once"

    auth._flush_cookie()
    assert len(written) == 1


# ------------------------------------------------- the gate, as a refresh hits it

def refreshed(monkeypatch, cookies) -> "AppTest":
    """The router's own two lines, in a session as empty as a refresh leaves it.

    `accounts.load` raises: if the cookie does its job the `users` tab is never
    read, which is the same discipline `tests/test_auth_gate.py` holds the rest
    of the gate to.
    """
    from streamlit.testing.v1 import AppTest

    from ledger import accounts

    monkeypatch.setattr(accounts, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(accounts, "load", _no_sheet)
    monkeypatch.setattr(auth, "configured", lambda *a, **k: False)
    monkeypatch.setattr(auth, "_remember_key", lambda *a, **k: KEY)
    monkeypatch.setattr(auth.st, "context", FakeContext(cookies))
    monkeypatch.setattr(auth, "_write_cookie", lambda *a, **k: None)

    def script() -> None:
        import streamlit as st

        from ledger import auth as _auth

        _auth.gate()
        _auth.sidebar_identity()
        st.title("Personal Ledger")

    return AppTest.from_function(script, default_timeout=30)


def _no_sheet(*_a, **_k):
    raise AssertionError("a remembered sign-in must not cost a sheet read")


def test_a_refresh_with_the_cookie_lands_in_the_app_not_on_the_form(monkeypatch):
    """The bug this fixes: every refresh started an empty session, so the gate
    concluded nobody was signed in and showed the form again."""
    token = auth.make_token("ravi@example.com", "Ravi", KEY)
    app = refreshed(monkeypatch, {auth.COOKIE: token}).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.title[0].value == "Personal Ledger"
    assert any("Ravi" in c.value for c in app.sidebar.caption)


def test_a_refresh_without_the_cookie_still_asks_who_you_are(monkeypatch):
    app = refreshed(monkeypatch, {})
    monkeypatch.setattr("ledger.accounts.load", lambda *a, **k: ([], []))
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert not app.title, "the app itself must not be reachable"


def test_a_forged_cookie_does_not_get_past_the_gate(monkeypatch):
    app = refreshed(monkeypatch, {auth.COOKIE: auth.make_token("x@y.com", "X", OTHER)})
    monkeypatch.setattr("ledger.accounts.load", lambda *a, **k: ([], []))
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert not app.title


# ------------------------------------------------------------- the home button

def home_app(monkeypatch):
    """The control as the router draws it. Deliberately patches nothing — a
    test that wants to watch `clear_cache` patches it itself, and patching it
    here would quietly overwrite that."""
    from streamlit.testing.v1 import AppTest

    def script() -> None:
        import streamlit as st

        from ledger.ui import home_button

        home_button()
        st.title("Ledger")

    app = AppTest.from_function(script, default_timeout=30)
    app.session_state[auth.SESSION] = "ravi@example.com"
    app.session_state[auth.SESSION_NAME] = "Ravi"
    return app


def test_home_is_offered_on_the_page(monkeypatch):
    app = home_app(monkeypatch).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.button(key="khata_home_button") is not None


def test_home_refreshes_the_figures_without_signing_anybody_out(monkeypatch):
    """The whole point of the control: going back for fresh numbers must not
    be the same gesture as throwing the session away."""
    from ledger import ui

    cleared = []
    monkeypatch.setattr(ui, "clear_cache", lambda: cleared.append(True))
    switched = []
    monkeypatch.setattr(ui.st, "switch_page", lambda page: switched.append(page))

    app = home_app(monkeypatch).run()
    app.button(key="khata_home_button").click().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert cleared, "the cached sheet read should be dropped"
    assert switched == ["views/dashboard.py"]
    assert app.session_state[auth.SESSION] == "ravi@example.com", "still signed in"
