"""The weekly interest email: right figures, right people, never a false "nothing due"."""

from datetime import date

from ledger import interest, notify, weekly

SECRETS = """
[gcp_service_account]
client_email = "x@example.test"
[sheet]
url = "https://docs.google.com/spreadsheets/d/x"
[notify]
to = "me@example.test"
password = "app-password"
"""


def test_the_self_check_passes():
    weekly.demo()


def test_it_goes_to_both_addresses_with_the_pages_figures(monkeypatch):
    sent = []
    monkeypatch.setattr(interest, "load", lambda s: ([interest.Charge(
        date=date(2026, 10, 1), person="Ravi", amount_minor=2_500_00)], []))
    monkeypatch.setattr(notify, "_send", lambda subject, body, config: sent.append((body, config)))
    monkeypatch.setattr(notify, "last_error", lambda: "")
    why = weekly.send({"LEDGER_SECRETS_TOML": SECRETS, "WEEKLY_EXTRA_TO": "other@example.test"},
                      today=date(2026, 11, 23))
    assert why == ""
    body, config = sent[0]
    assert config["to"] == "me@example.test, other@example.test"
    assert "- Ravi: ₹2,500.00 (Oct 2026 ₹2,500.00)" in body


def test_an_unreadable_tab_is_not_sent_as_nothing_due(monkeypatch):
    sent = []
    monkeypatch.setattr(interest, "load", lambda s: ([], ["Could not reach the interest tab."]))
    monkeypatch.setattr(notify, "_send", lambda *a: sent.append(a))
    assert weekly.send({"LEDGER_SECRETS_TOML": SECRETS}) and not sent
