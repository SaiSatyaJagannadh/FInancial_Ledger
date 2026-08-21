from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base


@pytest.fixture()
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()
    os.unlink(path)


@pytest.fixture()
def chart(client):
    """A usable chart of accounts, returned as code -> id."""
    spec = [
        ("assets:checking", "Checking", "asset"),
        ("liabilities:card", "Credit Card", "liability"),
        ("equity:opening", "Opening Balances", "equity"),
        ("income:salary", "Salary", "income"),
        ("expenses:food", "Food", "expense"),
        ("expenses:transport", "Transport", "expense"),
    ]
    out = {}
    for code, name, type_ in spec:
        r = client.post("/accounts", json={"code": code, "name": name, "type": type_})
        assert r.status_code == 201, r.text
        out[code] = r.json()["id"]
    return out


def tx_payload(chart, amount_minor=5000, day="2026-01-15", desc="Groceries"):
    return {
        "date": day,
        "description": desc,
        "postings": [
            {"account_id": chart["expenses:food"], "amount_minor": amount_minor},
            {"account_id": chart["assets:checking"], "amount_minor": -amount_minor},
        ],
    }


def test_health_reports_a_balanced_empty_ledger(client):
    body = client.get("/health").json()
    assert body["balanced"] is True
    assert body["trial_balance_minor"] == 0
    assert body["ai_enabled"] in (True, False)


def test_account_crud(client):
    r = client.post(
        "/accounts", json={"code": "Assets:Cash", "name": "Cash", "type": "asset"}
    )
    assert r.status_code == 201
    assert r.json()["code"] == "assets:cash"  # normalized

    assert client.post(
        "/accounts", json={"code": "assets:cash", "name": "Dup", "type": "asset"}
    ).status_code == 409

    account_id = r.json()["id"]
    assert client.patch(f"/accounts/{account_id}", json={"name": "Wallet"}).json()["name"] == "Wallet"
    assert client.delete(f"/accounts/{account_id}").status_code == 204
    assert client.patch("/accounts/9999", json={"name": "x"}).status_code == 404


def test_account_cannot_parent_itself_or_a_descendant(client):
    parent = client.post(
        "/accounts", json={"code": "expenses:home", "name": "Home", "type": "expense"}
    ).json()
    child = client.post(
        "/accounts",
        json={
            "code": "expenses:home:rent",
            "name": "Rent",
            "type": "expense",
            "parent_id": parent["id"],
        },
    ).json()

    assert client.patch(
        f"/accounts/{parent['id']}", json={"parent_id": parent["id"]}
    ).status_code == 422
    assert client.patch(
        f"/accounts/{parent['id']}", json={"parent_id": child["id"]}
    ).status_code == 422


def test_create_and_read_transaction(client, chart):
    r = client.post("/transactions", json=tx_payload(chart))
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["postings"]) == 2
    assert body["postings"][0]["amount"] == "50.00"

    assert client.get(f"/transactions/{body['id']}").status_code == 200
    assert client.get("/health").json()["balanced"] is True


def test_unbalanced_transaction_is_a_422_that_names_the_gap(client, chart):
    payload = tx_payload(chart)
    payload["postings"][1]["amount_minor"] = -4000
    r = client.post("/transactions", json=payload)
    assert r.status_code == 422
    assert "balance" in r.json()["detail"].lower()


def test_single_posting_is_rejected_by_schema(client, chart):
    payload = tx_payload(chart)
    payload["postings"] = payload["postings"][:1]
    assert client.post("/transactions", json=payload).status_code == 422


def test_transaction_filters(client, chart):
    client.post("/transactions", json=tx_payload(chart, 5000, "2026-01-10", "Coffee Shop"))
    client.post("/transactions", json=tx_payload(chart, 2500, "2026-02-10", "Market"))

    assert len(client.get("/transactions").json()) == 2
    assert len(client.get("/transactions", params={"q": "coffee"}).json()) == 1
    assert len(client.get("/transactions", params={"start": "2026-02-01"}).json()) == 1
    assert (
        len(client.get("/transactions", params={"account_id": chart["expenses:food"]}).json())
        == 2
    )
    assert len(client.get("/transactions", params={"account_id": chart["equity:opening"]}).json()) == 0


