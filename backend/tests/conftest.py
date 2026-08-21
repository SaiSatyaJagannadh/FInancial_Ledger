import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("NVIDIA_API_KEY", "")

from app.models import Account, AccountType, Base  # noqa: E402


@pytest.fixture()
def db():
    """A fresh file-backed SQLite database per test (file, not :memory:, so the
    FastAPI dependency override and the test share the same data)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        os.unlink(path)


@pytest.fixture()
def accounts(db):
    """A minimal chart of accounts used across the ledger tests."""
    made = {}
    for code, name, type_ in [
        ("assets:checking", "Checking", AccountType.asset),
        ("assets:savings", "Savings", AccountType.asset),
        ("liabilities:card", "Credit Card", AccountType.liability),
        ("equity:opening", "Opening Balances", AccountType.equity),
        ("income:salary", "Salary", AccountType.income),
        ("expenses:food", "Food", AccountType.expense),
    ]:
        acct = Account(code=code, name=name, type=type_, currency="USD")
        db.add(acct)
        made[code] = acct
    db.commit()
    for acct in made.values():
        db.refresh(acct)
    return made
