from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import Depends

from app import ledger
from app.config import get_settings
from app.db import get_db, init_db
from app.models import Account, Transaction
from app.routers import accounts, ai, imports, reports, rules, transactions
from app.schemas import HealthOut

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Financial Ledger",
    version="0.1.0",
    description="Double-entry ledger with NVIDIA NIM categorization and Q&A.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (accounts, transactions, rules, imports, reports, ai):
    app.include_router(module.router)


@app.get("/health", response_model=HealthOut, tags=["meta"])
def health(db: Session = Depends(get_db)):
    """Includes the trial balance: if `balanced` is ever false, the books are
    broken and that is the first thing anyone needs to know."""
    trial = ledger.trial_balance(db)
    return HealthOut(
        status="ok" if trial == 0 else "unbalanced",
        trial_balance_minor=trial,
        balanced=trial == 0,
        accounts=int(db.scalar(select(func.count(Account.id))) or 0),
        transactions=int(db.scalar(select(func.count(Transaction.id))) or 0),
        ai_enabled=settings.ai_enabled,
    )
