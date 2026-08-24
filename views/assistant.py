"""Chat your entries in. Say what happened; approve what it proposes.

Nothing here writes to the sheet on its own. The model proposes rows, you look
at them, and only a click saves. That is the whole safety story of this page.
"""

from __future__ import annotations

import streamlit as st

from ledger import docs, facts, interest, people as grouping, store
from dataclasses import replace

from ledger.assistant import read_image, read_note, summarise
from ledger.models import BY_CHAT, BY_IMAGE, Direction, EntryError
from ledger.money import format_money, to_minor
from ledger.ui import api_key, clear_cache, demo_banner, load_ledger, styles

styles()

result = load_ledger()
demo_banner(result)

st.title("Assistant")
st.caption(
    "Tell me what happened and I'll draft the entry — I ask when I'm unsure "
    "rather than guessing, and nothing is saved until you press Save. "
    "**Questions about the ledger are answered instantly**, by adding your "
    "sheet up rather than asking a model."
)

# No key is not the end of the page. Questions about the ledger are answered
# by adding the sheet up, which needs nothing but the sheet — only *drafting*
# an entry out of a sentence needs the model. Stopping here used to take the
# working half down with the missing half.
key = api_key()
NO_KEY_NOTE = (
    "**Drafting entries needs an NVIDIA API key.**\n\n"
    "Open **Manage app → Settings → Secrets** and add this line "
    "**at the very top**, above `[gcp_service_account]`:\n\n"
    "```toml\nNVIDIA_API_KEY = \"nvapi-…\"\n```\n\n"
    "It has to go above every `[section]` heading — in TOML a heading "
    "claims every line beneath it, so a key added at the bottom becomes "
    "part of that section instead of a setting on its own."
)
if not key:
    st.info(
        "No API key configured, so I can't turn a sentence into an entry yet — "
        "but **questions about your ledger still work**. Those are added up "
        "from the sheet, not generated, so they need no model at all. Try "
        "*“who owes me the most?”*"
    )

people = sorted({e.person for e in result.entries})
ledgers = sorted({e.ledger for e in result.entries})

# Interest and groupings are loaded so the assistant can answer about them and
# propose changes to them — never so it can add them to a ledger total.
charges, _charge_problems = interest.load()
members, _member_problems = grouping.load()
parents = grouping.mapping(members)

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
         "text": "Two things I can do.\n\n"
                 "**Add an entry** — say *“I gave 2500 to Ravi today for the UK "
                 "ledger, by UPI”* and I'll work out the person, ledger, amount "
                 "and direction, and show it to you before anything is saved. If "
                 "I'm not sure I'll ask rather than guess, and I'll remember "
                 "instructions like *“put these all under one person”*.\n\n"
                 "**Answer a question** — *“who owes me the most?”*, *“how much "
                 "does Ravi owe me?”*, *“which ledgers are still open?”* Those "
                 "come back instantly, added up from the sheet rather than "
                 "written by a model, so the figures are exact.",
         "drafts": [], "rejected": []},
    ]


def respond(reply, source: str, via: str = BY_CHAT) -> None:
    """Add one assistant turn: proposed entries, or a question back."""
    drafts, rejected = reply.drafts, reply.rejected
    if reply.charges:
        text = (f"That reads as **interest**, not a ledger entry, so it will be "
                f"saved to the Interest page and stay out of your totals. "
                f"Check the {'figure' if len(reply.charges) == 1 else 'figures'}, "
                f"then save.")
    elif reply.groupings:
        text = "That reads as a **grouping**. Nothing moves in the ledger — only how the totals roll up."
    elif reply.answer:
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
        "charges": list(reply.charges), "groupings": list(reply.groupings),
    })


def history() -> list[dict]:
    """The conversation so far, for the model.

    Sent in full so a standing instruction — "put these all under Amma", "the
    names I mention are for the note" — still applies several turns later.
    """
    return [
        {"role": turn["role"], "content": turn["text"]}
        for turn in st.session_state.chat[1:]  # skip the canned greeting
    ]


