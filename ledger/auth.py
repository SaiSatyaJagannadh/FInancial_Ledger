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

from ledger.ui import INK, RULE   # the ledger's own ink and rule, so the way in
                                  # looks like the thing it leads to

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

#: The display name, kept beside it. Written once at sign-in so that showing
#: who is signed in costs nothing — see `signed_in_email`.
SESSION_NAME = "account_name"

#: Set by `_sign_out` so the next run says "signed out" rather than dropping
#: somebody straight back onto a form they did not ask for.
SIGNED_OUT = "signed_out"


def signed_in_email() -> str:
    """The password account signed in on this session, or "".

    **Session state alone, never a re-read of the `users` tab.** Checking the
    sheet on every rerun put a network call in front of every page render, and a
    single 503 — which Google hands out at random, and which a write-heavy rerun
    makes likelier — answered "no accounts", so `find` returned None and the
    person was thrown back to the login form mid-action. Deleting an entry was
    the reliable way to trigger it: archive, delete and notify all go out, then
    the very next read is this one. Nothing is being trusted here that was not
    already trusted: this key is only ever set by a verified sign-in.

    The cost is that removing somebody from the sheet does not end a session
    they already have. It ends when their browser tab does.
    """
    return str(st.session_state.get(SESSION) or "")


def signed_in_account():
    """The full `users`-tab record for this session — a sheet read.

    Not used to decide whether somebody is signed in; `signed_in_email` is.
    """
    from ledger import accounts

    email = signed_in_email()
    if not email:
        return None
    known, _ = accounts.load()
    return accounts.find(email, known)


#: The signed-out screen and the sign-in screen are the whole app while they are
#: up, so they hide the app's furniture. The sidebar matters most: `st.stop()`
#: runs before `st.navigation` does, so Streamlit keeps the *previous* run's page
#: list on screen — somebody who has just signed out was still being shown
#: Ledger, Add entry, Interest and the rest down the side of the login form.
_AUTH_CSS = f"""
<style>
  [data-testid="stSidebar"],
  [data-testid="stSidebarNav"],
  [data-testid="stSidebarCollapsedControl"],
  header[data-testid="stHeader"] {{ display: none !important; }}

  .block-container {{ max-width: 30rem; padding-top: 4rem; }}

  .auth-mark {{ font-size: 1.9rem; line-height: 1; }}
  .auth-name {{
      font-size: 1.9rem; font-weight: 700; letter-spacing: -0.025em;
      color: {INK}; margin: .35rem 0 .1rem 0;
  }}
  .auth-sub {{ font-size: .92rem; opacity: .6; margin: 0 0 1.5rem 0; }}

  /* One card, hairline-ruled, the same ink and rule the ledger itself uses. */
  div[data-testid="stForm"] {{
      border: 1px solid {RULE}; border-radius: 14px;
      padding: 1.2rem 1.2rem .4rem 1.2rem;
  }}
  [data-baseweb="tab-list"] {{ gap: 1.2rem; }}
</style>
"""


def _auth_chrome() -> None:
    """Strip the app down to the screen in front of you."""
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-mark">₹</div>'
        '<div class="auth-name">Personal Ledger</div>',
        unsafe_allow_html=True,
    )


def _signed_out_screen() -> None:
    """Say the sign-out happened, and stop. Then offer the way back in.

    Landing straight back on the sign-in form left nobody any sign that the
    button had worked — the same page they were just told to fill in, with their
    address gone from it. This is one screen, and it stays until they ask.
    """
    _auth_chrome()
    st.markdown(
        '<div class="auth-sub">You are signed out. Nothing from that session '
        "is left in this browser tab.</div>",
        unsafe_allow_html=True,
    )
    if st.button("Sign in again", type="primary", width="stretch"):
        st.session_state.pop(SIGNED_OUT, None)
        st.rerun()
    st.stop()


