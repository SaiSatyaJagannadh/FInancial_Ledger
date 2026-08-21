"""Populate a ledger with a realistic chart of accounts and ~8 months of activity.

    python seed.py [--reset]

Deterministic: the same run produces the same books, so the numbers in a demo
or a screenshot do not move around between runs.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from app.db import SessionLocal, engine, init_db
from app.ledger import PostingInput, create_transaction, trial_balance
from app.models import Account, AccountType, Base, Rule

CHART = [
    ("assets:checking", "Checking", AccountType.asset),
    ("assets:savings", "Savings", AccountType.asset),
    ("liabilities:card", "Credit Card", AccountType.liability),
    ("equity:opening", "Opening Balances", AccountType.equity),
    ("income:salary", "Salary", AccountType.income),
    ("income:interest", "Interest", AccountType.income),
    ("expenses:housing", "Housing", AccountType.expense),
    ("expenses:food", "Food", AccountType.expense),
    ("expenses:food:groceries", "Groceries", AccountType.expense),
    ("expenses:food:dining", "Dining Out", AccountType.expense),
    ("expenses:transport", "Transport", AccountType.expense),
    ("expenses:utilities", "Utilities", AccountType.expense),
    ("expenses:health", "Health", AccountType.expense),
    ("expenses:entertainment", "Entertainment", AccountType.expense),
    ("expenses:shopping", "Shopping", AccountType.expense),
]

RULES = [
    ("WHOLE FOODS", "expenses:food:groceries"),
    ("TRADER JOE", "expenses:food:groceries"),
    ("SHELL", "expenses:transport"),
    ("UBER", "expenses:transport"),
    ("NETFLIX", "expenses:entertainment"),
    ("CVS PHARMACY", "expenses:health"),
]

#: (description, account code, low, high) in dollars.
MERCHANTS = [
    ("WHOLE FOODS MARKET", "expenses:food:groceries", 42, 160),
    ("TRADER JOES #451", "expenses:food:groceries", 25, 95),
    ("BLUE BOTTLE COFFEE", "expenses:food:dining", 4, 14),
    ("THAI GARDEN", "expenses:food:dining", 22, 68),
    ("SHELL OIL 4432", "expenses:transport", 32, 74),
    ("UBER TRIP", "expenses:transport", 9, 41),
    ("CVS PHARMACY", "expenses:health", 8, 62),
    ("NETFLIX.COM", "expenses:entertainment", 15, 15),
    ("AMAZON MKTPL", "expenses:shopping", 12, 210),
    ("CITY POWER & LIGHT", "expenses:utilities", 68, 145),
]


def build(db, months: int = 8) -> None:
    accounts = {}
    for code, name, type_ in CHART:
        parent = None
        if code.count(":") == 2:
            parent = accounts[code.rsplit(":", 1)[0]].id
        account = Account(code=code, name=name, type=type_, parent_id=parent)
        db.add(account)
        db.commit()
        db.refresh(account)
        accounts[code] = account

    for pattern, target in RULES:
        db.add(Rule(pattern=pattern, match_type="contains", account_id=accounts[target].id))
    db.commit()

    rng = random.Random(20260820)
    today = date.today()
    start = date(today.year, today.month, 1) - timedelta(days=31 * (months - 1))
    start = date(start.year, start.month, 1)

    # Opening balances: money has to come from somewhere, and equity is where.
    create_transaction(
        db,
        tx_date=start,
        description="Opening balances",
        source="seed",
        postings=[
            PostingInput(accounts["assets:checking"].id, 420000),
            PostingInput(accounts["assets:savings"].id, 1250000),
            PostingInput(accounts["equity:opening"].id, -1670000),
        ],
    )

    day = start
    while day <= today:
        if day.day == 1:
            create_transaction(
                db,
                tx_date=day,
                description="ACME CORP PAYROLL",
                source="seed",
                postings=[
                    PostingInput(accounts["assets:checking"].id, 520000),
                    PostingInput(accounts["income:salary"].id, -520000),
                ],
            )
            create_transaction(
                db,
                tx_date=day,
                description="RENT PAYMENT",
                source="seed",
                postings=[
                    PostingInput(accounts["expenses:housing"].id, 185000),
                    PostingInput(accounts["assets:checking"].id, -185000),
                ],
            )
            create_transaction(
                db,
                tx_date=day,
                description="TRANSFER TO SAVINGS",
                source="seed",
                postings=[
                    PostingInput(accounts["assets:savings"].id, 60000),
                    PostingInput(accounts["assets:checking"].id, -60000),
                ],
            )
        if day.day == 28:
            create_transaction(
                db,
                tx_date=day,
                description="SAVINGS INTEREST",
                source="seed",
                postings=[
                    PostingInput(accounts["assets:savings"].id, 1450),
                    PostingInput(accounts["income:interest"].id, -1450),
                ],
            )

        for _ in range(rng.choice([0, 1, 1, 2, 3])):
            description, target, low, high = rng.choice(MERCHANTS)
            amount = rng.randint(low * 100, high * 100)
            # Roughly a third of spending goes on the card, the rest on debit.
            funding = "liabilities:card" if rng.random() < 0.35 else "assets:checking"
            create_transaction(
                db,
                tx_date=day,
                description=description,
                source="seed",
                postings=[
                    PostingInput(accounts[target].id, amount),
                    PostingInput(accounts[funding].id, -amount),
                ],
            )
        day += timedelta(days=1)

    # A few uncategorized rows so the Categorize page has work to do.
    holding = Account(
        code="expenses:uncategorized", name="Uncategorized Expenses", type=AccountType.expense
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)

    for offset, (description, amount) in enumerate(
        [
            ("SQ *BAKERY ON MAIN", 1850),
            ("PADDLE.NET* SOFTWARE", 4900),
            ("MTA VENDING MACHINE", 3400),
            ("WHOLE FOODS MARKET", 7325),
        ]
    ):
        create_transaction(
            db,
            tx_date=today - timedelta(days=offset + 1),
            description=description,
            source="seed",
            postings=[
                PostingInput(holding.id, amount),
                PostingInput(accounts["assets:checking"].id, -amount),
            ],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop every table first")
    args = parser.parse_args()

    if args.reset:
        Base.metadata.drop_all(engine)
    init_db()

    db = SessionLocal()
    try:
        if db.query(Account).count() and not args.reset:
            print("ledger already has accounts; pass --reset to rebuild")
            return
        build(db)
        assert trial_balance(db) == 0, "seed produced unbalanced books"
        print(
            f"seeded {db.query(Account).count()} accounts, "
            f"{db.query(Rule).count()} rules, trial balance 0"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
