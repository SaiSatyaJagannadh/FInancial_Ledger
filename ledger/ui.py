"""Shared Streamlit pieces. Presentation only — no arithmetic lives here."""

from __future__ import annotations

import streamlit as st

from ledger import store
from ledger.money import format_money

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
  .khata-head {{
      font-size: .72rem; letter-spacing: .09em; text-transform: uppercase;
      opacity: .55; font-weight: 600;
  }}
  .khata-cell {{ line-height: 1.35; padding: .1rem 0; }}
  .khata-cell.khata-amount {{ font-size: 1.02rem; }}
  .khata-rule-head {{ border-top-color: {INK}; opacity: .35; margin-bottom: .1rem; }}
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


#: One grid, one set of widths, so every row lines up down the page.
_LEDGER_COLS = [1.5, 2.3, 1.8, 3.0, 1.35, 1.45]
_LEDGER_HEADS = ["Date", "Amount", "Ledger", "Note", "", ""]


def _head_row(labels: list[str], widths: list[float]) -> None:
    cells = st.columns(widths, vertical_alignment="bottom")
    for cell, text in zip(cells, labels):
        if text:
            cell.markdown(f'<div class="khata-head">{text}</div>', unsafe_allow_html=True)
    st.markdown('<hr class="khata-rule khata-rule-head">', unsafe_allow_html=True)


def entry_table(entries: list, scope: str, *, empty: str = "Nothing here yet.") -> None:
    """Entries as an aligned grid: date, amount, ledger, note, edit, delete.

    A grid rather than a free-flowing line, because reading down a column of
    dates and a column of amounts is the whole point of a ledger — and the
    previous flex layout put every row's amount in a different place.
    """
    just = st.session_state.pop("just_edited", None)
    if just:
        st.success(f"Updated {just}.")

    if not entries:
        st.caption(empty)
        return

    _head_row(_LEDGER_HEADS, _LEDGER_COLS)

    for entry in entries:
        when, amount, book, note, edit, remove = st.columns(
            _LEDGER_COLS, vertical_alignment="center"
        )
        outgoing = entry.signed_minor > 0
        when.markdown(
            f'<div class="khata-cell">{entry.date:%d %b %Y}</div>', unsafe_allow_html=True
        )
        amount.markdown(
            f'<div class="khata-cell khata-amount {"khata-out" if outgoing else "khata-back"}">'
            f'{format_money(entry.amount_minor, entry.currency)}'
            f'<span class="khata-dir"> {"gave" if outgoing else "got back"}</span></div>',
            unsafe_allow_html=True,
        )
        book.markdown(f'<div class="khata-cell">{entry.ledger}</div>', unsafe_allow_html=True)
        mark = _SOURCE_MARK.get(entry.source, "")
        link = (f' · <a href="{entry.attachment}" target="_blank">📎</a>'
                if entry.attachment else "")
        note.markdown(
            f'<div class="khata-cell khata-meta">{entry.note or "—"}'
            + (f' <span class="khata-src">{mark}</span>' if mark else "")
            + link + "</div>",
            unsafe_allow_html=True,
        )
        with edit:
            edit_control(entry, scope, [], [])
        with remove:
            delete_control(entry, scope)
        st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)


_SPEND_COLS = [2.3, 1.5, 1.7, 2.8, 1.35, 1.45]
_SPEND_HEADS = ["When", "", "Category", "Detail", "", ""]


