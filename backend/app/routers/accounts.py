from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ledger
from app.db import get_db
from app.models import Account
from app.money import to_major
from app.schemas import AccountBalanceOut, AccountCreate, AccountOut, AccountUpdate

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_account(account_id: int, db: Session = Depends(get_db)) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, f"account {account_id} not found")
    return account


@router.get("", response_model=list[AccountBalanceOut])
def list_accounts(
    include_archived: bool = False,
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(Account).order_by(Account.code)
    if not include_archived:
        stmt = stmt.where(Account.archived.is_(False))
    accounts = db.scalars(stmt).all()

    own = ledger.balances_by_account(db, as_of)
    rollup = ledger.rollup_balances(db, as_of)

    out = []
    for account in accounts:
        raw = ledger.natural_balance(account.type, own.get(account.id, 0))
        roll = ledger.natural_balance(account.type, rollup.get(account.id, 0))
        out.append(
            AccountBalanceOut(
                **AccountOut.model_validate(account).model_dump(),
                balance_minor=raw,
                balance=to_major(raw, account.currency),
                rollup_minor=roll,
                rollup=to_major(roll, account.currency),
            )
        )
    return out


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if payload.parent_id is not None and db.get(Account, payload.parent_id) is None:
        raise HTTPException(422, f"parent account {payload.parent_id} not found")
    account = Account(**payload.model_dump())
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"account code {payload.code!r} already exists") from None
    db.refresh(account)
    return account


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    payload: AccountUpdate,
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("parent_id") == account.id:
        raise HTTPException(422, "an account cannot be its own parent")
    if "parent_id" in data and data["parent_id"] is not None:
        if data["parent_id"] in set(ledger.descendant_ids(db, account.id)):
            raise HTTPException(422, "that parent would create a cycle")
    for field, value in data.items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def delete_account(account: Account = Depends(get_account), db: Session = Depends(get_db)):
    try:
        ledger.delete_account(db, account)
    except ledger.LedgerError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{account_id}/archive", response_model=AccountOut)
def archive_account(account: Account = Depends(get_account), db: Session = Depends(get_db)):
    return ledger.archive_account(db, account)
