"""AI layer tests. The model is never called for real — what matters is that we
reject bad output, degrade without a key, and never let a model produce a number."""

from __future__ import annotations

import io

import pytest

from app import ai as ai_mod
from app.models import Account, AccountType
from tests.test_api import chart, client  # noqa: F401
from tests.test_imports import CSV, upload  # noqa: F401


@pytest.fixture()
def imported(client, chart):
    preview = upload(client, chart["assets:checking"], CSV).json()
    client.post(
        "/imports/commit",
        json={"account_id": chart["assets:checking"], "rows": preview["rows"]},
    )
    return chart


# --------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the JSON you asked for:\n{"a": 1}',
        'Sure!\n```json\n{"a": 1}\n```\nHope that helps.',
    ],
)
def test_extract_json_survives_fences_and_prose(raw):
    assert ai_mod.extract_json(raw) == {"a": 1}


def test_extract_json_raises_on_prose_only():
    with pytest.raises(ValueError, match="did not return JSON"):
        ai_mod.extract_json("I cannot help with that.")


# ------------------------------------------------------------ plan sanitizing


def test_sanitize_plan_drops_unknown_types_codes_and_dates():
    plan = ai_mod.sanitize_plan(
        {
            "account_types": ["expense", "wizardry"],
            "account_codes": ["expenses:food", "expenses:invented"],
            "start": "2026-13-45",
            "end": "2026-03-31",
            "text": "  coffee ",
            "group_by": "sideways",
            "intent": "x" * 500,
        },
        {"expenses:food"},
    )
    assert plan["account_types"] == ["expense"]
    assert plan["account_codes"] == ["expenses:food"]
    assert plan["start"] is None            # unparseable date dropped
    assert plan["end"] == "2026-03-31"
    assert plan["text"] == "coffee"
    assert plan["group_by"] == "account"    # bad group_by falls back
    assert len(plan["intent"]) == 200


def test_sanitize_plan_defaults_to_expenses_when_the_model_says_nothing():
    assert ai_mod.sanitize_plan({}, set())["account_types"] == ["expense"]


# --------------------------------------------------- hallucinated id rejection


def test_llm_categorize_drops_invented_account_ids(client, chart, monkeypatch, db=None):
    from app.db import get_db

    session = next(client.app.dependency_overrides[get_db]())
    accounts = session.query(Account).filter(Account.type == AccountType.expense).all()
    real_id = accounts[0].id

    monkeypatch.setattr(
        ai_mod,
        "_complete",
        lambda *a, **k: (
            '{"suggestions": ['
            f'{{"transaction_id": 1, "account_id": {real_id}, "confidence": 0.9, "reason": "ok"}},'
            '{"transaction_id": 1, "account_id": 424242, "confidence": 0.9, "reason": "invented"},'
            '{"transaction_id": 999, "account_id": %d, "confidence": 0.9, "reason": "unasked"}'
            "]}" % real_id
        ),
    )
    out = ai_mod.llm_categorize(session, [(1, "WHOLE FOODS", -8432)], accounts)
    assert len(out) == 1
    assert out[0].account_id == real_id


def test_llm_categorize_clamps_confidence(client, chart, monkeypatch):
    from app.db import get_db

    session = next(client.app.dependency_overrides[get_db]())
    accounts = session.query(Account).filter(Account.type == AccountType.expense).all()
    monkeypatch.setattr(
        ai_mod,
        "_complete",
        lambda *a, **k: '{"suggestions": [{"transaction_id": 1, "account_id": %d, "confidence": 7.5}]}'
        % accounts[0].id,
    )
    assert ai_mod.llm_categorize(session, [(1, "X", -100)], accounts)[0].confidence == 1.0


# -------------------------------------------------------------- degradation


def test_categorize_without_a_key_falls_back_and_says_so(imported, client, no_ai_key):
    body = client.post("/ai/categorize", json={"limit": 10}).json()
    assert body["source"] in ("heuristic", "rule")
    assert body["note"] and "NVIDIA_API_KEY" in body["note"]


