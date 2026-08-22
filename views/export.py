"""Take the ledger with you: a spreadsheet to work in, a statement to print."""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger.compute import ALL_TIME, PERIODS, filter_entries
from ledger.export import to_excel, to_pdf
from ledger.money import Currency, format_money
from ledger.ui import demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Download")
st.caption("Both files hold the same rows, so they can never disagree with each other or with the screen.")

if not result.entries:
    st.info("There is nothing to download yet. Add an entry first.")
    st.stop()

period_col, people_col, currency_col = st.columns([1.2, 2, 1.2])
with period_col:
    period = st.selectbox("Period", list(PERIODS), index=list(PERIODS).index(ALL_TIME))
with people_col:
    everyone = sorted({e.person for e in result.entries})
    people = st.multiselect("People", everyone, default=everyone)
with currency_col:
    choice = st.selectbox(
        "Currency", ["Both", *(c.value for c in Currency)],
        format_func=lambda v: "Both currencies" if v == "Both" else Currency(v).label,
    )

currency = None if choice == "Both" else Currency(choice)
chosen = filter_entries(result.entries, period=period, people=people, currency=currency)

if not chosen:
    st.warning("Those filters leave nothing to download. Widen them and the buttons come back.")
    st.stop()

st.divider()

summary = st.columns(len(Currency) + 1)
summary[0].metric("Entries", len(chosen))
for slot, unit in zip(summary[1:], Currency):
    subset = [e for e in chosen if e.currency is unit]
    net = sum(e.signed_minor for e in subset)
    slot.metric(f"Net owed · {unit.value}", format_money(net, unit) if subset else "—")

st.divider()

stamp = f"{date.today():%Y-%m-%d}"
excel_col, pdf_col = st.columns(2)

with excel_col:
    st.subheader("Spreadsheet")
    st.caption(
        "One tab per currency plus every entry. Amounts are real numbers, not "
        "text, so the columns add up."
    )
    st.download_button(
        "Download .xlsx",
        data=to_excel(chosen),
        file_name=f"personal-ledger-{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )

with pdf_col:
    st.subheader("Statement")
    st.caption(
        "A printable page: totals per currency, then every entry in date order. "
        "Amounts read as INR / USD, because the PDF's built-in fonts have no ₹."
    )
    st.download_button(
        "Download .pdf",
        data=to_pdf(chosen),
        file_name=f"personal-ledger-{stamp}.pdf",
        mime="application/pdf",
        width="stretch",
    )

st.divider()
with st.expander(f"Preview the {len(chosen)} rows going into these files"):
    st.dataframe(
        [
            {
                "Date": f"{e.date:%d %b %Y}",
                "Person": e.person,
                "Ledger": e.ledger,
                "Direction": e.direction.value,
                "Amount": format_money(e.amount_minor, e.currency),
                "Note": e.note,
            }
            for e in sorted(chosen, key=lambda x: (x.date, x.person))
        ],
        hide_index=True,
        width="stretch",
    )
