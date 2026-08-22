"""Add one entry to the ledger."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import streamlit as st

from ledger import attach, store
from ledger.models import BY_HAND, Direction, Entry, EntryError
from ledger.money import Currency, format_money, spoken, to_minor
from ledger.ui import clear_cache, demo_banner, load_ledger, styles

NEW = "➕ New…"

styles()

result = load_ledger()
demo_banner(result)

st.title("Add Entry")
st.caption("Fields marked * are required — the rest you can leave blank.")

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


# Each save starts a new "round", and every input's key carries the round
# number. Clearing the session value alone is not enough: the browser keeps a
# text input's typed value while the widget's identity is unchanged, so the
# field would still *look* full even though the app had forgotten it. A new
# key is a new widget, and a new widget renders empty.
ROUND = st.session_state.setdefault("form_round", 0)


def field(name: str) -> str:
    return f"{name}_{ROUND}"


def picker(label: str, options: list[str], key: str) -> str:
    """A select that also accepts a new value, so a new person needs no setup."""
    choice = st.selectbox(label, [*options, NEW], key=field(f"{key}_choice"))
    if choice == NEW:
        return st.text_input(f"New {label.lower()}", key=field(f"{key}_new")).strip()
    return choice


# Set by a save; read on the next run so the message appears above an empty form.
if st.session_state.pop("entry_saved", None):
    st.success(st.session_state.pop("entry_saved_text", "Entry added."))


col_a, col_b = st.columns(2)
with col_a:
    person = picker("Person", people, "person")
with col_b:
    ledger_name = picker("Ledger", ledgers_for.get(person, []), "ledger")

col_c, col_d, col_e = st.columns([1, 1, 1])
with col_c:
    when = st.date_input("Date *", value=date.today(), format="DD/MM/YYYY")
with col_d:
    direction = st.radio(
        "Direction",
        [Direction.given, Direction.received],
        format_func=lambda d: "I gave them" if d is Direction.given else "They gave me back",
        horizontal=False,
    )
with col_e:
    amount_text = st.text_input(
        f"Amount ({currency.symbol}) *", placeholder="1500", key=field("amount")
    )
    # Read the figure back in lakhs. An extra zero is hard to see in "2500000"
    # and impossible to miss in "25 lakh".
    try:
        _typed = to_minor(amount_text) if amount_text.strip() else 0
    except ValueError:
        _typed = 0
    if _typed > 0:
        _short = spoken(_typed, currency)
        st.caption(
            f"= {format_money(_typed, currency)}" + (f"  ·  **{_short}**" if _short else "")
        )

note = st.text_input("Note", placeholder="UPI, cash, cheque…", key=field("note"))

statement = st.file_uploader(
    "Bank statement or receipt (optional)",
    type=["pdf", "png", "jpg", "jpeg", "webp"],
    key=field("statement"),
    help=(
        f"Kept inside the spreadsheet, up to {attach.MAX_BYTES // 1024} KB. "
        "Google does not allow this app to write to your Drive, so a link you "
        "paste yourself works too — put it in the entry's Edit box."
    ),
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

# Name what is still missing rather than leaving a dead button with no reason.
absent = []
if not str(person or "").strip():
    absent.append("Person")
if not str(ledger_name or "").strip():
    absent.append("Ledger")
if not amount_text.strip():
    absent.append("Amount")
if absent:
    st.warning("Still needed: **" + "**, **".join(absent) + "**")

for problem in problems:
    st.error(problem)

ready = entry is not None and not problems
if st.button("Save entry", type="primary", disabled=not ready):
    try:
        if statement is not None:
            # Upload first: a saved row pointing at a file that never arrived
            # would be worse than failing before anything is written.
            with st.spinner(f"Storing {statement.name}…"):
                link = attach.put(
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
        short = spoken(entry.amount_minor, entry.currency)
        st.session_state["form_round"] = ROUND + 1   # a fresh, empty form
        st.session_state["entry_saved"] = True
        st.session_state["entry_saved_text"] = (
            f"Added {format_money(entry.amount_minor, entry.currency)}"
            + (f" ({short})" if short else "")
            + f" for {entry.person} on {entry.ledger}. Ready for the next one."
        )
        st.rerun()

st.caption(
    "Amounts are always positive — Direction decides whether it counts as money out "
    "or money back."
)

st.divider()
st.page_link(
    "views/edit_entries.py",
    label="Change or remove an entry",
    icon=":material/edit_note:",
)
