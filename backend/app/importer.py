"""CSV import: parse, dedupe, and suggest an account per row.

Nothing here writes to the ledger. The router previews rows, the user confirms,
and the commit step goes through app.ledger like every other write.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import rules as rules_mod
from app.models import Account, AccountType, Transaction
from app.money import to_minor
from app.schemas import ImportPreview, ImportPreviewRow

#: Tried in order. Ambiguous 01/02/2026 resolves as US month-first, which is
#: what the banks that emit that format mean.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%d-%m-%Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y/%m/%d",
)

_ALIASES = {
    "date": {"date", "transaction date", "posted date", "post date", "trans date"},
    "description": {"description", "payee", "merchant", "name", "details", "memo"},
    "amount": {"amount", "value", "transaction amount"},
    "debit": {"debit", "withdrawal", "withdrawals", "money out"},
    "credit": {"credit", "deposit", "deposits", "money in"},
}


def parse_date(raw: str) -> date:
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {raw!r}")


def detect_columns(headers: list[str]) -> dict[str, str]:
    """Map our field names onto this file's headers, case/space insensitively."""
    normalized = {h.strip().lower(): h for h in headers}
    mapping: dict[str, str] = {}
    for field, names in _ALIASES.items():
        for name in names:
            if name in normalized:
                mapping[field] = normalized[name]
                break
    return mapping


def row_amount_minor(row: dict, mapping: dict[str, str], currency: str) -> int:
    """Signed amount from either a single amount column or debit/credit pair.

    Sign convention: positive means money **into** the bank account.
    """
    if "amount" in mapping:
        raw = (row.get(mapping["amount"]) or "").strip()
        if raw:
            return to_minor(raw, currency)

    debit = (row.get(mapping.get("debit", "")) or "").strip()
    credit = (row.get(mapping.get("credit", "")) or "").strip()
    if debit:
        return -abs(to_minor(debit, currency))
    if credit:
        return abs(to_minor(credit, currency))
    raise ValueError("row has no amount")


def make_external_id(tx_date: date, description: str, amount_minor: int, seq: int) -> str:
    """Stable dedupe key. `seq` distinguishes genuinely identical same-day rows
    (two $3.50 coffees) so the second one is not silently dropped."""
    digest = hashlib.sha256(
        f"{tx_date.isoformat()}|{description.strip().lower()}|{amount_minor}|{seq}".encode()
    ).hexdigest()
    return f"csv:{digest[:24]}"


def preview(
    db: Session, content: str, account_id: int, currency: str = "USD"
) -> ImportPreview:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return ImportPreview(rows=[], total=0, duplicates=0, errors=["file has no header row"])

    mapping = detect_columns(list(reader.fieldnames))
    missing = [f for f in ("date", "description") if f not in mapping]
    if not ({"amount", "debit", "credit"} & mapping.keys()):
        missing.append("amount (or debit/credit)")
    if missing:
        return ImportPreview(
            rows=[],
            total=0,
            duplicates=0,
            errors=[f"missing column(s): {', '.join(missing)}. found: {reader.fieldnames}"],
        )

    active = rules_mod.active_rules(db)
    categorizable = list(
        db.scalars(
            select(Account).where(
                Account.type.in_([AccountType.expense, AccountType.income]),
                Account.archived.is_(False),
            )
        ).all()
    )
    history = rules_mod.description_history(db, (AccountType.expense, AccountType.income))
    existing_ids = set(
        db.scalars(select(Transaction.external_id).where(Transaction.external_id.is_not(None))).all()
    )

    rows: list[ImportPreviewRow] = []
    errors: list[str] = []
    seen_counts: dict[tuple, int] = {}
    duplicates = 0

    for line_no, raw in enumerate(reader, start=2):
        try:
            tx_date = parse_date(raw[mapping["date"]])
            description = (raw.get(mapping["description"]) or "").strip() or "(no description)"
            amount = row_amount_minor(raw, mapping, currency)
            if amount == 0:
                errors.append(f"line {line_no}: zero amount, skipped")
                continue
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"line {line_no}: {exc}")
            continue

        key = (tx_date, description.lower(), amount)
        seq = seen_counts.get(key, 0)
        seen_counts[key] = seq + 1
        external_id = make_external_id(tx_date, description, amount, seq)

        is_duplicate = external_id in existing_ids
        duplicates += is_duplicate

        suggestion = None
        rule = rules_mod.match(description, active)
        if rule is not None:
            suggestion = db.get(Account, rule.account_id)
        else:
            # Spending picks an expense account; income picks an income one.
            wanted = AccountType.income if amount > 0 else AccountType.expense
            pool = [a for a in categorizable if a.type is wanted]
            guess = rules_mod.heuristic_account(description, pool, history)
            if guess:
                suggestion = guess[0]

        rows.append(
            ImportPreviewRow(
                date=tx_date,
                description=description,
                amount_minor=amount,
                external_id=external_id,
                suggested_account_id=suggestion.id if suggestion else None,
                suggested_account_code=suggestion.code if suggestion else None,
                duplicate=is_duplicate,
            )
        )

    return ImportPreview(rows=rows, total=len(rows), duplicates=duplicates, errors=errors)
