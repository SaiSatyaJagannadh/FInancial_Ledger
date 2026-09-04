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


def allowed_emails(secrets: dict | None = None) -> list[str] | None:
    """The access list. `[]` means unrestricted; **None means unreadable**.

    The distinction is the whole point. An earlier version answered a parse
    failure with `[]`, and `[]` means "let everyone in" — so a typo in the
    secrets file would have opened the ledger to anybody with a Google account,
    silently. A list that cannot be read is a configuration the owner intended
    and this code failed to honour, and the only safe reading of that is "let
    nobody in until it is fixed".
    """
    secrets = _secrets() if secrets is None else secrets
    section = secrets.get(SECTION) or {}
    try:
        raw = dict(section).get(ALLOWED)
    except Exception:  # noqa: BLE001 — a section we cannot even read
        return None
    if raw is None:
        return []                       # not set at all: unrestricted, and said so
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(",", " ").split()]
    try:
        items = list(raw)               # a number, a bool, anything not iterable
    except TypeError:
        return None
    return [str(item).strip().lower() for item in items if str(item).strip()]


def permitted(email: str, secrets: dict | None = None) -> bool:
    """Is this address allowed in? A broken list denies rather than admits."""
    allowed = allowed_emails(secrets)
    if allowed is None:
        return False
    if not allowed:
        return True                     # deliberately unrestricted
    return str(email or "").strip().lower() in allowed


def current_user() -> str:
    """The signed-in address, whichever way they signed in, or empty.

    Called from the write paths, which also run under pytest with no Streamlit
    runtime at all, so every failure here is answered with "nobody" rather than
    an exception. Attribution is a nice-to-have; saving the row is not.
    """
    try:
        if configured():
            return str(st.user.get("email") or "") if st.user.is_logged_in else ""
        return str(st.session_state.get(SESSION) or "")
    except Exception:  # noqa: BLE001 — no runtime, no secrets, no session
        return ""


#: Where a password sign-in is remembered. Session state only: it lasts as long
#: as the browser tab and is gone on a refresh. A cookie would outlive that, but
#: signing it needs a secret and getting that wrong is worse than signing in
#: again.
SESSION = "account_email"


def signed_in_account():
    """The `users`-tab account signed in on this session, if any."""
    from ledger import accounts

    email = st.session_state.get(SESSION)
    if not email:
        return None
    known, _ = accounts.load()
    return accounts.find(email, known)


def _password_gate() -> None:
    """Sign in or register against the `users` tab, then stop the page.

    Renders in place of the app rather than as a page of its own, for the same
    reason the OIDC gate does: the router is the only way in, and a login that
    is itself a page is a login somebody can navigate around.
    """
    from ledger import accounts
    from ledger.models import EntryError

    if signed_in_account() is not None:
        return

    known, problems = accounts.load()
    for problem in problems:
        st.warning(problem)

    st.title("Personal Ledger")
    if st.session_state.pop("account_created", None):
        st.success(
            f"Account created for **{st.session_state.pop('account_created_email', '')}**. "
            "Sign in with it below."
        )

    first_ever = not known
    if first_ever:
        st.info(
            "No accounts yet. The first one created becomes yours — make it now, "
            "before the app is shared with anybody."
        )

    sign_in, sign_up = st.tabs(["Sign in", "Create an account"])

    with sign_in:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Sign in", type="primary", key="login_go"):
            account = accounts.authenticate(email, password)
            if account is None:
                # One message for both causes. Saying which was wrong tells a
                # stranger whether an address has an account here.
                st.error("Email or password is wrong.")
            else:
                st.session_state[SESSION] = account.email
                st.rerun()

    with sign_up:
        code_wanted = accounts.signup_code()
        if not code_wanted and not first_ever:
            st.warning(
                "Anyone who can open this page can create an account. Set "
                "`signup_code` under `[accounts]` in secrets to require a word."
            )
        name = st.text_input("Name", key="signup_name")
        new_email = st.text_input("Email", key="signup_email")
        new_password = st.text_input(
            f"Password (at least {accounts.MIN_PASSWORD} characters)",
            type="password", key="signup_password",
        )
        confirm = st.text_input("Password again", type="password", key="signup_confirm")
        code_given = st.text_input("Sign-up code", key="signup_code") if code_wanted else ""

        if st.button("Create account", type="primary", key="signup_go"):
            wrong = accounts.validate(name, new_email, new_password, confirm)
            if code_wanted and code_given.strip() != code_wanted:
                wrong.append("That sign-up code is not right.")
            for problem in wrong:
                st.error(problem)
            if not wrong:
                try:
                    made = accounts.create(name, new_email, new_password)
                except (EntryError, RuntimeError) as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001 — say what the sheet said
                    st.error(f"Could not create the account: {exc}")
                else:
                    # Registering is not signing in. Landing straight in the
                    # app hides whether the password actually works — the first
                    # time it gets typed should be now, while it is still in
                    # mind, not on some later visit when it is not.
                    st.session_state["account_created"] = True
                    st.session_state["account_created_email"] = made.email
                    st.rerun()

    st.stop()


