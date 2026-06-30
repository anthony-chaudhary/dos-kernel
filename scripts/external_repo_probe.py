#!/usr/bin/env python3
"""External-repo conformance probe for issue #199.

This is the adoption-wall probe: can DOS say useful, truthful things about a
repo that did not grow up with DOS's own commit stamp grammar?

Two rungs stay separate:

* ``dos doctor --json`` reports whether ``dos verify`` can bind recent commits
  to a unit-of-work stamp. That is the stamp-grammar/verifiability rung.
* ``dos commit-audit --sweep --json`` reports whether concrete commit-message
  claims are backed by their own diffs. That is the claim-vs-diff rung.

The default path is offline and deterministic: fold the committed
``docs/scoreboard/<org>/<repo>/sweep.json`` files. That data has the
claim-vs-diff rung only. To measure stamp-grammar fit, pass local clone paths via
``--workspace`` or ``--corpus``; the script then runs the two public CLI verbs
against those clones. It performs no network access.
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SCOREBOARD = REPO / "docs" / "scoreboard"
DEFAULT_REPORT = SCOREBOARD / "external-repo-conformance.md"
DEFAULT_STAMP = "2026-06-30"
DEFAULT_RANGE = "HEAD~200..HEAD"


@dataclass(frozen=True)
class Probe:
    repo: str
    source: str
    audit: dict[str, Any]
    verifiability: dict[str, Any] | None = None
    error: str | None = None

    @property
    def failure_modes(self) -> list[str]:
        return classify_failure_modes(self)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rate(num: int, den: int) -> float:
    return (num / den) if den else 0.0


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("summary", doc)


def _repo_name(doc: dict[str, Any], path: Path) -> str:
    if doc.get("repo"):
        return str(doc["repo"])
    # .../docs/scoreboard/<org>/<repo>/sweep.json
    return f"{path.parent.parent.name}/{path.parent.name}"


def find_scoreboard_sweeps(root: Path = SCOREBOARD) -> list[Path]:
    """Every committed per-repo scoreboard sweep, sorted for a stable fold."""
    return sorted(Path(p) for p in glob.glob(str(root / "*" / "*" / "sweep.json")))


def audit_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    commits = _int(summary.get("commits"))
    checkable = _int(summary.get("checkable"))
    witnessed = _int(summary.get("witnessed"))
    unwitnessed = _int(summary.get("unwitnessed"))
    abstained = _int(summary.get("abstained"))
    return {
        "commits": commits,
        "checkable": checkable,
        "witnessed": witnessed,
        "unwitnessed": unwitnessed,
        "abstained": abstained,
        "checkable_rate": _rate(checkable, commits),
        "abstain_rate": _rate(abstained, commits),
        "witness_rate": _rate(witnessed, checkable),
        "unwitnessed_shas": list(summary.get("unwitnessed_shas", [])),
        "by_kind": summary.get("by_kind", {}),
    }


def probe_from_scoreboard_sweep(path: Path) -> Probe:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return Probe(
        repo=_repo_name(doc, path),
        source="scoreboard-sweep",
        audit=audit_from_summary(_summary(doc)),
        verifiability=None,
    )


def probes_from_scoreboard(root: Path = SCOREBOARD) -> list[Probe]:
    return [probe_from_scoreboard_sweep(p) for p in find_scoreboard_sweeps(root)]


def parse_corpus(text: str) -> list[str]:
    """One workspace path per line; comments and blanks are skipped."""
    entries: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def _run_json(args: list[str], *, cwd: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode not in (0, 1):
        msg = (proc.stderr or proc.stdout or "").strip()
        return None, f"exit {proc.returncode}: {msg}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"bad json: {exc}"


def _workspace_label(path: Path) -> str:
    rc = subprocess.run(
        ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    remote = rc.stdout.strip()
    if remote:
        return remote.removesuffix(".git")
    return path.name


def _verifiability_from_doctor(doc: dict[str, Any]) -> dict[str, Any]:
    raw = doc.get("verifiability") or {}
    commits_read = _int(raw.get("commits_read"))
    verifiable = _int(raw.get("verifiable", raw.get("recognized")))
    recognized = _int(raw.get("recognized"))
    ship_shaped = _int(raw.get("ship_shaped"))
    named_trailer = _int(raw.get("named_trailer"))
    return {
        "commits_read": commits_read,
        "verifiable": verifiable,
        "recognized": recognized,
        "ship_shaped": ship_shaped,
        "named_trailer": named_trailer,
        "verifiable_rate": _rate(verifiable, commits_read),
        "recognized_rate": _rate(recognized, commits_read),
        "grammar": str(raw.get("grammar", "")),
    }


def probe_workspace(workspace: Path, *, audit_range: str = DEFAULT_RANGE) -> Probe:
    workspace = workspace.resolve()
    doc, doc_error = _run_json(
        [sys.executable, "-m", "dos.cli", "doctor", "--workspace", str(workspace), "--json"],
        cwd=REPO,
    )
    audit, audit_error = _run_json(
        [
            sys.executable,
            "-m",
            "dos.cli",
            "commit-audit",
            "--sweep",
            "--json",
            "--workspace",
            str(workspace),
            audit_range,
        ],
        cwd=REPO,
    )
    label = _workspace_label(workspace)
    if doc_error or audit_error or doc is None or audit is None:
        return Probe(
            repo=label,
            source="local-workspace",
            audit=audit_from_summary(audit or {}),
            verifiability=_verifiability_from_doctor(doc or {}) if doc else None,
            error="; ".join(e for e in (doc_error, audit_error) if e),
        )
    return Probe(
        repo=label,
        source="local-workspace",
        audit=audit_from_summary(audit),
        verifiability=_verifiability_from_doctor(doc),
    )


def classify_failure_modes(probe: Probe) -> list[str]:
    """Name the visible conformance gaps without turning them into a grade."""
    modes: list[str] = []
    if probe.error:
        modes.append("probe-error")
    v = probe.verifiability
    if v is None:
        modes.append("stamp-grammar-not-measured")
    else:
        commits_read = _int(v.get("commits_read"))
        verifiable = _int(v.get("verifiable"))
        ship_shaped = _int(v.get("ship_shaped"))
        recognized = _int(v.get("recognized"))
        if commits_read and verifiable == 0 and ship_shaped == 0:
            modes.append("stamp-grammar-absence")
        elif ship_shaped > recognized:
            modes.append("stamp-grammar-mismatch")
        elif commits_read and verifiable < commits_read:
            modes.append("partial-stamp-coverage")
    audit = probe.audit
    commits = _int(audit.get("commits"))
    checkable = _int(audit.get("checkable"))
    unwitnessed = _int(audit.get("unwitnessed"))
    abstained = _int(audit.get("abstained"))
    if commits and checkable == 0:
        modes.append("no-checkable-commit-claims")
    elif commits and _rate(abstained, commits) >= 0.5:
        modes.append("claim-grammar-low-fit")
    if unwitnessed:
        modes.append("claim-diff-gap")
    if not modes:
        modes.append("fits-current-rungs")
    return modes


def aggregate(probes: list[Probe]) -> dict[str, Any]:
    audit_totals = {
        "commits": 0,
        "checkable": 0,
        "witnessed": 0,
        "unwitnessed": 0,
        "abstained": 0,
    }
    ver_totals = {
        "repos": 0,
        "commits_read": 0,
        "verifiable": 0,
        "recognized": 0,
        "ship_shaped": 0,
        "named_trailer": 0,
    }
    modes: dict[str, int] = {}
    for p in probes:
        for key in audit_totals:
            audit_totals[key] += _int(p.audit.get(key))
        if p.verifiability is not None:
            ver_totals["repos"] += 1
            for key in ("commits_read", "verifiable", "recognized",
                        "ship_shaped", "named_trailer"):
                ver_totals[key] += _int(p.verifiability.get(key))
        for mode in p.failure_modes:
            modes[mode] = modes.get(mode, 0) + 1
    audit_totals["checkable_rate"] = _rate(audit_totals["checkable"], audit_totals["commits"])
    audit_totals["abstain_rate"] = _rate(audit_totals["abstained"], audit_totals["commits"])
    audit_totals["witness_rate"] = _rate(audit_totals["witnessed"], audit_totals["checkable"])
    ver_totals["verifiable_rate"] = _rate(ver_totals["verifiable"], ver_totals["commits_read"])
    ver_totals["recognized_rate"] = _rate(ver_totals["recognized"], ver_totals["commits_read"])
    return {
        "repos": len(probes),
        "audit": audit_totals,
        "verifiability": ver_totals,
        "failure_modes": dict(sorted(modes.items())),
    }


def as_json(probes: list[Probe], *, stamp: str) -> dict[str, Any]:
    return {
        "schema": "dos-external-repo-conformance/v1",
        "generated": stamp,
        "aggregate": aggregate(probes),
        "repos": [
            {
                "repo": p.repo,
                "source": p.source,
                "audit": p.audit,
                "verifiability": p.verifiability,
                "failure_modes": p.failure_modes,
                **({"error": p.error} if p.error else {}),
            }
            for p in sorted(probes, key=lambda item: item.repo.lower())
        ],
    }


def _mode_line(modes: dict[str, int]) -> str:
    if not modes:
        return "none"
    return ", ".join(f"{name} {count}" for name, count in sorted(modes.items()))


def render_report(payload: dict[str, Any]) -> str:
    stamp = payload["generated"]
    agg = payload["aggregate"]
    audit = agg["audit"]
    ver = agg["verifiability"]
    lines = [
        "# External-repo conformance probe",
        "",
        f"Generated {stamp} by `python scripts/external_repo_probe.py`.",
        "",
        "This report keeps two questions separate:",
        "",
        "- **Stamp verifiability:** can `dos verify` bind recent commits to a "
        "unit-of-work stamp under the active grammar?",
        "- **Claim auditability:** can `dos commit-audit` classify a commit "
        "message claim and check it against the commit's own diff?",
        "",
        "The committed scoreboard data measures the second rung. Run the live "
        "local-clone mode below to add the first rung.",
        "",
        "## Aggregate",
        "",
        "| measure | value |",
        "|---|---|",
        f"| repos probed | {agg['repos']} |",
        f"| commits audited by `commit-audit` | {audit['commits']:,} |",
        f"| checkable commit claims | {audit['checkable']:,} "
        f"({_pct(audit['checkable_rate'])}) |",
        f"| claims backed by their own diff | {audit['witnessed']:,} "
        f"({_pct(audit['witness_rate'])} of checkable) |",
        f"| claims not backed by their own diff | {audit['unwitnessed']:,} |",
        f"| abstained, no concrete claim | {audit['abstained']:,} "
        f"({_pct(audit['abstain_rate'])}) |",
        f"| repos with live `doctor` verifiability | {ver['repos']} |",
        f"| commits read by `doctor` | {ver['commits_read']:,} |",
        f"| `dos verify`-verifiable commits | {ver['verifiable']:,} "
        f"({_pct(ver['verifiable_rate'])}) |",
        f"| failure-mode counts | {_mode_line(agg['failure_modes'])} |",
        "",
        "## Reading",
        "",
        "A high backed rate means the claim-vs-diff auditor can check the commits "
        "that make concrete claims. It does **not** mean `dos verify` can close "
        "work units in that repo. The adoption wall is the gap between those "
        "rungs: a Conventional-Commits repo can have honest, backed diffs while "
        "`dos verify PLAN PHASE` still resolves through `none` because no commit "
        "names a unit of work.",
        "",
    ]
    if ver["repos"] == 0:
        lines += [
            "No live `doctor` data is present in this committed offline run. "
            "That is intentional: the checked-in scoreboard artifacts are "
            "network-free `commit-audit` sweeps. To measure stamp grammar fit, "
            "run the same script over local clones.",
            "",
        ]
    else:
        lines += [
            "Live `doctor` data is present, so the table below includes the "
            "stamp-verifiable rate for those local clones.",
            "",
        ]
    lines += [
        "## Repositories",
        "",
        "| repo | source | commit claims checkable | backed | skipped | "
        "stamp-verifiable | modes |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for item in payload["repos"]:
        audit_i = item["audit"]
        ver_i = item.get("verifiability")
        stamp_rate = None if ver_i is None else ver_i.get("verifiable_rate", 0.0)
        lines.append(
            f"| {item['repo']} | {item['source']} | "
            f"{_pct(audit_i.get('checkable_rate', 0.0))} | "
            f"{_pct(audit_i.get('witness_rate', 0.0))} | "
            f"{_pct(audit_i.get('abstain_rate', 0.0))} | "
            f"{_pct(stamp_rate)} | {', '.join(item['failure_modes'])} |"
        )
    lines += [
        "",
        "## Reproduce",
        "",
        "Offline, from committed scoreboard data:",
        "",
        "```bash",
        "python scripts/external_repo_probe.py --from-scoreboard "
        "--out docs/scoreboard/external-repo-conformance.md "
        f"--stamp {stamp}",
        "python scripts/external_repo_probe.py --from-scoreboard "
        "--out docs/scoreboard/external-repo-conformance.md "
        f"--stamp {stamp} --check",
        "```",
        "",
        "Live, from local clones listed one per line:",
        "",
        "```bash",
        "python scripts/external_repo_probe.py --no-scoreboard "
        "--corpus local-clones.txt --range HEAD~200..HEAD "
        "--out _scratch/external-repo-conformance.md",
        "```",
        "",
        "Use the live path for the stamp-grammar question. Use the offline path "
        "for a deterministic regression check over the published scoreboard "
        "claim-audit corpus.",
        "",
    ]
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_probes(args: argparse.Namespace) -> list[Probe]:
    probes: list[Probe] = []
    if args.from_scoreboard:
        probes.extend(probes_from_scoreboard(Path(args.scoreboard_root)))
    for raw in args.workspace or []:
        probes.append(probe_workspace(Path(raw), audit_range=args.range))
    if args.corpus:
        for raw in parse_corpus(Path(args.corpus).read_text(encoding="utf-8")):
            probes.append(probe_workspace(Path(raw), audit_range=args.range))
    return probes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-scoreboard", dest="from_scoreboard",
                    action="store_true", default=True,
                    help="fold committed docs/scoreboard sweep.json files "
                    "(default)")
    ap.add_argument("--no-scoreboard", dest="from_scoreboard",
                    action="store_false",
                    help="do not fold committed scoreboard data")
    ap.add_argument("--scoreboard-root", default=str(SCOREBOARD),
                    help="scoreboard root to fold (default docs/scoreboard)")
    ap.add_argument("--workspace", action="append",
                    help="local git workspace to probe live; repeatable")
    ap.add_argument("--corpus",
                    help="text file of local git workspaces to probe live")
    ap.add_argument("--range", default=DEFAULT_RANGE,
                    help=f"commit range for live commit-audit sweeps "
                    f"(default {DEFAULT_RANGE})")
    ap.add_argument("--stamp", default=DEFAULT_STAMP,
                    help=f"report date (default {DEFAULT_STAMP})")
    ap.add_argument("--out", default=str(DEFAULT_REPORT),
                    help="markdown report path")
    ap.add_argument("--json-out", help="optional JSON payload path")
    ap.add_argument("--check", action="store_true",
                    help="compare the generated markdown to --out and exit "
                    "non-zero on drift")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    probes = build_probes(args)
    if not probes:
        print("external-repo-probe: no inputs", file=sys.stderr)
        return 2
    payload = as_json(probes, stamp=args.stamp)
    report = render_report(payload)
    if args.check:
        out_path = Path(args.out)
        if not out_path.exists():
            print(f"DRIFT: {out_path} does not exist", file=sys.stderr)
            return 1
        if out_path.read_text(encoding="utf-8") != report:
            print(f"DRIFT: {out_path} does not match probe inputs", file=sys.stderr)
            return 1
        print(f"OK: {out_path} matches probe inputs ({len(probes)} repos)")
        return 0
    _write(Path(args.out), report)
    if args.json_out:
        _write(Path(args.json_out), json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out} ({len(probes)} repos)")
    if args.json_out:
        print(f"json: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
