#!/usr/bin/env python3
"""residual_review — the next-generation diff: review the residual, not the diff.

This example is now a THIN SHELL over the shipped kernel module
`dos.residual_review` (issue #211): the three-band projection it pioneered was
promoted to a first-class `dos review` verb + `dos_review` MCP tool. The example
does only boundary reads (`commit_audit.audit_range`, subjects, diffstats), then
hands those bytes to the kernel projection — it recomputes no rung.

The whole point of the surface: a reviewer today reads every changed line with
roughly equal attention, but for a large fraction of those lines the kernel
already KNOWS the change did the kind of thing its commit claimed (the diff
*witnesses* the claim — `commit-audit`'s `diff-witnessed` rung, docs/214). So
invert the sweep: project a range back onto its commits and partition the review
surface into CLEARED (witnessed, ~0 attention) / RESIDUAL (the claim the machine
could not witness — the human's 100%) / UNVERIFIABLE (no claim to check), plus an
advisory SEMANTIC lens that only ever asks for MORE eyes. Full prose lives in the
kernel module's docstring (`dos.residual_review`).

Run:  dos review origin/master..HEAD        # the shipped verb
      dos review --json HEAD~20..HEAD
      dos review --walk HEAD~20..HEAD
      python examples/residual_review/residual_review.py    # this shell, same output
"""
from __future__ import annotations

import argparse
import json
import sys

from dos.commit_audit import audit_range
from dos.vcs import active_vcs

# Re-export the SHIPPED kernel surface — the example recomputes no rung. Names
# kept stable so the example's own tests (and any downstream importer) still
# resolve `plan_review`, `render_walk`, `ReviewPlan`.
from dos.residual_review import (  # noqa: F401  (re-exported for the example API)
    RISK_SURFACES,
    ReviewItem,
    ReviewPlan,
    _all_files,
    _risk_reasons,
    _short,
    plan_review,
    plan_to_dict,
    render_text,
    render_walk,
)

# Back-compat alias: the example historically exposed `_plan_to_dict`. The kernel
# promoted it to the public `plan_to_dict`; keep the old name working.
_plan_to_dict = plan_to_dict


def _subjects(rev_range: str, root: str, limit: int = 500) -> dict[str, str]:
    """sha -> subject labels. Example/CLI boundary I/O; empty on failure."""
    lines = active_vcs(root=root).log_lines(
        (f"-{int(limit)}", "--pretty=format:%H\x1f%s", rev_range))
    if not lines:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        if "\x1f" in line:
            sha, subj = line.split("\x1f", 1)
            out[sha.strip()] = subj
    return out


def build_plan(rev_range: str, root: str = ".") -> ReviewPlan:
    """Boundary pipeline for the example: audit commits, then project purely."""
    verdicts = audit_range(rev_range, root=root)
    return plan_review(verdicts, rev_range, subjects=_subjects(rev_range, root))


def _commit_diffstat(sha: str, root: str) -> str:
    """Rendered numstat for one review card. Example boundary I/O."""
    deltas = active_vcs(root=root).commit_diffstat(sha)
    if not deltas:
        return ""
    rows: list[str] = []
    for d in deltas:
        if d.added < 0 or d.removed < 0:
            rows.append(f"{d.path} | bin")
        else:
            rows.append(f"{d.path} | +{d.added} -{d.removed}")
    return "\n".join(rows)


def _diffstats_for_plan(plan: ReviewPlan, root: str) -> dict[str, str]:
    return {it.sha: _commit_diffstat(it.sha, root) for it in plan.residual}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="The residual diff: review what the kernel could not witness.")
    ap.add_argument("rev_range", nargs="?", default="HEAD~20..HEAD",
                    help="git range to review (default: HEAD~20..HEAD)")
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--walk", action="store_true",
                    help="step through the residual as numbered review cards "
                         "(the navigation surface) instead of the three-band list")
    args = ap.parse_args(argv)

    plan = build_plan(args.rev_range, root=args.root)
    if args.json:
        print(json.dumps(plan_to_dict(plan), indent=2))
    elif args.walk:
        print(render_walk(plan, diffstats=_diffstats_for_plan(plan, args.root)))
    else:
        print(render_text(plan))
    # Exit code mirrors commit-audit's CI convention: non-zero iff there is a
    # residual a human must read. A range that is entirely cleared exits 0.
    return 1 if plan.residual else 0


if __name__ == "__main__":
    sys.exit(main())
