"""Double-entry service layer. Every invariant in the spec is enforced here.

Routers must not write postings directly — they go through these functions, so
there is exactly one place that can violate the books.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, AccountType, Posting, Transaction, normal_sign


class LedgerError(ValueError):
    """Rejected because it would break the books."""


@dataclass(frozen=True)
class PostingInput:
    account_id: int
    amount_minor: int
    currency: str = "USD"


def validate_postings(postings: list[PostingInput]) -> None:
    """Invariants 1 and 2: at least two legs, and zero sum per currency."""
    if len(postings) < 2:
        raise LedgerError("a transaction needs at least 2 postings")

    if any(p.amount_minor == 0 for p in postings):
        raise LedgerError("a posting of zero moves nothing; remove it")

    by_currency: dict[str, int] = defaultdict(int)
    for p in postings:
        by_currency[p.currency.upper()] += p.amount_minor

    unbalanced = {c: total for c, total in by_currency.items() if total != 0}
    if unbalanced:
        detail = ", ".join(f"{c} off by {total}" for c, total in sorted(unbalanced.items()))
        raise LedgerError(f"postings do not balance to zero: {detail}")


def _load_accounts(db: Session, ids: list[int]) -> dict[int, Account]:
    if not ids:
        return {}
    rows = db.scalars(select(Account).where(Account.id.in_(set(ids)))).all()
    found = {a.id: a for a in rows}
    missing = sorted(set(ids) - found.keys())
    if missing:
        raise LedgerError(f"unknown account id(s): {missing}")
    archived = sorted(a.id for a in rows if a.archived)
    if archived:
        raise LedgerError(f"cannot post to archived account(s): {archived}")
    return found


def create_transaction(
    db: Session,
    *,
    tx_date: date,
    description: str,
    postings: list[PostingInput],
    memo: str | None = None,
    external_id: str | None = None,
    source: str = "manual",
) -> Transaction:
    validate_postings(postings)
    accounts = _load_accounts(db, [p.account_id for p in postings])

    for p in postings:
        account = accounts[p.account_id]
        if account.currency.upper() != p.currency.upper():
            raise LedgerError(
                f"account {account.code} is {account.currency}, "
                f"posting is {p.currency}"
            )

    if external_id and db.scalar(
        select(Transaction.id).where(Transaction.external_id == external_id)
    ):
        raise LedgerError(f"transaction with external_id {external_id!r} already exists")

    tx = Transaction(
        date=tx_date,
        description=description.strip(),
        memo=memo,
        external_id=external_id,
        source=source,
        postings=[
            Posting(
                account_id=p.account_id,
                amount_minor=p.amount_minor,
                currency=p.currency.upper(),
            )
            for p in postings
        ],
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def replace_transaction(
    db: Session,
    tx: Transaction,
    *,
    tx_date: date,
    description: str,
    postings: list[PostingInput],
    memo: str | None = None,
) -> Transaction:
    """Invariant 3: postings are immutable, so an edit replaces every leg."""
    validate_postings(postings)
    _load_accounts(db, [p.account_id for p in postings])

    tx.date = tx_date
    tx.description = description.strip()
    tx.memo = memo
    tx.postings = [
        Posting(
            account_id=p.account_id,
            amount_minor=p.amount_minor,
            currency=p.currency.upper(),
        )
        for p in postings
    ]
    db.commit()
    db.refresh(tx)
    return tx


def delete_transaction(db: Session, tx: Transaction) -> None:
    db.delete(tx)  # postings cascade
    db.commit()


def archive_account(db: Session, account: Account) -> Account:
    """Invariant 4: an account with history is archived, never deleted."""
    account.archived = True
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, account: Account) -> None:
    used = db.scalar(
        select(func.count(Posting.id)).where(Posting.account_id == account.id)
    )
    if used:
        raise LedgerError(
            f"account {account.code} has {used} posting(s); archive it instead of deleting"
        )
    if db.scalar(select(func.count(Account.id)).where(Account.parent_id == account.id)):
        raise LedgerError(f"account {account.code} has child accounts; move them first")
    db.delete(account)
    db.commit()


def account_balance(db: Session, account_id: int, as_of: date | None = None) -> int:
    """Raw signed sum of this account's own postings (no subtree rollup)."""
    stmt = select(func.coalesce(func.sum(Posting.amount_minor), 0)).where(
        Posting.account_id == account_id
    )
    if as_of is not None:
        stmt = stmt.join(Transaction).where(Transaction.date <= as_of)
    return int(db.scalar(stmt) or 0)


def descendant_ids(db: Session, account_id: int) -> list[int]:
    """The account plus every account beneath it. Small trees: a BFS is plenty."""
    parents = defaultdict(list)
    for child_id, parent_id in db.execute(select(Account.id, Account.parent_id)).all():
        if parent_id is not None:
            parents[parent_id].append(child_id)

    out: list[int] = []
    queue = [account_id]
    seen = set()
    while queue:
        current = queue.pop()
        if current in seen:  # guards a cycle from a bad parent_id write
            continue
        seen.add(current)
        out.append(current)
        queue.extend(parents.get(current, []))
    return out


def balances_by_account(db: Session, as_of: date | None = None) -> dict[int, int]:
    """One query for every account's own balance — avoids N+1 in the reports."""
    stmt = select(Posting.account_id, func.sum(Posting.amount_minor)).group_by(
        Posting.account_id
    )
    if as_of is not None:
        stmt = stmt.join(Transaction).where(Transaction.date <= as_of)
    return {aid: int(total or 0) for aid, total in db.execute(stmt).all()}


def rollup_balances(db: Session, as_of: date | None = None) -> dict[int, int]:
    """Each account's balance including its subtree."""
    own = balances_by_account(db, as_of)
    accounts = db.scalars(select(Account)).all()
    return {
        a.id: sum(own.get(d, 0) for d in descendant_ids(db, a.id)) for a in accounts
    }


def natural_balance(account_type: AccountType, raw_minor: int) -> int:
    """Raw signed balance flipped into the sign a human expects for the type.

    Income of 500 is stored as -50000 (a credit); a report should show 50000.
    """
    return raw_minor * normal_sign(account_type)


def trial_balance(db: Session, as_of: date | None = None) -> int:
    """Sum of every posting. Must be zero — this is the books' health check."""
    stmt = select(func.coalesce(func.sum(Posting.amount_minor), 0))
    if as_of is not None:
        stmt = stmt.join(Transaction).where(Transaction.date <= as_of)
    return int(db.scalar(stmt) or 0)
