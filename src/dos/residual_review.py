"""residual_review — the next-generation diff: review the residual, not the diff.

The idea (operator, 2026-06-16). A reviewer today reads every changed line with
roughly equal attention. But for a large fraction of those lines the kernel
already KNOWS the change did the kind of thing its commit claimed — the diff
*witnesses* the claim (`commit-audit`'s `diff-witnessed` rung, docs/214). Reading
those lines for "did they do what they said" is wasted attention: the question is
already answered, by a party that did not author the claim.

So invert the sweep. Instead of folding a range into a single drift RATE
(`dos commit-audit --sweep`), project it back onto the files and partition the
review surface into three bands:

    Band 0  CLEARED   — the claim is `diff-witnessed`. The kernel corroborated the
                        SHAPE of the change against a non-forgeable fact (the file
                        set git itself recorded). ~0 review attention for "did this
                        do what it claims". Still reviewable for correctness — but
                        that is a CHOICE, not the default the diff forces on you.

    Band 1  RESIDUAL  — the claim is `subject-only`, or a tests-pass claim that
                        net-DELETED assertions, or an empty/doc-only code claim, or
                        any `CLAIM_UNWITNESSED`. The kernel could NOT witness the
                        claimed kind of change. This is the 100% — the only place a
                        human's attention buys anything the machine couldn't get.

    Band 2  SEMANTIC  — ADVISORY. A witnessed commit whose touched files land on a
                        risk surface (concurrency, auth, money, crypto, deletion).
                        The SHAPE checked out, but correctness is permanently out of
                        the kernel's scope (docs/214 §3, Wall 3). This band re-adds
                        the semantic side the operator asked for — as a fail-to-
                        ABSTAIN lens that NEVER changes a verdict and NEVER blocks.
                        It only says "human eyes are worth more here than on the
                        average witnessed hunk."

Why this is a "diff" at all, and a next-generation one: a classic diff's unit is
"a line changed". This surface's unit is "a CLAIM the machine couldn't confirm".
Just as an unchanged line can still be part of a bug, a *witnessed* change can
still be wrong — so Band 2 keeps a door open — but the DEFAULT sort of attention
follows where verification ran dry. The definition of "what to look at" became
more flexible than "what bytes changed": it became "what the kernel couldn't
clear."

Soundness: Bands 0 and 1 carry ZERO new trust. They are a pure re-projection of
the shipped `commit_audit` verdict — the same `diff-witnessed` / `subject-only`
rung the reactive tool already computes, sorted by file instead of folded by
rate. Band 2 is the only judgment, and it is advisory and one-sided: it can only
ask for MORE eyes, never fewer, so it cannot hide a real residual.

Layering (CLAUDE.md): this is a KERNEL leaf. `plan_review` is the pure
projection (`classify(evidence, policy)` — no git, no I/O). The git reads
(`audit_range`, the subject/diffstat reads, `build_plan`) live here too but are
boundary I/O the CLI drives, exactly as `commit_audit._git` does. The module
imports only `dos.commit_audit` + stdlib — it names no host, no vendor (the
`RISK_SURFACES` table is a generic default a host overrides via config, the same
host-policy seam the example shipped). `dos review` (the CLI verb) and
`dos_review` (the MCP tool) are thin shells over `build_plan`/`plan_review`;
`examples/residual_review/` re-exports this module, recomputing no rung.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Stand on the REAL kernel verdict — do NOT reimplement the witness rung. The
# whole proof is that the residual is exactly the shipped `commit-audit` output,
# re-projected. If we recomputed the rung here it would be a different (and
# unproven) thing wearing the same name.
from dos.commit_audit import ClaimVerdict, Verdict, Witness, audit_range


# --- Band 2: the advisory semantic lens (fail-to-ABSTAIN) -------------------
#
# A path-pattern → why-it's-risky map. This is deliberately a small, OVERT data
# table, not a model: a witnessed change touching one of these surfaces gets a
# human-eyes flag because the COST of a wrong-but-shaped change there is high,
# not because the kernel found anything wrong (it can't — that's the point).
#
# Adding a pattern can only ever ask for MORE review, so the table is safe to
# grow. It is a host-policy seam: a real consumer would source this from its own
# config. We ship a generic default so the verb runs standalone — it names no
# host, only generic risk vocabulary.
RISK_SURFACES: list[tuple[str, str]] = [
    (r"lease|arbitrat|concurren|lock|mutex|wal\b|atomic", "concurrency / shared-state primitive"),
    (r"auth|login|session|token|credential|permission|rbac|acl", "authentication / authorization"),
    (r"crypto|hash|sign|verify_sig|secret|key\b|nonce", "cryptography / secret handling"),
    (r"payment|billing|charge|price|invoice|refund|money|ledger", "money / billing path"),
    (r"delete|destroy|drop_|truncate|rm_|purge|wipe", "destructive / data-loss operation"),
    (r"migrat|schema|alter_table|backfill", "data migration / schema change"),
    (r"subprocess|os\.system|exec\(|eval\(|shell=true", "process / shell execution"),
]
_RISK_RE = [(re.compile(p, re.IGNORECASE), why) for p, why in RISK_SURFACES]


def _risk_reasons(files: tuple[str, ...]) -> list[str]:
    """Every distinct risk-surface label any of ``files`` lands on (advisory)."""
    hits: list[str] = []
    for f in files:
        for rx, why in _RISK_RE:
            if rx.search(f) and why not in hits:
                hits.append(why)
    return hits


# --- The three-band partition ------------------------------------------------

@dataclass
class ReviewItem:
    """One commit, placed in its review band with the reason it landed there."""

    sha: str
    subject: str
    band: str  # "cleared" | "residual" | (carries an advisory note when semantic)
    witness: str
    claim_kind: str
    verdict: str
    files: list[str]
    reason: str
    semantic_flags: list[str] = field(default_factory=list)


@dataclass
class ReviewPlan:
    """The full review surface for a range — the next-generation diff."""

    rev_range: str
    n_commits: int
    cleared: list[ReviewItem]  # Band 0 — witnessed, ~0 attention
    residual: list[ReviewItem]  # Band 1 — unwitnessed CLAIMS: the human's 100%
    unverifiable: list[ReviewItem]  # Band 1b — no claim to check (ABSTAIN); look, lower
    semantic: list[ReviewItem]  # Band 2 — advisory, a SUBSET of cleared by sha
    # Headline economics: what fraction of CHECKABLE commits the reviewer can
    # de-prioritise because the kernel already cleared their shape.
    checkable: int  # commits that make a checkable claim (exclude ABSTAIN)
    cleared_rate: float  # cleared / checkable — the "skip" fraction
    fields: dict = field(default_factory=dict)


def _all_files(v: ClaimVerdict) -> tuple[str, ...]:
    """Every file the verdict knows the commit touched, across its rungs."""
    seen: list[str] = []
    for grp in (v.source_files, v.test_files, v.ci_files, v.data_files):
        for f in grp:
            if f not in seen:
                seen.append(f)
    return tuple(seen)


def plan_review(verdicts: list[ClaimVerdict], rev_range: str) -> ReviewPlan:
    """Project a list of shipped `ClaimVerdict`s into the three-band review plan.

    Pure: no git, no I/O. Takes the kernel's verdicts and sorts them by where a
    human's attention is worth something. The band rule is one-sided and exact:

    - verdict OK AND a NON-FORGEABLE witness rung -> Band 0 (cleared, ~0 attention).
      That rung is `diff-witnessed` (the diff touches the source the claim refers
      to) OR `data-witnessed` (a lockfile/config/template change that IS the
      claimed effect — the kernel's own ladder, one rung below diff-witnessed,
      docs/214 §1; it is WITNESSED, not unwitnessed). Both rest on the file set
      git recorded, not on the message, so both clear. The cleared item carries
      its rung so the reviewer can choose to look harder at a data-witnessed one.
    - makes a claim the diff did NOT witness     -> Band 1  (residual — the 100%):
      `subject-only` / `CLAIM_UNWITNESSED`. The claim rests on message text alone.
    - ABSTAIN (no claim to check)                -> Band 1b (unverifiable — still
      reviewable, but lower priority than an unwitnessed CLAIM: the commit
      asserted nothing for the kernel to confirm or contradict, so there is no
      claim-vs-diff gap to concentrate a reviewer on).

    The residual/unverifiable split is the load-bearing one: a reviewer's scarce
    attention should land FIRST on a claim the machine could not confirm, not on
    a `chore`/`docs` commit that simply made no claim. Folding both into one bucket
    would dilute exactly the signal the surface exists to concentrate. Likewise a
    `data-witnessed` commit is CLEARED, not residual: the kernel DID witness it
    (on a weaker rung), so dumping it in the must-read pile would overstate the
    residual and contradict "residual = what the kernel could not witness".
    """
    cleared: list[ReviewItem] = []
    residual: list[ReviewItem] = []
    unverifiable: list[ReviewItem] = []
    semantic: list[ReviewItem] = []
    checkable = 0

    for v in verdicts:
        files = list(_all_files(v))
        item = ReviewItem(
            sha=v.sha,
            subject="",  # filled by the caller from git; pure layer leaves blank
            band="",
            witness=v.witness.value,
            claim_kind=v.claim_kind.value,
            verdict=v.verdict.value,
            files=files,
            reason=v.reason,
        )

        is_abstain = v.verdict is Verdict.ABSTAIN
        # CLEARED iff the verdict is OK on a NON-FORGEABLE witness rung. Both
        # diff-witnessed and data-witnessed rest on git's recorded file set (the
        # kernel's ladder, docs/214 §1) — neither is the forgeable `subject-only`
        # rung. The dangerous direction (clearing an unwitnessed claim) is
        # impossible: subject-only never reaches here.
        is_witnessed = (v.verdict is Verdict.OK
                        and v.witness in (Witness.DIFF_WITNESSED,
                                          Witness.DATA_WITNESSED))

        if not is_abstain:
            checkable += 1

        if is_abstain:
            item.band = "unverifiable"
            unverifiable.append(item)
        elif is_witnessed:
            item.band = "cleared"
            cleared.append(item)
            # Band 2 runs ONLY over already-cleared commits: it never rescues a
            # residual item (that would be the dangerous direction) and never
            # demotes one. It is purely "of the things the kernel cleared, which
            # deserve a second human look anyway".
            flags = _risk_reasons(tuple(files))
            if flags:
                sem = ReviewItem(
                    sha=v.sha, subject="", band="semantic",
                    witness=v.witness.value, claim_kind=v.claim_kind.value,
                    verdict=v.verdict.value, files=files, reason=v.reason,
                    semantic_flags=flags,
                )
                semantic.append(sem)
        else:
            # A claim the diff did NOT witness: subject-only, an unwitnessed
            # code/test claim, a tests-pass claim that net-deleted assertions.
            # This is the residual — the only place human attention is the ONLY
            # way to answer "did this do what it said".
            item.band = "residual"
            residual.append(item)

    cleared_rate = (len(cleared) / checkable) if checkable else 0.0
    return ReviewPlan(
        rev_range=rev_range,
        n_commits=len(verdicts),
        cleared=cleared,
        residual=residual,
        unverifiable=unverifiable,
        semantic=semantic,
        checkable=checkable,
        cleared_rate=cleared_rate,
    )


# --- Subjects (a thin git read at the boundary, like commit_audit's) ---------

def _subjects(rev_range: str, root: str, limit: int = 500) -> dict[str, str]:
    """sha -> subject, for labelling. Boundary I/O; empty on any failure."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", root, "log", f"-{int(limit)}",
             "--pretty=format:%H\x1f%s", rev_range],
            capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL,  # evidence reader: never inherit the caller's stdin (docs/295)
            # git emits subjects as UTF-8; the platform default (cp1252 on
            # Windows) would mojibake an international contributor's subject INTO
            # the data, not just the terminal. Mirror commit_audit._git.
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return {}
    if out.returncode != 0:
        return {}
    m: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "\x1f" in line:
            sha, subj = line.split("\x1f", 1)
            m[sha.strip()] = subj
    return m


