---
name: financial-ledger-playbook
description: The development history and hard-won engineering rules of FInancial_Ledger — a Streamlit + Google Sheets personal lending ledger built over 52 commits. Use this whenever working in this repo, and reach for it on any task that stores money in a spreadsheet, writes to Google Sheets through gspread, rounds or formats currency, builds Streamlit forms/dialogs/multi-page apps, or decides what an LLM may and may not compute. It carries the specific defects that reached the real sheet — an append that overwrote a live row, a guard that matched the wrong entry and deleted a ₹2,00,000 loan, a model narrating cents as dollars at 100x — with the root cause and the rule that stopped each recurring. Consult it before adding a write path, a totals figure, a retry, or an LLM call to anything holding money.
---

# Financial Ledger Playbook

What 52 commits over 11 days taught us about putting real money in a
spreadsheet with an LLM nearby. Every bug below actually happened; most were
found in the deployed app or in the live sheet, not in tests.

The point of this skill is not the anecdotes. It is that each one has a
general shape that recurs, and the shape is what to watch for.

For the commit-by-commit history, read `references/history.md`.

## The shape of the thing

A Streamlit app where **one Google Sheets workbook is the entire database** —
five tabs, five modules, no SQL. Money is integer minor units everywhere.
`compute.py` holds every aggregate; views format but never sum. Currencies
never mix. ~640 tests, plus `demo()` self-checks inside ten modules.

## Phase 0: we built the wrong thing first

The first twelve commits were a double-entry accounting system — chart of
accounts, debits and credits, FastAPI backend, React frontend, trial balance.
It worked. It was thrown away whole at `76b997f`:

> The build so far solved the wrong problem: a business chart of accounts with
> debits and credits, when what was wanted is a record of money lent to family
> and friends and paid back.

**The lesson is not "spec harder".** It is that a working implementation of
the wrong model is *more* expensive than a broken one, because it argues for
its own survival. When the domain model and the user's mental model disagree,
delete the code. The old stack stayed in git history; nothing was lost by
being ruthless in the working tree.

A tell worth heeding: nobody lending money to family thinks in debits and
credits. If your users would not use your nouns, they are the wrong nouns.

## Bugs that moved money

These are the ones that matter. Every other class of bug is cosmetic beside
them.

### A guard that matched by shape deleted a real loan

The worst defect in the project, and it was introduced *deliberately and with
reasoning*. `find_ledger_entry` located the ledger row belonging to an
interest charge by matching person + ledger + currency + date + direction. A
commit message even defended the choice: matching on the note would break when
someone reworded it.

But money handed to that same person on the first of the month is *also* a
"given" row for them on that date. The next interest save found the loan,
called `store.update`, and wrote ₹5,000 over ₹2,00,000. The row still said
"given". The money actually lent was gone from the sheet with nothing anywhere
recording it.

The fix: identify the row by the **trail the writer itself stamps into the
note**, never by shape.

> **Rule.** A row you may later overwrite must be identified by provenance you
> wrote, not by properties it happens to have. Shape is a fingerprint that
> other records share. Ask: *could a record I did not create satisfy this
> predicate?* If yes, the predicate is a data-loss bug waiting for a
> coincidence.

### Editing a row to flip its direction erased half the history

Recording "they paid me back" by opening the loan and switching its Direction
from *gave* to *got back* rewrites what happened. The ledger stops saying
₹2,00,000 went out and some came back, and says only that money was returned.
Both halves are needed to reconcile; one is worse than useless.

The dialog now offers **Add as a new entry** beside **Save changes**, and
leads with it when Direction is what changed.

> **Rule.** In an append-only domain — ledgers, audit logs, event histories —
> an edit that changes the *meaning* of a record is nearly always a second
> record. Offer both, and let the destructive one be the button nobody reaches
> by accident.

### An upsert plus a bare append lent the same money twice

An interest charge is an upsert (one figure per person per month), but the
ledger write beside it was a plain `append`. Saving twice left one interest
row and *two* identical loans. Found in the sheet: rows 45 and 46
byte-for-byte the same. A person was shown owing ₹15,000 more than he did.

> **Rule.** When one user action writes to two stores, their write semantics
> must match. An idempotent write beside a non-idempotent one is a duplicate
> on the second click, and the second click always comes.

### The model narrated cents as dollars

The computed total was right to the cent — verified against raw SQL. The
*sentence above it* said `$611,297` for `6112.97`. `run_query` returned rows
carrying `amount_minor`, and the narration step handed those raw rows to the
model, which read cents as dollars.

