"""The two places a colour lives, and the table the redesign drew by hand.

Streamlit draws its own widgets from `.streamlit/config.toml` and cannot read a
CSS variable, so the inks are written twice. If they drift, a figure in one shade
sits beside a button in another and nothing fails, so this does.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ledger.ui import table_html

ROOT = Path(__file__).resolve().parent.parent
TOKENS = (ROOT / "ledger" / "styles" / "tokens.css").read_text(encoding="utf-8")


def _token(name: str) -> str:
    return re.search(rf"--color-{name}:\s*(#[0-9A-Fa-f]{{6}})", TOKENS).group(1)


def test_the_theme_and_the_stylesheet_use_the_same_inks():
    theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
    assert theme["primaryColor"] == _token("accent")
    assert theme["backgroundColor"] == _token("paper")
    assert theme["secondaryBackgroundColor"] == _token("sheet")
    assert theme["textColor"] == _token("ink")
    assert theme["borderColor"] == _token("edge")
    assert theme["redTextColor"] == _token("out")
    assert theme["greenTextColor"] == _token("back")
    assert theme["sidebar"]["backgroundColor"] == _token("paper-2")


def test_no_chart_series_borrows_the_colours_of_money():
    """Red and green say which way money moved. A person's bar in the same red
    reads as a statement about that person."""
    theme = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
    assert not {_token("out"), _token("back")} & set(theme["chartCategoricalColors"])


def test_every_cell_and_heading_of_a_ruled_table_is_escaped():
    """A note is free text, and is also written by a model reading a document."""
    hostile = "<img src=x onerror=alert(1)>"
    out = table_html([{hostile: hostile, "Note": hostile, "Net": (hostile, "out")}])
    assert "<img" not in out
    assert out.count("&lt;img") == 4  # the heading, the row's name and both cells

    # A heading is also written onto every cell of its column, for the phone.
    labelled = table_html([{"Person": "Ravi", hostile: "x"}])
    assert "<img" not in labelled and 'data-label="&lt;img' in labelled


def test_a_table_cell_carries_its_column_name_for_the_phone():
    out = table_html([{"Person": "Ravi", "Net owed": ("₹5.00", "out")}], numeric=("Net owed",))
    assert '<th scope="row">Ravi</th>' in out
    assert 'data-label="Net owed"' in out
    assert 'class="khata-num khata-out"' in out


def test_no_rows_draws_no_table():
    assert table_html([]) == ""
