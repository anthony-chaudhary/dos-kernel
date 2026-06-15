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
    "A message-vs-diff mismatch is **never** a correctness, honesty, or intent "
    "grade — only a note that a commit's words and its own diff disagree."
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
    """The 'what we check' section — markdown, no leading/trailing blank. (Name
    kept for callers; the reader-facing word "drift" is gone — the precise term
    lives in the methodology.)"""
    return "\n".join([
        "## What \"backed by the diff\" means",
        "",
        "The commit *message* is written by a person or an agent — it can say "
        "anything. The *diff* is written by git — it can't. We check whether a "
        "concrete claim in the message is **backed by** the commit's own diff. "
        "When it isn't, the claim rests on the words alone:",
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
        "Those are the two clearest mismatch shapes. Most real commits aren't "
        "mismatches at all — which is the whole point of a clean page.",
    ])


# ---------------------------------------------------------------------------
# The AI-built SHARE formatter — one helper, used by every render site.
#
# `f"{share:.0%}"` rounds anything under 0.5% to the literal "0%". On a board OF
# AI-built repos a "0% AI-built" cell reads as a bug: a repo with real
# AI-authored commits (28/5727 = 0.49%, 39/10000 = 0.39%) showed "0%". This
# helper floors any NONZERO share that would round to "0%" to "<1%" instead, and
# keeps a ≥1% share rendering as a whole percent ("3%") exactly as before. Pure
# and deterministic — the renders stay byte-reproducible under `--check`.
#
# This is the AI-built SHARE only; the BACKED rate (witnessed/checkable) is a
# different, checkable number and is NOT touched here.
# ---------------------------------------------------------------------------

def format_ai_share(attributed: int, scanned: int) -> str:
    """Render an AI-built share as a percent, with a sub-1% floor.

    Returns "—" when `scanned <= 0` (no denominator to divide by). A true zero
    (`attributed == 0`) renders as "0%" — there is genuinely no AI-built share.
    Every NAMED scoreboard repo has `attributed > 0`, so a true zero shouldn't
    occur on a listed page; the sub-1% case is the real one this fixes:

      * 28 / 5727  → 0.49%  → "<1%"  (was "0%")
      * 39 / 10000 → 0.39%  → "<1%"  (was "0%")
      *  3 / 100   → 3.0%   → "3%"   (unchanged)
    """
    if scanned <= 0:
        return "—"
    share = attributed / scanned
    if attributed > 0 and round(share * 100) == 0:
        return "<1%"
    return f"{share:.0%}"


# ---------------------------------------------------------------------------
# The index (docs/scoreboard/README.md) — assembled by seed_scoreboard_index.py.
# ---------------------------------------------------------------------------

def index_hook() -> str:
    """The H1 hook + the one-command call to action. Leads the index.

    The job of these few lines: make a cold reader FEEL the stakes before the
    leaderboard. So it names the concrete failure (a team merges on a message
    its own diff doesn't back), the cost (a false "done" enters the code), and
    the move (check the diff, not the message — free, on your own history). The
    restraint line ("a mismatch is not an accusation") rides up here too, so the
    care is visible on the first screen, not buried in the fine print.
    """
    return "\n".join([
        "# AI commit messages can lie. This catches it.",
        "",
        "More and more code is written by agents that also write their own "
        "commit messages. So the message stops being evidence. `fix the login "
        "bug`, `tests pass`, `add caching` — that is just text the agent typed. "
        "The **diff** is what git actually recorded. The two can disagree.",
        "",
        "**Why it matters:** when a team merges on the message, an empty commit "
        "that says `fix` — or a `tests pass` that *deleted* the test — slips in "
        "quietly. Nobody lied on purpose; the words just ran ahead of the work. "
        "The cost is a false sense of done: the codebase now carries a \"fix\" "
        "that fixed nothing, and no one noticed.",
        "",
        "**The move:** read the thing that can't lie. This check compares each "
        "AI commit's claim against its own diff — never the message alone. A "
        "mismatch is **not** an accusation of lying or bad code; it means only "
        "that the words and the diff disagree, and someone should look. That "
        "restraint is why the result is worth trusting.",
        "",
        "This scoreboard is the proof it works on real, popular repos: for "
        "every AI-authored commit it asks **how much of the repo the AI wrote — "
        "and whether each commit's claim is backed by its own diff.** It graded "
        "our own repo first, and published a non-zero result. Each page below "
        "is the receipt.",
        "",
        "### Score your own repo in one command",
        "",
        "```bash",
        "pip install dos-kernel",
        "dos commit-audit --sweep --workspace . BASE..HEAD",
        "```",
        "",
        "That is the exact same check, on your history — before you trust the "
        "next \"done\". No account, no upload, no one named.",
    ])


