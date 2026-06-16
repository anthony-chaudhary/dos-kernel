#!/usr/bin/env python3
"""Decide whether to cut a release right now — the automated `/release` Step 0+2.

Today every release is a human running `/release`: a person reads the commits
since the last tag, judges whether they are a coherent shippable unit, picks the
semver level (patch / minor / major), and cuts the tag. That manual gate stalls —
the proof is 240+ commits sitting unreleased behind v0.26.0 while the version
stayed at 0.26.0. This script is that judgment, made mechanical, so a cron can
run it on a cadence (the `.github/workflows/release-cadence.yml` tick).

It is a DECISION, not a mutation: read-only on the repo, JSON verdict on stdout.
``scripts/release_cut.py`` is the half that actually bumps + commits; this half
only answers "release or hold, and at what version".

The verdict shape (stdout JSON):
  {
    "decision":     "release" | "hold",
    "level":        "patch" | "minor" | "major" | null,   # null on hold
    "next_version": "X.Y.Z" | null,
    "last_tag":     "vX.Y.Z" | null,
    "n_commits":    int,                  # commits since last_tag
    "reason":       "<one line>",         # WHY release/hold — the typed cause
    "themes":       ["scope", ...],       # conventional-commit scopes, for notes
    "blockers":     ["<typed gate>", ...] # the gates that vetoed a release (hold)
  }

Exit code: 0 = release, 2 = hold, 1 = usage/internal error. (2-not-1 so a hold
is distinguishable from a crash by the workflow.)

The should-release predicate (ALL must hold, else HOLD with the failing gate
named in `blockers`):
  * there is at least one commit since the last tag (NOTHING_TO_SHIP otherwise);
  * the range is MEANINGFUL — at least `--min-substantive` (default 1) commits
    since the last tag change behaviour (feat/fix/perf/revert/breaking), so an
    hourly tick over pure doc/chore churn HOLDs (BELOW_SIGNIFICANCE) rather than
    cutting a version every hour; `--force` bypasses this one gate for an
    explicitly-requested cut (the CI/drift/parse gates still bind);
  * the trunk CI base is green (`ci_on_head.status == "green"`) — a release cut
    on a red base inherits the red and the publish ci-green witness refuses it
    (docs/295 P1). `unknown` (gh offline) is treated as a soft pass unless
    --require-ci-green is set, so an offline cadence tick can still decide;
  * every workflow file parses (an unparseable workflow fails CI in 0s);
  * the version markers do not already disagree (a pre-existing drift must be
    fixed before a bump, never papered over).

The semver auto-rule (the judgment a human makes in `/release` Step 2, encoded):
classify each commit subject's conventional-commit prefix and take the HIGHEST
level seen across the range —
  * a `!` bang (`feat!:`, `fix(x)!:`) or a `BREAKING CHANGE` body marker → major;
  * a `feat` (with or without scope) → minor;
  * everything else (`fix`, `docs`, `build`, `chore`, `refactor`, `test`,
    `perf`, `ci`, a bare `area:` prefix, or an unrecognized subject) → patch.
A release with only fixes/docs is a patch; one new feature makes it a minor; a
breaking marker makes it a major. (Conservative by construction: an unknown
prefix counts as patch, never silently inflating the bump.)

This is **dev / release tooling, not kernel** — it operates ON the package but
is never imported BY it (`dos.*` imports nothing under `scripts/`). It reuses
the existing collectors rather than re-deriving git state: `release_context.py`
for the git+CI+drift digest. It is loaded by path (scripts/ is not an importable
package), the same convention `tests/test_release_bump.py` uses.

Usage:
  python scripts/release_decide.py                 # human-readable verdict
  python scripts/release_decide.py --json          # machine verdict on stdout
  python scripts/release_decide.py --require-ci-green   # treat unknown CI as a blocker
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# Order matters: index in this tuple IS the precedence (higher index wins).
_LEVELS = ("patch", "minor", "major")

# A conventional-commit type token → the bump level it implies. Anything not
# here (a bare `area:` prefix, an unknown word, a prefix-less subject) defaults
# to patch — the conservative direction (never inflate a bump on a surprise).
_TYPE_LEVEL = {
    "feat": "minor",
    "fix": "patch",
    "docs": "patch",
    "build": "patch",
    "chore": "patch",
    "refactor": "patch",
    "test": "patch",
    "perf": "patch",
    "ci": "patch",
    "style": "patch",
    "revert": "patch",
}

# `feat`, `feat(scope)`, `fix!`, `feat(api)!` — capture the type word, an optional
# `(scope)`, and a trailing `!` (the breaking-change shorthand). Mirrors the
# Conventional Commits 1.0 grammar; the type is lowercased before lookup.
_CC_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:")
# A `vX.Y.Z:` subject is a prior release commit — it carries no feature signal
# and must never count toward the next bump (it would otherwise read as an
# unknown prefix → patch, which is harmless, but skip it for honest themes).
_VERSION_SUBJECT_RE = re.compile(r"^v\d+\.\d+\.\d+:")
# Body marker for a breaking change (the long form of the `!` shorthand).
_BREAKING_BODY_RE = re.compile(r"\bBREAKING[ -]CHANGE\b")


def _max_level(levels: list[str]) -> str:
    """The highest-precedence level in `levels` (patch < minor < major)."""
    best = 0
    for lv in levels:
        if lv in _LEVELS:
            best = max(best, _LEVELS.index(lv))
    return _LEVELS[best]


def classify_subject(subject: str, body: str = "") -> str:
    """The bump level a single commit (subject [+ body]) implies.

    Pure function — the unit the semver auto-rule is tested against. A `!` bang
    or a `BREAKING CHANGE` body marker is major regardless of type; otherwise the
    type word maps via `_TYPE_LEVEL`, defaulting to patch for anything unknown.
    """
    s = subject.strip()
    m = _CC_RE.match(s)
    if m and m.group("bang"):
        return "major"
    if body and _BREAKING_BODY_RE.search(body):
        return "major"
    if m:
        return _TYPE_LEVEL.get(m.group("type").lower(), "patch")
    return "patch"


def decide_level(commits: list[dict]) -> tuple[str, list[str]]:
    """(level, themes) from the commit list.

    `commits` is `release_context`'s `commits_since_tag` shape — each a dict with
    at least a `subject`. `level` is the max across all non-release-commit
    subjects; `themes` is the ordered-unique set of conventional-commit scopes
    (for the release-notes headline). A range with no feature signal is patch.
    """
    levels: list[str] = []
    themes: list[str] = []
    seen_scopes: set[str] = set()
    for c in commits:
        subj = str(c.get("subject") or "")
        if _VERSION_SUBJECT_RE.match(subj.strip()):
            continue  # a prior release commit carries no bump signal
        levels.append(classify_subject(subj, str(c.get("body") or "")))
        m = _CC_RE.match(subj.strip())
        if m and m.group("scope"):
            scope = m.group("scope").strip()
            if scope and scope not in seen_scopes:
                seen_scopes.add(scope)
                themes.append(scope)
    level = _max_level(levels) if levels else "patch"
    return level, themes


# A release is "meaningful" (worth a version number) when it carries real,
# user-visible substance — not when the only thing since the last tag is a doc
# tweak or a chore. These types CHANGE BEHAVIOUR a consumer can observe; a range
# that touches none of them is churn the hourly tick should HOLD on (the operator
# directive: "every hour BUT only if there is enough for a meaningful release").
_SUBSTANTIVE_TYPES = {"feat", "fix", "perf", "revert"}


def significance(commits: list[dict]) -> dict:
    """How much real shippable substance is in the range — the meaningful-release
    signal. Pure; mirrors `decide_level`'s parse so the two never disagree.

    Returns {substantive, total, has_breaking, kinds} where `substantive` counts
    commits whose conventional-commit type is behaviour-changing (feat/fix/perf/
    revert) or carries a breaking marker. A prior `vX.Y.Z:` release commit is
    skipped (it is the bump itself, not new substance).
    """
    substantive = 0
    total = 0
    has_breaking = False
    kinds: dict[str, int] = {}
    for c in commits:
        subj = str(c.get("subject") or "").strip()
        if _VERSION_SUBJECT_RE.match(subj):
            continue
        total += 1
        body = str(c.get("body") or "")
        m = _CC_RE.match(subj)
        typ = (m.group("type").lower() if m else "") or "(none)"
        kinds[typ] = kinds.get(typ, 0) + 1
        breaking = bool(m and m.group("bang")) or bool(_BREAKING_BODY_RE.search(body))
        if breaking:
            has_breaking = True
        if breaking or typ in _SUBSTANTIVE_TYPES:
            substantive += 1
    return {
        "substantive": substantive,
        "total": total,
        "has_breaking": has_breaking,
        "kinds": kinds,
    }


def next_version(last_tag: "str | None", level: str) -> str:
    """Compute the next semver from `last_tag` and the chosen `level`.

    No tag yet → the first release is 0.1.0 (a minor from the implicit 0.0.0),
    or 0.0.1/1.0.0 by level. A malformed tag falls back to 0.0.0 as the base so
    the bump still produces a valid semver rather than crashing.
    """
    m = SEMVER_RE.match(last_tag or "")
    major, minor, patch = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _context_payload(root: Path, limit_commits: int) -> dict:
    """The release_context digest, via subprocess (so its CLI contract is the
    boundary — robust to it being run from any cwd). `--no-previews` keeps the
    payload small; we only read git/CI/version fields, never the diff previews.
    """
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "release_context.py"),
         "--no-previews", "--limit-commits", str(limit_commits)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(root),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"release_context.py failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    return json.loads(proc.stdout)


def repo_root() -> Path:
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        top = ""
    return Path(top) if top else Path.cwd()


def decide(
    payload: dict,
    *,
    require_ci_green: bool,
    min_substantive: int = 1,
    force: bool = False,
) -> dict:
    """The pure decision: payload (release_context shape) → verdict dict.

    Separated from I/O so the test can drive it with a synthetic payload and
    never touch git. Returns the full verdict; the caller adds nothing.

    `min_substantive` is the meaningful-release floor: HOLD unless at least this
    many commits since the last tag actually change behaviour (feat/fix/perf/
    revert/breaking) — so an hourly tick over a stream of doc/chore churn is a
    cheap no-op, and only a substantive accumulation cuts a version. `force`
    bypasses ONLY this significance floor (the "specifically requested" escape);
    the CI/drift/parse gates still bind — a forced release on a red base is still
    refused.
    """
    commits = payload.get("commits_since_tag") or []
    last_tag = payload.get("last_tag")
    n = len(commits)
    sig = significance(commits)

    blockers: list[str] = []

    # Gate 1 — is there anything to ship?
    if n == 0:
        blockers.append("NOTHING_TO_SHIP")

    # Gate 1b — is it MEANINGFUL? An hourly cadence over a hot trunk would
    # otherwise cut a version every time a doc lands. Require real substance
    # (feat/fix/perf/revert/breaking) above the floor; `--force` bypasses this
    # one gate for an explicitly-requested cut. (Skipped when there is nothing to
    # ship at all — NOTHING_TO_SHIP already covers that, and we never stack two
    # reasons for the empty range.)
    elif not force and sig["substantive"] < min_substantive:
        blockers.append("BELOW_SIGNIFICANCE")

    # Gate 2 — the trunk CI base. A red base is inherited by the release commit
    # and the publish ci-green witness would refuse it. `unknown` (gh offline)
    # is a soft pass unless the caller demands a hard green.
    ci = (payload.get("ci_on_head") or {}).get("status")
    if ci == "red":
        blockers.append("CI_BASE_RED")
    elif ci == "none":
        blockers.append("CI_BASE_NONE")
    elif ci == "unknown" and require_ci_green:
        blockers.append("CI_STATE_UNKNOWN")

    # Gate 3 — every workflow file parses (a broken one fails CI in 0s).
    wf = payload.get("workflows_parse_ok") or {}
    if wf.get("ok") is False:
        blockers.append("WORKFLOW_UNPARSEABLE")

    # Gate 4 — the version markers must not already disagree.
    if (payload.get("version_files") or {}).get("drift") is True:
        blockers.append("VERSION_DRIFT")

    level, themes = decide_level(commits)

    if blockers:
        # Surface the most actionable blocker first in the reason line.
        head = blockers[0]
        reason = {
            "NOTHING_TO_SHIP": f"no commits since {last_tag or '(no tag)'} — nothing to release",
            "BELOW_SIGNIFICANCE": (
                f"{sig['substantive']} substantive commit(s) of {sig['total']} since "
                f"{last_tag or '(no tag)'} (floor {min_substantive}) — only churn to ship; "
                f"holding for a meaningful release (--force to override)"
            ),
            "CI_BASE_RED": "trunk CI base is red — fix forward before releasing (docs/295 P1)",
            "CI_BASE_NONE": "no decisive trunk CI run — cannot confirm a green base",
            "CI_STATE_UNKNOWN": "CI state unknown (gh offline) and --require-ci-green set",
            "WORKFLOW_UNPARSEABLE": "a workflow file does not parse — CI would run zero jobs",
            "VERSION_DRIFT": "version markers already disagree — reconcile before bumping",
        }.get(head, head)
        return {
            "decision": "hold", "level": None, "next_version": None,
            "last_tag": last_tag, "n_commits": n, "reason": reason,
            "themes": themes, "blockers": blockers,
            "substantive": sig["substantive"],
        }

    nv = next_version(last_tag, level)
    forced = " (forced — below the significance floor)" if (
        force and sig["substantive"] < min_substantive
    ) else ""
    return {
        "decision": "release", "level": level, "next_version": nv,
        "last_tag": last_tag, "n_commits": n,
        "reason": (f"{n} commit(s) since {last_tag or '(no tag)'} "
                   f"({sig['substantive']} substantive), gates green; "
                   f"highest signal = {level} → {nv}{forced}"),
        "themes": themes, "blockers": [],
        "substantive": sig["substantive"],
    }


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit the verdict as JSON on stdout")
    p.add_argument("--require-ci-green", action="store_true",
                   help="Treat an unknown CI state (gh offline) as a blocker, "
                        "not a soft pass")
    p.add_argument("--limit-commits", type=int, default=300,
                   help="Max commits to inspect since the last tag (default 300)")
    p.add_argument("--min-substantive", type=int, default=1,
                   help="Meaningful-release floor: HOLD unless at least this many "
                        "commits since the last tag change behaviour "
                        "(feat/fix/perf/revert/breaking). Default 1 — an hourly "
                        "tick over doc/chore churn is then a no-op.")
    p.add_argument("--force", action="store_true",
                   help="Bypass ONLY the significance floor (the explicitly-"
                        "requested cut). CI/drift/parse gates still bind.")
    args = p.parse_args(argv)

    root = repo_root()
    try:
        payload = _context_payload(root, args.limit_commits)
    except Exception as exc:  # the collector is the boundary; a failure is a usage error
        print(f"release-decide: could not read release context: {exc}", file=sys.stderr)
        return 1

    verdict = decide(
        payload,
        require_ci_green=args.require_ci_green,
        min_substantive=args.min_substantive,
        force=args.force,
    )

    if args.as_json:
        json.dump(verdict, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        d = verdict["decision"].upper()
        print(f"release-decide: {d} — {verdict['reason']}")
        if verdict["decision"] == "release":
            print(f"  {verdict['last_tag'] or '(no tag)'} → v{verdict['next_version']} "
                  f"({verdict['level']}, {verdict['n_commits']} commits)")
            if verdict["themes"]:
                print(f"  themes: {', '.join(verdict['themes'][:12])}")
        else:
            print(f"  blockers: {', '.join(verdict['blockers'])}")

    return 0 if verdict["decision"] == "release" else 2


if __name__ == "__main__":
    raise SystemExit(main())
