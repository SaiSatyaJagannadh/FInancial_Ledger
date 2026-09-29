"""Old debts cleared between people — recorded, filed under a name, never summed.

Somebody hands somebody else money to clear an old debt of theirs. There is no
ledger row to net it against, so this page writes it down and adds it to
nothing: not the ledger, not spending, not interest. The heading figure is the
sum of *these rows only*, which is why it says so.

Grouped by the name it was given under, alphabetically — this is a filing
cabinet, and the question it answers is "what is filed under Vihar".
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger import clearance
from ledger.models import BY_HAND, EntryError
from ledger.money import Currency, format_money, spoken, to_minor
from ledger.ui import demo_banner, esc, load_ledger, styles

NEW = "➕ New…"

styles()

result = load_ledger()
demo_banner(result)

st.title("Debt clearance")
st.caption(
    "Money handed over to clear somebody's old debt. Kept as a record only — "
    "it is never added to the ledger, to spending or to interest."
)

rows, problems = clearance.load()
for problem in problems:
    st.warning(problem)

# Names already in the workbook, so a person is picked rather than retyped —
# a name typed a second way files the same person in two places.
known = sorted(
    {c.payer for c in rows} | {c.under for c in rows} | {e.person for e in result.entries},
    key=str.casefold,
)


def picker(label: str, key: str, hint: str) -> str:
    choice = st.selectbox(label, [*known, NEW], key=f"cl_{key}", help=hint)
    if choice != NEW:
        return choice
    return st.text_input(
        f"New name for {label.lower()}", key=f"cl_{key}_new",
        placeholder="Type the name", label_visibility="collapsed",
    )


st.subheader("Record one")

payer_col, under_col = st.columns(2)
with payer_col:
    payer = picker("Who gave it *", "payer", "The person who handed the money over.")
with under_col:
    under = picker("Given under *", "under",
                   "Whose old debt it clears. Records are filed and sorted by this name.")

amount_col, currency_col = st.columns([1.4, 2])
with currency_col:
    currency = Currency(
        st.radio(
            "Currency", [c.value for c in Currency],
            format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
            horizontal=True, key="cl_currency",
        )
    )
with amount_col:
    amount_text = st.text_input(f"Amount ({currency.symbol}) *", placeholder="2500",
                               key="cl_amount")
    try:
        typed = to_minor(amount_text) if amount_text.strip() else 0
    except ValueError:
        typed = 0
    if typed > 0:
        short = spoken(typed, currency)
        st.caption(
            f"= {format_money(typed, currency)}" + (f"  ·  **{short}**" if short else "")
        )

when = st.date_input("Date *", value=date.today(), format="DD/MM/YYYY", key="cl_date")
note = st.text_input("Note", placeholder="What the old debt was, how it was paid",
                     key="cl_note")

missing: list[str] = []
if not str(payer).strip():
    missing.append("Who gave it")
if not str(under).strip():
    missing.append("Given under")
if typed <= 0:
    missing.append("Amount")

record = None
if not missing:
    try:
        record = clearance.Clearance(
            date=when, payer=str(payer).strip(), under=str(under).strip(),
            amount_minor=typed, currency=currency, note=note.strip(), source=BY_HAND,
        )
    except EntryError as exc:
        missing.append(str(exc))

if record is not None:
    st.info(
        f"**{format_money(record.amount_minor, currency)}** from "
        f"*{record.payer}*, filed under *{record.under}* — {record.date:%d %b %Y}. "
        "A record only; no total elsewhere changes."
    )
elif missing:
    st.warning("Still needed: **" + "**, **".join(missing) + "**")

if st.button("Save record", type="primary", disabled=record is None):
    try:
        clearance.add(record)
    except Exception as exc:  # noqa: BLE001 — surface whatever the sheet said
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        st.success(
            f"Recorded {format_money(record.amount_minor, currency)} under "
            f"{record.under}."
        )
        st.rerun()

st.divider()

if not rows:
    st.caption("Nothing recorded yet.")
    st.stop()

head, year_col = st.columns([3, 1.4], vertical_alignment="bottom")
with head:
    st.subheader(f"{currency.flag}  Filed under each name")
with year_col:
    year = st.selectbox("Year", ["All years", *clearance.years(rows)],
                        label_visibility="collapsed", key="cl_year")

shown = [c for c in rows if c.currency is currency]
if year != "All years":
    shown = [c for c in shown if c.date.year == int(year)]

if not shown:
    st.info("Nothing matches those filters. Widen them and it comes back.")
    st.stop()

total = clearance.recorded_total(shown, currency)
st.caption(
    f"{format_money(total, currency)} across {len(shown)} "
    f"record{'s' if len(shown) != 1 else ''} — the sum of these rows only, "
    "counted nowhere else in the app."
)

groups = clearance.by_under(shown, currency)
for bucket in groups:
    of_group = sorted(
        (c for c in shown if c.under == bucket["under"]),
        key=lambda c: (c.date, c.row or 0), reverse=True,
    )
    with st.expander(
        f"{bucket['under']} — {format_money(bucket['total_minor'], currency)} · "
        f"{bucket['count']} record{'s' if bucket['count'] != 1 else ''}",
        expanded=(len(groups) == 1),
    ):
        st.caption("From: " + ", ".join(bucket["payers"]))
        for c in of_group:
            when_col, amount_col, payer_col, note_col, remove_col = st.columns(
                [1.3, 1.3, 1.5, 2.4, 1], vertical_alignment="center"
            )
            when_col.markdown(
                f'<div class="khata-cell">{c.date:%d %b %Y}</div>',
                unsafe_allow_html=True,
            )
            amount_col.markdown(
                '<div class="khata-cell khata-amount">'
                f'{format_money(c.amount_minor, c.currency)}</div>',
                unsafe_allow_html=True,
            )
            payer_col.markdown(
                f'<div class="khata-cell">{esc(c.payer)}</div>', unsafe_allow_html=True
            )
            note_col.markdown(
                f'<div class="khata-cell khata-meta">{esc(c.note) or "—"}</div>',
                unsafe_allow_html=True,
            )
            with remove_col:
                # Two clicks, like everywhere else — the sheet has no undo.
                armed = f"cl_arm_{c.row}"
                if not st.session_state.get(armed):
                    if c.row is not None and st.button("Delete", key=f"cl_del_{c.row}",
                                                       width="stretch"):
                        st.session_state[armed] = True
                        st.rerun()
                else:
                    if st.button("Delete it", key=f"cl_yes_{c.row}", type="primary",
                                 width="stretch"):
                        try:
                            clearance.remove(c)
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Could not delete: {exc}")
                            st.session_state[armed] = False
                        else:
                            st.session_state[armed] = False
                            st.toast("Record deleted")
                            st.rerun()
                    if st.button("Cancel", key=f"cl_no_{c.row}", width="stretch"):
                        st.session_state[armed] = False
                        st.rerun()
            st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)
