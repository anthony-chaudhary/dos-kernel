#!/usr/bin/env python3
"""Gate-context for the DOS /stable-release skill.

A *stable release* promotes an already-shipped rolling tag (`vX.Y.Z`) to a
named, less-frequent channel (`stable/<codename>`). It mints no new version,
builds no new artifact — it freezes a gate evaluation into an evidence file and
pins a second annotated tag on the same commit. This script collapses every
gate-relevant read into one JSON payload, the way `release_context.py` does for
the rolling release.

This is **dev / release tooling, not kernel** — it operates ON the package but
is never imported BY it.

## Why the gate is different from job's

job's stable gate reads apply-loop hero metrics (silent-failure share, PSV
verified-success rate, funnel-stage regressions) and KEEP-slot baselines out of
`execution-state.yaml` + `baselines.yaml`. **None of that exists in DOS** — DOS
is a domain-free trust substrate, not an apply pipeline; it has no runtime
funnel, no hero metric, no baseline ledger. So the gate is re-grounded on the
only "known-good" signals DOS actually has:

  1. **The kernel suite is green** — `python -m pytest -q` exits 0. The whole
     value proposition of the substrate is that its verdicts are deterministic
     and correct; a red suite means the substrate itself is not trustworthy, so
     it cannot anchor a stable channel.
  2. **The truth syscall runs clean** — `dos verify` executes against the
     workspace without error (exit 0). On a no-plan repo it answers
     `source="none"`; that is a *pass* (the syscall works), not a failure.
  3. **The candidate tag has soaked** — it has existed for >= `window_days` so
     it is not a just-cut tag being promoted in the same breath.
  4. **No tag collision** — `stable/*` does not already point at the candidate
     commit (idempotency / double-promote guard).

These are deliberately conservative and few. As DOS grows a real "known-good"
history (e.g. an N-day clean-`dos verify`-across-consumers soak, or a
consumer-side apply-loop in `job`), additional gate rows can be threaded in here
without touching the kernel.

Usage:
  python scripts/stable_release_context.py --codename 2026-06-aardvark
  python scripts/stable_release_context.py --codename ... --from v0.3.0
  python scripts/stable_release_context.py --codename ... --window-days 7
  python scripts/stable_release_context.py --codename ... --skip-pytest   # smoke

Emits the gate JSON to stdout. `summary.all_green` is the single bit the skill
keys on; if false, the skill stops and reports the failing rows.

The payload also carries an `idempotency` block so a partial-failure re-run with
the SAME codename is safe (the operator hit Ctrl-C between tagging and writing the
evidence file, say): `{tag_exists, tag_sha, tag_matches_candidate,
evidence_file_exists, evidence_path}`. A same-codename tag already pointing at the
candidate is reported as an idempotent SKIP (not a `tag_collision` blocker — that
is reserved for a DIFFERENT codename on the same SHA, i.e. an already-promoted
commit); a same-codename tag pointing at a DIFFERENT commit IS a blocker (the word
was reused — a stable tag is never re-pointed, so pick a fresh codename).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
CODENAME_RE = re.compile(r"^\d{4}-\d{2}-[a-z][a-z0-9-]{2,20}$")


def run(cmd: list[str]) -> tuple[int, str]:
    """Run a command, return (exit_code, combined_output)."""
    try:
        out = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
        )
        return out.returncode, out.stdout or ""
    except FileNotFoundError as exc:
        return 127, f"{exc}"


def git(args: list[str]) -> str:
    code, out = run(["git", *args])
    return out if code == 0 else ""


def repo_root() -> Path:
    top = git(["rev-parse", "--show-toplevel"]).strip()
    return Path(top) if top else Path.cwd()


def latest_semver_tag() -> str | None:
    for line in git(["tag", "--sort=-v:refname"]).splitlines():
        if SEMVER_TAG_RE.match(line.strip()):
            return line.strip()
    return None


def tag_sha(tag: str) -> str | None:
    sha = git(["rev-list", "-n1", tag]).strip()
    return sha or None


def tag_commit_date_epoch(tag: str) -> int | None:
    """Committer epoch seconds of the commit the tag points at."""
    raw = git(["log", "-1", "--format=%ct", tag]).strip()
    try:
        return int(raw)
    except ValueError:
        return None


def head_now_epoch() -> int | None:
    """Use the newest commit's committer time as 'now'.

    Workflow scripts forbid wall-clock reads for determinism; for a CLI helper
    that is overkill, but anchoring 'now' to HEAD's commit time keeps the age
    computation reproducible and avoids a timezone-sensitive `datetime.now()`.
    The skill can always override the candidate with --from.
    """
    raw = git(["log", "-1", "--format=%ct", "HEAD"]).strip()
    try:
        return int(raw)
    except ValueError:
        return None


def stable_tag_collision(candidate_sha: str) -> str | None:
    """Return an existing stable/* tag already pointing at candidate_sha, if any."""
    for line in git(["tag", "-l", "stable/*"]).splitlines():
        t = line.strip()
        if not t:
            continue
        if tag_sha(t) == candidate_sha:
            return t
    return None


def idempotency(codename: str, candidate_sha: str | None, root: Path) -> dict:
    """Detect already-done side-effects of THIS codename (a partial-failure re-run).

    A stable promotion has two durable side-effects the skill writes in sequence:
    the `stable/<codename>` annotated tag, and the `docs/stable-releases/<codename>.md`
    evidence file. If the operator hit Ctrl-C (or a push failed) between them, a
    re-run with the SAME codename must be safe — it should detect what already
    exists and SKIP it, not error or write a duplicate. This is the idempotent
    re-run property the userland app bakes into its promote orchestrator
    (`idempotent_skips`); DOS surfaces the same signal as data the skill body acts
    on (DOS keeps the promotion script-light — the skill drives the tag + evidence
    steps directly, so the idempotency check is data, not a wrapper).

    Crucially, a `stable/<codename>` tag is reported here as an idempotent
    `tag_exists` — NOT as a `tag_collision` blocker. The two are different: a
    DIFFERENT codename pointing at the candidate SHA is a real collision (already
    promoted under another name → stop); the SAME codename existing is just the
    earlier, interrupted run of THIS promotion → skip the tag step and continue.

    Returns ``{tag_exists, tag_sha, evidence_file_exists, evidence_path,
    tag_matches_candidate}``. Pure reads; never raises.
    """
    tag = f"stable/{codename}"
    existing = [t.strip() for t in git(["tag", "-l", tag]).splitlines() if t.strip()]
    tag_exists = bool(existing)
    this_tag_sha = tag_sha(tag) if tag_exists else None
    rel_evidence = f"docs/stable-releases/{codename}.md"
    evidence_path = root / "docs" / "stable-releases" / f"{codename}.md"
    return {
        "tag_exists": tag_exists,
        "tag_sha": this_tag_sha,
        # True when the existing same-codename tag already points at the candidate
        # — the clean idempotent case (skip the tag step, the right commit is tagged).
        # False + tag_exists means the codename was used for a DIFFERENT commit:
        # a real conflict the skill must stop on (mistakes get a new codename).
        "tag_matches_candidate": (
            this_tag_sha == candidate_sha if (tag_exists and candidate_sha) else None
        ),
        "evidence_file_exists": evidence_path.exists(),
        "evidence_path": rel_evidence,
    }


def previous_stable() -> dict | None:
    """Most recent stable/* tag by creation, with its underlying commit."""
    tags = [t.strip() for t in git(["tag", "-l", "stable/*",
                                    "--sort=-creatordate"]).splitlines() if t.strip()]
    if not tags:
        return None
    t = tags[0]
    return {"tag": t, "sha": tag_sha(t)}


def gate_pytest(root: Path, skip: bool) -> dict:
    """Run the kernel suite. Pass == exit 0."""
    if skip:
        return {"name": "pytest_suite_green", "pass": True, "skipped": True,
                "note": "--skip-pytest passed; suite not run"}
    code, out = run([sys.executable, "-m", "pytest", "-q"])
    tail = "\n".join(out.splitlines()[-8:])  # last few lines carry the summary
    return {
        "name": "pytest_suite_green",
        "pass": code == 0,
        "exit_code": code,
        "summary_tail": tail,
    }


def gate_dos_verify(root: Path) -> dict:
    """Run the truth syscall against the workspace. Pass == it produced a
    well-formed verdict without crashing.

    IMPORTANT: `dos verify` exits 0 iff the probed phase *shipped*, and 1 when
    it did not (that is the syscall's contract — the exit code carries the
    verdict, not execution health). We probe with a sentinel (plan, phase) that
    by construction never shipped, so a *healthy* syscall on a no-plan repo
    returns `shipped=false, source="none"` and exit **1**. The gate therefore
    keys on "did we get a parseable verdict dict?", treating exit 1 with a valid
    verdict as a PASS and reserving failure for a crash: a non-{0,1} exit code,
    a traceback, or no JSON on stdout.
    """
    code, out = run([sys.executable, "-m", "dos.cli", "verify",
                     "--workspace", str(root), "STABLE-GATE-PROBE", "P0", "--json"])
    verdict = None
    try:
        verdict = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
    except (ValueError, IndexError):
        verdict = None
    # Healthy iff we got a verdict dict carrying the expected keys AND the exit
    # code is one of the two contract values (0 shipped / 1 not-shipped).
    well_formed = isinstance(verdict, dict) and {"plan", "phase", "shipped", "source"} <= verdict.keys()
    healthy = well_formed and code in (0, 1)
    return {
        "name": "dos_verify_clean",
        "pass": healthy,
        "exit_code": code,
        "verdict": verdict,
        "note": "exit 1 with a valid verdict is a PASS — the code carries the "
                "ship verdict, not execution health",
    }


def gate_ci_green(candidate_sha: str | None, repo: str, skip: bool) -> dict:
    """Consult the CI/Checks oracle for the candidate commit — the third-party
    green-build fossil, not the local pytest self-report.

    This is the gate row that makes the substrate eat its own distrust (docs/93).
    `gate_pytest` above runs the suite *locally* — a self-report from the very
    machine cutting the promotion, which is exactly the kind of narration the
    kernel exists not to believe. This row reads the verdict the CI system
    recorded for the candidate commit on infrastructure the promoter does not
    control (`dos.drivers.ci_status`), so "the suite is green" becomes a verified
    claim against a more accountable referent at the one decision — promoting to a
    stable channel — where a silent local-vs-CI drift detonates as a broken
    published wheel.

    The blocking policy is deliberately ASYMMETRIC, the fail-safe discipline of
    the oracle itself (NO_SIGNAL is never a fabricated pass — but it is also never
    a fabricated failure):

      * GREEN     → pass. CI confirms the build is green at the candidate.
      * RED       → BLOCK. CI says a required check failed — a real, accountable
                    failure; promotion must stop. This is the row's whole reason
                    to exist.
      * PENDING   → advisory pass (does NOT block). CI hasn't finished; the local
                    pytest row still stands as the load-bearing suite gate. The
                    note says so loudly so the operator can wait for green.
      * NO_SIGNAL → advisory pass (does NOT block). `gh` is unwired/unauthed, or
                    the commit was never pushed. Hard-blocking here would make the
                    gate unrunnable on any box without `gh` — contradicting the
                    "ship the socket, the host decides how strong to plug in"
                    discipline. The local pytest row remains load-bearing; this
                    row is purely additive signal.

    So this row can only ever *add* a blocker (a RED CI run the local suite might
    not have caught — e.g. a Windows-leg or py3.11 matrix failure invisible on the
    promoter's machine); it never removes the existing local-suite guarantee.
    """
    if skip:
        return {"name": "ci_green", "pass": True, "skipped": True,
                "advisory": True, "note": "--skip-ci passed; CI oracle not consulted"}
    if not candidate_sha:
        return {"name": "ci_green", "pass": True, "advisory": True,
                "verdict": "NO_SIGNAL",
                "note": "no candidate SHA to consult CI for — advisory pass"}
    try:
        # Consumer-side import: this is dev/release tooling, the one-way arrow
        # (it imports the package; the package never imports it) is intact.
        from dos.drivers import ci_status
    except ImportError as exc:  # pragma: no cover - dos always importable in the gate env
        return {"name": "ci_green", "pass": True, "advisory": True,
                "verdict": "NO_SIGNAL",
                "note": f"could not import dos.drivers.ci_status ({exc}) — advisory pass"}

    verdict = ci_status.status_of(candidate_sha, repo=repo)
    v = verdict.verdict
    # RED is the ONLY value that blocks; everything else is an advisory pass so the
    # row is purely additive over the local-suite gate.
    blocks = v is ci_status.Ci.RED
    return {
        "name": "ci_green",
        "pass": not blocks,
        "advisory": v is not ci_status.Ci.GREEN,  # GREEN is a hard pass; the rest are advisory
        "verdict": v.value,
        "repo": repo,
        "reason": verdict.reason,
        "failing": list(verdict.failing),
        "note": (
            "CI reports a RED build at the candidate — promotion blocked"
            if blocks else
            "CI is GREEN at the candidate — verified against the third-party record"
            if v is ci_status.Ci.GREEN else
            f"CI verdict {v.value} is advisory only; the local pytest row remains "
            "the load-bearing suite gate"
        ),
    }


def gate_tag_age(candidate_tag: str, window_days: int) -> dict:
    """Candidate tag must have soaked >= window_days."""
    cand_epoch = tag_commit_date_epoch(candidate_tag)
    now_epoch = head_now_epoch()
    if cand_epoch is None or now_epoch is None:
        return {"name": "tag_age", "pass": False,
                "reason": "could not read commit dates", "window_days": window_days}
    age_days = round((now_epoch - cand_epoch) / 86400.0, 2)
    return {
        "name": "tag_age",
        "pass": age_days >= window_days,
        "age_days": age_days,
        "window_days": window_days,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate-context for the DOS /stable-release skill."
    )
    parser.add_argument("--codename", required=True,
                        help="Stable codename, e.g. 2026-06-aardvark")
    parser.add_argument("--from", dest="from_tag", default=None,
                        help="Pin the candidate vX.Y.Z (default: latest semver tag)")
    parser.add_argument("--window-days", type=int, default=3,
                        help="Min soak age for the candidate tag (default 3)")
    parser.add_argument("--skip-pytest", action="store_true",
                        help="Skip the suite run (smoke / dry inspection only)")
    parser.add_argument("--skip-ci", action="store_true",
                        help="Skip the CI-oracle row (smoke / offline inspection)")
    parser.add_argument("--repo", default="anthony-chaudhary/dos-kernel",
                        help="owner/name to read CI checks from "
                             "(default: anthony-chaudhary/dos-kernel — this project's own pipeline)")
    parser.add_argument("--force-promote", action="store_true",
                        help="Report blockers but still set all_green-eligible "
                             "(the skill still records the rationale)")
    args = parser.parse_args()

    root = repo_root()
    blockers: list[str] = []

    if not CODENAME_RE.match(args.codename):
        blockers.append(
            f"codename {args.codename!r} fails ^\\d{{4}}-\\d{{2}}-[a-z][a-z0-9-]{{2,20}}$ "
            "(expected YYYY-MM-<word>, e.g. 2026-06-aardvark)"
        )

    candidate_tag = args.from_tag or latest_semver_tag()
    if candidate_tag is None:
        blockers.append("no vX.Y.Z tag exists to promote — cut a rolling /release first")
        payload = {
            "candidate_tag": None,
            "codename": args.codename,
            "summary": {"all_green": False, "blockers": blockers},
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    candidate_sha = tag_sha(candidate_tag)
    idem = idempotency(args.codename, candidate_sha, root)
    collision = stable_tag_collision(candidate_sha) if candidate_sha else None
    # A `stable/<codename>` tag for THIS codename is an idempotent re-run, not a
    # collision — exclude it from the collision-blocker check (a DIFFERENT-codename
    # tag on the same SHA is still a real "already promoted" conflict).
    if collision and collision != f"stable/{args.codename}":
        blockers.append(
            f"candidate {candidate_tag} ({candidate_sha[:8] if candidate_sha else '?'}) "
            f"is already promoted as {collision}"
        )
    # The one idempotency case that IS a blocker: this codename already names a
    # DIFFERENT commit (the word was reused) — stop, pick a fresh codename.
    if idem["tag_exists"] and idem["tag_matches_candidate"] is False:
        blockers.append(
            f"codename {args.codename!r} already tags a DIFFERENT commit "
            f"({(idem['tag_sha'] or '?')[:8]} != candidate {(candidate_sha or '?')[:8]}) "
            "-- pick a fresh codename (a stable tag is never re-pointed)"
        )

    gate = {
        "pytest_suite_green": gate_pytest(root, args.skip_pytest),
        "ci_green": gate_ci_green(candidate_sha, args.repo, args.skip_ci),
        "dos_verify_clean": gate_dos_verify(root),
        "tag_age": gate_tag_age(candidate_tag, args.window_days),
    }
    for row in gate.values():
        if not row.get("pass", False):
            blockers.append(f"gate row '{row['name']}' did not pass")

    all_green = len(blockers) == 0
    payload = {
        "candidate_tag": candidate_tag,
        "candidate_sha": candidate_sha,
        "codename": args.codename,
        "window_days": args.window_days,
        "previous_stable": previous_stable(),
        "tag_collision": bool(collision),
        "idempotency": idem,
        "gate": gate,
        "summary": {
            "all_green": all_green or args.force_promote,
            "blockers": blockers,
            "forced": args.force_promote and not all_green,
        },
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
