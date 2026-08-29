"""Record the interest somebody owes — or has handed over — this month.

One form: who, which month, how much, whether it is still due or already
given, and what it is for. The same shape as Add entry, because that page
already solved every part of this.

**Interest stays out of the lending ledger by default.** The ledger says how
much of your money is out there; this says what it earned while it was. The
single exception is deliberate and opt-in: when the interest money is handed
on to somebody else rather than kept, it has stopped being interest and become
a loan to them, and the ledger is where loans live. That only ever happens
because the radio was set to it — never on its own.
"""

from __future__ import annotations

import streamlit as st

from ledger import attach, interest, people as grouping
from ledger.compute import by_person
from ledger.money import Currency, compact, format_money, to_minor
from ledger.ui import (
    attachment_button, attachment_field, attachment_is_stored, clear_cache,
    demo_banner, load_ledger, safe_href, esc, styles,
)

styles()

result = load_ledger()
demo_banner(result)

st.title("Interest")
st.caption(
    "What each person owes in interest, or has already handed over. "
    "**Kept out of the ledger unless you say otherwise.**"
)

charges, charge_problems = interest.load()
members, member_problems = grouping.load()
parents = grouping.mapping(members)
for problem in charge_problems + member_problems:
    st.warning(problem)

if not result.entries:
    st.info("Nothing lent yet, so there is nothing to charge interest on.")
    st.stop()

ROUND = st.session_state.setdefault("interest_round", 0)


def field(name: str) -> str:
    return f"{name}_{ROUND}"


if st.session_state.pop("interest_saved", None):
    st.success(st.session_state.pop("interest_saved_text", "Interest recorded."))

currency = Currency(
    st.radio(
        "Currency",
        [c.value for c in Currency],
        format_func=lambda v: f"{Currency(v).flag}  {Currency(v).label}",
        horizontal=True, key=field("currency"),
    )
)

mine = [e for e in result.entries if e.currency is currency]
if not mine:
    st.info(f"No {currency.label} entries yet.")
    st.stop()

everyone = sorted({e.person for e in mine})
owed = {s.person: s.net_minor for s in by_person(mine, currency)}
TO_LEDGER = "Someone else took it → add to the ledger"


def ledger_options(person: str) -> list[str]:
    return sorted({e.ledger for e in mine if e.person == person}) or ["Interest"]


# ------------------------------------------------------------------ the form
st.divider()
st.subheader("Add interest")

who_col, month_col = st.columns(2)
with who_col:
    person = st.selectbox("Person *", everyone, key=field("person"))
with month_col:
    month = st.selectbox(
        "Month *", interest.months_back(24),
        format_func=lambda d: f"{d:%B %Y}", key=field("month"),
    )

status = st.radio(
    "What happened *",
    [interest.Kind.due.value, interest.Kind.given.value, TO_LEDGER],
    format_func=lambda v: TO_LEDGER if v == TO_LEDGER else interest.Kind(v).label,
    horizontal=False, key=field("kind"),
    help=(
        "Still due — they owe it. Given to me — they have paid it. The third "
        "also writes a ledger entry, for when the money went to somebody else."
    ),
)
to_ledger = status == TO_LEDGER
kind = interest.Kind.given if to_ledger else interest.Kind(status)

taker, taker_ledger = "", ""
if to_ledger:
    take_col, book_col = st.columns(2)
    taker = take_col.selectbox(
        "Who took it *", everyone, key=field("taker"),
        help="A ledger entry is written against them for this amount.",
    )
    taker_ledger = book_col.selectbox(
        "Onto which ledger *", ledger_options(taker), key=field("taker_ledger")
    )

amount_col, purpose_col = st.columns([1, 2])
with amount_col:
    amount_text = st.text_input(
        f"Amount ({currency.symbol}) *", placeholder="35000", key=field("amount")
    )
with purpose_col:
    purpose = st.text_input(
        "Purpose", placeholder="what this interest is for", key=field("purpose")
    )

photo = st.file_uploader(
    "Photo or receipt (optional)",
    type=["pdf", "png", "jpg", "jpeg", "webp"], key=field("photo"),
    help=f"Kept inside the spreadsheet, up to {attach.MAX_BYTES // 1024} KB.",
)

