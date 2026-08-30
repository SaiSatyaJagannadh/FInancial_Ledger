"""General transactions: the model, and the guards on changing a row."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ledger import spend, store
from ledger.models import EntryError
from ledger.money import Currency
from ledger.spend import Kind, Transaction

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def make(row: int | None = 4, **kw) -> Transaction:
    fields = dict(date=date(2026, 3, 1), category="Rent", amount_minor=1_500_000, row=row)
    fields.update(kw)
    return Transaction(**fields)


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.writes: list = []
        self.deleted: list[int] = []
        self.appended: list = []
        self.options: list = []

    def row_values(self, n):
        return self.rows.get(n, [])

    def update(self, values=None, range_name=None, **kw):
        self.writes.append((range_name, values))

    def delete_rows(self, n):
        self.deleted.append(n)

    def append_rows(self, rows, **kw):
        self.options.append(kw.get("insert_data_option"))
        self.appended.extend(list(r) for r in rows)


def wire(monkeypatch, rows) -> FakeSheet:
    fake = FakeSheet({1: list(spend.COLUMNS), **rows})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    monkeypatch.setattr(store, "_secrets", lambda: CONFIGURED)
    return fake


# ------------------------------------------------------------------- the model

def test_spending_is_negative_and_earning_is_positive():
    """The opposite of the ledger, where money out is positive. This asks what
    you have; the ledger asks what you are owed."""
    assert make().signed_minor == -1_500_000
    assert make(kind=Kind.earned).signed_minor == 1_500_000


def test_only_date_category_and_amount_are_required():
    made = Transaction.from_row({"date": "2026-03-01", "category": "Food", "amount": "250"})
    assert made.amount_minor == 25_000
    assert made.description == "" and made.note == "" and made.end_date is None


@pytest.mark.parametrize("field", ["date", "category", "amount"])
def test_a_missing_required_field_is_named(field):
    row = {"date": "2026-03-01", "category": "Food", "amount": "250"}
    row[field] = ""
    with pytest.raises(EntryError, match=field):
        Transaction.from_row(row)


def test_a_period_that_runs_backwards_is_refused():
    with pytest.raises(EntryError, match="end date"):
        make(end_date=date(2025, 1, 1))


def test_an_end_date_equal_to_the_start_is_not_ongoing():
    assert not make(end_date=date(2026, 3, 1)).ongoing


def test_an_ongoing_cost_spells_out_its_period():
    t = make(end_date=date(2026, 8, 31))
    assert t.ongoing
    assert t.period == "01 Mar 2026 → 31 Aug 2026"


def test_a_zero_amount_is_refused():
    with pytest.raises(EntryError, match="more than zero"):
        make(amount_minor=0)


def test_the_row_round_trips_including_the_period():
    t = make(end_date=date(2026, 8, 31), description="flat", note="UPI", kind=Kind.earned)
    again = Transaction.from_row(dict(zip(spend.COLUMNS, t.to_row())))
    assert again.end_date == t.end_date
    assert again.kind is Kind.earned
    assert again.description == "flat" and again.note == "UPI"


# ------------------------------------------------------------------- reporting

def test_totals_keep_spending_and_earning_apart():
    got = spend.totals(
        [make(), make(amount_minor=500_000, kind=Kind.earned)], Currency.INR
    )
    assert got.spent_minor == 1_500_000
    assert got.earned_minor == 500_000
    assert got.net_minor == -1_000_000


def test_totals_ignore_the_other_currency():
    got = spend.totals([make(), make(currency=Currency.USD)], Currency.INR)
    assert got.count == 1


def test_an_ongoing_cost_belongs_to_every_year_it_spans():
    t = make(date=date(2025, 11, 1), end_date=date(2026, 2, 1))
    assert spend.years([t]) == [2026, 2025]
    assert spend.in_year([t], 2025) == [t]
    assert spend.in_year([t], 2026) == [t]
    assert spend.in_year([t], 2027) == []


def test_categories_are_ranked_by_spend():
    rows = [make(category="Rent"), make(category="Food", amount_minor=10_000),
            make(category="Food", amount_minor=20_000)]
    ranked = spend.by_category(rows, Currency.INR)
    assert [b["category"] for b in ranked] == ["Rent", "Food"]
    assert ranked[1]["count"] == 2


# -------------------------------------------------------------------- the sheet

def test_add_appends_the_row(monkeypatch):
    fake = wire(monkeypatch, {})
    spend.add(make(), CONFIGURED)
    assert fake.appended[0][3] == "Rent"


def test_delete_refuses_when_the_row_changed(monkeypatch):
    fake = wire(monkeypatch, {4: ["2026-03-01", "", "spent", "Something else",
                                  "", "99.00", "INR", "", ""]})
    with pytest.raises(RuntimeError, match="no longer matches"):
        spend.remove(make(), CONFIGURED)
    assert fake.deleted == []


def test_delete_removes_a_matching_row(monkeypatch):
    t = make()
    fake = wire(monkeypatch, {4: t.to_row()})
    spend.remove(t, CONFIGURED)
    assert fake.deleted == [4]


def test_the_amount_is_compared_as_a_number(monkeypatch):
    """Sheets returns "15000" for what we wrote as "15000.00"."""
    t = make()
    row = t.to_row()
    row[5] = "15000"
    fake = wire(monkeypatch, {4: row})
    spend.remove(t, CONFIGURED)
    assert fake.deleted == [4]


def test_edit_writes_to_the_original_row(monkeypatch):
    t = make()
    fake = wire(monkeypatch, {4: t.to_row()})
    spend.replace_row(t, replace(t, amount_minor=999_00, row=99), CONFIGURED)
    range_name, values = fake.writes[-1]
    assert range_name.startswith("A4:")
    assert values[0][5] == "999.00"


def test_edit_refuses_when_the_row_changed(monkeypatch):
    fake = wire(monkeypatch, {4: ["2020-01-01", "", "spent", "Other", "", "1.00", "INR", "", ""]})
    with pytest.raises(RuntimeError, match="no longer matches"):
        spend.replace_row(make(), replace(make(), note="x"), CONFIGURED)
    assert not [w for w in fake.writes if w[0] and w[0].startswith("A4:")]


def test_a_row_with_no_number_cannot_be_changed(monkeypatch):
    wire(monkeypatch, {})
    with pytest.raises(RuntimeError, match="no sheet row"):
        spend.remove(make(row=None), CONFIGURED)
