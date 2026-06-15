#!/usr/bin/env python3
"""The human-facing copy for the drift scoreboard — one place, plain English.

The scoreboard has two generated surfaces: the per-repo pages
(``scripts/scoreboard_page.py``) and the index (``scripts/seed_scoreboard_index.py``).
Both used to carry their wording inline as f-strings, so a copy edit meant
hunting through render logic in two files. This module pulls every reader-facing
string into one spot — so changing how the scoreboard *reads* never means
touching how it *renders*. Expect to edit this file; that is its job.

Design rules this file keeps:

  * **Plain Feynman English** — short common words, short sentences, one idea at
    a time. The cold reader has never heard of DOS.
  * **The hook leads; the disclaimer follows.** A drift is a message-vs-diff
    mismatch — the visceral, true hook. The ethics line ("never a correctness,
    honesty, or intent grade") is load-bearing and kept verbatim, but it comes
    *after* the reader knows what a drift is, not before.
  * **The numbers are never here.** Counts, SHAs, ranges, dates all come from the
    generated verdict (the JSON). This file holds words and the slots the
    generators fill — never a hard-coded count that could drift from the data.

Stdlib-only, pure (strings in, strings out, no I/O). Nothing under ``src/dos/``
imports it; it is dev tooling, like the two generators that consume it.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The load-bearing ethics line — verbatim, reused everywhere a verdict appears.
# Moving it later in the reading order is allowed; changing its claim is not.
# ---------------------------------------------------------------------------

ETHICS_LINE = (
    "A drift is a message-vs-diff mismatch — **never** a correctness, honesty, "
    "or intent grade."
)

# Where the schema / grade vocabulary is defined (relative link from a per-repo
# page at docs/scoreboard/<org>/<repo>.md depth).
_SCHEMA_LINK = "[docs/311](../../311_scoreboard-per-repo-index-plan.md)"


# ---------------------------------------------------------------------------
# The reusable near-miss block — "what a drift looks like".
#
# Two REAL adjudicated shapes from the aggregate report (3 concrete fix claims on
# empty commits; test claims with net-deleted test lines), de-identified and
# shaped as "says X / did Y" so a cold dev gets it in five seconds. No repo named.
# ---------------------------------------------------------------------------

def drift_explainer() -> str:
    """The 'What a drift looks like' section — markdown, no leading/trailing blank."""
    return "\n".join([
        "## What a \"drift\" looks like",
        "",
        "The commit *message* is written by a person or an agent — it can say "
        "anything. The *diff* is written by git — it can't. A **drift** is when "
        "the two don't match: the message makes a concrete claim the diff "
        "doesn't back up.",
        "",
        "**The empty fix.**",
        "",
        "> **says:** `fix: handle null user in the auth callback`  ",
        "> **did:** touched **0 files**",
        "",
        "The message claims a fix. The commit changed nothing. The claim rests "
        "on the words alone.",
        "",
        "**\"Tests pass\" that deletes the test.**",
        "",
        "> **says:** `test: green after the refactor`  ",
        "> **did:** **deleted** lines from the test file, added none",
        "",
        "The message claims the tests pass. The diff removed test code. Maybe "
        "that was the right call — but the subject says the opposite of what the "
        "diff shows.",
        "",
        "Those are the two clearest shapes. Most real commits aren't drifts at "
        "all — which is the whole point of a clean page.",
    ])


# ---------------------------------------------------------------------------
# The index (docs/scoreboard/README.md) — assembled by seed_scoreboard_index.py.
# ---------------------------------------------------------------------------

def index_hook() -> str:
    """The H1 hook + the one-command call to action. Leads the index."""
    return "\n".join([
        "# AI commit messages can lie. This catches it.",
        "",
        "When Copilot, Cursor, or Claude writes a commit for you, the message "
        "is just text — `fix the login bug`, `tests pass`, `add caching`. "
        "Nothing checks that the change actually did that. The **diff** can't "
        "lie about which files it touched. The **message** can say anything.",
        "",
        "This scoreboard runs one check over public repos built with AI agents: "
        "**does each commit's message match what its diff actually did?** When "
        "it doesn't — an empty commit that says \"fixed it\", a \"tests pass\" "
        "that deletes the test — we call that a **drift**. Each page below is "
        "the receipt.",
        "",
        "### Score your own repo in one command",
        "",
        "```bash",
        "pip install dos-kernel",
        "dos commit-audit --sweep --workspace . BASE..HEAD",
        "```",
        "",
        "That's the same check, on your history. No account, no upload.",
    ])


def index_self_section(self_page: str) -> str:
    """The promoted self-page block — the lede, with a reason to click. The
    auditor grades itself first and publishes whatever it finds."""
    org, name = self_page.split("/", 1)
    return "\n".join([
        "## Start here — the auditor grades itself",
        "",
        "We ran the check on our own repo first and published whatever it said. "
        "It says **non-zero** — a few commits that claim a fix but touched "
        "nothing. They're a deliberate house convention, and the page shows "
        "exactly why. We left them in. A scoreboard that airbrushed its own "
        "page to zero wouldn't be worth reading.",
        "",
        f"- **[{self_page}]({org}/{name}.md)** — our own grade, every flag "
        "explained.",
    ])


def index_clean_section_intro() -> str:
    """The intro line above the list of clean pages — green reframed as earned."""
    return "\n".join([
        "## Repos that came back clean",
        "",
        "Every checkable commit message matched its diff over the audited "
        "range. \"Clean\" here is earned, not empty: each page shows the range, "
        "the count, and receipts you can re-run yourself.",
    ])


def index_clean_empty_placeholder() -> str:
    """Shown under the clean section when the corpus run hasn't published yet."""
    return ("_(none yet — the seed run publishes here once the corpus sweep "
            "runs and the operator publishes to Pages, #98)_")


