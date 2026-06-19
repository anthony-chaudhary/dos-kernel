"""Pin the proc-reaper classifier (`scripts/proc_reaper.py`).

The tool kills runaway processes — a destructive sweep over a hot machine running a
fleet of agents. Its whole safety story rests on the PURE classifier: given a
:class:`ProcRecord` `(name, cmdline, parent_alive, cpu_sec, rss_bytes, search_root)`,
does it return KEEP / REAP / REFUSE correctly? These tests drive that classifier with
synthetic records so they assert the DOS distrust rules WITHOUT a live process table
(nothing is killed, no machine read):

  * a protected fleet/infra cmdline → KEEP, no matter its footprint (protection wins);
  * an orphaned whole-disk scanner past the CPU floor → REAP (the `find /` leak class);
  * a dead-parent scanner UNDER the CPU floor → KEEP (too young — grace);
  * a heavy scanner with a LIVE parent → REFUSE (surfaced, never auto-killed);
  * a process over the RSS cap → REAP (the memory-leak outlier, the ~62 GB python);
  * a scoped (non-root) scanner → KEEP (only whole-tree walks are reaped);
  * dry-run is the default — `run_reaper(apply=False)` plans REAP rows but kills none.

The destructive kill itself (`psutil terminate`/`kill`) is never exercised here; it
is the thin shell over the classifier these tests pin.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "proc_reaper.py"
_spec = importlib.util.spec_from_file_location("proc_reaper", _HELPER)
pr = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module's own annotations
# (frozen dataclasses look the module up by __module__ during _process_class).
sys.modules["proc_reaper"] = pr
_spec.loader.exec_module(pr)


_GB = 1024**3


def _rec(**kw) -> "pr.ProcRecord":
    """A process record with safe defaults (a normal, light, non-scanner process)."""
    base = dict(
        pid=1000,
        name="python",
        cmdline="python -m something",
        parent_alive=True,
        cpu_sec=1.0,
        rss_bytes=200 * 1024 * 1024,  # 200 MB
        search_root="",
    )
    base.update(kw)
    return pr.ProcRecord(**base)


# --------------------------------------------------------------------------- #
# Rule 1 — protection wins over every waste signal
# --------------------------------------------------------------------------- #

def test_protected_fleet_worker_is_kept_even_when_huge():
    # A supervise-loop holding 50 GB is still KEEP — stopping a live worker is the
    # operator's call, not the cron's.
    rec = _rec(cmdline="python scripts/run_supervise_loop.py --target 4",
               rss_bytes=50 * _GB)
    v = pr.classify_process(rec)
    assert v.verdict == "KEEP"
    assert v.reason == "PROTECTED"


def test_protected_matches_by_name_when_cmdline_empty():
    # Windows often hides the cmdline; a protected process must still be matched by
    # its executable stem so it is never reaped on a blank command line.
    rec = _rec(name="proc_reaper", cmdline="", rss_bytes=40 * _GB)
    assert pr.classify_process(rec).verdict == "KEEP"


def test_apply_loop_worker_protected():
    rec = _rec(cmdline="python -m job_search.cli apply-loop --profile anthony")
    assert pr.classify_process(rec).reason == "PROTECTED"


# --------------------------------------------------------------------------- #
# Waste class A — orphaned whole-disk scanners
# --------------------------------------------------------------------------- #

def test_orphaned_rootish_scanner_past_floor_is_reaped():
    # The exact incident: `find / -path ...` with a dead parent, hours of CPU.
    rec = _rec(name="find", cmdline="find / -path *dos*verdict*journal*",
               parent_alive=False, cpu_sec=7584.0, search_root="/")
    v = pr.classify_process(rec)
    assert v.verdict == "REAP"
    assert v.reason == "ORPHANED_SCANNER"


def test_orphaned_scanner_under_floor_is_kept_grace():
    # Dead parent but only a few CPU-seconds — too young to be sure it's wedged.
    rec = _rec(name="find", cmdline="find / -name foo",
               parent_alive=False, cpu_sec=5.0, search_root="/")
    v = pr.classify_process(rec)
    assert v.verdict == "KEEP"
    assert v.reason == "SCANNER_TOO_YOUNG"


def test_scanner_with_live_parent_is_surfaced_not_reaped():
    # A heavy scan whose parent is alive may be a real pipeline — REFUSE (surface).
    rec = _rec(name="grep", cmdline="grep -r foo /",
               parent_alive=True, cpu_sec=9999.0, search_root="/")
    v = pr.classify_process(rec)
    assert v.verdict == "REFUSE"
    assert v.reason == "SCANNER_LIVE_PARENT"


def test_scoped_scanner_is_never_reaped():
    # `grep -E foo file` — search root "foo" is not root-ish, so it's a normal,
    # bounded search even with a dead parent and high CPU. KEEP.
    rec = _rec(name="grep", cmdline="grep -E foo somefile",
               parent_alive=False, cpu_sec=9999.0, search_root="foo")
    v = pr.classify_process(rec)
    assert v.verdict == "KEEP"
    assert v.reason == "NORMAL"


def test_bare_drive_root_counts_as_rootish():
    rec = _rec(name="find", cmdline="find C:\\ -name x",
               parent_alive=False, cpu_sec=500.0, search_root="c:\\")
    assert pr.classify_process(rec).verdict == "REAP"


# --------------------------------------------------------------------------- #
# Waste class B — memory-leak outliers
# --------------------------------------------------------------------------- #

def test_memory_outlier_over_cap_is_reaped():
    # The ~62 GB python: not protected, RSS over the 32 GB default cap → REAP.
    rec = _rec(name="python", cmdline="python data/_scratch/leaky.py",
               rss_bytes=62 * _GB)
    v = pr.classify_process(rec)
    assert v.verdict == "REAP"
    assert v.reason == "MEMORY_OUTLIER"


def test_process_under_rss_cap_is_normal():
    rec = _rec(name="python", rss_bytes=4 * _GB)
    v = pr.classify_process(rec)
    assert v.verdict == "KEEP"
    assert v.reason == "NORMAL"


def test_rss_cap_is_configurable():
    # A 10 GB process is KEEP under the default 32 GB cap but REAP under a 8 GB cap.
    rec = _rec(name="python", rss_bytes=10 * _GB)
    assert pr.classify_process(rec).verdict == "KEEP"
    assert pr.classify_process(rec, rss_cap_bytes=8 * _GB).verdict == "REAP"


def test_cpu_floor_is_configurable():
    rec = _rec(name="find", parent_alive=False, cpu_sec=60.0, search_root="/")
    # Default floor 120s → too young; a 30s floor → reap.
    assert pr.classify_process(rec).verdict == "KEEP"
    assert pr.classify_process(rec, cpu_floor_sec=30.0).verdict == "REAP"


# --------------------------------------------------------------------------- #
# The search-root extractor (pure helper feeding the classifier)
# --------------------------------------------------------------------------- #

def test_search_root_skips_flags():
    # `find / -name x` → first non-flag arg is "/".
    assert pr._scanner_search_root(["find", "/", "-name", "x"], "find") == "/"
    # `grep -E foo file` → first non-flag arg is "foo".
    assert pr._scanner_search_root(["grep", "-E", "foo", "file"], "grep") == "foo"
    # A non-scanner returns "".
    assert pr._scanner_search_root(["python", "/"], "python") == ""
    # An argless scanner returns "".
    assert pr._scanner_search_root(["find"], "find") == ""


# --------------------------------------------------------------------------- #
# Dry-run is the default (the sweep plans but never kills without --apply)
# --------------------------------------------------------------------------- #

def test_dry_run_plans_reap_without_killing(monkeypatch):
    # Feed a synthetic table with one orphaned scanner + one normal process; assert
    # the dry-run plan lists the scanner as a would-reap and calls reap() zero times.
    scanner = _rec(pid=42, name="find", cmdline="find / -name x",
                   parent_alive=False, cpu_sec=9000.0, search_root="/")
    normal = _rec(pid=43, name="python", rss_bytes=1 * _GB)
    monkeypatch.setattr(pr, "gather_processes", lambda: ([scanner, normal], None))
    killed: list[int] = []
    monkeypatch.setattr(pr, "reap", lambda pid: (killed.append(pid), (True, "killed"))[1])

    result = pr.run_reaper(apply=False)
    assert killed == []  # nothing killed in dry-run
    assert result["counts"]["REAP"] == 1
    assert result["counts"]["KEEP"] == 1
    assert result["reaped"][0]["pid"] == 42
    assert result["reaped"][0]["note"] == "dry-run"


def test_apply_kills_only_reap_rows(monkeypatch):
    scanner = _rec(pid=42, name="find", cmdline="find / -name x",
                   parent_alive=False, cpu_sec=9000.0, search_root="/")
    protected = _rec(pid=44, cmdline="python scripts/run_supervise_loop.py",
                     rss_bytes=50 * _GB)
    monkeypatch.setattr(pr, "gather_processes", lambda: ([scanner, protected], None))
    killed: list[int] = []
    monkeypatch.setattr(pr, "reap", lambda pid: (killed.append(pid), (True, "killed"))[1])

    result = pr.run_reaper(apply=True)
    assert killed == [42]  # only the orphaned scanner, never the protected worker
    assert result["reaped"][0]["ok"] is True


def test_missing_psutil_is_failsoft(monkeypatch):
    # gather returns an error note → run_reaper degrades to a skip, never crashes.
    monkeypatch.setattr(pr, "gather_processes",
                        lambda: ([], "psutil not installed — proc-reaper skipped"))
    result = pr.run_reaper(apply=True)
    assert "error" in result
    assert result["counts"] == {}
    assert result["reaped"] == []
