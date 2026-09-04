"""Change notifications.

The rule that matters most here is negative: **a notification must never be
able to affect a save.** The row is already in the sheet by the time one is
sent, so anything that goes wrong afterwards — a wrong password, a blocked
port, a bug in this module — has to stay invisible to the person who just
recorded money.

The second rule is that it must stay silent unless it is deliberately turned
on, which is what keeps the whole test suite and every demo-mode run off the
network.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from ledger import interest, notify, store
from ledger.models import COLUMNS, Direction, Entry
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}
WITH_NOTIFY = {**CONFIGURED, "notify": {"to": "me@example.com", "password": "app-pw"}}


def entry(minor=2_00_000_00, note="uncle", direction=Direction.given, row=46) -> Entry:
    return Entry(date=date(2026, 8, 27), person="Narayana Rao D", ledger="Nanna",
                 direction=direction, amount_minor=minor, currency=Currency.INR,
                 note=note, row=row)


class FakeSheet:
    """The slice of a worksheet the write paths touch."""

    def __init__(self, row=None):
        self.row = list(row) if row else list(entry().to_row())
        self.deleted, self.updated, self.appended = [], [], []

    def row_values(self, index):
        return list(COLUMNS) if index == 1 else list(self.row)

    def append_rows(self, rows, **kw):
        self.appended.extend(rows)

    def update(self, values=None, range_name=None, **kw):
        self.updated.append((range_name, values))

    def delete_rows(self, index):
        self.deleted.append(index)


@pytest.fixture()
def wired(monkeypatch):
    """A sheet that accepts writes, and a record of every notice attempted."""
    sheet = FakeSheet()
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: sheet)
    sent: list[dict] = []
    monkeypatch.setattr(
        notify, "changed",
        lambda kind, action, **kw: sent.append({"kind": kind, "action": action, **kw}),
    )
    return sheet, sent


# ----------------------------------------------------- it must never break a save

def test_a_failing_notifier_does_not_break_an_append(monkeypatch, wired):
    """The row is already written. An SMTP problem is not the saver's problem."""
    sheet, _ = wired

    def explode(*_a, **_kw):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(notify, "changed", explode)
    store.append(entry(), secrets=WITH_NOTIFY)          # must not raise
    assert sheet.appended, "the entry still has to reach the sheet"


def test_a_failing_notifier_does_not_break_an_edit(monkeypatch, wired):
    sheet, _ = wired
    monkeypatch.setattr(notify, "changed",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no route")))
    store.update(entry(), entry(minor=1_50_000_00), secrets=WITH_NOTIFY)
    assert sheet.updated, "the edit still has to reach the sheet"


def test_a_failing_notifier_does_not_break_a_delete(monkeypatch, wired):
    sheet, _ = wired
    monkeypatch.setattr(notify, "changed",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no route")))
    store.delete(entry(), secrets=WITH_NOTIFY)
    assert sheet.deleted == [46]


# ------------------------------------------------------------- silent by default

def test_nothing_is_sent_without_configuration():
    assert notify.settings(None) is None
    assert notify.settings({}) is None
    assert notify.changed("Ledger entry", "added", after=entry(), secrets={}) is False


def test_a_recipient_without_a_password_is_not_configured():
    """Half-filled config is off, not a crash on the first save."""
    assert notify.settings({"notify": {"to": "me@example.com"}}) is None


def test_the_whole_suite_cannot_accidentally_send(monkeypatch):
    """No secrets means the SMTP path is never even reached."""
    monkeypatch.setattr(notify, "_send",
                        lambda *_a: pytest.fail("SMTP must not be touched"))
    notify.changed("Ledger entry", "added", after=entry(), secrets={})


# ------------------------------------------------------------ the right notice

def test_each_ledger_write_announces_the_right_action(wired):
    _, sent = wired
    store.append(entry(), secrets=WITH_NOTIFY)
    store.update(entry(), entry(minor=1_50_000_00), secrets=WITH_NOTIFY)
    store.delete(entry(), secrets=WITH_NOTIFY)
    assert [s["action"] for s in sent] == ["added", "edited", "deleted"]
    assert {s["kind"] for s in sent} == {"Ledger entry"}


def test_an_edit_carries_both_halves_so_a_diff_is_possible(wired):
    _, sent = wired
    before, after = entry(), entry(minor=1_50_000_00)
    store.update(before, after, secrets=WITH_NOTIFY)
    notice = sent[0]
    assert notice["before"] == before and notice["after"] == after


def test_interest_writes_announce_under_their_own_name(monkeypatch):
    sheet = FakeSheet(row=["2026-07-01", "Narayana", "15000.00"])
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: sheet)
    monkeypatch.setattr(interest, "_sheet", lambda _s: sheet)
    sent: list[dict] = []
    monkeypatch.setattr(notify, "changed",
                        lambda kind, action, **kw: sent.append((kind, action)))

    charge = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                            amount_minor=15_000_00, row=7)
    interest.add(charge, secrets=WITH_NOTIFY)
    interest.remove(charge, secrets=WITH_NOTIFY)
    assert sent == [("Interest charge", "added"), ("Interest charge", "deleted")]


