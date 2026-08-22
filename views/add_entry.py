"""Add one entry to the ledger."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import streamlit as st

from ledger import store
from ledger.models import BY_HAND, Direction, Entry, EntryError
from ledger.money import Currency, format_money, to_minor
from ledger.ui import clear_cache, demo_banner, entry_ledger, load_ledger, styles

NEW = "➕ New…"

styles()

result = load_ledger()
demo_banner(result)

st.title("Add Entry")

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True,
        help="Rupee and dollar ledgers are kept completely separate.",
    )
)

# Only offer people and ledgers that exist *in this currency*, so picking USD
# does not suggest a rupee arrangement that has nothing to do with it.
in_currency = [e for e in result.entries if e.currency is currency]
people = sorted({e.person for e in in_currency})
ledgers_for = {
    person: sorted({e.ledger for e in in_currency if e.person == person}) for person in people
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
    amount_text = st.text_input(
        f"Amount ({currency.symbol})", placeholder="1500", key=f"amount_{currency.value}"
    )

note = st.text_input("Note", placeholder="UPI, cash, cheque…")

statement = st.file_uploader(
    "Bank statement or receipt (optional)",
    type=["pdf", "png", "jpg", "jpeg", "webp"],
    help="Uploaded to your Drive folder and linked from the entry.",
)

# Validate before offering to save, so the preview is the thing that gets saved.
problems: list[str] = []
entry: Entry | None = None

if amount_text.strip():
    try:
        minor = to_minor(amount_text)
        if minor <= 0:
            problems.append("Amount must be more than zero — use Direction to say which way.")
        else:
            entry = Entry(
                date=when,
                person=person or "",
                ledger=ledger_name or "",
                direction=direction,
                amount_minor=minor,
                currency=currency,
                note=note,
                source=BY_HAND,
            )
    except ValueError as exc:
        problems.append(str(exc))
    except EntryError as exc:
        problems.append(str(exc))

if entry is not None:
    verb = "give" if direction is Direction.given else "get back"
    st.info(
        f"**{format_money(entry.amount_minor, entry.currency)}** — you {verb} this "
        f"{'to' if direction is Direction.given else 'from'} **{entry.person}** "
        f"on *{entry.ledger}*, dated {entry.date:%d %b %Y}."
    )

for problem in problems:
    st.error(problem)

ready = entry is not None and not problems
if st.button("Save entry", type="primary", disabled=not ready):
    try:
        if statement is not None:
            # Upload first: a saved row pointing at a file that never arrived
            # would be worse than failing before anything is written.
            with st.spinner(f"Uploading {statement.name}…"):
                link = store.upload_attachment(
                    statement.name,
                    statement.getvalue(),
                    statement.type or "application/octet-stream",
                )
            entry = replace(entry, attachment=link)
        store.append(entry)
    except RuntimeError as exc:
        # Demo mode: say so rather than pretending the row was written.
        st.warning(str(exc))
    except Exception as exc:
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        clear_cache()
        st.success(f"Saved {format_money(entry.amount_minor, entry.currency)} for {entry.person}.")
        st.balloons()

st.caption(
    "Amounts are always positive — Direction decides whether it counts as money out "
    "or money back."
)

# The entries live right here, under the form. Adding one and then hunting for
# it on another page to fix a typo is the thing that makes a ledger annoying.
st.divider()

recent = sorted(result.entries, key=lambda e: (e.date, e.row or 0), reverse=True)
mine = [e for e in recent if e.currency is currency]

head, filter_col = st.columns([3, 1.4], vertical_alignment="bottom")
with head:
    st.subheader(f"{currency.flag}  {currency.label} entries")
    st.caption("Newest first. Delete asks twice — the sheet row goes for good.")
with filter_col:
    only_person = st.selectbox(
        "Show", ["Everyone", *sorted({e.person for e in mine})], label_visibility="collapsed",
    )

if only_person != "Everyone":
    mine = [e for e in mine if e.person == only_person]

SHOWN = 12
entry_ledger(mine[:SHOWN], scope="add", empty=f"No {currency.label} entries yet.")

if len(mine) > SHOWN:
    st.caption(f"Showing the {SHOWN} most recent of {len(mine)}. The rest are on the Ledger page.")
