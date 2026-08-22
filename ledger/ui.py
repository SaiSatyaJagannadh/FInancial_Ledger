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


#: Names people actually paste. Streamlit secrets are hand-edited TOML, so the
#: key arrives however the person typed it.
_KEY_NAMES = ("NVIDIA_API_KEY", "nvidia_api_key", "NVIDIA_KEY", "nvapi_key")


def api_key() -> str:
    """The NVIDIA key, from Streamlit secrets or the environment.

    Looks inside every section, not just the top level. A TOML section header
    swallows every key beneath it, so a key pasted at the bottom of the box
    silently becomes `sheet.NVIDIA_API_KEY` and a top-level lookup misses it.
    That is a footgun in the file format, not a mistake worth making anyone
    debug, so we just find it wherever it landed.

    Falls back to the environment so the assistant can be exercised locally
    without a secrets.toml, which is how it gets tested.
    """
    import os

    def search(mapping) -> str:
        for name in _KEY_NAMES:
            try:
                value = mapping.get(name)
            except Exception:
                value = None
            if value:
                return str(value)
        # One level down: [sheet], [drive], [gcp_service_account], …
        try:
            children = list(mapping.values())
        except Exception:
            children = []
        for child in children:
            if hasattr(child, "get"):
                found = search(child)
                if found:
                    return found
        return ""

    try:
        from_secrets = search(st.secrets)
    except Exception:
        from_secrets = ""

    for name in _KEY_NAMES:
        from_secrets = from_secrets or os.environ.get(name, "")

    return str(from_secrets).strip()


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