def index_aggregate_headline(*, repos: int, scanned: int, attributed: int,
                             checkable: int, witnessed: int) -> str:
    """The one-line ecosystem fact at the top of the index — a punchy
    comparative insight across the published set, computed from the threaded
    per-repo rows. Denominators are kept distinct: the agent SHARE is over
    commits scanned; the backed RATE is over checkable claims. Returns "" when
    there is nothing to summarize (no rows yet)."""
    if repos <= 0:
        return ""
    repo_noun = "repo" if repos == 1 else "repos"
    bits = [f"Across **{repos} {repo_noun}** audited here"]
    if scanned > 0:
        share = format_ai_share(attributed, scanned)
        bits.append(f"AI agents wrote about **{share}** of recent commits")
    if checkable > 0:
        rate = witnessed / checkable
        gap = checkable - witnessed
        if gap == 0:
            bits.append("and **every one** of the concrete claims those "
                        "commits made was backed by the commit's own diff")
        else:
            bits.append(f"and **{rate:.1%}** of the concrete claims those "
                        "commits made were backed by the commit's own diff "
                        f"(the {gap} exceptions are named on their pages)")
    return " — ".join(bits) + "."


def index_leaderboard(rows: list[dict]) -> str:
    """The comparison table — the heart of the redesigned index. `rows` is the
    pre-sorted list of per-repo stat dicts (over the PUBLISHED set only, never a
    withheld repo): each {full, attributed, scanned, mix (list[(label,count)]),
    checkable, witnessed}. Columns differ per repo, so the table is worth
    reading — unlike a flat list of identical 'clean' links. Returns "" for no
    rows (the caller falls back to the bullet list)."""
    if not rows:
        return ""
    out = [
        "What differs between them is how much of the repo the AI built and "
        "which agents did it — sorted by AI-built share. Click a repo for the "
        "full receipt.",
        "",
        "| Repo | AI-built | Agents | Claims checked | Backed |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        full = r["full"]
        org, name = full.split("/", 1)
        link = f"[{full}]({org}/{name}.md)"
        scanned = int(r.get("scanned", 0) or 0)
        attributed = int(r.get("attributed", 0) or 0)
        share = format_ai_share(attributed, scanned)
        mix = r.get("mix") or []
        mix_cell = " · ".join(f"{label} {count:,}" for label, count in mix[:3])
        if len(mix) > 3:
            mix_cell += " · …"
        mix_cell = mix_cell or "—"
        checkable = int(r.get("checkable", 0) or 0)
        witnessed = int(r.get("witnessed", 0) or 0)
        backed = f"{witnessed / checkable:.0%}" if checkable > 0 else "—"
        out.append(f"| {link} | {share} | {mix_cell} | {checkable:,} "
                   f"| {backed} |")
    return "\n".join(out)


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
    """The intro line above the list of clean pages — green reframed as earned.
    Shown as the section heading; when a leaderboard table is present it sits
    above it, when not it heads the plain bullet list (the empty/seed case)."""
    return "\n".join([
        "## Repos that came back clean",
        "",
        "Every checkable claim an AI commit made matched the commit's own diff "
        "over the audited range. \"Clean\" here is earned, not empty: each page "
        "shows the range, the count, and receipts you can re-run yourself.",
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
        "**A mismatch is not an accusation.** It does not mean the code is "
        "wrong, or that anyone lied. It means one thing only: a commit's "
        "subject claimed something its own diff doesn't show. A real fix to "
        "the wrong bug passes the check; an honest doc cleanup with a sloppy "
        "subject can flag. " + ETHICS_LINE,
        "",
        "- **[How it works](methodology.md)** — exactly what the check reads, "
        "what it skips, and every time the check itself was wrong (we narrow "
        "the check, never trust the subject).",
        "- **[The big picture](report-2026-06.md)** — the population mismatch "
        "rate across public repos, with every flag hand-checked and "
        "denominators everywhere.",
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

def page_h1(repo: str) -> str:
    """The page title. Leads with the question a real person asks — how much of
    THIS repo did AI build — not the internal metric name."""
    return f"# How AI built {repo}"


def page_headline(state: str, *, confirmed: int, checkable: int, pct: str,
                  pending: int) -> str:
    """The bold one-line verdict at the very top of a per-repo page.

    state is one of CLEAN / DRIFT / RAW_ONLY (the scoreboard_page.py constants).
    Plain English, derived from the verdict, never authored. The reader-facing
    word "drift" is gone (it confused readers — it lives on only in the
    methodology as the precise term); the honesty fact is stated in plain
    words: did every claim an AI commit made check out against its own diff.
    """
    if state == "DRIFT":
        n = confirmed
        noun = "commit" if n == 1 else "commits"
        return (f"**{n} of {checkable} AI commit {('message' if n == 1 else 'messages')} "
                f"here claimed work the commit's own diff doesn't show ({pct}). "
                "The rest checked out.**")
    if state == "CLEAN":
        return (f"**Every one of the {checkable} AI commits here that made a "
                "checkable claim did what it said — each claim backed by the "
                "commit's own diff.**")
    # RAW_ONLY
    flag_noun = "commit" if pending == 1 else "commits"
    return (f"**Not graded yet — {pending} AI {flag_noun} of {checkable} "
            "checkable claims still need a human look before this page can "
            "grade.**")


def page_headline_tail() -> str:
    """The sentence under the headline blockquote on every page: what the check
    is, in plain words, with the ethics line folded in (not a standalone
    disclaimer), plus the schema link. No jargon — the precise term lives in
    the linked methodology, not here."""
    return (
        "The check: an AI agent's commit message is just text it wrote — the "
        "diff is what git recorded. This page reports, for the AI-authored "
        "commits, whether each concrete claim in a message (\"fix X\", \"add "
        "tests for Y\") is backed by that commit's own diff. " + ETHICS_LINE
        + " Schema and the precise definition: " + _SCHEMA_LINK + "."
    )


# ---------------------------------------------------------------------------
# The "How AI built this" section — the new lede that makes each page DIFFER:
# the agent-authored share, the agent mix, and the claims-backed rate, all in
# plain words. Driven by the optional attribution facts the corpus sweep
# carries (markers / attributed_commits / commits_scanned); omitted when the
# input is a bare summary (the self page) that has no agent data.
# ---------------------------------------------------------------------------

def agent_share_sentence(*, attributed: int, scanned: int) -> str:
    """How much of the recent history is agent-authored, in plain words."""
    if scanned <= 0:
        return ""
    pct = format_ai_share(attributed, scanned)
    commits = "commit" if attributed == 1 else "commits"
    return (f"AI agents wrote **{pct}** of the last {scanned:,} commits "
            f"here — {attributed:,} agent-authored {commits}.")


def agent_mix_sentence(mix: list[tuple[str, int]]) -> str:
    """Which agents, biggest first. `mix` is a list of (label, count) the
    renderer sorts deterministically (count desc, then name) before passing in.
    """
    if not mix:
        return ""
    parts = [f"{label} {count:,}" for label, count in mix]
    if len(parts) == 1:
        return f"The agent behind them: **{parts[0]}**."
    return "The agents behind them: **" + " · ".join(parts) + "**."


def claims_backed_sentence(*, witnessed: int, checkable: int,
                           unwitnessed: int) -> str:
    """The truthfulness fact, in one plain sentence — no 'drift'."""
    if checkable <= 0:
        return ("None of the audited commits made a concrete, checkable "
                "claim, so there is nothing to back-check here.")
    if unwitnessed == 0:
        return (f"Of those, **{checkable:,}** made a concrete claim you can "
                f"check (\"fix X\", \"add tests for Y\") — and **every one** "
                "was backed by the commit's own diff. No commit claimed work "
                "its diff doesn't show.")
    pct = witnessed / checkable
    n = unwitnessed
    msg = "message" if n == 1 else "messages"
    return (f"Of those, **{checkable:,}** made a concrete claim you can check; "
            f"**{witnessed:,}** ({pct:.0%}) were backed by the commit's own "
            f"diff. **{n}** {msg} claimed work the diff doesn't show — listed "
            "in the receipts below.")


def how_ai_built_heading() -> str:
    return "## How AI built this"


def clean_passed_block() -> str:
    """The 'what an over-claim would look like (this repo had none)' block —
    makes a clean page concrete instead of empty. Markdown, no trailing blank.
    No jargon: the shapes are shown, the precise term stays in the methodology.
    """
    return "\n".join([
        "### What a mismatch would have looked like (this repo had none)",
        "",
        "> **would flag:** `fix: handle null user` → touched 0 files  ",
        "> **would flag:** `test: all green` → deleted test lines, added "
        "none",
        "",
        "Neither happened here. Every \"fix / add / remove\" commit touched a "
        "real source file; every \"tests\" commit touched a real test file. "
        "That's what a clean page means — **not \"nothing happened\", but "
        "every checkable claim backed by the diff.**",
    ])


# Plain-English column labels for the tables, replacing the auditor's
# Witnessed / Unwitnessed / Abstained vocabulary on the reader-facing page.
# "Drifted" is gone — "Claimed, not shown" says the same thing in plain words.
VERDICT_TABLE_HEADER = (
    "| Commits | Checkable | Backed by the diff | Claimed, not shown (raw) "
    "| Skipped | Raw rate | Final grade |"
)
VERDICT_TABLE_RULE = "|---|---|---|---|---|---|---|"

BY_KIND_HEADER = (
    "| Kind of claim | Backed by the diff | Claimed, not shown | Skipped |"
)
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