Fix: the model never sees a raw minor unit, only pre-formatted currency
strings, and its sentence is rejected unless it repeats our figure verbatim.

> **Rule.** A person reads the sentence before the number. A correct figure
> underneath a wrong sentence is a wrong answer. Never hand an LLM a unit it
> has to interpret; hand it the rendered string.

### A float on the persistence boundary

`to_row` rendered the amount as `paise / 100`. Money is integer minor units
everywhere precisely so that no float exists — and the one place it appeared
was the write to the sheet.

Now `divmod`, with a test using a value beyond what floats represent exactly.

> **Rule.** The invariant is only as strong as its boundary. Test integer-money
> discipline at serialisation with a value that float would visibly corrupt,
> or the discipline is decorative.

## Writes to a spreadsheet are not what you think

### `values.append` overwrites by default

Google's `insertDataOption` defaults to `OVERWRITE`. "Append" does not mean
"put this at the bottom": Sheets ends the table at the first wholly blank row
and writes there, over whatever it finds. A one-row append survives by luck
(the gap is at least one row wide). A **multi-row** append does not — and
attachments write one row per 40,000 characters of base64.

Every append in the app now goes through one `store.append_rows` that sets
`INSERT_ROWS`.

> **Rule.** Read the default of every destructive-capable API parameter before
> shipping it. "It worked when I tried it" and "it is safe" are different
> claims, and a gap-dependent bug will wait months for the right sheet state.

### Retry belongs in exactly one place

Google Sheets answers a perfectly good request with `503 The service is
currently unavailable` at random. One of them was enough to drop the whole app
into demo mode.

Fixed by subclassing gspread's `HTTPClient` and retrying inside `request` —
**every read and write in the app, all five tabs, goes out through that one
method.** Call sites never ask for retrying.

Specifics that generalise:
- 408/429/5xx retried; 4xx never (a revoked key will not pass on the fourth try).
- A dropped connection is retried **only for GET** — the reply to a lost POST
  may already have been applied, and repeating it appends the entry twice.
- Waits `(0.4, 1.0, 2.5)`: four attempts under four seconds. The library's own
  backoff starts at 2s and doubles to 128s, which nobody watching a web page
  will sit through.
- The authorised client is cached; building one costs a network round trip,
  and doing that per operation added another chance of a 503 to every read.

> **Rule.** Retry at the single chokepoint every call already passes through.
> Retry at call sites is both incomplete and impossible to reason about. And a
> retry policy is a *product* decision: the right backoff for a batch job is
> the wrong one for a page load.

### An unreachable store shows nothing, never sample data

A 503 used to fall back to demo entries under a heading reading "your ledger" —
other people's names and figures presented as yours. There is no way to read
that screen which is true.

> **Rule.** Fallback data must never be able to impersonate real data. Blank
> plus an explanation beats plausible and wrong.

## Where the LLM is allowed to go

The app has a chat assistant. The discipline that made it trustworthy:

- **The model proposes; it never writes.** Output is validated through the same
  `Entry.from_row` a sheet row goes through, and a human clicks Save. A misread
  "5,000" as "50,000" cannot land.
- **Arithmetic is never asked of it.** `facts.py` answers the common questions
  in code — a balance, a ranking, totals, last activity — and only unrecognised
  questions reach the endpoint. Exact and instant instead of 1–3 seconds of
  something that can be confidently wrong about money.
- **`answer() -> str | None`, and `None` is the important half.** A question
  naming somebody unidentifiable returns `None` rather than falling back to the
  grand total — a real figure that answers a different question. That fallback
  was a genuine bug: "how much does Kavita owe me" confidently reported the
  whole book.
- **Deliberately no RAG.** The whole ledger is ~700 tokens against a 128,000
  window. There is nothing to retrieve *from*; embedding it would add a round
  trip and then ask the model to sum the rows it got back.
- **Facts the text already contains are parsed, not inferred.** Every candidate
  model read "gave amma $250" as rupees. Currency now comes from the user's
  words. (Match on word boundaries — `rs` sits inside `dollars`.)
- **Names are canonicalised deterministically**, not by prompt.
- **Nothing network-shaped escapes the module.** `_post` converts *every*
  failure into one error type. The view caught only that type, so a raw
  `Timeout` became a red traceback.

> **Rule.** Decide what the model is *for*. If code can answer it exactly,
> code answers it. The model is for the part that is genuinely language.

