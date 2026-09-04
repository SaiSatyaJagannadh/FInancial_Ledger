"""Who is using the app, via Streamlit's own OIDC login.

No passwords live here, and none live in the workbook. Google verifies the
person and hands back an email address; the app only decides whether that
address is on the list. That is deliberate: the sheet *is* the database, so a
`users` tab would put password hashes in the same document the service account
and every shared viewer can already read — and worse, anyone able to edit that
document could add themselves a row and sign in as anybody.

**Off unless `[auth]` is configured.** Streamlit raises if `st.user` is touched
without it, and demo mode, the page tests and every local run have no such
section. `gate()` returning quietly in that case is what keeps them working.

The point of knowing who someone is, here, is attribution: `ledger/notify.py`
puts the address on the change email, so "who edited this" has an answer.
"""

from __future__ import annotations

import streamlit as st

#: Streamlit's own section. It needs redirect_uri, cookie_secret and a provider.
SECTION = "auth"

#: Addresses allowed in, read from `[auth].allowed`. An empty or missing list
#: means anybody who can authenticate with the provider may use the app, which
#: is only safe behind Streamlit's sharing list — `gate()` says so on screen
#: rather than letting it be a silent assumption.
ALLOWED = "allowed"


def _secrets() -> dict:
    try:
        return dict(st.secrets)
    except Exception:  # noqa: BLE001 — no secrets file at all
        return {}


def configured(secrets: dict | None = None) -> bool:
    """Is OIDC login set up? Absent means the app runs open, as it always has."""
    secrets = _secrets() if secrets is None else secrets
    section = secrets.get(SECTION) or {}
    try:
        section = dict(section)
    except Exception:  # noqa: BLE001
        return False
    # Streamlit needs both of these plus a provider; without them st.user raises.
    return bool(section.get("redirect_uri") and section.get("cookie_secret"))


def allowed_emails(secrets: dict | None = None) -> list[str]:
    secrets = _secrets() if secrets is None else secrets
    section = secrets.get(SECTION) or {}
    try:
        raw = dict(section).get(ALLOWED) or []
    except Exception:  # noqa: BLE001
        return []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(",", " ").split()]
    return [str(item).strip().lower() for item in raw if str(item).strip()]


def permitted(email: str, secrets: dict | None = None) -> bool:
    """An empty list lets everyone in — see ALLOWED for why that is announced."""
    allowed = allowed_emails(secrets)
    return True if not allowed else str(email or "").strip().lower() in allowed


def current_user() -> str:
    """The signed-in address, or empty when there is no login configured.

    Called from the write paths, which also run under pytest with no Streamlit
    runtime at all, so every failure here is answered with "nobody" rather than
    an exception. Attribution is a nice-to-have; saving the row is not.
    """
    try:
        if not configured():
            return ""
        if not st.user.is_logged_in:
            return ""
        return str(st.user.get("email") or "")
    except Exception:  # noqa: BLE001 — no runtime, no secrets, no session
        return ""


def gate() -> None:
    """Stop the page unless somebody permitted is signed in.

    Does nothing when `[auth]` is absent, which is how the app behaved before
    this existed and how it still behaves in demo mode and in the page tests.
    """
    if not configured():
        return

    if not st.user.is_logged_in:
        st.title("Personal Ledger")
        st.caption("This ledger is private. Sign in to continue.")
        st.button("Sign in with Google", type="primary", on_click=st.login)
        st.stop()

    email = str(st.user.get("email") or "")
    if not permitted(email):
        st.title("Personal Ledger")
        st.error(
            f"**{email}** is not on the access list for this ledger. "
            "Ask the owner to add you."
        )
        st.button("Sign out", on_click=st.logout)
        st.stop()


def sidebar_identity() -> None:
    """Say who is signed in, with a way out. Shown on every page by the router."""
    if not configured() or not current_user():
        return
    with st.sidebar:
        st.caption(f"Signed in as {current_user()}")
        st.button("Sign out", width="stretch", on_click=st.logout)


def demo() -> None:
    """Self-check for the list handling, which is what decides who gets in."""
    assert configured({}) is False
    assert configured({"auth": {}}) is False
    assert configured({"auth": {"redirect_uri": "x"}}) is False, "needs both"
    assert configured({"auth": {"redirect_uri": "x", "cookie_secret": "y"}}) is True

    both = {"auth": {"allowed": ["A@Example.com ", "b@example.com"]}}
    assert allowed_emails(both) == ["a@example.com", "b@example.com"]
    # Hand-edited TOML: a plain string is what people actually type.
    assert allowed_emails({"auth": {"allowed": "a@x.com, b@x.com"}}) == \
        ["a@x.com", "b@x.com"]

    assert permitted("A@EXAMPLE.COM", both), "matching must ignore case"
    assert not permitted("c@example.com", both)
    # No list means no restriction — deliberate, and said on screen.
    assert permitted("anyone@anywhere.com", {"auth": {}})
    assert permitted("", {"auth": {}})

    # Outside a Streamlit runtime this must answer, not raise.
    assert current_user() == ""

    print("ledger.auth: all checks passed")


if __name__ == "__main__":
    demo()
