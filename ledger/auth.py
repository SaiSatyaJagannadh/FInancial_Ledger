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

import base64
import hashlib
import hmac
import json
import time

import streamlit as st

from ledger.ui import INK, PAPER, RULE, SHADE   # the ledger's own colours, so
                                                # the way in looks like the
                                                # thing it leads to

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


#: Where a password sign-in is remembered *within* a run. Session state is per
#: websocket connection, so a refresh starts an empty one — which is why the
#: cookie below exists. Everything that asks "is somebody signed in" still asks
#: this key and nothing else; the cookie only ever puts a value back into it.
SESSION = "account_email"

#: The display name, kept beside it. Written once at sign-in so that showing
#: who is signed in costs nothing — see `signed_in_email`.
SESSION_NAME = "account_name"

#: Set by `_sign_out` so the next run says "signed out" rather than dropping
#: somebody straight back onto a form they did not ask for.
SIGNED_OUT = "signed_out"


#: The name of the cookie a remembered sign-in lives in.
COOKIE = "ledger_session"

#: How long it lasts. Long enough that a refresh, a closed tab, or coming back
#: after lunch is not a sign-in; short enough that a borrowed laptop does not
#: stay signed in for ever.
REMEMBER_DAYS = 14

#: Set to a token by a successful sign-in, and written to the browser on the
#: next run. A rerun discards whatever the current run had drawn, so the script
#: that sets the cookie cannot be rendered in the same breath as the sign-in.
_PENDING = "remember_pending"

#: Set once a cookie has been read back into the session, so a sign-out later in
#: the same run cannot be undone by the cookie that is still in `st.context`.
_RESTORED = "remember_restored"


def _remember_key(secrets: dict | None = None) -> bytes:
    """The key the session token is signed with, or b"" when there is none.

    Preference is an explicit `[accounts].cookie_secret`. Failing that it is
    derived from the service-account private key, which is already a
    high-entropy secret, is already in this file, and is never shown to anyone
    using the app. It is run through HMAC with a purpose string rather than used
    directly, so this use cannot be turned back into the key Google trusts.

    **No secret, no remembering.** An unsigned token is one anybody can forge
    into "I am you", which is far worse than signing in again after a refresh.
    """
    secrets = _secrets() if secrets is None else secrets
    try:
        configured_secret = str(dict(secrets.get("accounts") or {})
                                .get("cookie_secret") or "").strip()
    except Exception:  # noqa: BLE001 — a section we cannot even read
        configured_secret = ""
    if configured_secret:
        return configured_secret.encode()
    try:
        private = str(dict(secrets.get("gcp_service_account") or {})
                      .get("private_key") or "").strip()
    except Exception:  # noqa: BLE001
        private = ""
    if not private:
        return b""
    return hmac.new(private.encode(), b"personal-ledger session cookie v1",
                    hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(email: str, name: str, key: bytes, *, now: float | None = None,
               days: int = REMEMBER_DAYS) -> str:
    """A signed "this browser is <email>" note, good until it expires.

    The payload is readable by anybody holding the cookie — it is their own
    address and name, which they already know. What the signature buys is that
    it cannot be *changed* into somebody else's.
    """
    if not key:
        return ""
    now = time.time() if now is None else now
    body = _b64(json.dumps(
        {"e": email, "n": name, "x": int(now + days * 86400)},
        separators=(",", ":"),
    ).encode())
    return f"v1.{body}.{_b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"


def read_token(token: str, key: bytes, *, now: float | None = None) -> tuple[str, str]:
    """(email, name) from a token that is intact and unexpired, else ("", "").

    Every failure — a wrong shape, a bad signature, an expired stamp, a payload
    that is not the JSON it should be — is answered the same way: nobody. A
    cookie is under the holder's control, so this is a parser for hostile input
    and it never raises.
    """
    if not key or not token:
        return "", ""
    try:
        version, body, signature = str(token).split(".")
        if version != "v1":
            return "", ""
        want = hmac.new(key, body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), want):
            return "", ""
        payload = json.loads(_unb64(body))
        if float(payload["x"]) < (time.time() if now is None else now):
            return "", ""
        return str(payload.get("e") or ""), str(payload.get("n") or "")
    except Exception:  # noqa: BLE001 — anything malformed is simply nobody
        return "", ""


def _cookie_script(value: str, seconds: int) -> str:
    """The one line of JavaScript that sets or clears the cookie.

    Streamlit has no API for writing one, but `components.html` renders a
    `srcdoc` iframe sandboxed with `allow-same-origin`, so a script inside it
    shares the app's origin and `document.cookie` lands on the app's domain.
    `Secure` only over https — a Secure cookie is silently dropped on the plain
    http of a local run, which would make this look broken everywhere but live.
    """
    try:
        secure = "; Secure" if str(st.context.url or "").startswith("https") else ""
    except Exception:  # noqa: BLE001 — no runtime, no url
        secure = ""
    return (
        "<script>document.cookie="
        f"{json.dumps(f'{COOKIE}={value}; Path=/; Max-Age={seconds}; SameSite=Lax{secure}')}"
        ";</script>"
    )


