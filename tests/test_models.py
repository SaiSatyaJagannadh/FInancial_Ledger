from datetime import date

import pytest

from ledger.money import Currency
from ledger.models import COLUMNS, Direction, Entry, EntryError, parse_date, parse_direction


def make(**kw):
    base = dict(
        date=date(2026, 1, 1),
        person="Brother",
        ledger="Bike loan",
        direction=Direction.given,
        amount_minor=10_000,
    )
    base.update(kw)
    return Entry(**base)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-01-24", date(2026, 1, 24)),
        ("24/01/2026", date(2026, 1, 24)),
        ("24-01-2026", date(2026, 1, 24)),
        ("24 Jan 2026", date(2026, 1, 24)),
        (date(2026, 1, 24), date(2026, 1, 24)),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_rejects_nonsense():
    with pytest.raises(EntryError, match="unrecognised date"):
        parse_date("sometime last year")


@pytest.mark.parametrize("word", ["given", "Gave", "LENT", "out", "paid"])
def test_direction_given_synonyms(word):
    assert parse_direction(word) is Direction.given


@pytest.mark.parametrize("word", ["received", "got", "repaid", "IN", "returned"])
def test_direction_received_synonyms(word):
    assert parse_direction(word) is Direction.received


def test_direction_rejects_unknown():
    with pytest.raises(EntryError, match="must be 'given' or 'received'"):
        parse_direction("maybe")


def test_signed_amount_carries_the_direction():
    assert make(direction=Direction.given).signed_minor == 10_000
    assert make(direction=Direction.received).signed_minor == -10_000


def test_amount_must_be_positive():
    # Direction already carries the sign; a negative amount would double-negate.
    with pytest.raises(EntryError, match="must be positive"):
        make(amount_minor=-10_000)
    with pytest.raises(EntryError, match="must be positive"):
        make(amount_minor=0)


@pytest.mark.parametrize("field", ["person", "ledger"])
def test_required_text_fields(field):
    with pytest.raises(EntryError, match=f"{field} is required"):
        make(**{field: "   "})


def test_from_row_reads_a_sheet_row():
    entry = Entry.from_row(
        {
            "date": "24/01/2026",
            "person": " Father ",
            "ledger": "House repair",
            "direction": "gave",
            "amount": "1,200.50",
            "note": "UPI",
        },
        row_number=7,
    )
    assert entry.person == "Father"
    assert entry.amount_minor == 120_050
    assert entry.direction is Direction.given
    assert entry.row == 7


def test_from_row_names_the_missing_column():
    with pytest.raises(EntryError, match="missing column"):
        Entry.from_row({"date": "2026-01-01", "person": "X"})


def test_to_row_round_trips():
    entry = make(amount_minor=120_050, note="UPI")
    row = entry.to_row()
    assert row == ["2026-01-01", "Brother", "Bike loan", "given", "1200.50", "INR", "UPI", "", ""]
    again = Entry.from_row(dict(zip(COLUMNS, row)))
    assert again.amount_minor == entry.amount_minor
    assert again.date == entry.date


def test_to_row_is_exact_for_large_amounts():
    """The written cell must not depend on float division at the store boundary."""
    entry = make(amount_minor=9_007_199_254_740_993)  # beyond exact float integers
    assert entry.to_row()[4] == "90071992547409.93"


@pytest.mark.parametrize("paise,text", [(1, "0.01"), (99, "0.99"), (100, "1.00"), (12345, "123.45")])
def test_to_row_amount_formatting(paise, text):
    assert make(amount_minor=paise).to_row()[4] == text


def test_currency_defaults_to_rupees_for_a_sheet_without_the_column():
    """Sheets written before currency existed are rupee ledgers."""
    entry = Entry.from_row(
        {"date": "2026-01-01", "person": "A", "ledger": "L",
         "direction": "given", "amount": "100"}
    )
    assert entry.currency is Currency.INR


@pytest.mark.parametrize(
    "written,expected",
    [("USD", Currency.USD), ("usd", Currency.USD), ("$", Currency.USD),
     ("INR", Currency.INR), ("₹", Currency.INR), ("Rs", Currency.INR), ("", Currency.INR)],
)
def test_currency_is_read_from_the_sheet(written, expected):
    entry = Entry.from_row(
        {"date": "2026-01-01", "person": "A", "ledger": "L",
         "direction": "given", "amount": "100", "currency": written}
    )
    assert entry.currency is expected


def test_unknown_currency_is_reported_not_guessed():
    with pytest.raises(EntryError, match="unknown currency"):
        Entry.from_row(
            {"date": "2026-01-01", "person": "A", "ledger": "L",
             "direction": "given", "amount": "100", "currency": "GBP"}
        )


def test_currency_is_part_of_the_ledger_identity():
    """Same person, same ledger name, different money: two arrangements."""
    rupees = make(currency=Currency.INR)
    dollars = make(currency=Currency.USD)
    assert rupees.key != dollars.key


def test_dollar_row_round_trips():
    entry = make(currency=Currency.USD, amount_minor=45_000)
    row = entry.to_row()
    assert row[5] == "USD"
    assert Entry.from_row(dict(zip(COLUMNS, row))).currency is Currency.USD
