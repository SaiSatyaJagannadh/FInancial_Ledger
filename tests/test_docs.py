"""Turning an uploaded file into something a model can read."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from ledger import docs
from ledger.docs import UnreadableDocument, read, shrink_image, suffix_of


def noisy_png(width: int, height: int) -> bytes:
    """A PNG that cannot compress away to nothing, so size tests mean something."""
    image = Image.new("RGB", (width, height))
    for x in range(0, width, 5):
        for y in range(0, height, 7):
            image.putpixel((x, y), (x % 256, y % 256, (x * y) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def workbook(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- big images

def test_a_large_image_is_shrunk_not_refused():
    """The bug: a 244 KB photo was rejected outright. It should be resized."""
    raw = noisy_png(2600, 1900)
    assert len(raw) > docs.VISION_BUDGET

    got = read("statement.png", raw, "image/png")

    assert got.kind == "image"
    assert len(got.data) <= docs.VISION_BUDGET
    assert got.mimetype == "image/jpeg"
    assert "Resized" in got.note


def test_the_shrunk_image_is_still_a_valid_image():
    raw = noisy_png(2200, 1600)
    data, _, _ = shrink_image(raw)
    reopened = Image.open(io.BytesIO(data))
    reopened.load()
    assert reopened.size[0] > 0


def test_a_small_image_is_left_untouched():
    raw = noisy_png(40, 40)
    got = read("small.png", raw, "image/png")
    assert got.data == raw
    assert got.note == ""


def test_an_image_with_transparency_survives():
    """RGBA cannot be written as JPEG without converting first, so an
    over-budget transparent image is where that conversion gets exercised."""
    image = Image.new("RGBA", (2600, 1900), (255, 0, 0, 128))
    for x in range(0, 2600, 3):
        for y in range(0, 1900, 5):
            image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    assert len(buffer.getvalue()) > docs.VISION_BUDGET

    data, mimetype, _ = shrink_image(buffer.getvalue())

    assert mimetype == "image/jpeg"
    assert len(data) <= docs.VISION_BUDGET


def test_a_corrupt_image_says_so():
    with pytest.raises(UnreadableDocument, match="could not be opened"):
        shrink_image(b"\x89PNG\r\n\x1a\n" + b"garbage" * 40_000)


# ------------------------------------------------------------------ documents

def test_a_csv_is_read_as_text():
    got = read("s.csv", b"date,who,amount\n2026-01-01,Ravi,2500\n", "text/csv")
    assert got.kind == "text"
    assert "Ravi" in got.text and "2500" in got.text


def test_a_spreadsheet_is_read_as_text():
    data = workbook([["Date", "Person", "Amount"], ["2026-01-01", "Amma", 2500]])
    got = read("book.xlsx", data,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert got.kind == "text"
    assert "Amma" in got.text and "2500" in got.text


def test_an_empty_spreadsheet_says_so():
    with pytest.raises(UnreadableDocument, match="empty"):
        read("book.xlsx", workbook([]), "")


def test_plain_text_passes_through():
    assert read("n.txt", b"gave 500 to amma", "text/plain").text == "gave 500 to amma"


def test_a_scanned_pdf_suggests_a_screenshot():
    """A PDF with no text layer cannot be read, and the advice matters."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(UnreadableDocument, match="scan"):
        read("scan.pdf", buffer.getvalue(), "application/pdf")


def test_a_broken_pdf_fails_readably():
    with pytest.raises(UnreadableDocument):
        read("x.pdf", b"definitely not a pdf", "application/pdf")


@pytest.mark.parametrize("name", ["a.pdf", "a.csv", "a.png", "a.xlsx", "a.txt"])
def test_an_empty_file_is_refused_before_parsing(name):
    with pytest.raises(UnreadableDocument, match="empty"):
        read(name, b"")


def test_an_unsupported_type_lists_what_works():
    with pytest.raises(UnreadableDocument) as caught:
        read("thing.dmg", b"xx", "application/octet-stream")
    assert "pdf" in str(caught.value)


def test_long_text_is_truncated_and_says_so():
    got = read("long.txt", b"x" * (docs.MAX_TEXT_CHARS + 1000), "text/plain")
    assert len(got.text) == docs.MAX_TEXT_CHARS
    assert "first" in got.note


def test_the_type_is_taken_from_the_name_when_the_mimetype_is_blank():
    """Browsers do not always send a mimetype."""
    assert read("s.csv", b"a,b\n1,2\n", "").kind == "text"
    assert read("p.png", noisy_png(30, 30), "").kind == "image"


@pytest.mark.parametrize("name,expected", [
    ("a.PDF", ".pdf"), ("photo.JPEG", ".jpeg"), ("noext", ""), ("", ""),
])
def test_suffix_detection_is_case_insensitive(name, expected):
    assert suffix_of(name) == expected


def test_every_accepted_suffix_is_offered_without_a_dot():
    """Streamlit's uploader wants bare extensions."""
    assert all(not s.startswith(".") for s in docs.ACCEPTED)
    assert "pdf" in docs.ACCEPTED and "xlsx" in docs.ACCEPTED and "png" in docs.ACCEPTED
