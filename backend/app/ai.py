"""NVIDIA NIM integration.

Two rules govern this module:

1. **The model never does arithmetic.** For questions it picks *filters*; the
   database sums the postings. A wrong filter is visible in the response; a
   wrong total silently corrupts the user's understanding of their money.
2. **No key is not an error.** Without NVIDIA_API_KEY the categorizer falls back
   to rules plus the token heuristic and the app keeps working.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AccountType, Posting, Transaction

MAX_ACCOUNTS_IN_PROMPT = 120
MAX_TX_PER_CALL = 25


class AIUnavailable(RuntimeError):
    """Raised when a route needs the model and no key is configured."""


@dataclass
class Suggestion:
    transaction_id: int
    account_id: int
    confidence: float
    reason: str
    source: str


def client():
    settings = get_settings()
    if not settings.ai_enabled:
        raise AIUnavailable(
            "NVIDIA_API_KEY is not set. Add it to backend/.env to enable AI features."
        )
    from openai import OpenAI  # imported lazily so the app boots without the SDK configured

    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        # A model that never answers must not hold an HTTP worker open; the
        # callers all have a local fallback to drop to.
        timeout=settings.nvidia_timeout_seconds,
        max_retries=settings.nvidia_max_retries,
    )


def _complete(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 1024) -> str:
    settings = get_settings()
    response = client().chat.completions.create(
        model=settings.nvidia_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> dict | list:
    """Pull JSON out of a model response that may be fenced or prose-wrapped.

    Small models fence their output or prepend "Here is the JSON:" often enough
    that failing on the first json.loads would make the feature flaky.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _JSON_BLOCK.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"model did not return JSON: {text[:200]!r}")


# --------------------------------------------------------------------------
# Categorization
# --------------------------------------------------------------------------

CATEGORIZE_SYSTEM = """You categorize bank transactions into accounting accounts.

You are given a numbered list of accounts and a list of transactions. For each
transaction, choose the single best account BY ID from the list.

Rules:
- Only use account ids that appear in the provided list. Never invent an id.
- Negative amounts are money spent (use an expense account).
- Positive amounts are money received (use an income account).
- If nothing fits well, use confidence below 0.4 rather than forcing a match.

Reply with JSON only, no prose:
{"suggestions": [{"transaction_id": 1, "account_id": 7, "confidence": 0.9, "reason": "short reason"}]}"""


def _account_catalog(db: Session, wanted: set[AccountType]) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .where(Account.type.in_(wanted), Account.archived.is_(False))
            .order_by(Account.code)
            .limit(MAX_ACCOUNTS_IN_PROMPT)
        ).all()
    )


def llm_categorize(
    db: Session, transactions: list[tuple[int, str, int]], accounts: list[Account]
) -> list[Suggestion]:
    """transactions: (id, description, signed amount in minor units)."""
    if not transactions or not accounts:
        return []

    catalog = "\n".join(f"- id={a.id} code={a.code} name={a.name} type={a.type.value}" for a in accounts)
    listing = "\n".join(
        f"- transaction_id={tid} description={desc!r} amount={amount / 100:.2f}"
        for tid, desc, amount in transactions[:MAX_TX_PER_CALL]
    )

    raw = _complete(
        [
            {"role": "system", "content": CATEGORIZE_SYSTEM},
            {"role": "user", "content": f"ACCOUNTS:\n{catalog}\n\nTRANSACTIONS:\n{listing}"},
        ]
    )
    payload = extract_json(raw)
    items = payload.get("suggestions", []) if isinstance(payload, dict) else payload

    valid_ids = {a.id for a in accounts}
    requested = {tid for tid, _, _ in transactions}
    out: list[Suggestion] = []
    for item in items:
        try:
            tid = int(item["transaction_id"])
            aid = int(item["account_id"])
        except (KeyError, TypeError, ValueError):
            continue
        # Drop hallucinated ids rather than trusting the model's list membership.
        if aid not in valid_ids or tid not in requested:
            continue
        out.append(
            Suggestion(
                transaction_id=tid,
                account_id=aid,
                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                reason=str(item.get("reason", ""))[:200],
                source="llm",
            )
        )
    return out


# --------------------------------------------------------------------------
# Natural-language questions
# --------------------------------------------------------------------------

ASK_SYSTEM = """You translate a question about a personal ledger into a JSON query.
You never compute totals yourself — the database does that.

Available fields:
  "account_types": subset of ["asset","liability","equity","income","expense"]
  "account_codes": list of account codes to restrict to (optional)
  "start": "YYYY-MM-DD" (optional)
  "end": "YYYY-MM-DD" (optional)
  "text": substring to match against the transaction description (optional)
  "group_by": one of "account", "month", "none"
  "intent": short restatement of what is being asked

Reply with JSON only, for example:
{"account_types":["expense"],"account_codes":[],"start":"2026-01-01","end":"2026-03-31","text":null,"group_by":"account","intent":"Q1 spending by category"}"""

_VALID_TYPES = {t.value for t in AccountType}


def plan_query(question: str, accounts: list[Account], today: date) -> dict:
    catalog = ", ".join(a.code for a in accounts[:MAX_ACCOUNTS_IN_PROMPT])
    raw = _complete(
        [
            {"role": "system", "content": ASK_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Today is {today.isoformat()}.\n"
                    f"Account codes: {catalog}\n\nQuestion: {question}"
                ),
            },
        ],
        max_tokens=400,
    )
    plan = extract_json(raw)
    if not isinstance(plan, dict):
        raise ValueError("query plan was not an object")
    return sanitize_plan(plan, {a.code for a in accounts})


