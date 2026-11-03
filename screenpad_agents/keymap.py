"""The per-harness keymap of the blocked card (Tier 0 approvals).

A harness is known by the kind Herdr reports for its agent ("claude" for
Claude Code). Its entry says how to recognise its permission dialog in the
lines the card draws (its question, then its options right under it), which
answers that dialog offers, the keys that choose each, and the keys that
interrupt it. An answer is offered only when the dialog shows its option, so "Always" never maps to a key that means
something else in a dialog without that option. A kind not in HARNESSES, or a
dialog that is not a permission dialog (Claude Code's folder-trust,
external-imports and bypass-permissions dialogs above all, which must never be
answered with a key), gets the card read-only, with no answer buttons.

The Claude Code keys: its interrupt is a single Escape (firstmate's
harness-adapter reference); its permission dialog numbers its options
"1. Yes", "2. Yes, and don't ask again ..." and "3. No, and tell Claude what
to do differently (esc)", and a digit chooses that option. Deny is Escape,
which cancels the dialog whatever its option count.
"""

import re

ONCE, ALWAYS, DENY = "once", "always", "deny"
ANSWERS = (ONCE, ALWAYS, DENY)


class Harness:
    """name: shown on the card; question: the line that asks (the last line
    matching it is the dialog's); refuse: a pattern that makes the screen a
    dialog never to answer; answers: {answer: (keys, option pattern, where)}
    with where the option's line after the question (0 the first), or None
    for any line after it; interrupt: the keys that interrupt the agent."""

    __slots__ = ("answers", "interrupt", "name", "question", "refuse")

    def __init__(self, name, question, refuse, answers, interrupt):
        self.name, self.question, self.refuse = name, question, refuse
        self.answers, self.interrupt = answers, interrupt


HARNESSES = {
    "claude": Harness(
        "Claude Code",
        question=re.compile(r"Do you want to .+\?"),
        refuse=re.compile(r"trust this (folder|project)|external CLAUDE\.md|Bypass Permissions", re.IGNORECASE),
        answers={
            ONCE: (("1",), re.compile(r"([>\u276f]\s*)?1\.\s*Yes"), 0),
            ALWAYS: (("2",), re.compile(r"([>\u276f]\s*)?2\.\s*Yes\b.*"), 1),
            DENY: (("esc",), re.compile(r"([>\u276f]\s*)?\d\.\s*No\b.*"), None),
        },
        interrupt=("esc",),
    ),
}


def harness(kind):
    return HARNESSES.get(kind)


def offered(kind, lines):
    """{answer: keys} for the answers the dialog in lines offers; empty when
    the harness is unknown or lines are not a permission dialog of it. The
    dialog is the last question line and the options right under it: Allow
    once must be the first option, exactly "1. Yes", so a screen whose
    question is only in earlier text, or another dialog with numbered
    choices, is never answered."""
    h = HARNESSES.get(kind)
    if h is None or any(h.refuse.search(line) for line in lines):
        return {}
    asks = [i for i, line in enumerate(lines) if h.question.fullmatch(line)]
    if not asks:
        return {}
    after = lines[asks[-1] + 1 :]
    out = {}
    for answer, (keys, option, where) in h.answers.items():
        if where is None:
            found = any(option.fullmatch(line) for line in after)
        else:
            found = where < len(after) and option.fullmatch(after[where]) is not None
        if found:
            out[answer] = tuple(keys)
    return out if ONCE in out else {}


def interrupt_keys(kind):
    """The keys that interrupt an agent of this kind, None if unknown."""
    h = HARNESSES.get(kind)
    return tuple(h.interrupt) if h else None
