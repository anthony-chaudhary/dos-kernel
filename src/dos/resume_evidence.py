"""resume-evidence — the boundary I/O for the resume axis (docs/107 §3.3, §5).

`resume.resume_plan` is a PURE verdict over `AncestryFacts` (which claimed SHAs are
in ancestry). SOMETHING has to gather those facts from git, and SOMETHING has to
mint a `STEP_VERIFIED` by re-checking a claimed SHA against the non-forgeable rung
(§5 req 2). That is this module — the resume axis's `git_delta`/`journal_delta`:
boundary I/O (subprocess + the served root) feeding the pure core, never inside the
verdict.

Two boundary jobs:

  * **`gather_ancestry(...)`** — ask git which of a set of claimed SHAs are
    reachable from HEAD on the served workspace, and freeze the answer into the
    `AncestryFacts` the pure `resume_plan` consumes. The `liveness` evidence-gather
    shape: the subprocess happens HERE, the already-decided membership is handed to
    the classifier.
  * **`verify_step(...)`** — the `STEP_VERIFIED` MINT (§5). Given a claimed
    `(step_id, sha)`, decide whether it may become a minted belief: the SHA must be
    **in ancestry** AND the commit must stand on the **non-forgeable rung** (it
    touched ≥1 distinctive file — NOT an `--allow-empty` commit, NOT a
    bookkeeping/release-bump-only footprint). A step that passes yields a
    `STEP_VERIFIED` entry tagged `via="file-path"`; one that fails yields *nothing*
    (it stays in the residual). **A forged `--allow-empty` step never reaches
    `STEP_VERIFIED`** — the load-bearing Phase-3 guarantee.

The served root is passed EXPLICITLY (never the process-global active config), so a
long-lived caller fielding several workspaces — the MCP server, a fleet daemon —
gets the right tree (the `git_delta` discipline). Every failure mode degrades to
the SAFE direction: a SHA we cannot resolve is treated as NOT in ancestry / NOT
verifiable (fail-closed — a step we cannot prove landed must be redone, never
skipped), the opposite of `git_delta`'s permissive empty (because here the safe
direction for a *resume anchor* is "don't trust it," whereas for a *liveness
delta* it is "no progress observed").
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from dos import config as _config
from dos import intent_ledger as _il
from dos.intent_ledger import LedgerState
from dos.resume import AncestryFacts

_GIT_TIMEOUT_S = 15


def _is_ancestor(sha: str, *, root: Path | str) -> bool:
    """True iff `sha` is reachable from HEAD (an ancestor) on `root`. Fail-closed.

    `git merge-base --is-ancestor <sha> HEAD` exits 0 iff `sha` is an ancestor of
    HEAD, 1 iff not, and >1 on error (bad sha, not a git dir). We treat ONLY a
    clean exit-0 as "in ancestry" — every other outcome (unknown sha, git missing,
    timeout, non-git dir) is `False` (the safe direction for a resume anchor: a SHA
    we cannot prove is reachable must not anchor a resume point). The opposite of
    `git_delta`'s permissive-empty, because the safe failure here is "don't trust."
    """
    s = (sha or "").strip()
    if not s:
        return False
    try:
        res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", s, "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _touched_files(sha: str, *, root: Path | str) -> set[str] | None:
    """The repo-relative paths commit `sha` touched on `root`, or None if unresolvable.

    The explicit-root sibling of `oracle._git_touched_files` (which reads the
    process-global active config). None means "could not resolve" (unknown sha,
    shallow clone, git missing) — the caller treats it as NOT verifiable
    (fail-closed). An EMPTY set means the commit touched NO files: an `--allow-empty`
    commit — the exact forgeable case §5 req 2 forecloses.
    """
    s = (sha or "").strip()
    if not s:
        return None
    try:
        res = subprocess.run(
            ["git", "show", "--name-only", "--format=", s],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return {ln.strip().replace("\\", "/") for ln in res.stdout.splitlines() if ln.strip()}


def step_stands_on_nonforgeable_rung(
    sha: str, *, root: Path | str,
    region: "list[str] | tuple[str, ...] | None" = None,
    touched_files=None,
    is_ancestor=None,
) -> bool:
    """True iff `sha` is a SAFE resume anchor — in ancestry AND its footprint is real (§5).

    The §5-req-2 predicate, the heart of the mint. A claimed step's SHA earns a
    `STEP_VERIFIED` ONLY when all hold:

      1. **In ancestry.** The commit is reachable from HEAD (`_is_ancestor`). A
         claimed SHA that is not in ancestry is a step the agent SAID it landed but
         never did (or that was rewritten out) — fail-closed, not verified.
      2. **Non-forgeable footprint.** The commit touched ≥1 real file. An
         `--allow-empty` commit (the forgeable rung §5 names: an empty commit whose
         SUBJECT names the step) touches NO files, so it fails this — exactly the
         named attack.
      3. **Footprint INTERSECTS the step's declared region (when one is declared).**
         This closes the residual §5 hole the adversarial review found: requirement 2
         alone defeats `--allow-empty` but NOT a forged record pointing at any *real,
         unrelated* commit (the attacker needs only ANY ancestry SHA). When the step
         declared a file region (a list of repo-relative globs in its INTENT), the
         commit's touched-file set must OVERLAP that region — a commit that touched
         only files OUTSIDE the step's region is not that step's work, even if it is a
         real ancestry commit. Overlap reuses the kernel's ONE collision algebra
         (`_tree.lane_trees_disjoint`, case-folded / leading-glob-aware) so there is
         no second match definition. A step with NO declared region falls back to
         requirement 2 only (the `--allow-empty` defense) — additive, so a region-less
         ledger still gets real protection, just not region-pinned.

    `touched_files` / `is_ancestor` are injectable (callable(sha)->set|None and
    callable(sha)->bool) so the predicate is unit-testable WITHOUT git — the
    `oracle`/`liveness` injection discipline. Production passes neither and the
    git-backed defaults run against `root`.
    """
    anc = is_ancestor or (lambda x: _is_ancestor(x, root=root))
    touch = touched_files or (lambda x: _touched_files(x, root=root))
    if not anc(sha):
        return False
    files = touch(sha)
    if not files:  # None (unresolvable) OR empty (--allow-empty) → not a safe anchor
        return False
    if region:
        # The footprint must OVERLAP the step's declared region. The concrete touched
        # files are treated as zero-wildcard "globs"; intersection is the negation of
        # the kernel's disjointness verdict — one algebra, no drift.
        from dos._tree import lane_trees_disjoint
        if lane_trees_disjoint(list(files), list(region)):
            return False  # commit touched only files OUTSIDE the step's region
    return True


def verify_step(
    run_id: str,
    step_id: str,
    sha: str,
    *,
    cfg: "_config.SubstrateConfig | None" = None,
    path: Path | None = None,
    region: "list[str] | tuple[str, ...] | None" = None,
    touched_files=None,
    is_ancestor=None,
) -> dict | None:
    """Mint a `STEP_VERIFIED` for `(step_id, sha)` IFF it stands on the non-forgeable rung.

    The CLI-boundary write the dispatch loop / `dos resume verify-step` calls after
    an agent claims a step. Re-checks the claimed SHA against ancestry on the
    non-forgeable rung (`step_stands_on_nonforgeable_rung`, incl. the `region`
    intersection when one is declared); on success appends a `STEP_VERIFIED` entry
    (tagged `via="file-path"`) to the run's ledger and returns the stamped entry; on
    failure appends NOTHING and returns None (the step stays in the residual — a
    forged/unverifiable claim is never minted into a belief).

    This is the §5-req-2 guarantee in code: a step the agent merely CLAIMED — claimed
    with an `--allow-empty` commit, or claimed against a real-but-unrelated commit
    outside its declared region — cannot reach `STEP_VERIFIED`, so it can never become
    a resume anchor that skips work that never happened.
    """
    cfg = _config.ensure(cfg)
    if not step_stands_on_nonforgeable_rung(
        sha, root=cfg.paths.root, region=region,
        touched_files=touched_files, is_ancestor=is_ancestor,
    ):
        return None
    entry = _il.step_verified_entry(step_id, sha, via="file-path")
    return _il.append(run_id, entry, path=path, cfg=cfg)


def adjudicate_verified_steps(
    state: LedgerState,
    *,
    root: Path | str,
    touched_files=None,
    is_ancestor=None,
) -> frozenset[str]:
    """Re-adjudicate, at READ time, which steps stand on the non-forgeable rung (§5).

    The docs/107 §5 / docs/103 fix, and the heart of "the kernel doesn't believe the
    agents." The intent ledger is written BY the distrusted run, so a stored
    `STEP_VERIFIED` record is an UNAUTHENTICATED HINT — a malicious run can append its
    own `STEP_VERIFIED {via: "file-path", sha: <any real ancestry commit>}` for a step
    it never did. This RE-RUNS the non-forgeable footprint check
    (`step_stands_on_nonforgeable_rung`) on each step's SHA, and returns ONLY the step
    ids that pass — the authority the pure `resume_plan` trusts for "done"
    (`AncestryFacts.steps_verified_at_read`), never the agent's say-so.

    For each declared step we re-check the step's SHA — preferring the
    `STEP_VERIFIED` record's recorded SHA, falling back to the `STEP_CLAIMED` SHA —
    against `step_stands_on_nonforgeable_rung` (in ancestry AND a real, non-empty
    footprint). A forged record pointing at an unrelated empty/foreign commit fails
    the footprint re-check (an `--allow-empty` forgery touches nothing; a record with
    no real SHA resolves to nothing) and is absent from the result, so the step is
    redone. `touched_files`/`is_ancestor` are injectable for tests (no git needed).

    NOTE on the residual hardening: this re-check confirms the commit is a real
    artefact in ancestry; a future tightening (the review's strongest suggestion)
    would also require the footprint to INTERSECT the step's declared file region, so
    a real-but-unrelated commit can't anchor a step. That needs per-step declared
    regions the ledger doesn't yet carry; the non-empty-footprint + ancestry re-check
    already defeats the `--allow-empty` forgery the §5 attack names, and a real commit
    falsely claimed for a step is still strictly safer than trusting the stored record.
    """
    out: set[str] = set()
    for sid in state.declared_steps:
        vs = state.verified.get(sid)
        sha = (vs.sha if vs and vs.sha else state.claimed.get(sid, ""))
        if not sha:
            continue
        region = state.step_regions.get(sid)  # the step's declared file region (or None)
        if step_stands_on_nonforgeable_rung(
            sha, root=root, region=region,
            touched_files=touched_files, is_ancestor=is_ancestor,
        ):
            out.add(sid)
    return frozenset(out)


def gather_ancestry(
    state: LedgerState,
    *,
    cfg: "_config.SubstrateConfig | None" = None,
    extra_shas: Iterable[str] = (),
    lane_advanced_past_resume: bool = False,
    is_ancestor=None,
    touched_files=None,
    head_sha: str = "",
) -> AncestryFacts:
    """Freeze the RE-ADJUDICATED ancestry facts `resume_plan` needs (§3.3, §5).

    The boundary evidence-gather (the `liveness` CLI shape). Two reads, both at this
    boundary, never inside the pure verdict:

      1. **Ancestry membership** — collect every SHA the ledger mentions (claimed +
         verified + start + `extra_shas`) and ask git which are ancestors of HEAD on
         the served workspace (`_is_ancestor`, explicit root).
      2. **Step re-adjudication (the §5 fix)** — RE-RUN the non-forgeable footprint
         check on each declared step (`adjudicate_verified_steps`), so the pure
         verdict's "done" set comes from a fresh git re-check, NOT from the
         agent-written `STEP_VERIFIED` record. A forged record is rejected here.

    `lane_advanced_past_resume` is computed by the CALLER (it knows the lane's tree
    and the commits since the resume point); the verdict only consumes it.
    `is_ancestor`/`touched_files` are injectable for tests (the `oracle` injection
    discipline). The result is handed verbatim to the pure `resume.resume_plan`.
    """
    cfg = _config.ensure(cfg)
    root = cfg.paths.root
    anc = is_ancestor or (lambda x: _is_ancestor(x, root=root))

    candidates: set[str] = set()
    if state.start_sha:
        candidates.add(state.start_sha)
    candidates.update(s for s in state.claimed.values() if s)
    candidates.update(vs.sha for vs in state.verified.values() if vs.sha)
    candidates.update(s for s in extra_shas if s)

    in_ancestry = frozenset(s for s in candidates if anc(s))
    verified_at_read = adjudicate_verified_steps(
        state, root=root, touched_files=touched_files, is_ancestor=is_ancestor,
    )
    return AncestryFacts(
        shas_in_ancestry=in_ancestry,
        steps_verified_at_read=verified_at_read,
        head_sha=head_sha,
        lane_advanced_past_resume=lane_advanced_past_resume,
    )