# ------------------------------------------------------------------- the message

def test_the_diff_names_only_what_changed():
    lines = notify.describe(entry(), entry(minor=1_50_000_00), COLUMNS)
    assert any(line.startswith("amount:") for line in lines)
    assert not any(line.startswith("person:") for line in lines)


def test_the_amount_reads_as_money_not_as_a_cell():
    """The point of the email is that a figure can be checked at a glance."""
    lines = " | ".join(notify.describe(entry(), entry(minor=1_50_000_00), COLUMNS))
    assert "2,00,000" in lines and "1,50,000" in lines
    assert "200000.00" not in lines


def test_a_direction_flip_is_named(monkeypatch):
    """The change that erases history has to be legible in the notice."""
    lines = notify.describe(
        entry(), entry(direction=Direction.received), COLUMNS
    )
    assert any(line.startswith("direction:") for line in lines)


def test_an_unattributed_change_is_reported_as_one():
    """With no login configured the message must not invent a person."""
    seen = {}
    notify.changed("Ledger entry", "edited", before=entry(),
                   after=entry(minor=1_50_000_00), columns=COLUMNS, secrets={},
                   by="", sender=lambda s, b: seen.update(subject=s, body=b))
    assert "By:" not in seen["body"]
    assert "Narayana Rao D" in seen["subject"]


def test_the_signed_in_address_is_named_when_there_is_one():
    seen = {}
    notify.changed("Ledger entry", "edited", before=entry(),
                   after=entry(minor=1_50_000_00), columns=COLUMNS, secrets={},
                   by="ravi@example.com",
                   sender=lambda s, b: seen.update(body=b))
    assert "By: ravi@example.com" in seen["body"]


def test_attribution_never_costs_a_save(monkeypatch):
    """auth runs with no Streamlit runtime under pytest; it must answer, not raise."""
    from ledger import auth

    assert auth.current_user() == ""


def test_the_password_is_never_put_in_the_message():
    seen = {}
    notify.changed("Ledger entry", "added", after=entry(), columns=COLUMNS,
                   secrets=WITH_NOTIFY,
                   sender=lambda s, b: seen.update(subject=s, body=b))
    assert "app-pw" not in seen["subject"] + seen["body"]


class TestEverySheetWriteIsAnnounced:
    """Coverage, checked against the source rather than remembered.

    Six of fourteen write paths notified when this was first built — the ledger
    and interest only. Spending, grouping and, more importantly, somebody
    getting an account or changing a password all went out silently.
    """

    SOURCES = {
        name: (pathlib.Path(__file__).resolve().parent.parent / "ledger" / name).read_text()
        for name in ("store.py", "interest.py", "spend.py", "people.py",
                     "accounts.py", "archive.py")
    }

    @pytest.mark.parametrize("module,expected", [
        ("store.py", 3), ("interest.py", 3), ("spend.py", 3),
        ("people.py", 3), ("accounts.py", 2), ("archive.py", 1),
    ])
    def test_the_module_announces_each_of_its_writes(self, module, expected):
        source = self.SOURCES[module]
        # Calls, not the definition — archive passes the kind as a variable.
        calls = source.count("_announce(") - source.count("def _announce(")
        assert calls >= expected, (
            f"{module} announces {calls} writes, expected at least {expected}"
        )

    def test_a_password_hash_is_never_put_in_an_email(self):
        """An inbox is the least private place this app can put anything."""
        assert 'redact=("password_hash",)' in self.SOURCES["accounts.py"]

    def test_the_redaction_actually_redacts(self):
        class Row:
            def __init__(self, secret):
                self.person, self.amount_minor, self.currency = "R", 100, None
                self.secret = secret

            def to_row(self):
                return ["R", self.secret]

        lines = notify.describe(Row("hash-aaa"), Row("hash-bbb"),
                                ["person", "password_hash"],
                                redact=("password_hash",))
        assert lines == ["password_hash: changed"]

    def test_an_unredacted_diff_would_have_leaked_it(self):
        """Guard the guard: without redact the hash really does go in."""
        class Row:
            def __init__(self, secret):
                self.person, self.amount_minor, self.currency = "R", 100, None
                self.secret = secret

            def to_row(self):
                return ["R", self.secret]

        lines = notify.describe(Row("hash-aaa"), Row("hash-bbb"),
                                ["person", "password_hash"])
        assert "hash-bbb" in " ".join(lines)
