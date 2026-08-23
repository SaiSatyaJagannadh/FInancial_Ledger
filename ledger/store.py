"""Where entries come from: a Google Sheet, or the built-in demo data.

Demo mode is not an error path. With no credentials the app is fully usable
against sample data and says so, which is how you look at it before deciding to
wire up a sheet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ledger.demo import build_demo_entries
from ledger.models import COLUMNS, Entry, EntryError, parse_date, parse_direction
from ledger.money import to_minor

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    # drive.file grants access only to files this app itself creates, which is
    # all an attachment upload needs. Full drive scope would hand the service
    # account the rest of the Drive for no reason.
    "https://www.googleapis.com/auth/drive.file",
]

#: Attachments larger than this are refused rather than sent. Bank statements
#: are a few hundred KB; anything past this is a mistake worth naming.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass
class LoadResult:
    entries: list[Entry]
    demo: bool
    #: Rows the sheet contained that could not be read, as human messages.
    problems: list[str]
    detail: str = ""
    #: Set when the sheet is configured but did not answer. Distinct from
    #: `demo`, which means there is no sheet to reach in the first place.
    unreachable: bool = False


def rows_to_entries(rows: list[dict]) -> tuple[list[Entry], list[str]]:
    """Convert sheet rows to entries, collecting per-row problems.

    One malformed row must not hide the other 200, so bad rows are reported
    rather than raised.
    """
    entries: list[Entry] = []
    problems: list[str] = []
    for offset, raw in enumerate(rows):
        row_number = offset + 2  # 1 is the header
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue  # blank spacer row
        try:
            entries.append(Entry.from_row(cleaned, row_number=row_number))
        except EntryError as exc:
            problems.append(f"row {row_number}: {exc}")
    entries.sort(key=lambda e: (e.date, e.person, e.ledger))
    return entries, problems


def _secrets() -> dict:
    """Streamlit secrets as a plain dict, or empty when there are none."""
    try:
        import streamlit as st

        return dict(st.secrets)
    except Exception:
        # No secrets.toml, or running outside Streamlit (e.g. pytest).
        return {}


def is_configured(secrets: dict | None = None) -> bool:
    secrets = _secrets() if secrets is None else secrets
    account = secrets.get("gcp_service_account")
    sheet = secrets.get("sheet") or {}
    return bool(account) and bool(sheet.get("url") or sheet.get("id"))


#: Google answers a perfectly good request with one of these when its own side
#: is busy. "[503]: The service is currently unavailable" is the common one, and
#: it says nothing about the sheet or the credentials — the same call succeeds a
#: second later. Retried rather than surfaced.
RETRY_CODES = frozenset({408, 429, 500, 502, 503, 504})

#: Three waits, so four attempts in all. Long enough to ride out a blip, short
#: enough that a page load which is going to fail fails within four seconds
#: instead of hanging. gspread ships its own BackOffHTTPClient, but it starts at
#: two seconds and doubles to 128 — a wait no one watching a web page will sit
#: through.
RETRY_WAITS = (0.4, 1.0, 2.5)

_CLIENTS: dict = {}
_RETRYING_CLIENT = None


def _retrying_http_client():
    """gspread's HTTP client, with transient Google failures retried.

    Every read and write this app makes — ledger, spending, attachments — goes
    out through one `HTTPClient.request`. Retrying there covers all of them at
    once, and means no call site has to remember to ask for it.
    """
    global _RETRYING_CLIENT
    if _RETRYING_CLIENT is not None:
        return _RETRYING_CLIENT

    import requests
    from gspread.exceptions import APIError
    from gspread.http_client import HTTPClient

    class _Retrying(HTTPClient):
        def request(self, method, endpoint, *args, **kwargs):
            for wait in RETRY_WAITS:
                try:
                    return super().request(method, endpoint, *args, **kwargs)
                except APIError as exc:
                    if exc.code not in RETRY_CODES:
                        raise          # a 404 or a revoked key will never pass
                except (requests.ConnectionError, requests.Timeout):
                    # The reply was lost, so we cannot know whether the write
                    # landed; repeating it could append the same row twice. A
                    # 5xx above is different — the API said it did not act.
                    if method.upper() not in ("GET", "HEAD"):
                        raise
                time.sleep(wait)
            # The last attempt is not wrapped: whatever it raises is the real
            # answer, and by now it has earned its way to the page.
            return super().request(method, endpoint, *args, **kwargs)

    _RETRYING_CLIENT = _Retrying
    return _RETRYING_CLIENT


def _client(account: dict):
    """An authorised gspread client, reused across calls.

    Building one exchanges the service-account key for an OAuth token, which is
    a network round trip of its own. Doing that afresh for every read added a
    call — and so another chance of a 503 — to every single operation.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    cached = _CLIENTS.get(account.get("client_email"))
    if cached is not None:
        return cached
    client = gspread.authorize(
        Credentials.from_service_account_info(account, scopes=SCOPES),
        http_client=_retrying_http_client(),
    )
    _CLIENTS[account.get("client_email")] = client
    return client