def _write_cookie(value: str, seconds: int) -> None:
    """Render the one-line script that carries the cookie to the browser.

    `height=1`, not `height=0`: a component with no size is not mounted at all,
    so the script never runs. That was silent — the cookie simply never
    appeared, and the sign-in looked exactly as forgetful as before.
    """
    import streamlit.components.v1 as components

    components.html(_cookie_script(value, seconds), height=1)


def _restore() -> None:
    """Put a remembered sign-in back into this session, once per session.

    Runs *before* the gate decides, and only when nothing is signed in already.
    A sign-out in this browser clears the cookie, but `st.context.cookies` holds
    what the page was loaded with — so `_RESTORED` and `SIGNED_OUT` between them
    stop a cookie that is already gone from signing somebody back in.
    """
    if st.session_state.get(SESSION) or st.session_state.get(_RESTORED):
        return
    if st.session_state.get(SIGNED_OUT):
        return
    st.session_state[_RESTORED] = True
    try:
        token = str(st.context.cookies.get(COOKIE) or "")
    except Exception:  # noqa: BLE001 — no runtime, no cookies
        return
    email, name = read_token(token, _remember_key())
    if email:
        st.session_state[SESSION] = email
        st.session_state[SESSION_NAME] = name or email


def _flush_cookie() -> None:
    """Write whatever the last run decided the browser should hold."""
    pending = st.session_state.pop(_PENDING, None)
    if pending is None:
        return
    _write_cookie(pending, REMEMBER_DAYS * 86400 if pending else 0)


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
#:
#: The design is one card on paper. Everything Streamlit renders here — the
#: mark, the tabs, the form, any warning — lands inside `.block-container`, so
#: *that* is made the card rather than a box drawn around one part of it. A
#: border around only the form left the tabs floating above an unrelated
#: rectangle.
#:
#: It has to work on a phone, which is where this app is actually used: widths
#: are `min(…, 100% - margin)` rather than fixed, type is `clamp()`ed, the
#: buttons fill their width so they can be hit with a thumb, and the tab row
#: wraps instead of scrolling a third tab out of sight.
_AUTH_CSS = f"""
<style>
  [data-testid="stSidebar"],
  [data-testid="stSidebarNav"],
  [data-testid="stSidebarCollapsedControl"],
  header[data-testid="stHeader"] {{ display: none !important; }}

  /* Centred in the window, not pinned to the top — a short card (the signed-out
     screen is four lines and a button) floating under a header-shaped gap looks
     like a page that failed to finish loading. `safe center` rather than
     `center`: when the card is taller than the phone it holds it, the top of an
     overflowing form would otherwise be scrolled off above the viewport and
     unreachable. */
  .stMain {{ display: flex; justify-content: center; align-items: safe center; }}

  /* The card. Padding scales with the viewport so a phone does not spend a
     third of its screen on the space above the mark. */
  .block-container {{
      /* Against the viewport, not the parent: `100%` of a full-bleed parent is
         a card with its edges flush to both sides of a phone. */
      width: min(25rem, calc(100vw - 2rem));
      max-width: 25rem;
      margin: 1.25rem auto;
      padding: clamp(1.4rem, 5vw, 2.2rem) clamp(1.2rem, 5vw, 2rem) 1.4rem;
      background: {PAPER};
      border: 1px solid {RULE};
      border-radius: 18px;
      box-shadow: 0 1px 2px rgba(22, 32, 46, .04),
                  0 14px 34px -22px rgba(22, 32, 46, .45);
  }}

  /* The mark is a struck seal, not a stray character — ink block, cream glyph,
     the one place on the screen the ledger's own colour appears. */
  .auth-mark {{
      width: 42px; height: 42px; border-radius: 11px;
      background: {INK}; color: {PAPER};
      display: flex; align-items: center; justify-content: center;
      font-size: 1.32rem; font-weight: 600; line-height: 1;
      margin-bottom: .85rem;
  }}
  .auth-name {{
      font-size: clamp(1.35rem, 5.5vw, 1.6rem); font-weight: 700;
      letter-spacing: -0.02em; color: {INK}; margin: 0 0 .2rem 0;
      line-height: 1.15;
  }}
  .auth-sub {{
      font-size: .9rem; opacity: .62; margin: 0 0 1.35rem 0; line-height: 1.5;
  }}

  /* The form is inside the card, so it draws no box of its own. */
  div[data-testid="stForm"] {{
      border: 0; padding: 0; background: transparent;
  }}

  /* Wrapping, not scrolling. A tab that has scrolled off the right of a narrow
     phone is a route nobody finds, and on this screen that route is the way
     back into a locked-out account. Addressed by ARIA role and testid rather
     than by Streamlit's generated class names, which change between releases. */
  [role="tablist"] {{
      flex-wrap: wrap; gap: .2rem 1.1rem; overflow-x: visible;
      margin-bottom: .35rem;
  }}
  [data-testid="stTab"] {{ padding-left: 0; padding-right: 0; }}
  [data-testid="stTab"] p {{ font-size: .9rem; font-weight: 600; }}

  /* Fields read as fields: a hairline box on the card, not a grey slab. */
  [data-testid="stTextInputRootElement"] {{
      background: #FFFFFF; border: 1px solid {RULE}; border-radius: 10px;
  }}
  [data-testid="stTextInputRootElement"]:focus-within {{ border-color: {INK}; }}
  .stTextInput label p {{ font-size: .85rem; font-weight: 600; opacity: .75; }}

  /* Streamlit's alerts arrive in a blue that belongs to no other part of this
     app. On paper they become a ruled note. */
  [data-testid="stAlertContainer"] {{
      background: {SHADE}; border-radius: 10px; border-left: 3px solid {INK};
  }}
  [data-testid="stAlert"] p {{ color: {INK}; font-size: .86rem; line-height: 1.5; }}

  /* 44px is the smallest thing a thumb reliably hits; these come out at 46. */
  .stButton button, .stFormSubmitButton button {{
      border-radius: 10px; font-weight: 600; padding: .68rem 1rem; width: 100%;
  }}
</style>
"""


