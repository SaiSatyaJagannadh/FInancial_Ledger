"""Deterministic categorization. Runs before the AI layer so that anything the
user has already taught the system never costs a model call."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Rule


class RuleError(ValueError):
    pass


def compile_pattern(pattern: str, match_type: str) -> re.Pattern[str]:
    if match_type == "contains":
        return re.compile(re.escape(pattern), re.IGNORECASE)
    if match_type == "regex":
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise RuleError(f"invalid regex {pattern!r}: {exc}") from exc
    raise RuleError(f"unknown match_type {match_type!r}")


def active_rules(db: Session) -> list[Rule]:
    return list(
        db.scalars(
            select(Rule).where(Rule.active.is_(True)).order_by(Rule.priority, Rule.id)
        ).all()
    )


def match(description: str, rules: list[Rule]) -> Rule | None:
    """First rule by (priority, id) whose pattern hits. A rule with a broken
    regex is skipped rather than taking the whole import down."""
    for rule in rules:
        try:
            if compile_pattern(rule.pattern, rule.match_type).search(description):
                return rule
        except RuleError:
            continue
    return None


_TOKENS = re.compile(r"[a-z]{3,}")
_NOISE = {"the", "inc", "llc", "com", "pos", "purchase", "payment", "debit", "card"}


def tokens(text: str) -> set[str]:
    return {t for t in _TOKENS.findall(text.lower()) if t not in _NOISE}


def heuristic_account(
    description: str, candidates: list[Account], history: dict[str, int] | None = None
) -> tuple[Account, float] | None:
    """Fallback when there is no rule and no model.

    Prefers an exact past description match, then token overlap with the
    account's code/name. Deliberately conservative: no match beats a wrong one.
    """
    if history:
        exact = history.get(description.strip().lower())
        if exact is not None:
            for account in candidates:
                if account.id == exact:
                    return account, 0.9

    want = tokens(description)
    if not want:
        return None

    best: tuple[Account, float] | None = None
    for account in candidates:
        have = tokens(f"{account.code.replace(':', ' ')} {account.name}")
        if not have:
            continue
        overlap = len(want & have)
        if not overlap:
            continue
        score = overlap / len(have | want)
        if best is None or score > best[1]:
            best = (account, round(min(score, 0.75), 2))
    return best


def description_history(db: Session, account_types: tuple[str, ...] = ()) -> dict[str, int]:
    """Past description -> most recently used account, for the exact-match path."""
    from app.models import Posting, Transaction

    stmt = (
        select(Transaction.description, Posting.account_id, Transaction.id)
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Account.id == Posting.account_id)
        .order_by(Transaction.id)
    )
    if account_types:
        stmt = stmt.where(Account.type.in_(account_types))

    out: dict[str, int] = {}
    for description, account_id, _ in db.execute(stmt).all():
        out[description.strip().lower()] = account_id
    return out
