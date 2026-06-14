"""Registry-first ship oracle — single source of truth for "has this phase shipped?"

Background — the tail-wagging-the-dog inversion this module starts to correct.
============================================================================

Before this module, every gate that needed to answer "is `(plan, phase)`
already shipped?" went straight to `scripts/check_phase_shipped.py`, which
greps git log + plan-doc markdown for a `· SHIPPED <date> <sha>` heading
stamp. That grep is the *cheap* signal but it lags reality whenever a stamp
isn't atomically written at ship time (`QWB1` `mark done` partially fixed
this; `QWB2` /replan backstop catches the rest). When the stamp lags, the
gates that consume it get the wrong answer:

  * `fanout_state._check_phase_shipped_prescreen` (register-time gate)
  * `next_up_render._batch_check_shipped` (packet-render pre-screen)
  * `/dispatch` Step 5.6 empty-packet gate
  * `gate_classify.classify_packet` (typed-verdict classifier)

The fix is to invert the lookup: the run registry (`recently_completed` in
`docs/_plans/execution-state.yaml`) is the *load-bearing* source — every
`fanout_state.py mark <phase> done` writes a row there with the SHA at ship
time, atomically, in the same call that publishes the work. The markdown
stamp becomes a *belt-and-suspenders* secondary signal: useful when the
registry is missing a row (a manual `git commit` that bypassed `mark`), but
never the primary gate.

What this module provides
=========================

`is_shipped(plan, phase, *, state=..., grep_fallback=...) -> ShipVerdict`
   Pure function. Looks up `(plan, phase)` in the supplied registry state;
   if a `recently_completed` row exists with `status: done`, returns a
   `ShipVerdict(shipped=True, sha=<short-sha>, source='registry')`. Otherwise
   calls `grep_fallback(plan, phase)` (defaulting to a thin wrapper around
   `check_phase_shipped.py --batch`) and returns its verdict tagged
   `source='grep'`. The fallback is fully pluggable so tests can pass a stub.

`batch_is_shipped(pairs, *, state=..., grep_fallback=...) -> dict[(plan,phase), ShipVerdict]`
   Many-pair variant. Registry hits short-circuit; the residual misses are
   passed to the fallback in one batched call (the existing batch shape of
   `check_phase_shipped.py`), so the worst case is still one subprocess.

`load_state()` / `load_state_from(path)` are the I/O wrappers. The pure-fn
core (`is_shipped` / `batch_is_shipped`) takes the parsed state explicitly so
callers and tests can inject any shape they want.

Status taxonomy considered "shipped"
====================================

A `recently_completed` row counts as a ship iff:

  * the row's `status` field is `done` (terminal-success), AND
  * the row's `claim_status` is either absent OR `done`.

`failed` / `stalled` / `expired` rows DO NOT count (those are terminal-
not-success). The `abandoned:` bucket is NEVER consulted — QWB3 routes
TTL-expired soft claims there precisely so no reader of `recently_completed`
miscounts them as completions.

CLI
===

    python scripts/ship_oracle.py <PLAN> <PHASE>
    python scripts/ship_oracle.py --batch < pairs.txt   # `<plan> <phase>` per line

Exit codes mirror `check_phase_shipped.py --batch`:

    0 — at least one queried pair is shipped
    1 — none shipped
    2 — usage/error

Each row is printed as a JSON line:

    {"plan": "IF", "phase": "IF4.1", "shipped": true, "sha": "53943fc2",
     "source": "registry"}

The CLI exists for inspection and tests; the in-repo callers
(`fanout_state.py`, `next_up_render.py`) import the Python API directly.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

from dos import config as _config

# Path coupling resolves against the ACTIVE WORKSPACE (separation refactor),
# not the package's own tree. Env overrides preserved for tests. The pure
# `is_shipped`/`batch_is_shipped` take `state` explicitly, so only the default
# `load_state()`/`load_soaks()` loaders read these.


def _state_path() -> Path:
    env = os.environ.get("JOB_FANOUT_STATE_PATH") or os.environ.get("DISPATCH_STATE_PATH")
    return Path(env) if env else _config.active().paths.execution_state


def _soaks_path() -> Path:
    env = os.environ.get("DISPATCH_SOAKS_PATH")
    return Path(env) if env else _config.active().paths.soaks_index


def _workspace_root() -> Path:
    """The workspace whose git history + plan docs the oracle reads.

    The grep rung shells out to `git` and reads plan-doc files; both must run
    against the SERVED workspace, not the dos package's own tree.
    """
    return _config.active().paths.root


# ---------------------------------------------------------------------------
# Verdict shape
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ShipVerdict:
    """One pair's verdict. `source` names which signal answered.

    `sha` is best-effort short SHA — present on `registry` hits when the
    `recently_completed` row carries `commit_sha`, present on `grep` hits
    when `check_phase_shipped` resolved a commit. Absent (`None`) when the
    answer is `shipped=False` or when the source can't name a SHA.

    `summary` is the human-readable commit summary when known — populated by
    the grep fallback for log-friendly output; not always available from the
    registry (the row doesn't carry the commit subject).

    `rung` is the RAW evidence rung the grep fallback stood on (`direct`,
    `file-path`, `release-prefix`, `body-mention`, `hyg-slug`,
    `sub-phase-parent`) — the `via` field `phase_shipped` already emits. It is
    carried verbatim so a verdict can be graded by FORGEABILITY: `file-path` is
    the artefact/diff rung (non-forgeable — a commit cannot fake which files it
    touched), whereas `direct`/`release-prefix`/`body-mention` rest on the commit
    SUBJECT/BODY the agent itself authored (forgeable by `git commit --allow-empty
    -m 'docs/X: PHASE …'`). `source` carries the GRADED label derived from it
    (`grep-artifact` vs `grep-subject`); `rung` keeps the un-graded original so a
    consumer that wants the exact rung name still has it. This is the same
    forgeability split `resume.NONFORGEABLE_RUNGS` already trusts ({file-path,
    registry}) — surfaced here instead of discarded at the oracle boundary
    (docs/118). Empty for a registry hit, a miss, or an injected fallback that
    doesn't set it.
    """

    plan: str
    phase: str
    shipped: bool
    sha: str | None = None
    source: str = ""  # "registry" | "grep" | "grep-artifact" | "grep-subject" | "none"
    summary: str = ""
    rung: str = ""  # raw `via` from the grep rung — the un-graded provenance

    def to_dict(self) -> dict:
        d = {
            "plan": self.plan,
            "phase": self.phase,
            "shipped": self.shipped,
            "source": self.source,
        }
        if self.sha:
            d["sha"] = self.sha
        if self.summary:
            d["summary"] = self.summary
        if self.rung:
            d["rung"] = self.rung
        return d


# ---------------------------------------------------------------------------
# The non-git evidence rung (docs/109/265) — a conjunctive, accountability-only
# upgrade layered on top of a git ship-verdict. The DATUM is kernel-local: a label
# + a one-line why + the rung's own verdict word, carrying NO provider type and no
# host name (the `gh api` subprocess lives in a DRIVER, resolved by name at the
# `cmd_verify` boundary; this dataclass is the already-gathered result handed in,
# exactly as `grep_touched_files`/`soaks`/`commit_touches_doc` are). The whole
# reason it is safe to ship: a non-git rung may make `verify` answer MORE
# skeptically (mint a richer `source` over a commit git ALREADY found, or withhold
# that upgrade), NEVER more permissively — it is applied ONLY to a `shipped=True`
# git verdict, so green CI without a reachable commit manufactures nothing
# (`docs/265 §1`). The kernel adjudication stays mechanical; only the SIGNAL gets
# more accountable (the `docs/76` move restated for `verify`).
# ---------------------------------------------------------------------------

# The non-git states an upgrade may stand on — the rung's own verdict word, mirroring
# `ci_status.Ci`. Kept as a frozen set of strings (data, not an import) so `oracle`
# names no driver type: the boundary maps a `CiVerdict` (or any future driver's
# verdict) onto one of these tokens before handing in a `NonGitRung`.
_NON_GIT_GREEN = "GREEN"
_NON_GIT_RED = "RED"
# A withheld-upgrade marker stamped onto a `shipped=True` verdict's `summary` when
# the non-git rung is RED on the very commit the git rung matched — the ship stands
# (git is the necessary gate), but the upgrade is withheld and the state flagged so a
# host MAY route a decision off it (docs/265 §2b; Phase 2 decides whether RED demotes
# to a WARN-class source — for now it is surfaced, never silently dropped).
_NON_GIT_RED_WITHHOLD_MARKER = (
    "[ship_oracle: non-git rung RED at this commit — git ship stands, "
    "accountability upgrade WITHHELD (docs/265)]"
)


@dataclasses.dataclass(frozen=True)
class NonGitRung:
    """An already-gathered non-git evidence verdict for one commit (docs/265 §2b).

    `source` is the upgraded label a GREEN rung mints onto the verdict (`"ci-green"`
    first; an infra-log/approval driver mints its own — `"approved"`, `"audit-green"`).
    It is DATA the verdict carries (the renderer prints it as `(via <source>)`), no
    provider type. `reason` is a one-line why (`"checks green at <sha>"`), carried
    into the verdict `summary` so an operator sees what upgraded it. `state` is the
    rung's own verdict word — `"GREEN"` / `"RED"` / `"NO_SIGNAL"` / `"PENDING"` — the
    only field the conjunctive fold branches on.

    The fold (`_apply_non_git_rung`) is applied ONLY to a `shipped=True` git verdict
    and ONLY in the conjunctive direction: GREEN upgrades `source`; RED withholds the
    upgrade (ship stands, a marker flags it); NO_SIGNAL/PENDING (and any unknown
    state) pass the git verdict through byte-identical. It can never promote
    `shipped=False → True` — the structural safety property the seam stands on.
    """

    source: str
    reason: str = ""
    state: str = ""

    def to_dict(self) -> dict:
        return {"source": self.source, "reason": self.reason, "state": self.state}


def _apply_non_git_rung(
    verdict: "ShipVerdict",
    non_git_rung: "NonGitRung | None",
) -> "ShipVerdict":
    """Fold a non-git rung onto a git ship-verdict — conjunctive, never promoting.

    Returns `verdict` unchanged when there is no rung, the git verdict is NOT
    `shipped=True` (the necessary gate did not pass — a non-git rung cannot
    manufacture a ship), or the rung's `state` is neither GREEN nor RED (NO_SIGNAL /
    PENDING / unknown all degrade to the git answer, byte-identical). The two active
    branches:

      * GREEN → mint `verdict.source = non_git_rung.source` (the accountability
        upgrade: a richer rung name over a commit git already found) and append the
        rung's `reason` to `summary` so the upgrade is legible.
      * RED → keep `shipped=True` and the git `source` (the ship stands), but append
        the withhold marker + the rung's reason so a host can route a decision off the
        flagged-but-unchanged state.

    PURE — the rung was gathered at the boundary; this only folds the already-decided
    states. The `shipped` bit is NEVER touched (the §1 invariant: conjunctive, never
    disjunctive)."""
    if non_git_rung is None or not verdict.shipped:
        return verdict
    state = (non_git_rung.state or "").strip().upper()
    why = (non_git_rung.reason or "").strip()
    if state == _NON_GIT_GREEN:
        upgraded_source = (non_git_rung.source or "").strip() or verdict.source
        new_summary = verdict.summary
        if why:
            new_summary = (new_summary + " " if new_summary else "") + why
        return dataclasses.replace(verdict, source=upgraded_source, summary=new_summary)
    if state == _NON_GIT_RED:
        flag = _NON_GIT_RED_WITHHOLD_MARKER + (f" {why}" if why else "")
        new_summary = (verdict.summary + " " if verdict.summary else "") + flag
        return dataclasses.replace(verdict, summary=new_summary)
    # NO_SIGNAL / PENDING / unknown → the git verdict, untouched.
    return verdict


# ---------------------------------------------------------------------------
# State loading (the only I/O paths)
# ---------------------------------------------------------------------------


def load_state_from(path: Path) -> dict:
    """Parse an execution-state.yaml file. Returns the raw dict; `{}` on miss.

    A missing file, malformed YAML, or missing PyYAML all degrade to `{}`
    — callers then see "no registry rows" and fall through to the grep
    fallback. This is the same defensive shape `next_up_context.py` uses;
    `ship_oracle` is downstream of it and must not be more brittle.
    """
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, Exception):
        return {}
    return data if isinstance(data, dict) else {}


def load_state() -> dict:
    """Default loader — parses the active workspace's execution-state.yaml."""
    return load_state_from(_state_path())


