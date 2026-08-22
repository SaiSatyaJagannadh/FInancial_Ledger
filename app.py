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
from ledger import store
from ledger.models import Entry
from ledger.money import Currency, format_money
from ledger.ui import clear_cache, demo_banner, load_ledger, page_config

page_config("Personal Ledger")

result = load_ledger()
demo_banner(result)

st.title("Personal Ledger")
st.caption(f"As of {date.today():%d %b %Y}")


def _delete_control(entry: Entry, key: str) -> None:
    """Two-click delete. The first click only arms it; the second does the work.

    A single-click delete next to a list of real debts is an accident waiting to
    happen, and the sheet has no undo of its own.
    """
    if entry.row is None:
        return
    armed = f"arm_{key}_{entry.row}"

    if not st.session_state.get(armed):
        if st.button("🗑", key=f"del_{key}_{entry.row}", help="Delete this entry"):
            st.session_state[armed] = True
            st.rerun()
        return

    confirm, cancel = st.columns(2)
    if confirm.button("Yes", key=f"yes_{key}_{entry.row}", type="primary"):
        try:
            store.delete(entry)
        except Exception as exc:  # noqa: BLE001 — show whatever the sheet said
            st.error(f"Could not delete: {exc}")
            st.session_state[armed] = False
        else:
            clear_cache()
            st.session_state[armed] = False
            st.toast(f"Deleted {entry.person} · {money_plain(entry)}")
            st.rerun()
    if cancel.button("No", key=f"no_{key}_{entry.row}"):
        st.session_state[armed] = False
        st.rerun()


def money_plain(entry: Entry) -> str:
    return format_money(entry.amount_minor, entry.currency)


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
        st.caption("Newest first. Delete asks twice — the sheet row goes for good.")
        for e in sorted(shown, key=lambda x: x.date, reverse=True):
            detail, link, remove = st.columns([6, 1.4, 1.4])
            with detail:
                arrow = "→ out" if e.signed_minor > 0 else "← back"
                st.markdown(
                    f"**{money(e.amount_minor)}** {arrow} · **{e.person}** · {e.ledger}  \n"
                    f"<span style='opacity:.65'>{e.date:%d %b %Y}"
                    + (f" · {e.note}" if e.note else "")
                    + "</span>",
                    unsafe_allow_html=True,
                )
            with link:
                if e.attachment:
                    st.link_button("📎 Statement", e.attachment, width="stretch")
            with remove:
                _delete_control(e, key)
            st.divider()

        if not shown:
            st.caption("Nothing to show.")


# Both currencies always get a tab, so the one you have not used yet is still
# discoverable. Totals are never combined across them.
tabs = st.tabs([f"{c.flag}  {c.label}" for c in Currency])
for tab, currency in zip(tabs, Currency):
    with tab:
        render(by_currency(result.entries, currency), currency, key=currency.value)
