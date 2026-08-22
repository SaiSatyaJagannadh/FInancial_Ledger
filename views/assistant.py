"""Chat your entries in. Say what happened; approve what it proposes.

Nothing here writes to the sheet on its own. The model proposes rows, you look
at them, and only a click saves. That is the whole safety story of this page.
"""

from __future__ import annotations

import streamlit as st

from ledger import store
from ledger.assistant import AssistantError, read_image, read_note
from ledger.money import format_money
from ledger.ui import api_key, clear_cache, demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Assistant")
st.caption("Tell me what happened and I'll draft the entry. Nothing is saved until you press Save.")

key = api_key()
if not key:
    st.error(
        "**No NVIDIA API key configured.**\n\n"
        "Open **Manage app → Settings → Secrets** and add this line "
        "**at the very top**, above `[gcp_service_account]`:\n\n"
        "```toml\nNVIDIA_API_KEY = \"nvapi-…\"\n```\n\n"
        "It has to go above every `[section]` heading — in TOML a heading "
        "claims every line beneath it, so a key added at the bottom becomes "
        "part of that section instead of a setting on its own."
    )
    st.stop()

people = sorted({e.person for e in result.entries})
ledgers = sorted({e.ledger for e in result.entries})

if "chat" not in st.session_state:
    st.session_state.chat = [
        {"role": "assistant",
         "text": "Tell me what happened — for example *“I gave 2500 to Vihar today for the "
                 "UK ledger, by UPI”*. I'll work out the person, ledger, amount and "
                 "direction, and show it to you before anything is saved.",
         "drafts": [], "rejected": []},
    ]


def respond(drafts, rejected, source: str) -> None:
    """Add one assistant turn describing what came back."""
    if drafts:
        text = f"Here {'is' if len(drafts) == 1 else 'are'} {len(drafts)} entr" \
               f"{'y' if len(drafts) == 1 else 'ies'} from {source}. Check the figures, then save."
    elif rejected:
        text = f"I read {source} but nothing was usable."
    else:
        text = (f"I couldn't find a person and an amount in {source}. "
                "Try naming who it was and how much, e.g. “gave Vihar 2500 today”.")
    st.session_state.chat.append(
        {"role": "assistant", "text": text, "drafts": drafts, "rejected": rejected}
    )


with st.expander("🖼️  Read a statement or receipt image instead"):
    upload = st.file_uploader(
        "Screenshot of a bank statement, a UPI receipt, a photo of a note",
        type=["png", "jpg", "jpeg", "webp"],
        key="assistant_image",
    )
    if upload is not None:
        st.image(upload, width=320)
        if st.button("Read this image", type="primary"):
            st.session_state.chat.append(
                {"role": "user", "text": f"📎 {upload.name}", "drafts": [], "rejected": []}
            )
            try:
                drafts, rejected = read_image(
                    upload.getvalue(), upload.type or "image/png",
                    api_key=key, people=people, ledgers=ledgers,
                )
                respond(drafts, rejected, "that image")
            except AssistantError as exc:
                st.session_state.chat.append(
                    {"role": "assistant", "text": f"⚠️ {exc}", "drafts": [], "rejected": []}
                )
            st.rerun()

for index, turn in enumerate(st.session_state.chat):
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])

        for problem in turn.get("rejected") or []:
            st.warning(f"Skipped — {problem}")

        for slot, draft in enumerate(turn.get("drafts") or []):
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
                    if st.button("Save", key=f"save_{index}_{slot}", type="primary"):
                        try:
                            store.append(entry)
                        except Exception as exc:  # noqa: BLE001 — surface what the sheet said
                            st.error(f"Could not save: {type(exc).__name__}: {exc}")
                        else:
                            clear_cache()
                            st.session_state.chat[index]["drafts"] = [
                                d for i, d in enumerate(turn["drafts"]) if i != slot
                            ]
                            st.session_state.chat.append({
                                "role": "assistant",
                                "text": f"✅ Saved **{format_money(entry.amount_minor, entry.currency)}** "
                                        f"for **{entry.person}** on *{entry.ledger}*. "
                                        "It's on the dashboard now.",
                                "drafts": [], "rejected": [],
                            })
                            st.rerun()

message = st.chat_input("e.g. I gave 2500 to Vihar today for the UK ledger, by UPI")
if message:
    st.session_state.chat.append(
        {"role": "user", "text": message, "drafts": [], "rejected": []}
    )
    try:
        drafts, rejected = read_note(
            message, api_key=key, people=people, ledgers=ledgers
        )
        respond(drafts, rejected, "that")
    except AssistantError as exc:
        st.session_state.chat.append(
            {"role": "assistant", "text": f"⚠️ {exc}", "drafts": [], "rejected": []}
        )
    st.rerun()

if len(st.session_state.chat) > 1 and st.button("Clear chat"):
    del st.session_state.chat
    st.rerun()
