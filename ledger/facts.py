"""Answer the common ledger questions in code, without asking a model.

Most of what anyone asks a lending ledger is arithmetic: what does Ravi owe
me, who owes the most, how much have I lent altogether. Sending those to an
LLM costs one to three seconds and hands the sum to something that can be
confidently wrong about it. The figures already exist in `compute.py`, so the
answer can be exact and instant instead.

**There are no vectors here, and that is deliberate.** Retrieval exists to
find the relevant part of a corpus too large to hold in context. This corpus
is a few dozen rows — the whole ledger renders to roughly 700 tokens against a
128,000-token window, so there is nothing to retrieve *from*. Embedding it
would add a network round trip before the model call and then ask the model to
add up the rows it got back, which is precisely the arithmetic this module
exists to keep out of a model's hands.

The contract is `answer() -> str | None`, and **`None` is the important half**.
A wrong instant answer is worse than a slow right one, so anything this cannot
recognise with confidence falls through to the assistant untouched.
"""

from __future__ import annotations

import re
from datetime import date

from ledger.compute import by_person, ledger_breakdown, totals
from ledger.models import Direction
from ledger.money import Currency, format_money, spoken

#: Marks an answer as arithmetic rather than generation, so the page can say so.
COMPUTED = "computed"


def _money(minor: int, currency: Currency) -> str:
    """A figure, with its lakh/crore reading when it has one.

    ₹1,88,900.00 and "1.89 lakh" are the same number, but only one of them
    survives being read aloud, and only the other can be checked against a
    receipt. Both, then.
    """
    short = spoken(abs(minor), currency)
    return format_money(minor, currency) + (f" ({short})" if short else "")


def _normalise(question: str) -> str:
    """Lowercase, no punctuation, single spaces — what the matchers see."""
    return re.sub(r"[^a-z0-9 ]+", " ", str(question or "").lower()).strip()


