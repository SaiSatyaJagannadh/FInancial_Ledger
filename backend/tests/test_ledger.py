from datetime import date, timedelta

import pytest

from app import ledger
from app.ledger import LedgerError, PostingInput
from app.models import Account, AccountType


def spend(db, accounts, amount_minor, day=date(2026, 1, 15), desc="Groceries"):
    return ledger.create_transaction(
        db,
        tx_date=day,
        description=desc,
        postings=[
            PostingInput(accounts["expenses:food"].id, amount_minor),
            PostingInput(accounts["assets:checking"].id, -amount_minor),
        ],
    )


def test_balanced_transaction_is_accepted(db, accounts):
    tx = spend(db, accounts, 5000)
    assert len(tx.postings) == 2
    assert sum(p.amount_minor for p in tx.postings) == 0


def test_unbalanced_transaction_is_rejected_with_the_imbalance(db, accounts):
    with pytest.raises(LedgerError, match="do not balance"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Bad",
            postings=[
                PostingInput(accounts["expenses:food"].id, 5000),
                PostingInput(accounts["assets:checking"].id, -4000),
            ],
        )


def test_single_posting_is_rejected(db, accounts):
    with pytest.raises(LedgerError, match="at least 2"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Half",
            postings=[PostingInput(accounts["expenses:food"].id, 5000)],
        )


def test_zero_posting_is_rejected(db, accounts):
    with pytest.raises(LedgerError, match="zero"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Nothing",
            postings=[
                PostingInput(accounts["expenses:food"].id, 0),
                PostingInput(accounts["assets:checking"].id, 0),
            ],
        )


def test_each_currency_must_balance_independently(db, accounts):
    """A EUR leg cannot be netted against a USD leg to fake a zero sum."""
    with pytest.raises(LedgerError, match="do not balance"):
        ledger.validate_postings(
            [
                PostingInput(1, 5000, "USD"),
                PostingInput(2, -5000, "EUR"),
            ]
        )


def test_posting_currency_must_match_the_account(db, accounts):
    with pytest.raises(LedgerError, match="is USD"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Wrong currency",
            postings=[
                PostingInput(accounts["expenses:food"].id, 5000, "EUR"),
                PostingInput(accounts["assets:checking"].id, -5000, "EUR"),
            ],
        )


def test_unknown_account_is_rejected(db, accounts):
    with pytest.raises(LedgerError, match="unknown account"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Ghost",
            postings=[
                PostingInput(9999, 5000),
                PostingInput(accounts["assets:checking"].id, -5000),
            ],
        )


def test_archived_accounts_reject_new_postings(db, accounts):
    ledger.archive_account(db, accounts["expenses:food"])
    with pytest.raises(LedgerError, match="archived"):
        spend(db, accounts, 100)


def test_duplicate_external_id_is_rejected(db, accounts):
    ledger.create_transaction(
        db,
        tx_date=date(2026, 1, 1),
        description="Import",
        external_id="bank-1",
        postings=[
            PostingInput(accounts["expenses:food"].id, 100),
            PostingInput(accounts["assets:checking"].id, -100),
        ],
    )
    with pytest.raises(LedgerError, match="already exists"):
        ledger.create_transaction(
            db,
            tx_date=date(2026, 1, 1),
            description="Import again",
            external_id="bank-1",
            postings=[
                PostingInput(accounts["expenses:food"].id, 100),
                PostingInput(accounts["assets:checking"].id, -100),
            ],
        )


def test_balances_and_as_of(db, accounts):
    spend(db, accounts, 5000, day=date(2026, 1, 10))
    spend(db, accounts, 2500, day=date(2026, 2, 10))

    checking = accounts["assets:checking"].id
    assert ledger.account_balance(db, checking) == -7500
    assert ledger.account_balance(db, checking, as_of=date(2026, 1, 31)) == -5000
    assert ledger.account_balance(db, checking, as_of=date(2025, 12, 31)) == 0


def test_natural_balance_flips_credit_normal_types(db, accounts):
    ledger.create_transaction(
        db,
        tx_date=date(2026, 1, 1),
        description="Paycheck",
        postings=[
            PostingInput(accounts["assets:checking"].id, 300000),
            PostingInput(accounts["income:salary"].id, -300000),
        ],
    )
    raw = ledger.account_balance(db, accounts["income:salary"].id)
    assert raw == -300000  # stored as a credit
    assert ledger.natural_balance(AccountType.income, raw) == 300000  # shown as income


