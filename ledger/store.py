"""Where entries come from: a Google Sheet, or the built-in demo data.

Demo mode is not an error path. With no credentials the app is fully usable
against sample data and says so, which is how you look at it before deciding to
wire up a sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

from ledger.demo import build_demo_entries
from ledger.models import COLUMNS, Entry, EntryError

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


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


def _open_worksheet(secrets: dict):
    import gspread
    from google.oauth2.service_account import Credentials

    account = dict(secrets["gcp_service_account"])
    sheet = dict(secrets["sheet"])
    credentials = Credentials.from_service_account_info(account, scopes=SCOPES)
    client = gspread.authorize(credentials)

    book = client.open_by_url(sheet["url"]) if sheet.get("url") else client.open_by_key(sheet["id"])
    name = sheet.get("worksheet")
    return book.worksheet(name) if name else book.sheet1


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


def _ensure_header(worksheet) -> None:
    """Write the header row if the sheet is empty, so a blank sheet just works."""
    try:
        first = worksheet.row_values(1)
    except Exception:
        first = []
    if not any(str(v).strip() for v in first):
        worksheet.update("A1", [COLUMNS])