def _auth_chrome(sub: str = "") -> None:
    """Strip the app down to the screen in front of you, and head it."""
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-mark">₹</div>'
        '<div class="auth-name">Personal Ledger</div>'
        + (f'<div class="auth-sub">{sub}</div>' if sub else ""),
        unsafe_allow_html=True,
    )


def _signed_out_screen() -> None:
    """Say the sign-out happened, and stop. Then offer the way back in.

    Landing straight back on the sign-in form left nobody any sign that the
    button had worked — the same page they were just told to fill in, with their
    address gone from it. This is one screen, and it stays until they ask.
    """
    # Clearing it here rather than in `_sign_out`: that runs as an on_click
    # callback, before the page is drawn, and a script nobody renders sets no
    # cookie. This screen is the one thing that is certain to be drawn after.
    _write_cookie("", 0)
    _auth_chrome("You are signed out. Nothing from that session is left in "
                 "this browser, on this device.")
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

    # A refresh starts a brand new session, so this is where a remembered
    # sign-in comes back — before anything asks whether somebody is signed in.
    _restore()

    if signed_in_email():
        _flush_cookie()
        return

    if st.session_state.get(SIGNED_OUT):
        _signed_out_screen()

    known, problems = accounts.load()
    made = st.session_state.pop("account_created", None)
    first_ever = not known

    _auth_chrome("Private. Sign in to see it." if not made else "")

    for problem in problems:
        st.warning(problem)

    if made:
        st.success(
            f"Account created for **{st.session_state.pop('account_created_email', '')}**. "
            "Sign in with it below."
        )

    if first_ever:
        st.info(
            "No accounts yet. The first one created becomes yours — make it now, "
            "before the app is shared with anybody."
        )

    # Short labels. The long ones ("Create an account", "Forgotten password")
    # ran past the right edge of a phone, and a tab that has scrolled out of
    # sight is a route nobody finds — which here is the way back in.
    sign_in, sign_up, forgot = st.tabs(["Sign in", "Create account", "Forgot?"])

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
            submitted = st.form_submit_button("Sign in", type="primary",
                                              width="stretch")
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
                # Written to the browser on the next run — `st.rerun()` throws
                # away whatever this one has drawn, the script included.
                st.session_state[_PENDING] = make_token(
                    account.email, account.name or account.email, _remember_key()
                )
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
            registering = st.form_submit_button("Create account", type="primary",
                                                width="stretch")

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
                "Send me a code" if can_email else "Continue", type="primary",
                width="stretch",
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
        _auth_chrome("Private. Sign in to see it.")
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
    for key in [SESSION, SESSION_NAME, _RESET, _PENDING, "account_created",
                "account_created_email",
                "login_email", "login_password", "signup_name", "signup_email",
                "signup_password", "signup_confirm", "reset_email", "reset_code",
                "reset_new", "reset_again"]:
        st.session_state.pop(key, None)
    st.session_state[SIGNED_OUT] = True
    # `st.context.cookies` still holds the token this page was loaded with —
    # the browser only forgets it once the screen below has drawn the script.
    # Until then this latch is what stops `_restore` from undoing the sign-out.
    st.session_state[_RESTORED] = True


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
