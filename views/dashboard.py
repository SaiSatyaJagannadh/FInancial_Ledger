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
    by_group,
    by_person,
    filter_entries,
    ledger_breakdown,
    monthly_given,
    totals,
)
from ledger import interest, people as grouping, settle, store
from ledger.models import Entry
from ledger.money import Currency, compact, format_money
from ledger.ui import clear_cache, demo_banner, entry_table, load_ledger, ruled_table, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Personal Ledger")
st.caption(f"As of {date.today():%d %b %Y}")


def _settle_control(person: str, entries: list[Entry], currency: Currency, key: str) -> None:
    """Clear a person's balance by recording the repayment, not by deleting it.

    Erasing the loan would lose the fact it ever happened. Writing the
    repayment brings the net to zero and keeps the history, which is what a
    ledger is for.
    """
    owed = settle.outstanding(entries, person, currency)
    if owed <= 0:
        st.caption("Settled, nothing outstanding.")
        return

    armed = f"settle_{key}_{person}"
    books = settle.open_ledgers(entries, person, currency)

    if not st.session_state.get(armed):
        left, right = st.columns([3, 1.4])
        left.caption(
            f"Outstanding: **{format_money(owed, currency)}** across "
            f"{len(books)} ledger{'s' if len(books) != 1 else ''}."
        )
        if right.button("Mark settled", key=f"sb_{key}_{person}", width="stretch",
                        help="Record that this has been paid back in full"):
            st.session_state[armed] = True
            st.rerun()
        return

    st.warning(
        f"Record **{format_money(owed, currency)}** as repaid by **{person}**? "
        "This writes a received entry for each open ledger ("
        + ", ".join(f"{name} {format_money(amount, currency)}" for name, amount in books)
        + "), so the balance becomes zero. Nothing is deleted."
    )
    yes, no = st.columns(2)
    if yes.button("Yes, settle", key=f"sy_{key}_{person}", type="primary", width="stretch"):
        made = settle.balancing_entries(entries, person, currency)
        try:
            for one in made:
                store.append(one)
        except Exception as exc:  # noqa: BLE001 — surface whatever the sheet said
            st.error(f"Could not settle: {exc}")
        else:
            clear_cache()
            st.session_state[armed] = False
            st.toast(f"{person} settled")
            st.rerun()
    if no.button("Cancel", key=f"sn_{key}_{person}", width="stretch"):
        st.session_state[armed] = False
        st.rerun()


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

    # Grouped people are offered by their group, not one by one: picking Vihar
    # has to bring Chaitu and Sirisha with him, because the arrangement is with
    # Vihar and their separate rows are an implementation detail of it.
    parents = grouping.mapping(grouping.load()[0])
    families = grouping.groups(everyone, parents)
    heads = sorted(families)

    def label(head: str) -> str:
        others = [n for n in families[head] if n != head]
        return f"{head}  (+{len(others)})" if others else head

    left, right = st.columns([2, 3])
    with left:
        # All time, not a recent window: an unsettled debt from 2020 is still
        # owed, and a default that hides it reads as "nobody owes you anything".
        period = st.selectbox(
            "Period", list(PERIODS), index=list(PERIODS).index(ALL_TIME), key=f"{key}_period"
        )
    with right:
        # Nothing preselected: an empty filter already means everyone, and
        # pre-filling it with every name made the box unreadable and gave no
        # hint that it was a filter at all.
        picked = st.multiselect(
            "People", heads, format_func=label, key=f"{key}_people",
            placeholder="Everyone",
            help="Leave empty for everyone. Picking a group includes its people.",
        )

    # A group expands to its members before filtering; empty still means all.
    chosen = sorted({name for head in picked for name in families[head]})
    shown = filter_entries(entries, period=period, people=chosen, currency=currency)
    summary = totals(shown, currency)

    if not shown:
        # Say what is being hidden. "No entries" alone reads as "you have none",
        # which is a lie when a filter is what emptied the view.
        hidden = len(filter_entries(entries, currency=currency))
        st.info(
            f"No entries match these filters, but you have **{hidden}** "
            f"{currency.label} entr{'y' if hidden == 1 else 'ies'} outside them. "
            "Widen the period or add someone back."
        )
        return

    def headline(amount: int) -> str:
        """Lakhs when the figure is long, because a metric that reads
        "₹60,28,00…" tells you nothing. The exact number goes underneath."""
        short = compact(amount, currency)
        return f"{currency.symbol}{short}" if short else money(amount)

    # What is still out there is the figure this page exists for, so it leads and
    # the rest stand beside it rather than four tiles of equal weight.
    with st.container(key=f"khata-figures-{key}"):
        net, gave, back, folk, records = st.columns([1.8, 1.1, 1.1, 0.6, 0.7])
    net.metric(
        "Net outstanding",
        headline(summary.net_minor),
        delta=f"{summary.open_ledgers} open ledger" + ("s" if summary.open_ledgers != 1 else ""),
        delta_color="off",
        delta_arrow="off",
    )
    gave.metric("Total given", headline(summary.given_minor))
    back.metric("Total received", headline(summary.received_minor))
    folk.metric("People", summary.people)
    records.metric("Records", summary.records)

    for slot, amount in (
        (net, summary.net_minor),
        (gave, summary.given_minor),
        (back, summary.received_minor),
    ):
        if compact(amount, currency):
            slot.caption(money(amount))

    # Interest, said out loud and kept out of every figure above it. Leaving it
    # off the dashboard entirely meant the only way to see it was to go looking
    # on another page; adding it to the totals would be a lie.
    _charges = [c for c in interest.load()[0] if c.currency is currency]
    if _charges:
        _total = interest.totals(_charges, currency)
        _split = interest.split_by_kind(_charges, currency)
        st.caption(
            f"**Interest: {money(_total)}** "
            f"({money(_split[interest.Kind.due])} still due, "
            f"{money(_split[interest.Kind.given])} given). Recorded on the "
            "Interest page and **not** included in any figure above."
        )

    grouped_rows = by_group(shown, currency, parents)
    any_group = any(g.grouped for g in grouped_rows)
    st.subheader("Who owes me what" if not any_group else "Who owes me what, by group")
    rows = by_person(shown, currency)

    # One line per arrangement when groups exist, one per person when they
    # do not: the same table either way, so nothing new to learn.
    display = grouped_rows if any_group else [
        type("Row", (), {
            "head": r.person, "people": [r.person], "given_minor": r.given_minor,
            "received_minor": r.received_minor, "net_minor": r.net_minor,
            "last_activity": r.last_activity, "grouped": False,
        })() for r in rows
    ]

    ruled_table(
        [
            {
                "Person": g.head,
                # The members get their own column rather than being glued onto
                # the name: a long "Vihar (with A, B, C)" pushed every figure off
                # the side of the table.
                **({"With": ", ".join(n for n in g.people if n != g.head)} if any_group else {}),
                "Given": money(g.given_minor),
                "Received": money(g.received_minor),
                "Net owed": (
                    money(g.net_minor),
                    "out" if g.net_minor > 0 else "back" if g.net_minor < 0 else "",
                ),
                "In short": compact(g.net_minor, currency),
                "Entries": sum(1 for e in shown if e.person in g.people),
                "Last activity": f"{g.last_activity:%d %b %Y}",
            }
            for g in display
        ],
        numeric=("Given", "Received", "Net owed", "In short", "Entries"),
        lead="Net owed",
    )
    st.caption(
        f"Amounts in {currency.value}. Positive net = they owe you. Negative = you owe them."
        + ("  Grouped people count as one arrangement. Open one below to "
           "see each person." if any_group else "")
    )
    you_owe = [r for r in rows if r.net_minor < 0]
    if you_owe:
        st.caption("You owe: " + ", ".join(r.person for r in you_owe) + ".")

    st.markdown(
        "**Open a group to see every entry**" if any_group
        else "**Open a person to see every entry**"
    )
    with st.container(key=f"khata-list-{key}"):
        for g in display:
            theirs = sorted(
                [e for e in shown if e.person in g.people],
                key=lambda e: (e.date, e.row or 0), reverse=True,
            )
            short = compact(g.net_minor, currency)
            title = g.head + (f" + {len(g.people) - 1}" if g.grouped else "")
            with st.expander(
                f"{title}: {money(g.net_minor)}"
                + (f" ({short})" if short else "")
                + f", {len(theirs)} entr{'y' if len(theirs) == 1 else 'ies'}"
            ):
                if g.grouped:
                    # Each person's own balance inside the arrangement. The group
                    # total is what you chase; this is who actually holds it.
                    st.caption("Inside this group:")
                    inside = [r for r in rows if r.person in g.people]
                    for r in sorted(inside, key=lambda r: -r.net_minor):
                        st.markdown(
                            f"- **{r.person}**: {money(r.net_minor)}"
                            + (" *(the group head)*" if r.person == g.head else "")
                        )
                    st.divider()
                for person in ([g.head] if not g.grouped else sorted(g.people)):
                    if g.grouped:
                        st.markdown(f"**{person}**")
                    _settle_control(person, entries, currency, key)
                entry_table(theirs, scope=f"{key}_{g.head}")

        with st.expander("Every ledger, separately"):
            ruled_table(
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
                ],
                numeric=("Given", "Received", "Net owed"),
                lead="Net owed",
            )

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
            .mark_bar(cornerRadiusEnd=4)
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
            .properties(height=280)
        )
        st.altair_chart(chart, width="stretch")

        with st.expander("View as table"):
            pivot = frame.pivot_table(
                index="month", columns="Person", values="amount_minor",
                aggfunc="sum", fill_value=0,
            )
            ruled_table(
                [
                    {"Month": month, **{name: money(int(v)) for name, v in row.items()}}
                    for month, row in pivot.iterrows()
                ],
                numeric=tuple(pivot.columns),
            )

    # Open from the start and last on the page: the rows are the ledger, and a
    # long list belongs where it pushes nothing else out of reach.
    st.subheader(f"All {len(shown)} entries")
    st.caption("Newest first. Delete asks twice, because the sheet row goes for good.")
    entry_table(
        sorted(shown, key=lambda x: (x.date, x.row or 0), reverse=True),
        scope=key,
    )


# Both currencies always get a tab, so the one you have not used yet is still
# discoverable. Totals are never combined across them.
tabs = st.tabs([f"{c.symbol}  {c.label}" for c in Currency])
for tab, currency in zip(tabs, Currency):
    with tab:
        render(by_currency(result.entries, currency), currency, key=currency.value)
