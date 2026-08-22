"""Describe a transaction, or upload a statement, and let the model draft it.

Nothing here writes to the sheet on its own. The model proposes rows, you look
at them, and only a click saves. That is the whole safety story of this page.
"""

from __future__ import annotations

import streamlit as st

from ledger import store
from ledger.assistant import AssistantError, read_image, read_note
from ledger.money import format_money
from ledger.ui import api_key, clear_cache, demo_banner, load_ledger, page_config

page_config("Assistant")

result = load_ledger()
demo_banner(result)

st.title("Assistant")
st.caption("Say what happened in plain words, or upload a statement. You approve before anything is saved.")

key = api_key()
if not key:
    st.error(
        "**No NVIDIA API key configured.** Add this to your Streamlit secrets "
        "(Manage app → Settings → Secrets) and reload:\n\n"
        "```toml\nNVIDIA_API_KEY = \"nvapi-…\"\n```"
    )
    st.stop()

people = sorted({e.person for e in result.entries})
ledgers = sorted({e.ledger for e in result.entries})

note_tab, image_tab = st.tabs(["💬  Describe it", "🖼️  Read an image"])

with note_tab:
    st.caption(
        "Dictation works here — press the mic on your phone keyboard, or ⌃⌃ on a Mac, "
        "and just talk."
    )
    text = st.text_area(
        "What happened?",
        placeholder="I gave 5000 to my nanna today for the house",
        height=110,
        key="assistant_note",
    )
    if st.button("Read this", type="primary", disabled=not text.strip()):
        with st.spinner("Reading…"):
            try:
                drafts, rejected = read_note(
                    text, api_key=key, people=people, ledgers=ledgers
                )
                st.session_state["drafts"] = drafts
                st.session_state["rejected"] = rejected
            except AssistantError as exc:
                st.error(str(exc))

with image_tab:
    st.caption("A screenshot of a bank statement, a UPI receipt, a photo of a note.")
    upload = st.file_uploader(
        "Statement or receipt", type=["png", "jpg", "jpeg", "webp"], key="assistant_image"
    )
    if upload is not None:
        st.image(upload, width=340)
        if st.button("Read this image", type="primary"):
            with st.spinner("Reading the image…"):
                try:
                    drafts, rejected = read_image(
                        upload.getvalue(), upload.type or "image/png",
                        api_key=key, people=people, ledgers=ledgers,
                    )
                    st.session_state["drafts"] = drafts
                    st.session_state["rejected"] = rejected
                except AssistantError as exc:
                    st.error(str(exc))

drafts = st.session_state.get("drafts") or []
rejected = st.session_state.get("rejected") or []

for problem in rejected:
    st.warning(f"Skipped — {problem}")

if not drafts:
    st.stop()

st.divider()
st.subheader(f"{len(drafts)} proposed {'entry' if len(drafts) == 1 else 'entries'}")
st.caption("Check every figure against what you actually did before saving.")

for index, draft in enumerate(drafts):
    entry = draft.entry
    arrow = "→ out" if entry.signed_minor > 0 else "← back"
    with st.container(border=True):
        left, right = st.columns([4, 1])
        with left:
            st.markdown(
                f"**{format_money(entry.amount_minor, entry.currency)}** {arrow} · "
                f"**{entry.person}** · {entry.ledger}  \n"
                f"{entry.date:%d %b %Y}"
                + (f" · _{entry.note}_" if entry.note else "")
            )
        with right:
            if st.button("Save", key=f"save_{index}", type="primary"):
                try:
                    store.append(entry)
                except Exception as exc:  # noqa: BLE001 — surface anything the sheet says
                    st.error(f"Could not save: {type(exc).__name__}: {exc}")
                else:
                    clear_cache()
                    st.session_state["drafts"] = [
                        d for i, d in enumerate(drafts) if i != index
                    ]
                    st.success(f"Saved {entry.person}.")
                    st.rerun()

if st.button("Discard all"):
    st.session_state["drafts"] = []
    st.session_state["rejected"] = []
    st.rerun()