def _has(text: str, *words: str) -> bool:
    """True when every word appears as a whole word.

    Whole words, not substrings: "owe" inside "owner" matched a question about
    nothing of the sort.
    """
    return all(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _any(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


#: Words that can follow "does"/"about" without naming anybody.
_NOT_A_NAME = frozenset("""
i me my mine we us our you your it its that this these those there they them
their he him his she her hers everyone everybody anyone anybody someone somebody
the a an any all still left open total net much many more most any something
anything nothing what who whom whose when where how why
""".split())

#: "how much does <someone> owe me" is a question about a person even when we
#: have never heard of them.
_DIRECTED = re.compile(r"\b(?:does|do|did|is|about|for|from|with)\s+(\w+)")


def _names_a_person(text: str) -> bool:
    """Is this question pointed at somebody in particular?

    Needed because "how much does Kavita owe me" also contains "how much", and
    was being answered with the grand total — a real figure, but not an answer
    to the question asked. If a question names somebody and that somebody
    cannot be resolved, nothing here should answer it.
    """
    return any(word not in _NOT_A_NAME for word in _DIRECTED.findall(text))


def _currencies(entries: list) -> list[Currency]:
    """The currencies actually present, in enum order. Never mixed."""
    found = {e.currency for e in entries}
    return [c for c in Currency if c in found]


def _person_in(question: str, entries: list) -> str | None:
    """The one known person this question names, or None.

    None covers both "nobody is named" and "more than one is", because
    answering about the wrong person is worse than not answering. Matching is
    on whole words so "Ram" does not pick out "Ramesh"; `canonical()` handles
    the genuine prefix case ("ravi" -> "Ravi (friend)") afterwards.
    """
    from ledger.assistant import canonical

    people = sorted({e.person for e in entries})
    text = _normalise(question)

    hits = [p for p in people if _has(text, *_normalise(p).split()) and _normalise(p)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None  # ambiguous: two known people named in one question

    # A first name or an abbreviation: let canonical() decide, and only accept
    # an unambiguous snap onto exactly one existing person.
    for word in text.split():
        if len(word) < 3:
            continue
        snapped = canonical(word, people)
        if snapped in people:
            return snapped
    return None


# --------------------------------------------------------------- the shapes

def _person_balance(text: str, entries: list) -> str | None:
    """What one person owes, and the given − received behind it."""
    who = _person_in(text, entries)
    if not who:
        return None
    asked = _any(text, "owe", "owes", "owed", "balance", "much", "about", "status")
    if not asked:
        return None

    lines: list[str] = []
    for currency in _currencies(entries):
        mine = [e for e in entries if e.currency is currency and e.person == who]
        if not mine:
            continue
        summary = next((s for s in by_person(mine, currency) if s.person == who), None)
        if summary is None:
            continue
        net = summary.net_minor
        verb = "owes you" if net > 0 else ("you owe" if net < 0 else "is settled with you")
        figure = _money(abs(net), currency) if net else format_money(0, currency)
        lines.append(
            f"**{who}** {verb} **{figure}**  \n"
            f"given {format_money(summary.given_minor, currency)} − received "
            f"{format_money(summary.received_minor, currency)}, across "
            f"{summary.ledgers} ledger{'s' if summary.ledgers != 1 else ''} "
            f"({summary.open_ledgers} still open), last activity "
            f"{summary.last_activity:%d %b %Y}."
        )
    return "\n\n".join(lines) or None


def _ranking(text: str, entries: list) -> str | None:
    """Who owes what, most first."""
    if not _any(text, "who", "everyone", "everybody"):
        return None
    if not _any(text, "owe", "owes", "owed", "most", "outstanding", "pending"):
        return None

    # Each currency is its own block, separated by a blank line: a bold line
    # placed directly under a numbered list gets swallowed into the list.
    blocks: list[str] = []
    for currency in _currencies(entries):
        mine = [e for e in entries if e.currency is currency]
        rows = [s for s in by_person(mine, currency) if s.net_minor != 0]
        if not rows:
            continue
        lines = [f"**{currency.label}**", ""]
        for place, summary in enumerate(rows, start=1):
            net = summary.net_minor
            side = "owes you" if net > 0 else "you owe"
            lines.append(f"{place}. **{summary.person}** — {side} {_money(abs(net), currency)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or None


def _totals(text: str, entries: list) -> str | None:
    """The headline figures, the same ones the dashboard shows."""
    wants_total = _any(text, "total", "altogether", "overall", "sum", "net")
    wants_lent = _has(text, "how", "much") or _any(text, "lent", "given", "outstanding", "owed")
    if not (wants_total or wants_lent):
        return None
    if _person_in(text, entries):
        return None  # a question about one person, not the whole book

    lines: list[str] = []
    for currency in _currencies(entries):
        overall = totals([e for e in entries if e.currency is currency], currency)
        lines.append(
            f"**{currency.label}** — still owed to you: "
            f"**{_money(overall.net_minor, currency)}**  \n"
            f"given {format_money(overall.given_minor, currency)} − received "
            f"{format_money(overall.received_minor, currency)}, across "
            f"{overall.records} entries and {overall.people} "
            f"{'person' if overall.people == 1 else 'people'} "
            f"({overall.open_ledgers} of {overall.ledgers} ledgers still open)."
        )
    return "\n\n".join(lines) or None


def _counts(text: str, entries: list) -> str | None:
    """How many of a thing there are."""
    if not _has(text, "how", "many"):
        return None
    lines: list[str] = []
    for currency in _currencies(entries):
        overall = totals([e for e in entries if e.currency is currency], currency)
        if _any(text, "person", "people", "one", "ones"):
            lines.append(f"**{currency.label}**: {overall.people} people.")
        elif _any(text, "ledger", "ledgers", "arrangement", "arrangements"):
            lines.append(
                f"**{currency.label}**: {overall.ledgers} ledgers, "
                f"{overall.open_ledgers} still open."
            )
        else:
            lines.append(f"**{currency.label}**: {overall.records} entries.")
    return "\n\n".join(lines) or None


def _last_activity(text: str, entries: list) -> str | None:
    """When a person last appeared, and what that entry was."""
    if not _any(text, "last", "recent", "latest", "when"):
        return None
    who = _person_in(text, entries)
    if not who:
        return None

    mine = [e for e in entries if e.person == who]
    if not mine:
        return None
    latest = max(mine, key=lambda e: (e.date, e.row or 0))
    verb = "gave them" if latest.direction is Direction.given else "got back"
    return (
        f"The last entry for **{who}** is **{latest.date:%d %b %Y}** — you {verb} "
        f"**{_money(latest.amount_minor, latest.currency)}** on *{latest.ledger}*"
        + (f", noted “{latest.note}”." if latest.note else ".")
    )


def _largest(text: str, entries: list) -> str | None:
    """The single biggest movement."""
    if not _any(text, "biggest", "largest", "highest", "max", "maximum"):
        return None
    lines: list[str] = []
    for currency in _currencies(entries):
        mine = [e for e in entries if e.currency is currency]
        if not mine:
            continue
        top = max(mine, key=lambda e: e.amount_minor)
        verb = "gave" if top.direction is Direction.given else "got back"
        lines.append(
            f"**{currency.label}** — the largest single entry is "
            f"**{_money(top.amount_minor, currency)}**: you {verb} it "
            f"{'to' if top.direction is Direction.given else 'from'} "
            f"**{top.person}** on *{top.ledger}*, {top.date:%d %b %Y}."
        )
    return "\n\n".join(lines) or None


def _open_ledgers(text: str, entries: list) -> str | None:
    """Which arrangements have not been settled."""
    if not _any(text, "open", "unsettled", "outstanding", "pending"):
        return None
    if not _any(text, "ledger", "ledgers", "arrangement", "arrangements"):
        return None

    rows = [r for r in ledger_breakdown(entries) if r["open"]]
    if not rows:
        return "Every ledger is settled — nothing is outstanding."
    rows.sort(key=lambda r: -abs(r["net_minor"]))
    lines = [f"{len(rows)} ledger{'s' if len(rows) != 1 else ''} still open:"]
    for row in rows:
        net = row["net_minor"]
        side = "owes you" if net > 0 else "you owe"
        lines.append(
            f"- **{row['person']}** · *{row['ledger']}* — {side} "
            f"{_money(abs(net), row['currency'])}"
        )
    return "\n".join(lines)


#: Order matters: the narrower shapes get first refusal, so "how much does
#: Ravi owe me" is read as a question about Ravi rather than as a grand total.
_SHAPES = (
    _person_balance,
    _last_activity,
    _ranking,
    _open_ledgers,
    _largest,
    _counts,
    _totals,
)


def answer(question: str, entries: list, *, today: date | None = None) -> str | None:
    """An exact answer computed from the entries, or None to let the model try.

    Returning None is not a failure — it is this module declining to guess, and
    the assistant carrying on to the model exactly as it did before.
    """
    text = _normalise(question)
    if not text:
        return None
    if not entries:
        return "The ledger is empty — nothing has been recorded yet."

    # A question pointed at somebody we cannot identify — an unknown name, or
    # two known ones at once — gets no answer at all. Falling back to the whole
    # book would return a real figure that answers a different question, which
    # is the most convincing way to be wrong.
    if _names_a_person(text) and not _person_in(text, entries):
        return None

    for shape in _SHAPES:
        try:
            found = shape(text, entries)
        except Exception:  # noqa: BLE001 — a shape that trips is a shape that abstains
            continue
        if found:
            return found
    return None


def demo() -> None:
    """Self-check: the shapes answer, and — more importantly — know when not to."""
    from ledger.demo import build_demo_entries

    entries = build_demo_entries()
    people = sorted({e.person for e in entries})
    assert people, "demo data should have people in it"
    someone = people[0]

    # Every shape produces something.
    for question in (
        f"how much does {someone} owe me",
        "who owes me the most",
        "what is the total i have given",
        "how many entries are there",
        f"when did i last give {someone}",
        "what is the biggest entry",
        "which ledgers are still open",
    ):
        assert answer(question, entries), f"no answer for {question!r}"

    # And the refusals, which matter more than the answers.
    for question in (
        "what do you think of my spending habits",
        "should i lend him more money",
        "",
        "hello",
    ):
        assert answer(question, entries) is None, f"should not have answered {question!r}"

    # An empty ledger is a sentence, not a crash and not a None.
    assert "empty" in answer("who owes me", []).lower()

    # A figure that is checkable: the working is shown beside the total.
    reply = answer(f"how much does {someone} owe me", entries)
    assert "given" in reply and "received" in reply, "the arithmetic must be visible"

    # Whole-word matching: "owe" must not be found inside another word.
    assert not _has("who is the owner", "owe")

    # No currency is ever added to another.
    from ledger.money import Currency as _C
    both = [e for e in entries if e.currency is _C.INR][:2]
    both += [e for e in entries if e.currency is _C.USD][:2]
    if len({e.currency for e in both}) == 2:
        totals_reply = answer("what is the total", both)
        assert totals_reply and _C.INR.label in totals_reply and _C.USD.label in totals_reply

    print("ledger.facts: all checks passed")


if __name__ == "__main__":
    demo()
