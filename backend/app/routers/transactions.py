from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ledger
from app.db import get_db
from app.ledger import LedgerError, PostingInput
from app.models import Posting, Transaction
from app.schemas import TransactionCreate, TransactionOut

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _inputs(payload: TransactionCreate) -> list[PostingInput]:
    return [
        PostingInput(p.account_id, p.amount_minor, p.currency) for p in payload.postings
    ]


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    start: date | None = None,
    end: date | None = None,
    account_id: int | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    if start:
        stmt = stmt.where(Transaction.date >= start)
    if end:
        stmt = stmt.where(Transaction.date <= end)
    if q:
        stmt = stmt.where(Transaction.description.ilike(f"%{q}%"))
    if account_id:
        stmt = stmt.where(
            Transaction.id.in_(
                select(Posting.transaction_id).where(Posting.account_id == account_id)
            )
        )
    return db.scalars(stmt.limit(limit).offset(offset)).unique().all()


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(404, f"transaction {transaction_id} not found")
    return tx


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    try:
        return ledger.create_transaction(
            db,
            tx_date=payload.date,
            description=payload.description,
            memo=payload.memo,
            external_id=payload.external_id,
            postings=_inputs(payload),
        )
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{transaction_id}", response_model=TransactionOut)
def replace_transaction(
    transaction_id: int, payload: TransactionCreate, db: Session = Depends(get_db)
):
    tx = db.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(404, f"transaction {transaction_id} not found")
    try:
        return ledger.replace_transaction(
            db,
            tx,
            tx_date=payload.date,
            description=payload.description,
            memo=payload.memo,
            postings=_inputs(payload),
        )
    except LedgerError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(404, f"transaction {transaction_id} not found")
    ledger.delete_transaction(db, tx)