def sanitize_plan(plan: dict, known_codes: set[str]) -> dict:
    """Keep only fields we understand, with values we can actually execute."""

    def as_date(value) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            return None

    types = [t for t in plan.get("account_types") or [] if t in _VALID_TYPES]
    codes = [c for c in plan.get("account_codes") or [] if c in known_codes]
    group_by = plan.get("group_by") if plan.get("group_by") in ("account", "month", "none") else "account"
    text = plan.get("text")

    return {
        "account_types": types or ["expense"],
        "account_codes": codes,
        "start": as_date(plan.get("start")),
        "end": as_date(plan.get("end")),
        "text": text.strip() if isinstance(text, str) and text.strip() else None,
        "group_by": group_by,
        "intent": str(plan.get("intent", ""))[:200],
    }


def run_query(db: Session, plan: dict) -> tuple[int, list[dict]]:
    """Execute the plan in SQL. This is where every number in an answer comes from."""
    from app import ledger

    stmt = (
        select(Account.type, Account.code, Account.name, Transaction.date, Posting.amount_minor)
        .join(Posting, Posting.account_id == Account.id)
        .join(Transaction, Transaction.id == Posting.transaction_id)
        .where(Account.type.in_([AccountType(t) for t in plan["account_types"]]))
    )
    if plan["account_codes"]:
        stmt = stmt.where(Account.code.in_(plan["account_codes"]))
    if plan["start"]:
        stmt = stmt.where(Transaction.date >= date.fromisoformat(plan["start"]))
    if plan["end"]:
        stmt = stmt.where(Transaction.date <= date.fromisoformat(plan["end"]))
    if plan["text"]:
        stmt = stmt.where(Transaction.description.ilike(f"%{plan['text']}%"))

    rows = db.execute(stmt).all()

    total = 0
    grouped: dict[str, int] = {}
    for account_type, code, name, tx_date, amount in rows:
        natural = ledger.natural_balance(account_type, int(amount))
        total += natural
        if plan["group_by"] == "month":
            key = f"{tx_date.year:04d}-{tx_date.month:02d}"
        elif plan["group_by"] == "account":
            key = f"{code} ({name})"
        else:
            key = "total"
        grouped[key] = grouped.get(key, 0) + natural

    listing = [
        {"label": key, "amount_minor": value, "amount": value / 100}
        for key, value in sorted(grouped.items(), key=lambda kv: -abs(kv[1]))
    ]
    return total, listing[:50]


def format_usd(minor: int) -> str:
    """The one rendering of a figure that goes anywhere near the model."""
    return f"${minor / 100:,.2f}"


def states_the_total(text: str, total_minor: int) -> bool:
    """True when the sentence actually contains the total we computed.

    A small model will happily write "$611,297" for 6112.97, or echo raw cents.
    The computed figure is shown separately in the UI, but the prose is what a
    person reads first, so a sentence that misstates it is not usable.
    """
    target = format_usd(total_minor)
    plain = target.lstrip("$")
    candidates = {target, plain, plain.replace(",", "")}
    if plain.endswith(".00"):
        # "$5,200.00" may reasonably be written "$5,200" or "$5200".
        whole = plain[:-3]
        candidates |= {f"${whole}", whole, whole.replace(",", "")}
    return any(c in text for c in candidates)


def narrate(question: str, plan: dict, total_minor: int, rows: list[dict]) -> str:
    """Ask the model to phrase the answer — using only the numbers we computed.

    The model is handed pre-formatted currency strings and never raw minor
    units, and its sentence is rejected unless it repeats our total verbatim.
    """
    fallback = f"Total: {format_usd(total_minor)} across {len(rows)} group(s)."
    facts = json.dumps(
        {
            "total": format_usd(total_minor),
            "breakdown": [
                {"label": r["label"], "amount": format_usd(r["amount_minor"])}
                for r in rows[:15]
            ],
        },
        indent=None,
    )
    try:
        text = _complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You state a ledger result in 1-3 plain sentences. Copy the "
                        "amounts EXACTLY as written, including the dollar sign, commas "
                        "and cents. Never rescale, reformat, round, or recompute a "
                        "figure, and never introduce one that is not in the data."
                    ),
                },
                {"role": "user", "content": f"Question: {question}\nData: {facts}"},
            ],
            temperature=0.2,
            max_tokens=250,
        ).strip()
    except Exception:
        # The numbers are already correct; a phrasing failure must not lose them.
        return fallback

    # Silently dropping a mangled sentence beats showing someone the wrong
    # magnitude of their own money.
    return text if states_the_total(text, total_minor) else fallback


def default_window(today: date) -> tuple[date, date]:
    return today - timedelta(days=90), today


def uncategorized_transactions(db: Session, limit: int = 25) -> list[Transaction]:
    """Transactions with a leg in an account whose code contains 'uncategorized'."""
    holding = select(Account.id).where(Account.code.like("%uncategorized%"))
    return list(
        db.scalars(
            select(Transaction)
            .join(Posting, Posting.transaction_id == Transaction.id)
            .where(Posting.account_id.in_(holding))
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(limit)
        ).unique().all()
    )


def transaction_count(db: Session) -> int:
    return int(db.scalar(select(func.count(Transaction.id))) or 0)
