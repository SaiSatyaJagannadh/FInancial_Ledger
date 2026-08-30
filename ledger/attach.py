"""Attachments kept inside the spreadsheet itself.

Google Drive is not available to us: a service account has no storage quota of
its own, and uploading into a folder you share with it still fails, because the
file would be owned by the account. Google's own answer is "use a Shared Drive
or OAuth delegation", and both need Google Workspace — a personal Gmail account
cannot do either. That is a wall, not a bug to work around.

So the file goes where the app demonstrably can write: the workbook. It is
base64'd and split across cells of an `attachments` tab, and reassembled on the
way out. Not elegant, but it needs no second service, no extra credential, and
no setup step that can silently rot.

Deliberately capped small. This is for a statement page or a receipt, not a
photo library, and a workbook is a poor place to keep megabytes.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass

WORKSHEET = "attachments"
COLUMNS = ["id", "name", "mimetype", "part", "data"]

#: A Sheets cell holds 50,000 characters. Staying well under it leaves room for
#: the quoting and escaping that happens on the way through the API.
CHUNK = 40_000

#: 2 MB of file becomes ~2.8 MB of base64, about 70 cells. A statement page or
#: a phone photo fits comfortably; anything larger belongs in real storage.
MAX_BYTES = 2 * 1024 * 1024

#: What an entry's attachment field holds when the file lives in the workbook.
PREFIX = "sheet:"


@dataclass(frozen=True)
class Stored:
    id: str
    name: str
    mimetype: str
    data: bytes


def is_stored(reference: str) -> bool:
    return str(reference or "").startswith(PREFIX)


def reference_of(attachment_id: str) -> str:
    return f"{PREFIX}{attachment_id}"


def id_of(reference: str) -> str:
    return str(reference or "")[len(PREFIX):]


def chunks(text: str, size: int = CHUNK) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def put(name: str, data: bytes, mimetype: str, secrets: dict | None = None) -> str:
    """Store one file, returning the reference to put on the entry."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is nowhere to store an attachment.")
    if not data:
        raise RuntimeError(f"{name} is empty.")
    if len(data) > MAX_BYTES:
        raise RuntimeError(
            f"{name} is {len(data) // 1024} KB. The limit is "
            f"{MAX_BYTES // 1024} KB, because attachments live inside the "
            "spreadsheet — Google will not let this app write to Drive. "
            "Attach a single page, or paste a link instead."
        )

    sheet = _sheet(secrets)
    attachment_id = uuid.uuid4().hex[:12]
    encoded = base64.b64encode(data).decode()
    rows = [
        [attachment_id, name, mimetype or "application/octet-stream", index, part]
        for index, part in enumerate(chunks(encoded))
    ]
    store.append_rows(sheet, rows, value_input_option="RAW")
    return reference_of(attachment_id)


def get(reference: str, secrets: dict | None = None) -> Stored | None:
    """Reassemble a stored file, or None when the reference is not ours."""
    from ledger import store

    if not is_stored(reference):
        return None
    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return None

    wanted = id_of(reference)
    try:
        sheet = _sheet(secrets)
        parts = _rows_for(sheet, wanted)
    except Exception:  # noqa: BLE001 — a missing tab is not a crash
        return None

    if not parts:
        return None
    parts.sort(key=lambda r: int(r.get("part") or 0))
    encoded = "".join(str(r.get("data") or "") for r in parts)
    try:
        data = base64.b64decode(encoded)
    except Exception:  # noqa: BLE001 — a corrupted row should not take the page down
        return None
    first = parts[0]
    return Stored(
        id=wanted,
        name=str(first.get("name") or "attachment"),
        mimetype=str(first.get("mimetype") or "application/octet-stream"),
        data=data,
    )


def _rows_for(sheet, attachment_id: str) -> list[dict]:
    """The rows belonging to one attachment, without reading the others.

    Reading the whole tab would pull every stored file over the wire to serve
    one of them: base64 lives in these cells, so a hundred statements is tens
    of megabytes fetched to download a single receipt. The id column is
    searched first, and only the matching rows are read.
    """
    try:
        # Column A holds only ids, so this request stays small no matter how
        # much base64 the tab is carrying. Measured faster than both a full
        # scan and gspread's findall, at two attachments and at a hundred.
        column = sheet.col_values(1)
    except Exception:  # noqa: BLE001 — fall back rather than fail the download
        return [
            r for r in sheet.get_all_records()
            if str(r.get("id", "")).strip() == attachment_id
        ]

    numbers = [
        index for index, value in enumerate(column, start=1)
        if str(value).strip() == attachment_id
    ]
    if not numbers:
        return []
    last = _column_letter(len(COLUMNS))
    ranges = [f"A{n}:{last}{n}" for n in numbers]
    rows = [block[0] if block else [] for block in sheet.batch_get(ranges)]
    return [
        dict(zip(COLUMNS, list(row) + [""] * (len(COLUMNS) - len(row))))
        for row in rows if row
    ]


def _column_letter(index: int) -> str:
    from ledger import store

    return store._column_letter(index)


def _sheet(secrets: dict):
    from ledger import store

    sheet = store._open_worksheet(secrets, WORKSHEET)
    try:
        first = sheet.row_values(1)
    except Exception:  # noqa: BLE001
        first = []
    if not any(str(v).strip() for v in first):
        sheet.update(values=[COLUMNS], range_name="A1")
    return sheet


def demo() -> None:
    """Self-check for the split/rejoin, which is the part that can corrupt a file."""
    encoded = base64.b64encode(bytes(range(256)) * 400).decode()
    pieces = chunks(encoded, 1000)
    assert len(pieces) > 1
    assert all(len(p) <= 1000 for p in pieces)
    assert "".join(pieces) == encoded, "rejoining must be byte-exact"

    # An exact multiple of the chunk size must not gain an empty tail.
    assert chunks("abcd", 2) == ["ab", "cd"]
    assert chunks("", 10) == [""]
    assert chunks("abc", 10) == ["abc"]

    assert is_stored("sheet:abc") and not is_stored("https://example.com/x.pdf")
    assert not is_stored("") and not is_stored(None)
    assert id_of(reference_of("deadbeef")) == "deadbeef"

    # Round trip through base64 exactly as put/get do it.
    raw = bytes(range(256)) * 37
    rebuilt = base64.b64decode("".join(chunks(base64.b64encode(raw).decode(), 97)))
    assert rebuilt == raw

    print("ledger.attach: all checks passed")


if __name__ == "__main__":
    demo()
