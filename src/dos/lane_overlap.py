"""Lane-tree overlap policy for `/dispatch-loop` lane arbitration.

A *lane* (a `--scope` cluster, a keyword scope, or a bare plan) owns a set of
repo-relative path globs — its `tree`. Two lanes are safe to run concurrently
when their trees barely intersect; the binary "any overlap = refuse" rule was
provably too tight for narrow keyword lanes whose tree shares a handful of
incidental files with a cluster's summary glob.

Read this module as a **lock-compatibility function**, not a swim-lane rule: a
lane is a leased predicate-lock over a region, and the ratio threshold below is a
*deliberately loosened* compatibility test (strict disjointness was too
conservative). That reframing — and why it matters for tuning the threshold and
for the capability-lattice generalization — is `docs/89_the-lane-is-a-region-lock.md`.

The policy is a pure function — list-in, verdict-out — so it is replay-tested
in isolation (`tests/test_dispatch_lane.py::TestArbitrateSoftOverlap`), the
same discipline as `scripts/gate_classify.py`.

  >>> overlap_verdict(["playbooks/ats/workday.yaml"], ["agents/apply_*.py"]).verdict
  <Verdict.ADMIT_SOFT: 'admit_soft'>

  >>> overlap_verdict(["agents/apply_*.py"], ["agents/apply_*.py"]).verdict
  <Verdict.REFUSE_EXACT_GLOB: 'refuse_exact_glob'>

A lane that shares the *identical* glob with a live lease refuses as a hard
collision (REFUSE_EXACT_GLOB), checked before the ratio test so a real
write-surface overlap cannot be diluted to ADMIT by padding the requesting
tree with private files. This closed the 2026-06-01 TM↔tailor mutual-wedge:
TM (8 entries, sharing only `agents/tailor_*.py` + `agents/tailor_steps/...`
with the tailor cluster) scored 2/8 = 25 % ≤ 33 % and SOFT-ADMITTED under the
ratio alone, while the reverse direction refused — an asymmetry that
*guaranteed* a wedge. Exact-glob equality is symmetric and kills it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dos._tree import prefixes_collide as _prefixes_collide
# Lock-aware normalization: a `lock://NAME` named-mutex region (docs/363) maps to a
# reserved, file-disjoint prefix; a FILE entry passes through to the unchanged
# `_tree.norm_tree_prefix` verbatim — so the file:// algebra (and every existing
# verdict) is byte-identical, and a richer region (a named critical section with no
# file backing) rides this UNCHANGED ratio/exact-glob logic. This is the single
# call site that turns "deconfliction beyond file paths" on for the overlap policy:
# `lock://gh-pages-publish` on both sides hits REFUSE_EXACT_GLOB (same reserved
# prefix), two different locks are disjoint, a lock never collides with a file.
from dos.named_lock import normalize_entry as _norm_tree_prefix

# Ratio threshold: shared/requested above this = refuse. ⅓ is NOT a calibrated
# soundness bound — it is a STAND-IN that admits a known hazard. The prior-art
# audit (`docs/114` §A1) is the load-bearing caveat: lane conflict is a *measure*
# here ("how much of the requested tree shares prefixes"), but 50 years of
# concurrency control (Gray et al. 1975, *Granularity of Locks*) make
# lock-compatibility a *boolean predicate* — two writers may share a contended
# datum ONLY under operation commutativity (O'Neil 1986, escrow), which arbitrary
# file overwrites lack. So any ⅓ > 0 admits genuine write–write conflicts on the
# shared remainder (a silent lost-update `verify()` cannot catch — there is no
# over-claim against git). The value was read off two observed lanes — a narrow
# keyword lane that should admit (`--scope workday` at 5/16 = 31 %) vs one sharing
# substantial code with its cluster (`apply-heavy` at 4/10 = 40 %) — i.e. it is an
# empirical elbow between two examples, not a derived safe bound.
#
# Why it is NOT simply flipped to 0 (the audit's first instinct): `docs/114` §F
# dispositioned that ratio flip as a *detector re-tune* — it trades away the read
# concurrency ⅓ buys without closing the underlying hazard (two lanes still collide
# *under any ratio* at the unmediated write moment; DOS is a PDP with no PEP). The
# sound fix is a real shared/exclusive lock MODE + a glob-intersection disjointness
# floor enforced at a `dos`-mediated apply-gate, deferred there rather than half-built
# as a stricter advisory scalar. A workspace that wants the predicate today can set
# `dos.toml [overlap] ratio_max = 0` (tightening below ⅓ takes effect; loosening above
# is capped by the floor — `overlap_policy.floor_decision`).
OVERLAP_RATIO_MAX = 1 / 3


class Verdict(str, Enum):
    """Admission verdict + reason category. Carried as the policy's typed
    return so the arbiter can render a legible refusal without re-classifying
    a free-text string."""
    ADMIT_DISJOINT    = "admit_disjoint"     # no shared prefixes at all
    ADMIT_SOFT        = "admit_soft"         # shared but under the ratio threshold
    ADMIT_SHARED      = "admit_shared"       # shared region, compatible lock modes
    REFUSE_OVERLAP    = "refuse_overlap"     # shared above the ratio threshold
    REFUSE_EXACT_GLOB = "refuse_exact_glob"  # both lanes claim an identical glob
    REFUSE_LOCK_CONFLICT = "refuse_lock_conflict"  # intersecting incompatible locks


@dataclass(frozen=True)
class OverlapDecision:
    verdict: Verdict
    shared: int
    requested: int
    reason: str

    @property
    def admissible(self) -> bool:
        return self.verdict in (
            Verdict.ADMIT_DISJOINT,
            Verdict.ADMIT_SOFT,
            Verdict.ADMIT_SHARED,
        )


def _exact_glob_collisions(req_tree: list[str], lease_tree: list[str]) -> list[str]:
    """Requested entries whose normalized prefix EXACTLY equals a lease entry's.

    This is the *hard*-collision detector that the ratio test below cannot
    see. The ratio test measures *how much* of a lane subsumes another — fine
    for the incidental case (a narrow keyword lane's specific file falling
    under a cluster's broad summary glob). But when two lanes name the
    **identical** glob (`agents/tailor_*.py` on both sides), they are claiming
    the *same write region*, not incidentally overlapping — and that is a
    collision at *any* ratio. The bug this closes: a priority plan-lane (TM, 8
    entries, 6 of them private test files) sharing exactly `agents/tailor_*.py`
    with a `tailor` cluster lease scored 2/8 = 25 % ≤ 33 % and SOFT-ADMITTED,
    then the two loops mutually wedged because the reverse direction
    (tailor 2/3 = 67 %) refused. Exact-glob equality is **symmetric**, so it
    yields the same verdict in both directions and kills that asymmetry.

    The universal empty prefix (a bare ``**/*`` / ``*.py`` that normalizes to
    ``""``) is excluded here — a whole-repo glob is handled by the ratio path
    (it collides with everything, so its ratio is already 100 %); treating it
    as an "exact glob" would refuse every pair of whole-repo lanes for the
    wrong reason. Only *named-region* exact matches count.
    """
    if not req_tree or not lease_tree:
        return []
    lease_exact = {
        _norm_tree_prefix(p)
        for p in lease_tree
        if p and _norm_tree_prefix(p) != ""
    }
    if not lease_exact:
        return []
    seen: set[str] = set()
    hits: list[str] = []
    for r in req_tree:
        if not r:
            continue
        nr = _norm_tree_prefix(r)
        if nr and nr in lease_exact and nr not in seen:
            seen.add(nr)
            hits.append(r)
    return hits


def _shared_count(req_tree: list[str], lease_tree: list[str]) -> int:
    """Count requested entries that prefix-collide with any lease entry.

    Each requested entry counts at most once regardless of how many lease
    entries it collides with — symmetric and stable. Prefix collision is the
    same definition `_tree.lane_trees_disjoint` uses, now shared verbatim via
    `_tree.prefixes_collide` so the two cannot drift.

    A **leading-glob** entry (`**/*`, `*.py`) normalizes to the empty prefix
    ``""`` — the *universal* prefix that matches every path. It is KEPT, not
    dropped: a requested whole-repo glob collides with every lease entry, and a
    whole-repo lease glob is collided-with by every requested entry. (Only a
    LITERALLY blank/empty entry — falsy before normalization — carries no path
    information and is filtered.) This is the fix for the bug where ``**/*`` was
    truncated to ``""`` and then dropped, making the broadest possible tree read
    as "touches nothing" and two whole-repo lanes admit concurrently.
    """
    if not req_tree or not lease_tree:
        return 0
    # Keep the empty prefix when it came from a real (leading-glob) entry; drop
    # only literally-blank entries that carry no path at all.
    lease_prefixes = [_norm_tree_prefix(p) for p in lease_tree if p]
    if not lease_prefixes:
        return 0
    shared = 0
    for r in req_tree:
        if not r:
            continue
        nr = _norm_tree_prefix(r)
        if any(_prefixes_collide(nr, nl) for nl in lease_prefixes):
            shared += 1
    return shared


def overlap_verdict(
    requested_tree: list[str], lease_tree: list[str],
    *, ratio_max: float = OVERLAP_RATIO_MAX,
) -> OverlapDecision:
    """Decide whether a known-tree lane can run alongside a known-tree lease.

    Empty-tree handling is the caller's job (`_lease_blocks` in
    `fanout_state.py` applies the unknown-blast-radius asymmetry); this
    function is only for known-vs-known.

      * Any IDENTICAL named glob on both sides → REFUSE_EXACT_GLOB
        (hard collision, checked first — see `_exact_glob_collisions`; this is
        symmetric, so it cannot admit-one / refuse-the-other).
      * No shared prefixes  → ADMIT_DISJOINT.
      * Shared ≤ ``ratio_max`` of requested tree → ADMIT_SOFT.
      * Shared > ``ratio_max`` → REFUSE_OVERLAP.

    ``ratio_max`` is the soft-overlap tolerance and defaults to the module
    constant ``OVERLAP_RATIO_MAX`` (⅓) — so every existing caller is
    byte-for-byte unchanged. It is a *parameter* (not a hardcode) because the
    elbow is a calibrated guess, not a theory (`docs/90 §2`): a workspace may
    declare a different value in ``dos.toml`` ``[overlap] ratio_max`` (folded
    onto ``SubstrateConfig`` and threaded here by `overlap_policy.PrefixOverlapPolicy`).
    This is the "thresholds are config, mechanism is kernel" split `liveness`
    already uses for its windows; the **functional form** (a ratio compare)
    stays here, and swapping the form entirely is the `overlap_policy` seam.
    The exact-glob hard floor is INDEPENDENT of ``ratio_max`` — an identical
    glob is a collision at any tolerance, including 0.
    """
    # Hard floor: two lanes naming the same glob claim the same write region.
    # Checked BEFORE the ratio so a real collision cannot be diluted to
    # admit by padding the requesting tree with private (non-shared) files.
    exact = _exact_glob_collisions(list(requested_tree), list(lease_tree))
    if exact:
        shared_all = _shared_count(list(requested_tree), list(lease_tree))
        preview = ", ".join(exact[:3]) + ("…" if len(exact) > 3 else "")
        return OverlapDecision(
            Verdict.REFUSE_EXACT_GLOB, shared_all, len(requested_tree),
            (f"exact-glob overlap: identical glob claimed by both lanes "
             f"({len(exact)}: {preview}) — same write region, hard collision "
             "regardless of ratio"),
        )
    requested = max(1, len(requested_tree))
    shared = _shared_count(list(requested_tree), list(lease_tree))
    if shared == 0:
        return OverlapDecision(
            Verdict.ADMIT_DISJOINT, shared, len(requested_tree),
            "no shared prefixes — fully disjoint",
        )
    ratio = shared / requested
    if ratio > ratio_max:
        return OverlapDecision(
            Verdict.REFUSE_OVERLAP, shared, len(requested_tree),
            (f"overlap too large ({shared}/{len(requested_tree)} = "
             f"{ratio:.0%} of requested tree shared, threshold "
             f"{ratio_max:.0%})"),
        )
    return OverlapDecision(
        Verdict.ADMIT_SOFT, shared, len(requested_tree),
        (f"soft-overlap admit — {shared}/{len(requested_tree)} = "
         f"{ratio:.0%} of requested tree shared (≤{ratio_max:.0%})"),
    )
