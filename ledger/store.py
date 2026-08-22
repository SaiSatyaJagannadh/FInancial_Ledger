"""Where entries come from: a Google Sheet, or the built-in demo data.

Demo mode is not an error path. With no credentials the app is fully usable
against sample data and says so, which is how you look at it before deciding to
wire up a sheet.
"""

from __future__ import annotations

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


def _open_worksheet(secrets: dict, tab: str | None = None):
    """One tab of the workbook. `tab` overrides the configured default.

    A missing tab is created rather than raising: the second tab only exists
    once something has been written to it, and a first-run crash is not a
    useful way to learn that.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    account = dict(secrets["gcp_service_account"])
    sheet = dict(secrets["sheet"])
    credentials = Credentials.from_service_account_info(account, scopes=SCOPES)
    client = gspread.authorize(credentials)

    book = client.open_by_url(sheet["url"]) if sheet.get("url") else client.open_by_key(sheet["id"])
    name = tab or sheet.get("worksheet")
    if not name:
        return book.sheet1
    try:
        return book.worksheet(name)
    except Exception:
        return book.add_worksheet(title=name, rows=200, cols=20)


def load(secrets: dict | None = None) -> LoadResult:
    """Load every entry. Falls back to demo data when unconfigured or unreachable."""
    secrets = _secrets() if secrets is None else secrets

    if not is_configured(secrets):
        return LoadResult(build_demo_entries(), demo=True, problems=[])

    try:
        worksheet = _open_worksheet(secrets)
        records = worksheet.get_all_records()
    except Exception as exc:
        # A network blip or a revoked key should not present an empty ledger,
        # which would read as "nobody owes you anything".
        return LoadResult(
            build_demo_entries(),
            demo=True,
            problems=[],
            detail=f"Could not reach the sheet ({type(exc).__name__}: {exc}). Showing demo data.",
        )

    entries, problems = rows_to_entries(records)
    return LoadResult(entries, demo=False, problems=problems)


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
