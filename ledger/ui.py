"""Shared Streamlit pieces. Presentation only — no arithmetic lives here."""

from __future__ import annotations

import streamlit as st

from ledger import store

CACHE_SECONDS = 60


@st.cache_data(ttl=CACHE_SECONDS, show_spinner="Loading your ledger…")
def _cached_load() -> store.LoadResult:
    return store.load()


def load_ledger() -> store.LoadResult:
    return _cached_load()


def clear_cache() -> None:
    _cached_load.clear()


def demo_banner(result: store.LoadResult) -> None:
    """Say plainly which data is on screen. Silence here would be misleading."""
    if not result.demo:
        if result.problems:
            with st.expander(f"⚠️ {len(result.problems)} row(s) could not be read"):
                for problem in result.problems:
                    st.write(f"- {problem}")
        return

    if result.detail:
        st.error(f"**Sheet unavailable** — {result.detail}")
    else:
        st.warning(
            "**Demo mode** — showing sample data. Add your credentials to "
            "`.streamlit/secrets.toml` to connect your real Google Sheet.",
            icon="⚠️",
        )


def page_config(title: str) -> None:
    st.set_page_config(page_title=title, page_icon="₹", layout="wide")
