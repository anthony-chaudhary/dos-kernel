#!/usr/bin/env python3
"""Run the rung-occupancy backtest over a real repo's plan/phase history.

This is the I/O boundary for `dos.rung_occupancy` (the pure fold) — the script
that answers docs/138 §"Collecting more data" item 3 ON REAL HISTORY: over this
workspace's plans, which `source=` actually answered each `verify`, and of the
forgeable (`grep-subject`) greens, how many an independent artifact-rung re-check
fails to corroborate. The headline it prints is the one docs/138 names and leaves
unmeasured: **the fraction of green verdicts resting on the forgeable rung** — if
high, the non-forgeable floor is doing little work.

The split of concerns is the kernel's one-way arrow (the same as `forge_page.py`
and `trajectory_audit.py`): ALL git / config / plan-glob I/O lives here; the
counting is `dos.rung_occupancy.census`, pure and host-free. Nothing under
`src/dos/` imports this. The eval is only as honest as its inputs, so the two
inputs this script gathers — the verdict per pair, and the artifact re-check —
both come from the kernel's OWN oracle (`oracle.is_shipped`,
`phase_shipped._check_phase_by_filepath`), never from a hand-rolled re-implementation
that could quietly grade itself.

The artifact re-check (the second, independent witness over a forgeable green):
a forgeable green stood on a phase token in a commit SUBJECT the agent typed. The
re-check asks the file-path backstop DIRECTLY — "did a commit touch ≥2 of this
phase's declared load-bearing files?" — via `_check_phase_by_filepath`. It does
NOT route through `check_phase_shipped`, because that resolves the forgeable
subject/trailer rung first and short-circuits before the artefact backstop runs
(the backstop is a false-NEGATIVE catch); re-running it would make the
"independent" witness echo the forgeable rung it is meant to test. Going straight
to the file-path rung asks the diff — and only the diff. If that re-derivation
comes back on the `file-path` (artifact) rung, the subject green is CORROBORATED by
the diff; if it does not, the green stood ONLY on the forgeable subject rung — a
`recheck_refuted`, the floor's measured payload.

A phase that declares NO distinctive deliverable (a re-stamp, a doc-only phase, or
one whose only footprint is a shared-infra hub) is OUT of the floor's domain: it
cannot be diff-corroborated by anyone, so a False re-check there is not a breach.
Such a unit is left out of the re-check map → folded as `recheck_skipped`, never
`recheck_refuted` (mirrors `phase_deliverable_touched`'s None=permissive). See
`_phase_has_declarable_artefact`.

Usage:
    PYTHONPATH=src python scripts/rung_occupancy_backtest.py [--workspace .] [--json]
                                                             [--limit N] [--check]

Exit codes:
    0  ran (and, with --check, the floor HELD — no forgeable green was refuted)
    1  --check and the floor was BREACHED (a forgeable green failed its re-check)
    2  bad input / no plans found
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make `dos` importable when run as `python scripts/...` from a source checkout,
# mirroring the other scripts' PYTHONPATH-or-src bootstrap.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import re  # noqa: E402
import subprocess  # noqa: E402

from dos import config as _config  # noqa: E402
from dos import oracle  # noqa: E402
from dos import phase_shipped  # noqa: E402
from dos import plan_source  # noqa: E402
from dos import rung_occupancy  # noqa: E402
from dos import stamp as _stamp  # noqa: E402

# The raw `via` rungs that rest on the commit ARTEFACT (the diff) — a re-check
# landing on one of these corroborates a forgeable subject green from a witness
# the agent did not author. Mirrors `oracle._NONFORGEABLE_GREP_RUNGS`; kept here
# (not imported) so the script stays a plain consumer of the public verdict shape.
_ARTIFACT_VIA = frozenset({"file-path"})


# A candidate to grade: the (plan/series, phase) pair `is_shipped` takes, plus the
# plan doc the file-path re-check needs. Flat duck-typed shape — a `PlanRow` (which
# carries .plan/.phase/.doc_path) or a git-harvested `_Candidate` both satisfy it.
class _Candidate:
    __slots__ = ("plan", "phase", "doc_path")

    def __init__(self, plan: str, phase: str, doc_path: str = ""):
        self.plan = plan
        self.phase = phase
        self.doc_path = doc_path


# A `docs/NN` series token in a commit subject — the workspace's stamp dir prefix.
# Generic to the docs/ layout; a host with a different stamp dir overrides via the
# plan-doc mode (the glob), so this harvester stays a best-effort fallback, never the
# sole source of truth.
_SERIES_RE = re.compile(r"\b(docs/\d+)\b", re.IGNORECASE)


def _git_subjects(workspace: str, window: int) -> list[str]:
    """The recent commit subjects, oldest-irrelevant — best-effort, fail-to-empty."""
    try:
        out = subprocess.run(
            ["git", "-C", workspace, "log", f"-{window}", "--format=%s"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _harvest_git_stamps(workspace: str, window: int) -> list[_Candidate]:
    """Candidate (series, phase) pairs from real ship-stamps in the commit log.

    The corpus for a repo whose plans-glob matches no files (this kernel's own
    layout: design docs are `docs/NN_*.md`, not `*-plan.md`, and `dos verify` is
    called with explicit args). Uses the kernel's OWN subject parser
    (`stamp.parse_phase_labels`) for the phase token and the `docs/NN` dir prefix
    for the series — so the enumeration is grounded in the same grammar `verify`
    adjudicates against, never a hand-rolled regex the eval would then grade itself
    on. The plan doc is resolved to the matching `docs/NN_*.md` so the file-path
    re-check has a row to read. De-duplicated; a subject with a `docs/NN` series but
    NO phase token contributes the bare doc-number phase (the doc itself shipped).
    """
    root = Path(workspace)
    seen: set[tuple[str, str]] = set()
    out: list[_Candidate] = []
    for subj in _git_subjects(workspace, window):
        series_m = _SERIES_RE.search(subj)
        if not series_m:
            continue
        series = series_m.group(1).lower()  # docs/NN
        num = series.split("/", 1)[1]
        doc_path = ""
        try:
            matches = sorted(root.glob(f"docs/{num}_*.md"))
            if matches:
                # When several files share a doc number, prefer the most-specific
                # (longest-named) — the descriptive design doc over a short sidecar
                # like `docs/NN_GOAL_PROMPTS.md`, so the file-path backstop reads the
                # right plan's load-bearing rows rather than the alphabetically-first.
                best = max(matches, key=lambda p: len(p.name))
                doc_path = str(best.relative_to(root)).replace("\\", "/")
        except (OSError, ValueError):
            doc_path = ""
        phases = _stamp.parse_phase_labels(subj) or _stamp.parse_phase_labels(
            subj.replace(series, "").replace(series.upper(), ""))
        # Milestone tokens (`M4`) the P-only parser misses — pick them up so a
        # `(docs/342 M4)` stamp is graded, not dropped.
        phases = list(phases) + re.findall(r"\bM\d+(?:\.\d+)?\b", subj)
        if not phases:
            phases = [num]  # the doc itself is the unit
        for ph in phases:
            key = (series, ph)
            if key in seen:
                continue
            seen.add(key)
            out.append(_Candidate(series, ph, doc_path))
    return out


def _enumerate_rows(cfg, workspace: str, git_window: int):
    """All (plan, phase, doc_path) candidates to grade.

    Primary: the workspace's declared plans (the markdown plan source, fail-to-empty).
    Fallback: when the plans-glob matches nothing (a repo like this kernel that
    verifies by explicit args, not plan-doc enumeration), harvest candidates from the
    real ship-stamps in the commit log. The fallback is announced on stderr so a
    reader never mistakes a git-stamp corpus for a plan-doc one."""
    src = plan_source.MarkdownPlanSource()
    rows = plan_source.run_plan_source(src, cfg)
    if rows:
        return rows, "plans"
    stamped = _harvest_git_stamps(workspace, git_window)
    return stamped, "git-stamps"


def _artifact_recheck(series: str, phase: str, plan_doc: str) -> bool:
    """Independent artifact-rung re-derivation of one forgeable green.

    Returns True iff the file-path backstop finds the ship (the subject green IS
    corroborated by the diff), False iff it does not (the green stood only on the
    forgeable subject rung).

    Calls the file-path rung DIRECTLY (`_check_phase_by_filepath`), NOT
    `check_phase_shipped`. This is the load-bearing design point: `check_phase_
    shipped` resolves the FORGEABLE subject/trailer rung first and short-circuits
    before the artefact backstop runs (`_apply_filepath_backstop` is a false-NEGATIVE
    backstop — it engages only when the subject rungs MISS). Re-running it would
    therefore answer `via=trailer` for the very `(docs/NN Pk)`-stamped greens we are
    re-checking, making the "independent" witness echo the forgeable rung it is meant
    to test. Going straight to `_check_phase_by_filepath` asks the diff — and ONLY
    the diff — whether a real commit touched >= 2 of the phase's declared load-
    bearing files, which is exactly the non-forgeable corroboration the floor claims.

    The forgeability split is unchanged: `is_shipped` (the verdict the census grades)
    still answers via the subject rung, so the green is correctly FORGEABLE; this
    re-check is the second, artefact-only opinion on whether the diff agrees.

    A re-check that errors is treated as NOT corroborated (conservative — an
    un-derivable artefact witness is no witness).
    """
    if not plan_doc:
        return False
    try:
        m = phase_shipped._subject_matchers()
        fp = phase_shipped._check_phase_by_filepath(series, phase, plan_doc, m)
    except Exception:
        return False
    return bool(fp.get("shipped")) and fp.get("via", "") in _ARTIFACT_VIA


def _phase_has_declarable_artefact(series: str, phase: str, plan_doc: str) -> bool:
    """Does this phase declare ANY distinctive deliverable file the artefact rung
    could match? — the applicability gate for the re-check.

    A forgeable green only belongs in the floor's denominator if it HAS an artefact
    to stand on. A phase whose plan section names no distinctive file — a re-stamp,
    a doc-only phase, or one whose only footprint is a shared-infra hub — cannot be
    diff-corroborated by anyone, so a False re-check there is NOT a floor breach;
    it is "the floor has no domain here." Mirrors `phase_deliverable_touched`'s
    None=permissive contract (a phase with no distinctive deliverable yields no
    refusal). The backtest folds such a unit as `recheck_skipped`, never `refuted`,
    so `floor_held` measures only declared-but-unwitnessed greens.

    This is NOT a way to game the check: declaring MORE files moves a unit toward
    corroborated/refuted, never toward skipped — a unit skips precisely when there
    is nothing distinctive to declare, which is exactly when the floor cannot apply.

    Uses the SAME harvester (`_extract_phase_files`) and distinctiveness filters
    (`_is_shared_infra`, the plan-doc-self drop) the backstop itself uses, so the
    gate and the re-check agree on what "distinctive" means. Fail-OPEN (returns
    True) on any error: an undecidable applicability falls back to the conservative
    "it applies", so a real breach is never silently skipped.
    """
    if not plan_doc:
        return False  # no doc → nothing declared → not in the floor's domain
    try:
        m = phase_shipped._subject_matchers()
        declared = phase_shipped._extract_phase_files(plan_doc, phase, series, m)
        pdp = plan_doc.replace("\\", "/")
        distinctive = [
            f for f in declared
            if f.replace("\\", "/") != pdp
            and not phase_shipped._is_shared_infra(f, m)
        ]
        return len(distinctive) >= 1
    except Exception:
        return True  # undecidable → assume it applies (never skip a real breach)


def run(workspace: str, limit: int | None = None, git_window: int = 1500):
    cfg = _config.load_workspace_config(workspace)
    rows, corpus = _enumerate_rows(cfg, workspace, git_window)
    if corpus == "git-stamps":
        print("rung-occupancy backtest: no plans matched the plans-glob — "
              "harvesting the corpus from real ship-stamps in the commit log.",
              file=sys.stderr)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return None, []

    verdicts: list[oracle.ShipVerdict] = []
    rechecks: dict[tuple[str, str], bool] = {}
    # `is_shipped(cfg=cfg)` reads `_config.active()` for the grep rung's root, so
    # install this workspace as active for the sweep and restore after — the same
    # no-global-side-effect discipline the library convenience uses internally.
    prev_active = None
    try:
        prev_active = _config.active()
    except Exception:
        prev_active = None
    _config.set_active(cfg) if hasattr(_config, "set_active") else None
    try:
        for r in rows:
            v = oracle.is_shipped(r.plan, r.phase, cfg=cfg)
            verdicts.append(v)
            # Only forgeable greens need the second witness — and only those that
            # DECLARE a distinctive artefact are in the floor's domain. A phase that
            # declares no distinctive deliverable (re-stamp / doc-only / infra-only
            # ship) is left OUT of the recheck map, so the census folds it as
            # `recheck_skipped`, not `recheck_refuted` — the floor cannot apply where
            # there is no artefact to stand on (mirrors `phase_deliverable_touched`'s
            # None=permissive). `floor_held` then measures only declared-but-
            # unwitnessed greens.
            if rung_occupancy.classify_forgeability(v.shipped, v.source) \
                    is rung_occupancy.Forgeability.FORGEABLE \
                    and _phase_has_declarable_artefact(r.plan, r.phase, r.doc_path):
                rechecks[(r.plan, r.phase)] = _artifact_recheck(
                    r.plan, r.phase, r.doc_path)
    finally:
        if prev_active is not None and hasattr(_config, "set_active"):
            _config.set_active(prev_active)

    occ = rung_occupancy.census(verdicts, rechecks=rechecks)
    return occ, verdicts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=os.getcwd(),
                    help="repo root to backtest (default: cwd)")
    ap.add_argument("--json", action="store_true", help="emit the census as JSON")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the number of (plan, phase) rows swept")
    ap.add_argument("--git-window", type=int, default=1500,
                    help="commits to scan when harvesting the git-stamp corpus")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the floor was BREACHED (a forgeable green refuted)")
    args = ap.parse_args(argv)

    try:
        occ, _verdicts = run(args.workspace, limit=args.limit,
                             git_window=args.git_window)
    except Exception as e:  # noqa: BLE001 — a script boundary; report and exit 2
        print(f"rung-occupancy backtest: {e}", file=sys.stderr)
        return 2

    if occ is None:
        print("rung-occupancy backtest: no plans found under the workspace "
              f"({args.workspace}) — nothing to grade.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(occ.to_dict(), indent=2))
    else:
        print(rung_occupancy.render_text(occ))

    if args.check and not occ.floor_held:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
