"""Reporting. Every number here is computed by SQL over postings — nothing is
estimated, cached, or produced by a model."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ledger
from app.models import Account, AccountType, Posting, Transaction
from app.money import to_major
from app.schemas import (
    BalanceSheet,
    BalanceSheetLine,
    CategorySpend,
    IncomeStatement,
    MonthlyPoint,
    NetWorthPoint,
)


def _line(account: Account, minor: int) -> BalanceSheetLine:
    natural = ledger.natural_balance(account.type, minor)
    return BalanceSheetLine(
        account_id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        amount_minor=natural,
        amount=to_major(natural, account.currency),
    )


def balance_sheet(db: Session, as_of: date | None = None) -> BalanceSheet:
    as_of = as_of or date.today()
    own = ledger.balances_by_account(db, as_of)
    accounts = db.scalars(select(Account).order_by(Account.code)).all()

    buckets: dict[AccountType, list[BalanceSheetLine]] = defaultdict(list)
    for account in accounts:
        minor = own.get(account.id, 0)
        if minor == 0 and account.archived:
            continue
        if account.type in (AccountType.asset, AccountType.liability, AccountType.equity):
            buckets[account.type].append(_line(account, minor))

    assets = buckets[AccountType.asset]
    liabilities = buckets[AccountType.liability]
    equity = buckets[AccountType.equity]

    total_assets = sum(line.amount_minor for line in assets)
    total_liabilities = sum(line.amount_minor for line in liabilities)
    total_equity = sum(line.amount_minor for line in equity)

    # Retained earnings (income - expenses to date) is not a stored account, so
    # the identity only closes once it is folded into equity.
    retained = _period_net(db, None, as_of)

    return BalanceSheet(
        as_of=as_of,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets_minor=total_assets,
        total_liabilities_minor=total_liabilities,
        total_equity_minor=total_equity,
        balanced=total_assets == total_liabilities + total_equity + retained,
    )


def _typed_totals(
    db: Session, account_type: AccountType, start: date | None, end: date | None
) -> dict[int, int]:
    stmt = (
        select(Posting.account_id, func.sum(Posting.amount_minor))
        .join(Transaction)
        .join(Account, Account.id == Posting.account_id)
        .where(Account.type == account_type)
        .group_by(Posting.account_id)
    )
    if start:
        stmt = stmt.where(Transaction.date >= start)
    if end:
        stmt = stmt.where(Transaction.date <= end)
    return {aid: int(total or 0) for aid, total in db.execute(stmt).all()}


def _period_net(db: Session, start: date | None, end: date | None) -> int:
    """Income minus expenses over the window, in natural (positive) terms."""
    income = sum(_typed_totals(db, AccountType.income, start, end).values())
    expenses = sum(_typed_totals(db, AccountType.expense, start, end).values())
    return ledger.natural_balance(AccountType.income, income) - ledger.natural_balance(
        AccountType.expense, expenses
    )


def income_statement(db: Session, start: date, end: date) -> IncomeStatement:
    accounts = {a.id: a for a in db.scalars(select(Account)).all()}

    income_lines = [
        _line(accounts[aid], minor)
        for aid, minor in _typed_totals(db, AccountType.income, start, end).items()
    ]
    expense_lines = [
        _line(accounts[aid], minor)
        for aid, minor in _typed_totals(db, AccountType.expense, start, end).items()
    ]
    income_lines.sort(key=lambda x: -x.amount_minor)
    expense_lines.sort(key=lambda x: -x.amount_minor)

    total_income = sum(line.amount_minor for line in income_lines)
    total_expenses = sum(line.amount_minor for line in expense_lines)

    return IncomeStatement(
        start=start,
        end=end,
        income=income_lines,
        expenses=expense_lines,
        total_income_minor=total_income,
        total_expenses_minor=total_expenses,
        net_minor=total_income - total_expenses,
    )


def spend_by_category(
    db: Session, start: date, end: date, rollup: bool = True
) -> list[CategorySpend]:
    """Expense totals per account, optionally including each subtree."""
    own = _typed_totals(db, AccountType.expense, start, end)
    accounts = {a.id: a for a in db.scalars(select(Account)).all()}

    out: list[CategorySpend] = []
    for aid, account in accounts.items():
        if account.type is not AccountType.expense:
            continue
        if rollup:
            minor = sum(own.get(d, 0) for d in ledger.descendant_ids(db, aid))
        else:
            minor = own.get(aid, 0)
        if minor == 0:
            continue
        natural = ledger.natural_balance(AccountType.expense, minor)
        out.append(
            CategorySpend(
                account_id=aid,
                code=account.code,
                name=account.name,
                amount_minor=natural,
                amount=to_major(natural, account.currency),
            )
        )
    out.sort(key=lambda x: -x.amount_minor)
    return out


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def monthly_trend(db: Session, months: int = 12) -> list[MonthlyPoint]:
    """Income/expense per calendar month, most recent `months` that have data."""
    rows = db.execute(
        select(Transaction.date, Account.type, func.sum(Posting.amount_minor))
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Account.id == Posting.account_id)
        .where(Account.type.in_([AccountType.income, AccountType.expense]))
        .group_by(Transaction.date, Account.type)
    ).all()

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"income": 0, "expenses": 0})
    for tx_date, account_type, total in rows:
        key = _month_key(tx_date)
        natural = ledger.natural_balance(account_type, int(total or 0))
        bucket = "income" if account_type is AccountType.income else "expenses"
        buckets[key][bucket] += natural

    points = [
        MonthlyPoint(
            month=key,
            income_minor=vals["income"],
            expenses_minor=vals["expenses"],
            net_minor=vals["income"] - vals["expenses"],
        )
        for key, vals in sorted(buckets.items())
    ]
    return points[-months:]


def net_worth_trend(db: Session, months: int = 12) -> list[NetWorthPoint]:
    """Assets minus liabilities at each month end, cumulative by construction."""
    rows = db.execute(
        select(Transaction.date, Account.type, func.sum(Posting.amount_minor))
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Account.id == Posting.account_id)
        .where(Account.type.in_([AccountType.asset, AccountType.liability]))
        .group_by(Transaction.date, Account.type)
        .order_by(Transaction.date)
    ).all()
    if not rows:
        return []

    per_month: dict[str, dict[str, int]] = defaultdict(lambda: {"assets": 0, "liabilities": 0})
    for tx_date, account_type, total in rows:
        key = _month_key(tx_date)
        natural = ledger.natural_balance(account_type, int(total or 0))
        bucket = "assets" if account_type is AccountType.asset else "liabilities"
        per_month[key][bucket] += natural

    points: list[NetWorthPoint] = []
    assets = liabilities = 0
    for key in sorted(per_month):
        assets += per_month[key]["assets"]
        liabilities += per_month[key]["liabilities"]
        points.append(
            NetWorthPoint(
                month=key,
                assets_minor=assets,
                liabilities_minor=liabilities,
                net_worth_minor=assets - liabilities,
            )
        )
    return points[-months:]
