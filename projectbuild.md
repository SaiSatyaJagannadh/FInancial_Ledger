# Personal Ledger — Project Build Spec

A ledger for money lent to and repaid by **people you know** — family, friends —
not a business chart of accounts. It answers one question: *who owes me what.*

Streamlit UI, Google Sheet as the store, amounts in ₹ (INR).

## 1. The domain

You give money to a person; they give some back over time. A **ledger** is one
running arrangement with one person (a loan, a shared expense, a standing float).
A person can have several — a brother with a bike loan and a rent float is two
ledgers, tracked separately, summarised together.

An **entry** is a single movement:

| field | type | notes |
|---|---|---|
| date | date | when the money moved |
| person | str | who it was with |
| ledger | str | which arrangement, e.g. `Bike loan` |
| direction | enum | `given` (money out to them) / `received` (money back) |
| amount | ₹ | always positive; `direction` carries the sign |
| note | str? | free text |

**Net owed = given − received.** Positive means they owe you. Negative means you
owe them. A ledger is **open** while its net is non-zero.

Money is stored as **integer paise**. No float ever touches a total: `0.1 + 0.2`
is not `0.3` in binary floating point, and a ledger that drifts is worthless.

## 2. Store

A single Google Sheet, one row per entry, columns exactly as above.

**Demo mode is the default.** With no credentials in `.streamlit/secrets.toml`
the app loads deterministic sample data and says so in a banner. This is not a
degraded state — it is how you evaluate the app before wiring your own sheet up.

Credentials are a Google service account; the sheet is shared with that
account's email. Reads are cached briefly so a rerun does not re-fetch.

## 3. Screens

### Dashboard (`app.py`)
- **Filters:** period (last 6/12/24 months, all time), people (multi-select).
- **Headline:** total given, total received, net outstanding, people count, and
  how many ledgers are still open.
- **Who owes me what:** one row per person — given, received, net owed, last
  activity, ledger count. Sorted by net owed, largest first.
- **Money given per month:** grouped bars per person per month, with the
  underlying numbers available as a table.

### Add Entry (`pages/`)
A form: date, person, ledger, direction, amount, note. Person and ledger offer
existing values but accept new ones, so a new arrangement needs no setup step.
Appends one row and invalidates the cache.

## 4. Stack

Python 3.11, Streamlit, gspread + google-auth, pandas, Altair.
pytest for the arithmetic and filtering. A Chrome pass over the running app.
GitHub Actions runs the suite.

## 5. Layout

```
app.py                  dashboard
pages/1_Add_Entry.py    entry form
ledger/
  money.py              paise <-> ₹, INR formatting
  models.py             Entry, validation, direction enum
  compute.py            filtering and every aggregate the UI shows
  store.py              Google Sheets read/append + demo fallback
  demo.py               deterministic sample data
tests/
```

## 6. Acceptance

- [ ] Totals are exact in paise; no float appears in an arithmetic path.
- [ ] Net owed per person = given − received, and the people rows sum to the
      headline totals.
- [ ] Open-ledger count counts ledgers whose net is non-zero.
- [ ] Period and people filters change every figure on the page consistently.
- [ ] Demo mode runs with no credentials and says it is demo mode.
- [ ] Adding an entry appends one row and the dashboard reflects it.
- [ ] A person with more money received than given shows a negative net.
- [ ] Test suite green; Chrome pass over both screens.
