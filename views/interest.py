"""Interest charged on money already lent — and money moved inside a group.

Kept apart from the ledger on purpose. Nothing on this page is added into
"who owes me what": the ledger says how much of your money is out there, this
says what it earned while it was, and merging them would inflate the first
with a figure nobody was ever handed.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger import interest, people, store
from ledger.compute import by_person
from ledger.money import Currency, format_money, spoken, to_minor
from ledger.ui import clear_cache, demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Interest")
st.caption(
    "Charged monthly on what someone still owes. **None of this is added to "
    "the ledger** — the ledger tracks the money you handed over, this tracks "
    "what it earned."
)

charges, charge_problems = interest.load()
members, member_problems = people.load()
parents = people.mapping(members)

for problem in charge_problems + member_problems:
    st.warning(problem)

if not result.entries:
    st.info("Nothing lent yet, so there is nothing to charge interest on.")
    st.stop()

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True,
        help="Rupee and dollar interest are kept apart, like everything else.",
    )
)

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

rate = st.slider(
    "Rate (% a month)", min_value=0.0, max_value=10.0,
    value=interest.DEFAULT_RATE, step=0.25,
    help="Used to suggest each charge. You can change any figure before saving.",
)

owed = [s for s in by_person(mine, currency) if s.net_minor > 0]

# ------------------------------------------------------------ what is charged
here = [c for c in charges if c.currency is currency]
total = interest.totals(here, currency)

if here:
    left, middle, right = st.columns(3)
    short = spoken(total, currency)
    left.metric(
        "Interest recorded",
        f"{currency.symbol}{short}" if short else format_money(total, currency),
        help="Never counted in the ledger's totals.",
    )
    middle.metric("Charges", f"{len(here)}")
    right.metric("People", f"{len({c.person for c in here})}")
    st.caption(f"Exactly: {format_money(total, currency)}")

st.divider()

# --------------------------------------------------------- charge this month
st.subheader("Charge a month")

if not owed:
    st.success("Nobody owes anything in this currency, so there is nothing to charge.")
else:
    when = st.date_input(
        "Month", value=date.today(), format="DD/MM/YYYY",
        help="Any day in the month you are charging for.",
    )

    for summary in owed:
        existing = interest.already_charged(here, summary.person, when, currency)
        suggested = interest.suggest(
            mine, summary.person, rate_percent=rate, currency=currency, on=when
        )
        group = people.group_of(summary.person, parents)
        under = f" · under **{group}**" if group != summary.person else ""

        with st.container(border=True):
            st.markdown(
                f"**{summary.person}**{under}  \n"
                f"owes {format_money(summary.net_minor, currency)} · "
                f"{rate:g}% of that is **{format_money(suggested, currency)}**"
            )
            if existing:
                st.caption(
                    f"Already charged {existing.money()} for "
                    f"{existing.month_label}. Remove it below to change it."
                )
                continue

            amount_col, note_col, save_col = st.columns([1.2, 2, 1])
            typed = amount_col.text_input(
                f"Amount ({currency.symbol})",
                value=f"{suggested / 100:.2f}",
                key=f"amt_{summary.person}_{currency.value}",
                label_visibility="collapsed",
            )
            note = note_col.text_input(
                "Note", value=f"{when:%b %Y} interest",
                key=f"note_{summary.person}_{currency.value}",
                label_visibility="collapsed",
            )
            try:
                minor = to_minor(typed) if typed.strip() else 0
            except ValueError:
                minor = 0

            if save_col.button(
                "Save", key=f"save_{summary.person}_{currency.value}",
                type="primary", width="stretch", disabled=minor <= 0,
            ):
                try:
                    interest.add(interest.Charge(
                        date=interest.month_start(when),
                        person=summary.person,
                        amount_minor=minor,
                        currency=currency,
                        rate_percent=rate,
                        note=note.strip(),
                        source="manual",
                    ))
                except Exception as exc:  # noqa: BLE001 — say what the sheet said
                    st.error(f"Could not save: {exc}")
                else:
                    st.success(
                        f"Charged {format_money(minor, currency)} to "
                        f"{summary.person} for {when:%b %Y}."
                    )
                    st.rerun()

st.divider()

# ------------------------------------------------------------ what is on file
st.subheader("Recorded interest")

if not here:
    st.caption("Nothing charged yet in this currency.")
else:
    years = interest.years(here)
    year = st.selectbox("Year", ["All years", *years])
    shown = here if year == "All years" else [c for c in here if c.date.year == int(year)]
    shown = sorted(shown, key=lambda c: (c.date, c.person), reverse=True)

    heads = st.columns([1.4, 2, 1.5, 2.4, 1.3], vertical_alignment="bottom")
    for head, label in zip(heads, ["Month", "Person", "Amount", "Note", ""]):
        if label:
            head.markdown(f'<div class="khata-head">{label}</div>', unsafe_allow_html=True)
    st.markdown('<hr class="khata-rule khata-rule-head">', unsafe_allow_html=True)

    for charge in shown:
        month, who, amount, note_cell, remove = st.columns(
            [1.4, 2, 1.5, 2.4, 1.3], vertical_alignment="center"
        )
        month.markdown(
            f'<div class="khata-cell">{charge.month_label}</div>', unsafe_allow_html=True
        )
        who.markdown(
            f'<div class="khata-cell">{charge.person}</div>', unsafe_allow_html=True
        )
        amount.markdown(
            f'<div class="khata-cell khata-amount khata-back">{charge.money()}</div>',
            unsafe_allow_html=True,
        )
        detail = charge.note or "—"
        if charge.rate_percent:
            detail += f' <span class="khata-src">at {charge.rate_percent:g}%</span>'
        note_cell.markdown(
            f'<div class="khata-cell khata-meta">{detail}</div>', unsafe_allow_html=True
        )
        armed = f"iarm_{charge.row}"
        with remove:
            if not st.session_state.get(armed):
                if st.button("Delete", key=f"idel_{charge.row}", width="stretch"):
                    st.session_state[armed] = True
                    st.rerun()
            else:
                yes, no = st.columns(2)
                if yes.button("Yes", key=f"iyes_{charge.row}", type="primary",
                              width="stretch"):
                    try:
                        interest.remove(charge)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Could not delete: {exc}")
                    st.session_state[armed] = False
                    st.rerun()
                if no.button("No", key=f"ino_{charge.row}", width="stretch"):
                    st.session_state[armed] = False
                    st.rerun()
        st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)

st.divider()

# ------------------------------------------------- money moved inside a group
st.subheader("Money moved inside a group")
st.caption(
    "When one of a group's people takes from what was already handed to the "
    "head — Chaitu out of Vihar's pot — this records it **in the main ledger**, "
    "because it changes who owes what. The group's total does not move: the "
    "money only went out of the house once."
)

heads_with_people = {
    head: names for head, names in
    people.groups(sorted({e.person for e in mine}), parents).items()
    if len(names) > 1
}

if not heads_with_people:
    st.info(
        "No groups set up yet. Put people under somebody on the **Edit entries** "
        "page, and this is where you record money moving between them."
    )
else:
    head_col, member_col = st.columns(2)
    head = head_col.selectbox("Group head", sorted(heads_with_people))
    takers = [p for p in heads_with_people[head] if p != head]
    member = member_col.selectbox("Who took it", takers) if takers else ""

    pot = next((s.net_minor for s in by_person(mine, currency) if s.person == head), 0)
    st.caption(f"**{head}** currently holds {format_money(max(pot, 0), currency)} of yours.")

    books = sorted({e.ledger for e in mine if e.person == head}) or ["Family"]
    book_col, amount_col = st.columns(2)
    book = book_col.selectbox("Ledger", books)
    moved_text = amount_col.text_input(f"Amount ({currency.symbol})", placeholder="10000")
    reason = st.text_input("Note", placeholder="why it moved")

    try:
        moved = to_minor(moved_text) if moved_text.strip() else 0
    except ValueError:
        moved = 0

    if moved > pot > 0:
        st.warning(
            f"That is more than the {format_money(pot, currency)} {head} still "
            "holds. Recording it anyway will put them in credit."
        )

    if st.button("Add to main ledger", type="primary",
                 disabled=not (member and moved > 0)):
        try:
            rows = people.transfer_entries(
                head, member, moved, currency, ledger=book, note=reason,
            )
            for row in rows:
                store.append(row)
        except Exception as exc:  # noqa: BLE001 — surface what the sheet said
            st.error(f"Could not record it: {exc}")
        else:
            clear_cache()
            st.success(
                f"Recorded in the ledger: **{head}** returned "
                f"{format_money(moved, currency)} and **{member}** took it. "
                f"The group still owes the same overall."
            )
            st.rerun()
