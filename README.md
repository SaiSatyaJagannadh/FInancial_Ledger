# Financial Ledger

A double-entry ledger for personal or small-business books, with an AI layer
(NVIDIA NIM) that categorizes imported transactions and answers questions in
plain English.

The reason it is double entry: a single-table "transactions with a category"
ledger cannot prove it is correct. Here every movement of money balances to
zero, so the books are self-checking — `/health` reports the trial balance, and
if it is ever non-zero something is wrong and you find out immediately.

## Two rules the AI layer follows

1. **The model never does arithmetic.** For a question it picks *filters* —
   which accounts, which dates, which description text. The database sums the
   postings. A wrong filter is visible in the response; a wrong total would
   quietly corrupt your understanding of your own money.
2. **No API key is not an error.** Without `NVIDIA_API_KEY`, categorization
   falls back to your rules plus a local token heuristic, and the Ask page
   explains that it is off. Everything else works unchanged.

## Quick start

```bash
# backend
cd backend
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
cp .env.example .env          # optional: add your NVIDIA key
.venv/bin/python seed.py --reset   # 8 months of demo books
.venv/bin/python -m uvicorn app.main:app --port 8000

# frontend, in another shell
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Or run both with `./dev.sh`.

API docs are at http://localhost:8000/docs.

## How money is stored

Everything internal is a **signed integer in minor units** (cents). `Decimal`
appears only when parsing input and formatting output; no float ever touches a
balance. Binary floating point cannot represent `0.10` exactly, and a ledger
that drifts by a cent per thousand rows is a ledger nobody can trust.

Debits are positive, credits negative. `asset` and `expense` accounts are
debit-normal; `liability`, `equity` and `income` are credit-normal, so reports
flip their sign to show income of $500 as `+500.00` rather than `-500.00`.

## The invariants

Enforced in `backend/app/ledger.py`, which is the only write path for postings:

1. A transaction has at least two postings.
2. Postings sum to zero **per currency** — a EUR leg cannot be netted against a
   USD leg to fake a balanced entry.
3. Postings are immutable; editing a transaction replaces every leg.
4. An account with history is archived, never deleted.

## Importing

`Import` takes a CSV from a bank or card. Columns are detected by name (date /
posted date / transaction date, description / payee / merchant, and either an
amount column or a debit/credit pair). Nothing is written until you confirm the
preview.

Dedupe is a content hash of date + description + amount **plus an occurrence
counter**, so two identical $3.50 coffees on the same day are both kept while
re-importing the same file adds nothing. Rows nobody could categorize park in a
holding account rather than being guessed at.

## Layout

```
backend/app/
  ledger.py      double-entry service — the invariants live here
  reports.py     balance sheet, income statement, trends (all SQL)
  importer.py    CSV parsing, column detection, dedupe
  rules.py       deterministic pattern -> account matching
  ai.py          NVIDIA NIM client, query planning, fallbacks
  routers/       accounts, transactions, rules, imports, reports, ai
frontend/src/
  pages/         Dashboard, Transactions, Accounts, Import,
                 Categorize, Reports, Ask
```

## Tests

```bash
cd backend  && .venv/bin/python -m pytest      # 79 tests
cd frontend && npx vitest run                  # 21 tests
```

The backend suite covers the invariants, as-of balances, subtree rollup with a
cycle guard, CSV round trips, and the AI layer's failure modes — hallucinated
account ids are dropped, a model outage falls back to the heuristic, and a
total the model invents never reaches the response.

## A note on models

`meta/llama-3.1-8b-instruct` is the default because it is verified responsive on
this endpoint. Some larger models are listed by `/v1/models` but accept a chat
request and never reply. Every call carries a timeout so that failure degrades
to the local fallback instead of hanging.
