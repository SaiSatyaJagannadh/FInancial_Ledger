"""Find an entry and change it, or remove it.

Separate from Add entry on purpose. Adding is a quick, repetitive act — type,
save, type the next. Correcting is a slower one that starts with finding the
right row. Putting a long list under the form made the common case scroll past
something it never needed.
"""

from __future__ import annotations

import streamlit as st

from ledger import people as grouping
from ledger import store
from ledger.compute import by_person as _by_person
from ledger.money import Currency, compact, format_money, to_minor
from ledger.ui import clear_cache, demo_banner, entry_table, load_ledger, styles

ANYONE = "Everyone"
ANY_YEAR = "All years"
ANY_LEDGER = "All ledgers"

styles()

result = load_ledger()
demo_banner(result)

st.title("Edit entries")
st.caption("Find the row, then change or remove it. Deleting asks twice — the sheet row goes for good.")

if not result.entries:
    st.info("Nothing recorded yet. Add an entry first.")
    st.stop()

# Grouping sits above the entry list on purpose: it is a setting you come
# here to change, and it was unreachable below two hundred rows.
with st.expander("👪  Group people together", expanded=False):
    # A grouping is a fact about a person, not about a row, so it is set here
    # once rather than on each of their entries. Everything that rolls up follows.
    st.caption(
        "When several people borrow under one arrangement — Chaitu and Sirisha "
        "under Vihar — put them under that person here. Their entries stay their "
        "own; only the totals roll up."
    )

    everyone = sorted({e.person for e in result.entries})
    members, member_problems = grouping.load()
    for problem in member_problems:
        st.warning(problem)
    parents = grouping.mapping(members)

    NOBODY = "— on their own —"

    who_col, under_col, set_col = st.columns([2, 2, 1])
    target = who_col.selectbox("Person", everyone, key="group_person")
    options = [NOBODY, *[p for p in everyone if p != target]]
    current = parents.get(target, "")
    under = under_col.selectbox(
        "Comes under", options,
        index=options.index(current) if current in options else 0,
        key="group_parent",
    )
    with set_col:
        st.write("")
        if st.button("Save group", width="stretch"):
            try:
                grouping.set_parent(target, "" if under == NOBODY else under)
            except Exception as exc:  # noqa: BLE001 — say what went wrong
                st.error(str(exc))
            else:
                st.success(
                    f"{target} is now on their own." if under == NOBODY
                    else f"{target} now comes under {under}."
                )
                st.rerun()

    grouped = {
        head: names
        for head, names in grouping.groups(everyone, parents).items()
        if len(names) > 1
    }
    if grouped:
        for head, names in grouped.items():
            others = [n for n in names if n != head]
            st.markdown(f"- **{head}** — with {', '.join(others)}")
    else:
        st.caption("Nobody is grouped yet.")

# Writing to the *ledger*, not to a group setting — but it is a thing you do
# to a group, so it lives beside the grouping editor rather than on the
# Interest page, which no longer touches the ledger at all.
with st.expander("↔️  Money moved inside a group", expanded=False):
    st.caption(
        "When one of a group's people takes from what was already handed to "
        "the head — Chaitu out of Vihar's pot — record it here. Two entries "
        "are written so the group still owes the same overall: the head is "
        "shown as having returned it, and the person who took it as having "
        "taken it."
    )

    _families = {
        head: names
        for head, names in grouping.groups(
            sorted({e.person for e in result.entries}), parents
        ).items()
        if len(names) > 1
    }
    if not _families:
        st.info("No groups yet. Put somebody under another person above first.")
    else:
        _cur = Currency(
            st.radio(
                "Currency", [c.value for c in Currency],
                format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
                horizontal=True, key="move_currency",
            )
        )
        _in_cur = [e for e in result.entries if e.currency is _cur]
        _head_col, _member_col = st.columns(2)
        _head = _head_col.selectbox("Group head", sorted(_families), key="move_head")
        _takers = [p for p in _families[_head] if p != _head]
        _member = _member_col.selectbox("Who took it", _takers, key="move_member")

        _pot = next(
            (s.net_minor for s in _by_person(_in_cur, _cur) if s.person == _head), 0
        )
        st.caption(
            f"**{_head}** currently holds "
            f"{format_money(max(_pot, 0), _cur)} of yours."
        )

        _books = sorted({e.ledger for e in _in_cur if e.person == _head}) or ["Family"]
        _book_col, _amount_col = st.columns(2)
        _book = _book_col.selectbox("Ledger", _books, key="move_ledger")
        _typed = _amount_col.text_input(
            f"Amount ({_cur.symbol})", placeholder="10000", key="move_amount"
        )
        _reason = st.text_input("Note", placeholder="why it moved", key="move_note")

        try:
            _moved = to_minor(_typed) if _typed.strip() else 0
        except ValueError:
            _moved = 0
        if _moved > _pot > 0:
            st.warning(
                f"That is more than the {format_money(_pot, _cur)} {_head} still "
                "holds. Recording it anyway will put them in credit."
            )

        if st.button("Add to main ledger", type="primary",
                     disabled=not (_member and _moved > 0)):
            try:
                for _row in grouping.transfer_entries(
                    _head, _member, _moved, _cur, ledger=_book, note=_reason,
                ):
                    store.append(_row)
            except Exception as exc:  # noqa: BLE001 — surface what the sheet said
                st.error(f"Could not record it: {exc}")
            else:
                clear_cache()
                st.success(
                    f"Recorded: **{_head}** returned {format_money(_moved, _cur)} "
                    f"and **{_member}** took it. The group still owes the same."
                )
                st.rerun()

st.divider()

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True,
    )
)

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

people = sorted({e.person for e in mine})
years = sorted({e.date.year for e in mine}, reverse=True)

person_col, ledger_col, year_col = st.columns(3)
with person_col:
    who = st.selectbox("Person", [ANYONE, *people])
with ledger_col:
    for_person = [e for e in mine if who in (ANYONE, e.person)]
    books = sorted({e.ledger for e in for_person})
    which = st.selectbox("Ledger", [ANY_LEDGER, *books])
with year_col:
    year = st.selectbox("Year", [ANY_YEAR, *years])

search = st.text_input(
    "Search", placeholder="Part of a note, a person, a ledger, or an amount",
    help="Matches the note, the person, the ledger and the amount as typed.",
)

shown = mine
if who != ANYONE:
    shown = [e for e in shown if e.person == who]
if which != ANY_LEDGER:
    shown = [e for e in shown if e.ledger == which]
if year != ANY_YEAR:
    shown = [e for e in shown if e.date.year == int(year)]
if search.strip():
    needle = search.strip().lower()
    shown = [
        e for e in shown
        if needle in e.note.lower()
        or needle in e.person.lower()
        or needle in e.ledger.lower()
        or needle in f"{e.amount_minor / 100:.2f}"
    ]

st.divider()

if not shown:
    st.info("Nothing matches those filters. Widen them and the entries come back.")
    st.stop()

total = sum(e.signed_minor for e in shown)
count_col, net_col, oldest_col = st.columns(3)
count_col.metric("Showing", f"{len(shown)} of {len(mine)}")
short = compact(abs(total), currency)
net_col.metric(
    "Net of these",
    f"{currency.symbol}{short}" if short else format_money(abs(total), currency),
    delta="owed to you" if total >= 0 else "you owe",
    delta_color="off",
)
oldest_col.metric("Oldest", f"{min(e.date for e in shown):%b %Y}")

st.divider()

entry_table(
    sorted(shown, key=lambda e: (e.date, e.row or 0), reverse=True),
    scope="edit",
)
