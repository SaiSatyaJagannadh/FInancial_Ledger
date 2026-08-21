"""End-to-end over an in-memory sheet: the append path demo mode cannot reach."""

from datetime import date

import pytest

from ledger import store
from ledger.compute import by_person, filter_entries, monthly_given, totals
from ledger.models import COLUMNS, Direction, Entry
from ledger.money import Currency, to_minor

SECRETS = {
    "gcp_service_account": {"client_email": "svc@example.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/x/edit"},
}


class FakeSheet:
    """Behaves like the slice of gspread.Worksheet the store uses."""

    def __init__(self, rows=None):
        self.header = list(COLUMNS)
        self.rows = list(rows or [])

    def get_all_records(self):
        return [dict(zip(self.header, row)) for row in self.rows]

    def row_values(self, index):
        return self.header if index == 1 else []

    def append_row(self, row, **_kwargs):
        self.rows.append(list(row))

    def update(self, _cell, values):
        self.header = list(values[0])


@pytest.fixture()
def sheet(monkeypatch):
    fake = FakeSheet(
        [
            ["2026-01-10", "Father", "House repair", "given", "1000.00", "INR", "UPI"],
            ["2026-02-10", "Father", "House repair", "received", "400.00", "INR", ""],
        ]
    )
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)
    return fake


def test_round_trip_load_append_load(sheet):
    first = store.load(secrets=SECRETS)
    assert first.demo is False
    assert totals(first.entries).net_minor == to_minor(600)

    store.append(
        Entry(
            date=date(2026, 3, 1),
            person="Father",
            ledger="House repair",
            direction=Direction.received,
            amount_minor=to_minor(250),
            note="cash",
        ),
        secrets=SECRETS,
    )

    second = store.load(secrets=SECRETS)
    assert len(second.entries) == len(first.entries) + 1
    assert totals(second.entries).net_minor == to_minor(350)

    father = next(r for r in by_person(second.entries) if r.person == "Father")
    assert father.last_activity == date(2026, 3, 1)


def test_a_full_repayment_closes_the_ledger(sheet):
    store.append(
        Entry(date=date(2026, 3, 1), person="Father", ledger="House repair",
              direction=Direction.received, amount_minor=to_minor(600)),
        secrets=SECRETS,
    )
    result = store.load(secrets=SECRETS)
    summary = totals(result.entries)
    assert summary.net_minor == 0
    assert summary.ledgers == 1
    assert summary.open_ledgers == 0  # settled, so it stops being chased


def test_overpayment_shows_that_you_owe_them(sheet):
    store.append(
        Entry(date=date(2026, 3, 1), person="Father", ledger="House repair",
              direction=Direction.received, amount_minor=to_minor(900)),
        secrets=SECRETS,
    )
    result = store.load(secrets=SECRETS)
    father = next(r for r in by_person(result.entries) if r.person == "Father")
    assert father.net_minor == to_minor(-300)


def test_a_brand_new_person_needs_no_setup(sheet):
    store.append(
        Entry(date=date(2026, 4, 1), person="Neighbour", ledger="Emergency",
              direction=Direction.given, amount_minor=to_minor(5000)),
        secrets=SECRETS,
    )
    result = store.load(secrets=SECRETS)
    assert {r.person for r in by_person(result.entries)} == {"Father", "Neighbour"}
    assert totals(result.entries).people == 2


def test_appended_amount_survives_the_sheet_round_trip(sheet):
    """A value written then read back must be the same paise, not a rounded float."""
    store.append(
        Entry(date=date(2026, 4, 1), person="Ravi", ledger="Odd",
              direction=Direction.given, amount_minor=to_minor("1234.56")),
        secrets=SECRETS,
    )
    result = store.load(secrets=SECRETS)
    ravi = next(e for e in result.entries if e.person == "Ravi")
    assert ravi.amount_minor == 123_456


def test_dashboard_figures_agree_after_an_append(sheet):
    """Every figure on the page comes from the same filtered list."""
    store.append(
        Entry(date=date(2026, 3, 5), person="Brother", ledger="Bike loan",
              direction=Direction.given, amount_minor=to_minor(2000)),
        secrets=SECRETS,
    )
    entries = filter_entries(store.load(secrets=SECRETS).entries, today=date(2026, 6, 1))
    summary = totals(entries)
    rows = by_person(entries)

    assert sum(r.given_minor for r in rows) == summary.given_minor
    assert sum(r.received_minor for r in rows) == summary.received_minor
    assert sum(r.net_minor for r in rows) == summary.net_minor
    assert len(rows) == summary.people
    assert sum(r["amount_minor"] for r in monthly_given(entries)) == summary.given_minor


def test_a_dollar_entry_does_not_touch_the_rupee_totals(sheet):
    """The exact case this feature exists for: rupees home, dollars abroad."""
    from ledger.compute import by_currency

    store.append(
        Entry(date=date(2026, 3, 1), person="Brother", ledger="Flight ticket",
              direction=Direction.given, amount_minor=to_minor(600),
              currency=Currency.USD),
        secrets=SECRETS,
    )
    entries = store.load(secrets=SECRETS).entries

    rupees = totals(by_currency(entries, Currency.INR))
    dollars = totals(by_currency(entries, Currency.USD))

    assert rupees.net_minor == to_minor(600)      # unchanged rupee ledger
    assert rupees.currency is Currency.INR
    assert dollars.net_minor == to_minor(600)     # same number, different money
    assert dollars.currency is Currency.USD
    assert rupees.people == 1 and dollars.people == 1


def test_same_person_same_ledger_name_in_two_currencies_stays_separate(sheet):
    from ledger.compute import by_currency

    for currency, amount in ((Currency.INR, 5_000), (Currency.USD, 200)):
        store.append(
            Entry(date=date(2026, 4, 1), person="Brother", ledger="Shared",
                  direction=Direction.given, amount_minor=to_minor(amount),
                  currency=currency),
            secrets=SECRETS,
        )
    entries = store.load(secrets=SECRETS).entries
    assert totals(by_currency(entries, Currency.INR)).ledgers == 2   # House repair + Shared
    assert totals(by_currency(entries, Currency.USD)).ledgers == 1   # Shared, separately