def _password_gate() -> None:
    """Sign in or register against the `users` tab, then stop the page.

    Renders in place of the app rather than as a page of its own, for the same
    reason the OIDC gate does: the router is the only way in, and a login that
    is itself a page is a login somebody can navigate around.
    """
    from ledger import accounts
    from ledger.models import EntryError

    if signed_in_email():
        return

    if st.session_state.get(SIGNED_OUT):
        _signed_out_screen()

    _auth_chrome()

    known, problems = accounts.load()
    for problem in problems:
        st.warning(problem)

    if st.session_state.pop("account_created", None):
        st.success(
            f"Account created for **{st.session_state.pop('account_created_email', '')}**. "
            "Sign in with it below."
        )
    else:
        st.markdown(
            '<div class="auth-sub">Private. Sign in to see it.</div>',
            unsafe_allow_html=True,
        )

    first_ever = not known
    if first_ever:
        st.info(
            "No accounts yet. The first one created becomes yours — make it now, "
            "before the app is shared with anybody."
        )

    sign_in, sign_up, forgot = st.tabs(
        ["Sign in", "Create an account", "Forgotten password"]
    )

    with sign_in:
        # A form, not loose inputs and a button. A browser filling a saved
        # password sets the field's value without firing the event Streamlit
        # listens for, so a plain button submits whatever the widget held
        # before — usually nothing, which is why an autofilled sign-in failed
        # and typing the very same thing by hand worked. A form gathers its
        # values at submit. It also makes Enter work, which is what anybody
        # expects of a password box.
        with st.form("sign_in_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password",
                                     key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            account = accounts.authenticate(email, password)
            if account is None:
                # One message for both causes. Saying which was wrong tells a
                # stranger whether an address has an account here.
                st.error("Email or password is wrong.")
            else:
                st.session_state[SESSION] = account.email
                # Kept beside it so the sidebar can say who is signed in
                # without reading the users tab again on every rerun.
                st.session_state[SESSION_NAME] = account.name or account.email
                st.rerun()

    with sign_up:
        code_wanted = accounts.signup_code()
        if not code_wanted and not first_ever:
            st.warning(
                "Anyone who can open this page can create an account. Set "
                "`signup_code` under `[accounts]` in secrets to require a word."
            )
        with st.form("sign_up_form"):
            name = st.text_input("Name", key="signup_name")
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input(
                f"Password (at least {accounts.MIN_PASSWORD} characters)",
                type="password", key="signup_password",
            )
            confirm = st.text_input("Password again", type="password",
                                    key="signup_confirm")
            code_given = (st.text_input("Sign-up code", key="signup_code")
                          if code_wanted else "")
            registering = st.form_submit_button("Create account", type="primary")

        if registering:
            wrong = accounts.validate(name, new_email, new_password, confirm)
            if code_wanted and code_given.strip() != code_wanted:
                wrong.append("That sign-up code is not right.")
            for problem in wrong:
                st.error(problem)
            if not wrong:
                try:
                    made = accounts.create(name, new_email, new_password)
                except EntryError as exc:
                    st.error(str(exc))
                    if "already an account" in str(exc):
                        st.info(
                            "If that account is yours and you have forgotten the "
                            "password, use the **Forgotten password** tab above."
                        )
                except RuntimeError as exc:
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

    with forgot:
        _reset_form(known)

    st.stop()


#: Where a pending reset lives while somebody goes to read their email. Session
#: state, so the code never touches the sheet and dies with the browser tab.
_RESET = "reset_pending"


def _reset_form(known: list) -> None:
    """Set a new password, having proved the account is yours.

    Two ways to prove it, and the app uses the stronger one it has. A code
    emailed to the address on the account proves control of that mailbox, which
    is what a reset should require. Failing that, the shared sign-up code is
    accepted — weaker, since everybody who can register knows it, but it is the
    difference between a locked-out household and a support request.
    """
    from datetime import datetime, timedelta
    from hmac import compare_digest

    from ledger import accounts
    from ledger.models import EntryError

    can_email = accounts.settings_for_email_exists()
    code_wanted = accounts.signup_code()

    if not can_email and not code_wanted:
        st.info(
            "There is no way to verify a reset yet. Either add `[notify]` to "
            "your secrets so a code can be emailed, or set `signup_code` under "
            "`[accounts]`.\n\nUntil then, the way back in is to delete that "
            "person's row from the **users** tab of the sheet and sign up again."
        )
        return

    pending = st.session_state.get(_RESET) or {}

    if not pending:
        with st.form("reset_start_form"):
            email = st.text_input("Your email", key="reset_email")
            asked = st.form_submit_button(
                "Send me a code" if can_email else "Continue", type="primary"
            )
        if asked:
            account = accounts.find(email, known)
            if account is None:
                # Same non-answer as a failed sign-in: saying "no such account"
                # tells a stranger who is registered here.
                st.success(
                    "If that address has an account, a code is on its way to it."
                    if can_email else
                    "If that address has an account, you can set a new password now."
                )
            else:
                code = accounts.reset_code()
                problem = accounts.send_reset_code(account, code) if can_email else ""
                if problem:
                    st.error(f"Could not send the code: {problem}")
                else:
                    st.session_state[_RESET] = {
                        "email": account.email,
                        "code": code,
                        "until": (datetime.now()
                                  + timedelta(minutes=accounts.RESET_MINUTES)).isoformat(),
                        "left": accounts.RESET_ATTEMPTS,
                    }
                    st.rerun()
        return

    if datetime.now() > datetime.fromisoformat(pending["until"]):
        st.session_state.pop(_RESET, None)
        st.warning("That code has expired. Ask for another.")
        return

    st.caption(f"Setting a new password for **{pending['email']}**.")
    with st.form("reset_finish_form"):
        given = st.text_input(
            "Code from your email" if can_email else "Sign-up code", key="reset_code"
        )
        new = st.text_input(
            f"New password (at least {accounts.MIN_PASSWORD} characters)",
            type="password", key="reset_new")
        again = st.text_input("New password again", type="password", key="reset_again")
        change_it, cancelled = st.columns(2)
        with change_it:
            changing = st.form_submit_button("Set new password", type="primary")
        with cancelled:
            giving_up = st.form_submit_button("Cancel")

    if giving_up:
        st.session_state.pop(_RESET, None)
        st.rerun()
    if changing:
        wanted = pending["code"] if can_email else code_wanted
        if not compare_digest(str(given).strip(), str(wanted)):
            pending["left"] -= 1
            if pending["left"] <= 0:
                st.session_state.pop(_RESET, None)
                st.error("Too many wrong codes. Start again.")
            else:
                st.session_state[_RESET] = pending
                st.error(f"That code is not right. {pending['left']} tries left.")
        elif new != again:
            st.error("The two passwords do not match.")
        else:
            try:
                accounts.set_password(pending["email"], new)
            except (EntryError, RuntimeError) as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(_RESET, None)
                st.session_state["account_created"] = True
                st.session_state["account_created_email"] = pending["email"]
                st.rerun()



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
        _auth_chrome()
        st.markdown(
            '<div class="auth-sub">Private. Sign in to see it.</div>',
            unsafe_allow_html=True,
        )
        st.button("Sign in with Google", type="primary", width="stretch",
                  on_click=st.login)
        st.stop()

    email = str(st.user.get("email") or "")
    if allowed_emails() is None:
        # Locked, not refused — saying "you are not allowed" would send somebody
        # chasing an access request when the file is what needs fixing.
        _auth_chrome()
        st.error(
            "The access list in `[auth].allowed` cannot be read, so nobody is "
            "being let in. It must be a list of addresses, for example "
            '`allowed = ["you@gmail.com"]`.'
        )
        st.button("Sign out", on_click=st.logout)
        st.stop()

    if not permitted(email):
        _auth_chrome()
        st.error(
            f"**{email}** is not on the access list for this ledger. "
            "Ask the owner to add you."
        )
        st.button("Sign out", on_click=st.logout)
        st.stop()


def _sign_out() -> None:
    """Leave nothing behind, so the next run lands on the sign-in page.

    Clearing only the session key left a half-filled reset and the typed email
    sitting in state, so signing out and back in showed somebody the previous
    person's address in the box.
    """
    for key in [SESSION, SESSION_NAME, _RESET, "account_created",
                "account_created_email",
                "login_email", "login_password", "signup_name", "signup_email",
                "signup_password", "signup_confirm", "reset_email", "reset_code",
                "reset_new", "reset_again"]:
        st.session_state.pop(key, None)
    st.session_state[SIGNED_OUT] = True


def sidebar_identity() -> None:
    """Say who is signed in, with a way out. Shown on every page by the router."""
    if configured() and current_user():
        with st.sidebar:
            st.caption(f"Signed in as {current_user()}")
            st.button("Sign out", width="stretch", on_click=st.logout)
        return

    email = signed_in_email()
    if email:
        with st.sidebar:
            st.caption(f"Signed in as {st.session_state.get(SESSION_NAME) or email}")
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

    # Being signed in is a fact about the session, not a question for the sheet.
    # This is what stopped a random Google 503 mid-delete from bouncing somebody
    # back to the login form, so it is worth a check that fails if the users tab
    # ever creeps back into the answer.
    class _Session(dict):
        pass

    real, st.session_state = st.session_state, _Session()
    try:
        assert signed_in_email() == ""
        st.session_state[SESSION] = "ravi@example.com"
        st.session_state[SESSION_NAME] = "Ravi"
        assert signed_in_email() == "ravi@example.com"
        assert current_user() == "ravi@example.com", "writes are attributed to them"

        _sign_out()
        assert signed_in_email() == "", "sign out must clear the session"
        assert st.session_state.get(SESSION_NAME) is None, "and the name with it"
        assert st.session_state[SIGNED_OUT] is True, "the next run says so on screen"
    finally:
        st.session_state = real

    print("ledger.auth: all checks passed")


if __name__ == "__main__":
    demo()
