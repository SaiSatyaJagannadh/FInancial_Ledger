"""One regression test per security fix; each fails on the behaviour it replaced."""

from __future__ import annotations

import ssl
from pathlib import Path
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from ledger import accounts, auth, interest, notify, store
from ledger.assistant import Draft
from ledger.models import Direction, Entry
from ledger.money import Currency


# ---- 1. assistant drafts are bound to an id, not a list position -------------

def _entry(person: str, minor: int) -> Entry:
    return Entry(date=date(2026, 7, 1), person=person, ledger="Loan",
                 direction=Direction.given, amount_minor=minor,
                 currency=Currency.INR, note="")


def test_saving_one_draft_leaves_the_others_alone(monkeypatch):
    saved = []
    monkeypatch.setattr(store, "append", lambda e, *a, **k: saved.append(e))
    app = AppTest.from_file(str(Path(__file__).parent.parent / "views" / "assistant.py"), default_timeout=30)
    app.session_state.chat = [
        {"role": "assistant", "text": "hi", "drafts": [], "rejected": []},
        {"role": "assistant", "text": "two", "rejected": [], "uid": 7,
         "drafts": [Draft(_entry("Ravi", 100000), {}), Draft(_entry("Vihar", 777700), {})],
         "ids": [70, 71], "gids": []},
    ]
    app.run()
    [b for b in app.button if b.label == "Save"][0].click().run()
    assert [e.person for e in saved] == ["Ravi"]
    assert [d.entry.person for d in app.session_state.chat[1]["drafts"]] == ["Vihar"]
    [b for b in app.button if b.label == "Save"][0].click().run()
    assert [(e.person, e.amount_minor) for e in saved] == [("Ravi", 100000), ("Vihar", 777700)]


# ---- 2. a re-save of a handed-on charge corrects its row, never adds a loan --

CHARGE = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                         amount_minor=15_000_00, kind=interest.Kind.given)


@pytest.fixture
def calls(monkeypatch):
    out = {"append": [], "update": []}
    monkeypatch.setattr(store, "append", lambda e, *a, **k: out["append"].append(e))
    monkeypatch.setattr(store, "update", lambda o, n, *a, **k: out["update"].append((o, n)))
    return out


def test_a_standing_row_on_another_ledger_is_corrected_not_duplicated(calls):
    standing = interest.ledger_entry(CHARGE, "Vihar", ledger="VIHAR")
    interest.sync_ledger_entry([standing], CHARGE, "Vihar", "A-HOME", was_moved_to="Vihar")
    assert not calls["append"] and len(calls["update"]) == 1


def test_changing_the_taker_corrects_the_old_row(calls):
    standing = interest.ledger_entry(CHARGE, "Vihar", ledger="VIHAR")
    interest.sync_ledger_entry([standing], CHARGE, "Amma", "AMMA", was_moved_to="Vihar")
    assert not calls["append"] and calls["update"][0][1].person == "Amma"


def test_a_missing_row_for_a_handed_on_charge_refuses_rather_than_appends(calls):
    with pytest.raises(RuntimeError):
        interest.sync_ledger_entry([], CHARGE, "Vihar", "VIHAR", was_moved_to="Vihar")
    assert not calls["append"]


# ---- 3. present-but-unusable config locks; absent stays open ------------------

def _gate_app(monkeypatch, secrets):
    monkeypatch.setattr(auth, "_secrets", lambda: secrets)
    monkeypatch.setattr(accounts, "enabled", lambda *a, **k: False)

    def script() -> None:
        import streamlit as st

        from ledger import auth as _auth

        _auth.gate()
        st.title("Personal Ledger")

    return AppTest.from_function(script, default_timeout=30).run()


@pytest.mark.parametrize("secrets", [
    {"auth": {"allowed": ["a@x.com"], "client_id": "x"}},     # no cookie_secret
    {"auth": "oops"},                                          # not a table
    {"accounts": "yes"},                                       # not a table
    {"accounts": {"Enabled": True}},                           # wrong case
])
def test_an_unusable_section_stops_the_page(monkeypatch, secrets):
    app = _gate_app(monkeypatch, secrets)
    assert app.error and not app.title


