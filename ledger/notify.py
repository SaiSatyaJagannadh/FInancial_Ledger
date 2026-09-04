"""Email a note whenever the ledger or the interest tab is written to.

Says what changed, which row, when, and — when `[auth]` is configured — who.
The signer comes from `ledger/auth.py`, which is Google's answer rather than
this app's, so the address on the message is one somebody proved they own.

With no login configured the app is open, and the message simply carries no
`By:` line. It never guesses: an unattributed change is reported as one.

**A failed notification must never fail a save.** The entry is already in the
sheet by the time this runs; an SMTP timeout afterwards is not a reason to show
the person an error about money they successfully recorded. Everything here is
best-effort: failures are swallowed, recorded in `last_error()` for the page to
mention, and never raised.

Sending happens on a background thread. Gmail's SMTP handshake takes one to
three seconds, and Add Entry is a form built for typing, saving and typing the
next — putting that wait in front of every save would be felt on every row.

Google will not let a service account send mail as you: the Gmail API needs
domain-wide delegation, which is Workspace-only, exactly as with Drive in
`attach.py`. So this uses plain SMTP with an app password, which is the one
route a personal account has.
"""

from __future__ import annotations

import pathlib
import smtplib
import sys
import threading
from datetime import datetime
from email.message import EmailMessage

#: Section in Streamlit secrets. Absent means notifications are simply off —
#: not an error. Tests and demo mode take this path without doing anything.
SECTION = "notify"

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

#: Gmail closes a connection that stalls; keep it shorter than a person's
#: patience since nobody is waiting on this thread anyway.
TIMEOUT = 20

_LAST_ERROR = ""


def last_error() -> str:
    """Why the most recent send failed, or empty if none has.

    A notification system you believe in but which is quietly not working is
    worse than none at all, so the app can surface this.
    """
    return _LAST_ERROR


def settings(secrets: dict | None) -> dict | None:
    """The notify config, or None when it is not set up.

    Requires a recipient and a password. Without both there is nothing to do,
    and returning None here is what keeps every test and every demo-mode run
    from touching the network.
    """
    section = (secrets or {}).get(SECTION) or {}
    try:
        section = dict(section)
    except Exception:  # noqa: BLE001 — a malformed section is just "off"
        return None

    to = str(section.get("to") or "").strip()
    password = str(section.get("password") or section.get("smtp_password") or "").strip()
    if not to or not password:
        return None

    user = str(section.get("user") or section.get("smtp_user") or to).strip()
    return {
        "to": to,
        "user": user,
        "password": password,
        "host": str(section.get("host") or DEFAULT_HOST).strip(),
        "port": int(section.get("port") or DEFAULT_PORT),
    }


def _shown(record, column: str, raw: str) -> str:
    """One field, rendered the way the app renders it.

    The amount arrives from `to_row()` as "200000.00", which is the right thing
    to put in a cell and the wrong thing to read in an email — the whole point
    of this message is that somebody can glance at it and see whether the figure
    is what they expected.
    """
    if column == "amount":
        try:
            from ledger.money import format_money

            return format_money(record.amount_minor, record.currency)
        except Exception:  # noqa: BLE001 — fall back to the raw cell
            return raw
    return raw or "—"


def describe(before, after, columns: list[str]) -> list[str]:
    """The fields that differ, as "name: old → new" lines.

    Built from `to_row()` so it works for an Entry and a Charge without knowing
    anything about either: both serialise in the order of their module's
    COLUMNS, and that is already the contract the sheet depends on.
    """
    old_row = before.to_row() if before is not None else []
    new_row = after.to_row() if after is not None else []
    lines = []
    for index, column in enumerate(columns):
        old = str(old_row[index]) if index < len(old_row) else ""
        new = str(new_row[index]) if index < len(new_row) else ""
        if old == new:
            continue
        if before is None:
            lines.append(f"{column}: {_shown(after, column, new)}")
        elif after is None:
            lines.append(f"{column}: {_shown(before, column, old)}")
        else:
            lines.append(
                f"{column}: {_shown(before, column, old)} → {_shown(after, column, new)}"
            )
    return lines


def _subject(kind: str, action: str, record) -> str:
    who = getattr(record, "person", "") or ""
    return f"[Personal Ledger] {kind} {action}" + (f" — {who}" if who else "")


def _body(kind: str, action: str, before, after, columns: list[str],
          by: str = "") -> str:
    record = after if after is not None else before
    changes = describe(before, after, columns)

    lines = [f"{kind} {action}.", ""]
    if by:
        lines += [f"By: {by}", ""]
    if action == "edited" and changes:
        lines.append("What changed:")
        lines.extend(f"  {line}" for line in changes)
        lines.append("")
        lines.append("Unchanged:")
        changed_names = {line.split(":", 1)[0] for line in changes}
        for index, column in enumerate(columns):
            if column in changed_names:
                continue
            row = after.to_row()
            value = str(row[index]) if index < len(row) else ""
            if value:
                lines.append(f"  {column}: {_shown(after, column, value)}")
    else:
        lines.append("The row:")
        lines.extend(f"  {line}" for line in changes)

    row_number = getattr(record, "row", None) or getattr(before, "row", None)
    lines += [
        "",
        f"Saved {datetime.now():%d %b %Y %H:%M}"
        + (f" · sheet row {row_number}" if row_number else ""),
        "",
        "Sent by your Personal Ledger.",
    ]
    return "\n".join(lines)


def _send(subject: str, body: str, config: dict) -> None:
    """Deliver one message. Called on a background thread; never raises."""
    global _LAST_ERROR
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["user"]
    message["To"] = config["to"]
    message.set_content(body)

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=TIMEOUT) as server:
            server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 — a notification must not escape
        # The password must never reach the log: SMTPAuthenticationError puts
        # the server's reply in its args, not the credential, but the class name
        # and a short reason are all anyone needs anyway.
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"[notify] could not send: {_LAST_ERROR}", file=sys.stderr)
    else:
        _LAST_ERROR = ""


