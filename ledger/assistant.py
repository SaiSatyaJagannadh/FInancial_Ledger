"""Turn a sentence or a statement image into a draft ledger entry.

The model never writes to the sheet. It proposes; `Entry.from_row` validates;
a human confirms. That ordering is deliberate — a model misreading "5,000" as
"50,000" must not be able to land in a ledger of real debts on its own.

Talks to NVIDIA's OpenAI-compatible endpoint over plain HTTP, so there is no
vendor SDK to install for what is one POST.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import date

from ledger.models import COLUMNS, Entry, EntryError

BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

TEXT_MODEL = "meta/llama-3.1-70b-instruct"
VISION_MODEL = "meta/llama-3.2-90b-vision-instruct"

#: Vision requests inline the image as a data URI, which NVIDIA caps well below
#: the sheet's own attachment limit.
MAX_IMAGE_BYTES = 180 * 1024

_SCHEMA = """Reply with ONLY a JSON object, no prose, no markdown fence.

When you are confident, propose entries:
{"entries": [{"date": "YYYY-MM-DD", "person": "...", "ledger": "...",
"direction": "given" | "received", "amount": "1234.56",
"currency": "INR" | "USD", "note": "..."}]}

When anything essential is missing or ambiguous, ASK INSTEAD:
{"question": "one short question"}

When the user is ASKING ABOUT the ledger rather than recording something —
"how much does Vihar owe me", "what did I give this year", "give me a brief" —
answer from the figures in the summary below:
{"answer": "a short, direct answer"}
Quote the figures exactly as the summary gives them. Never estimate a total
that is not in the summary, and never invent a person who is not listed.

Rules:
- "given" = the user handed money out. "received" = money came back to them.
- amount is always POSITIVE. Direction carries the sign.
- If a date is not stated, use today's date.
- If the currency is not stated, use INR.
- Reuse an existing person or ledger name EXACTLY when the text clearly refers
  to one.

ASK a question rather than guessing when:
- you cannot tell WHO the money involves, or HOW MUCH;
- a name could be the person OR just detail for the note, and you are unsure;
- the person is new and does not match any existing name closely;
- the message says several things and you cannot tell how many entries it is.

Obey standing instructions the user has given earlier in this conversation —
for example "put all of these under one person", or "the names I mention are
for the note, not the person". Those instructions outrank your own guess.

