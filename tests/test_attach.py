"""Attachments stored in the workbook. The risk is corrupting a file."""

from __future__ import annotations

import base64

import pytest

from ledger import attach


@pytest.mark.parametrize("payload", [
    b"",
    b"x",
    b"%PDF-1.4 short",
    bytes(range(256)),
    bytes(range(256)) * 500,        # spans several chunks
    "рупии ₹ नमस्ते".encode(),      # non-ascii survives base64
])
def test_a_file_survives_the_split_and_rejoin(payload):
    encoded = base64.b64encode(payload).decode()
    rebuilt = base64.b64decode("".join(attach.chunks(encoded, 97)))
    assert rebuilt == payload


def test_chunks_never_exceed_the_cell_limit():
    encoded = base64.b64encode(bytes(range(256)) * 5000).decode()
    parts = attach.chunks(encoded)
    assert len(parts) > 1
    assert all(len(p) <= attach.CHUNK for p in parts)
    assert "".join(parts) == encoded


def test_the_chunk_size_stays_under_a_sheets_cell():
    """A cell holds 50,000 characters; going over loses data silently."""
    assert attach.CHUNK < 50_000


def test_an_exact_multiple_gains_no_empty_tail():
    assert attach.chunks("abcd", 2) == ["ab", "cd"]


def test_empty_input_still_yields_one_chunk():
    """Otherwise the row set would be empty and the file unrecoverable."""
    assert attach.chunks("", 10) == [""]


@pytest.mark.parametrize("reference,expected", [
    ("sheet:abc123", True),
    ("https://drive.google.com/file/d/x/view", False),
    ("", False),
    (None, False),
    ("sheets:abc", False),
])
def test_only_our_own_references_are_recognised(reference, expected):
    assert attach.is_stored(reference) is expected


def test_a_reference_round_trips():
    assert attach.id_of(attach.reference_of("deadbeef")) == "deadbeef"


def test_an_oversized_file_is_refused_before_it_is_sent(monkeypatch):
    from ledger import store

    monkeypatch.setattr(store, "is_configured", lambda _s=None: True)
    with pytest.raises(RuntimeError, match="limit is"):
        attach.put("big.pdf", b"x" * (attach.MAX_BYTES + 1), "application/pdf", {"a": 1})


def test_an_empty_file_is_refused(monkeypatch):
    from ledger import store

    monkeypatch.setattr(store, "is_configured", lambda _s=None: True)
    with pytest.raises(RuntimeError, match="empty"):
        attach.put("nothing.pdf", b"", "application/pdf", {"a": 1})


def test_demo_mode_refuses_rather_than_pretending():
    with pytest.raises(RuntimeError, match="Demo mode"):
        attach.put("x.pdf", b"data", "application/pdf", {})


def test_get_ignores_a_reference_that_is_not_ours():
    assert attach.get("https://example.com/x.pdf") is None


def test_parts_are_reassembled_in_order_not_sheet_order(monkeypatch):
    """Rows can come back in any order; the part number decides."""
    raw = bytes(range(256)) * 20
    encoded = base64.b64encode(raw).decode()
    pieces = attach.chunks(encoded, 500)
    records = [
        {"id": "abc", "name": "s.pdf", "mimetype": "application/pdf",
         "part": index, "data": part}
        for index, part in enumerate(pieces)
    ]
    records.reverse()  # deliberately out of order

    class FakeSheet:
        def get_all_records(self):
            return records

    from ledger import store

    monkeypatch.setattr(store, "is_configured", lambda _s=None: True)
    monkeypatch.setattr(attach, "_sheet", lambda _s: FakeSheet())

    got = attach.get("sheet:abc", {"a": 1})
    assert got is not None
    assert got.data == raw
    assert got.name == "s.pdf"


def test_a_missing_attachment_returns_none(monkeypatch):
    class FakeSheet:
        def get_all_records(self):
            return [{"id": "other", "part": 0, "data": "AA=="}]

    from ledger import store

    monkeypatch.setattr(store, "is_configured", lambda _s=None: True)
    monkeypatch.setattr(attach, "_sheet", lambda _s: FakeSheet())
    assert attach.get("sheet:missing", {"a": 1}) is None


def test_corrupted_data_returns_none_rather_than_raising(monkeypatch):
    class FakeSheet:
        def get_all_records(self):
            return [{"id": "abc", "part": 0, "data": "!!!not base64!!!"}]

    from ledger import store

    monkeypatch.setattr(store, "is_configured", lambda _s=None: True)
    monkeypatch.setattr(attach, "_sheet", lambda _s: FakeSheet())
    assert attach.get("sheet:abc", {"a": 1}) is None


# --------------------------------------------- attaching to an entry already saved

class _FakeStreamlit:
    """Just enough of `st` to run the attachment field and see what it drew.

    The real dialog cannot be opened from a test — `st.dialog` needs a click on
    a row that demo data has no sheet row for — but the branch that matters is
    plain Python: what is offered, and what comes back.
    """

    def __init__(self, *, checkbox=False, typed=None):
        self.drawn: list[str] = []
        self._checkbox = checkbox
        self._typed = typed

    def caption(self, text, **_kw):
        self.drawn.append(f"caption:{text}")

    def checkbox(self, label, **_kw):
        self.drawn.append(f"checkbox:{label}")
        return self._checkbox

    def text_input(self, label, value="", **_kw):
        self.drawn.append(f"text_input:{label}")
        return self._typed if self._typed is not None else value

    def file_uploader(self, label, **_kw):
        self.drawn.append(f"file_uploader:{label}")
        return None


class _Row:
    def __init__(self, attachment=""):
        self.attachment = attachment
        self.row = 5


def _field(monkeypatch, row, **fake):
    from ledger import ui

    stub = _FakeStreamlit(**fake)
    monkeypatch.setattr(ui, "st", stub)
    return ui.attachment_field(row), stub


def test_an_entry_with_nothing_attached_is_offered_an_upload(monkeypatch):
    (kept, upload), stub = _field(monkeypatch, _Row())
    assert kept == "" and upload is None
    assert any(d.startswith("file_uploader:") for d in stub.drawn), (
        "editing an entry must offer a file, not a box to paste a reference into"
    )


def test_a_stored_file_is_shown_rather_than_asked_for_again(monkeypatch):
    """`sheet:ab12…` is this app's own bookkeeping. Nobody can retype it."""
    (kept, _), stub = _field(monkeypatch, _Row("sheet:ab12cd"))
    assert kept == "sheet:ab12cd", "the file must survive an edit that ignores it"
    assert not any(d.startswith("text_input:") for d in stub.drawn)
    assert any(d.startswith("file_uploader:") for d in stub.drawn)


def test_a_stored_file_can_be_taken_off(monkeypatch):
    (kept, _), _ = _field(monkeypatch, _Row("sheet:ab12cd"), checkbox=True)
    assert kept == ""


def test_a_pasted_link_stays_editable(monkeypatch):
    """A statement kept in Drive is a real thing people have."""
    (kept, _), stub = _field(
        monkeypatch, _Row("https://example.com/a.pdf"),
        typed="https://example.com/b.pdf",
    )
    assert kept == "https://example.com/b.pdf"
    assert any(d.startswith("text_input:") for d in stub.drawn)
    assert any(d.startswith("file_uploader:") for d in stub.drawn)