@pytest.mark.parametrize("secrets", [{}, {"auth": {}}, {"accounts": {"enabled": False}}])
def test_an_absent_section_still_means_open(monkeypatch, secrets):
    assert _gate_app(monkeypatch, secrets).title


@pytest.mark.parametrize("blank", ["", [""], [" "], {}])
def test_a_blank_allowed_list_locks(blank):
    secrets = {"auth": {"allowed": blank}}
    assert auth.allowed_emails(secrets) is None
    assert not auth.permitted("stranger@x.com", secrets)


def test_an_explicit_empty_list_or_absent_key_is_still_unrestricted():
    assert auth.allowed_emails({"auth": {"allowed": []}}) == []
    assert auth.allowed_emails({"auth": {}}) == []


# ---- 4. a remembered sign-in is tied to the current password hash ------------

KEY = b"a test key, thirty-two bytes ok!"


def _account(hash_: str) -> accounts.Account:
    return accounts.Account(email="ravi@example.com", name="Ravi",
                            password_hash=hash_, joined=date(2026, 1, 1), row=2)


def _restore(monkeypatch, token, load):
    state: dict = {}

    class Ctx:
        cookies = {auth.COOKIE: token}

    monkeypatch.setattr(auth.st, "session_state", state)
    monkeypatch.setattr(auth.st, "context", Ctx)
    monkeypatch.setattr(auth, "_remember_key", lambda *a, **k: KEY)
    monkeypatch.setattr(accounts, "load", load)
    auth._restore()
    return state


def _token(hash_: str) -> str:
    return auth.make_token("ravi@example.com", "Ravi", KEY,
                           claim=auth.claim_for("ravi@example.com", hash_, KEY))


def test_a_token_with_the_current_hash_restores(monkeypatch):
    state = _restore(monkeypatch, _token("h1"), lambda *a, **k: ([_account("h1")], []))
    assert state[auth.SESSION] == "ravi@example.com"


def test_a_password_change_ends_the_remembered_sign_in(monkeypatch):
    state = _restore(monkeypatch, _token("h1"), lambda *a, **k: ([_account("h2")], []))
    assert auth.SESSION not in state


def test_a_removed_row_ends_the_remembered_sign_in(monkeypatch):
    assert auth.SESSION not in _restore(monkeypatch, _token("h1"), lambda *a, **k: ([], []))


def test_a_token_without_a_claim_is_refused(monkeypatch):
    old = auth.make_token("ravi@example.com", "Ravi", KEY)
    assert auth.SESSION not in _restore(monkeypatch, old,
                                        lambda *a, **k: ([_account("h1")], []))


def test_an_unreadable_users_tab_does_not_sign_anybody_out(monkeypatch):
    down = lambda *a, **k: ([], ["Could not read the users tab: APIError: 503"])  # noqa: E731
    assert _restore(monkeypatch, _token("h1"), down)[auth.SESSION] == "ravi@example.com"

    def boom(*a, **k):
        raise RuntimeError("503")

    assert _restore(monkeypatch, _token("h1"), boom)[auth.SESSION] == "ravi@example.com"


# ---- 5. failed guesses earn a growing wait, process-wide ---------------------

@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(accounts, "_clock", lambda: now[0])
    accounts._FAILS.clear()
    yield now
    accounts._FAILS.clear()


def test_wrong_passwords_earn_a_wait_even_for_the_right_one(monkeypatch, clock):
    account = _account(accounts.hash_password("correct horse", iterations=1000))
    monkeypatch.setattr(accounts, "load", lambda *a, **k: ([account], []))
    for _ in range(accounts.FREE_GUESSES + 1):
        assert accounts.authenticate("Ravi@Example.com ", "wrong password") is None
    assert accounts.authenticate("ravi@example.com", "correct horse") is None
    assert accounts.authenticate("other@example.com", "x" * 8) is None  # others unaffected
    clock[0] += accounts.MAX_WAIT + 1
    assert accounts.authenticate("ravi@example.com", "correct horse") is not None


def test_one_clients_failures_do_not_delay_another_clients_correct_password(monkeypatch, clock):
    account = _account(accounts.hash_password("correct horse", iterations=1000))
    monkeypatch.setattr(accounts, "load", lambda *a, **k: ([account], []))
    for _ in range(accounts.FREE_GUESSES + 1):
        accounts.authenticate("ravi@example.com", "wrong password", client="9.9.9.9")
    assert accounts.authenticate("ravi@example.com", "correct horse", client="9.9.9.9") is None
    assert accounts.authenticate("ravi@example.com", "correct horse", client="1.2.3.4") is not None


