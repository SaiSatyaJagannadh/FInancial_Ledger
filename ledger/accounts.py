"""Sign-up and sign-in against a `users` tab in the workbook.

**Read this before trusting it.** The workbook is the database, so the password
hashes live in the same document the service account can write and everybody
the sheet is shared with can read. Hashing means a reader cannot recover a
password — but anyone who can *edit* the sheet can add themselves a user row,
or paste their own hash over somebody else's, and then sign in as them. Sheet
access is therefore administrator access, and no amount of care in this file
changes that.

Streamlit's own OIDC login (`ledger/auth.py`, `[auth]` in secrets) stores no
passwords at all and is the stronger option. This exists because it was asked
for, and because a household that will not set up an OAuth client is better off
with this than with nothing.

What is done properly here: PBKDF2-HMAC-SHA256 at the current OWASP iteration
count, a fresh random salt per user, constant-time comparison, and a sign-in
failure that does not reveal whether the address is registered.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ledger.models import EntryError

WORKSHEET = "users"
COLUMNS = ["email", "name", "password_hash", "joined"]

#: Section that switches this on. Absent means the app is open, as it was
#: before any of this existed — the same default every other optional feature
#: here takes.
SECTION = "accounts"

#: OWASP's floor for PBKDF2-HMAC-SHA256 at time of writing. hashlib runs this
#: in C, so it costs a fraction of a second on a sign-in and is worth it.
ITERATIONS = 600_000
ALGORITHM = "pbkdf2_sha256"
SALT_BYTES = 16

#: Short passwords are the whole attack. Eight is a floor, not a blessing.
MIN_PASSWORD = 8

#: How long a reset code stays good. Long enough to go and read an email,
#: short enough that one left in an inbox is not a spare key.
RESET_MINUTES = 15

#: Guesses allowed before a code is thrown away. Six digits is a million
#: possibilities; five tries at it is nothing, five thousand is not.
RESET_ATTEMPTS = 5

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Account:
    email: str
    name: str
    password_hash: str
    joined: date
    row: int | None = field(default=None, compare=False)

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Account:
        from ledger.models import parse_date

        email = str(row.get("email") or "").strip().lower()
        if not email:
            raise EntryError("email is required")
        try:
            joined = parse_date(row.get("joined") or date.today().isoformat())
        except EntryError:
            joined = date.today()
        return cls(
            email=email,
            name=str(row.get("name") or "").strip(),
            password_hash=str(row.get("password_hash") or "").strip(),
            joined=joined,
            row=row_number,
        )

    def to_row(self) -> list[str]:
        return [self.email, self.name, self.password_hash, self.joined.isoformat()]


def enabled(secrets: dict | None = None) -> bool:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    section = secrets.get(SECTION) or {}
    try:
        return bool(dict(section).get("enabled"))
    except Exception:  # noqa: BLE001
        return False


def signup_code(secrets: dict | None = None) -> str:
    """A shared word new accounts must quote, when one is set.

    Without it, anyone who can reach the page can register — which on a public
    URL means anyone at all. The page says so rather than leaving it implied.
    """
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    section = secrets.get(SECTION) or {}
    try:
        return str(dict(section).get("signup_code") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------- hashing

def hash_password(password: str, *, salt: bytes | None = None,
                  iterations: int = ITERATIONS) -> str:
    """`algorithm$iterations$salt$hash`, all hex. Never the password itself."""
    if len(password) < MIN_PASSWORD:
        raise EntryError(f"password must be at least {MIN_PASSWORD} characters")
    salt = os.urandom(SALT_BYTES) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify(password: str, stored: str) -> bool:
    """Constant-time check. A malformed or empty hash is a failure, not a crash.

    `hmac.compare_digest` rather than `==`: a plain comparison returns as soon
    as two bytes differ, and the time it took is a measurement of how much of
    the hash was guessed correctly.
    """
    try:
        algorithm, iterations, salt_hex, digest_hex = str(stored).split("$")
        if algorithm != ALGORITHM:
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError, TypeError):
        return False
    return hmac.compare_digest(computed.hex(), digest_hex)


# ---------------------------------------------------------------- the users tab

def load(secrets: dict | None = None) -> tuple[list[Account], list[str]]:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        rows = store.records(_sheet(secrets), COLUMNS)
    except Exception as exc:  # noqa: BLE001 — an absent tab is not a crash
        return [], [f"Could not read the users tab: {type(exc).__name__}: {exc}"]

    out, problems = [], []
    for offset, raw in enumerate(rows):
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            out.append(Account.from_row(cleaned, row_number=offset + 2))
        except EntryError as exc:
            problems.append(f"row {offset + 2}: {exc}")
    return out, problems


def find(email: str, accounts: list[Account]) -> Account | None:
    wanted = str(email or "").strip().lower()
    return next((a for a in accounts if a.email == wanted), None)


def validate(name: str, email: str, password: str, confirm: str) -> list[str]:
    """Everything wrong with a sign-up, so the form can say all of it at once."""
    problems = []
    if not str(name or "").strip():
        problems.append("Name is required.")
    if not _EMAIL.match(str(email or "").strip()):
        problems.append("That does not look like an email address.")
    if len(str(password or "")) < MIN_PASSWORD:
        problems.append(f"Password must be at least {MIN_PASSWORD} characters.")
    if password != confirm:
        problems.append("The two passwords do not match.")
    return problems


def create(name: str, email: str, password: str,
           secrets: dict | None = None) -> Account:
    """Register one person. Refuses a duplicate address rather than shadowing it.

    A second row for the same email would make `find` return whichever came
    first, so the newer password would silently never work.
    """
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to register against.")

    address = str(email or "").strip().lower()
    existing, _ = load(secrets)
    if find(address, existing) is not None:
        raise EntryError("There is already an account for that email.")

    account = Account(
        email=address,
        name=str(name or "").strip(),
        password_hash=hash_password(password),
        joined=date.today(),
    )
    store.append_rows(_sheet(secrets), [account.to_row()], value_input_option="RAW")
    return account


def authenticate(email: str, password: str,
                 secrets: dict | None = None) -> Account | None:
    """The account if the password is right, otherwise None.

    Deliberately gives the caller nothing to distinguish "no such address" from
    "wrong password" — telling them apart hands an attacker a list of who has
    an account here. An unknown address still costs a hash, so the two answers
    take the same time as well as saying the same thing.
    """
    accounts, _ = load(secrets)
    account = find(email, accounts)
    if account is None:
        # Burn the same work rather than returning early and timing differently.
        verify(password, hash_password("x" * MIN_PASSWORD))
        return None
    return account if verify(password, account.password_hash) else None


def set_password(email: str, password: str, secrets: dict | None = None) -> Account:
    """Give an existing account a new password.

    Rewrites only the hash cell of that person's row, after re-reading it to
    confirm it still holds the address we mean — rows shift here exactly as they
    do in the ledger, and writing a password onto a stranger's row would hand
    them somebody else's account.
    """
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to change a password on.")

    address = str(email or "").strip().lower()
    known, _ = load(secrets)
    account = find(address, known)
    if account is None or account.row is None:
        raise EntryError("There is no account for that email.")

    fresh = hash_password(password)     # refuses a short one before touching the sheet
    sheet = _sheet(secrets)
    cells = sheet.row_values(account.row)
    if not cells or str(cells[0]).strip().lower() != address:
        raise RuntimeError(
            f"Row {account.row} no longer holds that account — the users tab "
            "changed since it was read. Try again."
        )

    column = store._column_letter(COLUMNS.index("password_hash") + 1)
    sheet.update(values=[[fresh]],
                 range_name=f"{column}{account.row}:{column}{account.row}",
                 value_input_option="RAW")
    return Account(email=account.email, name=account.name, password_hash=fresh,
                   joined=account.joined, row=account.row)


def settings_for_email_exists(secrets: dict | None = None) -> bool:
    """Can a reset code actually be emailed? Reuses the change-email settings."""
    from ledger import notify, store

    secrets = store._secrets() if secrets is None else secrets
    return notify.settings(secrets) is not None


def reset_code() -> str:
    """A six-digit one-time code, from the system's own randomness.

    `secrets.randbelow`, not `random`: the latter is seeded predictably and its
    output can be reconstructed from earlier draws, which for something that
    stands in for a password is the whole game.
    """
    import secrets as _secrets

    return f"{_secrets.randbelow(1_000_000):06d}"


def send_reset_code(account: Account, code: str,
                    secrets: dict | None = None) -> str:
    """Email a reset code to the account's own address. Returns "" on success.

    Sent to the address on the account, never to one typed into the form, so
    the code only ever reaches whoever already controls that mailbox. Uses the
    same SMTP settings the change emails use, so this needs no config of its
    own — and says so plainly when there is none.
    """
    from ledger import notify, store

    secrets = store._secrets() if secrets is None else secrets
    config = notify.settings(secrets)
    if config is None:
        return (
            "Email is not set up for this ledger, so a code cannot be sent. "
            "Add [notify] to your secrets, or use the sign-up code instead."
        )

    to_them = dict(config)
    to_them["to"] = account.email
    notify._send(
        "[Personal Ledger] Your password reset code",
        f"Your reset code is {code}\n\n"
        "It works once, in the browser tab you asked from, for the next "
        f"{RESET_MINUTES} minutes. If you did not ask for it, ignore this — "
        "nothing has changed on your account.\n",
        to_them,
    )
    return notify.last_error()


def _sheet(secrets: dict):
    from ledger import store

    sheet = store._open_worksheet(secrets, WORKSHEET)
    try:
        first = sheet.row_values(1)
    except Exception:  # noqa: BLE001
        first = []
    if not any(str(v).strip() for v in first):
        sheet.update(values=[COLUMNS], range_name="A1")
    return sheet


def demo() -> None:
    """Self-check for the crypto, which is the part that fails silently."""
    stored = hash_password("correct horse battery", iterations=1000)
    assert stored.startswith(f"{ALGORITHM}$1000$")
    assert "correct horse battery" not in stored, "the password must not be in the hash"

    assert verify("correct horse battery", stored)
    assert not verify("Correct horse battery", stored), "case matters"
    assert not verify("wrong", stored)
    assert not verify("", stored)

    # Two users with the same password must not share a hash, or one crack is two.
    a = hash_password("same password here", iterations=1000)
    b = hash_password("same password here", iterations=1000)
    assert a != b, "each account needs its own salt"
    assert verify("same password here", a) and verify("same password here", b)

    # Anything malformed is a failed sign-in, never an exception on the page.
    for junk in ("", "nonsense", "pbkdf2_sha256$notanumber$aa$bb", None,
                 "md5$1000$aa$bb", "a$b$c$d$e"):
        assert verify("anything", junk) is False, junk

    # Short passwords are refused where they are set, not where they are used.
    try:
        hash_password("short")
    except EntryError:
        pass
    else:
        raise AssertionError("a short password should be refused")

    problems = validate("", "not-an-email", "abc", "abd")
    assert len(problems) == 4, problems
    assert validate("Ravi", "ravi@example.com", "longenough1", "longenough1") == []

    account = Account(email="a@b.com", name="A", password_hash=stored,
                      joined=date(2026, 9, 3))
    assert Account.from_row(dict(zip(COLUMNS, account.to_row()))) == account
    assert find("A@B.COM", [account]) is account, "lookup must ignore case"
    assert find("c@d.com", [account]) is None

    # A reset code must be six digits and must not be predictable.
    codes = {reset_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 150, "codes are repeating far too often"

    assert enabled({}) is False
    assert enabled({"accounts": {"enabled": True}}) is True
    assert signup_code({"accounts": {"signup_code": " hello "}}) == "hello"

    print("ledger.accounts: all checks passed")


if __name__ == "__main__":
    demo()
