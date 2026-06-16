"""The SELF_MODIFY built-in admission predicate — the self-modification guard (ADM Phase 2, docs/73).

This is the first *real* second built-in predicate (the first, `DisjointnessPredicate`,
was a behavior-preserving refactor of the existing rule). It refuses a lease whose
requested file tree intersects the **orchestrator's own running code** — the kernel
modules that sit in a live dispatch loop's execution path.

Why this is a kernel concern (`project-dos-self-modification-hazard`, mechanism (a)):
editing the arbiter / classifiers / token rules *while a loop that depends on them
is live* is a T1 hazard — a packet that rewrites `arbiter.py` between two Step-0
admission checks changes the very logic deciding whether the next packet may run,
silently, mid-flight. The "natural DOS-kernel realization" of guarding against it is
a new typed arbiter refuse: intersect the requested tree with a frozen
`_DISPATCH_RUNTIME_FILES` set and refuse on a hit. ADM's predicate seam is the
vehicle; this is its first safety payload.

The override is `--force` and ONLY `--force` — the operator's explicit "yes, I am
editing the kernel between loop runs, I know what I'm doing" (the safe, human-in-loop
path the hazard memo calls for). A predicate can never force itself; `--force` skips
predicate refusals exactly as it skips the disjointness refuse (see `arbiter.arbitrate`).

Pure stdlib + the `_tree` prefix algebra — no I/O, no host names. The set is data,
pinned with a comment tying each entry to *why* it is runtime-critical, so a reviewer
can audit the blast radius of the guard at a glance.
"""

from __future__ import annotations

from dos._tree import norm_tree_prefix as _norm_tree_prefix
from dos._tree import prefixes_collide as _prefixes_collide
from dos.admission import AdmissionRequest, AdmissionVerdict

# The typed reason a SELF_MODIFY refusal carries — declared in
# `dos.reasons.BASE_REASONS` so it is simultaneously emittable (here), verifiable
# (the registry's `category_for`), refusable (`is_refusal`), and `dos man
# wedge SELF_MODIFY`-documented (the Axis-1 completeness rail).
SELF_MODIFY_REASON = "SELF_MODIFY"

# ---------------------------------------------------------------------------
# The T1 runtime set — the kernel modules in a LIVE dispatch loop's own decision
# path. Editing any of these mid-flight changes the logic that admits the NEXT
# packet, silently. Repo-relative POSIX paths (the form a lane tree carries). Each
# entry is annotated with WHY it is runtime-critical so the guard's blast radius
# is auditable. This is the T1 set from `project-dos-self-modification-hazard`,
# scoped to the kernel's *adjudication* path — NOT every kernel file (a lease
# editing `timeline.py`, pure post-hoc assembly, is not a live-decision hazard).
# ---------------------------------------------------------------------------
#
# docs/355 — the set was TRIMMED to the mid-flight-critical CORE: the files a
# live ADMISSION reads on every decision, where a mid-flight rewrite silently
# changes the next packet's verdict. The docs/73 criterion ("modules in the
# running orchestrator's own execution path") applied honestly leaves six. The
# loop-internal classifiers (`gate_classify`, `loop_decide`, `tokens`) and the
# reason DATA/prose (`reasons`, `wedge_reason`) were DROPPED: they are edited far
# more often than they are mid-flight-critical (a reason rename was the dominant
# false interactive wall — the #11/#145 type-specimen), a live LOOP still reaches
# them via the opt-in apply-gate (docs/126) when it leased `src/`, and the
# always-on guard now softens to a WARN for an interactive no-loop human anyway
# (docs/355 Change 1). The hard deny survives where the hazard is real: a live
# loop rewriting its own collision-adjudication path.
_DISPATCH_RUNTIME_FILES: tuple[str, ...] = (
    # The admission kernel itself — the code that runs THIS very check. A packet
    # rewriting it between two Step-0 calls changes whether the next loop admits.
    "src/dos/arbiter.py",
    # The admission-predicate seam + the self-modify guard — the conjunction
    # runner and this predicate. Editing them mid-flight can disable the guard
    # that is protecting the edit (the most direct self-defeat).
    "src/dos/admission.py",
    "src/dos/self_modify.py",
    # The soft-overlap / tree-disjointness algebra the arbiter delegates its
    # collision check to — the substance of "may these two lanes coexist."
    "src/dos/lane_overlap.py",
    "src/dos/_tree.py",
    # The config seam — the lane taxonomy + paths a live arbiter reads on every
    # admission. Re-pointing the workspace or rewriting the taxonomy mid-loop
    # changes what "disjoint" even means for the next packet.
    "src/dos/config.py",
)

# Pre-normalized prefixes (truncated at the first glob `*`, `\\`→`/`), computed
# once at import. The intersection test compares against these — the same
# normalization `lane_overlap._shared_count` / `_tree.lane_trees_disjoint` use,
# so "does this lane touch a runtime file" is decided by the identical algebra
# the rest of the kernel trusts for collision detection.
_RUNTIME_PREFIXES: tuple[str, ...] = tuple(
    _norm_tree_prefix(p) for p in _DISPATCH_RUNTIME_FILES
)