balance = owed.get(person, 0)
if balance > 0:
    short = compact(balance, currency)
    st.caption(
        f"**{person}** still owes {format_money(balance, currency)}"
        + (f" ({short})" if short else "")
        + " on the ledger — shown for reference."
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

already = interest.for_month(charges, month, currency).get(person)
if already:
    st.warning(
        f"**{person}** already has {already.money()} recorded for "
        f"{already.month_label}. Saving will replace it."
    )

absent = []
if not amount_text.strip():
    absent.append("Amount")
if to_ledger and not taker:
    absent.append("Who took it")
if absent:
    st.warning("Still needed: **" + "**, **".join(absent) + "**")
elif minor > 0:
    line = (
        f"**{format_money(minor, currency)}** interest from **{person}** "
        f"for *{month:%B %Y}* — {kind.label.lower()}"
    )
    if to_ledger:
        line += (
            f".  \nA ledger entry will also be written: **{taker}** given "
            f"{format_money(minor, currency)} on *{taker_ledger}*."
        )
    st.info(line)

if st.button("Save interest", type="primary",
             disabled=minor <= 0 or (to_ledger and not taker)):
    try:
        link = ""
        if photo is not None:
            with st.spinner(f"Storing {photo.name}…"):
                link = attach.put(
                    photo.name, photo.getvalue(),
                    photo.type or "application/octet-stream",
                )
        what = interest.set_for_month(
            person, month, minor, currency=currency, note=purpose.strip(),
            kind=kind, attachment=link, source="manual",
        )
        wrote = ""
        if to_ledger:
            # The only path from this page to the ledger, and it is here
            # because the radio was set to it. Saving the same charge twice
            # must neither append a second loan nor overwrite an unrelated
            # one — sync_ledger_entry is where both are decided.
            saved = interest.for_month(interest.load()[0], month, currency)[person]
            did = interest.sync_ledger_entry(
                result.entries, saved, taker, taker_ledger, note=purpose,
            )
            wrote = {
                "added": f" A ledger entry was written for {taker}.",
                "updated": f" {taker}'s ledger entry was updated to match.",
                "unchanged": f" {taker} already has this in the ledger — left alone.",
            }[did]
            interest.set_for_month(
                person, month, minor, currency=currency, note=purpose.strip(),
                kind=kind, attachment=link, moved_to=taker, source="manual",
            )
            clear_cache()
    except RuntimeError as exc:
        st.warning(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface what the sheet said
        st.error(f"Could not save: {type(exc).__name__}: {exc}")
    else:
        st.session_state["interest_round"] = ROUND + 1
        st.session_state["interest_saved"] = True
        st.session_state["interest_saved_text"] = (
            f"{'Replaced' if what == 'updated' else 'Recorded'} "
            f"{format_money(minor, currency)} for {person}, {month:%B %Y}."
            + wrote
        )
        st.rerun()

st.divider()

# ------------------------------------------------------------------ the list
here = [c for c in charges if c.currency is currency]
if not here:
    st.caption("Nothing recorded yet in this currency.")
    st.stop()

split = interest.split_by_kind(here, currency)
net = interest.recorded_total(here, currency)
moved = interest.moved_total(here, currency)

one, two, three, four = st.columns(4)
short = compact(net, currency)
one.metric(
    "Interest recorded",
    f"{currency.symbol}{short}" if short else format_money(net, currency),
    help="What is still interest. Anything handed on to somebody else is "
         "counted in the ledger instead, not twice.",
)
two.metric("Still due", format_money(split[interest.Kind.due], currency))
three.metric("Given to me", format_money(split[interest.Kind.given], currency))
four.metric(
    "Moved to ledger", format_money(moved, currency),
    delta="counted there, not here", delta_color="off",
)
st.caption(
    f"Exactly: {format_money(net, currency)} of interest"
    + (f", plus {format_money(moved, currency)} now sitting in the ledger"
       if moved else "")
    + "."
)

st.divider()

ANYONE = "Everyone"
ANY_MONTH = "All months"
filter_who, filter_month = st.columns(2)
with filter_who:
    # Grouped people are offered by their group, as everywhere else.
    charged = sorted({c.person for c in here})
    families = grouping.groups(charged, parents)
    heads = sorted(families)

    def _label(head: str) -> str:
        others = [n for n in families[head] if n != head]
        return f"{head}  (+{len(others)})" if others else head

    picked = st.selectbox("Person", [ANYONE, *heads], format_func=
                          lambda v: v if v == ANYONE else _label(v))
with filter_month:
    seen = sorted({c.month for c in here}, reverse=True)
    labels = {c.month: c.month_label for c in here}
    chosen_month = st.selectbox(
        "Month", [ANY_MONTH, *seen],
        format_func=lambda v: v if v == ANY_MONTH else labels[v],
    )

shown = here
if picked != ANYONE:
    shown = [c for c in shown if c.person in families[picked]]
if chosen_month != ANY_MONTH:
    shown = [c for c in shown if c.month == chosen_month]
shown = sorted(shown, key=lambda c: (c.date, c.person), reverse=True)

if not shown:
    st.info("Nothing matches those filters.")
    st.stop()

st.subheader(f"{len(shown)} charge{'s' if len(shown) != 1 else ''}")
st.caption(
    f"Showing {format_money(sum(c.amount_minor for c in shown), currency)} "
    "of interest."
)


@st.dialog("Edit interest")
def _edit(charge) -> None:
    """Change any field of a recorded charge, then write it back to its row."""
    from dataclasses import replace

    st.caption(f"{charge.person} · {charge.month_label}")

    amount = st.text_input(
        f"Amount ({charge.currency.symbol})",
        value=f"{charge.amount_minor / 100:.2f}",
    )
    new_kind = st.radio(
        "What happened", list(interest.Kind),
        index=list(interest.Kind).index(charge.kind),
        format_func=lambda k: k.label, horizontal=True,
    )
    note = st.text_input("Purpose", value=charge.note)
    link, upload = attachment_field(charge, key="i")

    also = st.checkbox(
        "Someone else took this — add it to the main ledger",
        help="Writes a ledger entry for the amount. Nothing is written unless "
             "this is ticked.",
    )
    taker_now, book_now = "", ""
    if also:
        take, book = st.columns(2)
        taker_now = take.selectbox("Who took it", everyone)
        book_now = book.selectbox("Onto which ledger", ledger_options(taker_now))

    edited, problems = None, []
    try:
        minor_now = to_minor(amount)
        if minor_now <= 0:
            problems.append("Interest must be more than zero.")
        else:
            edited = replace(
                charge, amount_minor=minor_now, kind=new_kind,
                note=note.strip(), attachment=link.strip(), source="manual",
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(str(exc))
    for problem in problems:
        st.error(problem)

    save, cancel = st.columns(2)
    if save.button("Save changes", type="primary", width="stretch",
                   disabled=edited is None or (also and not taker_now)):
        try:
            if upload is not None:
                # Stored first: a row pointing at a file that never arrived is
                # worse than failing before anything is written.
                with st.spinner(f"Storing {upload.name}…"):
                    edited = replace(edited, attachment=attach.put(
                        upload.name, upload.getvalue(),
                        upload.type or "application/octet-stream",
                    ))
            interest.replace_row(charge, edited)
            if also:
                # Same guard as the add form: ticking this twice must not put
                # a second loan in the ledger, nor land on a stranger's row.
                interest.sync_ledger_entry(
                    result.entries, edited, taker_now, book_now, note=note,
                )
                clear_cache()
        except Exception as exc:  # noqa: BLE001 — show what the sheet said
            st.error(f"Could not save: {exc}")
        else:
            st.session_state["interest_edited"] = (
                f"{edited.person} · {edited.month_label}"
                + (f" — and a ledger entry for {taker_now}" if also else "")
            )
            st.rerun()
    if cancel.button("Cancel", width="stretch"):
        st.rerun()


if st.session_state.pop("interest_edited", None) is not None:
    st.success("Updated.")

for charge in shown:
    line, edit, remove = st.columns([6, 1.2, 1.2], vertical_alignment="center")
    group = grouping.group_of(charge.person, parents)
    with line:
        st.markdown(
            f'<div class="khata-row">'
            f'<span class="khata-amount '
            f'{"khata-back" if charge.kind is interest.Kind.given else "khata-out"}">'
            f'{charge.money()}</span>'
            f'<span class="khata-dir">{esc(charge.kind.label)}</span>'
            f'<span class="khata-who">{esc(charge.person)}</span>'
            f'<span class="khata-meta">· {charge.month_label}'
            + (f" · {esc(group)}" if group != charge.person else "")
            + (f" · {esc(charge.note)}" if charge.note else "")
            + (f' · <span class="khata-src">→ ledger, {esc(charge.moved_to)}</span>'
               if charge.moved_to else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )
        if charge.attachment and attachment_is_stored(charge.attachment):
            attachment_button(charge.attachment, key=f"iatt_{charge.row}")
        elif charge.attachment:
            href = safe_href(charge.attachment)
            st.markdown(
                f'<div class="khata-meta">📎 <a href="{esc(href)}" target="_blank" '
                'rel="noopener noreferrer">attachment</a></div>' if href
                else '<div class="khata-meta">📎 attachment</div>',
                unsafe_allow_html=True,
            )
    with edit:
        if st.button("Edit", key=f"iedit_{charge.row}", width="stretch"):
            _edit(charge)
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

# ------------------------------------------------------------- by month
st.divider()
st.subheader("Total by month")

for bucket in reversed(interest.by_month(shown, currency)):
    label = next(c.month_label for c in shown if c.month == bucket["month"])
    of_month = [c for c in shown if c.month == bucket["month"]]
    moved_here = sum(c.amount_minor for c in of_month if c.moved_to)
    st.markdown(
        f"- **{label}** — {format_money(bucket['total_minor'], currency)} "
        f"across {len(of_month)} "
        f"{'charge' if len(of_month) == 1 else 'charges'}"
        + (f" · {format_money(moved_here, currency)} of it moved to the ledger"
           if moved_here else "")
    )

# ------------------------------------------------------------- by person
st.divider()
st.subheader("Total by person")

rows = interest.by_person(shown, currency)
for row in rows:
    head = grouping.group_of(row["person"], parents)
    st.markdown(
        f"- **{row['person']}** — {format_money(row['total_minor'], currency)} "
        f"over {row['months']} month{'s' if row['months'] != 1 else ''}"
        + (f" · under *{head}*" if head != row["person"] else "")
    )

if any(grouping.group_of(r["person"], parents) != r["person"] for r in rows):
    st.markdown("**By group**")
    per_group: dict[str, int] = {}
    for row in rows:
        head = grouping.group_of(row["person"], parents)
        per_group[head] = per_group.get(head, 0) + row["total_minor"]
    for head, amount in sorted(per_group.items(), key=lambda kv: -kv[1]):
        st.markdown(f"- **{head}** — {format_money(amount, currency)}")
