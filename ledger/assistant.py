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

_SCHEMA = """Return ONLY a JSON object, no prose, no markdown fence:
{"entries": [{"date": "YYYY-MM-DD", "person": "...", "ledger": "...",
"direction": "given" | "received", "amount": "1234.56",
"currency": "INR" | "USD", "note": "..."}]}

Rules:
- "given" = the user handed money out. "received" = money came back to them.
- amount is always POSITIVE. Direction carries the sign.
- If a date is not stated, use today's date.
- If the currency is not stated, use INR.
- Reuse an existing person or ledger name EXACTLY when the text clearly refers
  to one; otherwise use the name as written.
- If you cannot tell the amount or the person, return {"entries": []}.
Never invent an amount you did not read."""


@dataclass(frozen=True)
class Draft:
    """A proposed entry plus what the model claimed, for display before saving."""

    entry: Entry
    raw: dict


class AssistantError(RuntimeError):
    """The model could not be reached, or gave nothing usable."""


def _context(people: list[str], ledgers: list[str], today: date) -> str:
    known = ""
    if people:
        known += f"\nExisting people: {', '.join(sorted(set(people))[:40])}"
    if ledgers:
        known += f"\nExisting ledgers: {', '.join(sorted(set(ledgers))[:40])}"
    return f"Today is {today.isoformat()}.{known}\n\n{_SCHEMA}"


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


def _to_drafts(payload: dict) -> tuple[list[Draft], list[str]]:
    """Validate the model's rows through the same door a sheet row goes through."""
    drafts: list[Draft] = []
    rejected: list[str] = []
    for raw in payload.get("entries") or []:
        if not isinstance(raw, dict):
            rejected.append(f"not an object: {raw!r}")
            continue
        row = {key: str(raw.get(key, "") or "") for key in COLUMNS if key != "attachment"}
        try:
            drafts.append(Draft(entry=Entry.from_row(row), raw=raw))
        except EntryError as exc:
            rejected.append(f"{raw.get('person') or 'entry'}: {exc}")
    return drafts, rejected


def read_note(
    text: str,
    *,
    api_key: str,
    people: list[str] | None = None,
    ledgers: list[str] | None = None,
    today: date | None = None,
    model: str = TEXT_MODEL,
    timeout: int = 60,
) -> tuple[list[Draft], list[str]]:
    """Read a typed or dictated note into draft entries."""
    if not text.strip():
        return [], []
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 900,
        "messages": [
            {"role": "system",
             "content": "You extract personal-lending ledger entries. "
                        + _context(people or [], ledgers or [], today or date.today())},
            {"role": "user", "content": text.strip()},
        ],
    }
    return _to_drafts(_extract_json(_post(payload, api_key, timeout)))


def read_image(
    data: bytes,
    mimetype: str,
    *,
    api_key: str,
    people: list[str] | None = None,
    ledgers: list[str] | None = None,
    today: date | None = None,
    model: str = VISION_MODEL,
    timeout: int = 120,
) -> tuple[list[Draft], list[str]]:
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
    return _to_drafts(_extract_json(_post(payload, api_key, timeout)))


def demo() -> None:
    """Self-check for the parsing layer, which is what breaks. No network."""
    fenced = '```json\n{"entries": [{"date": "2026-01-05", "person": "Nanna", ' \
             '"ledger": "Home", "direction": "given", "amount": "5000", ' \
             '"currency": "INR", "note": "UPI"}]}\n```'
    drafts, rejected = _to_drafts(_extract_json(fenced))
    assert not rejected, rejected
    assert len(drafts) == 1
    assert drafts[0].entry.amount_minor == 500_000
    assert drafts[0].entry.person == "Nanna"

    chatty = 'Sure! Here is the entry:\n{"entries": [{"date": "2026-02-01", ' \
             '"person": "A", "ledger": "L", "direction": "received", ' \
             '"amount": "12.50", "currency": "USD"}]}\nHope that helps.'
    drafts, _ = _to_drafts(_extract_json(chatty))
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
    drafts, rejected = _to_drafts(bad)
    assert drafts == [], drafts
    assert len(rejected) == 3, rejected

    assert _to_drafts({"entries": []}) == ([], [])
    try:
        _extract_json("I could not find anything.")
    except AssistantError:
        pass
    else:
        raise AssertionError("prose without JSON should raise")

    print("ledger.assistant: all checks passed")


if __name__ == "__main__":
    demo()