def _open_worksheet(secrets: dict, tab: str | None = None):
    """One tab of the workbook. `tab` overrides the configured default.

    A missing tab is created rather than raising: the second tab only exists
    once something has been written to it, and a first-run crash is not a
    useful way to learn that.
    """
    from gspread.exceptions import WorksheetNotFound

    account = dict(secrets["gcp_service_account"])
    sheet = dict(secrets["sheet"])
    client = _client(account)

    book = client.open_by_url(sheet["url"]) if sheet.get("url") else client.open_by_key(sheet["id"])
    name = tab or sheet.get("worksheet")
    if not name:
        return book.sheet1
    try:
        return book.worksheet(name)
    except WorksheetNotFound:
        # Only an absent tab is worth creating. Catching everything here meant
        # a 503 on the lookup was answered by adding a second tab of the same
        # name — a transient failure turning into a split ledger.
        return book.add_worksheet(title=name, rows=200, cols=20)


def load(secrets: dict | None = None) -> LoadResult:
    """Load every entry.

    Demo data when there is no sheet configured; nothing at all when there is
    one but it cannot be reached.
    """
    secrets = _secrets() if secrets is None else secrets

    if not is_configured(secrets):
        return LoadResult(build_demo_entries(), demo=True, problems=[])

    try:
        worksheet = _open_worksheet(secrets)
        records = worksheet.get_all_records()
    except Exception as exc:
        # Not demo data. Sample entries under a heading that says "your ledger"
        # are worse than nothing: they carry names and figures that are not
        # yours, and there is no reading of them that is true. Say the sheet is
        # unreachable and show nothing.
        return LoadResult([], demo=False, problems=[], detail=_why(exc), unreachable=True)

    entries, problems = rows_to_entries(records)
    return LoadResult(entries, demo=False, problems=problems)


