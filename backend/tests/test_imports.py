from __future__ import annotations

import io

import pytest

from tests.test_api import chart, client  # noqa: F401

CSV = """Date,Description,Amount
2026-01-05,WHOLE FOODS MARKET #123,-84.32
2026-01-06,ACME CORP PAYROLL,3200.00
01/07/2026,SHELL OIL 4432,-45.10
2026-01-08,Coffee Bar,-3.50
2026-01-08,Coffee Bar,-3.50
"""

DEBIT_CREDIT_CSV = """Post Date,Payee,Withdrawal,Deposit
2026-02-01,RENT PAYMENT,1800.00,
2026-02-02,REFUND,,25.00
"""


def upload(client, account_id, content, name="bank.csv"):
    return client.post(
        "/imports/preview",
        data={"account_id": str(account_id)},
        files={"file": (name, io.BytesIO(content.encode()), "text/csv")},
    )


def test_preview_parses_dates_amounts_and_signs(client, chart):
    r = upload(client, chart["assets:checking"], CSV)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert body["duplicates"] == 0

    rows = body["rows"]
    assert rows[0]["amount_minor"] == -8432
    assert rows[1]["amount_minor"] == 320000
    assert rows[2]["date"] == "2026-01-07"  # M/D/Y parsed


def test_identical_same_day_rows_get_distinct_ids(client, chart):
    rows = upload(client, chart["assets:checking"], CSV).json()["rows"]
    coffees = [r for r in rows if r["description"] == "Coffee Bar"]
    assert len(coffees) == 2
    assert coffees[0]["external_id"] != coffees[1]["external_id"]


def test_debit_credit_columns(client, chart):
    rows = upload(client, chart["assets:checking"], DEBIT_CREDIT_CSV).json()["rows"]
    assert rows[0]["amount_minor"] == -180000  # withdrawal is money out
    assert rows[1]["amount_minor"] == 2500     # deposit is money in


def test_missing_columns_are_reported_not_raised(client, chart):
    r = upload(client, chart["assets:checking"], "foo,bar\n1,2\n")
    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert "missing column" in r.json()["errors"][0]


def test_bad_rows_are_skipped_with_an_error_line(client, chart):
    bad = "Date,Description,Amount\nnot-a-date,X,-1.00\n2026-01-01,Good,-2.00\n"
    body = upload(client, chart["assets:checking"], bad).json()
    assert body["total"] == 1
    assert "line 2" in body["errors"][0]


def test_import_target_must_be_a_bank_or_card(client, chart):
    assert upload(client, chart["expenses:food"], CSV).status_code == 422


def test_rules_drive_the_suggestion(client, chart):
    client.post(
        "/rules",
        json={
            "pattern": "WHOLE FOODS",
            "match_type": "contains",
            "account_id": chart["expenses:food"],
        },
    )
    rows = upload(client, chart["assets:checking"], CSV).json()["rows"]
    assert rows[0]["suggested_account_code"] == "expenses:food"


def test_commit_creates_transactions_and_keeps_the_books_square(client, chart):
    preview = upload(client, chart["assets:checking"], CSV).json()
    r = client.post(
        "/imports/commit",
        json={"account_id": chart["assets:checking"], "rows": preview["rows"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 5
    assert client.get("/health").json()["balanced"] is True

    # Uncategorized rows parked in holding accounts, not lost
    codes = {a["code"] for a in client.get("/accounts").json()}
    assert "expenses:uncategorized" in codes


def test_importing_the_same_file_twice_creates_no_duplicates(client, chart):
    first = upload(client, chart["assets:checking"], CSV).json()
    client.post("/imports/commit", json={"account_id": chart["assets:checking"], "rows": first["rows"]})

    second = upload(client, chart["assets:checking"], CSV).json()
    assert second["duplicates"] == 5
    assert all(row["duplicate"] for row in second["rows"])

    result = client.post(
        "/imports/commit", json={"account_id": chart["assets:checking"], "rows": second["rows"]}
    ).json()
    assert result["created"] == 0
    assert result["skipped"] == 5
    assert client.get("/health").json()["transactions"] == 5


def test_commit_honours_a_user_override_of_the_suggestion(client, chart):
    preview = upload(client, chart["assets:checking"], CSV).json()
    rows = preview["rows"]
    rows[0]["suggested_account_id"] = chart["expenses:transport"]
    client.post("/imports/commit", json={"account_id": chart["assets:checking"], "rows": rows})

    by_code = {a["code"]: a for a in client.get("/accounts").json()}
    assert by_code["expenses:transport"]["balance_minor"] == 8432
