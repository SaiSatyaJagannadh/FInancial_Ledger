"""Record one general transaction. Three fields are required; the rest are not."""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger import spend
from ledger.models import BY_HAND, EntryError
from ledger.money import Currency, format_money, spoken, to_minor
from ledger.ui import demo_banner, load_ledger, styles, transaction_table

NEW = "➕ New…"

styles()

result = load_ledger()
demo_banner(result)

st.title("Add spending")
st.caption("Rent, food, fees, a salary. Fields marked * are required — the rest you can leave blank.")

rows, problems = spend.load()
for problem in problems:
    st.warning(problem)

kind_col, currency_col = st.columns([2, 2])
with kind_col:
    kind = st.radio(
        "Kind", list(spend.Kind), format_func=lambda k: k.label, horizontal=True,
        help="Spent is money out. Earned is money in.",
    )
with currency_col:
    currency = Currency(
        st.radio(
            "Currency", [c.value for c in Currency],
            format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
            horizontal=True,
        )
    )

known = sorted({t.category for t in rows} | set(spend.CATEGORIES))
category_col, amount_col = st.columns([2, 1.4])
with category_col:
    picked = st.selectbox("Category *", [NEW, *known])
    category = st.text_input(
        "New category", placeholder="Rent, Food, Fees…", label_visibility="collapsed"
    ) if picked == NEW else picked
with amount_col:
    amount_text = st.text_input(f"Amount ({currency.symbol}) *", placeholder="1500")
    try:
        typed = to_minor(amount_text) if amount_text.strip() else 0
    except ValueError:
        typed = 0
    if typed > 0:
        short = spoken(typed, currency)
        st.caption(
            f"= {format_money(typed, currency)}" + (f"  ·  **{short}**" if short else "")
        )

# An ongoing cost — rent, a course fee paid over months — needs a period, not
# a day. It is off by default because most transactions are a single day.
ongoing = st.checkbox("This runs over a period (rent, EMI, a subscription)")
if ongoing:
    from_col, to_col = st.columns(2)
    starts = from_col.date_input("From *", value=date.today(), format="DD/MM/YYYY")
    ends = to_col.date_input("Until *", value=date.today(), format="DD/MM/YYYY")
else:
    starts = st.date_input("Date *", value=date.today(), format="DD/MM/YYYY")
    ends = None

description = st.text_input("Description", placeholder="What it was for")
note = st.text_input("Note", placeholder="UPI, cash, cheque…")

# Say what is missing, rather than leaving a disabled button and no reason.
missing: list[str] = []
if not str(category).strip():
    missing.append("Category")
if typed <= 0:
    missing.append("Amount")
if ends is not None and starts is not None and ends < starts:
    missing.append("Until (it cannot be before From)")

transaction = None
if not missing:
    try:
        transaction = spend.Transaction(
            date=starts,
            end_date=ends if ongoing else None,
            kind=kind,
            category=str(category).strip(),
            amount_minor=typed,
            currency=currency,
            description=description.strip(),
            note=note.strip(),
            source=BY_HAND,
        )
    except EntryError as exc:
        missing.append(str(exc))

if transaction is not None:
    verb = "spent" if kind is spend.Kind.spent else "earned"
    st.info(
        f"**{format_money(transaction.amount_minor, currency)}** {verb} on "
        f"*{transaction.category}* — {transaction.period}."
    )
elif missing:
    st.warning("Still needed: **" + "**, **".join(missing) + "**")

if st.button("Save transaction", type="primary", disabled=transaction is None):
    try:
        spend.add(transaction)
    except Exception as exc:  # noqa: BLE001 — surface whatever the sheet said
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        short = spoken(transaction.amount_minor, currency)
        st.success(
            f"Added {format_money(transaction.amount_minor, currency)}"
            + (f" ({short})" if short else "")
            + f" · {transaction.category} · {transaction.period}."
        )
        st.rerun()

st.divider()

if not rows:
    st.caption("Nothing recorded yet.")
    st.stop()

mine = [t for t in rows if t.currency is currency]
available = spend.years(mine)

head, year_col = st.columns([3, 1.4], vertical_alignment="bottom")
with head:
    st.subheader(f"{currency.flag}  Recorded so far")
    st.caption("Newest first. Delete asks twice — the sheet row goes for good.")
with year_col:
    year = st.selectbox("Year", ["All years", *available], label_visibility="collapsed")

shown = mine if year == "All years" else spend.in_year(mine, int(year))
shown = sorted(shown, key=lambda t: (t.date, t.row or 0), reverse=True)

transaction_table(shown, scope="addspend", empty="Nothing for this year.")
