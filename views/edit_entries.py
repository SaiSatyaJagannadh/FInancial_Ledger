"""Find an entry and change it, or remove it.

Separate from Add entry on purpose. Adding is a quick, repetitive act — type,
save, type the next. Correcting is a slower one that starts with finding the
right row. Putting a long list under the form made the common case scroll past
something it never needed.
"""

from __future__ import annotations

import streamlit as st

from ledger.money import Currency, compact, format_money
from ledger.ui import demo_banner, entry_table, load_ledger, styles

ANYONE = "Everyone"
ANY_YEAR = "All years"
ANY_LEDGER = "All ledgers"

styles()

result = load_ledger()
demo_banner(result)

st.title("Edit entries")
st.caption("Find the row, then change or remove it. Deleting asks twice — the sheet row goes for good.")

if not result.entries:
    st.info("Nothing recorded yet. Add an entry first.")
    st.stop()

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True,
    )
)

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

people = sorted({e.person for e in mine})
years = sorted({e.date.year for e in mine}, reverse=True)

person_col, ledger_col, year_col = st.columns(3)
with person_col:
    who = st.selectbox("Person", [ANYONE, *people])
with ledger_col:
    for_person = [e for e in mine if who in (ANYONE, e.person)]
    books = sorted({e.ledger for e in for_person})
    which = st.selectbox("Ledger", [ANY_LEDGER, *books])
with year_col:
    year = st.selectbox("Year", [ANY_YEAR, *years])

search = st.text_input(
    "Search", placeholder="Part of a note, a person, a ledger, or an amount",
    help="Matches the note, the person, the ledger and the amount as typed.",
)

shown = mine
if who != ANYONE:
    shown = [e for e in shown if e.person == who]
if which != ANY_LEDGER:
    shown = [e for e in shown if e.ledger == which]
if year != ANY_YEAR:
    shown = [e for e in shown if e.date.year == int(year)]
if search.strip():
    needle = search.strip().lower()
    shown = [
        e for e in shown
        if needle in e.note.lower()
        or needle in e.person.lower()
        or needle in e.ledger.lower()
        or needle in f"{e.amount_minor / 100:.2f}"
    ]

st.divider()

if not shown:
    st.info("Nothing matches those filters. Widen them and the entries come back.")
    st.stop()

total = sum(e.signed_minor for e in shown)
count_col, net_col, oldest_col = st.columns(3)
count_col.metric("Showing", f"{len(shown)} of {len(mine)}")
short = compact(abs(total), currency)
net_col.metric(
    "Net of these",
    f"{currency.symbol}{short}" if short else format_money(abs(total), currency),
    delta="owed to you" if total >= 0 else "you owe",
    delta_color="off",
)
oldest_col.metric("Oldest", f"{min(e.date for e in shown):%b %Y}")

st.divider()

entry_table(
    sorted(shown, key=lambda e: (e.date, e.row or 0), reverse=True),
    scope="edit",
)