## Streamlit specifics that will bite

- **Clearing a form needs new widget keys, not a cleared value.** Popping the
  key makes the app forget while the browser keeps displaying the old text —
  Save went disabled and the preview vanished while "777" still sat in the box.
  Carry a round number in every key; incrementing it makes new widgets, and new
  widgets render empty.
- **`st.rerun()` inside a dialog dismisses it**, taking every field the person
  just typed. Anything in a dialog that needs a rerun to fetch belongs outside
  it.
- **Everything interpolated into `unsafe_allow_html` must be escaped**, and
  every URL scheme-checked. A note is free text — and is also written by a model
  out of an uploaded document. `javascript:` in an attachment box became a
  working link.
- **`st.set_page_config` lives in the router and nowhere else**; declare pages
  with `st.navigation` or the sidebar names the entry page after its filename
  ("app").
- **Test that every page renders.** A module shadowed by a local variable
  (`people = sorted(...)` over `from ledger import people`) crashed one page
  while that module's own unit tests passed. CI's boot-and-curl only renders the
  default page; run every view through `AppTest`.

## Tests that passed while the code was broken

The most dangerous category, because it buys false confidence.

- **A fake that flatters the API.** `FakeSheet.append_rows` was
  `self.rows.append(row)` — the convenient fiction. The entire suite stayed
  green over a write path that destroys rows on the real sheet. The fake now
  reproduces Sheets' table detection and OVERWRITE semantics.
- **A comparison that only holds against the fake.** The row-identity check
  compared the amount as *text*. Sheets returns `42` for what was written as
  `42.00`, so it passed in tests and failed against the real sheet.
- **A guard that pinned the example instead of the rule.** A column test
  asserted the last two names; adding a column broke it for no reason. It now
  asserts the original seven stay first and in order.
- **An assertion that depended on the model's choice.** An acceptance check
  asserted the model would always filter to groceries, so it failed whenever
  the model legitimately chose a broader filter — reporting an app defect where
  there was none. It now recomputes whatever plan came back independently and
  compares, which is the property actually wanted.

> **Rule.** A test double must copy what the real system *does*, not what
> would be convenient. When a fake and production disagree, the fake wins
> silently and forever. And when testing anything stochastic, assert the
> invariant, never the sample.

## Platform walls — recognise them and stop pushing

Two problems ate real time before being recognised as unfixable:

- **A service account has no Drive storage quota.** Sharing a folder with it
  does not help; a file it creates would be owned by it, and it has nowhere to
  own anything. Google's own answer is a Shared Drive or OAuth delegation, both
  needing paid Workspace. A personal account can do neither. Attachments went
  into the spreadsheet as base64 instead.
- **fpdf ships latin-1 core fonts** and raises on anything else. A rupee sign
  in a *note* took the whole export down while the same sign in an *amount* was
  safe, because only the money was guarded. Every string reaching the PDF is now
  transliterated with readable stand-ins — a statement missing one glyph beats
  no statement.

> **Rule.** When the third attempt fails for the same underlying reason, stop
> and ask whether it is a wall. Route around it and write down *why*, or the
> next person spends the same afternoon. Guard the whole class, not the one
> field where you saw the error.

## The rules, distilled

1. A working implementation of the wrong model costs more than no code.
2. Identify a mutable row by provenance you wrote, never by its shape.
3. In an append-only domain, a meaning-changing edit is a second record.
4. Match write semantics when one action writes to two stores.
5. Never hand an LLM a raw unit; hand it the rendered string.
6. Test the money invariant *at the boundary*, with a value that breaks floats.
7. Read the defaults of destructive-capable APIs.
8. Retry at the one chokepoint; never at call sites.
9. Fallback data must not be able to impersonate real data.
10. If code can answer it exactly, code answers it.
11. A test double copies what the real system does, not what is convenient.
12. Assert the invariant, never the sample.
13. Recognise a wall, route around it, and write down why.
14. Correct a stale doc claim in the same commit as the behaviour it describes.

## Working in this repo

`CLAUDE.md` is the current-state reference — architecture, invariants, and
known dead paths. This skill is the *history*: why each rule exists and what
it cost to learn. When they disagree, `CLAUDE.md` is the code and this is the
reasoning.

Before changing a write path, read the "Writing to the sheet" section of
`CLAUDE.md` and the two write-bug sections above. Run `pytest -q` and the
`demo()` self-checks (`python -m ledger.<module>`); both are cheap and both
have caught real regressions.