def gate() -> None:
    """Stop the page unless somebody permitted is signed in.

    Two ways in, and Google wins when both are set up: it stores no password
    anywhere, while the `users` tab keeps hashes in the workbook where anyone
    who can edit the sheet could add themselves a row.

    Does nothing when neither is configured, which is how the app behaved before
    any of this existed and how it still behaves in demo mode and the page tests.
    """
    from ledger import accounts

    if not configured():
        if accounts.enabled():
            _password_gate()
        return

    if not st.user.is_logged_in:
        st.title("Personal Ledger")
        st.caption("This ledger is private. Sign in to continue.")
        st.button("Sign in with Google", type="primary", on_click=st.login)
        st.stop()

    email = str(st.user.get("email") or "")
    if allowed_emails() is None:
        # Locked, not refused — saying "you are not allowed" would send somebody
        # chasing an access request when the file is what needs fixing.
        st.title("Personal Ledger")
        st.error(
            "The access list in `[auth].allowed` cannot be read, so nobody is "
            "being let in. It must be a list of addresses, for example "
            '`allowed = ["you@gmail.com"]`.'
        )
        st.button("Sign out", on_click=st.logout)
        st.stop()

    if not permitted(email):
        st.title("Personal Ledger")
        st.error(
            f"**{email}** is not on the access list for this ledger. "
            "Ask the owner to add you."
        )
        st.button("Sign out", on_click=st.logout)
        st.stop()


def _sign_out() -> None:
    st.session_state.pop(SESSION, None)


def sidebar_identity() -> None:
    """Say who is signed in, with a way out. Shown on every page by the router."""
    if configured() and current_user():
        with st.sidebar:
            st.caption(f"Signed in as {current_user()}")
            st.button("Sign out", width="stretch", on_click=st.logout)
        return

    account = signed_in_account()
    if account is not None:
        with st.sidebar:
            st.caption(f"Signed in as {account.name or account.email}")
            st.button("Sign out", width="stretch", on_click=_sign_out)


def demo() -> None:
    """Self-check for the list handling, which is what decides who gets in."""
    assert configured({}) is False
    assert configured({"auth": {}}) is False
    assert configured({"auth": {"redirect_uri": "x"}}) is False, "needs both"
    assert configured({"auth": {"redirect_uri": "x", "cookie_secret": "y"}}) is True

    both = {"auth": {"allowed": ["A@Example.com ", "b@example.com"]}}
    assert allowed_emails(both) == ["a@example.com", "b@example.com"]

    # A list that cannot be read must lock the door, never open it. Answering
    # this with [] would have meant "unrestricted", which is the wrong way to
    # fail for the only thing standing between a stranger and the ledger.
    for broken in (5, True, 3.4):
        assert allowed_emails({"auth": {"allowed": broken}}) is None, broken
        assert permitted("anyone@anywhere.com", {"auth": {"allowed": broken}}) is False
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
