from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import reports as reports_mod
from app.db import get_db
from app.schemas import (
    BalanceSheet,
    CategorySpend,
    IncomeStatement,
    MonthlyPoint,
    NetWorthPoint,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _default_range(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or date.today()
    start = start or (end - timedelta(days=365))
    return start, end


@router.get("/balance-sheet", response_model=BalanceSheet)
def balance_sheet(as_of: date | None = None, db: Session = Depends(get_db)):
    return reports_mod.balance_sheet(db, as_of)


@router.get("/income-statement", response_model=IncomeStatement)
def income_statement(
    start: date | None = None, end: date | None = None, db: Session = Depends(get_db)
):
    return reports_mod.income_statement(db, *_default_range(start, end))


@router.get("/spend-by-category", response_model=list[CategorySpend])
def spend_by_category(
    start: date | None = None,
    end: date | None = None,
    rollup: bool = True,
    db: Session = Depends(get_db),
):
    return reports_mod.spend_by_category(db, *_default_range(start, end), rollup=rollup)


@router.get("/monthly", response_model=list[MonthlyPoint])
def monthly(months: int = Query(default=12, ge=1, le=60), db: Session = Depends(get_db)):
    return reports_mod.monthly_trend(db, months)


@router.get("/net-worth", response_model=list[NetWorthPoint])
def net_worth(months: int = Query(default=12, ge=1, le=60), db: Session = Depends(get_db)):
    return reports_mod.net_worth_trend(db, months)