def test_edit_and_delete_transaction(client, chart):
    tx_id = client.post("/transactions", json=tx_payload(chart)).json()["id"]

    updated = tx_payload(chart, 9000, "2026-03-01", "Corrected")
    assert client.put(f"/transactions/{tx_id}", json=updated).json()["description"] == "Corrected"

    broken = tx_payload(chart)
    broken["postings"][1]["amount_minor"] = -1
    assert client.put(f"/transactions/{tx_id}", json=broken).status_code == 422

    assert client.delete(f"/transactions/{tx_id}").status_code == 204
    assert client.get(f"/transactions/{tx_id}").status_code == 404
    assert client.get("/health").json()["trial_balance_minor"] == 0


def test_account_balances_use_natural_signs(client, chart):
    client.post(
        "/transactions",
        json={
            "date": "2026-01-01",
            "description": "Paycheck",
            "postings": [
                {"account_id": chart["assets:checking"], "amount_minor": 300000},
                {"account_id": chart["income:salary"], "amount_minor": -300000},
            ],
        },
    )
    by_code = {a["code"]: a for a in client.get("/accounts").json()}
    assert by_code["assets:checking"]["balance_minor"] == 300000
    assert by_code["income:salary"]["balance_minor"] == 300000  # flipped to positive
    assert by_code["assets:checking"]["balance"] == "3000.00"


def test_accounts_with_history_cannot_be_deleted(client, chart):
    client.post("/transactions", json=tx_payload(chart))
    r = client.delete(f"/accounts/{chart['expenses:food']}")
    assert r.status_code == 409
    assert "archive" in r.json()["detail"]

    assert client.post(f"/accounts/{chart['expenses:food']}/archive").json()["archived"] is True
    # and archived accounts refuse new postings
    assert client.post("/transactions", json=tx_payload(chart)).status_code == 422


def test_rules_crud_and_validation(client, chart):
    r = client.post(
        "/rules",
        json={"pattern": "WHOLE FOODS", "match_type": "contains", "account_id": chart["expenses:food"]},
    )
    assert r.status_code == 201
    assert len(client.get("/rules").json()) == 1

    assert client.post(
        "/rules", json={"pattern": "[", "match_type": "regex", "account_id": chart["expenses:food"]}
    ).status_code == 422
    assert client.post(
        "/rules", json={"pattern": "x", "match_type": "fuzzy", "account_id": chart["expenses:food"]}
    ).status_code == 422
    assert client.post(
        "/rules", json={"pattern": "x", "match_type": "contains", "account_id": 9999}
    ).status_code == 422

    assert client.delete(f"/rules/{r.json()['id']}").status_code == 204


def test_reports(client, chart):
    client.post(
        "/transactions",
        json={
            "date": "2026-01-05",
            "description": "Paycheck",
            "postings": [
                {"account_id": chart["assets:checking"], "amount_minor": 500000},
                {"account_id": chart["income:salary"], "amount_minor": -500000},
            ],
        },
    )
    client.post("/transactions", json=tx_payload(chart, 12000, "2026-01-10", "Groceries"))
    client.post("/transactions", json=tx_payload(chart, 3000, "2026-02-10", "Groceries"))

    sheet = client.get("/reports/balance-sheet", params={"as_of": "2026-12-31"}).json()
    assert sheet["total_assets_minor"] == 485000
    assert sheet["balanced"] is True

    stmt = client.get(
        "/reports/income-statement", params={"start": "2026-01-01", "end": "2026-01-31"}
    ).json()
    assert stmt["total_income_minor"] == 500000
    assert stmt["total_expenses_minor"] == 12000
    assert stmt["net_minor"] == 488000

    spend = client.get(
        "/reports/spend-by-category", params={"start": "2026-01-01", "end": "2026-12-31"}
    ).json()
    assert spend[0]["code"] == "expenses:food"
    assert spend[0]["amount_minor"] == 15000

    monthly = client.get("/reports/monthly").json()
    assert {p["month"] for p in monthly} == {"2026-01", "2026-02"}
    assert monthly[0]["net_minor"] == 488000

    net_worth = client.get("/reports/net-worth").json()
    assert net_worth[-1]["net_worth_minor"] == 485000  # cumulative across months


def test_reports_on_an_empty_ledger_do_not_explode(client):
    assert client.get("/reports/balance-sheet").status_code == 200
    assert client.get("/reports/monthly").json() == []
    assert client.get("/reports/net-worth").json() == []
    assert client.get("/reports/spend-by-category").json() == []
