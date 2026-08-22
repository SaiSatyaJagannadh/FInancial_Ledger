"""Personal Ledger — who owes me what, in each currency separately."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from ledger.compute import (
    ALL_TIME,
    PERIODS,
    by_currency,
    by_person,
    filter_entries,
    ledger_breakdown,
    monthly_given,
    totals,
)
from ledger.models import Entry
from ledger.money import Currency, compact, format_money
from ledger.ui import demo_banner, entry_table, load_ledger, styles

styles()

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
        # All time, not a recent window: an unsettled debt from 2020 is still
        # owed, and a default that hides it reads as "nobody owes you anything".
        period = st.selectbox(
            "Period", list(PERIODS), index=list(PERIODS).index(ALL_TIME), key=f"{key}_period"
        )
    with middle:
        people = st.multiselect("People", everyone, default=everyone, key=f"{key}_people")

    shown = filter_entries(entries, period=period, people=people, currency=currency)
    summary = totals(shown, currency)

    with right:
        st.metric("Records", summary.records)

    st.divider()

    if not shown:
        # Say what is being hidden. "No entries" alone reads as "you have none",
        # which is a lie when a filter is what emptied the view.
        hidden = len(filter_entries(entries, currency=currency))
        st.info(
            f"No entries match these filters — but you have **{hidden}** "
            f"{currency.label} entr{'y' if hidden == 1 else 'ies'} outside them. "
            "Widen the period or add someone back."
        )
        return

    def headline(amount: int) -> str:
        """Lakhs when the figure is long, because a metric that reads
        "₹60,28,00…" tells you nothing. The exact number goes underneath."""
        short = compact(amount, currency)
        return f"{currency.symbol}{short}" if short else money(amount)

    one, two, three, four = st.columns(4)
    one.metric("Total given", headline(summary.given_minor))
    two.metric("Total received", headline(summary.received_minor))
    three.metric(
        "Net outstanding",
        headline(summary.net_minor),
        delta=f"{summary.open_ledgers} open ledger" + ("s" if summary.open_ledgers != 1 else ""),
        delta_color="off",
    )
    four.metric("People", summary.people)

    for slot, amount in (
        (one, summary.given_minor),
        (two, summary.received_minor),
        (three, summary.net_minor),
    ):
        if compact(amount, currency):
            slot.caption(money(amount))

    st.divider()

    table_col, chart_col = st.columns([1.35, 1], gap="large")

    with table_col:
        st.subheader("Who owes me what")
        rows = by_person(shown, currency)

        st.dataframe(
            pd.DataFrame([
                {
                    "Person": r.person,
                    "Given": r.given_minor / 100,
                    "Received": r.received_minor / 100,
                    "Net owed": r.net_minor / 100,
                    "In short": compact(r.net_minor, currency) or "—",
                    "Entries": sum(1 for e in shown if e.person == r.person),
                    "Last activity": r.last_activity,
                }
                for r in rows
            ]),
            hide_index=True,
            width="stretch",
            column_config={
                "Given": st.column_config.NumberColumn(format="%.2f"),
                "Received": st.column_config.NumberColumn(format="%.2f"),
                "Net owed": st.column_config.NumberColumn(
                    format="%.2f", help="Positive = they owe you. Negative = you owe them."
                ),
                "Last activity": st.column_config.DateColumn(format="DD MMM YYYY"),
            },
        )
        st.caption(f"Amounts in {currency.value}. Positive net = they owe you.")

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
                        axis=alt.Axis(
                            labelExpr=(
                                f"'{currency.symbol}' + "
                                "(datum.value >= 10000000 "
                                "? format(datum.value / 10000000, '~r') + ' cr' "
                                ": datum.value >= 100000 "
                                "? format(datum.value / 100000, '~r') + ' L' "
                                ": format(datum.value, '~s'))"
                            )
                            if currency.value == "INR"
                            else alt.Undefined,
                            format="~s" if currency.value != "INR" else alt.Undefined,
                        ),
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

    st.markdown("**Open a person to see every entry**")
    for r in rows:
        theirs = sorted(
            [e for e in shown if e.person == r.person],
            key=lambda e: (e.date, e.row or 0), reverse=True,
        )
        short = compact(r.net_minor, currency)
        with st.expander(
            f"{r.person} — {money(r.net_minor)}"
            + (f" ({short})" if short else "")
            + f" · {len(theirs)} entr{'y' if len(theirs) == 1 else 'ies'}"
        ):
            entry_table(theirs, scope=f"{key}_{r.person}")

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

    with st.expander(f"All {len(shown)} entries", expanded=False):
        st.caption("Newest first. Delete asks twice — the sheet row goes for good.")
        entry_table(
            sorted(shown, key=lambda x: (x.date, x.row or 0), reverse=True),
            scope=key,
        )


# Both currencies always get a tab, so the one you have not used yet is still
# discoverable. Totals are never combined across them.
tabs = st.tabs([f"{c.flag}  {c.label}" for c in Currency])
for tab, currency in zip(tabs, Currency):
    with tab:
        render(by_currency(result.entries, currency), currency, key=currency.value)
