from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountType(str, enum.Enum):
    asset = "asset"
    liability = "liability"
    equity = "equity"
    income = "income"
    expense = "expense"


#: Debit-normal types increase with a positive posting amount. The other three
#: are credit-normal, so a positive stored amount is a *decrease* for them.
DEBIT_NORMAL = {AccountType.asset, AccountType.expense}


def normal_sign(account_type: AccountType) -> int:
    return 1 if account_type in DEBIT_NORMAL else -1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[AccountType] = mapped_column(Enum(AccountType), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    parent: Mapped[Account | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Account]] = relationship(back_populates="parent")
    postings: Mapped[list[Posting]] = relationship(back_populates="account")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(256))
    memo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Dedupe key for imports: unique when present, free when NULL.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    postings: Mapped[list[Posting]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (UniqueConstraint("external_id", name="uq_transactions_external_id"),)


class Posting(Base):
    __tablename__ = "postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # Signed integer minor units (cents). Never a float: binary floating point
    # cannot represent 0.10 exactly and the balance invariant would drift.
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    transaction: Mapped[Transaction] = relationship(back_populates="postings")
    account: Mapped[Account] = relationship(back_populates="postings")

    __table_args__ = (
        CheckConstraint("amount_minor != 0", name="ck_postings_nonzero"),
        Index("ix_postings_account_tx", "account_id", "transaction_id"),
    )


class Rule(Base):
    """Deterministic categorization: pattern on the description -> account."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(256))
    match_type: Mapped[str] = mapped_column(String(16), default="contains")  # contains | regex
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    account: Mapped[Account] = relationship()
