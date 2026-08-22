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


#: A khata is ruled in two inks, and which ink a figure is written in *is* its
#: direction. Colour carries that meaning here and nothing else, so it is never
#: spent on decoration.
INK = "#16202E"
OUT = "#B3261E"   # money out, to them
BACK = "#1B7A5A"  # money back, from them
RULE = "#DCD8CC"

_CSS = f"""
<style>
  /* Figures are the thing you actually read on a ledger, so they get tabular
     numerals: columns of digits that line up instead of shimmying. */
  [data-testid="stMetricValue"], .khata-amount, .khata-row td {{
      font-variant-numeric: tabular-nums;
      font-feature-settings: "tnum" 1;
  }}
  [data-testid="stMetricValue"] {{ letter-spacing: -0.02em; }}

  .khata-row {{
      display: flex; align-items: baseline; gap: .6rem;
      padding: .1rem 0 .35rem 0;
  }}
  .khata-amount {{ font-size: 1.12rem; font-weight: 700; letter-spacing: -0.01em; }}
  .khata-out  {{ color: {OUT}; }}
  .khata-back {{ color: {BACK}; }}
  .khata-who  {{ font-size: 1.02rem; font-weight: 600; color: {INK}; }}
  .khata-meta {{ font-size: .82rem; opacity: .62; }}
  .khata-dir  {{ font-size: .74rem; letter-spacing: .08em; text-transform: uppercase; opacity: .55; }}

  /* The ruled line is the signature. One hairline per entry, like the page of
     a real khata — no cards, no shadows, no borders boxing every row in. */
  .khata-rule {{ border: 0; border-top: 1px solid {RULE}; margin: .1rem 0 .55rem 0; }}

  /* Readability: the default line length runs long on a wide screen. */
  .block-container p, .block-container li {{ max-width: 68ch; }}
</style>
"""


def styles() -> None:
    """Inject the shared look. Cheap enough to call on every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def entry_line(entry, *, show_attachment: bool = True) -> None:
    """One ruled entry, written the way a khata writes it."""
    from ledger.money import format_money

    outgoing = entry.signed_minor > 0
    st.markdown(
        f'<div class="khata-row">'
        f'<span class="khata-amount {"khata-out" if outgoing else "khata-back"}">'
        f'{format_money(entry.amount_minor, entry.currency)}</span>'
        f'<span class="khata-dir">{"gave" if outgoing else "got back"}</span>'
        f'<span class="khata-who">{entry.person}</span>'
        f'<span class="khata-meta">· {entry.ledger} · {entry.date:%d %b %Y}'
        + (f" · {entry.note}" if entry.note else "")
        + "</span></div>",
        unsafe_allow_html=True,
    )
    if show_attachment and entry.attachment:
        st.markdown(
            f'<div class="khata-meta">📎 <a href="{entry.attachment}" target="_blank">'
            "statement</a></div>",
            unsafe_allow_html=True,
        )


def delete_control(entry, scope: str) -> bool:
    """Two-click delete. First click arms it, second does it.

    A one-click delete beside a list of real debts is an accident waiting to
    happen, and the sheet has no undo of its own.

    Returns True when a row was actually removed.
    """
    from ledger import store

    if entry.row is None:
        return False
    armed = f"arm_{scope}_{entry.row}"

    if not st.session_state.get(armed):
        if st.button("Delete", key=f"del_{scope}_{entry.row}",
                     help="Remove this entry from the sheet", width="stretch"):
            st.session_state[armed] = True
            st.rerun()
        return False

    yes, no = st.columns(2)
    if yes.button("Yes", key=f"yes_{scope}_{entry.row}", type="primary", width="stretch"):
        try:
            store.delete(entry)
        except Exception as exc:  # noqa: BLE001 — show whatever the sheet said
            st.error(f"Could not delete: {exc}")
            st.session_state[armed] = False
        else:
            clear_cache()
            st.session_state[armed] = False
            st.toast("Entry deleted")
            st.rerun()
    if no.button("No", key=f"no_{scope}_{entry.row}", width="stretch"):
        st.session_state[armed] = False
        st.rerun()
    return False


def entry_ledger(entries: list, scope: str, *, empty: str = "Nothing here yet.") -> None:
    """A ruled list of entries, each with its own delete."""
    if not entries:
        st.caption(empty)
        return
    for entry in entries:
        line, action = st.columns([9, 1.6], vertical_alignment="center")
        with line:
            entry_line(entry)
        with action:
            delete_control(entry, scope)
        st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)
