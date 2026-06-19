#!/usr/bin/env python3
"""DOS proc-reaper — adjudicate runaway processes for safe reaping.

The reusable form of the one-time "a few processes are eating crazy resources"
cleanup. A hot machine running a fleet of headless agents accumulates two kinds of
resource leak that nothing reaps on its own:

1. **Orphaned whole-disk scanners.** An agent shell command like
   ``find / -path '*foo*'`` walks the entire filesystem. When its parent process
   dies the scan is re-parented and keeps spinning forever — one observed instance
   had burned ~2.9 CPU-hours on a single ``find /`` before it was found. The class
   is: a known scan tool (find/grep/rg/...), a root-ish search root, a DEAD parent,
   and non-trivial CPU-time already spent.

2. **Memory-leaked outliers.** A single worker that leaks for hours can reach tens
   of GB of resident memory (one observed python held ~62 GB), pinning the box. The
   class is: resident set over a hard ceiling.

This applies the DOS distrust posture to that sweep: it never kills a process on its
name alone or a guess. It REAPs only on **evidence of waste** (a dead-parent scanner
that has already burned real CPU, or RSS over a hard cap) and never touches anything
in a protected set — the fleet's own supervisors/workers, this reaper's own process
tree, or any process whose command line matches a keep-pattern.

> **The classifier is pure; the I/O is a thin shell.** ``classify_process`` decides
> KEEP / REAP / REFUSE from a plain :class:`ProcRecord` — no OS calls. The shell
> (``gather_processes``) reads the live process table to build those records, then
> the classifier rules. This is what makes the safety rules unit-testable without a
> live machine: drive the pure function with synthetic records and assert the verdict.

Three rules keep it conservative (each a foot-gun the sweep must never fire on):

1. **A protected command line -> KEEP, always.** The fleet's supervisors, watchdogs,
   apply-loops, MCP servers, and this reaper's own tree are never candidates, no
   matter how much CPU/RAM they use — stopping a live worker is the operator's call,
   not a cron's. Keep-patterns are matched case-insensitively as substrings.
2. **A living parent -> not an orphan.** A scanner with a live parent may be a real
   in-flight pipeline (``tail -f | grep`` feeding a dashboard); only a DEAD-parent
   scanner that has burned past the CPU floor is REAPed. A live-parent heavy scanner
   is surfaced as REFUSE (a finding), never killed.
3. **Under every threshold -> KEEP.** A scanner below the CPU floor, or a process
   under the RSS cap, is normal; it is KEEP, not a finding.

**Dry-run is the default.** Without ``--apply`` the tool only prints the verdict table
(or ``--json``) — it kills nothing. ``--apply`` terminates the REAP rows only; REFUSE
and KEEP rows are never touched. This is dev / workflow tooling: it operates ON the
machine but is never imported BY the ``dos`` package — nothing under ``src/dos/``
imports anything in ``scripts/``. It names no host: the keep-patterns and thresholds
are defaults a caller overrides for its own fleet.

Usage::

    python scripts/proc_reaper.py                    # dry-run table, exit 0
    python scripts/proc_reaper.py --json              # machine-readable plan
    python scripts/proc_reaper.py --apply             # actually kill the REAP rows
    python scripts/proc_reaper.py --rss-cap-gb 16     # tighten the memory ceiling
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Policy defaults — a caller overrides these for its own fleet. Names no host:
# these are generic agent-leak signatures, not a specific machine's processes.
# --------------------------------------------------------------------------- #

# Shell tools that, pointed at a root-ish path, walk a huge subtree. An orphaned
# one of these is the "find /" leak class. Matched against the executable stem.
_SCANNER_STEMS: frozenset[str] = frozenset(
    {"find", "grep", "rg", "ag", "ack", "fd", "ls", "du", "wc", "sort"}
)

# A search root counts as "root-ish" (whole-disk-ish) when it is a filesystem root
# or a top-level drive. Anything deeper is a scoped search and never a reap target.
_ROOTISH_ARGS: frozenset[str] = frozenset({"/", "\\", ".", "c:", "c:\\", "c:/"})

# Substrings (case-insensitive) that mark a process as a protected fleet member or
# infrastructure we must never auto-kill. The reaper's own tree is added at runtime.
_DEFAULT_KEEP_PATTERNS: tuple[str, ...] = (
    "supervise",
    "watchdog",
    "apply-loop",
    "apply_loop",
    "tailor-tick",
    "tailor_tick",
    "tailor_batch",
    "careers",
    "fleet_",
    "fleet-",
    "heartbeat",
    "control_pane",
    "aos_supervise",
    "dos_mcp.server",
    "dos pulse",
    "cleanup_sweep",
    "proc_reaper",
)

# A dead-parent scanner is only reaped once it has burned at least this much CPU
# time — a freshly-spawned scan with a momentarily-missing parent is given grace.
_DEFAULT_CPU_FLOOR_SEC: float = 120.0

# A process over this resident-set ceiling is a memory-leak outlier. Generous by
# default (most real workers stay well under); a caller tightens it per machine.
_DEFAULT_RSS_CAP_GB: float = 32.0


# --------------------------------------------------------------------------- #
# The record + the pure classifier — no OS calls below this line.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProcRecord:
    """One process, reduced to the fields the classifier rules on. Built by the
    I/O shell from the live table; the classifier never reaches back to the OS."""

    pid: int
    name: str                # executable stem, lowercased (e.g. "find")
    cmdline: str             # full command line (may be empty on Windows)
    parent_alive: bool       # is the parent PID still a live process?
    cpu_sec: float           # cumulative CPU-seconds this process has used
    rss_bytes: int           # resident set size
    search_root: str = ""    # for a scanner: its first path argument, lowercased


@dataclass
class Verdict:
    """The classifier's decision for one process. `reason` is a closed token; the
    pure classifier never frees a byte — `--apply` acts on REAP rows in the shell."""

    pid: int
    verdict: str             # "KEEP" | "REAP" | "REFUSE"
    reason: str              # closed token, e.g. "ORPHANED_SCANNER"
    summary: str             # one human line
    record: ProcRecord = field(repr=False, default=None)


def _is_protected(cmdline: str, name: str, keep_patterns: tuple[str, ...]) -> bool:
    """True if this process matches any keep-pattern (case-insensitive substring)
    over its command line. The executable stem is also checked so a protected
    process with an empty cmdline (Windows hides some) is still matched by name."""
    hay = f"{cmdline} {name}".lower()
    return any(pat.lower() in hay for pat in keep_patterns)


def _is_rootish(search_root: str) -> bool:
    """True if a scanner's search root is whole-disk-ish (a root or a bare drive).
    A scoped search (a deep project path) is never a reap target — only runaway
    whole-tree walks are."""
    return search_root.strip().lower() in _ROOTISH_ARGS


def classify_process(
    rec: ProcRecord,
    *,
    keep_patterns: tuple[str, ...] = _DEFAULT_KEEP_PATTERNS,
    cpu_floor_sec: float = _DEFAULT_CPU_FLOOR_SEC,
    rss_cap_bytes: int = int(_DEFAULT_RSS_CAP_GB * 1024**3),
) -> Verdict:
    """Decide KEEP / REAP / REFUSE for one process — pure, no I/O.

    Order matters: protection wins over every waste signal, so a heavy fleet worker
    is KEEP not REAP. Then the two waste classes are checked. Everything else KEEPs.
    """
    # Rule 1 — a protected process is KEEP regardless of footprint.
    if _is_protected(rec.cmdline, rec.name, keep_patterns):
        return Verdict(rec.pid, "KEEP", "PROTECTED",
                       f"protected fleet/infra process ({rec.name})", rec)

    # Waste class A — an orphaned whole-disk scanner past the CPU floor.
    if rec.name in _SCANNER_STEMS and _is_rootish(rec.search_root):
        if not rec.parent_alive and rec.cpu_sec >= cpu_floor_sec:
            return Verdict(
                rec.pid, "REAP", "ORPHANED_SCANNER",
                f"orphaned {rec.name} {rec.search_root!r} scan, "
                f"dead parent, {rec.cpu_sec:.0f} CPU-sec spent", rec)
        if not rec.parent_alive:
            # Dead parent but under the CPU floor — too young to be sure it's wedged.
            return Verdict(
                rec.pid, "KEEP", "SCANNER_TOO_YOUNG",
                f"orphaned {rec.name} scan but only {rec.cpu_sec:.0f} CPU-sec "
                f"(< {cpu_floor_sec:.0f}s floor) — grace", rec)
        # Live parent — may be a real pipeline; surface, never kill.
        return Verdict(
            rec.pid, "REFUSE", "SCANNER_LIVE_PARENT",
            f"{rec.name} {rec.search_root!r} scan with a LIVE parent "
            f"— surfaced, not reaped", rec)

    # Waste class B — a memory-leak outlier over the RSS ceiling.
    if rec.rss_bytes >= rss_cap_bytes:
        gb = rec.rss_bytes / 1024**3
        cap_gb = rss_cap_bytes / 1024**3
        return Verdict(
            rec.pid, "REAP", "MEMORY_OUTLIER",
            f"{rec.name} holds {gb:.1f} GB RSS — over the {cap_gb:.0f} GB cap", rec)

    # Everything else is normal.
    return Verdict(rec.pid, "KEEP", "NORMAL", f"{rec.name} within limits", rec)


# --------------------------------------------------------------------------- #
# The I/O shell — read the live process table, build records, then kill REAP rows.
# psutil is the cross-platform reader; if it is absent the gather degrades to an
# empty list with a clear note rather than crashing the sweep (fail-soft).
# --------------------------------------------------------------------------- #

def _scanner_search_root(cmdline_parts: list[str], name: str) -> str:
    """For a scanner process, return its first non-flag path argument, lowercased.
    `find / -name x` -> "/"; `grep -E foo file` -> "foo" (not root-ish, so KEEP).
    Returns "" for a non-scanner or an argless invocation."""
    if name not in _SCANNER_STEMS:
        return ""
    # Skip the executable itself, then the first token that is not an option flag.
    for tok in cmdline_parts[1:]:
        if tok.startswith("-"):
            continue
        return tok.lower()
    return ""


def gather_processes() -> tuple[list[ProcRecord], str | None]:
    """Read the live process table into ProcRecords. Returns (records, error). On a
    missing psutil or any read error, returns ([], note) — never raises, so a caller
    (the sweep) degrades to a recorded skip. The reaper's own tree is left out of
    the candidate set entirely so it can never target itself."""
    try:
        import psutil  # noqa: PLC0415 — optional dep, imported lazily by design
    except ImportError:
        return [], "psutil not installed — proc-reaper skipped (pip install psutil)"

    own_pid = os.getpid()
    own_ppid = os.getppid()
    # Snapshot live PIDs first so parent-liveness is read from one consistent view.
    live_pids: set[int] = set()
    try:
        live_pids = set(psutil.pids())
    except Exception as exc:  # noqa: BLE001 — fail-soft: any reader error -> skip
        return [], f"could not read process table: {exc}"

    records: list[ProcRecord] = []
    for proc in psutil.process_iter(["pid", "name", "ppid", "cmdline",
                                     "cpu_times", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            if pid in (own_pid, own_ppid):
                continue  # never consider our own process tree
            name = (info.get("name") or "").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            parts = info.get("cmdline") or []
            cmdline = " ".join(parts)
            ppid = info.get("ppid") or 0
            cput = info.get("cpu_times")
            cpu_sec = float((cput.user + cput.system)) if cput else 0.0
            mem = info.get("memory_info")
            rss = int(mem.rss) if mem else 0
            records.append(ProcRecord(
                pid=pid,
                name=name,
                cmdline=cmdline,
                parent_alive=ppid in live_pids,
                cpu_sec=cpu_sec,
                rss_bytes=rss,
                search_root=_scanner_search_root(parts, name),
            ))
        except Exception:  # noqa: BLE001 — a vanished/forbidden process is just skipped
            continue
    return records, None


def reap(pid: int) -> tuple[bool, str]:
    """Terminate one PID. Returns (ok, note). Tries a graceful terminate first, then
    a hard kill if it lingers. Any failure (already gone, access denied) is folded to
    a note, never raised."""
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return False, "psutil not installed"
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        return True, "killed"
    except psutil.NoSuchProcess:
        return True, "already gone"
    except psutil.AccessDenied:
        return False, "access denied"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# The sweep — gather, classify, optionally reap, fold the result.
# --------------------------------------------------------------------------- #

def run_reaper(
    *,
    apply: bool,
    keep_patterns: tuple[str, ...] = _DEFAULT_KEEP_PATTERNS,
    cpu_floor_sec: float = _DEFAULT_CPU_FLOOR_SEC,
    rss_cap_gb: float = _DEFAULT_RSS_CAP_GB,
) -> dict:
    """Classify every process, kill the REAP rows iff `apply`. Returns a result dict:
    {error?, counts, reaped[], surfaced[]}. Fail-soft: a missing psutil -> an error
    note and zero counts, not a crash."""
    records, gather_err = gather_processes()
    if gather_err:
        return {"error": gather_err, "counts": {}, "reaped": [], "surfaced": []}

    rss_cap_bytes = int(rss_cap_gb * 1024**3)
    verdicts = [
        classify_process(r, keep_patterns=keep_patterns,
                         cpu_floor_sec=cpu_floor_sec, rss_cap_bytes=rss_cap_bytes)
        for r in records
    ]
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    reaped: list[dict] = []
    if apply:
        for v in verdicts:
            if v.verdict != "REAP":
                continue
            ok, note = reap(v.pid)
            reaped.append({"pid": v.pid, "reason": v.reason,
                           "summary": v.summary, "ok": ok, "note": note})
    else:
        reaped = [{"pid": v.pid, "reason": v.reason, "summary": v.summary,
                   "ok": None, "note": "dry-run"}
                  for v in verdicts if v.verdict == "REAP"]

    surfaced = [{"pid": v.pid, "reason": v.reason, "summary": v.summary}
                for v in verdicts if v.verdict == "REFUSE"]
    return {"counts": counts, "reaped": reaped, "surfaced": surfaced}


def render_report(result: dict, *, apply: bool) -> str:
    if result.get("error"):
        return f"# DOS proc-reaper — SKIPPED\n  {result['error']}"
    mode = "APPLY" if apply else "DRY-RUN (nothing killed — pass --apply to act)"
    counts = result.get("counts", {})
    lines = [f"# DOS proc-reaper — {mode}", ""]
    lines.append(f"  scanned: KEEP={counts.get('KEEP', 0)} "
                 f"REAP={counts.get('REAP', 0)} REFUSE={counts.get('REFUSE', 0)}")
    reaped = result.get("reaped", [])
    if reaped:
        verb = "reaped" if apply else "would reap"
        lines.append(f"\n  {verb}:")
        for r in reaped:
            mark = "" if r["ok"] is None else (" [ok]" if r["ok"] else f" [FAIL: {r['note']}]")
            lines.append(f"    pid {r['pid']:<7} {r['reason']:<18} {r['summary']}{mark}")
    surfaced = result.get("surfaced", [])
    if surfaced:
        lines.append("\n  surfaced (heavy but NOT auto-reaped — your call):")
        for s in surfaced:
            lines.append(f"    pid {s['pid']:<7} {s['reason']:<18} {s['summary']}")
    if not reaped and not surfaced:
        lines.append("\n  nothing to reap or surface — machine is clean.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--json", action="store_true", help="emit the result as JSON")
    p.add_argument("--apply", action="store_true",
                   help="actually kill the REAP rows; default is dry-run")
    p.add_argument("--cpu-floor-sec", type=float, default=_DEFAULT_CPU_FLOOR_SEC,
                   help="min CPU-seconds before an orphaned scanner is reaped")
    p.add_argument("--rss-cap-gb", type=float, default=_DEFAULT_RSS_CAP_GB,
                   help="resident-set ceiling (GB) above which a process is a leak outlier")
    args = p.parse_args(argv)

    result = run_reaper(apply=args.apply, cpu_floor_sec=args.cpu_floor_sec,
                        rss_cap_gb=args.rss_cap_gb)

    if args.json:
        sys.stdout.write(json.dumps(
            {"apply": args.apply, **result}, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_report(result, apply=args.apply) + "\n")

    # Exit non-zero only when an --apply kill failed — a cron then notices a real
    # problem (an access-denied reap), while a clean dry-run is always 0.
    kill_failure = any(r.get("ok") is False for r in result.get("reaped", []))
    return 1 if (args.apply and kill_failure) else 0


if __name__ == "__main__":
    sys.exit(main())