Never invent an amount, a person, or a date you did not read. One good
question is always better than a wrong entry."""


@dataclass(frozen=True)
class Reply:
    """What came back: entries, a question, an answer, or nothing usable."""

    drafts: list
    rejected: list[str]
    question: str = ""
    answer: str = ""

    def __iter__(self):
        """Unpack as (drafts, rejected) so older call sites keep working."""
        return iter((self.drafts, self.rejected))


@dataclass(frozen=True)
class Draft:
    """A proposed entry plus what the model claimed, for display before saving."""

    entry: Entry
    raw: dict


class AssistantError(RuntimeError):
    """The model could not be reached, or gave nothing usable."""


def summarise(entries: list) -> str:
    """The ledger as a few lines of plain figures, for answering questions.

    Computed here rather than left to the model: a total it works out itself is
    a total that can be wrong, and being confidently wrong about money is the
    one thing this must not do.
    """
    if not entries:
        return "The ledger is empty."

    from ledger.compute import by_person, totals
    from ledger.money import Currency, format_money

    lines: list[str] = []
    for currency in Currency:
        subset = [e for e in entries if e.currency is currency]
        if not subset:
            continue
        overall = totals(subset, currency)
        lines.append(
            f"{currency.label}: given {format_money(overall.given_minor, currency)}, "
            f"received {format_money(overall.received_minor, currency)}, "
            f"still owed {format_money(overall.net_minor, currency)}, "
            f"across {overall.records} entries."
        )
        for summary in by_person(subset, currency):
            lines.append(
                f"  - {summary.person}: owes "
                f"{format_money(summary.net_minor, currency)} "
                f"(given {format_money(summary.given_minor, currency)}, "
                f"received {format_money(summary.received_minor, currency)}), "
                f"last activity {summary.last_activity:%d %b %Y}."
            )
    return "\n".join(lines)


def _context(
    people: list[str], ledgers: list[str], today: date, summary: str = ""
) -> str:
    known = ""
    if people:
        known += f"\nExisting people: {', '.join(sorted(set(people))[:40])}"
    if ledgers:
        known += f"\nExisting ledgers: {', '.join(sorted(set(ledgers))[:40])}"
    figures = f"\n\nCurrent ledger:\n{summary}" if summary else ""
    return f"Today is {today.isoformat()}.{known}{figures}\n\n{_SCHEMA}"


def canonical(name: str, known: list[str]) -> str:
    """Snap a proposed name onto an existing one when it plainly means it.

    The model writes "VIHAR" for a person recorded as "VIHAR DVM", and a ledger
    that differs only by case fragments the ledger into two. Prompting alone
    does not reliably fix this, so the match is made here where it can be
    tested. Only unambiguous matches snap: an abbreviation that fits two
    existing names is left alone rather than guessed at.
    """
    cleaned = name.strip()
    if not cleaned or not known:
        return cleaned

    folded = cleaned.casefold()
    for candidate in known:
        if candidate.casefold() == folded:
            return candidate

    # A leading word-run of exactly one known name: "VIHAR" -> "VIHAR DVM".
    starts = [c for c in known if c.casefold().startswith(folded + " ")]
    if len(starts) == 1:
        return starts[0]

    # Or the other way round: "VIHAR DVM SIR" offered for "VIHAR DVM".
    within = [c for c in known if folded.startswith(c.casefold() + " ")]
    if len(within) == 1:
        return within[0]

    return cleaned


def _post(payload: dict, api_key: str, timeout: int) -> str:
    import requests

    response = requests.post(
        BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise AssistantError(f"NVIDIA API error {response.status_code}: {response.text[:300]}")
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise AssistantError(f"Unexpected response shape: {exc}") from exc


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a reply that may be wrapped in prose or fences."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AssistantError(f"No JSON in the reply: {text[:200]}")
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssistantError(f"Reply was not valid JSON: {exc}") from exc


def _to_reply(
    payload: dict,
    people: list[str] | None = None,
    ledgers: list[str] | None = None,
    person_ledgers: dict[str, list[str]] | None = None,
) -> Reply:
    """Validate the model's rows through the same door a sheet row goes through."""
    question = str(payload.get("question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if answer and not payload.get("entries"):
        return Reply([], [], answer=answer)
    if question and not payload.get("entries"):
        return Reply([], [], question=question)

    drafts: list[Draft] = []
    rejected: list[str] = []
    for raw in payload.get("entries") or []:
        if not isinstance(raw, dict):
            rejected.append(f"not an object: {raw!r}")
            continue
        row = {key: str(raw.get(key, "") or "") for key in COLUMNS if key != "attachment"}
        row["person"] = canonical(row["person"], people or [])
        row["ledger"] = canonical(row["ledger"], ledgers or [])
        # A ledger that matches nothing known, for a person who keeps exactly
        # one, is far more likely to be that ledger than a brand new one named
        # after the person. You still see it before it saves.
        owned = (person_ledgers or {}).get(row["person"], [])
        if len(owned) == 1 and row["ledger"] not in (ledgers or []):
            row["ledger"] = owned[0]
        try:
            drafts.append(Draft(entry=Entry.from_row(row), raw=raw))
        except EntryError as exc:
            rejected.append(f"{raw.get('person') or 'entry'}: {exc}")
    # Entries win over a question: if the model proposed rows it has decided,
    # and showing a question beside them would just be noise.
    return Reply(drafts, rejected, question="" if drafts else question)


def read_note(
    text: str | list[dict],
    *,
    api_key: str,
    people: list[str] | None = None,
    ledgers: list[str] | None = None,
    person_ledgers: dict[str, list[str]] | None = None,
    summary: str = "",
    today: date | None = None,
    model: str = TEXT_MODEL,
    timeout: int = 60,
) -> Reply:
    """Read a note, or a whole conversation, into draft entries or a question.

    Pass the conversation rather than one line when you have it: an instruction
    given three messages ago ("put these all under Nanna") has to still apply
    now, and it only can if the model can still see it.
    """
    history = (
        [{"role": "user", "content": text.strip()}]
        if isinstance(text, str)
        else [m for m in text if str(m.get("content", "")).strip()]
    )
    if not history:
        return Reply([], [])

    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 900,
        "messages": [
            {"role": "system",
             "content": "You keep a personal-lending ledger for the user. "
                        + _context(people or [], ledgers or [], today or date.today(),
                                   summary)},
            *history,
        ],
    }
    return _to_reply(
        _extract_json(_post(payload, api_key, timeout)),
        people, ledgers, person_ledgers,
    )


def read_image(
    data: bytes,
    mimetype: str,
    *,
    api_key: str,
    people: list[str] | None = None,
    ledgers: list[str] | None = None,
    person_ledgers: dict[str, list[str]] | None = None,
    today: date | None = None,
    model: str = VISION_MODEL,
    timeout: int = 120,
) -> Reply:
    """Read a statement or receipt image into draft entries."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AssistantError(
            f"Image is {len(data) // 1024} KB; the vision endpoint takes about "
            f"{MAX_IMAGE_BYTES // 1024} KB. Screenshot a smaller crop, or attach "
            "it to an entry you type in instead."
        )
    encoded = base64.b64encode(data).decode()
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 1200,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text":
                    "Read every money transfer in this image that involves the account "
                    "holder lending money out or being repaid. "
                    + _context(people or [], ledgers or [], today or date.today())},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mimetype};base64,{encoded}"}},
            ]},
        ],
    }
    return _to_reply(
        _extract_json(_post(payload, api_key, timeout)),
        people, ledgers, person_ledgers,
    )


def demo() -> None:
    """Self-check for the parsing layer, which is what breaks. No network."""
    fenced = '```json\n{"entries": [{"date": "2026-01-05", "person": "Nanna", ' \
             '"ledger": "Home", "direction": "given", "amount": "5000", ' \
             '"currency": "INR", "note": "UPI"}]}\n```'
    drafts, rejected = _to_reply(_extract_json(fenced))
    assert not rejected, rejected
    assert len(drafts) == 1
    assert drafts[0].entry.amount_minor == 500_000
    assert drafts[0].entry.person == "Nanna"

    chatty = 'Sure! Here is the entry:\n{"entries": [{"date": "2026-02-01", ' \
             '"person": "A", "ledger": "L", "direction": "received", ' \
             '"amount": "12.50", "currency": "USD"}]}\nHope that helps.'
    drafts, _ = _to_reply(_extract_json(chatty))
    assert drafts[0].entry.amount_minor == 1250
    assert drafts[0].entry.signed_minor == -1250  # received is money back

    # A hallucinated negative or empty person must be refused, not saved.
    bad = {"entries": [
        {"date": "2026-01-01", "person": "", "ledger": "L",
         "direction": "given", "amount": "5"},
        {"date": "2026-01-01", "person": "B", "ledger": "L",
         "direction": "given", "amount": "-5"},
        {"date": "nonsense", "person": "C", "ledger": "L",
         "direction": "given", "amount": "5"},
    ]}
    drafts, rejected = _to_reply(bad)
    assert drafts == [], drafts
    assert len(rejected) == 3, rejected

    assert _to_reply({"entries": []}).drafts == []

    # A question comes back as a question, not as a silent empty result.
    asked = _to_reply({"question": "Who did you give it to?"})
    assert asked.question == "Who did you give it to?"
    assert asked.drafts == []
    # Entries win when the model sends both, since it clearly decided.
    both = _to_reply({"question": "?", "entries": [
        {"date": "2026-01-01", "person": "A", "ledger": "L",
         "direction": "given", "amount": "5"}]})
    assert len(both.drafts) == 1 and not both.question
    try:
        _extract_json("I could not find anything.")
    except AssistantError:
        pass
    else:
        raise AssertionError("prose without JSON should raise")

    print("ledger.assistant: all checks passed")


if __name__ == "__main__":
    demo()
