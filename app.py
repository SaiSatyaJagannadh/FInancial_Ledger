"""Personal Ledger — who owes me what, in each currency separately.

This file is only the router. Streamlit names the entry page after its
filename, which is why the sidebar used to read "app"; declaring the pages
explicitly lets each one carry the name a person would actually use.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Personal Ledger", page_icon="₹", layout="wide")

# Sign-in goes here, before any page is constructed, because this is the only
# way into the app — every view is reached through this router. A page that
# gated itself would be one `st.Page` line away from being forgotten. Does
# nothing at all when [auth] is absent, which is demo mode and the page tests.
from ledger import auth  # noqa: E402 — must follow set_page_config

auth.gate()
auth.sidebar_identity()

st.navigation([
    st.Page("views/dashboard.py", title="Ledger", icon=":material/menu_book:", default=True),
    st.Page("views/add_entry.py", title="Add entry", icon=":material/add:"),
    st.Page("views/edit_entries.py", title="Edit entries", icon=":material/edit_note:"),
    st.Page("views/interest.py", title="Interest", icon=":material/percent:"),
    st.Page("views/assistant.py", title="Assistant", icon=":material/forum:"),
    st.Page("views/spending.py", title="Spending", icon=":material/receipt_long:"),
    st.Page("views/add_spending.py", title="Add spending", icon=":material/add_card:"),
    st.Page("views/export.py", title="Download", icon=":material/download:"),
]).run()