def test_ask_without_a_key_returns_503_not_a_crash(client, no_ai_key):
    r = client.post("/ai/ask", json={"question": "how much did I spend on food?"})
    assert r.status_code == 503
    assert "NVIDIA_API_KEY" in r.json()["detail"]


def test_categorize_survives_a_model_failure(imported, client, ai_key, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ai_mod, "llm_categorize", boom)
    body = client.post("/ai/categorize", json={"limit": 10}).json()
    assert body["source"] == "heuristic"
    assert "model call failed" in body["note"]


def test_rules_win_before_the_model_is_ever_called(imported, client, monkeypatch):
    client.post(
        "/rules",
        json={
            "pattern": "WHOLE FOODS",
            "match_type": "contains",
            "account_id": imported["expenses:food"],
        },
    )

    def must_not_run(*a, **k):
        raise AssertionError("the model was called for a transaction a rule matched")

    monkeypatch.setattr(ai_mod, "_complete", must_not_run)
    body = client.post(
        "/ai/categorize", json={"limit": 10}
    ).json()
    whole_foods = [s for s in body["suggestions"] if "WHOLE FOODS" in s["description"]]
    assert whole_foods and whole_foods[0]["source"] == "rule"
    assert whole_foods[0]["confidence"] == 1.0


# ------------------------------------------------------------------- applying


def test_apply_moves_the_holding_leg_and_keeps_the_books_square(imported, client):
    suggestions = client.post("/ai/categorize", json={"limit": 10}).json()["suggestions"]
    tx = client.get("/transactions", params={"q": "WHOLE FOODS"}).json()[0]

    r = client.post(
        "/ai/apply",
        json={"assignments": [{"transaction_id": tx["id"], "account_id": imported["expenses:food"]}]},
    )
    assert r.json()["updated"] == 1
    assert r.json()["errors"] == []

    by_code = {a["code"]: a for a in client.get("/accounts").json()}
    assert by_code["expenses:food"]["balance_minor"] == 8432
    # the remaining spend rows stay parked: 45.10 + 3.50 + 3.50
    assert by_code["expenses:uncategorized"]["balance_minor"] == 4510 + 350 + 350
    assert client.get("/health").json()["balanced"] is True


def test_apply_reports_unknown_ids_instead_of_failing_the_batch(imported, client):
    tx = client.get("/transactions").json()[0]
    r = client.post(
        "/ai/apply",
        json={
            "assignments": [
                {"transaction_id": 99999, "account_id": imported["expenses:food"]},
                {"transaction_id": tx["id"], "account_id": 99999},
                {"transaction_id": tx["id"], "account_id": imported["expenses:food"]},
            ]
        },
    ).json()
    assert r["updated"] == 1
    assert len(r["errors"]) == 2


# ------------------------------------------------- the model never does maths


def test_run_query_computes_totals_in_sql(imported, client):
    from app.db import get_db

    session = next(client.app.dependency_overrides[get_db]())
    plan = ai_mod.sanitize_plan(
        {"account_types": ["expense"], "start": "2026-01-01", "end": "2026-12-31"},
        set(),
    )
    total, rows = ai_mod.run_query(session, plan)
    assert total == 8432 + 4510 + 350 + 350  # every expense row, summed by the database
    assert sum(r["amount_minor"] for r in rows) == total


def test_ask_ignores_a_total_the_model_tries_to_invent(imported, client, ai_key, monkeypatch):
    monkeypatch.setattr(
        ai_mod,
        "plan_query",
        lambda q, a, t: ai_mod.sanitize_plan({"account_types": ["expense"]}, set()),
    )
    monkeypatch.setattr(ai_mod, "narrate", lambda *a, **k: "You spent one million dollars.")

    body = client.post("/ai/ask", json={"question": "total expenses?"}).json()
    assert body["total_minor"] == 8432 + 4510 + 350 + 350  # from SQL, not the sentence
    assert body["query"]["account_types"] == ["expense"]


def test_narrate_falls_back_to_the_computed_figure_if_the_model_dies(monkeypatch):
    monkeypatch.setattr(ai_mod, "_complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    text = ai_mod.narrate("q", {}, 123456, [{"label": "a", "amount_minor": 123456}])
    assert "1,234.56" in text
