# Financial Ledger — Project Build Spec

A double-entry personal/small-business ledger with an AI layer (NVIDIA NIM) for
transaction categorization and natural-language reporting.

## 1. Why double entry

Single-table "transactions with a category" ledgers cannot answer "where did the
money go" without guessing. Double entry makes every movement balance to zero, so
the books are self-checking: if the invariant holds, no money was invented or lost.

## 2. Domain model

**Money is stored as signed integer minor units** (cents). No floats anywhere in
the persistence or arithmetic path. Display formatting is the only place decimals
appear.

### Account
| field | type | notes |
|---|---|---|
| id | int PK | |
| code | str unique | e.g. `expenses:food:groceries` |
| name | str | display name |
| type | enum | `asset` `liability` `equity` `income` `expense` |
| currency | str(3) | ISO 4217, default `USD` |
| parent_id | int? | tree; enables rollups |
| archived | bool | hidden from pickers, history preserved |

Normal balance: `asset`/`expense` are debit-normal (positive = increase).
`liability`/`equity`/`income` are credit-normal (negative = increase).

### Transaction (journal entry)
| field | type | notes |
|---|---|---|
| id | int PK | |
| date | date | posting date |
| description | str | payee / merchant |
| memo | str? | |
| external_id | str? unique | dedupe key for imports |
| source | str | `manual` `csv` `rule` |

### Posting (split)
| field | type | notes |
|---|---|---|
| id | int PK | |
| transaction_id | int FK | cascade delete |
| account_id | int FK | restrict delete |
| amount_minor | int | signed; debit +, credit − |
| currency | str(3) | |

### Invariants (enforced in the service layer, tested)
1. A transaction has **>= 2 postings**.
2. `sum(amount_minor) == 0` **per currency** within a transaction.
3. Postings are immutable once written — edits replace the whole transaction.
4. Accounts with postings cannot be deleted, only archived.

## 3. Features

### Core
- CRUD accounts (tree), CRUD transactions (atomic, balanced).
- Account balances: point-in-time and as-of-date, with subtree rollup.
- Trial balance (must sum to zero — health check endpoint).

### Import
- CSV import with a column mapping (`date`, `description`, `amount`, or separate
  `debit`/`credit` columns).
- Dedupe on `external_id` (hash of date+description+amount when the bank gives none).
- Imports land as **two-posting transactions**: bank account + a holding account
  `expenses:uncategorized` / `income:uncategorized`, then get categorized.

### Rules engine (deterministic, runs before AI)
- Ordered rules: `match_type` (`contains` | `regex`), `pattern`, `account_id`.
- Applied on import and re-runnable over uncategorized transactions.

### AI layer (NVIDIA NIM, OpenAI-compatible)
- `POST /ai/categorize` — batch-classifies uncategorized transactions into existing
  expense/income accounts. Constrained to the real account list; returns confidence.
- `POST /ai/ask` — natural-language question → structured query over the ledger →
  numeric answer with the rows it used (no hallucinated numbers: the LLM picks
  filters, **the database computes the totals**).
- **Degradation:** with no `NVIDIA_API_KEY`, `/ai/categorize` falls back to the rules
  engine + a token-overlap heuristic, and `/ai/ask` returns a 503 with a clear
  message. The app is fully usable without the key.

### Reports
- Balance sheet (assets = liabilities + equity, as of a date).
- Income statement (income − expenses over a range).
- Monthly spend by category, with subtree rollup.
- Net worth trend.

## 4. Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2.x, SQLite (WAL). Pydantic v2 schemas.
- **AI:** `openai` SDK pointed at `https://integrate.api.nvidia.com/v1`.
- **Frontend:** Vite + React 18 + TypeScript + Tailwind, TanStack Query, Recharts.
- **Tests:** pytest (backend, incl. invariant + API tests), vitest (frontend units),
  a Chrome-driven E2E smoke pass over the running app.
- **CI:** GitHub Actions running both suites.

## 5. Layout

```
backend/
  app/
    main.py          FastAPI app + CORS + router mounts
    db.py            engine, session, init
    models.py        SQLAlchemy models
    schemas.py       Pydantic DTOs
    ledger.py        double-entry service — the invariants live here
    reports.py       balance sheet / income statement / trends
    rules.py         deterministic categorization
    ai.py            NVIDIA NIM client + fallbacks
    importer.py      CSV parsing + dedupe
    routers/         accounts, transactions, imports, reports, ai
  tests/
frontend/
  src/
    api.ts, types.ts
    pages/  Dashboard, Accounts, Transactions, Import, Ask, Reports
    components/
```

## 6. Acceptance

- [ ] Unbalanced transaction is rejected with a 422 naming the imbalance.
- [ ] Trial balance sums to zero after any sequence of API operations.
- [ ] CSV import of a file twice creates no duplicate transactions.
- [ ] Rules categorize on import; uncategorized remain visible and fixable.
- [ ] AI categorize works with a key, degrades without one; no crash either way.
- [ ] Every reported number traces to postings — no LLM-computed arithmetic.
- [ ] Backend and frontend suites green; Chrome smoke pass over all pages.
