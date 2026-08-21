"""Personal Ledger — who owes me what."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from ledger import store
from ledger.compute import ALL_TIME, PERIODS, by_person, filter_entries, monthly_given, totals
from ledger.money import format_inr
from ledger.ui import demo_banner, load_ledger, page_config

page_config("Personal Ledger")

result = load_ledger()
demo_banner(result)

st.title("Personal Ledger")
st.caption(f"As of {date.today():%d %b %Y}")

# --------------------------------------------------------------------- filters
everyone = sorted({e.person for e in result.entries})

left, middle, right = st.columns([2, 3, 1])
with left:
    period = st.selectbox("Period", list(PERIODS), index=list(PERIODS).index("Last 24 months"))
with middle:
    people = st.multiselect("People", everyone, default=everyone)
with right:
    # Placeholder so the metric lines up with the inputs beside it.
    st.write("")

entries = filter_entries(result.entries, period=period, people=people)
summary = totals(entries)

with right:
    st.metric("Records", summary.records)

st.divider()

if not entries:
    st.info("No entries match these filters. Widen the period or add someone back.")
    st.stop()

# -------------------------------------------------------------------- headline
one, two, three, four = st.columns(4)
one.metric("Total given", format_inr(summary.given_paise))
two.metric("Total received", format_inr(summary.received_paise))
three.metric(
    "Net outstanding",
    format_inr(summary.net_paise),
    delta=f"{summary.open_ledgers} open ledger" + ("s" if summary.open_ledgers != 1 else ""),
    delta_color="off",
)
four.metric("People", summary.people)

st.divider()

# ------------------------------------------------------------------- the tables
table_col, chart_col = st.columns([1.35, 1], gap="large")

with table_col:
    st.subheader("Who owes me what")
    rows = by_person(entries)
    st.table(
        pd.DataFrame(
            [
                {
                    "Person": r.person,
                    "Given": format_inr(r.given_paise, decimals=False),
                    "Received": format_inr(r.received_paise, decimals=False),
                    "Net owed": format_inr(r.net_paise, decimals=False),
                    "Last activity": f"{r.last_activity:%d %b %y}",
                    "Ledgers": r.ledgers,
                }
                for r in rows
            ]
        ).set_index("Person")
    )
    st.caption("Positive net = they owe you. Negative = you owe them.")

    owed_to_you = [r for r in rows if r.net_paise < 0]
    if owed_to_you:
        names = ", ".join(r.person for r in owed_to_you)
        st.caption(f"You owe: {names}.")

with chart_col:
    st.subheader("Money given per month")
    series = monthly_given(entries)
    if not series:
        st.info("No money went out in this period.")
    else:
        frame = pd.DataFrame(series)
        frame["Month"] = pd.to_datetime(frame["month"] + "-01")
        frame["Amount"] = frame["amount_paise"] / 100
        frame = frame.rename(columns={"person": "Person"})

        chart = (
            alt.Chart(frame)
            .mark_bar()
            .encode(
                x=alt.X("yearmonth(Month):T", title=None, axis=alt.Axis(format="%b %y")),
                y=alt.Y("Amount:Q", title=None, axis=alt.Axis(format="~s")),
                color=alt.Color("Person:N", title=None, legend=alt.Legend(orient="top")),
                xOffset=alt.XOffset("Person:N"),
                tooltip=[
                    alt.Tooltip("yearmonth(Month):T", title="Month", format="%b %Y"),
                    "Person:N",
                    alt.Tooltip("Amount:Q", title="Given", format=",.2f"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(chart, width="stretch")

        with st.expander("View as table"):
            pivot = (
                frame.pivot_table(
                    index="month", columns="Person", values="amount_paise",
                    aggfunc="sum", fill_value=0,
                )
                .map(format_inr)
            )
            st.dataframe(pivot, width="stretch")

# ------------------------------------------------------------------ the detail
st.divider()
with st.expander("Every ledger, separately"):
    from ledger.compute import ledger_breakdown

    detail = ledger_breakdown(entries)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Person": r["person"],
                    "Ledger": r["ledger"],
                    "Given": format_inr(r["given_paise"], decimals=False),
                    "Received": format_inr(r["received_paise"], decimals=False),
                    "Net owed": format_inr(r["net_paise"], decimals=False),
                    "Last activity": f"{r['last_activity']:%d %b %y}",
                    "Status": "open" if r["open"] else "settled",
                }
                for r in detail
            ]
        ),
        hide_index=True,
        width="stretch",
    )

with st.expander("All entries"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Date": f"{e.date:%d %b %y}",
                    "Person": e.person,
                    "Ledger": e.ledger,
                    "Direction": e.direction.value,
                    "Amount": format_inr(e.amount_paise, decimals=False),
                    "Note": e.note,
                }
                for e in sorted(entries, key=lambda x: x.date, reverse=True)
            ]
        ),
        hide_index=True,
        width="stretch",
    )
