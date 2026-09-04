# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
.venv/bin/python -m pytest -q                     # all tests (~665)
.venv/bin/python -m pytest tests/test_money.py -q  # one file
.venv/bin/python -m pytest -q -k "settle"          # one pattern
.venv/bin/python -m ledger.invest                  # one module's self-check
```

Several `ledger/` modules carry a `demo()` self-check runnable as
`python -m ledger.<module>`: `assistant`, `attach`, `docs`, `export`, `invest`,
`auth`, `facts`, `interest`, `notify`, `people`, `settle`, `spend`. These assert the behaviour that is awkward to unit-test
(file round-trips, compounding maths, JSON parsing) and run in CI-adjacent
fashion — keep them passing.

Run the app against the real sheet:

```bash
cp ~/Downloads/ledger_secrets.toml .streamlit/secrets.toml   # gitignored
.venv/bin/python -m streamlit run app.py --server.port 8899 --server.headless true
rm -f .streamlit/secrets.toml                                 # afterwards
```

CI (`.github/workflows/ci.yml`) runs pytest on 3.11 and then boots the app
headlessly and curls it. That curl only renders the *default* page, so
`tests/test_pages.py` runs every view through Streamlit's `AppTest` — a module
shadowed by a local variable crashed one page while its own unit tests passed.

**A fake worksheet must copy what Google does, not what would be convenient.**
`FakeSheet.append_rows` in `tests/test_integration.py` reproduces the real
table-detection and OVERWRITE behaviour; while it was a one-line
`self.rows.append(row)` the whole suite passed over a write path that destroys
rows on the real sheet. The same is true of the amount comparison below.

## Architecture

**One Google Sheets workbook is the entire database.** Five tabs, five
modules, no SQL:

| Tab | Module | Holds |
|---|---|---|
| `entries` | `ledger/store.py` | The lending ledger — who owes what |
| `transactions` | `ledger/spend.py` | General spending, deliberately never summed into the ledger |
| `attachments` | `ledger/attach.py` | Uploaded files, base64 across cells |
| `interest` | `ledger/interest.py` | Monthly interest charges, **never** summed into the ledger |
| `people` | `ledger/people.py` | Who rolls up under whom |

`store._open_worksheet(secrets, tab)` is the single door to the workbook; it
creates a missing tab rather than raising.

**Money is integer minor units (paise/cents) everywhere.** `ledger/money.py`
owns parsing and formatting; a float must never reach the persistence
boundary. `Entry.to_row` renders the amount with `divmod`, not `/ 100`, for
exactly this reason.

**`ledger/compute.py` holds every aggregate the UI shows.** Views format but
never sum, so the tests cover what is actually on screen. `ledger/invest.py`
(what-if compounding) and `ledger/settle.py` (clearing a balance to zero)
follow the same rule.

**Currencies are never mixed.** Rupees and dollars are separate arrangements
throughout — `Entry.key` includes currency, and no code path adds them, because
that would require inventing an exchange rate.

### Writing to the sheet

**Every append goes through `store.append_rows`**, which sets
`insert_data_option="INSERT_ROWS"`. Google's `values.append` defaults to
OVERWRITE: it ends the table at the first wholly blank row and writes there,
over whatever it finds. A one-row append survives that by luck — the gap is at
least one row wide. `attach.put` writes one row per 40,000 characters of
base64, and would have eaten the rows below a gap. Do not call `append_row` at
a call site, for the same reason retrying does not belong at one.

**A repayment is a second row, never an edit of the first.** The edit dialog
offers "Add as a new entry" beside "Save changes", and leads with it when the
Direction is what changed: flipping a "gave" row into a "got back" row erases
the fact that the money was ever lent, so the ledger no longer holds both
halves to reconcile.

`store.delete` / `store.update` and their `spend` equivalents **re-read the row
and confirm it still holds the expected entry before touching it.** Rows shift
when anything above them is removed, and a stale row number would otherwise
mutate a stranger's record.

The amount in that check is compared **numerically, not as text**: Sheets
returns `42` for what was written as `42.00`, so a string comparison passes
against a test fake and fails against the real sheet.

### Google's 503s

Sheets answers a perfectly good request with `[503]: The service is currently
unavailable` at random. It says nothing about the sheet, the key or the data,
and the same call succeeds a second later.

`store._retrying_http_client()` subclasses gspread's `HTTPClient` and retries
inside `request`. **Every read and write in the app — all five tabs — goes
out through that one method**, so this is the only place
retrying belongs; do not add it at a call site. 408/429/5xx are retried,
4xx never (a revoked key will not pass on the fourth try). A dropped
connection is retried **only for GET**: the reply to a lost POST may already
have been applied, and repeating it would append the entry twice.

Waits are `(0.4, 1.0, 2.5)` — four attempts, under four seconds. gspread ships
its own `BackOffHTTPClient`; it is not used because it starts at two seconds
and doubles to 128, which nobody watching a web page will sit through.

`store._client()` caches the authorised client per service account. Building
one exchanges the key for an OAuth token over the network, and doing that per
operation added a call, and so another chance of a 503, to every read.

**An unreachable sheet shows nothing, never demo data.** Sample entries under
a heading that says "your ledger" carry other people's names and figures;
there is no way to read that screen which is true. `LoadResult.unreachable`
says so, `ui.demo_banner` renders the reason with a Try again button and
calls `st.stop()`, and `ui.load_ledger` drops the failure from the cache so
the next view retries instead of repeating a stale error for a minute.
`store._status_of` digs the HTTP status out however gspread wrapped it —
`APIError.code`, a `SpreadsheetNotFound` holding a raw response, or a bare
builtin `PermissionError` for 403.

### Interest and grouping

**Interest is not a debt.** `ledger/interest.py` has its own tab and is never
added into ledger totals — the ledger says how much of your money is out
there, interest says what it earned while it was, and once merged the two
cannot be told apart. `interest.suggest()` offers a figure (a month of the
rate on what is *still owed*, not on what was first handed over) and the page
lets it be overridden before saving, because the rate is usually an
understanding rather than a contract.

**Carrying a month forward** (`interest.clone_month`) writes last month's
people and figures into a new month, because the arrangement rarely changes and
retyping every name monthly is how a digit goes wrong. It returns what *would*
be written and leaves saving to the caller, the same shape as
`settle.balancing_entries`. Three fields deliberately do not carry: `moved_to`
(a copy has been handed to nobody, and carrying it would drop the charge from
the interest total while claiming a ledger row that does not exist — counted in
neither place), `attachment` (August's receipt is not September's) and `kind`,
which starts as still-due. Anyone who already has a charge that month is
skipped and named — `set_for_month` is an upsert, so copying over a corrected
figure would silently undo the correction.

The one opt-in route from interest into the ledger goes through
`interest.sync_ledger_entry()`, and the row it may correct is identified by the
**trail it writes into the note** (`trail_note`), never by shape. Matching on
person + ledger + currency + date + direction also matched an ordinary loan
handed over on the same day, and the next save overwrote that loan with the
interest figure — the money actually lent simply vanished from the sheet.

**A grouping is a fact about a person, not about a row.** `ledger/people.py`
maps person → parent in the `people` tab, so Chaitu moving out of Vihar's
group is one edit rather than twenty. `group_of()` walks to the top of the
chain and **stops on a cycle** — the sheet is hand-editable, so a loop is a
question of when, not if — and `would_cycle()` refuses one before it is saved.
`compute.by_group()` rolls the totals up; it never changes them.

**Moving money inside a group writes two entries, not one**
(`people.transfer_entries`). When Chaitu takes from what was handed to Vihar,
Vihar is recorded as having returned it and Chaitu as having taken it. A
single "given" row under Chaitu would say more money left the house than
actually did.

### Answering questions (`ledger/facts.py`)

**The model is asked only what code cannot answer.** `views/assistant.py` runs
`facts.answer()` first; it returns `None` when the question is not one it
recognises, and only then is the endpoint called. What Ravi owes, who owes the
most, how much has gone out in total — all arithmetic, all already in
`compute.py`, so it is exact and instant instead of a second and a half of a
model that can be confidently wrong about a sum.

**There are no embeddings here, and that is deliberate.** Retrieval finds the
relevant part of a corpus too large for the context window; this corpus is a
few dozen rows, roughly 700 tokens against 128,000. A vector store would add a
round trip and then ask the model to add up the rows it got back — the exact
arithmetic this module exists to avoid. Do not add one.

### The assistant (`ledger/assistant.py`)

NVIDIA's OpenAI-compatible endpoint over plain HTTP — no vendor SDK.

- **The model proposes; it never writes.** Output is validated through
  `Entry.from_row` — the same door a sheet row goes through — and a human
  clicks Save. A misread "5,000" as "50,000" must not be able to land.
- It may answer `{"entries": …}`, `{"question": …}` or `{"answer": …}`.
  Answers are grounded in `summarise()`, computed in code — the model is never
  asked to work out a total.
- **Names are canonicalised deterministically** (`canonical()`), not left to
  the prompt: "ravi" becomes "RAVI KUMAR" when exactly one known name extends
  it, and an ambiguous abbreviation is left alone.
- **Currency comes from the user's words** (`currency_hint()`), not the model.
  Every candidate model tested read "gave amma $250" as rupees.
- `_post` converts *every* network failure into `AssistantError` and retries
  transient ones. Nothing network-shaped may escape the module — the view
  catching only `AssistantError` was how a timeout became a red traceback.

`ledger/docs.py` turns an upload into either text (PDF/XLSX/CSV → the text
model, which reads a table far better than a vision model reads a picture of
one) or a right-sized image. Oversized photos are **shrunk, never refused**.

### Streamlit specifics that will bite

- `app.py` is only a router. `st.set_page_config` lives there and nowhere else;
  views call `styles()` instead. Pages are declared via `st.navigation` so each
  gets a real name — Streamlit otherwise names the entry page after its file.
- **Clearing a form needs new widget keys, not a cleared value.** Popping a
  key makes the app forget the value while the browser keeps displaying it.
  `views/add_entry.py` carries a round number in every key (`field()`); saving
  increments it, making the widgets new and therefore blank.
- **Anything interpolated into `unsafe_allow_html` must go through
  `ui.esc()`**, and any URL through `ui.safe_href()` (http/https only). Notes
  are free text and are also written by the model from uploaded documents.

### Attachments live in the sheet, not Drive

A service account has no Drive storage quota, and sharing a folder with it does
not help — the file would be owned by the account. Google's answer is a Shared
Drive or OAuth delegation, both of which need paid Workspace. This is a wall,
not a bug to route around, so files are base64'd into the `attachments` tab and
capped at 2 MB. `attach.get` reads only column A to find the id, then just the
matching rows: reading the whole tab would pull every stored file to serve one.

## Configuration

Secrets are Streamlit's encrypted store (`.streamlit/secrets.toml` locally,
gitignored). `ledger/ui.py::api_key()` searches **every section** for the NVIDIA
key, because a key pasted at the bottom of the box lands inside the last
`[section]` and a top-level lookup misses it.

Without credentials the app runs in demo mode against `ledger/demo.py` and says
so. Demo mode is a supported path, not an error — `store` refuses writes there
rather than pretending.

### Who is signed in (`ledger/auth.py`)

Streamlit's own OIDC login (`st.login` / `st.user`, 1.42+), gated in `app.py`
before `st.navigation` — the router is the only way in, so a page cannot forget
to check. **No passwords are stored anywhere.** A `users` tab was rejected
deliberately: the workbook *is* the database, so hashes would sit where every
shared viewer can read them and anyone who can edit the sheet could add
themselves a row.

```toml
[auth]
redirect_uri = "https://<app>.streamlit.app/oauth2callback"
cookie_secret = "<a long random string>"
client_id = "<from Google Cloud console>"
client_secret = "<from Google Cloud console>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
allowed = ["you@gmail.com", "someone@gmail.com"]   # empty list = anyone Google verifies
```

**Absent `[auth]` means the app runs open, exactly as before.** `st.user` raises
without that section, so `auth.configured()` is checked first and every helper
answers "nobody" rather than throwing — which is what keeps demo mode and
`tests/test_pages.py` (which run views directly, not through the router)
working untouched.

`auth.current_user()` is called from the write paths, where there may be no
Streamlit runtime at all, so it swallows everything and returns `""`.
Attribution is worth having; it is not worth failing a save for.

### Change emails (`ledger/notify.py`)

Every write to `entries` and `interest` emails a note saying what changed.
**Off unless `[notify]` names both a recipient and a password** — that absence
is what keeps the whole test suite and every demo run off the network, so do
not give `settings()` a default that is truthy.

```toml
[notify]
to = "you@example.com"
password = "abcd efgh ijkl mnop"   # a Gmail App Password, not the account one
# user/host/port default to `to`, smtp.gmail.com, 587
```

Three rules hold it together:

- **A failed notice never fails a save.** The row is already in the sheet;
  raising afterwards would show somebody an error about money they successfully
  recorded. `store._announce` / `interest._announce` swallow everything, and
  `notify._send` cannot raise.
- **It sends on a background thread.** Gmail's handshake is 1–3 seconds and Add
  Entry is built for typing, saving and typing the next.
- **It names the signer when there is one**, from `auth.current_user()`, and
  says nothing at all when there is not — it never guesses.
  `ui._notify_health` surfaces a failed send, because a notifier that has gone
  quiet is indistinguishable from nothing having changed.

A service account cannot send as you — the Gmail API needs domain-wide
delegation, Workspace-only, the same wall as Drive in `attach.py`. Plain SMTP
with an app password is the one route a personal account has.

Note `set_for_month` writes twice on the hand-it-on path, so that action sends
two notices. Both are true; the second reads `moved_to: — → Vihar`.

## Known state

- The deployed app is **private**: an anonymous request to either
  `family-financialledger.streamlit.app` or
  `saijagannadh-personal-ledger.streamlit.app` (both live, both this app) is
  redirected to `share.streamlit.io/-/auth/app`, so viewers must sign in.
  Authorisation *inside* the app now exists too, but only when `[auth]` is
  configured — see below. Without it the app is still open to anyone who can
  open it, and Streamlit's sharing list is the only gate.
- The **Invested instead** page is off the router: `views/invested.py` and
  `ledger/invest.py` still work and are still tested, but nothing links to the
  page. Put its `st.Page` line back in `app.py` to bring it back.
- `store.upload_attachment` (the Drive multipart POST) has **no callers**.
  Attachments go into the `attachments` tab instead, for the reason above; the
  function is the record of a route that does not work on a personal account.
- `people.transfer_entries` has no caller either — the rule it encodes is
  right, but no page offers a move-money-inside-a-group action yet.
- PDF exports print `INR`/`USD` rather than `₹` — fpdf's core fonts are
  latin-1 and the glyph raises.
- NVIDIA's endpoint has no speech-to-text, so there is no voice input.
- The repo is public and indexed at
  [deepwiki.com/SaiSatyaJagannadh/FInancial_Ledger](https://deepwiki.com/SaiSatyaJagannadh/FInancial_Ledger),
  which regenerates from `README.md` and this file on a push. A stale claim in
  either becomes a stale claim in a public wiki, so correct them in the same
  commit as the behaviour they describe.
