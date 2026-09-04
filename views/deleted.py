"""What has been deleted, and a way to put it back.

Deleting asks twice, but a spreadsheet has no undo and the second click is
still a click. Every removal from the ledger and the interest tab is archived
before the row goes, so this page is the record of what was taken out — when,
by whom when there is a sign-in, and exactly what it said.
"""

from __future__ import annotations

import streamlit as st

from ledger import archive
from ledger.ui import clear_cache, demo_banner, esc, load_ledger, styles

ANY_KIND = "Everything"
KINDS = {archive.ENTRY: "Ledger entries", archive.INTEREST: "Interest charges"}

styles()

result = load_ledger()
demo_banner(result)

st.title("Deleted")
st.caption(
    "Everything removed from the ledger or the interest tab, newest first. "
    "Restoring writes the record back as a new row."
)

if result.demo:
    st.info("Demo mode keeps no archive — there is no sheet to delete from.")
    st.stop()

gone, problems = archive.load()
for problem in problems:
    st.warning(problem)

if not gone:
    st.success("Nothing has been deleted.")
    st.caption(
        "When something is, it will be kept here rather than disappearing — "
        "the sheet itself has no undo."
    )
    st.stop()

if st.session_state.pop("restored", None):
    st.success(st.session_state.pop("restored_text", "Restored."))

kind_col, who_col, search_col = st.columns([1.4, 1.4, 2])
with kind_col:
    kind = st.selectbox(
        "Kind", [ANY_KIND, *KINDS],
        format_func=lambda k: KINDS.get(k, k),
    )
with who_col:
    people = sorted({d.by for d in gone if d.by})
    who = st.selectbox(
        "Deleted by", ["Anyone", *people, "(not signed in)"],
        help="Only recorded when sign-in is configured.",
    )
with search_col:
    search = st.text_input("Search", placeholder="A name, an amount, a note")

shown = gone
if kind != ANY_KIND:
    shown = [d for d in shown if d.kind == kind]
if who == "(not signed in)":
    shown = [d for d in shown if not d.by]
elif who != "Anyone":
    shown = [d for d in shown if d.by == who]
if search.strip():
    needle = search.strip().lower()
    shown = [
        d for d in shown
        if needle in d.summary.lower() or needle in " ".join(d.data).lower()
    ]

count_col, kinds_col, last_col = st.columns(3)
count_col.metric("Showing", f"{len(shown)} of {len(gone)}")
kinds_col.metric(
    "Ledger / interest",
    f"{sum(1 for d in shown if d.kind == archive.ENTRY)}"
    f" / {sum(1 for d in shown if d.kind == archive.INTEREST)}",
)
last_col.metric("Most recent", gone[0].when if gone else "—")

st.divider()

if not shown:
    st.info("Nothing matches those filters.")
    st.stop()

for item in shown:
    line, restore = st.columns([7, 1.6], vertical_alignment="center")
    with line:
        st.markdown(
            f'<div class="khata-row">'
            f'<span class="khata-amount khata-out">{esc(item.summary or "—")}</span>'
            f'<span class="khata-dir">{esc(KINDS.get(item.kind, item.kind))}</span>'
            f'<span class="khata-meta">· deleted {esc(item.when)}'
            + (f" · by {esc(item.by)}" if item.by else " · sign-in was off")
            + (f" · was row {item.source_row}" if item.source_row else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )
        with st.expander("The row as it was"):
            st.code("\n".join(str(cell) for cell in item.data) or "(nothing recorded)")

    with restore:
        armed = f"restore_{item.row}"
        if not st.session_state.get(armed):
            if st.button("Restore", key=f"r_{item.row}", width="stretch",
                         help="Write this record back as a new row"):
                st.session_state[armed] = True
                st.rerun()
        else:
            yes, no = st.columns(2)
            if yes.button("Yes", key=f"ry_{item.row}", type="primary",
                          width="stretch"):
                try:
                    back = archive.restore(item)
                except Exception as exc:  # noqa: BLE001 — say what the sheet said
                    st.error(f"Could not restore: {exc}")
                    st.session_state[armed] = False
                else:
                    clear_cache()
                    st.session_state[armed] = False
                    st.session_state["restored"] = True
                    st.session_state["restored_text"] = (
                        f"Restored {getattr(back, 'person', 'the record')} — "
                        "it is back on the sheet as a new row."
                    )
                    st.rerun()
            if no.button("No", key=f"rn_{item.row}", width="stretch"):
                st.session_state[armed] = False
                st.rerun()
    st.markdown('<hr class="khata-rule">', unsafe_allow_html=True)

st.divider()
st.caption(
    "A restored record comes back as a **new row at the bottom**, not in its old "
    "position — everything below it moved up when it was removed, so the old row "
    "number no longer means anything."
)