def build_plan(rev_range: str, root: str = ".") -> ReviewPlan:
    """The full pipeline: kernel verdicts -> three-band plan, with subjects."""
    verdicts = audit_range(rev_range, root=root)
    plan = plan_review(verdicts, rev_range)
    subj = _subjects(rev_range, root)
    # `commit_audit.read_commit` stores the ABBREVIATED sha (`git show`'s `%h`),
    # while `git log --pretty=%H` keys the subject map on the FULL sha. Match by
    # prefix so the two sha widths line up.
    for bucket in (plan.cleared, plan.residual, plan.unverifiable, plan.semantic):
        for it in bucket:
            it.subject = next(
                (s for full, s in subj.items() if full.startswith(it.sha)), "")
    return plan


# --- Navigation (the UI/UX surface) ------------------------------------------
#
# A flat three-band listing answers "where is my attention owed". The next
# question the operator raised is "being able to navigate through that" — so
# `--walk` turns the residual into a sequence of self-contained REVIEW CARDS, one
# per residual commit, each carrying everything needed to adjudicate that single
# unwitnessed claim: the subject, the kernel's reason it could not be witnessed,
# the files, and the diffstat. The reviewer moves through cards instead of
# scrolling a wall — the residual is the navigable unit, the same way a diff made
# "the changed line" the navigable unit. This is a STATIC render of the cards
# (every TUI is downstream of the same data); a host wires it to a pager, an
# editor's quickfix list, or a PR-comment thread.

