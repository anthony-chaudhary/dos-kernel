"""scoreboard_surfaces — the per-repo badge + verdict.json emitters (docs/312, issue #85).

Project an already-computed drift sweep into the two consumption surfaces the
scoreboard's opt-in tier serves:

  * **`verdict.json`** — the machine endpoint (the context7 / Tessl-registry
    mechanic): a VERSIONED per-repo trust verdict an agent can fetch before
    believing a dependency's agent-written changelog. Schema id
    `dos-scoreboard-verdict/v1`; the key roster IS the schema — change it,
    bump the version. Carries the receipts (the offending SHAs), the grader
    version, and the methodology URL, so the number is inspectable all the
    way down.
  * **`badge.json`** — the embed surface (the Snyk-Advisor / OpenSSF-Scorecard
    mechanic): a shields.io-compatible endpoint object derived from the
    verdict by a pure function — the badge can never say something the
    verdict does not. Message vocabulary is COUNTS with an as-of date
    ("audited clean (as of …)" / "N unwitnessed of M (as of …)"), never an
    honesty grade (the Wall-3 line).

This is DOS dev tooling, not a kernel module: it lives under `scripts/` and
nothing under `src/dos/` knows it exists (the same one-way arrow as
`drift_scoreboard.py`). It computes NO new verdict — the input is the
`sweep_summary` fold as printed by `dos commit-audit --sweep --json`, or the
per-repo dict `drift_scoreboard.py` writes under `<out>/per-repo/`
(auto-detected by its `summary` key).

The docs/307 ethics floor applies at the PUBLISH step, not here: a per-repo
verdict names a repo and its SHAs, so one is published only for a repo that
REGISTERED (opt-in; see the methodology page's registration section). The
emitter just writes files; tracking them under `docs/scoreboard/<org>/<repo>/`
is the publication act, and this repository's own entry is the first.

Usage:
    dos commit-audit --sweep --json BASE..HEAD > self-sweep.json
    python scripts/scoreboard_surfaces.py --summary self-sweep.json \
        --repo <org>/<repo> --out docs/scoreboard/ \
        --range "full visible history" --head-sha $(git rev-parse HEAD)
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCHEMA_ID = "dos-scoreboard-verdict/v1"
GRADER_TOOL = "dos-kernel commit-audit --sweep"
METHODOLOGY_URL = ("https://anthony-chaudhary.github.io/dos-kernel/"
                   "drift-scoreboard.html")
ADVISORY = ("Drift is a claim-vs-diff mismatch, never a correctness or "
            "malice grade.")
BADGE_LABEL = "commit-claims"

# `<org>/<repo>` only — one slash, plain GitHub-name bytes on both sides. The
# repo string becomes an output PATH, so this is also the traversal guard.
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# The payload builders. Both PURE — I/O stays in main().
# ---------------------------------------------------------------------------


def verdict_payload(summary: dict, *, repo: str, generated: str,
                    grader_version: str, range_described: str,
                    head_sha: str | None = None,
                    methodology_url: str = METHODOLOGY_URL) -> dict:
    """The `dos-scoreboard-verdict/v1` object from an existing sweep fold. Pure.

    `summary` is either the bare `sweep_summary` dict or the per-repo wrapper
    `drift_scoreboard.sweep_repo` returns (detected by its `summary` key).
    """
    if "summary" in summary:
        summary = summary["summary"]
    if not _REPO_RE.match(repo) or {".", ".."} & set(repo.split("/")):
        raise ValueError(f"repo must be <org>/<repo>, got {repo!r}")
    return {
        "schema": SCHEMA_ID,
        "repo": repo,
        "generated": generated,
        "grader": {"tool": GRADER_TOOL, "version": grader_version},
        "methodology": methodology_url,
        "opt_in": True,
        "range": {
            "described": range_described,
            "head_sha": head_sha,
            "commits_audited": summary.get("commits", 0),
        },
        "claims": {
            "checkable": summary.get("checkable", 0),
            "witnessed": summary.get("witnessed", 0),
            "unwitnessed": summary.get("unwitnessed", 0),
            "abstained": summary.get("abstained", 0),
            "drift_rate": summary.get("drift_rate", 0.0),
        },
        "by_kind": summary.get("by_kind", {}),
        "receipts": {"unwitnessed_shas": list(summary.get("unwitnessed_shas", []))},
        "advisory": ADVISORY,
    }


def badge_payload(verdict: dict) -> dict:
    """The shields.io endpoint object, derived from the verdict alone. Pure.

    Closed mapping (docs/312 §1): clean → brightgreen; any unwitnessed claim
    → the counts in orange; an empty denominator → lightgrey. The counts in
    the message ARE the verdict's counts — no second computation.
    """
    claims = verdict["claims"]
    date = verdict["generated"]
    if claims["checkable"] == 0:
        message, color = f"no checkable claims (as of {date})", "lightgrey"
    elif claims["unwitnessed"] == 0:
        message, color = f"audited clean (as of {date})", "brightgreen"
    else:
        message = (f"{claims['unwitnessed']} unwitnessed of "
                   f"{claims['checkable']} (as of {date})")
        color = "orange"
    return {
        "schemaVersion": 1,   # shields.io's own endpoint contract
        "label": BADGE_LABEL,
        "message": message,
        "color": color,
    }


# ---------------------------------------------------------------------------
# Boundary I/O.
# ---------------------------------------------------------------------------


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", required=True,
                    help="sweep JSON: `dos commit-audit --sweep --json` output "
                         "or a drift_scoreboard per-repo file")
    ap.add_argument("--repo", required=True, help="<org>/<repo> being graded")
    ap.add_argument("--out", default="docs/scoreboard",
                    help="root the artifacts land under (default docs/scoreboard)")
    ap.add_argument("--range", dest="range_described",
                    default="full visible history",
                    help="human description of the audited range")
    ap.add_argument("--head-sha", help="HEAD at sweep time — pins what was audited")
    ap.add_argument("--stamp", help="report date (default: today UTC)")
    args = ap.parse_args(argv)

    from datetime import datetime, timezone
    from dos import __version__ as grader_version  # the one-way arrow

    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    stamp = args.stamp or datetime.now(timezone.utc).date().isoformat()
    verdict = verdict_payload(summary, repo=args.repo, generated=stamp,
                              grader_version=grader_version,
                              range_described=args.range_described,
                              head_sha=args.head_sha)
    badge = badge_payload(verdict)

    dest = Path(args.out) / args.repo
    _write_json(dest / "verdict.json", verdict)
    _write_json(dest / "badge.json", badge)
    print(f"verdict: {dest / 'verdict.json'}")
    print(f"badge:   {dest / 'badge.json'}")
    print("embed:   https://img.shields.io/endpoint?url=<served URL of badge.json>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
