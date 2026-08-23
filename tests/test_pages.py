"""Every page must actually render.

This exists because of a bug that every other test missed. `views/edit_entries.py`
already had a local `people = sorted(...)`, so `from ledger import people`
was shadowed by a list and the page died with
`'list' object has no attribute 'load'` — while the unit tests for the module
underneath it passed perfectly.

CI boots the app and curls it, but that only renders the *default* page. A
crash on any of the other eight was invisible until somebody clicked it.
Running each page here is the cheapest thing that would have caught it.

These run in demo mode: no secrets, no network, deterministic sample data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

VIEWS = Path(__file__).resolve().parent.parent / "views"

#: Every page the router offers, discovered rather than listed, so a new page
#: is covered the moment it exists instead of when somebody remembers.
PAGES = sorted(p.name for p in VIEWS.glob("*.py") if not p.name.startswith("_"))


def test_the_pages_were_actually_found():
    """A glob that matches nothing would make every test below vacuously pass."""
    assert len(PAGES) >= 8, PAGES
    assert "dashboard.py" in PAGES and "interest.py" in PAGES


#: `st.page_link` resolves against the `st.navigation` router, which
#: `AppTest.from_file` does not set up — it runs one script, not the app. The
#: link works in the real app (and in CI's boot-and-curl); only this harness
#: cannot see it. Tolerated by exact signature so a genuine crash still fails.
HARNESS_GAP = "url_pathname"


def render(page: str) -> list[str]:
    """Run one page in demo mode and return the real exceptions it raised."""
    app = AppTest.from_file(str(VIEWS / page), default_timeout=30)
    app.run()
    return [
        str(e.message) for e in app.exception
        if HARNESS_GAP not in str(e.message)
    ]


@pytest.mark.parametrize("page", PAGES)
def test_the_page_renders_without_an_exception(page):
    raised = render(page)
    assert not raised, f"{page} raised on render: " + "; ".join(raised)


@pytest.mark.parametrize("page", PAGES)
def test_the_page_says_it_is_in_demo_mode_rather_than_pretending(page):
    """With no credentials every page must still come up and be honest about
    which data is on screen."""
    app = AppTest.from_file(str(VIEWS / page), default_timeout=30)
    app.run()
    assert not render(page)
    text = " ".join(
        str(getattr(block, "value", "")) for block in (*app.warning, *app.info, *app.error)
    )
    # Either it names demo mode, or it stopped early for a reason it stated.
    assert text or app.title or app.markdown, f"{page} rendered nothing at all"


def test_the_router_lists_only_pages_that_exist():
    """A typo in app.py is a 404 in the sidebar, not an import error."""
    router = (VIEWS.parent / "app.py").read_text()
    named = {
        line.split('"')[1].split("/")[-1]
        for line in router.splitlines()
        if 'st.Page("views/' in line
    }
    missing = sorted(name for name in named if name not in PAGES)
    assert not missing, f"app.py points at pages that do not exist: {missing}"
