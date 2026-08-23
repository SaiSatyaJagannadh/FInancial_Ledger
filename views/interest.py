"""Interest, a month at a time.

One screen, one question: for this month, what does each person owe in
interest? Everybody who has ever appeared in the ledger gets a row, whether
they are being charged this month or not, so a blank is a decision you can see
rather than a name you forgot to add. Type over a figure to change it; set it
to zero and the charge goes away.

**Nothing here reaches the lending ledger.** The ledger says how much of your
money is out there; this says what it earned while it was. Once merged the two
cannot be told apart again, so they are never merged.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from ledger import interest, people as grouping, store
from ledger.compute import by_person
from ledger.money import Currency, compact, format_money, to_minor
from ledger.ui import clear_cache, demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Interest")
st.caption(
    "A month at a time. Everyone gets a row; type a figure to charge them, "
    "leave it at zero to charge nothing. **None of this touches the ledger.**"
)

charges, charge_problems = interest.load()
members, member_problems = grouping.load()
parents = grouping.mapping(members)
for problem in charge_problems + member_problems:
    st.warning(problem)

if not result.entries:
    st.info("Nothing lent yet, so there is nothing to charge interest on.")
    st.stop()

pick_currency, pick_month = st.columns([2, 1.4])
with pick_currency:
    currency = Currency(
        st.radio(
            "Currency",
            [c.value for c in Currency],
            format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
            horizontal=True,
        )
    )
with pick_month:
    options = interest.months_back(24)
    month = st.selectbox(
        "Month", options, format_func=lambda d: f"{d:%B %Y}",
        help="Interest is recorded once per person per month.",
    )

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

# Everyone in this currency, not only those currently in debt: somebody who
# has settled may still owe last month's interest, and a name that vanishes
# from the list is a name you cannot correct.
everyone = sorted({e.person for e in mine})
owed = {s.person: s.net_minor for s in by_person(mine, currency)}
existing = interest.for_month(charges, month, currency)

st.divider()

# ------------------------------------------------------------------ the grid
st.subheader(f"{month:%B %Y}")

frame = pd.DataFrame([
    {
        "Person": person,
        "Group": grouping.group_of(person, parents),
        "Still owed": owed.get(person, 0) / 100,
        "Interest": (existing[person].amount_minor / 100) if person in existing else 0.0,
        "Note": existing[person].note if person in existing else "",
    }
    for person in everyone
])

edited = st.data_editor(
    frame,
    hide_index=True,
    width="stretch",
    key=f"grid_{currency.value}_{month:%Y%m}",
    disabled=["Person", "Group", "Still owed"],
    column_config={
        "Person": st.column_config.TextColumn(width="medium"),
        "Group": st.column_config.TextColumn(
            help="Which arrangement this person rolls up to", width="small"
        ),
        "Still owed": st.column_config.NumberColumn(
            f"Still owed ({currency.symbol})", format="%.2f", disabled=True,
            help="From the ledger, for reference. Interest is never added to it.",
        ),
        "Interest": st.column_config.NumberColumn(
            f"Interest ({currency.symbol})", format="%.2f", min_value=0.0,
            help="Type the figure for this month. Zero means no charge.",
        ),
        "Note": st.column_config.TextColumn(width="medium"),
    },
)

changes: list[tuple[str, int, str]] = []
for _, row in edited.iterrows():
    person = str(row["Person"])
    try:
        typed = to_minor(f"{float(row['Interest'] or 0):.2f}")
    except (ValueError, TypeError):
        continue
    note = str(row["Note"] or "").strip()
    was = existing.get(person)
    before = was.amount_minor if was else 0
    if typed != before or (was is not None and note != was.note):
        changes.append((person, typed, note))

total_typed = sum(int(round(float(r["Interest"] or 0) * 100)) for _, r in edited.iterrows())

left, right = st.columns([3, 1])
with left:
    if changes:
        st.caption(
            f"**{len(changes)}** change{'s' if len(changes) != 1 else ''} not saved yet: "
            + ", ".join(
                f"{p} → {format_money(a, currency)}" if a else f"{p} → cleared"
                for p, a, _ in changes[:4]
            )
            + (" …" if len(changes) > 4 else "")
        )
    else:
        st.caption("Nothing changed yet.")
with right:
    if st.button("Save this month", type="primary", width="stretch",
                 disabled=not changes):
        done, failed = [], []
        for person, amount, note in changes:
            try:
                what = interest.set_for_month(
                    person, month, amount, currency=currency, note=note,
                )
            except Exception as exc:  # noqa: BLE001 — say what the sheet said
                failed.append(f"{person}: {exc}")
            else:
                if what != "unchanged":
                    done.append(person)
        if failed:
            for message in failed:
                st.error(message)
        if done:
            st.success(
                f"Saved {month:%B %Y} for " + ", ".join(done) + "."
            )
            st.rerun()

st.caption(
    f"This month totals **{format_money(total_typed, currency)}** across "
    f"{sum(1 for _, r in edited.iterrows() if float(r['Interest'] or 0) > 0)} "
    "people — and is not part of any ledger figure."
)

st.divider()

# --------------------------------------------------------------- the history
here = [c for c in charges if c.currency is currency]
if not here:
    st.caption("No interest recorded yet in this currency.")
    st.stop()

total = interest.totals(here, currency)
one, two, three = st.columns(3)
short = compact(total, currency)
one.metric(
    "Interest recorded",
    f"{currency.symbol}{short}" if short else format_money(total, currency),
    help="Across every month. Never counted in the ledger's totals.",
)
two.metric("Months", f"{len({c.month for c in here})}")
three.metric("People charged", f"{len({c.person for c in here})}")
st.caption(f"Exactly: {format_money(total, currency)}")

st.subheader("Every month, every person")

months = sorted({c.month for c in here}, reverse=True)
#: "2026-08" sorts correctly but reads as a sort key; the heading says "Aug 2026".
headings = {
    key: next(c.month_label for c in here if c.month == key) for key in months
}
grid = pd.DataFrame(
    [
        {
            "Person": person,
            **{
                headings[key]: next(
                    (c.amount_minor / 100 for c in here
                     if c.person == person and c.month == key), 0.0
                )
                for key in months
            },
            "Total": sum(c.amount_minor for c in here if c.person == person) / 100,
        }
        for person in sorted({c.person for c in here})
    ]
)
st.dataframe(
    grid, hide_index=True, width="stretch",
    column_config={
        column: st.column_config.NumberColumn(format="%.2f")
        for column in [*headings.values(), "Total"]
    },
)
st.caption(
    f"Amounts in {currency.value}. Columns are months; a zero means nothing "
    "was charged that month."
)