def test_subtree_rollup(db, accounts):
    food = accounts["expenses:food"]
    groceries = Account(
        code="expenses:food:groceries",
        name="Groceries",
        type=AccountType.expense,
        parent_id=food.id,
    )
    db.add(groceries)
    db.commit()
    db.refresh(groceries)

    ledger.create_transaction(
        db,
        tx_date=date(2026, 1, 5),
        description="Market",
        postings=[
            PostingInput(groceries.id, 4000),
            PostingInput(accounts["assets:checking"].id, -4000),
        ],
    )
    spend(db, accounts, 1000)  # directly on the parent

    rollup = ledger.rollup_balances(db)
    assert rollup[groceries.id] == 4000
    assert rollup[food.id] == 5000  # own 1000 + child 4000


def test_replace_transaction_swaps_every_leg(db, accounts):
    tx = spend(db, accounts, 5000)
    ledger.replace_transaction(
        db,
        tx,
        tx_date=date(2026, 3, 1),
        description="Corrected",
        postings=[
            PostingInput(accounts["expenses:food"].id, 6000),
            PostingInput(accounts["assets:checking"].id, -6000),
        ],
    )
    assert ledger.account_balance(db, accounts["expenses:food"].id) == 6000
    assert ledger.trial_balance(db) == 0


def test_replace_rejects_an_unbalanced_edit(db, accounts):
    tx = spend(db, accounts, 5000)
    with pytest.raises(LedgerError):
        ledger.replace_transaction(
            db,
            tx,
            tx_date=date(2026, 3, 1),
            description="Broken edit",
            postings=[
                PostingInput(accounts["expenses:food"].id, 6000),
                PostingInput(accounts["assets:checking"].id, -1),
            ],
        )
    db.rollback()
    assert ledger.account_balance(db, accounts["expenses:food"].id) == 5000


def test_delete_cascades_postings_and_keeps_the_books_balanced(db, accounts):
    tx = spend(db, accounts, 5000)
    ledger.delete_transaction(db, tx)
    assert ledger.account_balance(db, accounts["expenses:food"].id) == 0
    assert ledger.trial_balance(db) == 0


def test_account_with_history_cannot_be_deleted(db, accounts):
    spend(db, accounts, 5000)
    with pytest.raises(LedgerError, match="archive it instead"):
        ledger.delete_account(db, accounts["expenses:food"])


def test_unused_account_can_be_deleted(db, accounts):
    ledger.delete_account(db, accounts["assets:savings"])
    assert db.get(Account, accounts["assets:savings"].id) is None


def test_parent_with_children_cannot_be_deleted(db, accounts):
    child = Account(
        code="assets:checking:sub",
        name="Sub",
        type=AccountType.asset,
        parent_id=accounts["assets:checking"].id,
    )
    db.add(child)
    db.commit()
    with pytest.raises(LedgerError, match="child accounts"):
        ledger.delete_account(db, accounts["assets:checking"])


def test_trial_balance_is_zero_after_a_sequence_of_operations(db, accounts):
    """The acceptance criterion: whatever we do, the books stay square."""
    day = date(2026, 1, 1)
    for i in range(30):
        spend(db, accounts, 100 * (i + 1), day=day + timedelta(days=i))
    txs = [spend(db, accounts, 777), spend(db, accounts, 888)]
    ledger.delete_transaction(db, txs[0])
    ledger.replace_transaction(
        db,
        txs[1],
        tx_date=date(2026, 5, 5),
        description="Adjusted",
        postings=[
            PostingInput(accounts["liabilities:card"].id, -2500),
            PostingInput(accounts["expenses:food"].id, 2500),
        ],
    )
    assert ledger.trial_balance(db) == 0


def test_descendant_ids_survives_a_parent_cycle(db, accounts):
    """A bad parent_id write must not hang the rollup."""
    a, b = accounts["assets:checking"], accounts["assets:savings"]
    a.parent_id, b.parent_id = b.id, a.id
    db.commit()
    assert set(ledger.descendant_ids(db, a.id)) == {a.id, b.id}
