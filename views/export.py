"""Take the ledger with you: a spreadsheet to work in, a statement to print."""

from __future__ import annotations

from datetime import date

import streamlit as st

from ledger.compute import ALL_TIME, PERIODS, filter_entries
from ledger import people as grouping
from ledger.export import email_link, summary_text, to_excel, to_pdf, whatsapp_link
from ledger.money import Currency, format_money
from ledger.ui import demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

# Every download carries the groupings, so a spreadsheet or a forwarded
# statement says the same thing the dashboard does.
parents = grouping.mapping(grouping.load()[0])

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
        data=to_excel(chosen, parents),
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
        data=to_pdf(chosen, parents=parents),
        file_name=f"personal-ledger-{stamp}.pdf",
        mime="application/pdf",
        width="stretch",
    )

st.divider()

st.subheader("Share")
st.caption(
    "WhatsApp and email links carry text, not files — neither can attach a "
    "document. This sends a written summary; to send the spreadsheet itself, "
    "download it first and attach it the usual way."
)

shared = summary_text(chosen, parents=parents)
st.text_area("What gets sent", value=shared, height=190, key="share_text")

wa_col, mail_col = st.columns(2)
wa_col.link_button("💬  Send on WhatsApp", whatsapp_link(shared), width="stretch")
mail_col.link_button("✉️  Send by email", email_link(shared), width="stretch")
st.caption("Both open with the message already written — pick the recipient there.")

st.divider()

with st.expander("Where is my data kept?"):
    st.markdown(
        """
Everything lives in **one Google Sheet**, private to you and shared only with
the app's service account. Three tabs:

| Tab | Holds |
|---|---|
| `entries` | The lending ledger |
| `transactions` | General spending |
| `attachments` | Uploaded images and PDFs |

**Attachments are in the sheet, not Google Drive.** Google does not let a
service account own files in Drive without a paid Workspace account, so an
uploaded file is stored inside the `attachments` tab and rebuilt when you open
it. Open an entry and press **View / download** to get the original back.

**GitHub holds the code only** — no entries, no amounts, no keys, no sheet
address. Your credentials live in Streamlit's encrypted secrets, never in the
repository.
        """
    )

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