with st.expander("📄  Read a statement, spreadsheet or photo"):
    st.caption(
        "PDF, Excel, CSV or an image. A document is read as text, which is far "
        "more accurate than reading a picture of it. Large photos are shrunk "
        "automatically rather than refused."
    )
    upload = st.file_uploader(
        "Bank statement, UPI receipt, spreadsheet, photo of a note",
        type=docs.ACCEPTED,
        key="assistant_doc",
        disabled=not key,
    )
    if not key:
        st.caption("Reading a document is the model's job, so this one needs the key.")
    if upload is not None and key:
        if docs.suffix_of(upload.name) in docs.IMAGE_SUFFIXES:
            st.image(upload, width=320)
        else:
            st.caption(f"{upload.name} · {len(upload.getvalue()) // 1024} KB")

        if st.button("Read this", type="primary"):
            st.session_state.chat.append(
                {"role": "user", "text": f"📄 {upload.name}", "drafts": [], "rejected": []}
            )
            with st.chat_message("assistant"), st.spinner(f"Reading {upload.name}…"):
                try:
                    readable = docs.read(
                        upload.name, upload.getvalue(), upload.type or ""
                    )
                    if readable.note:
                        st.session_state.chat.append({
                            "role": "assistant", "text": f"_{readable.note}_",
                            "drafts": [], "rejected": [],
                        })
                    if readable.kind == "image":
                        reply = read_image(
                            readable.data, readable.mimetype,
                            api_key=key, people=people, ledgers=ledgers,
                            person_ledgers=person_ledgers,
                            entries=result.entries,
                        )
                        via = BY_IMAGE
                    else:
                        reply = read_note(
                            [{"role": "user",
                              "content": f"These are the contents of {upload.name}. "
                                         "Pull out every money transfer where I lent "
                                         "money out or was repaid.\n\n" + readable.text}],
                            api_key=key, people=people, ledgers=ledgers,
                            person_ledgers=person_ledgers,
                            summary=summarise(result.entries, charges, parents),
                            entries=result.entries,
                        )
                        via = BY_IMAGE
                    respond(reply, f"**{upload.name}**", via=via)
                except Exception as exc:  # noqa: BLE001 — nothing may reach the page
                    st.session_state.chat.append({
                        "role": "assistant", "text": f"⚠️ {exc}",
                        "drafts": [], "rejected": [],
                    })
            st.rerun()

for index, turn in enumerate(st.session_state.chat):
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])

        # Worth saying out loud which figures were added up and which were
        # written by a model. Only one of the two can be off by a digit.
        if turn.get("computed"):
            st.caption("⚡ Added up from your sheet — no model involved.")

        for problem in turn.get("rejected") or []:
            st.warning(f"Skipped — {problem}")

        # Interest saves to its own tab. Kept in a separate list from `drafts`
        # so no code path can reach for the wrong one and put a charge in the
        # ledger — the thing this whole feature exists to prevent.
        for slot, charge in enumerate(turn.get("charges") or []):
            with st.container(border=True):
                detail, save = st.columns([4, 1])
                rate = f" · at {charge.rate_percent:g}%" if charge.rate_percent else ""
                detail.markdown(
                    f"**{charge.money()}** interest · **{charge.person}** · "
                    f"{charge.month_label}{rate}"
                    + (f"  \n_{charge.note}_" if charge.note else "")
                )
                detail.caption("Goes to the Interest page. Never added to the ledger.")
                if save.button("Save", key=f"csave_{index}_{slot}", type="primary",
                               width="stretch"):
                    clash = interest.already_charged(
                        charges, charge.person, charge.date, charge.currency
                    )
                    if clash:
                        st.warning(
                            f"{charge.person} already has {clash.money()} recorded "
                            f"for {clash.month_label}. Remove that one first."
                        )
                    else:
                        try:
                            interest.add(charge)
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Could not save: {exc}")
                        else:
                            st.session_state.chat[index]["charges"] = [
                                c for i, c in enumerate(turn["charges"]) if i != slot
                            ]
                            st.session_state.chat.append({
                                "role": "assistant",
                                "text": f"✅ Recorded {charge.money()} interest for "
                                        f"**{charge.person}**, {charge.month_label}. "
                                        "It is on the Interest page, not the ledger.",
                                "drafts": [], "rejected": [],
                            })
                            st.rerun()

        # A grouping is a draft like everything else here: it is shown, it can
        # be corrected, and only then can it be applied. Saving straight from
        # what the model read means a misheard name silently regroups somebody.
        ON_OWN = "— on their own —"
        for slot, (person, under) in enumerate(turn.get("groupings") or []):
            tweak = st.session_state.get(f"gtweak_{index}_{slot}")
            if tweak is not None:
                person, under = tweak

            with st.container(border=True):
                st.markdown(
                    f"**{person}** comes under **{under}**" if under
                    else f"**{person}** goes back to being on their own"
                )
                st.caption(
                    "Draft — nothing has changed yet. Check the names, then apply. "
                    "Only the totals roll up; no entry moves."
                )

                with st.expander("Change something before applying"):
                    who_col, under_col = st.columns(2)
                    fixed_person = who_col.selectbox(
                        "Person", people,
                        index=people.index(person) if person in people else 0,
                        key=f"gp_{index}_{slot}",
                    )
                    options = [ON_OWN, *[p for p in people if p != fixed_person]]
                    current = under if under in options else ON_OWN
                    fixed_under = under_col.selectbox(
                        "Comes under", options,
                        index=options.index(current),
                        key=f"gu_{index}_{slot}",
                    )
                    chosen = (fixed_person, "" if fixed_under == ON_OWN else fixed_under)
                    if chosen != (person, under):
                        st.session_state[f"gtweak_{index}_{slot}"] = chosen
                        st.rerun()

                discard, apply = st.columns([1, 1])
                if discard.button("Discard", key=f"gdrop_{index}_{slot}",
                                  width="stretch"):
                    st.session_state.chat[index]["groupings"] = [
                        g for i, g in enumerate(turn["groupings"]) if i != slot
                    ]
                    st.rerun()
                if apply.button("Apply", key=f"gsave_{index}_{slot}", type="primary",
                                width="stretch"):
                    try:
                        grouping.set_parent(person, under)
                    except Exception as exc:  # noqa: BLE001 — say what went wrong
                        st.error(str(exc))
                    else:
                        st.session_state.chat[index]["groupings"] = [
                            g for i, g in enumerate(turn["groupings"]) if i != slot
                        ]
                        st.session_state.chat.append({
                            "role": "assistant",
                            "text": (f"✅ **{person}** now comes under **{under}**."
                                     if under else
                                     f"✅ **{person}** is on their own again."),
                            "drafts": [], "rejected": [],
                        })
                        st.rerun()

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

