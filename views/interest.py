"""Record the interest somebody owes this month.

One form: who, which month, how much, what for. The same shape as Add entry,
because that page already solved every part of this and a second idiom would
be one to learn for no reason.

**Nothing here is added to the lending ledger.** The ledger says how much of
your money is out there; this says what it earned while it was. Once merged
the two cannot be told apart again, so they are never merged — no code path on
this page writes an Entry.
"""

from __future__ import annotations

import streamlit as st

from ledger import interest, people as grouping
from ledger.compute import by_person
from ledger.money import Currency, compact, format_money, to_minor
from ledger.ui import demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Interest")
st.caption(
    "What each person owes in interest this month. Enter them one at a time — "
    "**none of it is added to the ledger.**"
)

charges, charge_problems = interest.load()
members, member_problems = grouping.load()
parents = grouping.mapping(members)
for problem in charge_problems + member_problems:
    st.warning(problem)

if not result.entries:
    st.info("Nothing lent yet, so there is nothing to charge interest on.")
    st.stop()

# Each save starts a new round, and every input's key carries the round number.
# Clearing the session value alone is not enough: the browser keeps a text
# input's typed value while the widget's identity is unchanged, so the field
# would still *look* full even though the app had forgotten it.
ROUND = st.session_state.setdefault("interest_round", 0)


def field(name: str) -> str:
    return f"{name}_{ROUND}"


# Set by a save; read on the next run so it appears above an empty form.
if st.session_state.pop("interest_saved", None):
    st.success(st.session_state.pop("interest_saved_text", "Interest recorded."))

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True,
        key=field("currency"),
        help="Rupee and dollar interest are kept apart, like everything else.",
    )
)

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

# Only people who are already in the ledger: somebody with no entries has
# nothing for interest to be owed on, so a free-text name here would only ever
# be a typo.
everyone = sorted({e.person for e in mine})
owed = {s.person: s.net_minor for s in by_person(mine, currency)}

st.divider()
st.subheader("Add interest")

who_col, month_col = st.columns(2)
with who_col:
    person = st.selectbox("Person *", everyone, key=field("person"))
with month_col:
    month = st.selectbox(
        "Month *", interest.months_back(24),
        format_func=lambda d: f"{d:%B %Y}", key=field("month"),
        help="Interest is recorded once per person per month.",
    )

amount_col, purpose_col = st.columns([1, 2])
with amount_col:
    amount_text = st.text_input(
        f"Amount ({currency.symbol}) *", placeholder="35000", key=field("amount")
    )
with purpose_col:
    purpose = st.text_input(
        "Purpose", placeholder="what this interest is for",
        key=field("purpose"),
    )

# What they still owe, as context for working the figure out. Read-only: the
# ledger is never written from this page.
balance = owed.get(person, 0)
if balance > 0:
    short = compact(balance, currency)
    st.caption(
        f"**{person}** still owes {format_money(balance, currency)}"
        + (f" ({short})" if short else "")
        + " on the ledger — shown for reference, never changed here."
    )
elif balance < 0:
    st.caption(f"You owe **{person}** {format_money(abs(balance), currency)}.")
else:
    st.caption(f"**{person}** is settled on the ledger.")

try:
    minor = to_minor(amount_text) if amount_text.strip() else 0
except ValueError as exc:
    minor = 0
    st.error(str(exc))

# Re-entering a month replaces it rather than adding a second row. Say so, so
# that Save overwriting a figure is a choice and not a surprise.
already = interest.for_month(charges, month, currency).get(person)
if already:
    st.warning(
        f"**{person}** already has {already.money()} recorded for "
        f"{already.month_label}"
        + (f" — “{already.note}”" if already.note else "")
        + ". Saving will replace it."
    )

absent = []
if not str(person or "").strip():
    absent.append("Person")
if not amount_text.strip():
    absent.append("Amount")
if absent:
    st.warning("Still needed: **" + "**, **".join(absent) + "**")
elif minor > 0:
    st.info(
        f"**{format_money(minor, currency)}** interest from **{person}** "
        f"for *{month:%B %Y}*"
        + (f" — {purpose.strip()}" if purpose.strip() else "")
    )

if st.button("Save interest", type="primary", disabled=minor <= 0):
    try:
        what = interest.set_for_month(
            person, month, minor, currency=currency,
            note=purpose.strip(), source="manual",
        )
    except RuntimeError as exc:
        # Demo mode: say so rather than pretending the row was written.
        st.warning(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface what the sheet said
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        st.session_state["interest_round"] = ROUND + 1   # a fresh, empty form
        st.session_state["interest_saved"] = True
        st.session_state["interest_saved_text"] = (
            f"{'Replaced' if what == 'updated' else 'Recorded'} "
            f"{format_money(minor, currency)} for {person}, {month:%B %Y}. "
            "Ready for the next one."
        )
        st.rerun()

st.caption("Amounts here are never added to the ledger's totals.")

st.divider()

# --------------------------------------------------------------- this month
here = [c for c in charges if c.currency is currency]
this_month = sorted(
    interest.for_month(charges, month, currency).values(),
    key=lambda c: -c.amount_minor,
)

st.subheader(f"{month:%B %Y}")
if not this_month:
    st.caption("Nothing recorded for this month yet.")
else:
    month_total = sum(c.amount_minor for c in this_month)
    for charge in this_month:
        line, remove = st.columns([6, 1.2], vertical_alignment="center")
        group = grouping.group_of(charge.person, parents)
        with line:
            st.markdown(
                f'<div class="khata-row">'
                f'<span class="khata-amount khata-back">{charge.money()}</span>'
                f'<span class="khata-who">{charge.person}</span>'
                f'<span class="khata-meta">'
                + (f"· {group} " if group != charge.person else "")
                + (f"· {charge.note}" if charge.note else "")
                + "</span></div>",
                unsafe_allow_html=True,
            )
        with remove:
            armed = f"iarm_{charge.row}"
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
    st.caption(
        f"{month:%B %Y} totals **{format_money(month_total, currency)}** across "
        f"{len(this_month)} {'person' if len(this_month) == 1 else 'people'}."
    )

# ----------------------------------------------------------- everything else
if here:
    st.divider()
    total = interest.totals(here, currency)
    one, two, three = st.columns(3)
    short = compact(total, currency)
    one.metric(
        "Interest recorded",
        f"{currency.symbol}{short}" if short else format_money(total, currency),
        help="Across every month. Never counted in the ledger's totals.",
    )
    two.metric("Months", f"{len({c.month for c in here})}")
    three.metric("People", f"{len({c.person for c in here})}")
    st.caption(f"Exactly: {format_money(total, currency)} — not part of any ledger figure.")

    with st.expander("Everything recorded"):
        for charge in sorted(here, key=lambda c: (c.date, c.person), reverse=True):
            st.markdown(
                f"- **{charge.month_label}** · {charge.person} — {charge.money()}"
                + (f" · _{charge.note}_" if charge.note else "")
            )