def _tree_touches_runtime(
    requested_tree: list[str],
    runtime_files: tuple[str, ...] = _DISPATCH_RUNTIME_FILES,
) -> list[str]:
    """Return the runtime files a requested tree would touch (empty = none).

    Prefix-collision in BOTH directions (a requested `src/dos/` glob contains
    `src/dos/arbiter.py`; a requested `src/dos/arbiter.py` IS a runtime file) —
    the same rule `_tree.lane_trees_disjoint` uses, now shared verbatim via
    `_tree.prefixes_collide`. Returns the offending runtime paths (the original,
    un-normalized entries) so the refusal can name exactly what was hit, in
    declaration order.

    A **leading-glob** request (`**/*`) normalizes to the empty (universal)
    prefix, which collides with EVERY runtime file — so a whole-repo lease is
    correctly flagged as touching the kernel's own code, not waved through as
    "touches nothing." Only literally-blank entries are filtered (no path
    information); the empty prefix from a real glob is kept. (This is the
    self-modify half of the `**/*`-normalizes-to-empty bug.)

    ``runtime_files`` is the kernel-source set to check against. It defaults to
    the full static `_DISPATCH_RUNTIME_FILES`, but a boundary caller hands in the
    subset that actually EXISTS under the served workspace (`existing_runtime_files`)
    — so a `**/*` lane in a *foreign* repo (which has no `src/dos/*.py`) collides
    with the empty set and is admitted, while the same lane in the DOS repo itself
    is refused. The default keeps the pure, workspace-unaware behavior for direct
    callers and tests.
    """
    hits: list[str] = []
    req_prefixes = [_norm_tree_prefix(p) for p in (requested_tree or []) if p]
    if not req_prefixes:
        return hits
    for original in runtime_files:
        rp = _norm_tree_prefix(original)
        if any(_prefixes_collide(nr, rp) for nr in req_prefixes):
            hits.append(original)
    return hits


class SelfModifyPredicate:
    """Refuse a lease whose tree includes the orchestrator's own running code.

    Always-on, like `DisjointnessPredicate` (`admission.built_in_predicates`).
    Unlike disjointness, it is **request-absolute**: it does NOT depend on the
    live lease — self-modification is a hazard regardless of what else is
    running — so it answers from the REQUEST alone and ignores ``live_lease``.
    (It still implements the per-lease predicate signature so it composes in the
    same conjunction; it returns the same verdict for every live lease, which is
    harmless: `run_predicates` short-circuits on the first refusal.)

    It fires on EVERY admit path, including an otherwise-idle repo with NO live
    leases: `run_predicates` runs the conjunction once against a synthetic
    empty-lease sentinel exactly so request-absolute predicates are not skipped
    when nothing else is live, and `arbiter.arbitrate` gates its cluster /
    exclusive-lane / keyword fast-paths through that conjunction (it does not
    return `acquire` before consulting the predicates). So a self-modifying lease
    is refused whether the repo is busy or idle, whether the request is a cluster,
    an exclusive lane, or a keyword. `--force` is the sole override. (This closes
    the idle-repo / fast-path gaps an adversarial review caught; see
    `test_self_modify_*` and `TestSelfModifyGatesEveryAdmitPath` for the pinned
    contract across all paths.)
    """

    name = "self-modify"

    def __init__(self, runtime_files: tuple[str, ...] = _DISPATCH_RUNTIME_FILES):
        """``runtime_files`` is the kernel-source set this guard protects.

        Defaults to the full static `_DISPATCH_RUNTIME_FILES` (pure, workspace-
        unaware — the safe default for a direct/test caller). A boundary builder
        (`admission.built_in_predicates(workspace=…)`) constructs the predicate
        with the subset that actually EXISTS under the served workspace
        (`existing_runtime_files`), so the guard fires only where the kernel
        source it protects is genuinely present — the DOS repo serving itself,
        not a foreign repo whose `**/*` lane cannot edit a `src/dos/` file that
        isn't there. Stored verbatim; no I/O here (the existence probe already
        ran at the boundary).
        """
        self._runtime_files = tuple(runtime_files)

    def __call__(self, request: AdmissionRequest, live_lease: dict,
                 config: object) -> AdmissionVerdict:
        hits = _tree_touches_runtime(list(request.tree), self._runtime_files)
        if not hits:
            return AdmissionVerdict.admit()
        shown = ", ".join(hits[:3]) + ("…" if len(hits) > 3 else "")
        return AdmissionVerdict.refuse(
            f"lane {request.lane!r} would edit the orchestrator's own running "
            f"code ({shown}) — refusing to let a live loop rewrite the kernel "
            f"that is adjudicating it (SELF_MODIFY). Pass --force only if you "
            f"are deliberately editing the kernel between loop runs.",
            reason_class=SELF_MODIFY_REASON,
        )


# ---------------------------------------------------------------------------
# Boundary helper — the ONE place the existence I/O lives. Mirrors
# `admission.active_predicates` (entry-point discovery) and the liveness/arbitrate
# pattern: I/O is gathered at the CALL BOUNDARY and the result is handed to a pure
# predicate, never run inside `arbitrate` itself.
# ---------------------------------------------------------------------------
def existing_runtime_files(workspace) -> tuple[str, ...]:
    """The `_DISPATCH_RUNTIME_FILES` that actually exist under ``workspace``.

    This is what makes the SELF_MODIFY guard **workspace-aware**: the static set
    is the kernel's own source paths (`src/dos/arbiter.py`, …), which exist only
    when DOS is serving its OWN repo. Against a foreign repo (or a fresh
    scaffold) none of them resolve, so this returns ``()`` and a `**/*` lane
    touches nothing — the correct admit. Against the DOS repo every entry
    resolves and the guard fires on a whole-repo lease.

    The single existence I/O of the self-modify guard. Called by
    `admission.built_in_predicates(workspace=…)` at the boundary; the predicate
    it feeds stays pure. ``workspace`` is a path-like (the `SubstrateConfig.workspace`);
    a falsy/None workspace yields the full static set (cannot prove non-existence,
    so stay conservative — the safe direction for a safety guard).
    """
    if not workspace:
        return _DISPATCH_RUNTIME_FILES
    from pathlib import Path
    root = Path(workspace)
    return tuple(f for f in _DISPATCH_RUNTIME_FILES if (root / f).exists())