def test_a_throttled_attempt_costs_no_hash_and_does_not_extend_the_wait(monkeypatch, clock):
    account = _account(accounts.hash_password("correct horse", iterations=1000))
    monkeypatch.setattr(accounts, "load", lambda *a, **k: ([account], []))
    for _ in range(accounts.FREE_GUESSES + 1):
        accounts.authenticate("ravi@example.com", "wrong password")
    hashed = []
    monkeypatch.setattr(accounts, "verify", lambda *a: hashed.append(a) or False)
    for _ in range(20):
        accounts.authenticate("ravi@example.com", "wrong password")
    assert not hashed


def test_the_signup_word_is_budgeted_once_and_compared_on_bytes(clock):
    assert accounts.check_signup_word("wörd", "wörd")           # no TypeError on non-ASCII
    for _ in range(accounts.FREE_GUESSES + 1):
        assert not accounts.check_signup_word("nope", "secret")
    assert not accounts.check_signup_word("secret", "secret")   # waiting
    clock[0] += accounts.MAX_WAIT + 1
    assert accounts.check_signup_word("secret", "secret")


# ---- 6. STARTTLS verifies the server certificate ------------------------------

CONFIG = {"user": "a@x.com", "to": "a@x.com", "host": "smtp.example", "port": 587,
          "password": "pw"}


def test_starttls_is_given_a_verifying_context(monkeypatch):
    seen = {}

    class SMTP:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **kw): seen.update(kw)
        def login(self, *a): ...
        def send_message(self, *a): ...

    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    notify._send("s", "b", CONFIG)
    ctx = seen["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_a_certificate_failure_is_a_failed_send_not_a_failed_save(monkeypatch):
    class SMTP:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **kw): raise ssl.SSLCertVerificationError("bad cert")

    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    notify._send("s", "b", CONFIG)          # must not raise
    assert "SSLCertVerificationError" in notify._LAST_ERROR


# ======================= low-severity fixes ===================================

