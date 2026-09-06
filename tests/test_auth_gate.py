"""The gate, run the way a browser runs it.

This exists because of a bug that looked like a delete bug. Confirming the
deletion of a ₹1 entry sent the person back to the sign-in form: the gate
re-read the `users` tab on *every* rerun to decide who was signed in, and the
rerun immediately after a delete follows an archive append, a row delete and a
notification — so it was the read most likely to meet one of Google's random
503s. `accounts.load` answers a failure with "no accounts", `find` then answered
None, and the app concluded nobody was signed in and threw away what they were
doing.

Nothing below touches a sheet. That is the point: being signed in is a fact
about the session, and these fail if reading the workbook creeps back into it.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from ledger import accounts, auth


def _boom(*_args, **_kwargs):
    raise AssertionError("the users tab must not be read to answer 'who is signed in'")


def gate_app(signed_in: str = "", signed_out: bool = False) -> AppTest:
    """The router's two lines — the gate and the identity — around a page."""
    def script() -> None:
        import streamlit as st

        from ledger import auth as _auth

        _auth.gate()
        _auth.sidebar_identity()
        st.title("Personal Ledger")

    app = AppTest.from_function(script, default_timeout=30)
    if signed_in:
        app.session_state[auth.SESSION] = signed_in
        app.session_state[auth.SESSION_NAME] = "Ravi"
    if signed_out:
        app.session_state[auth.SIGNED_OUT] = True
    return app


@pytest.fixture()
def accounts_on(monkeypatch):
    """Password accounts switched on, with a sheet that fails if touched."""
    monkeypatch.setattr(accounts, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(accounts, "load", _boom)


def test_a_signed_in_session_reaches_the_app_without_reading_the_sheet(accounts_on):
    app = gate_app(signed_in="ravi@example.com").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.title[0].value == "Personal Ledger", "the gate should have let them in"
    assert any("Ravi" in c.value for c in app.sidebar.caption), "and said who they are"


def test_signing_out_shows_a_signed_out_page_not_the_form_again(accounts_on):
    app = gate_app(signed_out=True).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert not app.session_state.filtered_state.get(auth.SESSION)
    labels = [b.label for b in app.button]
    assert labels == ["Sign in again"], labels
    # st.stop() ran, so nothing of the app itself was built.
    assert not app.tabs, "the sign-in form belongs on the next screen, not this one"


def test_the_way_back_in_leads_to_the_form(monkeypatch):
    monkeypatch.setattr(accounts, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(accounts, "load", lambda *a, **k: ([], []))

    app = gate_app(signed_out=True).run()
    app.button[0].click().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert auth.SIGNED_OUT not in app.session_state.filtered_state
    assert [t.label for t in app.tabs][:1] == ["Sign in"], [t.label for t in app.tabs]


def test_sign_out_clears_the_typed_email_as_well_as_the_session():
    """Signing out and back in must not show the next person the last one's address."""
    app = gate_app(signed_in="ravi@example.com")
    app.session_state["login_email"] = "ravi@example.com"
    app.session_state[auth._RESET] = {"email": "ravi@example.com"}

    def script() -> None:
        from ledger import auth as _auth

        _auth._sign_out()

    signing_out = AppTest.from_function(script, default_timeout=30)
    for key, value in app.session_state.filtered_state.items():
        signing_out.session_state[key] = value
    signing_out.run()

    left = signing_out.session_state.filtered_state
    assert not left.get(auth.SESSION) and not left.get(auth.SESSION_NAME)
    assert not left.get("login_email") and not left.get(auth._RESET)
    assert left[auth.SIGNED_OUT] is True


def test_the_gate_does_nothing_at_all_when_accounts_are_off(monkeypatch):
    """Demo mode and the page tests run views directly; the gate must not bite."""
    monkeypatch.setattr(accounts, "enabled", lambda *a, **k: False)
    app = gate_app().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.title[0].value == "Personal Ledger"
    assert not app.sidebar.caption, "nobody is signed in, so nothing to say"
