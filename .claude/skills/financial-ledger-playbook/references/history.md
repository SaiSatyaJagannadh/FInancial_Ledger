# FInancial_Ledger — the development history

52 commits, 2026-08-20 to 2026-08-30. Read this when you need the specific
commit behind a rule in `SKILL.md`, or when you want the order things were
learned in. Every claim here is drawn from a commit message in this repo.

## Contents

- [Phase 1 — the wrong product (c84c265 … ed48388)](#phase-1--the-wrong-product)
- [Phase 2 — the pivot (76b997f)](#phase-2--the-pivot)
- [Phase 3 — building the real app (584e9b2 … 8cca093)](#phase-3--building-the-real-app)
- [Phase 4 — surviving reality (bb5d70f … 1b82388)](#phase-4--surviving-reality)
- [Phase 5 — interest, grouping, and the money bugs (43cd2a4 … 7fc249e)](#phase-5--interest-grouping-and-the-money-bugs)

---

## Phase 1 — the wrong product

A double-entry accounting system: FastAPI backend, React frontend, chart of
accounts, debits and credits, CSV import, rules-based categorisation, an
AI query layer.

| Commit | What it settled |
|---|---|
| `8aaf602` | Ledger core: models, money arithmetic, double-entry service |
| `d969d9f` | API: accounts, transactions, rules, CSV import, reports, AI |
| `531643d` | React frontend |
| `95d7f8e` | **The OpenAI client had no timeout**, so a stalled model held an HTTP worker open indefinitely instead of falling back. Also: two models listed by `/v1/models` accept a chat request and never reply (>90s, no bytes). *Being listed is not being usable — verify end to end.* |
| `1448788` | **Three defects found by driving the app in a browser**, none visible from tests: custom CSS outside a cascade layer beat every Tailwind utility (`.field{width:100%}` silently defeated `w-40`); Recharts 3 renders at zero size and relies on an entry animation that never completed, so four charts drew correct axes over empty plots; the category chart used rolled-up totals so a parent and its children both appeared and the same dollars were drawn twice. |
| `01d25c5` | **The narrated answer misstated the figure.** Computed totals matched raw SQL to the cent, but the sentence above said `$611,297` for `6112.97`. `run_query` returned rows carrying `amount_minor` and narration handed them straight to the model. Fixed by showing the model only formatted strings and rejecting any sentence that does not repeat our total verbatim. |
| `ed48388` | **A flaky acceptance check reported an app defect where there was none** — it asserted the model would always filter to groceries. Now recomputes whatever plan came back independently in SQL and compares the property the spec actually asks for. |

Lessons that survived the rewrite: verify a model endpoint end to end;
drive the UI in a real browser; never let a model restate a number; assert
invariants rather than one lucky sample.

## Phase 2 — the pivot

**`76b997f` — Replace the double-entry stack with the personal lending ledger spec.**

> The build so far solved the wrong problem: a business chart of accounts with
> debits and credits, when what was wanted is a record of money lent to family
> and friends and paid back — who owes me what, in rupees.

The FastAPI backend and React frontend were deleted. Respecified around
people, ledgers and entries, with a Google Sheet as the store and demo data as
the default path. The old stack remains in history at `ed48388`.

## Phase 3 — building the real app

| Commit | What it settled |
|---|---|
| `584e9b2` | Entries, aggregates, Google Sheets store, demo data |
| `5fc5e44` | **A float on the persistence boundary**: `to_row` used `paise / 100`. Now integer `divmod`, tested with a value beyond exact float range. Six integration tests over an in-memory sheet; CI runs with no secrets so the demo path is what gets proven, then boots the app headlessly. 102 tests. |
| `28648aa` | Rupee and dollar ledgers split — currencies never mix |
| `6111125` | **A header comment predated the currency split** and listed six columns where `COLUMNS` had seven. A sheet built from the docs would fail every row. *Stale docs are a defect.* |
| `1c49e78` | **A 2020 entry fell outside the "Last 24 months" default and simply did not appear.** A debt does not expire. Default is now all time, and a filter that empties the view reports how many entries are hidden rather than implying there are none. |
| `2c37f38` | Assistant, invested-instead view, delete, attachments |
| `f30a8cd` | **Find the API key wherever it was pasted** — a TOML section header swallows every key beneath it, so a key pasted at the bottom lands inside the last `[section]` and a top-level lookup misses it. |
| `3ca0765` | **The sidebar read "app"** because Streamlit names the entry page after its filename. Fixed with `st.navigation`. |
| `1bcdb6b` | Editing, with the write going to the row the entry actually lives on and **checking that row still holds it first** — the same guard delete uses. A `source` column records whether a row was typed, drafted from chat, or read from an image. |
| `b9e2655` | Money read in lakhs — an extra zero is invisible in `2500000` and impossible to miss in `25 lakh` |
| `8cca093` | **A spending book in its own tab, never summed into the ledger.** Rent is not owed to you by anyone; letting it into "who owes me what" would make that figure a lie. |

## Phase 4 — surviving reality

| Commit | What it settled |
|---|---|
| `bb5d70f` | **The Drive wall.** The folder was shared with the service account and it could read it — but upload still failed: *"Service Accounts do not have storage quota."* A file it creates would be owned by it, and it has nowhere to own it. Google's answer is a Shared Drive or OAuth delegation; both need paid Workspace. Attachments went into the spreadsheet as base64, split across cells, capped at 2 MB. |
| `9cb9049` | **A 244 KB image was refused when it should have been resized.** Now stepped down until it fits, and the app says what it did. PDFs/spreadsheets/CSVs are read as text and sent to the text model — a model reads a table far better than a picture of a table. A PDF with no text layer is a scan, and says so. |
| `06134fd` | **Real family names were sitting in test fixtures in a public repo.** Replaced with neutral stand-ins; verified across every commit, not just the working tree. Also: currency read from the user's words, matched on word boundaries because `rs` sits inside `dollars`. |
| `b3859a3` | **Three faults made the chat look broken while the model was fine.** A dropped connection reached the page as a red traceback (`_post` converted HTTP errors but let `Timeout`/`ConnectionError` through, and the view caught only `AssistantError`). Nothing appeared on screen while it thought, because the turn was written to session state and only drawn on the rerun. A timeout lost the message. |
| `1dff2d7` | **Clearing the session value did not empty the form.** The app correctly forgot the amount — Save went disabled, the preview vanished — while the browser went on displaying "777". Streamlit keeps a text input's typed value while the widget identity is unchanged. Fields now carry a round number in their key. |
| `1b82388` | **Every entry field was interpolated raw into `unsafe_allow_html`.** The attachment URL was the sharper edge: unescaped inside `href="…"` with no scheme check, so `javascript:` in the edit box became a working link. Also: fetching one attachment read the whole tab. Three approaches were measured at real scale first — gspread's `findall` was *slower* than the full scan it would have replaced. |
| `8f8f676` | **Google's 503s.** One was enough to drop the app into demo mode. Retry moved into a subclass of gspread's `HTTPClient` so one override covers every tab. 408/429/5xx retried, 4xx never, dropped connections only for GET. The authorised client is cached. An unreachable sheet now shows nothing rather than demo data. |
| `9fbba23` | CLAUDE.md added |
| `3912f57`, `379a68f` | README brought back in line with the code; the deployed-app privacy note corrected |

## Phase 5 — interest, grouping, and the money bugs

| Commit | What it settled |
|---|---|
| `43cd2a4` | **`facts.py`: answer from the sheet instead of asking a model.** "Who owes me the most" is arithmetic. A real bug the tests found: a question naming an unidentifiable person fell back to the grand total — "how much does Kavita owe me" confidently reported the whole book. The contract is `answer() -> str \| None` and `None` is the important half. Deliberately no RAG: ~714 tokens against a 128,000-token window. |
| `565b2c5` | **`FPDFUnicodeEncodingException` reported from the deployed app.** fpdf's latin-1 core fonts raise on anything else, and *only the money was guarded* — a rupee sign in an amount was safe while the same sign in a note took the page down. Now every string is transliterated with readable stand-ins. |
| `dcbafd9` | Interest charges and people grouping, each in their own tab |
| `a6ba813` | **"charge Chaitu 2% interest this month" simply failed** against the live endpoint — the model answers with a rate and no figure, which the parser rejected as a missing amount. The percentage is now applied in code through the same `suggest()` the page uses. |
| `d25dd25`, `8161753` | **The Interest page had been through two designs answering a question nobody asked** — a rate slider, then a spreadsheet grid. What was wanted was the same kind of form as Add entry. |
| `6884be4` | Due/given, editing, photos, and **one opt-in route from interest into the ledger** — never fires unless chosen |
| `8142a99` | **A re-saved interest charge lent the money twice.** Rows 45 and 46 byte-for-byte identical in the sheet; a person shown owing ₹15,000 more than he did. The charge is an upsert but the ledger write was a bare append. Both guard tests failed on this change and both were right to. |
| `f026467` | **A ledger row read "given to vihar but used to pay proxy service"** — no mention of whose interest, that it was interest, or which month. The trail is now written first and always. Note this commit's own reasoning: *"find_ledger_entry deliberately matches on person, ledger, date, currency and direction rather than on the note"* — which is exactly the decision that caused the next bug. |
| `049c90b` | **The overwrite.** Matching by shape also matched an ordinary loan handed over on the same day; the next save wrote the interest figure over a ₹2,00,000 loan. Now matched on the trail. Both routes into the ledger unified behind `sync_ledger_entry`. |
| `c462481` | **A repayment is a second row.** The edit dialog gained "Add as a new entry". Separately: every append moved behind `store.append_rows` with `INSERT_ROWS`, because `values.append` defaults to OVERWRITE. The integration fake was rewritten to model the real API — as a one-liner it had passed over a destructive write path. |
| `2c03dc4` | CLAUDE.md stale claims corrected; `facts.py` and the test-fake lesson documented |
| `e71248e` | **Found by code review of the commit before it.** The interest edit dialog wrote the ledger row but never set `moved_to`, so the same money sat in the interest total *and* in a balance. The taker dropdown defaulted to whoever sorted first rather than where the charge had already gone — and since the match key carries the taker's name, re-saving appended a second loan under someone who never took the money. The success message claimed a ledger entry was written whatever actually happened, which would have hidden both. |
| `7fc249e` | Both live hostnames named rather than the one seen in a screenshot |

## What the arc shows

The bugs got *smaller in surface* and *larger in consequence* over time. Early
ones were visible immediately — a chart drew nothing, a page said "app". Late
ones were silent and cost money: a row overwritten, an amount counted twice, a
loan duplicated under the wrong name. Three of the last four were found by
reading code or reviewing a diff, not by running the app.

Once a system is basically correct, the remaining defects are the ones no
screen will show you.