def index_fine_print(audited: int, withheld: int) -> str:
    """The 'fine print' section — the ethics line, the deeper links, and the
    withheld count kept as a NUMBER (docs/311 §2: a withheld repo is a count,
    never a name). Relocated here, after the hook, so the front door is legible.

    ``audited`` is the number of NAMED pages above (a count we publish);
    ``withheld`` is how many more were checked but not named — the two are
    disjoint, so the sentence reads honestly without double-counting.
    """
    repo_noun = "repo" if audited == 1 else "repos"
    more_noun = "repo was" if withheld == 1 else "repos were"
    L = [
        "## The fine print (it matters)",
        "",
        "**A drift is not an accusation.** It does not mean the code is wrong, "
        "or that anyone lied. It means one thing only: a commit's subject "
        "claimed something its own diff doesn't show. A real fix to the wrong "
        "bug passes the check; an honest doc cleanup with a sloppy subject can "
        "flag. " + ETHICS_LINE,
        "",
        "- **[How it works](methodology.md)** — exactly what the check reads, "
        "what it skips, and every time the check itself was wrong (we narrow "
        "the check, never trust the subject).",
        "- **[The big picture](report-2026-06.md)** — the population drift rate "
        "across public repos, with every flag hand-checked and denominators "
        "everywhere.",
        "- **Want your repo listed?** Clean or not, it's opt-in and you see the "
        "result before it publishes. See the methodology's registration "
        "section.",
        "",
        f"The pages above are the {audited} {repo_noun} we've audited and "
        f"named. Another {withheld} {more_noun} checked but not named — a "
        "non-clean or unadjudicated verdict is reported only as a count, never "
        f"as a named page ({_index_schema_link()} §2).",
    ]
    return "\n".join(L)


def _index_schema_link() -> str:
    """Schema link at index depth (docs/scoreboard/README.md → docs/311)."""
    return "[docs/311](../311_scoreboard-per-repo-index-plan.md)"


INDEX_TAGLINE = "> The kernel is the part that doesn't believe the agents."


# ---------------------------------------------------------------------------
# The per-repo page (scoreboard_page.py) — headlines, verdict blockquote, the
# plain table labels, and the "what a drift would have looked like" block.
# ---------------------------------------------------------------------------

def page_headline(state: str, *, confirmed: int, checkable: int, pct: str,
                  pending: int) -> str:
    """The bold one-line verdict at the very top of a per-repo page.

    state is one of CLEAN / DRIFT / RAW_ONLY (the scoreboard_page.py constants).
    Plain English, the count inline — derived from the verdict, never authored.
    """
    if state == "DRIFT":
        noun = "drift" if confirmed == 1 else "drifts"
        verb = "claims" if confirmed == 1 else "claim"
        return (f"**We found {confirmed} {noun} — {confirmed} of {checkable} "
                f"checkable commit messages {verb} something the diff doesn't "
                f"show ({pct}).**")
    if state == "CLEAN":
        return (f"**Clean — every one of {checkable} checkable commit messages "
                "matched its diff. 0 drifts.**")
    # RAW_ONLY
    flag_noun = "flag" if pending == 1 else "flags"
    return (f"**No grade yet — {pending} {flag_noun} of {checkable} checkable "
            "claims still need a human look before this page can grade.**")


def page_headline_tail() -> str:
    """The sentence under the headline blockquote on every page: what a drift
    is, in plain words, with the ethics line folded in (not a standalone
    disclaimer), plus the schema link."""
    return (
        "A drift is a commit whose subject claims something its own diff "
        "doesn't show — an empty commit that says \"fixed it\", a \"tests "
        "pass\" that deletes the test. " + ETHICS_LINE + " Schema and grade "
        "vocabulary: " + _SCHEMA_LINK + "."
    )


def clean_passed_block() -> str:
    """The 'what a drift would have looked like (this repo had none)' block —
    makes a CLEAN page concrete instead of empty. Markdown, no trailing blank."""
    return "\n".join([
        "### What a drift would have looked like (this repo had none)",
        "",
        "> **would flag:** `fix: handle null user` → touched 0 files  ",
        "> **would flag:** `test: all green` → deleted test lines, added "
        "none",
        "",
        "Neither happened here. Every \"fix / add / remove\" commit touched a "
        "real source file; every \"tests\" commit touched a real test file. "
        "That's what clean means — **not \"nothing happened\", but every "
        "checkable claim backed by the diff.**",
    ])


# Plain-English column labels for the tables, replacing the auditor's
# Witnessed / Unwitnessed (raw) / Abstained vocabulary on the reader-facing page.
VERDICT_TABLE_HEADER = (
    "| Commits | Checkable | Backed by the diff | Drifted (raw) | Skipped "
    "| Raw rate | Final grade |"
)
VERDICT_TABLE_RULE = "|---|---|---|---|---|---|---|"

BY_KIND_HEADER = "| Kind of claim | Backed by the diff | Drifted | Skipped |"
BY_KIND_RULE = "|---|---|---|---|"

# How each claim kind reads to a cold dev (the raw key stays in the JSON / the
# methodology; the page shows the plain label).
KIND_LABELS = {
    "code_effect": "`fix / add / remove` (code)",
    "test": "`tests`",
    "doc": "`docs`",
    "none": "no checkable claim (skipped)",
}


def kind_label(kind: str) -> str:
    """Plain label for a claim kind, falling back to the raw key in backticks."""
    return KIND_LABELS.get(kind, f"`{kind}`")