@st.dialog("Edit transaction")
def _edit_transaction(t) -> None:
    from dataclasses import replace

    from ledger import spend
    from ledger.models import BY_HAND, EntryError
    from ledger.money import Currency, to_minor

    kind_col, cat_col = st.columns(2)
    kind = kind_col.radio(
        "Kind", list(spend.Kind), index=list(spend.Kind).index(t.kind),
        format_func=lambda k: k.label, horizontal=True,
    )
    category = cat_col.text_input("Category *", value=t.category)

    from_col, to_col = st.columns(2)
    start = from_col.date_input("From *", value=t.date, format="DD/MM/YYYY")
    ongoing = to_col.checkbox("Runs over a period", value=t.ongoing)
    end = to_col.date_input(
        "Until", value=t.end_date or t.date, format="DD/MM/YYYY"
    ) if ongoing else None

    amount_col, currency_col = st.columns(2)
    amount = amount_col.text_input(
        f"Amount ({t.currency.symbol}) *", value=f"{t.amount_minor / 100:.2f}")
    currency = currency_col.radio(
        "Currency", list(Currency), index=list(Currency).index(t.currency),
        format_func=lambda c: f"{c.flag}  {c.label}", horizontal=True,
    )
    description = st.text_input("Description", value=t.description)
    note = st.text_input("Note", value=t.note)

    edited = None
    try:
        edited = replace(
            t, kind=kind, category=category.strip(), date=start, end_date=end,
            amount_minor=to_minor(amount), currency=currency,
            description=description.strip(), note=note.strip(), source=BY_HAND,
        )
    except (ValueError, EntryError) as exc:
        st.error(str(exc))

    save, cancel = st.columns(2)
    if save.button("Save changes", type="primary", width="stretch", disabled=edited is None):
        try:
            spend.replace_row(t, edited)
        except Exception as exc:  # noqa: BLE001 — surface what the sheet said
            st.error(f"Could not save: {exc}")
        else:
            st.session_state["just_edited"] = f"{edited.category} · {edited.date:%d %b %Y}"
            st.rerun()
    if cancel.button("Cancel", width="stretch"):
        st.rerun()


def transaction_table(rows: list, scope: str, *, empty: str = "Nothing here yet.") -> None:
    """General transactions as an aligned grid, with the period spelled out."""
    from ledger import spend
    from ledger.money import format_money as _fmt

    just = st.session_state.pop("just_edited", None)
    if just:
        st.success(f"Updated {just}.")
    if not rows:
        st.caption(empty)
        return

    _head_row(_SPEND_HEADS, _SPEND_COLS)

    for t in rows:
        when, amount, category, detail, edit, remove = st.columns(
            _SPEND_COLS, vertical_alignment="center"
        )
        out = t.kind is spend.Kind.spent
        when.markdown(
            f'<div class="khata-cell">{t.period}'
            + ('<span class="khata-src"> ongoing</span>' if t.ongoing else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        amount.markdown(
            f'<div class="khata-cell khata-amount {"khata-out" if out else "khata-back"}">'
            f'{_fmt(t.amount_minor, t.currency)}</div>',
            unsafe_allow_html=True,
        )
        category.markdown(f'<div class="khata-cell">{t.category}</div>', unsafe_allow_html=True)
        bits = " · ".join(x for x in (t.description, t.note) if x) or "—"
        mark = _SOURCE_MARK.get(t.source, "")
        detail.markdown(
            f'<div class="khata-cell khata-meta">{bits}'
            + (f' <span class="khata-src">{mark}</span>' if mark else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        with edit:
            if t.row is not None and st.button("Edit", key=f"tedit_{scope}_{t.row}",
                                               width="stretch"):
                _edit_transaction(t)
        with remove:
            _remove_transaction(t, scope)
        st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)


def _remove_transaction(t, scope: str) -> None:
    """Two clicks, same as the ledger. The sheet has no undo."""
    from ledger import spend

    if t.row is None:
        return
    armed = f"tarm_{scope}_{t.row}"
    if not st.session_state.get(armed):
        if st.button("Delete", key=f"tdel_{scope}_{t.row}", width="stretch"):
            st.session_state[armed] = True
            st.rerun()
        return
    yes, no = st.columns(2)
    if yes.button("Yes", key=f"tyes_{scope}_{t.row}", type="primary", width="stretch"):
        try:
            spend.remove(t)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not delete: {exc}")
            st.session_state[armed] = False
        else:
            st.session_state[armed] = False
            st.toast("Transaction deleted")
            st.rerun()
    if no.button("No", key=f"tno_{scope}_{t.row}", width="stretch"):
        st.session_state[armed] = False
        st.rerun()


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
