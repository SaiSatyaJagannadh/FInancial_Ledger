# Personal Ledger

A ledger for money lent to and repaid by people you know — family, friends. It
answers one question: **who owes me what.**

Streamlit app, Google Sheet as the store, with **rupees and dollars in separate tabs**.

![demo mode](https://img.shields.io/badge/no%20setup-runs%20in%20demo%20mode-blue)

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

That is the whole setup. With no credentials it opens in **demo mode** against
sample data and says so — that is how you look at it before deciding whether to
connect a sheet.

## Connect your own sheet

1. Create a Google Cloud **service account** and download its JSON key.
2. `cp .streamlit/secrets.toml.example .streamlit/secrets.toml` and paste the
   JSON fields in, plus your sheet's URL.
3. **Share the sheet with the service account's `client_email`** — this is the
   step people miss; without it the app sees a permission error and falls back
   to demo data.

The sheet needs one header row:

```
date | person | ledger | direction | amount | currency | note
```

An empty sheet gets that header written the first time you save.

## Two currencies, never mixed

The dashboard has a tab for **🇮🇳 Indian Rupees** and one for **🇺🇸 US Dollars**.
Each is a complete, independent view: its own totals, its own people filter, its
own chart.

They are never added together. ₹40,000 plus $1,000 is not a number without an
exchange rate that changes every day, so the app declines to invent one —
`totals()` raises on a mixed list rather than printing something plausible and
wrong.

Currency is part of a ledger's identity. Lending your brother rupees at home and
dollars abroad is two arrangements, tracked separately, even if you give them the
same name.

## The model

- A **ledger** is one running arrangement with one person — a bike loan, a rent
  float. A person can have several; they are tracked separately and summed
  together.
- An **entry** is one movement: `given` (money out to them) or `received`
  (money back).
- **Net owed = given − received.** Positive means they owe you, negative means
  you owe them, and it is allowed to go negative because that is a real state.
- Each entry carries its **currency**. A sheet written before currency existed
  reads as rupees, so nothing breaks when you add the column.
- A ledger is **open** while its net is non-zero. Pay it off and it stops being
  counted, with no status column to maintain.

Amounts are always positive; `direction` carries the sign. A negative amount is
rejected rather than quietly double-negating into a repayment.

## Money

Everything internal is **integer minor units** (paise, cents). Floats never touch a total, including
at the sheet boundary — `0.1 + 0.2` is not `0.3` in binary floating point, and a
ledger that drifts is worthless.

Rupees use Indian grouping — **₹6,76,800.00**, not ₹676,800.00. Dollars use the
Western convention, **$676,800.00**. Each tab formats with its own.

## Reading a messy sheet

Real sheets are filled in by hand, so reads tolerate:

- header case and stray spaces (`  Date `, `PERSON`)
- several date formats (`2026-01-24`, `24/01/2026`, `24 Jan 2026`)
- the words people actually type for direction — `gave`, `got`, `repaid`, `lent`
- blank spacer rows
- amounts written `1,200`, `₹1200`, `Rs 1200`, `$600`
- currency written `INR`, `usd`, `₹`, `$`, `Rs` — or left blank, meaning rupees

A row that still cannot be read is reported **by row number** rather than taking
the rest of the sheet down with it. If the sheet is unreachable the app shows
demo data and says so, because an empty page would read as "nobody owes you
anything".

## Layout

```
app.py                  dashboard
pages/1_Add_Entry.py    entry form
ledger/
  money.py              paise <-> ₹, Indian grouping
  models.py             Entry, validation, direction
  compute.py            every aggregate the UI shows
  store.py              Sheets read/append + demo fallback
  demo.py               deterministic sample data
  ui.py                 shared Streamlit pieces
```

`compute.py` holds all the arithmetic; the UI formats but never sums, so the
tests cover exactly what is on screen.

## Tests

```bash
.venv/bin/python -m pytest
```

128 tests: money parsing and Indian formatting, entry validation, the
aggregates, filter consistency, messy-sheet tolerance, and an end-to-end
load → append → reload over an in-memory sheet — plus the currency split: a
dollar entry never moves a rupee total, and a mixed total raises.
