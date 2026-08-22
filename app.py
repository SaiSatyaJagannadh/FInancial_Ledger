"""Personal Ledger — who owes me what, in each currency separately.

This file is only the router. Streamlit names the entry page after its
filename, which is why the sidebar used to read "app"; declaring the pages
explicitly lets each one carry the name a person would actually use.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Personal Ledger", page_icon="₹", layout="wide")

st.navigation([
    st.Page("views/dashboard.py", title="Ledger", icon=":material/menu_book:", default=True),
    st.Page("views/add_entry.py", title="Add entry", icon=":material/add:"),
    st.Page("views/assistant.py", title="Assistant", icon=":material/forum:"),
    st.Page("views/invested.py", title="Invested instead", icon=":material/trending_up:"),
    st.Page("views/export.py", title="Download", icon=":material/download:"),
]).run()