def ask(text: str) -> None:
    """Answer one message: from the sheet if we can, from the model otherwise.

    Anything that goes wrong becomes a chat turn with the text kept for a
    retry. An exception reaching the page is a red traceback, which tells the
    reader nothing and loses what they typed.
    """
    # Most questions put to a ledger are arithmetic, and arithmetic has a right
    # answer that `compute.py` already knows. Answering here costs no network
    # call and cannot be off by a digit. `facts.answer` returns None whenever it
    # is not certain, and then this falls through to the model unchanged.
    computed = facts.answer(text, result.entries, parents=parents)
    if computed:
        st.session_state.chat.append({
            "role": "assistant", "text": computed, "drafts": [], "rejected": [],
            "computed": True,
        })
        return

    if not key:
        st.session_state.chat.append({
            "role": "assistant", "text": NO_KEY_NOTE, "drafts": [], "rejected": [],
        })
        return

    try:
        reply = read_note(
            history(), api_key=key, people=people, ledgers=ledgers,
            person_ledgers=person_ledgers, summary=summarise(result.entries, charges, parents),
                            entries=result.entries,
        )
    except Exception as exc:  # noqa: BLE001 — nothing may reach the page
        st.session_state.chat.append({
            "role": "assistant", "text": f"⚠️ {exc}",
            "drafts": [], "rejected": [], "failed": text,
        })
    else:
        respond(reply, "that")


message = st.chat_input("e.g. I gave 2500 to Ravi today for the UK ledger, by UPI")
if message:
    st.session_state.chat.append(
        {"role": "user", "text": message, "drafts": [], "rejected": []}
    )
    # Draw the message before the model is called. Everything below is written
    # to session state and only rendered on the rerun, so without this the page
    # sits unchanged for several seconds — not even showing what was typed.
    with st.chat_message("user"):
        st.markdown(message)
    with st.chat_message("assistant"), st.spinner("Reading that…"):
        ask(message)
    st.rerun()

# A failed turn keeps its text, so a blip costs a click rather than a retype.
last = st.session_state.chat[-1] if st.session_state.chat else {}
if last.get("failed"):
    again, drop = st.columns([1, 4])
    if again.button("Try again", type="primary"):
        text = last["failed"]
        st.session_state.chat.pop()          # the failure notice
        with st.chat_message("assistant"), st.spinner("Trying again…"):
            ask(text)
        st.rerun()
    drop.caption("Nothing was saved. Your message is kept — press to send it again.")

if len(st.session_state.chat) > 1 and st.button("Clear chat"):
    del st.session_state.chat
    st.rerun()