def load_soaks_from(path: Path) -> list[dict]:
    """Parse a `_soaks/index.yaml` file → its `soaks:` list; `[]` on any miss.

    A missing file, missing PyYAML, malformed YAML, or a non-list `soaks` key
    all degrade to `[]` (the #326 soak cross-check then suppresses nothing —
    permissive, never more brittle than the picker). Mirrors the defensive
    shape of `load_state_from` and `next_up_context._load_soak_registry`; kept
    self-contained (no import of `next_up_context`) so the oracle stays a leaf.
    """
    if not path.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, Exception):
        return []
    if not isinstance(data, dict):
        return []
    soaks = data.get("soaks")
    return soaks if isinstance(soaks, list) else []


def load_soaks() -> list[dict]:
    """Default loader — parses the active workspace's _soaks/index.yaml."""
    return load_soaks_from(_soaks_path())


# ---------------------------------------------------------------------------
# Pure-fn core
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Normalize a plan or phase token for comparison.

    Registry rows write plan ids upper-case (`IF`, `AAR`) but callers may
    pass either case; phase ids are case-preserved on disk but operator
    inputs may not be. Strip + lower for the match key — same shape both
    sides see.
    """
    return (s or "").strip().lower()


def _norm_doc(path: str) -> str:
    """Normalize a plan-doc path for comparison: posix slashes, lower-case,
    basename-or-relative tolerant. Two docs are "the same doc" iff their
    normalized full path OR their normalized basename matches — callers pass a
    repo-relative path, registry rows may carry either form."""
    p = (path or "").strip().replace("\\", "/").lower()
    return p


def _doc_paths_match(row_doc: str, expected_doc: str) -> bool:
    """True iff a registry row's `doc_path` names the SAME plan doc the caller
    expects. Compares normalized full path first, then basename — so a row that
    stored `docs/_plans/x-plan.md` matches an expected `docs/x-plan.md` only
    when the basenames agree, which is the load-bearing discriminator (two
    plans sharing a series id always have DIFFERENT doc basenames)."""
    a, b = _norm_doc(row_doc), _norm_doc(expected_doc)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


# The dispatch *ledger* files: pure bookkeeping that routing / status / soft-claim
# commits stamp constantly but that no phase "ships". A commit touching ONLY
# these (no plan doc, no code, no test, no substantive doc) is a routing stamp,
# never a ship — used by `default_commit_touches_doc`'s Signal C to demote the
# FQ-388 "routing-stamp row short-circuits as shipped" class. Matched on basename
# (findings queue, execution-state ledger, the rendered plan index).
# The kernel's OWN generic-layout bookkeeping basenames come FIRST — `for_dos_dir`
# ships `dos.state.yaml` (execution_state) + `dos.findings.md` (findings_queue), so a
# generic-layout workspace gets the same non-substantive-footprint demotion the
# reference app does (the bug the userland-coupling audit 2026-06-08 found: this set
# used to carry ONLY the reference app's filenames, so a generic workspace's
# bookkeeping commits were never demoted). The reference-app dialect
# (`execution-state.yaml` / `findings-followup-queue.md` / `plans.yaml`) is kept for
# back-compat; a host with a different layout should ideally have these DERIVED from
# `cfg.paths.{execution_state,findings_queue}` rather than enumerated here — left as a
# follow-up because the predicate is a pure module-level helper threaded through the
# truth syscall (see the audit's MEDIUM note).
_DISPATCH_LEDGER_BASENAMES = frozenset({
    # generic kernel layout (dos.config.PathLayout.for_dos_dir)
    "dos.state.yaml",
    "dos.findings.md",
    # reference-app dialect (back-compat)
    "findings-followup-queue.md",
    "execution-state.yaml",
    "plans.yaml",
})


def _is_dispatch_ledger(path: str) -> bool:
    """True iff `path` is a pure dispatch-bookkeeping ledger file (see
    `_DISPATCH_LEDGER_BASENAMES`). Basename match — these live at known paths
    but compare on basename for robustness to repo-relative vs absolute forms."""
    base = _norm_doc(path).rsplit("/", 1)[-1]
    return base in _DISPATCH_LEDGER_BASENAMES


# The version-bump *release* files: the small fixed set of paths a `/release`
# version-cut touches when it bumps the version and writes release notes. A
# commit touching ONLY these (no plan doc, no code, no test, no substantive
# doc) is a release-bump, never a phase ship — even though its *subject* /
# release-notes body routinely names the phases batched into that version
# (`v0.378.0: GBA6 soak registration + FQ-375 live-API closer + …`). The grep
# rung in `check_phase_shipped.py` matches such a commit when the queried
# phase token appears in the subject / release-notes prose, false-flagging an
# UNSHIPPED phase as shipped (finding #399 — the bookkeeping-only-diff predicate
# gap; recurred 3× in <2h on 2026-05-30, the `ship_oracle_false_positive`
# unstick cluster #336). This set mirrors the version files `/release` Step 7
# writes; `docs/releases/` and `docs/06_implementation-status.md` are the
# release-notes + status-rollup surfaces it also stamps. Matched basename-wise
# for `VERSION`/`version.txt`/`pyproject.toml`/`__init__.py`, prefix-wise for
# the release-notes / status dirs (one-definition, two callers — registry
# Signal C + the grep-side post-filter).
_RELEASE_BUMP_BASENAMES = frozenset({
    "version",            # VERSION (root) / go buildinfo VERSION
    "version.txt",        # go/internal/buildinfo/version.txt
    "pyproject.toml",     # version field bump
    "__init__.py",        # job_search/__init__.py __version__
    "changelog.md",       # CHANGELOG.md, if present
})
# `docs/releases/` is the generic /release notes surface (this repo's own, and the
# kernel's `/release` skill writes it). A host that re-stamps an extra status-rollup
# path on a version cut (e.g. the reference app's `docs/06_implementation-status`)
# declares it in its own config rather than baking the host path into the kernel
# oracle — the reaped host literal lived here (userland-coupling audit 2026-06-08).
_RELEASE_BUMP_PREFIXES = (
    "docs/releases/",                 # docs/releases/vX.Y.Z.md release notes
)


def _is_release_bump_path(path: str) -> bool:
    """True iff `path` is one of the fixed version-bump / release-notes files a
    `/release` version-cut touches (see `_RELEASE_BUMP_BASENAMES` /
    `_RELEASE_BUMP_PREFIXES`). Used to recognise a commit whose footprint is
    *only* a release bump — which names batched phases in its subject/notes but
    ships none of them."""
    norm = _norm_doc(path)
    base = norm.rsplit("/", 1)[-1]
    if base in _RELEASE_BUMP_BASENAMES:
        return True
    return any(norm.startswith(p) for p in _RELEASE_BUMP_PREFIXES)


def _commit_footprint_is_nonsubstantive(touched: Iterable[str]) -> bool:
    """True iff every path in `touched` is a dispatch-ledger OR release-bump
    file — i.e. the commit shipped no plan doc, no code, no test, and no
    substantive doc. Such a commit is a routing stamp or a version cut, never a
    phase ship, regardless of what phase tokens its subject / release-notes
    prose mentions. Empty input is NOT non-substantive (returns False — an
    unknown/empty footprint is not provably bookkeeping; callers treat it
    permissively).

    This is the shared predicate behind both the registry-side Signal C demotion
    (`default_commit_touches_doc`) and the grep-side release-bump post-filter
    (`_grep_verdict_is_release_bump_falsepos`). Pure — takes the touched-file
    set so it is unit-testable without git (finding #399 fix; unstick #336)."""
    paths = [p for p in touched if p and p.strip()]
    if not paths:
        return False
    return all(_is_dispatch_ledger(p) or _is_release_bump_path(p) for p in paths)


# Per-process memo of a commit's touched-file set, keyed by (root, sha). A git
# commit is IMMUTABLE — its SHA names a fixed tree, so the files it touched can
# never change — which makes this the safest possible cache (no mtime/staleness
# concern). It matters because a single `is_shipped` on a shipped=True grep
# verdict fetches the SAME sha's footprint twice (the #399 post-filter in
# `default_grep_fallback_batch`, then again in `_demote_if_false_positive`), and a
# fan-out over many phases re-hits the same release-bump SHAs repeatedly; each was
# a `git show` subprocess. Keyed on the workspace root too, so a server fielding
# several workspaces never crosses their histories. `None` results are NOT cached
# (a transient git failure should be retried, never frozen into a false miss).
_TOUCHED_FILES_CACHE: "dict[tuple[str, str], frozenset[str]]" = {}


def _clear_touched_files_cache() -> None:
    """Drop the per-process touched-files memo (test hook)."""
    _TOUCHED_FILES_CACHE.clear()


def _git_touched_files(sha: str, *, timeout: int = 15) -> set[str] | None:
    """Return the set of repo-relative paths a commit touched, or None if the
    sha is unknown / git is unavailable / the clone is shallow (caller treats
    None permissively — we never manufacture a false negative from an
    unresolvable sha).

    Memoized per process on (workspace-root, sha) — a commit's footprint is
    immutable, so the `git show` runs once per distinct SHA for the process's
    life (see `_TOUCHED_FILES_CACHE`). A copy of the cached frozenset is returned
    so a caller that mutates the result can't poison the cache."""
    sha = (sha or "").strip()
    if not sha:
        return None
    root = str(_workspace_root())
    cache_key = (root, sha)
    cached = _TOUCHED_FILES_CACHE.get(cache_key)
    if cached is not None:
        return set(cached)
    try:
        res = subprocess.run(
            ["git", "show", "--name-only", "--format=", sha],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    files = {
        ln.strip().replace("\\", "/")
        for ln in res.stdout.splitlines()
        if ln.strip()
    }
    _TOUCHED_FILES_CACHE[cache_key] = frozenset(files)
    return files


def _grep_verdict_is_release_bump_falsepos(
    verdict: "ShipVerdict",
    *,
    touched_files: Callable[[str], set[str] | None] = _git_touched_files,
) -> bool:
    """True iff a grep-derived `shipped=True` verdict rests on a commit whose
    footprint is *only* a release-bump / dispatch-ledger (the finding #399
    false-positive). The grep rung in `check_phase_shipped.py` has no
    footprint guard — it matches a phase token anywhere in the commit subject
    or release-notes body — so a version-cut that batches `… + FQ-375 closer +
    …` in its notes false-flags an unshipped FQ-375 as shipped. This is the
    grep-side complement of the registry-side Signal C.

    Only fires when (a) the verdict claims shipped, (b) it carries a resolvable
    sha, and (c) that commit's touched-file set is provably non-substantive.
    An unresolvable sha (None footprint) is permissive → returns False (we do
    NOT demote a verdict we cannot prove is a release bump). `touched_files`
    is injectable so tests run without git."""
    if not verdict.shipped:
        return False
    sha = (verdict.sha or "").strip()
    if not sha:
        return False
    touched = touched_files(sha)
    if not touched:
        return False  # unresolvable / empty → permissive, do not demote
    return _commit_footprint_is_nonsubstantive(touched)


# Markers stamped onto a demoted verdict's `summary` so a downstream log /
# audit can name *which* false-positive class fired. Kept module-level so the
# in-fallback site (`default_grep_fallback_batch`) and the oracle-boundary
# sites (`is_shipped` / `batch_is_shipped`) all stamp the identical string.
_RELEASE_BUMP_DEMOTE_MARKER = (
    "[ship_oracle: demoted — grep matched a release-bump/ledger-only commit "
    "whose footprint ships no phase content (#399)]"
)
# The operator-facing demote markers name the soak index by its ACTUAL configured
# path (`{soaks}` is interpolated from `cfg.paths.soaks_index` at the call site),
# never a hardcoded host literal — the generic default is `.dos/soaks/index.yaml`,
# the reference app's is `docs/_soaks/index.yaml`; the message must match the
# workspace's real location (userland-coupling audit 2026-06-08).
_SOAK_DEMOTE_MARKER = (
    "[ship_oracle: demoted — (plan, phase) is a registered in-progress soak in "
    "{soaks}; a commit names the soak token but the pick is the "
    "soak follow-up, not a re-ship (#326)]"
)
_SOAK_REGISTRY_DEMOTE_MARKER = (
    "[ship_oracle: demoted — (plan, phase) has a registry status:done row but its "
    "soak is still in_progress in {soaks}; the implementation "
    "shipped, but under soak-as-parallel-lane the pick the lane wants is the soak "
    "FOLLOW-UP, not the shipped impl — so the picker treats it as not-yet-done "
    "until the soak window closes (#326-registry)]"
)


def _alnum(s: str) -> str:
    """Aggressive token key: lower-case, strip every non-alphanumeric char.

    Soak ids on disk are inconsistently delimited — `dt5-discover-wallclock`
    (hyphen), `cs6_legacy_marker_classify` (underscore), `tf11_4_1` (a dotted
    phase rendered with underscores), `gba6` (bare). Reducing both the phase
    and the candidate id-tokens to alnum makes the comparison delimiter-blind.
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def _is_registered_inprogress_soak(
    plan: str,
    phase: str,
    *,
    soaks: Iterable[dict],
) -> bool:
    """True iff `(plan, phase)` is a registered, **in-progress** soak (#326).

    A soak whose implementation has shipped but whose soak window is still open
    is, by the soak-as-parallel-lane convention, a *pickable follow-up* — not a
    drained/shipped-done phase. But the grep rung false-flags it as shipped the
    moment any commit (a release note, a soak-ledger stamp) names the phase
    token, which culls the live soak-window follow-up pick and WEDGEs the lane.
    This predicate lets `is_shipped` / `batch_is_shipped` suppress that
    grep-derived shipped verdict for the duration of the in-progress window.

    Matching (delimiter-blind, boundary-safe). A soak entry matches iff:
      * its `plan` equals the queried plan (alnum-normalized — disk casing is
        inconsistent: `dt`, `PSC`, `gba` all appear), AND
      * its `status` is `in_progress` (the ONLY status that suppresses — once
        the operator flips it to passed/closed_pass/failed/aborted the phase
        reports shipped normally again), AND
      * the queried phase, alnum-normalized, EQUALS one of the entry-id
        candidate tokens: the leading run before the first `-`, the leading run
        before the first `_` of that, or the whole id (each alnum-normalized).

    The exact-equality-against-leading-token rule is what prevents over-match:
    a query for `DT50` does not match soak `dt5-discover-wallclock` (its tokens
    are `dt5`/`dt5discoverwallclock`, neither equals `dt50`), and `DT5` does not
    match a hypothetical `dt55-…`. Pure — takes the parsed soak list injected,
    so it is unit-testable with no file I/O (mirrors `touched_files` injection).
    """
    plan_n = _alnum(plan)
    phase_n = _alnum(phase)
    if not plan_n or not phase_n:
        return False
    for entry in soaks:
        if not isinstance(entry, dict):
            continue
        if (entry.get("status") or "").strip().lower() != "in_progress":
            continue
        if _alnum(str(entry.get("plan", ""))) != plan_n:
            continue
        sid = str(entry.get("id", "")).strip().lower()
        if not sid:
            continue
        lead = sid.split("-", 1)[0]          # 'dt5' from 'dt5-discover-wallclock'
        lead_us = lead.split("_", 1)[0]      # 'cs6' from 'cs6_legacy_marker_classify'
        candidates = {_alnum(lead), _alnum(lead_us), _alnum(sid)}
        if phase_n in candidates:
            return True
    return False


def _demote_if_false_positive(
    verdict: "ShipVerdict",
    *,
    grep_touched_files: Callable[[str], set[str] | None] | None,
    soaks: Iterable[dict] | None,
) -> "ShipVerdict":
    """Apply the two grep-side false-positive demotions to a `source='grep'`
    verdict, gated on their respective inputs being supplied (the gate-OFF
    convention: a `None` input means "this check is off → byte-identical").

    Returns the verdict unchanged unless a check fires, in which case it returns
    a `shipped=False` copy with the matching `#399` / `#326` marker appended to
    `summary`. Both checks are monotone `shipped=True → shipped=False`, so they
    compose; the markers are distinct so a log can name which fired. Only the
    grep rung is demoted — a registry `status: done` hit is real ship truth and
    is never routed through this helper.
    """
    if not verdict.shipped:
        return verdict
    if grep_touched_files is not None and _grep_verdict_is_release_bump_falsepos(
        verdict, touched_files=grep_touched_files
    ):
        return dataclasses.replace(
            verdict,
            shipped=False,
            summary=(verdict.summary + " " if verdict.summary else "")
            + _RELEASE_BUMP_DEMOTE_MARKER,
        )
    if soaks is not None and _is_registered_inprogress_soak(
        verdict.plan, verdict.phase, soaks=soaks
    ):
        return dataclasses.replace(
            verdict,
            shipped=False,
            summary=(verdict.summary + " " if verdict.summary else "")
            + _SOAK_DEMOTE_MARKER.format(soaks=_soaks_path()),
        )
    return verdict


def _suppress_registry_soak(
    verdict: "ShipVerdict",
    *,
    soaks: Iterable[dict] | None,
) -> "ShipVerdict":
    """Demote a REGISTRY `shipped=True` verdict whose phase is a registered
    in-progress soak (#326-registry).

    The grep-side `_demote_if_false_positive` deliberately does NOT touch a
    registry hit — a `status: done` row means the *implementation* genuinely
    shipped, which is real ship truth the oracle must not erase. But under the
    soak-as-parallel-lane model (`next_up_context.collect_soaks`,
    [[soak-as-parallel-lane]]), a phase whose impl shipped while its soak window
    is still open is a *pickable follow-up*, not a drained/done phase: the work
    the lane wants next is the soak's named follow-up, not a re-ship of the impl.
    Reporting it `shipped=True` makes the picker drop the pick and re-render —
    the exact soak-gated WEDGE the operator hit (PIW2/CS6/AFR6/IF3 all carried a
    `status:done` row while in_progress).

    So for the picker's purpose we suppress the registry-soak verdict *for the
    duration of the in-progress window only*. This is bounded and self-healing:
    the instant the operator flips the soak to passed/closed_pass/failed/aborted
    the suppression stops and the phase reports shipped from its registry row
    again. Opt-in via `soaks` (None → OFF → byte-identical, the gate-OFF
    convention); the demoted verdict keeps `source='registry'` and carries the
    distinct `#326-registry` marker so a log names the cause. Pure — injected
    soak list, no I/O."""
    if not verdict.shipped or soaks is None:
        return verdict
    if _is_registered_inprogress_soak(verdict.plan, verdict.phase, soaks=soaks):
        return dataclasses.replace(
            verdict,
            shipped=False,
            summary=(verdict.summary + " " if verdict.summary else "")
            + _SOAK_REGISTRY_DEMOTE_MARKER.format(soaks=_soaks_path()),
        )
    return verdict


# The plan-meta series-id + classification fields, read from a plan doc's
# `<!-- plan-meta … -->` frontmatter block (`id: ISV` / `classification: ACTIVE`).
# The id is the key the run registry stamps a `recently_completed` row on; the
# classification is what lets `default_plan_doc_map` prefer the LIVE plan when two
# docs collide on one id — the join the FQ-390 gate needs to know which doc a
# queried plan EXPECTS.
_PLAN_META_ID_RE = re.compile(r"^\s*id:\s*([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)
_PLAN_META_CLASS_RE = re.compile(r"^\s*classification:\s*([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)

# A classification that means the plan is NOT the live one an operator querying its
# id means — a retired/superseded doc that may still carry a same-id `done` row.
# Matched case-insensitively against the plan-meta `classification:` field.
_TOMBED_CLASSIFICATIONS = frozenset({"tombed", "tombstone", "retired", "superseded", "archived"})


def default_plan_doc_map(cfg: "_config.SubstrateConfig | None" = None) -> dict[str, str]:
    """Resolve ``{series_id: plan_doc_path}`` from the workspace's plan docs. FAIL-SAFE.

    Globs ``cfg.paths.plans_glob`` (the declared plan location), reads each doc's
    ``<!-- plan-meta … id: <SERIES> … -->`` frontmatter, and maps that series id to
    the doc's repo-relative path. This is the map the FQ-390 collision gate consults
    to learn which doc a queried plan EXPECTS — so a registry `status:done` row
    whose ship commit touched a DIFFERENT plan's doc (a tombed same-id plan) is
    rejected instead of false-clearing the active plan's unshipped phase.

    **Collision preference — the LIVE doc wins.** When two docs share one id (the
    exact PLAN_ID_COLLISION_FALSE_SHIPPED scenario: an ACTIVE `dispatch-lane-canon`
    plan and a TOMBED `data-labelling` plan both `id: DL`), an operator querying
    that id means the LIVE plan, so a non-tombed doc is preferred over a tombed one
    regardless of glob order. With the live doc as `expected_doc`, a registry row
    that points at the TOMBED doc fails `_doc_paths_match` and is correctly skipped.
    Among same-classification docs the sorted-glob first wins (deterministic).

    Every failure path yields an EMPTY map (a missing glob, an unreadable tree, a
    doc with no plan-meta id, a parse error) — and an empty map turns the gate OFF,
    byte-identical to the pre-wiring behavior (`expected_doc is None`). So wiring
    this in can only ADD collision rejections where the workspace genuinely has
    plan docs with series ids; it never changes the answer in a repo with no plans
    (the no-plan contract) or a host that doesn't stamp series ids. The single I/O
    is this glob+read at the call boundary, exactly like `plan_source` — never
    inside a pure verdict.

    Reuses `plan_source`'s harvest discipline conceptually but reads the *series id*
    (the registry key) from frontmatter, not the markdown heading token (the
    plan-view label) — a different join than `plan_source` needs, so it is its own
    small reader rather than a shared one.
    """
    cfg = _config.ensure(cfg)
    out: dict[str, str] = {}
    chosen_is_tombed: dict[str, bool] = {}  # id → did the doc we currently picked tomb?
    try:
        paths = getattr(cfg, "paths", None)
        root = Path(getattr(paths, "root", "."))
        glob = str(getattr(paths, "plans_glob", "") or "")
        if not glob:
            return out
        matched = sorted(root.glob(glob))
    except (OSError, ValueError):
        return out
    for p in matched:
        try:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _PLAN_META_ID_RE.search(text)
        if not m:
            continue
        series_id = m.group(1)
        cm = _PLAN_META_CLASS_RE.search(text)
        is_tombed = bool(cm) and cm.group(1).strip().lower() in _TOMBED_CLASSIFICATIONS
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        # Bind the id to this doc when (a) the id is unseen, or (b) we previously
        # picked a TOMBED doc and this one is LIVE — the live plan an operator
        # querying the id means. Two LIVE (or two tombed) docs keep the sorted-glob
        # first, which is deterministic; a genuine live-vs-live id collision is a
        # workspace bug the commit-footprint check still guards downstream.
        if series_id not in out or (chosen_is_tombed.get(series_id) and not is_tombed):
            out[series_id] = rel
            chosen_is_tombed[series_id] = is_tombed
    return out


def _registry_ship_row(
    state: dict,
    plan: str,
    phase: str,
    *,
    expected_doc: str | None = None,
    commit_touches_doc: Callable[[str, str, str], bool | None] | None = None,
) -> dict | None:
    """Return the most recent terminal-success `recently_completed` row for
    `(plan, phase)`, or None if no such row exists (or every candidate row is
    rejected as a cross-plan series collision — see `expected_doc`).

    "Most recent" means the first match in iteration order — `fanout_state`
    `_dump`s `recently_completed` with newest first (it `insert(0, entry)`s
    on every `mark done`), so the first hit is the latest ship.

    Only `status: done` rows count. `failed` / `stalled` / `expired` are
    terminal-not-success and must NOT be reported as ships — the test suite
    locks this contract.

    **Plan-id-collision disambiguation (`expected_doc`, opt-in, FQ-390).** Two
    DIFFERENT plans can share a series id (an ACTIVE `dispatch-lane-canonicalization`
    plan and a TOMBED data-labelling plan both register `id: DL`). The registry
    keys only on `(plan, phase)`, so a `DL/DL2 status=done` row written by the
    tombed plan was reported as a ship of the ACTIVE plan's genuinely-unshipped
    DL2 — culling the live pick and wedging `/dispatch-loop` (the
    `PLAN_ID_COLLISION_FALSE_SHIPPED` class). When `expected_doc` is supplied,
    a matched row must be proven to belong to the SAME plan doc before it counts:

      * row carries a `doc_path` → it must `_doc_paths_match(expected_doc)`;
        a mismatch is a collision → skip and keep scanning.
      * row carries NO `doc_path` (all legacy rows) → if a
        `commit_touches_doc(sha, expected_doc, phase)` callback is supplied,
        the row's `commit_sha` must have touched ≥1 of the *expected* plan's
        files (the callback returns True/False/None). A definite False is a
        collision → skip. None (can't tell — no sha, no callback, or git
        unavailable) is **permissive → keep**, preserving the deliberate
        direct-ship trust (stamp drift is a known operator habit; a real
        unstamped ship touched its OWN plan's files and must stay shipped).

    When `expected_doc is None` the gate is OFF and behavior is byte-identical
    to the pre-FQ-390 lookup — every existing caller and test is unaffected.
    """
    plan_n, phase_n = _norm(plan), _norm(phase)
    rows = state.get("recently_completed")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _norm(row.get("plan", "")) != plan_n:
            continue
        if _norm(row.get("phase", "")) != phase_n:
            continue
        if (row.get("status") or "").strip().lower() != "done":
            continue
        if expected_doc:
            row_doc = str(row.get("doc_path") or "")
            if row_doc:
                # The row knows its own plan doc — trust it directly.
                if not _doc_paths_match(row_doc, expected_doc):
                    continue  # cross-plan collision — not this plan's ship
            elif commit_touches_doc is not None:
                # Legacy row (no doc_path): verify the ship commit's footprint
                # against the expected plan's files. Only a DEFINITE miss skips.
                sha = str(row.get("commit_sha") or "")
                if sha:
                    touched = commit_touches_doc(sha, expected_doc, phase)
                    if touched is False:
                        continue  # commit touched none of the expected plan's files
        return row
    return None


def is_shipped(
    plan: str,
    phase: str,
    *,
    cfg: "_config.SubstrateConfig | None" = None,
    state: dict | None = None,
    grep_fallback: Callable[[str, str], ShipVerdict] | None = None,
    expected_doc: str | None = None,
    commit_touches_doc: Callable[[str, str, str], bool | None] | None = None,
    grep_touched_files: Callable[[str], set[str] | None] | None = None,
    soaks: Iterable[dict] | None = None,
    non_git_rung: "NonGitRung | None" = None,
) -> ShipVerdict:
    """Registry-first lookup. Pure given `state` + `grep_fallback`.

    Resolution order:

      1. `recently_completed` row with `status: done` → `source='registry'`,
         SHA from the row's `commit_sha` field if present.
      2. Otherwise call `grep_fallback(plan, phase)` → its verdict, with
         `source` set to `'grep'` (the fallback's `source` field is ignored;
         we own the label so callers can tell which gate answered).
      3. If no `grep_fallback` is supplied and the registry misses, return
         `shipped=False` with `source='none'` — the conservative default.

    `expected_doc` / `commit_touches_doc` (opt-in, FQ-390) disambiguate a
    cross-plan series collision in the registry — see `_registry_ship_row`. A
    row rejected as a collision falls through to the grep fallback (which runs
    its own doc-aware cross-check) exactly as a registry miss would.

    `grep_touched_files` (opt-in, #399) and `soaks` (opt-in, #326) demote a
    known false-positive `shipped=True`:
      * `grep_touched_files` demotes a GREP verdict resting on a release-bump/
        ledger-only commit (#399).
      * `soaks` demotes BOTH a grep verdict AND a registry `status:done` hit
        whose phase is a registered in-progress soak — for a grep hit the commit
        merely *names* the soak token (#326); for a registry hit the impl
        genuinely shipped but the pick the lane wants is the soak FOLLOW-UP, not
        a re-ship, so during the open window the picker treats it as not-yet-done
        (#326-registry; see `_suppress_registry_soak`).
    Both default `None` → the check is OFF → byte-identical to the pre-fix
    behavior (the `expected_doc is None` gate-OFF convention). The registry-soak
    suppression is bounded by `status: in_progress` and self-heals the instant
    the operator closes the soak.

    `non_git_rung` (opt-in, docs/265) is an already-gathered non-git evidence
    verdict (a `NonGitRung`: a label + reason + the rung's state word) the
    `cmd_verify` boundary resolves from a `dos.evidence_sources` driver and hands in.
    It is applied ONLY to a `shipped=True` git verdict and ONLY conjunctively (see
    `_apply_non_git_rung`): GREEN upgrades `source` to `non_git_rung.source`
    (`"ci-green"`); RED withholds the upgrade and flags the verdict; NO_SIGNAL /
    PENDING / unknown pass the git verdict through byte-identical. It can NEVER
    promote `shipped=False → True` — green CI without a reachable commit
    manufactures nothing (the §1 safety invariant). Default `None` → OFF →
    byte-identical (no provider type ever enters this module; the datum is gathered
    at the boundary, the arbiter/`git_delta` rule).

    `cfg` (opt-in) is the library convenience for "verify against THIS workspace":
    pass a `SubstrateConfig` and the loader fills the two I/O hooks from it when
    the caller didn't supply them — `state` from `cfg.state_path()` and a default
    git-log `grep_fallback` resolved against `cfg`'s root. This is what lets
    `is_shipped(plan, phase, cfg=cfg)` answer from git history alone in a repo
    with no registry (the no-plan contract, `tests/test_verify_no_plan.py`): the
    registry miss falls through to the grep rung instead of a bare `source='none'`.
    Passing `cfg` alongside an explicit `state`/`grep_fallback` leaves those
    explicit values untouched (the caller wins); omitting `cfg` is byte-identical
    to before — the pure, host-agnostic core.

    Tests inject `state` directly; the production callers pass `state=load_state()`.
    """
    if cfg is not None:
        # Wire the two I/O hooks from the workspace config, but only the ones the
        # caller left open. The grep rung reads `_config.active()` for its root, so
        # install `cfg` as active for the lookup and restore afterward — keeping
        # this convenience free of any global side effect once it returns.
        _prev_active = _config.active()
        _config.set_active(cfg)
        try:
            if state is None:
                state = load_state_from(cfg.state_path())
            if grep_fallback is None:
                grep_fallback = default_grep_fallback_single
            # FQ-390 — turn the plan-id-collision gate ON by DEFAULT for every
            # cfg-passing caller (the CLI `dos verify`, the MCP `dos_verify`, the
            # `dispatch_top`/`plan_board` fan-outs). Resolve the queried plan's
            # expected doc from the workspace's plan-meta ids and default the
            # footprint verifier, but ONLY where the caller left them open — an
            # explicit value still wins. Both resolutions are FAIL-SAFE (an empty
            # doc-map / unresolved plan ⇒ expected_doc stays None ⇒ gate OFF ⇒
            # byte-identical to before), so this can only ADD collision rejections
            # in a workspace that actually has same-id plan docs; it never changes
            # the no-plan answer. Without this, the gate was built but every shipped
            # verify surface opted out, so a tombed same-id plan's `done` row
            # false-cleared the active plan's unshipped phase.
            if expected_doc is None:
                doc_map = default_plan_doc_map(cfg)
                expected_doc = doc_map.get(plan) or doc_map.get(str(plan).upper())
            if commit_touches_doc is None and expected_doc:
                commit_touches_doc = default_commit_touches_doc
            # #326 — the soak false-positive demotion, ON by default for every
            # cfg-passing caller (same built-but-unwired class as FQ-390 above). A
            # phase whose impl shipped but is in an OPEN soak window is not the pick
            # the lane wants (the pick is the soak FOLLOW-UP), so a registry/grep
            # `done` hit during the window must read as not-yet-done — else `dos
            # verify` / MCP / the projections report a soaking phase shipped and the
            # picker culls the live follow-up. Load the workspace's soak index
            # fail-safe (a missing/!PyYAML/malformed index → [] → suppresses nothing
            # → byte-identical to before, so the no-plan/no-soak answer is unchanged)
            # and only where the caller left `soaks` open — an explicit value wins.
            # Bounded by `status: in_progress`; self-heals the instant the operator
            # closes the soak.
            if soaks is None:
                soaks = load_soaks_from(cfg.paths.soaks_index)
            # #399 — the release-bump / ledger-only grep false-positive demotion
            # needs the touched-files footprint to fire; default it ON for every
            # cfg-passing caller, the same way commit_touches_doc/soaks above are
            # defaulted (it was the one demotion hook the convenience branch left
            # open, so `dos verify` / MCP / the TUI projections inherited a grep
            # release-prefix false-ship the picker path already demotes). FAIL-SAFE:
            # `_git_touched_files` returns None on any git failure ⇒ the boundary
            # gate stays OFF ⇒ byte-identical to before; an explicit value wins.
            if grep_touched_files is None:
                grep_touched_files = _git_touched_files
            verdict = is_shipped(
                plan, phase,
                state=state,
                grep_fallback=grep_fallback,
                expected_doc=expected_doc,
                commit_touches_doc=commit_touches_doc,
                grep_touched_files=grep_touched_files,
                soaks=soaks,
                non_git_rung=non_git_rung,
            )
            # `source` names the gate that CONFIRMED a ship. A negative answer
            # carries `source='none'` whether the grep rung looked or not — the
            # no-plan contract is "no positive evidence", not "grep said no". (The
            # pure path keeps the fallback's own `source='grep'` on a miss for
            # back-compat; only this convenience surface normalizes it.)
            if not verdict.shipped and verdict.source != "none":
                verdict = dataclasses.replace(verdict, source="none")
            return verdict
        finally:
            _config.set_active(_prev_active)
    if state is None:
        state = load_state()
    row = _registry_ship_row(
        state, plan, phase,
        expected_doc=expected_doc, commit_touches_doc=commit_touches_doc,
    )
    if row is not None:
        sha = row.get("commit_sha") or None
        verdict = ShipVerdict(
            plan=plan,
            phase=phase,
            shipped=True,
            sha=str(sha)[:12] if sha else None,
            source="registry",
        )
        verdict = _suppress_registry_soak(verdict, soaks=soaks)
        # The non-git rung is folded LAST — only after every demotion has settled the
        # final `shipped` bit (a soak-suppressed registry hit is `shipped=False`, so
        # the conjunctive rung is a no-op on it; the §1 invariant). Conjunctive only:
        # it upgrades/withholds a real ship, never manufactures one.
        return _apply_non_git_rung(verdict, non_git_rung)
    if grep_fallback is None:
        return ShipVerdict(plan=plan, phase=phase, shipped=False, source="none")
    fb = grep_fallback(plan, phase)
    verdict = ShipVerdict(
        plan=fb.plan,
        phase=fb.phase,
        shipped=fb.shipped,
        sha=fb.sha,
        # Preserve a forgeability-graded source the default fallback set
        # (`grep-artifact`/`grep-subject`, docs/118); stamp bare `grep` otherwise
        # (incl. an injected stub) so the long-standing `source='grep'` contract
        # holds. Carry the raw `rung` through too.
        source=_restamp_grep_source(fb.source),
        summary=fb.summary,
        rung=fb.rung,
    )
    verdict = _demote_if_false_positive(
        verdict, grep_touched_files=grep_touched_files, soaks=soaks
    )
    # Fold the non-git rung LAST, after the #399/#326 grep demotions have settled the
    # final `shipped` bit — a demoted grep verdict is `shipped=False`, so the rung is a
    # no-op (conjunctive: it can only upgrade/withhold a still-standing ship).
    return _apply_non_git_rung(verdict, non_git_rung)


def batch_is_shipped(
    pairs: Iterable[tuple[str, str]],
    *,
    state: dict | None = None,
    grep_fallback: Callable[[list[tuple[str, str]]], dict[tuple[str, str], ShipVerdict]] | None = None,
    plan_doc_map: dict[str, str] | None = None,
    commit_touches_doc: Callable[[str, str, str], bool | None] | None = None,
    grep_touched_files: Callable[[str], set[str] | None] | None = None,
    soaks: Iterable[dict] | None = None,
    non_git_rungs: "dict[tuple[str, str], NonGitRung] | None" = None,
) -> dict[tuple[str, str], ShipVerdict]:
    """Many-pair variant. Registry hits short-circuit; the registry misses are
    passed to `grep_fallback` in one batched call, so the worst case is still
    one subprocess (matching the existing `check_phase_shipped --batch` shape).

    Keyed by the *original* (plan, phase) tuple the caller passed (case
    preserved). Values are `ShipVerdict`s. Pairs the caller passed but neither
    the registry nor the fallback resolved get a `shipped=False, source='none'`
    placeholder so the returned dict matches `pairs` 1:1.

    `plan_doc_map` (series-id → expected plan-doc path) + `commit_touches_doc`
    (opt-in, FQ-390) disambiguate a cross-plan series collision in the registry
    — see `_registry_ship_row`. A row rejected as a collision is treated as a
    registry MISS for that pair, so it falls through to the doc-aware grep
    fallback (which the production caller already feeds the same `plan_doc_map`).
    When `plan_doc_map` is None the gate is OFF — byte-identical to the
    pre-FQ-390 lookup.

    `grep_touched_files` (opt-in, #399) and `soaks` (opt-in, #326) demote
    false-positive `shipped=True` exactly as `is_shipped` does — `#399` on a
    grep verdict resting on a release-bump/ledger-only commit, and `#326` on a
    registered in-progress soak phase via EITHER source (grep token-name, or a
    registry `status:done` row whose soak window is still open — the pick is the
    soak follow-up). Both default `None` → OFF → byte-identical.

    `non_git_rungs` (opt-in, docs/265) is a PER-PAIR map `{(plan,phase): NonGitRung}`
    of already-gathered non-git evidence — per-pair because each pair resolves to a
    DIFFERENT commit, so a single batch CI verdict could not apply (the single-pair
    `is_shipped` takes one `non_git_rung`; the batch keys it the same way it keys
    everything else). Each rung is folded conjunctively onto its pair's FINAL git
    verdict (after every #399/#326 demotion has settled the `shipped` bit) by
    `_apply_non_git_rung`: GREEN upgrades `source`, RED withholds + flags,
    NO_SIGNAL/PENDING pass through — and it can never promote `shipped=False → True`.
    A pair with no entry is untouched; `None` → OFF → byte-identical.
    """
    if state is None:
        state = load_state()
    doc_map = plan_doc_map or {}
    pair_list = [(p, ph) for (p, ph) in pairs if p and ph]
    out: dict[tuple[str, str], ShipVerdict] = {}
    misses: list[tuple[str, str]] = []
    for plan, phase in pair_list:
        row = _registry_ship_row(
            state, plan, phase,
            expected_doc=doc_map.get(plan) or doc_map.get(plan.upper()) if doc_map else None,
            commit_touches_doc=commit_touches_doc,
        )
        if row is not None:
            sha = row.get("commit_sha") or None
            reg_verdict = ShipVerdict(
                plan=plan,
                phase=phase,
                shipped=True,
                sha=str(sha)[:12] if sha else None,
                source="registry",
            )
            out[(plan, phase)] = _suppress_registry_soak(reg_verdict, soaks=soaks)
        else:
            misses.append((plan, phase))
    if misses and grep_fallback is not None:
        fb_results = grep_fallback(misses)
        for key in misses:
            fb = fb_results.get(key)
            if fb is None:
                out[key] = ShipVerdict(plan=key[0], phase=key[1], shipped=False, source="none")
            else:
                verdict = ShipVerdict(
                    plan=fb.plan,
                    phase=fb.phase,
                    shipped=fb.shipped,
                    sha=fb.sha,
                    # Preserve the forgeability grade the default fallback set
                    # (`grep-artifact`/`grep-subject`, docs/118); bare `grep`
                    # otherwise. Carry the raw `rung` through.
                    source=_restamp_grep_source(fb.source),
                    summary=fb.summary,
                    rung=fb.rung,
                )
                out[key] = _demote_if_false_positive(
                    verdict, grep_touched_files=grep_touched_files, soaks=soaks
                )
    else:
        for key in misses:
            out[key] = ShipVerdict(plan=key[0], phase=key[1], shipped=False, source="none")
    # Fold the per-pair non-git rungs LAST, over the fully-settled verdicts (a single
    # pass covers both registry hits and grep results). Conjunctive + a no-op on any
    # `shipped=False` verdict, so a `source='none'` placeholder or a demoted hit is
    # never touched — the §1 invariant, applied uniformly across the batch.
    if non_git_rungs:
        for key, verdict in list(out.items()):
            rung = non_git_rungs.get(key)
            if rung is not None:
                out[key] = _apply_non_git_rung(verdict, rung)
    return out


def batch_is_shipped_cfg(
    pairs: Iterable[tuple[str, str]],
    cfg: "_config.SubstrateConfig",
) -> dict[tuple[str, str], ShipVerdict]:
    """Workspace-bound MANY-pair verify — the batch sibling of `is_shipped(cfg=…)`.

    A fan-out caller that verifies a screen-full of `(plan, phase)` picks against
    ONE workspace (the `dispatch_top` / `plan_board` verdict columns) used to call
    `is_shipped(plan, phase, cfg=cfg)` once per pick — and each call rebuilt the
    git-log grep cache from scratch (`default_grep_fallback_single` → a batch of
    one). On a real repo that is N full-repo `git log` + glob walks per frame (~0.5 s
    each); collapsing them to ONE cache build is the whole point of `batch_is_shipped`.

    This wires the SAME workspace defaults `is_shipped(cfg=…)` installs — state from
    `cfg.state_path()`, the FQ-390 plan-doc collision gate, the #326 soak demotion,
    the #399 release-bump demotion — but routes the registry MISSES through the
    BATCHED grep fallback (`default_grep_fallback_batch`, one cache build for all
    misses). The per-pair result is byte-identical to calling `is_shipped(…, cfg=cfg)`
    on each pair, including the negative-`source='none'` normalization; only the cost
    changes (N cache builds → 1). `cfg` is installed active for the lookup and
    restored in a `finally`, the same global-side-effect-free convenience contract.
    """
    pair_list = [(p, ph) for (p, ph) in pairs if p and ph]
    if not pair_list:
        return {}
    _prev_active = _config.active()
    _config.set_active(cfg)
    try:
        state = load_state_from(cfg.state_path())
        doc_map = default_plan_doc_map(cfg)
        commit_touches_doc = default_commit_touches_doc if doc_map else None
        soaks = load_soaks_from(cfg.paths.soaks_index)

        # Mirror `is_shipped(cfg=…)` EXACTLY: that path wires `default_grep_fallback_single`,
        # which calls the grep rung WITHOUT a `plan_doc_map` — the doc map there gates only
        # the REGISTRY collision check (`expected_doc`/`commit_touches_doc` below), never the
        # grep rung's plan-body consultation. Feeding the doc map into the grep fallback would
        # change a grep verdict (e.g. a file-path match the single path never consults), so the
        # batch fallback omits it to stay byte-identical to the per-pick answer.
        def _grep_fallback(misses: list[tuple[str, str]]):
            return default_grep_fallback_batch(misses)

        out = batch_is_shipped(
            pair_list,
            state=state,
            grep_fallback=_grep_fallback,
            plan_doc_map=doc_map,
            commit_touches_doc=commit_touches_doc,
            grep_touched_files=_git_touched_files,
            soaks=soaks,
        )
        # Mirror `is_shipped(cfg=…)`'s negative-source normalization: a non-shipped
        # verdict reports `source='none'` whether the grep rung looked or not (the
        # no-plan contract is "no positive evidence", not "grep said no").
        for key, verdict in list(out.items()):
            if not verdict.shipped and verdict.source != "none":
                out[key] = dataclasses.replace(verdict, source="none")
        return out
    finally:
        _config.set_active(_prev_active)


# ---------------------------------------------------------------------------
# Forgeability grading of the grep rung (docs/118).
#
# `phase_shipped` answers a ship on one of several rungs, emitted as `via`. They
# split cleanly by FORGEABILITY — whether an agent that can write a commit can
# fake the evidence the rung stood on:
#
#   * NON-FORGEABLE: `file-path` — the artefact rung re-derives SHIPPED from the
#     FILES a commit touched (≥2 of the phase's named load-bearing files). A
#     commit cannot lie about which files it changed; this is the diff, not the
#     narration. (`registry` is the other non-forgeable source, handled above —
#     it is a `mark done` write, not a grep verdict.)
#   * FORGEABLE: `direct` / `release-prefix` / `body-mention` / `hyg-slug` /
#     `sub-phase-parent` — every one of these matches a phase token in the commit
#     SUBJECT or BODY, which the agent authored. `git commit --allow-empty -m
#     'docs/X: PHASE — done'` satisfies `direct` with zero code shipped.
#
# This is the SAME split `resume.NONFORGEABLE_RUNGS` already encodes ({file-path,
# registry}) for the resume re-entry mint — but `verify` was throwing the `via`
# field away at the oracle boundary and flattening every rung to a single
# `source='grep'`, so `dos verify` rendered `(via grep)` identically whether the
# ship stood on the diff or on a subject line the agent typed. Grading it is the
# read-side seed of the mediated-apply-gate (docs/118): the gate's job is to run
# the artefact rung over the diff AT WRITE-TIME; this teaches the verdict to at
# least REPORT which rung answered, graded by trust. It is advisory — it changes
# the GRADE of the report, never the moment of control (no write is mediated).
# ---------------------------------------------------------------------------

# The grep rungs whose evidence is the commit ARTEFACT (files touched), not the
# agent-authored subject/body. Mirrors `resume.NONFORGEABLE_RUNGS` (minus
# `registry`, which is not a grep rung). Kept as data so a new artefact rung is a
# one-line addition, never a scattered edit.
_NONFORGEABLE_GREP_RUNGS = frozenset({"file-path"})


def _grade_grep_source(via: str) -> str:
    """Map a raw `via` rung → the graded `source` label for a grep verdict.

    `file-path` (the artefact/diff rung) → ``'grep-artifact'`` (non-forgeable).
    Every subject/body rung (`direct`, `release-prefix`, `body-mention`,
    `hyg-slug`, `sub-phase-parent`) → ``'grep-subject'`` (forgeable — the agent
    authored the text the match stood on). An empty/unknown `via` (a fallback
    that didn't report one) → bare ``'grep'`` so nothing is mis-graded as one
    side or the other. Pure — no I/O, unit-testable."""
    v = (via or "").strip()
    if not v:
        return "grep"
    return "grep-artifact" if v in _NONFORGEABLE_GREP_RUNGS else "grep-subject"


def _restamp_grep_source(fb_source: str) -> str:
    """The `source` to put on a grep verdict the oracle boundary re-stamps.

    `is_shipped`/`batch_is_shipped` OWN the `source` label (a caller can inject
    any `grep_fallback`, and the oracle decides what `source` the world sees). The
    rule: a fallback that already graded itself by forgeability (`grep-artifact` /
    `grep-subject`, from `default_grep_fallback_*`) keeps its grade; anything else
    is stamped the bare ``'grep'`` — preserving the long-standing contract that an
    injected stub returning `source='grep'` reports `'grep'`
    (`tests/test_oracle_and_loop.py`). Pure."""
    return fb_source if (fb_source or "").startswith("grep-") else "grep"


# ---------------------------------------------------------------------------
# Default grep fallback — thin wrapper around check_phase_shipped --batch
# ---------------------------------------------------------------------------


def _grep_batch_in_process(
    pairs: list[tuple[str, str]],
    doc_map: dict[str, str],
) -> "dict[str, dict] | None":
    """Run the `--batch` rung IN-PROCESS, returning ``{json-line}``-shaped dicts.

    `phase_shipped` is already importable in this interpreter (the kernel imports
    it), so shelling out to ``python -m dos.phase_shipped --batch`` paid a whole
    SECOND interpreter startup + ``import dos`` (~170ms of pure overhead on top of
    the ~60ms of actual `git log`) for a result this process can compute directly.
    A long-lived consumer that verifies on every call — the MCP `dos_verify` tool,
    the `dispatch_top` / `plan_board` fan-outs — paid that tax per call; this path
    removes it (~4× faster per `verify`).

    Behavior is BYTE-IDENTICAL to the child: it runs the SAME functions the child's
    `main()` `--batch` branch runs, in the same order (`_build_log_cache` ONCE, then
    per pair `_check_phase_with_cache` → `_consult_plan_body` → `_apply_filepath_backstop`),
    and reads the active ship-stamp convention from `config.active()` — which the
    caller has ALREADY installed (the `is_shipped(cfg=…)` branch `set_active`s it),
    so the env-var hand-off the subprocess needed (`ENV_STAMP_CONVENTION`) is moot
    in-process: the convention is simply already active. The git cwd is
    `phase_shipped._workspace_root()` == `config.active().paths.root`, the same root
    the subprocess used via `cwd=`. Returns one dict per pair keyed exactly like the
    child's JSON line (`{series, phase, shipped, sha, summary, via}`), or ``None`` if
    the rung module can't be driven (the caller then falls back to the subprocess).
    """
    try:
        from dos import phase_shipped as _ps  # noqa: PLC0415 — kernel sibling, always importable
    except Exception:  # pragma: no cover — defensive; the import never fails in practice
        return None
    try:
        oneline_lines, body_lines = _ps._build_log_cache()
        matchers = _ps._subject_matchers()
        # docs/284 — hoist the file-path backstop's per-pair `git log` into ONE
        # windowed `--name-only` scan shared across every pair. The cache maps each
        # named file to its commit history; `_apply_filepath_backstop` then reads it
        # instead of shelling a `git log` per file (~19s → ~1s on a 262-pair job
        # snapshot). A `None` cache (union window saturated / git error) makes the
        # backstop fall back to the exact per-file subprocess path — never-under-count.
        fp_cache = _ps.build_batch_filepath_cache(
            [(s, p, doc_map.get(s) or None) for s, p in pairs if p], matchers
        )
        rows: dict[str, dict] = {}
        for s, p in pairs:
            if not p:
                continue
            plan_doc = doc_map.get(s) or None
            result = _ps._check_phase_with_cache(s, p, oneline_lines, body_lines, matchers)
            result = _ps._consult_plan_body(result, plan_doc, p, s)
            result = _ps._apply_filepath_backstop(result, s, p, plan_doc, matchers, fp_cache)
            result["series"] = s
            result["phase"] = p
            rows[f"{s}\t{p}"] = result
        return rows
    except Exception:  # pragma: no cover — any rung error → let the subprocess try
        return None


def _grep_batch_subprocess(
    pairs: list[tuple[str, str]],
    doc_map: dict[str, str],
    timeout: int,
) -> "dict[str, dict] | None":
    """The legacy out-of-process `--batch` rung — fallback only.

    Kept as a safety net behind `_grep_batch_in_process` (and reachable directly by
    forcing `DOS_ORACLE_GREP_SUBPROCESS=1`) so a hypothetical in-process rung failure
    degrades to the previously-shipped behavior rather than a wrong answer. Returns
    the parsed JSON-line dicts keyed `"<series>\\t<phase>"`, or ``None`` on timeout.
    """
    lines: list[str] = []
    for s, p in pairs:
        if not p:
            continue
        # TAB-delimit the fields so a series OR phase containing spaces survives
        # the round-trip to the rung (`_parse_batch_line` splits on the tab). The
        # old space-join truncated a multi-word phase like `Phase 4` — the child's
        # `line.split(None, 2)` read phase=`Phase`, doc=`4`, so this pair's lookup
        # key never matched and a shipped phase resolved `via none` (F7). The
        # phase ids real hosts use (`hybrid-cache-type Phase 4`) contain spaces, so
        # this is load-bearing for any foreign repo, not an edge case.
        doc = doc_map.get(s, "")
        if doc:
            lines.append(f"{s}\t{p}\t{doc}")
        else:
            lines.append(f"{s}\t{p}")
    stdin_payload = "\n".join(lines) + "\n"
    # Carry the ACTIVE ship-stamp convention into the rung subprocess. The child
    # re-derives `config.active()` from scratch (it would default to the job
    # convention), so without this hand-off a caller-installed (`set_active`) or
    # `dos.toml`-declared convention would be lost the moment the grep rung shells
    # out — the exact gap that made `verify` non-domain-free. The child's
    # `_bootstrap_active_config` reads this env var back (SCV).
    child_env = dict(os.environ)
    try:
        child_env[_config.ENV_STAMP_CONVENTION] = json.dumps(
            _config.active().stamp.to_dict()
        )
    except Exception:
        pass  # never block the rung on a serialization hiccup
    try:
        res = subprocess.run(
            [sys.executable, "-m", "dos.phase_shipped", "--batch"],
            cwd=_workspace_root(),
            capture_output=True,
            text=True,
            input=stdin_payload,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return None
    rows: dict[str, dict] = {}
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = row.get("series", "")
        p = row.get("phase", "")
        if s and p:
            rows[f"{s}\t{p}"] = row
    return rows


def default_grep_fallback_batch(
    pairs: list[tuple[str, str]],
    *,
    plan_doc_map: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[tuple[str, str], ShipVerdict]:
    """Resolve the residual `(plan, phase)` pairs through the git-log grep rung.

    Runs the rung IN-PROCESS by default (`_grep_batch_in_process`) — the kernel
    already imports `phase_shipped`, so the historical ``python -m
    dos.phase_shipped --batch`` subprocess was paying a second interpreter startup
    + ``import dos`` per call for nothing. The out-of-process path
    (`_grep_batch_subprocess`) is kept as a fallback (and forced by
    `DOS_ORACLE_GREP_SUBPROCESS=1`) so a rung-import failure degrades to the
    previously-shipped behavior. Both produce the SAME JSON-line dicts; this
    function then grades each into a `ShipVerdict` (the forgeability `source` rung
    + the #399 release-bump demotion) exactly as before.

    Returns `{(plan,phase): ShipVerdict}` for every resolved pair; an unresolved
    pair is simply absent (caller fills with `source='none'`).
    """
    if not pairs:
        return {}
    doc_map = plan_doc_map or {}

    rows: "dict[str, dict] | None" = None
    if os.environ.get("DOS_ORACLE_GREP_SUBPROCESS") not in ("1", "true", "TRUE"):
        rows = _grep_batch_in_process(pairs, doc_map)
    if rows is None:  # forced subprocess, OR in-process rung unavailable/raised
        rows = _grep_batch_subprocess(pairs, doc_map, timeout)
    if rows is None:  # subprocess timed out
        return {}

    out: dict[tuple[str, str], ShipVerdict] = {}
    for _key, row in rows.items():
        s = row.get("series", "")
        p = row.get("phase", "")
        if not (s and p):
            continue
        # Carry the raw `via` rung and grade `source` by forgeability (docs/118):
        # `file-path` is the artefact/diff rung (`grep-artifact`, non-forgeable),
        # every subject/body rung is `grep-subject` (forgeable). A blank `via`
        # falls back to bare `grep` so nothing is mis-graded.
        via = str(row.get("via", "") or "")
        verdict = ShipVerdict(
            plan=s,
            phase=p,
            shipped=bool(row.get("shipped")),
            sha=row.get("sha") or None,
            source=_grade_grep_source(via),
            summary=row.get("summary", "") or "",
            rung=via,
        )
        # finding #399 — release-bump post-filter. The grep rung matches a
        # phase token anywhere in a commit's subject / release-notes body, so a
        # version cut that batches `… + <PHASE> closer + …` in its notes
        # false-flags an unshipped <PHASE> as shipped. Demote a shipped=True
        # verdict whose sha is a release-bump/ledger-only commit (footprint
        # check; an unresolvable sha stays permissive). This is the grep-side
        # complement of the registry-side Signal C demotion above.
        if _grep_verdict_is_release_bump_falsepos(verdict):
            verdict = dataclasses.replace(
                verdict,
                shipped=False,
                summary=(
                    (verdict.summary + " " if verdict.summary else "")
                    + f"[ship_oracle: demoted — grep matched release-bump/ledger-only "
                    f"commit {verdict.sha}; phase token in notes, not a ship (#399)]"
                ),
            )
        out[(s, p)] = verdict
    return out


def default_grep_fallback_single(plan: str, phase: str) -> ShipVerdict:
    """Single-pair convenience wrapper around the batch fallback."""
    out = default_grep_fallback_batch([(plan, phase)])
    return out.get(
        (plan, phase),
        ShipVerdict(plan=plan, phase=phase, shipped=False, source="none"),
    )


# ---------------------------------------------------------------------------
# FQ-390 — default commit-footprint verifier for the registry collision gate.
#
# A legacy `recently_completed` row (no `doc_path`) is disambiguated by asking:
# did this row's `commit_sha` actually touch any of the EXPECTED plan's phase
# files? A genuine ship of the expected plan touched its own files; a same-id
# collision (a tombed plan's `DL/DL2` row) touched a DIFFERENT plan's files.
# This reuses `check_phase_shipped`'s plan-doc file-extraction + shared-infra
# guard so the "distinctive file" definition is identical to the grep-side
# file-path backstop (one definition, two callers).
# ---------------------------------------------------------------------------


def default_commit_touches_doc(sha: str, expected_doc: str, phase: str) -> bool | None:
    """Return True/False/None for "did commit `sha` ship work for `expected_doc`?".

    Two signals over the commit's touched-file set, strongest first:

      A. **Plan-doc signal (the reliable collision detector).** If the commit
         touched a `docs/...-plan.md` (or `docs/tombstones/...-plan.md`):
           - it touched `expected_doc` (or a plan doc with the same basename) → True
           - it touched ONLY a DIFFERENT plan doc → False (the commit shipped
             another plan that happens to share this series id — the exact
             tombed-`DL` collision). Plan docs are stamped at ship time
             (`_stamp_plan_doc`), so a real ship of `expected_doc` touches
             `expected_doc`; a same-id collision touches the other plan's doc.
      B. **Distinctive-file signal (fallback).** Otherwise compare against the
         phase's NON-shared-infra files named in `expected_doc`:
           - commit touched ≥1 → True
           - phase names ≥1 distinctive file but commit touched NONE → False

      None — neither signal fires (no plan doc touched AND the phase names no
             distinctive file, or git/doc unreadable). PERMISSIVE → keep the
             row, preserving the deliberate direct-ship trust (a real unstamped
             ship must not be manufactured into a false negative).

    Only a *definite* miss (False) demotes a registry row — same posture as the
    grep-side `_apply_filepath_backstop`.
    """
    sha = (sha or "").strip()
    if not sha:
        return None
    try:
        from dos.phase_shipped import (  # type: ignore
            _extract_phase_files,
            _is_shared_infra,
        )
    except Exception:
        return None
    doc_path = expected_doc
    if not Path(doc_path).is_absolute():
        doc_path = str(_workspace_root() / expected_doc)

    # Read the commit's touched-file set through the per-process memo
    # (`_git_touched_files`) rather than shelling our OWN `git show`. The command
    # is byte-identical (`git show --name-only --format= <sha>` with the same path
    # normalization), so the verdict is unchanged — but the memo collapses the
    # duplicate footprint reads this rung shares with the grep-side #399 demotion
    # (the docs/284 "demotion reads from the same scan" deferred item). A single
    # registry-collision check warms a release-bump sha here whose footprint
    # `_demote_if_false_positive` → `_grep_verdict_is_release_bump_falsepos` then
    # reads from cache (and vice-versa); a fan-out re-hits the same shas free. The
    # memo is keyed on the IMMUTABLE (root, sha), so it is the safest possible cache
    # (no staleness) — see `_git_touched_files`. `None` (unknown sha / shallow clone
    # / git unavailable) and the empty-footprint case both fall to the permissive
    # `return None` below, exactly as the inline `git show` did.
    touched = _git_touched_files(sha)
    if not touched:
        return None

    expected_base = _norm_doc(expected_doc).rsplit("/", 1)[-1]

    # Signal A — plan docs the commit touched. Match ANY path ending `-plan.md`
    # (optionally under a `tombstones/` segment), NOT just a `docs/`-rooted one: the
    # plan location is a per-workspace choice (`cfg.paths.plans_glob`; the shipped
    # `examples/workspaces/riverflow` uses `experiments/*.md`), so anchoring on `docs/`
    # silently never fired Signal A for a non-`docs/` layout (userland-coupling audit
    # 2026-06-08). The verdict is decided on the plan BASENAME below (`expected_base`),
    # so dropping the host-shaped directory anchor is behavior-identical for a
    # `docs/`-rooted repo and correct for the rest.
    _PLAN_DOC_RE = re.compile(r"(?:^|/)(?:tombstones/)?[\w.-]+-plan\.md$", re.IGNORECASE)
    plan_docs_touched = {t for t in touched if _PLAN_DOC_RE.search(t.lower())}
    if plan_docs_touched:
        bases = {t.lower().rsplit("/", 1)[-1] for t in plan_docs_touched}
        if expected_base in bases:
            return True
        # The commit touched plan doc(s), but NONE is the expected one →
        # it shipped a different plan (same-series-id collision).
        return False

    # Signal B — distinctive (non-shared-infra) files named for this phase.
    try:
        series = re.sub(r"[\d.].*$", "", str(phase)) or str(phase)
        named = _extract_phase_files(doc_path, phase, series)
    except Exception:
        named = []
    distinctive = [f for f in named if not _is_shared_infra(f)]
    if not distinctive:
        # Signal C — routing-only / bookkeeping / release-bump commit (the
        # FQ-388 ANC3 shape + the finding #399 release-bump shape). Before
        # falling to the permissive None, check whether the commit touched
        # ANYTHING of substance. A genuine unstamped ship always edits ≥1 code,
        # test, or substantive doc file; a row stamped `status: done` by a
        # commit that touched ONLY the dispatch ledger (findings queue /
        # execution-state / plans.yaml) OR ONLY version-bump/release-notes files
        # is a routing stamp / version cut, never a ship — demote it
        # definitively. The release-bump case is what false-shipped FQ-375 off
        # `30d3ac30` (`v0.378.0: … + FQ-375 live-API closer + …` touched only
        # VERSION/pyproject/__init__/docs-releases). This kills the whole
        # "bookkeeping/release-bump row short-circuits as shipped" class, not one
        # row. Signal A already returned for plan-doc commits, so `touched` here
        # is plan-doc-free.
        if _commit_footprint_is_nonsubstantive(touched):
            return False  # touched only ledger / release-bump files → not a ship
        return None  # nothing distinctive to verify against — permissive
    for f in distinctive:
        fp = f.replace("\\", "/")
        if fp in touched:
            return True
        base = fp.rsplit("/", 1)[-1]
        if any(t.rsplit("/", 1)[-1] == base for t in touched):
            return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_single(plan: str, phase: str) -> int:
    state = load_state()
    # Live CLI gets BOTH grep-side false-positive demotions wired in (#399 +
    # #326) — the boundary backstop + the new soak cross-check. The pure core
    # stays injection-only; this is the in-process opt-in.
    verdict = is_shipped(
        plan, phase, state=state, grep_fallback=default_grep_fallback_single,
        grep_touched_files=_git_touched_files, soaks=load_soaks(),
    )
    print(json.dumps(verdict.to_dict(), ensure_ascii=False))
    return 0 if verdict.shipped else 1


def _cli_batch() -> int:
    state = load_state()
    pairs: list[tuple[str, str]] = []
    for line in sys.stdin:
        parts = line.strip().split(None, 2)
        if len(parts) >= 2:
            pairs.append((parts[0], parts[1]))
    results = batch_is_shipped(
        pairs, state=state, grep_fallback=default_grep_fallback_batch,
        grep_touched_files=_git_touched_files, soaks=load_soaks(),
    )
    any_shipped = False
    for pair in pairs:
        v = results.get(pair) or ShipVerdict(plan=pair[0], phase=pair[1], shipped=False, source="none")
        print(json.dumps(v.to_dict(), ensure_ascii=False))
        if v.shipped:
            any_shipped = True
    return 0 if any_shipped else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plan", nargs="?", help="Plan series (e.g. IF, AAR, TF)")
    parser.add_argument("phase", nargs="?", help="Phase id (e.g. IF4.1)")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Read `<plan> <phase>` pairs from stdin; emit one JSON line per result.",
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.batch:
        return _cli_batch()
    if not (args.plan and args.phase):
        parser.error("plan and phase are required unless --batch is used")
        return 2
    return _cli_single(args.plan, args.phase)


if __name__ == "__main__":
    sys.exit(main())
