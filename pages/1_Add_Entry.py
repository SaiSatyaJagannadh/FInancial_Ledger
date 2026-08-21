"""Add one entry to the ledger."""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger import store
from ledger.models import Direction, Entry, EntryError
from ledger.money import format_inr, to_paise
from ledger.ui import clear_cache, demo_banner, load_ledger, page_config

NEW = "➕ New…"

page_config("Add Entry")

result = load_ledger()
demo_banner(result)

st.title("Add Entry")

people = sorted({e.person for e in result.entries})
ledgers_for = {
    person: sorted({e.ledger for e in result.entries if e.person == person}) for person in people
}


def picker(label: str, options: list[str], key: str) -> str:
    """A select that also accepts a new value, so a new person needs no setup."""
    choice = st.selectbox(label, [*options, NEW], key=f"{key}_choice")
    if choice == NEW:
        return st.text_input(f"New {label.lower()}", key=f"{key}_new").strip()
    return choice


col_a, col_b = st.columns(2)
with col_a:
    person = picker("Person", people, "person")
with col_b:
    ledger_name = picker("Ledger", ledgers_for.get(person, []), "ledger")

col_c, col_d, col_e = st.columns([1, 1, 1])
with col_c:
    when = st.date_input("Date", value=date.today(), format="DD/MM/YYYY")
with col_d:
    direction = st.radio(
        "Direction",
        [Direction.given, Direction.received],
        format_func=lambda d: "I gave them" if d is Direction.given else "They gave me back",
        horizontal=False,
    )
with col_e:
    amount_text = st.text_input("Amount (₹)", placeholder="1500")

note = st.text_input("Note", placeholder="UPI, cash, cheque…")

# Validate before offering to save, so the preview is the thing that gets saved.
problems: list[str] = []
entry: Entry | None = None

if amount_text.strip():
    try:
        paise = to_paise(amount_text)
        if paise <= 0:
            problems.append("Amount must be more than zero — use Direction to say which way.")
        else:
            entry = Entry(
                date=when,
                person=person or "",
                ledger=ledger_name or "",
                direction=direction,
                amount_paise=paise,
                note=note,
            )
    except ValueError as exc:
        problems.append(str(exc))
    except EntryError as exc:
        problems.append(str(exc))

if entry is not None:
    verb = "give" if direction is Direction.given else "get back"
    st.info(
        f"**{format_inr(entry.amount_paise)}** — you {verb} this "
        f"{'to' if direction is Direction.given else 'from'} **{entry.person}** "
        f"on *{entry.ledger}*, dated {entry.date:%d %b %Y}."
    )

for problem in problems:
    st.error(problem)

ready = entry is not None and not problems
if st.button("Save entry", type="primary", disabled=not ready):
    try:
        store.append(entry)
    except RuntimeError as exc:
        # Demo mode: say so rather than pretending the row was written.
        st.warning(str(exc))
    except Exception as exc:
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        clear_cache()
        st.success(f"Saved {format_inr(entry.amount_paise)} for {entry.person}.")
        st.balloons()

st.divider()
st.caption(
    "Amounts are always positive — Direction decides whether it counts as money out "
    "or money back."
)
