"""Finding the NVIDIA key wherever a hand-edited secrets file put it."""

from __future__ import annotations

import pytest

from ledger import ui

KEY = "nvapi-abc123"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for name in ui._KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def use_secrets(monkeypatch, mapping: dict) -> None:
    monkeypatch.setattr(ui.st, "secrets", mapping, raising=False)


def test_finds_a_top_level_key(monkeypatch):
    use_secrets(monkeypatch, {"NVIDIA_API_KEY": KEY})
    assert ui.api_key() == KEY


def test_finds_a_key_that_landed_inside_a_section(monkeypatch):
    """The real failure: pasted at the bottom of the box, so TOML scoped it
    under the last section header instead of the top level."""
    use_secrets(monkeypatch, {
        "gcp_service_account": {"type": "service_account"},
        "sheet": {"url": "https://example", "NVIDIA_API_KEY": KEY},
    })
    assert ui.api_key() == KEY


def test_finds_it_under_the_credentials_block(monkeypatch):
    use_secrets(monkeypatch, {"gcp_service_account": {"NVIDIA_API_KEY": KEY}})
    assert ui.api_key() == KEY


@pytest.mark.parametrize("name", ["nvidia_api_key", "NVIDIA_KEY", "nvapi_key"])
def test_accepts_the_names_people_actually_type(monkeypatch, name):
    use_secrets(monkeypatch, {name: KEY})
    assert ui.api_key() == KEY


def test_strips_whitespace(monkeypatch):
    use_secrets(monkeypatch, {"NVIDIA_API_KEY": f"  {KEY}\n"})
    assert ui.api_key() == KEY


def test_absent_means_empty_not_an_error(monkeypatch):
    use_secrets(monkeypatch, {"sheet": {"url": "https://example"}})
    assert ui.api_key() == ""


def test_falls_back_to_the_environment(monkeypatch):
    use_secrets(monkeypatch, {})
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    assert ui.api_key() == KEY


def test_survives_secrets_that_raise(monkeypatch):
    """Streamlit raises rather than returning empty when there is no secrets
    file at all; that must not take the page down."""

    class Exploding:
        def get(self, _name):
            raise RuntimeError("no secrets file")

        def values(self):
            raise RuntimeError("no secrets file")

    use_secrets(monkeypatch, Exploding())
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    assert ui.api_key() == KEY


def test_a_non_mapping_value_does_not_break_the_walk(monkeypatch):
    use_secrets(monkeypatch, {"count": 3, "flag": True, "sheet": {"NVIDIA_API_KEY": KEY}})
    assert ui.api_key() == KEY
