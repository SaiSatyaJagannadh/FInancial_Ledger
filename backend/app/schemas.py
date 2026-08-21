from __future__ import annotations

from datetime import date as Date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models import AccountType
from app.money import to_major


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    type: AccountType
    currency: str = Field(default="USD", min_length=3, max_length=3)
    parent_id: int | None = None

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        return v.strip().lower()


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: int | None = None
    archived: bool | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    type: AccountType
    currency: str
    parent_id: int | None
    archived: bool


class AccountBalanceOut(AccountOut):
    """An account plus the numbers the UI shows next to it."""

    balance_minor: int = 0
    balance: Decimal = Decimal(0)
    rollup_minor: int = 0
    rollup: Decimal = Decimal(0)


class PostingIn(BaseModel):
    account_id: int
    #: Signed minor units. Debits positive, credits negative.
    amount_minor: int
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class PostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    amount_minor: int
    currency: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount(self) -> Decimal:
        """Major-unit view for the UI; the minor field stays authoritative."""
        return to_major(self.amount_minor, self.currency)


class TransactionCreate(BaseModel):
    date: Date
    description: str = Field(min_length=1, max_length=256)
    memo: str | None = Field(default=None, max_length=512)
    postings: list[PostingIn] = Field(min_length=2)
    external_id: str | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: Date
    description: str
    memo: str | None
    source: str
    external_id: str | None
    postings: list[PostingOut]


class RuleCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=256)
    match_type: str = "contains"
    account_id: int
    priority: int = 100
    active: bool = True

    @field_validator("match_type")
    @classmethod
    def known_match_type(cls, v: str) -> str:
        if v not in ("contains", "regex"):
            raise ValueError("match_type must be 'contains' or 'regex'")
        return v


class RuleOut(RuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ImportPreviewRow(BaseModel):
    date: Date
    description: str
    amount_minor: int
    external_id: str
    suggested_account_id: int | None = None
    suggested_account_code: str | None = None
    duplicate: bool = False


class ImportPreview(BaseModel):
    rows: list[ImportPreviewRow]
    total: int
    duplicates: int
    errors: list[str] = []


class ImportCommit(BaseModel):
    account_id: int
    rows: list[ImportPreviewRow]


class ImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str] = []


class BalanceSheetLine(BaseModel):
    account_id: int
    code: str
    name: str
    type: AccountType
    amount_minor: int
    amount: Decimal


class BalanceSheet(BaseModel):
    as_of: Date
    assets: list[BalanceSheetLine]
    liabilities: list[BalanceSheetLine]
    equity: list[BalanceSheetLine]
    total_assets_minor: int
    total_liabilities_minor: int
    total_equity_minor: int
    #: assets - (liabilities + equity). Zero when the books are square.
    balanced: bool


class IncomeStatement(BaseModel):
    start: Date
    end: Date
    income: list[BalanceSheetLine]
    expenses: list[BalanceSheetLine]
    total_income_minor: int
    total_expenses_minor: int
    net_minor: int


class CategorySpend(BaseModel):
    account_id: int
    code: str
    name: str
    amount_minor: int
    amount: Decimal


class MonthlyPoint(BaseModel):
    month: str  # YYYY-MM
    income_minor: int
    expenses_minor: int
    net_minor: int


class NetWorthPoint(BaseModel):
    month: str
    assets_minor: int
    liabilities_minor: int
    net_worth_minor: int


class HealthOut(BaseModel):
    status: str
    trial_balance_minor: int
    balanced: bool
    accounts: int
    transactions: int
    ai_enabled: bool


class CategorizeRequest(BaseModel):
    transaction_ids: list[int] | None = None
    limit: int = Field(default=25, ge=1, le=100)


class CategorySuggestion(BaseModel):
    transaction_id: int
    description: str
    account_id: int
    account_code: str
    confidence: float
    reason: str
    source: str  # rule | heuristic | llm


class CategorizeResponse(BaseModel):
    suggestions: list[CategorySuggestion]
    source: str
    note: str | None = None


class ApplySuggestion(BaseModel):
    transaction_id: int
    account_id: int


class ApplyRequest(BaseModel):
    assignments: list[ApplySuggestion]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskResponse(BaseModel):
    question: str
    answer: str
    #: The filters the model chose. The database, not the model, did the math.
    query: dict
    total_minor: int
    total: Decimal
    rows: list[dict]
