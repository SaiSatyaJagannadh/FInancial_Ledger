from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import importer, ledger
from app.db import get_db
from app.ledger import LedgerError, PostingInput
from app.models import Account, AccountType
from app.schemas import ImportCommit, ImportPreview, ImportResult

router = APIRouter(prefix="/imports", tags=["imports"])

UNCATEGORIZED = {
    AccountType.expense: ("expenses:uncategorized", "Uncategorized Expenses"),
    AccountType.income: ("income:uncategorized", "Uncategorized Income"),
}


def holding_account(db: Session, kind: AccountType, currency: str) -> Account:
    """The parking account for rows we could not categorize. Created on demand so
    a fresh install can import without any setup."""
    code, name = UNCATEGORIZED[kind]
    account = db.scalar(select(Account).where(Account.code == code))
    if account is None:
        account = Account(code=code, name=name, type=kind, currency=currency)
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def _bank_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(422, f"account {account_id} not found")
    if account.type not in (AccountType.asset, AccountType.liability):
        raise HTTPException(
            422, "import target must be an asset or liability account (a bank or card)"
        )
    return account


@router.post("/preview", response_model=ImportPreview)
async def preview(
    account_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    account = _bank_account(db, account_id)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "file is larger than 10MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")  # bank exports are frequently not UTF-8
    return importer.preview(db, content, account.id, account.currency)


@router.post("/commit", response_model=ImportResult)
def commit(payload: ImportCommit, db: Session = Depends(get_db)):
    account = _bank_account(db, payload.account_id)
    created = skipped = 0
    errors: list[str] = []

    for row in payload.rows:
        if row.duplicate:
            skipped += 1
            continue

        if row.suggested_account_id is not None:
            other_id = row.suggested_account_id
            if db.get(Account, other_id) is None:
                errors.append(f"{row.description}: account {other_id} not found")
                skipped += 1
                continue
        else:
            kind = AccountType.income if row.amount_minor > 0 else AccountType.expense
            other_id = holding_account(db, kind, account.currency).id

        try:
            ledger.create_transaction(
                db,
                tx_date=row.date,
                description=row.description,
                external_id=row.external_id,
                source="csv",
                postings=[
                    PostingInput(account.id, row.amount_minor, account.currency),
                    PostingInput(other_id, -row.amount_minor, account.currency),
                ],
            )
            created += 1
        except LedgerError as exc:
            db.rollback()
            skipped += 1
            errors.append(f"{row.description}: {exc}")

    return ImportResult(created=created, skipped=skipped, errors=errors[:50])
