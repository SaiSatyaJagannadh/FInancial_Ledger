"""An edit dialog must start from the record it opened, never from old typing.

Closing a dialog with its X does not rerun the app, so its inputs keep their
state. An input without a per-opening key then showed the previous record's
typing wherever two records agreed (an empty note, the same date), and Save
wrote it into the wrong row. Seen on the spending page.
"""

import ast
import pathlib

from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INPUTS = {"text_input", "text_area", "date_input", "number_input", "radio",
          "checkbox", "toggle", "selectbox", "multiselect", "file_uploader"}
DIALOGS = {
    "ledger/ui.py": {"_edit_dialog", "_edit_transaction"},
    "views/interest.py": {"_edit"},
    "views/clearance.py": {"edit_dialog"},
}


def test_every_input_in_an_edit_dialog_is_keyed_to_its_opening():
    seen = 0
    for path, names in DIALOGS.items():
        tree = ast.parse((ROOT / path).read_text())
        for fn in ast.walk(tree):
            if not (isinstance(fn, ast.FunctionDef) and fn.name in names):
                continue
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in INPUTS):
                    continue
                seen += 1
                key = next((k.value for k in call.keywords if k.arg == "key"), None)
                assert isinstance(key, ast.Call) and getattr(key.func, "id", "") == "fk", (
                    f"{path}:{call.lineno} in {fn.name} needs key=fk(...)")
    assert seen >= 25, seen   # a renamed dialog must not make this pass vacuously


def _app():
    import streamlit as st

    from ledger.ui import fk, open_edit

    def body(note):
        st.text_input("Note", value=note, key=fk("n"))

    note = st.session_state.get("record_note", "")
    if st.session_state.get("clicked_edit"):
        st.session_state["clicked_edit"] = False
        open_edit(body, note)
    else:
        body(note)   # typing in an open dialog reruns only the dialog


def test_a_second_record_does_not_show_the_firsts_typing():
    app = AppTest.from_function(_app, default_timeout=30)
    app.session_state["clicked_edit"] = True
    app.run()
    app.text_input[0].input("typed for row 5").run()
    assert app.text_input[0].value == "typed for row 5", "typing holds while open"

    # Closed with the X (no rerun), then Edit on row 9, whose note is also empty.
    app.session_state["clicked_edit"] = True
    app.run()
    assert app.text_input[0].value == ""
