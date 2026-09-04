"""Sign-up and sign-in against the users tab.

Passwords are the part that fails silently: a hash that never verifies locks
everybody out loudly, but a comparison that leaks, a missing salt, or a
verifier that says True on junk all look exactly like working software.
"""

from __future__ import annotations

from datetime import date

import pytest

from ledger import accounts
from ledger.models import EntryError

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}
FAST = 1000          # iterations; the count is not what these tests are about


# ------------------------------------------------------------------- the hashing

def test_the_password_is_not_recoverable_from_what_is_stored():
    stored = accounts.hash_password("correct horse battery", iterations=FAST)
    assert "correct horse battery" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_right_password_verifies_and_a_wrong_one_does_not():
    stored = accounts.hash_password("correct horse battery", iterations=FAST)
    assert accounts.verify("correct horse battery", stored)
    assert not accounts.verify("correct horse batteri", stored)
    assert not accounts.verify("Correct horse battery", stored), "case must matter"
    assert not accounts.verify("", stored)


def test_two_accounts_with_the_same_password_do_not_share_a_hash():
    """Without a per-user salt, cracking one cracks everyone who reused it."""
    a = accounts.hash_password("same password here", iterations=FAST)
    b = accounts.hash_password("same password here", iterations=FAST)
    assert a != b
    assert accounts.verify("same password here", a)
    assert accounts.verify("same password here", b)


@pytest.mark.parametrize("junk", [
    "", "nonsense", None, 5, "md5$1000$aa$bb",
    "pbkdf2_sha256$notanumber$aa$bb", "pbkdf2_sha256$1000$zz$bb", "a$b$c$d$e",
])
def test_a_malformed_hash_is_a_failed_sign_in_not_a_crash(junk):
    """A hand-edited cell must lock that person out, not take the page down."""
    assert accounts.verify("anything", junk) is False


def test_a_short_password_is_refused_where_it_is_set():
    with pytest.raises(EntryError):
        accounts.hash_password("short")


# -------------------------------------------------------------------- the form

def test_every_problem_is_reported_at_once():
    problems = accounts.validate("", "not-an-email", "abc", "abd")
    assert len(problems) == 4, problems


def test_a_good_signup_has_nothing_wrong_with_it():
    assert accounts.validate("Ravi", "ravi@example.com", "longenough1",
                             "longenough1") == []


def test_mismatched_confirmation_is_caught():
    assert any("do not match" in p for p in
               accounts.validate("R", "r@e.com", "longenough1", "longenough2"))


# ------------------------------------------------------------------ the lookup

def account(email="a@b.com", password="longenough1"):
    return accounts.Account(email=email, name="A",
                            password_hash=accounts.hash_password(password,
                                                                 iterations=FAST),
                            joined=date(2026, 9, 3))


def test_lookup_ignores_case_and_spacing():
    one = account()
    assert accounts.find("  A@B.COM ", [one]) is one
    assert accounts.find("c@d.com", [one]) is None


def test_a_row_survives_the_sheet_unchanged():
    one = account()
    back = accounts.Account.from_row(dict(zip(accounts.COLUMNS, one.to_row())))
    assert back == one
    assert accounts.verify("longenough1", back.password_hash)


# --------------------------------------------------------------- authentication

@pytest.fixture()
def known(monkeypatch):
    people = [account("ravi@example.com", "longenough1")]
    monkeypatch.setattr(accounts, "load", lambda *_a, **_kw: (people, []))
    return people


def test_the_right_password_returns_the_account(known):
    assert accounts.authenticate("ravi@example.com", "longenough1") is known[0]


def test_a_wrong_password_returns_nothing(known):
    assert accounts.authenticate("ravi@example.com", "wrongwrong1") is None


def test_an_unknown_address_returns_nothing_the_same_way(known):
    """Callers must not be able to tell 'no such user' from 'wrong password'."""
    assert accounts.authenticate("nobody@example.com", "longenough1") is None


def test_a_duplicate_registration_is_refused(monkeypatch, known):
    """A second row for one email would shadow the first, so the newer password
    would silently never work."""
    monkeypatch.setattr(accounts, "_sheet", lambda _s: object())
    with pytest.raises(EntryError, match="already an account"):
        accounts.create("Ravi", "RAVI@example.com", "longenough1",
                        secrets=CONFIGURED)


