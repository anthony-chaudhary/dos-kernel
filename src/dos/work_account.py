"""WKA — the work-kind account: *what KINDS of work did this iteration land?*

docs/310 — the **composition sibling** of the loop-economics family. `liveness`
asks "did state move at all?" (a binary); `productivity` asks "is the
work-per-step rate fading?" (a trend); `efficiency` asks "did the tokens buy
work?" (a ratio). None of them asks what the work *was*. The dispatch loop's
stats answer that question with one forced bit — "did a pick ship?" — and the
measured cost of that forcing is in `event_severity.py`'s own numbers: 67% of
dispatch-loop archives shipped 0 picks, and every one of them reads as
"drained" even when the iteration landed commits, reconciled stamps, raised
operator decisions, or caught a false "done" claim. Catching a lie is the
kernel's own product, and the stats graded it zero.

This module is the fix: a typed account of one iteration's work BY KIND,

    work_account.classify_work(WorkAccount) -> WorkVerdict

with each counter reduced at the caller's I/O edge from evidence the judged
worker did not author (the docs/138 invariant, held by every sibling):

    verified_ships   phases the ORACLE confirmed closed (dos verify /
                     dos reconcile VERIFIED) — never the claim
    claimed_ships    phases the iteration SAID it closed — the self-report,
                     carried so the over-claim gap stays visible, never believed
    catches          claims the oracle REFUTED (reconcile QUIET_INCOMPLETE,
                     a commit-audit drift) — the kernel's product, now counted
    advance_commits  commits git recorded on the leased lane that closed no
                     phase (the git_delta evidence liveness reads, folded in)
    grooms           durable plan-state bookkeeping (stamps reconciled,
                     findings closed/added, inbox promotions)
    unblocks         units that flipped HELD/BLOCKED -> OFFERABLE
    surfaced         operator-decision entries raised

⚓ Two axes, two words. The gate verdict (LIVE/DRAIN/...) is a statement about
the BACKLOG; the work account is a statement about THIS ITERATION'S WORK. An
iteration can be DRAIN-and-GROOMED (backlog empty, but it reconciled three
stamps). Today both meanings are forced through "drained"; this leaf gives the
second axis its own vocabulary, and IDLE — every counter zero — is the honest
"nothing witnessed", distinct from "nothing left to dispatch".

⚓ Non-forgeable in the direction that matters (docs/138, the `efficiency`
property verbatim): a worker cannot narrate its way UP the precedence ladder.
SHIPPED needs the oracle's count; ADVANCED needs git's count; CAUGHT needs the
oracle's refutation. The only narration-shaped counter is `claimed_ships`, and
it is deliberately powerless: claims alone classify as IDLE — the one place
this leaf is STRICTER than today's `picks_shipped` headline, which rides the
archive subject's self-report.

⚓ Advisory, and observability-only. `classify_work` REPORTS; it never gates,
takes no lease, stops no loop. The gate verdict and `loop_decide` are
untouched by construction — this leaf changes what the stats SAY, never what
the loop DOES. And like every deterministic verb it grades KIND, never
quality (the Wall-3 line): ADVANCED says bytes moved, CAUGHT says a claim
failed adjudication; neither says the work was right — that stays with the
JUDGE rung.

PURE, no I/O, timeless, names no host — the `productivity` discipline: no
plan, no registry, no journal, no clock. A caller with seven integers gets a
verdict.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields


class WorkKind(str, enum.Enum):
    """The typed dominant-work verdict — six states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `Productivity` / `Efficiency` /
    `GateVerdict`). Ordered most-operator-valuable first; `classify_work`
    names the FIRST kind the account has evidence for.
    """

    SHIPPED = "SHIPPED"      # >=1 ORACLE-verified phase closed
    CAUGHT = "CAUGHT"        # no ship, but >=1 false claim refused — actionable
    ADVANCED = "ADVANCED"    # no ship/catch, but real commits landed on the lane
    GROOMED = "GROOMED"      # bookkeeping only (grooms + unblocks)
    SURFACED = "SURFACED"    # raised operator decisions only
    IDLE = "IDLE"            # every witnessed counter zero — the honest nothing

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class WorkAccount:
    """One iteration's work, accounted by kind — gathered by the CALLER.

    No I/O inside the verdict (the arbiter rule): the caller's boundary reduces
    every signal to a non-negative count at the I/O edge — the oracle's
    verified count, git's commit count, the decisions-queue delta — and hands
    them in frozen. The leaf never re-probes; it only compares.

    Every counter is a count of facts an ENV-authored witness recorded; see the
    module docstring for which witness owns which counter. `claimed_ships` is
    the one exception by design — it is the self-report, carried so the
    over-claim gap (`overclaim`) is visible, and structurally powerless to move
    the verdict.
    """

    verified_ships: int = 0
    claimed_ships: int = 0
    catches: int = 0
    advance_commits: int = 0
    grooms: int = 0
    unblocks: int = 0
    surfaced: int = 0

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(
                    f"{f.name} must be a non-negative integer count, got {v!r}"
                )

    @property
    def overclaim(self) -> int:
        """How many claimed ships have NO oracle answer yet — claims that are
        neither verified nor refuted (caught). Never negative: a quiet
        completion (verified without a claim) is not an over-claim."""
        return max(0, self.claimed_ships - self.verified_ships - self.catches)

    def to_dict(self) -> dict:
        return {
            "verified_ships": self.verified_ships,
            "claimed_ships": self.claimed_ships,
            "catches": self.catches,
            "advance_commits": self.advance_commits,
            "grooms": self.grooms,
            "unblocks": self.unblocks,
            "surfaced": self.surfaced,
            "overclaim": self.overclaim,
        }


@dataclass(frozen=True)
class WorkVerdict:
    """The single verdict `classify_work` returns, with the account echoed back.

    `kind` is the typed dominant `WorkKind`. `reason` is a one-line
    operator-facing summary. `account` is the `WorkAccount` that drove the
    call, carried so `--json` can emit the verdict AND the facts behind it in
    one object (legible distrust — the operator sees not just ADVANCED but the
    4 commits, and any unadjudicated over-claim, behind it).
    """

    kind: WorkKind
    reason: str
    account: WorkAccount

    def to_dict(self) -> dict:
        return {
            "verdict": self.kind.value,
            "reason": self.reason,
            "account": self.account.to_dict(),
        }


def merge(*accounts: WorkAccount) -> WorkAccount:
    """Fold per-iteration accounts into one loop-level account (counter-wise
    sum). PURE. `merge()` of nothing is the zero account — the fold's
    identity — so a loop that never ran an iteration closes honestly IDLE."""
    return WorkAccount(
        verified_ships=sum(a.verified_ships for a in accounts),
        claimed_ships=sum(a.claimed_ships for a in accounts),
        catches=sum(a.catches for a in accounts),
        advance_commits=sum(a.advance_commits for a in accounts),
        grooms=sum(a.grooms for a in accounts),
        unblocks=sum(a.unblocks for a in accounts),
        surfaced=sum(a.surfaced for a in accounts),
    )


def _count_phrases(acc: WorkAccount) -> list[str]:
    """The non-zero kinds as operator-facing phrases, precedence order. The
    shared vocabulary of `classify_work` reasons and `account_lead_token` —
    one source, so the verdict and the headline can never disagree."""
    out: list[str] = []
    if acc.verified_ships:
        n = acc.verified_ships
        out.append(f"{n} pick{'s' if n != 1 else ''} shipped")
    if acc.catches:
        n = acc.catches
        out.append(f"{n} false claim{'s' if n != 1 else ''} caught")
    if acc.advance_commits:
        n = acc.advance_commits
        out.append(f"{n} commit{'s' if n != 1 else ''} advanced")
    if acc.grooms:
        n = acc.grooms
        out.append(f"{n} groom{'s' if n != 1 else ''}")
    if acc.unblocks:
        n = acc.unblocks
        out.append(f"{n} unblock{'s' if n != 1 else ''}")
    if acc.surfaced:
        n = acc.surfaced
        out.append(f"{n} decision{'s' if n != 1 else ''} surfaced")
    return out


def classify_work(account: WorkAccount) -> WorkVerdict:
    """Name the account's dominant work kind. PURE — no I/O.

    Reads the precedence ladder top to bottom — most operator-valuable first
    (this ordering IS the answer to "what should the headline lead with?"):

      1. SHIPPED  — >=1 verified ship. Claims alone never reach here: the
         oracle's count, not the archive subject's, is the gate.
      2. CAUGHT   — no ship, but >=1 refused claim. Ranked above ADVANCED
         because a refused lie is operator-actionable (a worker over-claimed);
         commits are routine.
      3. ADVANCED — real commits landed on the lane, no phase closed. The
         partial-progress rung that used to read as zero.
      4. GROOMED  — bookkeeping moved durable plan state (grooms + unblocks).
      5. SURFACED — raised operator decisions only.
      6. IDLE     — every witnessed counter zero. The honest nothing — a
         statement about THIS ITERATION's work, never about the backlog
         (that is the gate verdict's word, DRAIN).

    An unadjudicated over-claim (claimed > verified + caught) is appended to
    the reason whatever the verdict — visible, never believed.
    """
    phrases = _count_phrases(account)
    summary = " · ".join(phrases)
    if account.overclaim:
        n = account.overclaim
        tail = f" ({n} claimed ship{'s' if n != 1 else ''} unadjudicated)"
    else:
        tail = ""

    if account.verified_ships > 0:
        return WorkVerdict(WorkKind.SHIPPED, summary + tail, account)
    if account.catches > 0:
        return WorkVerdict(WorkKind.CAUGHT, summary + tail, account)
    if account.advance_commits > 0:
        return WorkVerdict(WorkKind.ADVANCED, summary + tail, account)
    if account.grooms > 0 or account.unblocks > 0:
        return WorkVerdict(WorkKind.GROOMED, summary + tail, account)
    if account.surfaced > 0:
        return WorkVerdict(WorkKind.SURFACED, summary + tail, account)
    return WorkVerdict(
        WorkKind.IDLE,
        "no witnessed work this iteration" + tail,
        account,
    )


def account_lead_token(account: WorkAccount) -> str:
    """The composed commit-subject / report headline for `account` — every
    non-zero kind in precedence order, e.g.
    `1 pick shipped · 1 false claim caught · 4 commits advanced`.

    PURE, and computed from the SAME phrases `classify_work`'s reason uses, so
    the headline and the verdict can never disagree (the `subject_lead_token`
    discipline: a function cannot drift the way retyped prose does). An IDLE
    account renders `idle` — the caller decides whether the backlog word
    (`drained`) applies; that is the gate's axis, not this one's.
    """
    phrases = _count_phrases(account)
    return " · ".join(phrases) if phrases else "idle"
