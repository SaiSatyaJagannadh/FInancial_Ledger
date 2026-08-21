from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai as ai_mod
from app import ledger, rules as rules_mod
from app.config import get_settings
from app.db import get_db
from app.ledger import LedgerError, PostingInput
from app.models import Account, AccountType, Posting, Transaction
from app.money import to_major
from app.schemas import (
    ApplyRequest,
    AskRequest,
    AskResponse,
    CategorizeRequest,
    CategorizeResponse,
    CategorySuggestion,
)

router = APIRouter(prefix="/ai", tags=["ai"])


def _signed_amount(tx: Transaction, holding_ids: set[int]) -> int:
    """The amount as the bank saw it: the leg that is *not* the holding account."""
    for posting in tx.postings:
        if posting.account_id not in holding_ids:
            return posting.amount_minor
    return tx.postings[0].amount_minor if tx.postings else 0


@router.post("/categorize", response_model=CategorizeResponse)
def categorize(payload: CategorizeRequest, db: Session = Depends(get_db)):
    """Suggest accounts for uncategorized transactions.

    Rules first (free and deterministic), then the model for what is left, then
    the token heuristic if no key is configured. Suggestions are never applied
    automatically — /ai/apply does that once the user confirms.
    """
    if payload.transaction_ids:
        transactions = list(
            db.scalars(
                select(Transaction).where(Transaction.id.in_(payload.transaction_ids))
            ).unique().all()
        )
    else:
        transactions = ai_mod.uncategorized_transactions(db, payload.limit)

    if not transactions:
        return CategorizeResponse(suggestions=[], source="none", note="nothing to categorize")

    holding_ids = set(
        db.scalars(select(Account.id).where(Account.code.like("%uncategorized%"))).all()
    )
    accounts = {a.id: a for a in db.scalars(select(Account)).all()}
    active = rules_mod.active_rules(db)

    suggestions: list[CategorySuggestion] = []
    unresolved: list[tuple[int, str, int]] = []

    for tx in transactions:
        amount = _signed_amount(tx, holding_ids)
        rule = rules_mod.match(tx.description, active)
        if rule is not None and rule.account_id in accounts:
            suggestions.append(
                CategorySuggestion(
                    transaction_id=tx.id,
                    description=tx.description,
                    account_id=rule.account_id,
                    account_code=accounts[rule.account_id].code,
                    confidence=1.0,
                    reason=f"rule: {rule.match_type} {rule.pattern!r}",
                    source="rule",
                )
            )
        else:
            unresolved.append((tx.id, tx.description, amount))

    settings = get_settings()
    note = None
    source = "rule"

    if unresolved:
        wanted = {AccountType.expense, AccountType.income}
        catalog = ai_mod._account_catalog(db, wanted)
        catalog = [a for a in catalog if a.id not in holding_ids]

        if settings.ai_enabled and catalog:
            try:
                for item in ai_mod.llm_categorize(db, unresolved, catalog):
                    suggestions.append(
                        CategorySuggestion(
                            transaction_id=item.transaction_id,
                            description=next(
                                d for i, d, _ in unresolved if i == item.transaction_id
                            ),
                            account_id=item.account_id,
                            account_code=accounts[item.account_id].code,
                            confidence=item.confidence,
                            reason=item.reason or "model suggestion",
                            source="llm",
                        )
                    )
                source = "llm"
            except Exception as exc:  # network, quota, malformed JSON
                note = f"model call failed ({type(exc).__name__}); used the local heuristic"
                source = "heuristic"
                suggestions.extend(_heuristic(db, unresolved, catalog, holding_ids))
        else:
            source = "heuristic"
            note = (
                None
                if catalog
                else "no expense/income accounts to categorize into yet"
            )
            if not settings.ai_enabled:
                note = "NVIDIA_API_KEY not set — using local rules and heuristics"
            suggestions.extend(_heuristic(db, unresolved, catalog, holding_ids))

    return CategorizeResponse(suggestions=suggestions, source=source, note=note)


def _heuristic(
    db: Session,
    unresolved: list[tuple[int, str, int]],
    catalog: list[Account],
    holding_ids: set[int],
) -> list[CategorySuggestion]:
    history = rules_mod.description_history(db, (AccountType.expense, AccountType.income))
    history = {k: v for k, v in history.items() if v not in holding_ids}

    out: list[CategorySuggestion] = []
    for tx_id, description, amount in unresolved:
        wanted = AccountType.income if amount > 0 else AccountType.expense
        pool = [a for a in catalog if a.type is wanted]
        guess = rules_mod.heuristic_account(description, pool, history)
        if guess is None:
            continue
        account, confidence = guess
        out.append(
            CategorySuggestion(
                transaction_id=tx_id,
                description=description,
                account_id=account.id,
                account_code=account.code,
                confidence=confidence,
                reason="matched past descriptions and account naming",
                source="heuristic",
            )
        )
    return out


@router.post("/apply")
def apply(payload: ApplyRequest, db: Session = Depends(get_db)):
    """Move the holding leg of each transaction onto the chosen account.

    Goes through ledger.replace_transaction, so the zero-sum invariant is
    re-checked on every one of these edits.
    """
    holding_ids = set(
        db.scalars(select(Account.id).where(Account.code.like("%uncategorized%"))).all()
    )
    updated, errors = 0, []

    for item in payload.assignments:
        tx = db.get(Transaction, item.transaction_id)
        if tx is None:
            errors.append(f"transaction {item.transaction_id} not found")
            continue
        target = db.get(Account, item.account_id)
        if target is None:
            errors.append(f"account {item.account_id} not found")
            continue

        postings = []
        replaced = False
        for posting in tx.postings:
            account_id = posting.account_id
            # Retarget the holding leg; if none, retarget the leg that shares the
            # new account's income/expense polarity.
            if not replaced and (
                account_id in holding_ids
                or (not holding_ids and _is_categorizable(db, account_id))
            ):
                account_id = target.id
                replaced = True
            postings.append(PostingInput(account_id, posting.amount_minor, posting.currency))

        if not replaced:
            errors.append(f"transaction {tx.id} has no uncategorized leg")
            continue

        try:
            ledger.replace_transaction(
                db,
                tx,
                tx_date=tx.date,
                description=tx.description,
                memo=tx.memo,
                postings=postings,
            )
            updated += 1
        except LedgerError as exc:
            db.rollback()
            errors.append(f"transaction {tx.id}: {exc}")

    return {"updated": updated, "errors": errors}


def _is_categorizable(db: Session, account_id: int) -> bool:
    account = db.get(Account, account_id)
    return account is not None and account.type in (AccountType.expense, AccountType.income)


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    """Natural language in, a real ledger number out.

    The model only chooses filters. run_query does the arithmetic in SQL, so the
    figure in the answer is always one the books can justify.
    """
    settings = get_settings()
    if not settings.ai_enabled:
        raise HTTPException(
            503,
            "AI is not configured. Set NVIDIA_API_KEY in backend/.env to use natural "
            "language questions. Reports and categorization work without it.",
        )

    accounts = list(db.scalars(select(Account).where(Account.archived.is_(False))).all())
    try:
        plan = ai_mod.plan_query(payload.question, accounts, date.today())
    except Exception as exc:
        raise HTTPException(502, f"could not plan that question: {exc}") from exc

    total, rows = ai_mod.run_query(db, plan)
    answer = ai_mod.narrate(payload.question, plan, total, rows)

    return AskResponse(
        question=payload.question,
        answer=answer,
        query=plan,
        total_minor=total,
        total=to_major(total),
        rows=rows,
    )
