"""Personal Ledger — who owes me what, in each currency separately."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from ledger.compute import (
    PERIODS,
    by_currency,
    by_person,
    filter_entries,
    ledger_breakdown,
    monthly_given,
    totals,
)
from ledger.models import Entry
from ledger.money import Currency, format_money
from ledger.ui import demo_banner, load_ledger, page_config

page_config("Personal Ledger")

result = load_ledger()
demo_banner(result)

st.title("Personal Ledger")
st.caption(f"As of {date.today():%d %b %Y}")


def render(entries: list[Entry], currency: Currency, key: str) -> None:
    """One currency's dashboard. Nothing here ever sees another currency."""
    money = lambda minor, decimals=True: format_money(minor, currency, decimals)  # noqa: E731

    if not entries:
        st.info(
            f"No {currency.label} entries yet. Add one from **Add Entry** "
            f"and pick {currency.value} as the currency."
        )
        return

    everyone = sorted({e.person for e in entries})

    left, middle, right = st.columns([2, 3, 1])
    with left:
        period = st.selectbox(
            "Period", list(PERIODS), index=list(PERIODS).index("Last 24 months"), key=f"{key}_period"
        )
    with middle:
        people = st.multiselect("People", everyone, default=everyone, key=f"{key}_people")

    shown = filter_entries(entries, period=period, people=people, currency=currency)
    summary = totals(shown, currency)

    with right:
        st.metric("Records", summary.records)

    st.divider()

    if not shown:
        st.info("No entries match these filters. Widen the period or add someone back.")
        return

    one, two, three, four = st.columns(4)
    one.metric("Total given", money(summary.given_minor))
    two.metric("Total received", money(summary.received_minor))
    three.metric(
        "Net outstanding",
        money(summary.net_minor),
        delta=f"{summary.open_ledgers} open ledger" + ("s" if summary.open_ledgers != 1 else ""),
        delta_color="off",
    )
    four.metric("People", summary.people)

    st.divider()

    table_col, chart_col = st.columns([1.35, 1], gap="large")

    with table_col:
        st.subheader("Who owes me what")
        rows = by_person(shown, currency)
        st.table(
            pd.DataFrame(
                [
                    {
                        "Person": r.person,
                        "Given": money(r.given_minor, False),
                        "Received": money(r.received_minor, False),
                        "Net owed": money(r.net_minor, False),
                        "Last activity": f"{r.last_activity:%d %b %y}",
                        "Ledgers": r.ledgers,
                    }
                    for r in rows
                ]
            ).set_index("Person")
        )
        st.caption("Positive net = they owe you. Negative = you owe them.")

        you_owe = [r for r in rows if r.net_minor < 0]
        if you_owe:
            st.caption("You owe: " + ", ".join(r.person for r in you_owe) + ".")

    with chart_col:
        st.subheader("Money given per month")
        series = monthly_given(shown)
        if not series:
            st.info("No money went out in this period.")
        else:
            frame = pd.DataFrame(series)
            frame["Month"] = pd.to_datetime(frame["month"] + "-01")
            frame["Amount"] = frame["amount_minor"] / 100
            frame = frame.rename(columns={"person": "Person"})

            chart = (
                alt.Chart(frame)
                .mark_bar()
                .encode(
                    x=alt.X("yearmonth(Month):T", title=None, axis=alt.Axis(format="%b %y")),
                    y=alt.Y(
                        "Amount:Q",
                        title=None,
                        axis=alt.Axis(format="~s", labelExpr=f"'{currency.symbol}' + datum.label"),
                    ),
                    color=alt.Color("Person:N", title=None, legend=alt.Legend(orient="top")),
                    xOffset=alt.XOffset("Person:N"),
                    tooltip=[
                        alt.Tooltip("yearmonth(Month):T", title="Month", format="%b %Y"),
                        "Person:N",
                        alt.Tooltip("Amount:Q", title=f"Given ({currency.value})", format=",.2f"),
                    ],
                )
                .properties(height=380)
            )
            st.altair_chart(chart, width="stretch")

            with st.expander("View as table"):
                st.dataframe(
                    frame.pivot_table(
                        index="month", columns="Person", values="amount_minor",
                        aggfunc="sum", fill_value=0,
                    ).map(lambda v: money(int(v))),
                    width="stretch",
                )

    st.divider()

    with st.expander("Every ledger, separately"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Person": r["person"],
                        "Ledger": r["ledger"],
                        "Given": money(r["given_minor"], False),
                        "Received": money(r["received_minor"], False),
                        "Net owed": money(r["net_minor"], False),
                        "Last activity": f"{r['last_activity']:%d %b %y}",
                        "Status": "open" if r["open"] else "settled",
                    }
                    for r in ledger_breakdown(shown)
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
                        "Amount": money(e.amount_minor, False),
                        "Note": e.note,
                    }
                    for e in sorted(shown, key=lambda x: x.date, reverse=True)
                ]
            ),
            hide_index=True,
            width="stretch",
        )


# Both currencies always get a tab, so the one you have not used yet is still
# discoverable. Totals are never combined across them.
tabs = st.tabs([f"{c.flag}  {c.label}" for c in Currency])
for tab, currency in zip(tabs, Currency):
    with tab:
        render(by_currency(result.entries, currency), currency, key=currency.value)
