"""Chat your entries in. Say what happened; approve what it proposes.

Nothing here writes to the sheet on its own. The model proposes rows, you look
at them, and only a click saves. That is the whole safety story of this page.
"""

from __future__ import annotations

import streamlit as st

from ledger import docs, store
from dataclasses import replace

from ledger.assistant import AssistantError, read_image, read_note, summarise
from ledger.models import BY_CHAT, BY_IMAGE, Direction, EntryError
from ledger.money import to_minor
from ledger.money import format_money
from ledger.ui import api_key, clear_cache, demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Assistant")
st.caption(
    "Tell me what happened and I'll draft the entry. I ask when I'm unsure "
    "rather than guessing, and nothing is saved until you press Save."
)

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

# Which ledgers each person actually keeps, so a proposed ledger can be snapped
# to the real one instead of a new one named after them.
person_ledgers: dict[str, list[str]] = {}
for _entry in result.entries:
    person_ledgers.setdefault(_entry.person, [])
    if _entry.ledger not in person_ledgers[_entry.person]:
        person_ledgers[_entry.person].append(_entry.ledger)

if "chat" not in st.session_state:
    st.session_state.chat = [
        {"role": "assistant",
         "text": "Tell me what happened — for example *“I gave 2500 to Vihar today for the "
                 "UK ledger, by UPI”*. I'll work out the person, ledger, amount and "
                 "direction, and show it to you before anything is saved. "
                 "If I'm not sure about something I'll ask rather than guess, and "
                 "I'll remember instructions like *“put these all under one person”*.",
         "drafts": [], "rejected": []},
    ]


def respond(reply, source: str, via: str = BY_CHAT) -> None:
    """Add one assistant turn: proposed entries, or a question back."""
    drafts, rejected = reply.drafts, reply.rejected
    if reply.answer:
        text = reply.answer
    elif reply.question:
        text = reply.question
    elif drafts:
        text = f"Here {'is' if len(drafts) == 1 else 'are'} {len(drafts)} entr" \
               f"{'y' if len(drafts) == 1 else 'ies'} from {source}. Check the figures, then save."
    elif rejected:
        text = f"I read {source} but nothing was usable."
    else:
        text = (f"I couldn't make an entry out of {source}. "
                "Tell me who it involves and how much.")
    st.session_state.chat.append({
        "role": "assistant", "text": text, "drafts": drafts,
        "rejected": rejected, "via": via,
    })


def history() -> list[dict]:
    """The conversation so far, for the model.

    Sent in full so a standing instruction — "put these all under Nanna", "the
    names I mention are for the note" — still applies several turns later.
    """
    return [
        {"role": turn["role"], "content": turn["text"]}
        for turn in st.session_state.chat[1:]  # skip the canned greeting
    ]


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
                reply = read_image(
                    upload.getvalue(), upload.type or "image/png",
                    api_key=key, people=people, ledgers=ledgers,
                )
                respond(reply, "that image", via=BY_IMAGE)
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

        # More than one entry from one document almost always belongs to the
        # same person and ledger. Ask once here rather than making it a fix on
        # every row.
        pending = turn.get("drafts") or []
        if len(pending) > 1 and people:
            with st.container(border=True):
                st.markdown("**File all of these under one person?**")
                who_col, book_col, go_col = st.columns([2, 2, 1])
                bulk_person = who_col.selectbox(
                    "Person", ["Leave as proposed", *people], key=f"bp_{index}",
                    label_visibility="collapsed",
                )
                bulk_ledger = book_col.selectbox(
                    "Ledger", ["Leave as proposed", *ledgers], key=f"bl_{index}",
                    label_visibility="collapsed",
                )
                if go_col.button("Apply", key=f"ba_{index}"):
                    for slot in range(len(pending)):
                        current = st.session_state.get(
                            f"tweak_{index}_{slot}", pending[slot].entry
                        )
                        changes = {}
                        if bulk_person != "Leave as proposed":
                            changes["person"] = bulk_person
                        if bulk_ledger != "Leave as proposed":
                            changes["ledger"] = bulk_ledger
                        if changes:
                            st.session_state[f"tweak_{index}_{slot}"] = replace(
                                current, **changes
                            )
                    st.rerun()
                st.caption(
                    "A statement usually holds one person's transactions. "
                    "Set them all at once, then check each amount."
                )

        for slot, draft in enumerate(turn.get("drafts") or []):
            entry = draft.entry
            tweak = st.session_state.get(f"tweak_{index}_{slot}")
            if tweak is not None:
                entry = tweak
            arrow = "→ out" if entry.signed_minor > 0 else "← back"
            with st.container(border=True):
                left, middle, right = st.columns([4, 1, 1])
                with left:
                    st.markdown(
                        f"**{format_money(entry.amount_minor, entry.currency)}** {arrow} · "
                        f"**{entry.person}** · {entry.ledger}  \n"
                        f"{entry.date:%d %b %Y}"
                        + (f" · _{entry.note}_" if entry.note else "")
                    )

                with st.expander("Change something before saving"):
                    fix_who, fix_book = st.columns(2)
                    new_person = fix_who.text_input(
                        "Person", value=entry.person, key=f"p_{index}_{slot}")
                    new_ledger = fix_book.text_input(
                        "Ledger", value=entry.ledger, key=f"l_{index}_{slot}")
                    fix_when, fix_dir, fix_amt = st.columns([1.2, 1.3, 1.2])
                    new_date = fix_when.date_input(
                        "Date", value=entry.date, format="DD/MM/YYYY", key=f"d_{index}_{slot}")
                    new_dir = fix_dir.radio(
                        "Direction", list(Direction),
                        index=list(Direction).index(entry.direction),
                        format_func=lambda d: "I gave them" if d is Direction.given
                        else "They gave me back",
                        key=f"dir_{index}_{slot}")
                    new_amount = fix_amt.text_input(
                        f"Amount ({entry.currency.symbol})",
                        value=f"{entry.amount_minor / 100:.2f}", key=f"a_{index}_{slot}")
                    new_note = st.text_input(
                        "Note", value=entry.note, key=f"n_{index}_{slot}")

                    try:
                        fixed = replace(
                            entry, person=new_person.strip(), ledger=new_ledger.strip(),
                            date=new_date, direction=new_dir,
                            amount_minor=to_minor(new_amount), note=new_note.strip(),
                        )
                    except (ValueError, EntryError) as exc:
                        st.error(str(exc))
                    else:
                        if fixed.to_row() != entry.to_row():
                            st.session_state[f"tweak_{index}_{slot}"] = fixed
                            st.rerun()

                with middle:
                    if st.button("Discard", key=f"drop_{index}_{slot}"):
                        st.session_state.chat[index]["drafts"] = [
                            d for i, d in enumerate(turn["drafts"]) if i != slot
                        ]
                        st.rerun()
                with right:
                    if st.button("Save", key=f"save_{index}_{slot}", type="primary"):
                        try:
                            store.append(replace(entry, source=turn.get("via", BY_CHAT)))
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
        reply = read_note(
            history(), api_key=key, people=people, ledgers=ledgers,
            person_ledgers=person_ledgers, summary=summarise(result.entries),
        )
        respond(reply, "that")
    except AssistantError as exc:
        st.session_state.chat.append(
            {"role": "assistant", "text": f"⚠️ {exc}", "drafts": [], "rejected": []}
        )
    st.rerun()

if len(st.session_state.chat) > 1 and st.button("Clear chat"):
    del st.session_state.chat
    st.rerun()