def test_registration_stores_a_hash_and_never_the_password(monkeypatch):
    written = []

    class FakeSheet:
        def row_values(self, n):
            return list(accounts.COLUMNS)

    monkeypatch.setattr(accounts, "load", lambda *_a, **_kw: ([], []))
    monkeypatch.setattr(accounts, "_sheet", lambda _s: FakeSheet())
    from ledger import store
    monkeypatch.setattr(store, "append_rows",
                        lambda sheet, rows, **kw: written.extend(rows))

    accounts.create("Ravi", "Ravi@Example.com", "longenough1", secrets=CONFIGURED)
    row = dict(zip(accounts.COLUMNS, written[0]))
    assert row["email"] == "ravi@example.com", "addresses are stored folded"
    assert "longenough1" not in " ".join(written[0])
    assert accounts.verify("longenough1", row["password_hash"])


# ------------------------------------------------------------------- switched off

def test_it_is_off_unless_switched_on():
    assert accounts.enabled({}) is False
    assert accounts.enabled({"accounts": {}}) is False
    assert accounts.enabled({"accounts": {"enabled": True}}) is True


def test_demo_mode_registers_nobody():
    with pytest.raises(RuntimeError, match="Demo mode"):
        accounts.create("A", "a@b.com", "longenough1", secrets={})


class TestATabThisAppMadeItselfCanBeRead:
    """The bug that made every sign-in say "email or password is wrong".

    `store._open_worksheet` creates a missing tab 20 columns wide. The modules
    write four or five headings into it, so the rest of the header row is blank
    — and gspread refuses a header row containing duplicates, which blanks are.
    `load` caught the refusal, reported no accounts, and the password was never
    compared against anything.

    It applies to every tab the app creates for itself, not just this one.
    """

    def tab(self):
        from gspread.exceptions import GSpreadException

        class TwentyColumns:
            def __init__(self):
                self.rows = []

            def row_values(self, n):
                return list(self.rows[n - 1]) if n <= len(self.rows) else []

            def update(self, values=None, range_name=None, **kw):
                head = [str(v) for v in values[0]] + [""] * (20 - len(values[0]))
                self.rows.insert(0, head) if not self.rows else None
                self.rows[0] = head

            def append_rows(self, rows, **kw):
                for r in rows:
                    self.rows.append([str(c) for c in r] + [""] * (20 - len(r)))

            def get_all_values(self):
                return [list(r) for r in self.rows]

            def get_all_records(self, expected_headers=None):
                header = self.rows[0] if self.rows else []
                dupes = [h for h in set(header) if header.count(h) > 1]
                if expected_headers is None and dupes:
                    raise GSpreadException(f"header contains duplicates: {dupes}")
                return [dict(zip(header, r)) for r in self.rows[1:]]

        return TwentyColumns()

    @pytest.fixture()
    def wired(self, monkeypatch):
        from ledger import store

        sheet = self.tab()
        monkeypatch.setattr(store, "_open_worksheet",
                            lambda _s, tab=None, **kw: sheet)
        return sheet

    def test_the_fake_really_does_refuse_a_bare_read(self, wired):
        """Guard the guard: without the fix this tab is genuinely unreadable."""
        from gspread.exceptions import GSpreadException

        accounts.create("Ravi", "ravi@example.com", "longenough1",
                        secrets=CONFIGURED)
        with pytest.raises(GSpreadException):
            wired.get_all_records()

    def test_an_account_can_be_read_back_after_it_is_created(self, wired):
        accounts.create("Ravi", "ravi@example.com", "longenough1",
                        secrets=CONFIGURED)
        known, problems = accounts.load(CONFIGURED)
        assert problems == []
        assert len(known) == 1 and known[0].email == "ravi@example.com"

    def test_signing_in_works_with_the_password_just_set(self, wired):
        """The user-visible bug, end to end."""
        accounts.create("Ravi", "ravi@example.com", "longenough1",
                        secrets=CONFIGURED)
        assert accounts.authenticate("ravi@example.com", "longenough1",
                                     secrets=CONFIGURED) is not None

    def test_a_wrong_password_is_still_refused(self, wired):
        accounts.create("Ravi", "ravi@example.com", "longenough1",
                        secrets=CONFIGURED)
        assert accounts.authenticate("ravi@example.com", "nope12345",
                                     secrets=CONFIGURED) is None