class _Sheet:
    """Copies gspread's real signatures, including the arguments unused here."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.appended, self.updated, self.deleted = [], [], []
        self.reads = []

    def append_rows(self, values, value_input_option=None, insert_data_option=None,
                    table_range=None, include_values_in_response=False):
        self.appended.append((values, value_input_option))

    def update(self, values=None, range_name=None, raw=True, **kwargs):
        self.updated.append((range_name, values, kwargs.get("value_input_option")))

    def delete_rows(self, start_index, end_index=None):
        self.deleted.append(start_index)

    def row_values(self, row, **kw):
        self.reads.append(row)
        return list(self.rows.get(row, []))


def test_an_unknown_address_costs_one_hash_like_a_known_one(monkeypatch):
    monkeypatch.setattr(accounts, "load", lambda secrets=None: ([], None))
    accounts._FAILS.clear()
    accounts.authenticate("nobody@example.test", "whatever-long")   # warms the decoy
    calls = []
    real = accounts.hashlib.pbkdf2_hmac
    monkeypatch.setattr(accounts.hashlib, "pbkdf2_hmac",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    accounts._FAILS.clear()
    accounts.authenticate("nobody@example.test", "whatever-long")
    assert len(calls) == 1


def test_restoring_does_not_forget_another_record_of_the_same_kind(monkeypatch):
    import json
    from datetime import datetime
    from ledger import archive

    sheet = _Sheet({2: ["2026-01-01", "entry", "", "", "3", json.dumps(["other"])]})
    monkeypatch.setattr(archive, "_sheet", lambda secrets: sheet)
    d = archive.Deletion(datetime(2026, 1, 1), "entry", "", "", 3, ["mine"], row=2)
    with pytest.raises(RuntimeError):
        archive._forget(d, {})
    assert sheet.deleted == []


def test_a_model_cannot_set_the_charge_fields_the_card_hides():
    from ledger.assistant import _to_charges

    raw = [{"date": "2026-07-01", "person": "Ravi", "amount": "100", "currency": "INR",
            "kind": "given", "paid": "100", "moved_to": "Mallory",
            "attachment": "https://attacker.example/r"}]
    charges, _ = _to_charges(raw, ["Ravi"])
    assert charges and not charges[0].moved_to and not charges[0].attachment
    assert charges[0].paid_minor == 0


def test_a_line_separator_in_a_name_is_reported_not_silent():
    notify._send("Ravi x", "body", {"user": "a@b.c", "to": "a@b.c",
                                          "host": "localhost", "port": 1,
                                          "password": "p"})
    assert "ValueError" in notify._LAST_ERROR
    notify._LAST_ERROR = ""


def test_an_enormous_amount_is_a_value_error_not_invalid_operation():
    from ledger.money import to_minor

    with pytest.raises(ValueError):
        to_minor("9" * 30)


def test_regrouping_writes_and_a_first_grouping_does_not_raise(monkeypatch):
    from ledger import people

    sheet = _Sheet({2: ["Chaitu", "Vihar"]})
    monkeypatch.setattr(people, "_sheet", lambda secrets: sheet)
    monkeypatch.setattr(people, "load",
                        lambda secrets: ([people.Member("Chaitu", "Vihar", row=2)], None))
    people.set_parent("Chaitu", "Ravi", {})
    assert sheet.updated and sheet.updated[0][0].startswith("A2")
    monkeypatch.setattr(people, "load", lambda secrets: ([], None))
    people.set_parent("Chaitu", "Ravi", {})
    assert sheet.appended


def test_text_that_looks_like_a_formula_is_stored_as_text():
    sheet = _Sheet()
    store.append_rows(sheet, [["=HYPERLINK(\"http://e\")", "+1+1", "@x", "-5.00", "ok"]])
    store.write_cells(sheet, [["=1+1", "-2"]], "A2:B2", "USER_ENTERED")
    store.append_rows(sheet, [["=raw"]], value_input_option="RAW")
    assert sheet.appended[0][0] == [["'=HYPERLINK(\"http://e\")", "'+1+1", "'@x",
                                     "-5.00", "ok"]]
    assert sheet.updated[0][1] == [["'=1+1", "-2"]]
    assert sheet.appended[1][0] == [["=raw"]]


def test_the_excel_export_does_not_write_a_live_formula():
    import io
    from openpyxl import load_workbook
    from ledger.export import to_excel

    e = Entry(date=date(2026, 7, 1), person="Ravi", ledger="Loan",
              direction=Direction.given, amount_minor=100, currency=Currency.INR,
              note="=HYPERLINK(\"http://e\",\"x\")")
    book = load_workbook(io.BytesIO(to_excel([e])))
    assert all(c.data_type != "f" for ws in book for r in ws.iter_rows() for c in r)


def test_a_shift_during_the_archive_write_stops_the_delete(monkeypatch):
    from ledger import archive

    from dataclasses import replace

    e = replace(_entry("Ravi", 100), row=2)
    sheet = _Sheet()
    reads = iter([e.to_row(), ["2026-07-01", "Someone else", "Loan", "given", "5", "INR"]])
    sheet.row_values = lambda row, **kw: next(reads)
    monkeypatch.setattr(store, "_open_worksheet", lambda secrets, tab=None: sheet)
    monkeypatch.setattr(store, "is_configured", lambda s: True)
    monkeypatch.setattr(archive, "record", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        store.delete(e, {})
    assert sheet.deleted == []


def test_a_delete_confirmation_belongs_to_the_record_not_the_row_number():
    def app():
        from datetime import date
        from ledger import ui
        from ledger.models import Direction, Entry
        from ledger.money import Currency

        ui.delete_control(Entry(date=date(2026, 7, 1), person="Ravi", ledger="L",
                                direction=Direction.given, amount_minor=100,
                                currency=Currency.INR, row=5), "t")

    at = AppTest.from_function(app).run()
    assert not [b for b in at.button if b.key == "yes_t_5"]
    at.session_state["arm_t_5"] = ("a", "different", "record")   # armed for another row's owner
    at.run()
    assert not [b for b in at.button if b.key == "yes_t_5"]
    at.button(key="del_t_5").click().run()
    assert [b for b in at.button if b.key == "yes_t_5"]


def test_a_sheet_scan_is_bounded_however_large_the_file_claims_to_be():
    import io
    from openpyxl import Workbook
    from ledger import docs

    wb = Workbook()
    wb.active["A1"] = "head"
    wb.active.cell(row=docs.MAX_SHEET_ROWS + 500, column=1, value="far")
    buf = io.BytesIO()
    wb.save(buf)
    assert "far" not in docs._sheet_text(buf.getvalue())


def test_the_upload_limit_fits_phone_photos_and_attachments_keep_their_cap():
    import tomllib
    from ledger import attach

    cfg = tomllib.loads(Path(".streamlit/config.toml").read_text())
    assert cfg["server"]["maxUploadSize"] == 10
    assert attach.MAX_BYTES == 2 * 1024 * 1024


def test_a_link_that_could_break_out_of_an_href_is_refused():
    from ledger.ui import safe_href

    assert safe_href("https://ok.example/a?b=1") == "https://ok.example/a?b=1"
    for bad in ('https://e.com/" onmouseover="x', "https://e.com/a b",
                "https://e.com/\nx", "https://e.com/'x", "https://e.com/<x>"):
        assert safe_href(bad) == ""


def test_markdown_in_free_text_cannot_make_an_image_or_link_but_names_read_as_typed():
    from ledger.ui import esc_line, md_plain

    for plain in ("ammu", "Ravi Kumar", "A & B (cash), 50%: ok!", "Rs. 5 - 10"):
        assert md_plain(plain) == plain
    out = md_plain("![x](http://t.example/p.png) [a](http://b)")
    assert "![" not in out.replace("\\[", "") and "\\[" in out
    assert esc_line("a\nb\r\n<c>") == "a b &lt;c&gt;"


def test_the_message_and_call_budget_per_session():
    from ledger.assistant import (MAX_CALLS_PER_SESSION, MAX_MESSAGE_CHARS,
                                  spend_model_call)

    state = {}
    assert spend_model_call(state, "x" * (MAX_MESSAGE_CHARS + 1))
    assert state == {}
    for _ in range(MAX_CALLS_PER_SESSION):
        assert spend_model_call(state, "hi") == ""
    assert spend_model_call(state, "hi")


def _reset_app():
    from ledger import accounts, auth

    auth._reset_form([])


def _drive_reset(monkeypatch, address, known):
    sent = []
    monkeypatch.setattr(accounts, "settings_for_email_exists", lambda: True)
    monkeypatch.setattr(accounts, "signup_code", lambda: "")
    monkeypatch.setattr(accounts, "find",
                        lambda e, _k: accounts.Account(e, "N", "h", date(2026, 1, 1))
                        if known else None)
    monkeypatch.setattr(accounts, "send_reset_code",
                        lambda a, c: sent.append(a.email) or "")
    at = AppTest.from_function(_reset_app).run()
    at.text_input(key="reset_email").input(address)
    at.button[0].click().run()
    return at, sent


def test_reset_gives_the_same_next_screen_for_a_known_and_an_unknown_address(monkeypatch):
    accounts._FAILS.clear()
    known, sent = _drive_reset(monkeypatch, "a@example.test", True)
    unknown, sent2 = _drive_reset(monkeypatch, "b@example.test", False)
    assert sent == ["a@example.test"] and sent2 == []
    assert known.session_state[auth._RESET] and unknown.session_state[auth._RESET]
    assert [c.value for c in known.caption] == [
        c.value.replace("b@", "a@") for c in unknown.caption]
    # and a code nobody holds can never be right
    assert unknown.session_state[auth._RESET]["real"] is False


def test_reset_requests_per_address_are_limited_across_sessions(monkeypatch):
    accounts._FAILS.clear()
    for _ in range(accounts.FREE_GUESSES + 1):
        at, _sent = _drive_reset(monkeypatch, "c@example.test", True)
    at, sent = _drive_reset(monkeypatch, "C@Example.test ", True)
    assert not sent and any("Too many" in e.value for e in at.error)
    accounts._FAILS.clear()


def test_a_send_failure_says_nothing_about_the_cause(monkeypatch):
    accounts._FAILS.clear()
    monkeypatch.setattr(accounts, "settings_for_email_exists", lambda: True)
    monkeypatch.setattr(accounts, "signup_code", lambda: "")
    monkeypatch.setattr(accounts, "find",
                        lambda e, _k: accounts.Account(e, "N", "h", date(2026, 1, 1)))
    monkeypatch.setattr(accounts, "send_reset_code", lambda a, c: "smtp.secret.host refused")
    at = AppTest.from_function(_reset_app).run()
    at.text_input(key="reset_email").input("d@example.test")
    at.button[0].click().run()
    assert "smtp.secret.host" not in " ".join(e.value for e in at.error)
    accounts._FAILS.clear()


def test_person_names_in_markdown_sinks_are_escaped():
    import re
    for f in ("views/edit_entries.py", "views/dashboard.py", "views/clearance.py", "views/interest.py"):
        assert "md_plain" in Path(f).read_text()
    src = Path("views/interest.py").read_text()
    assert not re.search(r'st\.caption\(f"(You owe )?\*\*\{person\}', src)
    assert 'st.caption(f"{charge.person}' not in src


# ---- interest upsert, restore and the entry guard ----------------------------

def _charge(**kw):
    base = dict(date=date(2026, 8, 1), person="Vinay", amount_minor=500000,
                currency=Currency.INR)
    return interest.Charge(**{**base, **kw})


def _tab(monkeypatch, charges, problems=()):
    wrote = []
    monkeypatch.setattr(interest, "load", lambda secrets=None: (list(charges), list(problems)))
    monkeypatch.setattr(interest, "add", lambda c, s=None: wrote.append(("add", c)))
    monkeypatch.setattr(interest, "replace_row", lambda old, new, s=None: wrote.append(("replace", new)))
    return wrote


def test_a_form_save_over_a_handed_on_month_keeps_the_mark_receipt_and_rate(monkeypatch):
    old = _charge(moved_to="Vihar", attachment="att:1", rate_percent=2.0, paid_minor=500000)
    wrote = _tab(monkeypatch, [old])
    interest.set_for_month("Vinay", date(2026, 8, 9), 600000, secrets={"x": 1})
    kind, new = wrote[0]
    assert (kind, new.moved_to, new.attachment, new.rate_percent) == ("replace", "Vihar", "att:1", 2.0)


def test_an_unreadable_tab_writes_nothing(monkeypatch):
    wrote = _tab(monkeypatch, [], ["Could not reach the interest tab. 503"])
    with pytest.raises(RuntimeError):
        interest.set_for_month("Vinay", date(2026, 8, 9), 600000, secrets={"x": 1})
    assert wrote == []


def test_a_saved_zero_paid_reads_back_as_zero_not_in_full():
    row = {"date": "2026-08-01", "person": "Vinay", "amount": "5000.00", "paid": 0,
           "moved_to": "Vihar"}
    assert interest.Charge.from_row(row).paid_minor == 0


def test_restoring_a_charge_for_a_month_that_has_one_refuses(monkeypatch):
    from datetime import datetime
    from ledger import archive
    from ledger.models import EntryError

    wrote = _tab(monkeypatch, [_charge()])
    monkeypatch.setattr(archive, "rebuild", lambda d: _charge(amount_minor=100))
    d = archive.Deletion(datetime(2026, 9, 1), archive.INTEREST, "", "", 3, [], row=2)
    with pytest.raises(EntryError, match="Vinay"):
        archive.restore(d, {"x": 1})
    assert wrote == []


def test_the_guard_refuses_a_sibling_row_differing_in_currency_or_note():
    e = Entry(date=date(2026, 7, 1), person="Ravi", ledger="Loan", direction=Direction.given,
              amount_minor=100000, currency=Currency.INR, note="rent")
    row = ["2026-07-01", "Ravi", "Loan", "given", "1000", "INR", "rent"]
    assert store._same_entry(row, e)
    assert not store._same_entry(row[:5] + ["USD", "rent"], e)
    assert not store._same_entry(row[:6] + ["food"], e)


def test_the_guard_accepts_a_numeric_note_sheets_reformatted():
    e = Entry(date=date(2026, 1, 1), person="Ravi", ledger="Ravi", direction=Direction.given,
              amount_minor=100000, currency=Currency.INR, note="2.50")
    row = ["2026-01-01", "Ravi", "Ravi", "given", "1000", "INR", "2.5"]
    assert store._same_entry(row, e)
    assert not store._same_entry(row[:6] + ["2.6"], e)