def changed(kind: str, action: str, *, before=None, after=None,
            columns: list[str] | None = None, secrets: dict | None = None,
            by: str | None = None, sender=None) -> bool:
    """Announce one write. Returns whether a message was handed off to send.

    `sender` exists so the self-check and the tests can watch what would go out
    without a network or a password anywhere near them.
    """
    config = settings(secrets)
    if config is None and sender is None:
        return False

    if columns is None:
        from ledger.models import COLUMNS

        columns = COLUMNS

    record = after if after is not None else before
    if record is None:
        return False

    if by is None:
        from ledger import auth

        by = auth.current_user()

    subject = _subject(kind, action, record)
    body = _body(kind, action, before, after, columns, by=by)

    if sender is not None:
        sender(subject, body)
        return True

    # Off the request path: the row is already saved, and Gmail's handshake
    # takes seconds that the person typing the next entry should not pay for.
    threading.Thread(
        target=_send, args=(subject, body, config), daemon=True
    ).start()
    return True


def demo() -> None:
    """Self-check for the diff, which is the part that can be wrong quietly."""
    from datetime import date

    from ledger.models import COLUMNS, Direction, Entry

    def entry(minor, note="", direction=Direction.given):
        return Entry(date=date(2026, 8, 27), person="Narayana Rao D",
                     ledger="Nanna", direction=direction, amount_minor=minor,
                     note=note, row=46)

    before, after = entry(2_00_000_00, "uncle"), entry(1_50_000_00, "part repaid")
    changes = describe(before, after, COLUMNS)
    joined = " | ".join(changes)
    assert any(c.startswith("amount:") for c in changes), joined
    assert any(c.startswith("note:") for c in changes), joined
    assert not any(c.startswith("person:") for c in changes), "person did not change"
    # The amount reads as money, not as a cell.
    assert "2,00,000" in joined and "1,50,000" in joined, joined
    assert "→" in joined, joined

    # An identical pair has nothing to report.
    assert describe(entry(100), entry(100), COLUMNS) == []

    # A direction flip is the change that matters most; it must be named.
    flip = describe(entry(100), entry(100, direction=Direction.received), COLUMNS)
    assert any(c.startswith("direction:") for c in flip), flip

    # Interest charges go through the same door with their own columns.
    from ledger.interest import COLUMNS as ICOLUMNS, Charge

    charge = lambda m, moved="": Charge(  # noqa: E731
        date=date(2026, 7, 1), person="Narayana", amount_minor=m, moved_to=moved)
    moved = describe(charge(15_000_00), charge(15_000_00, "Vihar"), ICOLUMNS)
    assert moved == ["moved_to: — → Vihar"], moved

    # Nothing configured means nothing sent, and no exception either.
    assert settings(None) is None
    assert settings({}) is None
    assert settings({"notify": {"to": "a@b.c"}}) is None, "a password is required"
    assert settings({"notify": {"to": "a@b.c", "password": "x"}})["user"] == "a@b.c"
    assert changed("Ledger entry", "added", after=entry(100), secrets={}) is False

    # With a sender injected, the message is built without touching a network.
    seen = {}
    changed("Ledger entry", "edited", before=before, after=after,
            secrets={}, sender=lambda s, b: seen.update(subject=s, body=b))
    assert "Narayana Rao D" in seen["subject"], seen["subject"]
    assert "What changed:" in seen["body"], seen["body"]
    # Nobody signed in means the message simply does not claim a person.
    assert "By:" not in seen["body"], seen["body"]

    seen.clear()
    changed("Ledger entry", "edited", before=before, after=after, secrets={},
            by="ravi@example.com", sender=lambda s, b: seen.update(body=b))
    assert "By: ravi@example.com" in seen["body"], seen["body"]

    print("ledger.notify: all checks passed")


def selftest() -> int:
    """Send one real message using whatever is in secrets. `-m ledger.notify --test`.

    Exists so the person who pastes the app password can prove it works without
    showing it to anybody — nothing here prints the credential, and the failure
    messages name the cause rather than echoing the config.
    """
    import tomllib

    path = pathlib.Path(".streamlit/secrets.toml")
    if not path.exists():
        print(f"No {path}. Copy {path}.example to it and fill in [notify].")
        return 1
    try:
        secrets = tomllib.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"{path} is not valid TOML: {exc}")
        return 1

    config = settings(secrets)
    if config is None:
        print(
            f"[notify] in {path} is incomplete — it needs both `to` and "
            "`password`. Until both are set, change emails stay off."
        )
        return 1

    # Say what will happen, without saying the password.
    print(f"Sending a test message to {config['to']} "
          f"via {config['host']}:{config['port']} as {config['user']}…")

    _send(
        "[Personal Ledger] Test message",
        "If you are reading this, change emails are working.\n\n"
        "From here on you will get one of these whenever an entry or an "
        "interest charge is added, edited or deleted, saying which fields "
        "changed and what they changed from and to.\n",
        config,
    )
    if last_error():
        print(f"\nFailed: {last_error()}")
        if "Username and Password not accepted" in last_error():
            print(
                "\nThat is Gmail rejecting the credential. Two usual causes:\n"
                "  · it is the account password, not an App Password\n"
                "  · the App Password was revoked or belongs to another account\n"
                "Generate a fresh one at myaccount.google.com/apppasswords."
            )
        return 1
    print("\nSent. Check your inbox.")
    return 0


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        raise SystemExit(selftest())
    demo()