def _commit_diffstat(sha: str, root: str) -> str:
    """`git show --stat` for one commit — the boundary read for a review card."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", root, "show", "--stat", "--oneline", "--no-color", sha],
            capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL,  # evidence reader: never inherit the caller's stdin (docs/295)
            # UTF-8, like _subjects / commit_audit._git — a diffstat can carry a
            # non-ASCII path or a renamed file's old name.
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return ""
    if out.returncode != 0:
        return ""
    # Drop the leading oneline header (subject already shown on the card).
    lines = out.stdout.splitlines()
    return "\n".join(lines[1:]).strip() if len(lines) > 1 else ""


def render_walk(plan: ReviewPlan, root: str = ".") -> str:
    """Render the residual as a numbered sequence of review cards to step through.

    Only the residual (and, dimmed, the advisory semantic flags) — the cleared
    band is the whole point of NOT showing here. If the residual is empty the walk
    is one line: there is nothing the kernel couldn't clear.
    """
    L: list[str] = []
    n = len(plan.residual)
    pct = round(plan.cleared_rate * 100)
    L.append(f"# walk the residual  —  {plan.rev_range}")
    L.append(f"#   {n} card{'s' if n != 1 else ''} to review "
             f"(kernel cleared {pct}% of {plan.checkable} checkable claims)")
    L.append("")
    if not plan.residual:
        L.append("✓ residual is empty — every checkable claim was diff-witnessed.")
        L.append("  Nothing here needs the 'did it do what it said' pass.")
        return "\n".join(L)

    sem_by_sha = {i.sha: i.semantic_flags for i in plan.semantic}
    for idx, it in enumerate(plan.residual, 1):
        L.append(f"┌─ [{idx}/{n}]  {_short(it.sha)}  ({it.witness})")
        L.append(f"│  {it.subject}")
        L.append(f"│  why residual: {it.reason}")
        if it.files:
            shown = ", ".join(it.files[:6])
            more = f"  (+{len(it.files) - 6} more)" if len(it.files) > 6 else ""
            L.append(f"│  files: {shown}{more}")
        for fl in sem_by_sha.get(it.sha, []):
            L.append(f"│  ⚠ semantic: {fl}")
        stat = _commit_diffstat(it.sha, root)
        if stat:
            for s in stat.splitlines():
                L.append(f"│    {s}")
        L.append("└" + "─" * 50)
        L.append("")
    L.append(f"End of residual: {n} card{'s' if n != 1 else ''}. "
             f"The other {len(plan.cleared)} cleared commit(s) were not shown.")
    return "\n".join(L)


# --- Rendering ----------------------------------------------------------------

def _short(sha: str) -> str:
    return sha[:9]


def render_text(plan: ReviewPlan) -> str:
    L: list[str] = []
    L.append(f"# residual review  —  {plan.rev_range}")
    L.append(f"#   {plan.n_commits} commits, {plan.checkable} make a checkable claim")
    pct = round(plan.cleared_rate * 100)
    L.append(
        f"#   the kernel cleared {len(plan.cleared)}/{plan.checkable} "
        f"({pct}%) of checkable claims — that's the attention you DON'T spend"
    )
    L.append("")

    L.append(f"RESIDUAL — your 100% ({len(plan.residual)})  [a CLAIM the kernel could not witness]")
    if not plan.residual:
        L.append("  (none — every checkable claim was diff-witnessed)")
    for it in plan.residual:
        L.append(f"  {_short(it.sha)}  {it.witness:<14} {it.subject}")
        L.append(f"             └─ {it.reason}")
    L.append("")

    if plan.unverifiable:
        L.append(f"UNVERIFIABLE — no claim to check ({len(plan.unverifiable)})  [look, but lower priority]")
        for it in plan.unverifiable:
            L.append(f"  {_short(it.sha)}  {it.subject}")
        L.append("")

    if plan.semantic:
        L.append(f"SEMANTIC (advisory, witnessed but worth a look) ({len(plan.semantic)})")
        for it in plan.semantic:
            L.append(f"  {_short(it.sha)}  {it.subject}")
            for fl in it.semantic_flags:
                L.append(f"             ⚠ {fl}")
        L.append("")

    L.append(f"CLEARED — ~0 attention ({len(plan.cleared)})  [diff-witnessed; shape confirmed]")
    for it in plan.cleared:
        L.append(f"  {_short(it.sha)}  {it.claim_kind:<8} {it.subject}")
    return "\n".join(L)


def plan_to_dict(plan: ReviewPlan) -> dict:
    """Serialize a ReviewPlan to a JSON-ready dict (the `--json` / MCP shape)."""
    def item(it: ReviewItem) -> dict:
        d = {
            "sha": it.sha, "subject": it.subject, "witness": it.witness,
            "claim_kind": it.claim_kind, "verdict": it.verdict,
            "files": it.files, "reason": it.reason,
        }
        if it.semantic_flags:
            d["semantic_flags"] = it.semantic_flags
        return d

    return {
        "rev_range": plan.rev_range,
        "n_commits": plan.n_commits,
        "checkable": plan.checkable,
        "cleared_rate": round(plan.cleared_rate, 4),
        "residual": [item(i) for i in plan.residual],
        "unverifiable": [item(i) for i in plan.unverifiable],
        "semantic": [item(i) for i in plan.semantic],
        "cleared": [item(i) for i in plan.cleared],
    }