def _status_of(exc: Exception) -> int | None:
    """The HTTP status behind a gspread failure, however it was wrapped.

    gspread does not report these consistently: an APIError carries `.code`,
    but a 404 is re-raised as SpreadsheetNotFound holding the raw response in
    its args, and a 403 becomes a bare builtin PermissionError with nothing at
    all. Reading only `.code` left the page showing
    "SpreadsheetNotFound: <Response [404]>", which tells nobody anything.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    for candidate in (getattr(exc, "response", None), *getattr(exc, "args", ())):
        status = getattr(candidate, "status_code", None)
        if isinstance(status, int):
            return status
    if isinstance(exc, PermissionError):
        return 403
    return None


def _why(exc: Exception) -> str:
    """The failure in words worth reading, not a class name and a stack."""
    code = _status_of(exc)
    if code in RETRY_CODES:
        return (
            f"Google Sheets answered {code} on all {len(RETRY_WAITS) + 1} attempts. "
            "That is Google's own service, not your sheet and not your data — it "
            "usually clears within a minute."
        )
    if code in (401, 403):
        return (
            f"Google refused access ({code}). The service account may have lost its "
            "share on the sheet, or the key may have been revoked."
        )
    if code == 404:
        return (
            "Google cannot find that sheet. Check the sheet id or URL in your "
            "secrets, and that the sheet is still shared with the service account."
        )
    return f"{type(exc).__name__}: {exc}"


def append(entry: Entry, secrets: dict | None = None) -> None:
    """Append one entry to the sheet. Refuses in demo mode rather than pretending."""
    secrets = _secrets() if secrets is None else secrets
    if not is_configured(secrets):
        raise RuntimeError(
            "Demo mode: there is no sheet to write to. Add your Google credentials "
            "to .streamlit/secrets.toml to save entries."
        )
    worksheet = _open_worksheet(secrets)
    _ensure_header(worksheet)
    worksheet.append_row(entry.to_row(), value_input_option="USER_ENTERED")


def delete(entry: Entry, secrets: dict | None = None) -> None:
    """Remove one entry's row from the sheet.

    Deletes by row number, and re-reads that row first to confirm it still holds
    the entry we think it does: rows shift when anything else is deleted, and a
    stale number would silently delete somebody else's record.
    """
    secrets = _secrets() if secrets is None else secrets
    if not is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to delete from.")
    if entry.row is None:
        raise RuntimeError("This entry has no sheet row, so it cannot be deleted.")

    worksheet = _open_worksheet(secrets)
    current = worksheet.row_values(entry.row)
    if not _same_entry(current, entry):
        raise RuntimeError(
            f"Row {entry.row} no longer matches this entry — the sheet changed "
            "since it was loaded. Reload and try again."
        )
    worksheet.delete_rows(entry.row)


def update(original: Entry, edited: Entry, secrets: dict | None = None) -> None:
    """Replace one entry's row with an edited version.

    Checks the row still holds `original` before writing, for the same reason
    delete does: rows shift, and overwriting a stranger's record is worse than
    refusing. The edit is written to `original.row` — where the entry actually
    lives — not to whatever row `edited` happens to be carrying.
    """
    secrets = _secrets() if secrets is None else secrets
    if not is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to edit.")
    if original.row is None:
        raise RuntimeError("This entry has no sheet row, so it cannot be edited.")

    worksheet = _open_worksheet(secrets)
    if not _same_entry(worksheet.row_values(original.row), original):
        raise RuntimeError(
            f"Row {original.row} no longer matches this entry — the sheet changed "
            "since it was loaded. Reload and try again."
        )
    row = edited.to_row()
    last = _column_letter(len(row))
    worksheet.update(
        values=[row], range_name=f"A{original.row}:{last}{original.row}",
        value_input_option="USER_ENTERED",
    )


def _column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA. Small enough not to import a library for."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _same_entry(cells: list[str], entry: Entry) -> bool:
    """Does this raw sheet row still describe `entry`?

    The amount is compared as a number, not as text: Sheets stores what we wrote
    as "42.00" and hands it back as "42", so a string comparison would call
    every row a mismatch and make deletion impossible.
    """
    if len(cells) < 5:
        return False
    try:
        date_matches = parse_date(cells[0]) == entry.date
        direction_matches = parse_direction(cells[3]) is entry.direction
        amount_matches = to_minor(cells[4]) == entry.amount_minor
    except (EntryError, ValueError):
        return False
    return (
        date_matches
        and cells[1].strip() == entry.person
        and cells[2].strip() == entry.ledger
        and direction_matches
        and amount_matches
    )


def upload_attachment(
    filename: str, data: bytes, mimetype: str, secrets: dict | None = None
) -> str:
    """Put one file in the configured Drive folder, returning a viewable link.

    Uses a plain multipart POST over an authorised session rather than pulling
    in the Drive client library for a single endpoint.
    """
    secrets = _secrets() if secrets is None else secrets
    folder = (secrets.get("drive") or {}).get("folder_id")
    account = (secrets.get("gcp_service_account") or {}).get("client_email", "the service account")
    if not folder:
        raise RuntimeError(
            "Attachments need a Drive folder. Two steps, both one-off:\n\n"
            "1. In Google Drive, make a folder and share it with "
            f"**{account}** as **Editor**.\n"
            "2. Add its id — the last part of the folder's URL — to your "
            "Streamlit secrets, above every [section] heading:\n\n"
            "```toml\n[drive]\nfolder_id = \"...\"\n```"
        )
    if len(data) > MAX_ATTACHMENT_BYTES:
        mb = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise RuntimeError(f"{filename} is larger than the {mb} MB attachment limit.")

    import json as _json

    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_info(
        dict(secrets["gcp_service_account"]), scopes=SCOPES
    )
    session = AuthorizedSession(credentials)

    metadata = {"name": filename, "parents": [folder]}
    boundary = "ledger-attachment-boundary"
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        _json.dumps(metadata).encode(),
        f"\r\n--{boundary}\r\nContent-Type: {mimetype}\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    response = session.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", "fields": "id,webViewLink"},
        data=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        timeout=120,
    )
    if response.status_code == 404:
        # The folder exists for you but not for the service account, which is
        # what "not found" means when the id is right: it cannot see it.
        raise RuntimeError(
            f"Drive cannot see folder {folder}. Open it in Drive, press Share, "
            f"and add **{account}** as **Editor**. A service account has no "
            "storage of its own, so it can only write into a folder you share "
            "with it — there is no way around this step."
        )
    if response.status_code == 403 and "storage quota" in response.text:
        raise RuntimeError(
            "Drive refused because the folder is not one you own and shared. "
            f"Share a folder in your own Drive with **{account}** as Editor "
            "and use that folder's id."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Drive rejected the upload ({response.status_code}): {response.text}")
    result = response.json()
    return result.get("webViewLink") or f"https://drive.google.com/file/d/{result['id']}/view"


def _ensure_header(worksheet) -> None:
    """Write the header row if the sheet is empty, so a blank sheet just works."""
    try:
        first = worksheet.row_values(1)
    except Exception:
        first = []
    if not any(str(v).strip() for v in first):
        worksheet.update("A1", [COLUMNS])
