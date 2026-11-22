"""Email the interest still due, once a week, to you and one other address.

Streamlit Cloud only runs while somebody has the app open, so it cannot send
anything on a schedule. `.github/workflows/weekly-interest.yml` runs this from
GitHub Actions instead:

    python -m ledger.weekly --send

It reads two environment variables, both GitHub secrets:

- `LEDGER_SECRETS_TOML`: the app's whole secrets file, pasted as it is. It needs
  `[gcp_service_account]`, `[sheet]` and a `[notify]` with `to` and `password`.
- `WEEKLY_EXTRA_TO`: the second address. Optional.

The figures are the Interest page's own (`interest.still_due`), so the email
and the screen cannot disagree. The log says only "sent" or why not: the repo
is public, and a name or an amount in a job log would be public too.

`python -m ledger.weekly` with no flag runs the self-check instead.
"""

from __future__ import annotations

import os
import sys
import tomllib
from datetime import date

from ledger import interest, notify, store
from ledger.money import Currency, format_money


def body(charges: list, today: date) -> str:
    """What the email says: per currency, who owes how much, month by month."""
    lines = [f"Interest still due as of {today:%A %d %b %Y}.", ""]
    for currency in Currency:
        owing = interest.still_due(charges, currency)
        if not owing:
            lines += [f"{currency.label}: nothing due.", ""]
            continue
        total = sum(r["remaining_minor"] for r in owing)
        lines.append(f"{currency.label}: {format_money(total, currency)} in total")
        for row in owing:
            months = ", ".join(
                f"{c.month_label} {format_money(c.remaining_minor, currency)}"
                for c in row["charges"]
            )
            lines.append(f"- {row['person']}: "
                         f"{format_money(row['remaining_minor'], currency)} ({months})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def send(environ=os.environ, today: date | None = None) -> str:
    """Send this week's email. Returns "" when sent, else why not."""
    try:
        secrets = tomllib.loads(environ.get("LEDGER_SECRETS_TOML", ""))
    except tomllib.TOMLDecodeError:
        return "LEDGER_SECRETS_TOML is not valid TOML."
    config = notify.settings(secrets)
    if config is None:
        return "LEDGER_SECRETS_TOML has no [notify] with both `to` and `password`."
    if not store.is_configured(secrets):
        return "LEDGER_SECRETS_TOML has no [gcp_service_account] and [sheet]."
    try:
        charges, problems = interest.load(secrets)
    except Exception as exc:  # noqa: BLE001 — say why, never a traceback with data
        return f"Could not read the interest tab: {type(exc).__name__}."
    if not charges and problems:
        # An unreadable tab must not arrive as "nothing due".
        return "Could not read the interest tab."

    extra = environ.get("WEEKLY_EXTRA_TO", "").strip()
    config = dict(config, to=", ".join(a for a in (config["to"], extra) if a))
    today = today or date.today()
    text = body(charges, today)
    if problems:
        text += f"\n{len(problems)} row(s) of the interest tab could not be read.\n"
    notify._send(f"[Personal Ledger] Interest still due, {today:%d %b %Y}", text, config)
    return notify.last_error()


def demo() -> None:
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    charges = [
        interest.Charge(date=aug, person="Ravi", amount_minor=5_000_00),
        interest.Charge(date=sep, person="Ravi", amount_minor=5_000_00, paid_minor=3_000_00),
        interest.Charge(date=sep, person="Kavita", amount_minor=1_000_00,
                        kind=interest.Kind.given),
        interest.Charge(date=sep, person="Sam", amount_minor=50_00, currency=Currency.USD),
    ]
    text = body(charges, date(2026, 11, 23))
    assert "Indian Rupees: ₹7,000.00 in total" in text, text
    assert "- Ravi: ₹7,000.00 (Aug 2026 ₹5,000.00, Sep 2026 ₹2,000.00)" in text, text
    assert "Kavita" not in text, "paid in full is not due"
    assert "US Dollars: $50.00 in total" in text, text
    assert "nothing due" in body([], date(2026, 11, 23))

    # Nothing is sent, and nothing is read, without the secrets.
    assert "no [notify]" in send({"LEDGER_SECRETS_TOML": "[sheet]\nurl='x'"})
    assert "not valid TOML" in send({"LEDGER_SECRETS_TOML": "[notify"})
    print("weekly: ok")


if __name__ == "__main__":
    if "--send" in sys.argv:
        why = send()
        print("sent" if not why else f"not sent: {why}")
        raise SystemExit(1 if why else 0)
    demo()
