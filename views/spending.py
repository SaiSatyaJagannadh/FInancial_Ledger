"""General transactions — money in and out that is nobody's debt.

Kept apart from the lending ledger on purpose. Rent is not owed to you by
anyone, and letting it into "who owes me what" would make that number a lie.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ledger import spend
from ledger.money import Currency, compact, format_money
from ledger.ui import demo_banner, load_ledger, styles, transaction_table

styles()

result = load_ledger()
demo_banner(result)

st.title("Spending")
st.caption("Everything that is not a loan: rent, food, fees, salary. Separate from the ledger, and never added to it.")

rows, problems = spend.load()
for problem in problems:
    st.warning(problem)

if not rows:
    st.info(
        "No transactions yet. Add one from **Add spending** — rent, groceries, "
        "a fee, a salary. Only the date, a category and an amount are required."
    )
    st.stop()

available = spend.years(rows)
year_col, currency_col, kind_col = st.columns([1.2, 1.6, 1.6])
with year_col:
    year = st.selectbox("Year", ["All years", *available])
with currency_col:
    currency = Currency(
        st.selectbox("Currency", [c.value for c in Currency],
                     format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}")
    )
with kind_col:
    which = st.selectbox("Showing", ["Everything", "Only spending", "Only income"])

chosen = rows if year == "All years" else spend.in_year(rows, int(year))
chosen = [t for t in chosen if t.currency is currency]
if which == "Only spending":
    chosen = [t for t in chosen if t.kind is spend.Kind.spent]
elif which == "Only income":
    chosen = [t for t in chosen if t.kind is spend.Kind.earned]

if not chosen:
    st.info("Nothing matches those filters. Widen them and it comes back.")
    st.stop()

st.divider()

summary = spend.totals(chosen, currency)


def headline(amount: int) -> str:
    short = compact(amount, currency)
    return f"{currency.symbol}{short}" if short else format_money(amount, currency)


spent_col, earned_col, net_col, count_col = st.columns(4)
spent_col.metric("Spent", headline(summary.spent_minor))
earned_col.metric("Earned", headline(summary.earned_minor))
net_col.metric(
    "Net", headline(abs(summary.net_minor)),
    delta="in hand" if summary.net_minor >= 0 else "short",
    delta_color="normal" if summary.net_minor >= 0 else "inverse",
)
count_col.metric("Transactions", summary.count)

for slot, amount in ((spent_col, summary.spent_minor), (earned_col, summary.earned_minor),
                     (net_col, abs(summary.net_minor))):
    if compact(amount, currency):
        slot.caption(format_money(amount, currency))

st.divider()

table_col, chart_col = st.columns([1.3, 1], gap="large")

with table_col:
    st.subheader("Where it went")
    buckets = spend.by_category(chosen, currency)
    st.dataframe(
        pd.DataFrame([
            {
                "Category": b["category"],
                "Spent": b["spent_minor"] / 100,
                "Earned": b["earned_minor"] / 100,
                "In short": compact(b["spent_minor"], currency) or "—",
                "Count": b["count"],
            }
            for b in buckets
        ]),
        hide_index=True,
        width="stretch",
        column_config={
            "Spent": st.column_config.NumberColumn(format="%.2f"),
            "Earned": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(f"Amounts in {currency.value}.")

with chart_col:
    st.subheader("By category")
    frame = pd.DataFrame([
        {"Category": b["category"], "Amount": b["spent_minor"] / 100}
        for b in buckets if b["spent_minor"] > 0
    ])
    if frame.empty:
        st.info("Nothing spent in this selection.")
    else:
        st.altair_chart(
            alt.Chart(frame).mark_bar().encode(
                x=alt.X("Amount:Q", title=f"Spent ({currency.symbol})"),
                y=alt.Y("Category:N", sort="-x", title=None),
                tooltip=["Category", alt.Tooltip("Amount:Q", format=",.2f")],
            ).properties(height=max(140, 38 * len(frame))),
            width="stretch",
        )

st.divider()

st.subheader("Every transaction")
grouped = {}
for t in chosen:
    grouped.setdefault(t.date.year, []).append(t)

for each_year in sorted(grouped, reverse=True):
    of_year = sorted(grouped[each_year], key=lambda t: (t.date, t.row or 0), reverse=True)
    spent = sum(t.amount_minor for t in of_year if t.kind is spend.Kind.spent)
    with st.expander(
        f"{each_year} — {format_money(spent, currency)} spent · "
        f"{len(of_year)} transaction{'s' if len(of_year) != 1 else ''}",
        expanded=(len(grouped) == 1),
    ):
        transaction_table(of_year, scope=f"spend_{each_year}")
