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
  .khata-src  {{ font-style: italic; opacity: .8; }}
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


#: A short mark, not a sentence: the line is already dense.
_SOURCE_MARK = {"chat": "via chat", "image": "from image"}


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
        + (f' · <span class="khata-src">{_SOURCE_MARK[entry.source]}</span>'
           if entry.source in _SOURCE_MARK else "")
        + "</span></div>",
        unsafe_allow_html=True,
    )
    if show_attachment and entry.attachment:
        st.markdown(
            f'<div class="khata-meta">📎 <a href="{entry.attachment}" target="_blank">'
            "statement</a></div>",
            unsafe_allow_html=True,
        )


@st.dialog("Edit entry")
def _edit_dialog(entry, people: list[str], ledgers: list[str]) -> None:
    """Change any field of an existing entry, then write it back to its row."""
    from dataclasses import replace

    from ledger import store
    from ledger.models import BY_HAND, Direction, EntryError, SOURCE_LABELS
    from ledger.money import Currency, to_minor

    origin = SOURCE_LABELS.get(entry.source, entry.source)
    if origin:
        st.caption(f"Originally {origin}. Editing marks it as typed in.")

    who, which = st.columns(2)
    person = who.text_input("Person", value=entry.person)
    ledger_name = which.text_input("Ledger", value=entry.ledger)

    when_col, dir_col, amount_col = st.columns([1.2, 1.3, 1.2])
    when = when_col.date_input("Date", value=entry.date, format="DD/MM/YYYY")
    direction = dir_col.radio(
        "Direction", list(Direction),
        index=list(Direction).index(entry.direction),
        format_func=lambda d: "I gave them" if d is Direction.given else "They gave me back",
    )
    amount = amount_col.text_input(
        f"Amount ({entry.currency.symbol})", value=f"{entry.amount_minor / 100:.2f}"
    )

    currency = st.radio(
        "Currency", list(Currency), index=list(Currency).index(entry.currency),
        format_func=lambda c: f"{c.flag}  {c.label}", horizontal=True,
    )
    note = st.text_input("Note", value=entry.note)
    attachment = st.text_input("Attachment link", value=entry.attachment)

    problems: list[str] = []
    edited = None
    try:
        minor = to_minor(amount)
        if minor <= 0:
            problems.append("Amount must be more than zero — Direction says which way it goes.")
        else:
            edited = replace(
                entry, date=when, person=person.strip(), ledger=ledger_name.strip(),
                direction=direction, amount_minor=minor, currency=currency,
                note=note.strip(), attachment=attachment.strip(), source=BY_HAND,
            )
    except (ValueError, EntryError) as exc:
        problems.append(str(exc))

    for problem in problems:
        st.error(problem)

    unchanged = edited is not None and edited.to_row() == entry.to_row()
    if unchanged:
        st.caption("Nothing changed yet.")

    save, cancel = st.columns(2)
    if save.button("Save changes", type="primary", width="stretch",
                   disabled=edited is None or unchanged):
        try:
            store.update(entry, edited)
        except Exception as exc:  # noqa: BLE001 — show whatever the sheet said
            st.error(f"Could not save: {exc}")
        else:
            clear_cache()
            st.session_state["just_edited"] = f"{edited.person} · {edited.date:%d %b %Y}"
            st.rerun()
    if cancel.button("Cancel", width="stretch"):
        st.rerun()


def edit_control(entry, scope: str, people: list[str], ledgers: list[str]) -> None:
    if entry.row is None:
        return
    if st.button("Edit", key=f"edit_{scope}_{entry.row}",
                 help="Change this entry", width="stretch"):
        _edit_dialog(entry, people, ledgers)


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
    """A ruled list of entries, each with its own edit and delete."""
    just = st.session_state.pop("just_edited", None)
    if just:
        st.success(f"Updated {just}.")

    if not entries:
        st.caption(empty)
        return

    people = sorted({e.person for e in entries})
    ledgers = sorted({e.ledger for e in entries})

    for entry in entries:
        line, edit, remove = st.columns([8, 1.3, 1.3], vertical_alignment="center")
        with line:
            entry_line(entry)
        with edit:
            edit_control(entry, scope, people, ledgers)
        with remove:
            delete_control(entry, scope)
        st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)
