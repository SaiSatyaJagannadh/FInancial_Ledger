"""What the outstanding money would have earned in an FD instead.

The comparison only ever covers what is *still owed*. Money that came back is
not an opportunity cost — you have it.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ledger.compute import by_person
from ledger.invest import DEFAULT_FREQUENCY, FREQUENCIES, what_if
from ledger.money import Currency, format_money
from ledger.ui import demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Invested Instead")
st.caption("If the money still owed to you had gone into a deposit instead, what would it be worth now?")

rate_col, freq_col = st.columns([2, 2])
with rate_col:
    rate = st.slider(
        "Interest rate (% a year)", min_value=1.0, max_value=15.0, value=7.0, step=0.25,
        help="Indian bank FDs sit around 6–7.5%. Set it to whatever you'd actually have got.",
    )
with freq_col:
    frequency = st.selectbox(
        "Compounding", list(FREQUENCIES),
        index=list(FREQUENCIES).index(DEFAULT_FREQUENCY),
    )
periods = FREQUENCIES[frequency]

for currency in Currency:
    entries = [e for e in result.entries if e.currency is currency]
    if not entries:
        continue

    st.divider()
    st.subheader(f"{currency.flag}  {currency.label}")

    total = what_if(entries, rate_percent=rate, periods_per_year=periods)
    if total.principal_minor <= 0:
        st.success("Nothing outstanding here — it has all come back.")
        continue

    one, two, three = st.columns(3)
    one.metric("Still owed", format_money(total.principal_minor, currency))
    two.metric("Would be worth", format_money(total.value_minor, currency))
    three.metric(
        "Interest forgone",
        format_money(total.interest_minor, currency),
        delta=f"{total.interest_minor / total.principal_minor:.1%}"
        if total.principal_minor else None,
    )
    st.caption(
        f"Oldest outstanding amount has been out for {total.days:,} days "
        f"({total.days / 365:.1f} years), at {rate:g}% {frequency.lower()}."
    )

    rows = []
    for summary in by_person(entries, currency):
        owed = summary.net_minor
        if owed <= 0:
            continue
        person_entries = [e for e in entries if e.person == summary.person]
        growth = what_if(person_entries, rate_percent=rate, periods_per_year=periods)
        rows.append({
            "Person": summary.person,
            "Still owed": growth.principal_minor / 100,
            "Interest forgone": growth.interest_minor / 100,
            "Would be worth": growth.value_minor / 100,
        })

    if not rows:
        continue

    frame = pd.DataFrame(rows).sort_values("Would be worth", ascending=False)
    melted = frame.melt(
        id_vars="Person",
        value_vars=["Still owed", "Interest forgone"],
        var_name="Part", value_name="Amount",
    )
    st.altair_chart(
        alt.Chart(melted)
        .mark_bar()
        .encode(
            x=alt.X("Amount:Q", title=f"Amount ({currency.symbol})", stack="zero"),
            y=alt.Y("Person:N", sort="-x", title=None),
            color=alt.Color(
                "Part:N",
                scale=alt.Scale(
                    domain=["Still owed", "Interest forgone"],
                    range=["#4c78a8", "#54a24b"],
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["Person", "Part", alt.Tooltip("Amount:Q", format=",.2f")],
        )
        .properties(height=max(120, 42 * len(frame))),
        use_container_width=True,
    )

    st.dataframe(
        frame.style.format({
            "Still owed": "{:,.2f}",
            "Interest forgone": "{:,.2f}",
            "Would be worth": "{:,.2f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.caption(
    "Each amount compounds from the day it went out, not from the ledger's first "
    "entry. Repayments are applied oldest-first, so what is still counted as "
    "outstanding is the most recent money — the conservative reading."
)
