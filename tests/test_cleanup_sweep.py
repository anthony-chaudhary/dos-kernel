"""Pin the cleanup-sweep orchestrator (`scripts/cleanup_sweep.py`).

The sweep runs every safe housekeeping chore in one pass for a cron. It invents no
cleanup logic — it FOLDS existing verbs/scripts (`dos reap`, `dos reindex`,
`git_cleanup.py`, a memory-index size check, `dos pulse`) into one fail-soft pass.
Its contract is the part worth pinning, and it is testable WITHOUT a real workspace
by stubbing the one I/O seam (`_run`, the subprocess shell):

  * fail-soft — one chore erroring (non-zero exit, missing binary) is recorded as
    `ok: false` and NEVER aborts the rest of the sweep;
  * dry-run default — no `--apply` ⇒ no chore is asked to act (`acted` is False) and
    the exit code is 0 even if a dry-run probe is noisy;
  * exit code — non-zero ONLY when a chore asked to ACT (`--apply`) failed; a clean
    dry-run, or an all-skip, is always 0;
  * the `--workspace` global flag precedes the verb in every shelled `dos` argv
    (the bug the first run hit: `dos reap --workspace .` is an argparse error);
  * the memory-index check SURFACES over-cap (it never auto-rewrites prose memory);
  * `dos pulse` is conditional — absent on a checkout ⇒ a recorded skip, not a fail.

The real subprocesses (`dos`, git) are never run here; `_run` is monkeypatched to
return canned `(returncode, stdout, stderr)` triples so the fold is asserted in
isolation, the same way `test_git_cleanup.py` drives the pure classifier.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_sweep.py"
_spec = importlib.util.spec_from_file_location("cleanup_sweep", _HELPER)
cs = importlib.util.module_from_spec(_spec)
# Register before exec so the module's dataclasses resolve their own annotations.
sys.modules["cleanup_sweep"] = cs
_spec.loader.exec_module(cs)


# --------------------------------------------------------------------------- #
# A scriptable _run stub: map an argv signature → a canned (code, out, err).
# --------------------------------------------------------------------------- #

class _Runs:
    """Records the argvs passed to _run and replays canned results by a substring
    key. Default for an unmatched argv is a clean exit-0 with empty JSON."""

    def __init__(self, table: dict[str, tuple[int, str, str]]):
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, cwd):  # signature matches cleanup_sweep._run
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, result in self.table.items():
            if key in joined:
                return result
        return (0, "{}", "")


def _install(monkeypatch, table):
    runs = _Runs(table)
    monkeypatch.setattr(cs, "_run", runs)
    return runs


# --------------------------------------------------------------------------- #
# Global-flag ordering — the regression the first live run hit.
# --------------------------------------------------------------------------- #

def test_workspace_flag_precedes_the_verb(monkeypatch):
    runs = _install(monkeypatch, {})
    cs.step_reap(Path("/ws"), apply=False)
    argv = runs.calls[-1]
    # `dos --workspace /ws reap ...` — the global flag is BEFORE the verb, never after.
    assert argv[0] == "dos"
    assert argv[1] == "--workspace"
    assert argv.index("--workspace") < argv.index("reap")


def test_reindex_does_not_pass_workspace(monkeypatch):
    # reindex reads the central store, not a per-workspace root.
    runs = _install(monkeypatch, {})
    cs.step_reindex(Path("/ws"), apply=False)
    assert "--workspace" not in runs.calls[-1]


# --------------------------------------------------------------------------- #
# reap — scratch counts + journal note
# --------------------------------------------------------------------------- #

def test_reap_folds_dropped_counts_and_journal(monkeypatch):
    payload = json.dumps({
        "audits": {"dropped": ["a", "b"]},
        "verdicts": {"dropped": ["c"]},
        "runs": {"dropped": []},
        "journal": {"compacted": True, "entries_before": 900, "entries_after": 200},
    })
    _install(monkeypatch, {"reap": (0, payload, "")})
    r = cs.step_reap(Path("/ws"), apply=True)
    assert r.ok and r.acted
    assert "reaped 3" in r.summary          # 2 audits + 1 verdict + 0 runs
    assert "900->200" in r.summary


def test_reap_failure_is_failsoft(monkeypatch):
    _install(monkeypatch, {"reap": (2, "", "argparse: bad flag")})
    r = cs.step_reap(Path("/ws"), apply=False)
    assert r.ran and not r.ok and not r.acted
    assert "exit 2" in r.summary


# --------------------------------------------------------------------------- #
# git_cleanup — counts + apply-failure surfacing
# --------------------------------------------------------------------------- #

def test_git_cleanup_dryrun_counts(monkeypatch):
    payload = json.dumps({"counts": {"PRUNE": 2, "REFUSE": 1, "KEEP": 5}})
    _install(monkeypatch, {"git_cleanup.py": (0, payload, "")})
    r = cs.step_git_cleanup(Path("/ws"), apply=False)
    assert r.ok and not r.acted
    assert "would prune 2" in r.summary and "1 refused" in r.summary


def test_git_cleanup_apply_failure_marks_not_ok(monkeypatch):
    # git_cleanup exits 1 when an --apply step failed (e.g. branch -d refused).
    payload = json.dumps({
        "counts": {"PRUNE": 1, "REFUSE": 0, "KEEP": 0},
        "applied": [{"argv": ["branch", "-d", "x"], "ok": False}],
    })
    _install(monkeypatch, {"git_cleanup.py": (1, payload, "")})
    r = cs.step_git_cleanup(Path("/ws"), apply=True)
    assert r.acted and not r.ok
    assert "1 apply step(s) failed" in r.summary


# --------------------------------------------------------------------------- #
# memory-index — surfaced, never auto-edited
# --------------------------------------------------------------------------- #

def test_memory_index_over_cap_surfaces(monkeypatch, tmp_path):
    idx = tmp_path / "MEMORY.md"
    idx.write_bytes(b"x" * (cs._MEMORY_SOFT_CAP_BYTES + 1))
    monkeypatch.setattr(cs, "_memory_index_path", lambda: idx)
    r = cs.step_memory_index()
    assert r.ran and r.ok and not r.acted     # surfaced, never acted on
    assert r.detail["over_cap"] is True
    assert "OVER" in r.summary


def test_memory_index_under_cap_is_clean(monkeypatch, tmp_path):
    idx = tmp_path / "MEMORY.md"
    idx.write_bytes(b"x" * 100)
    monkeypatch.setattr(cs, "_memory_index_path", lambda: idx)
    r = cs.step_memory_index()
    assert r.ran and r.ok and not r.detail["over_cap"]


def test_memory_index_absent_is_clean_not_error(monkeypatch):
    monkeypatch.setattr(cs, "_memory_index_path", lambda: None)
    r = cs.step_memory_index()
    assert not r.ran and r.ok                 # no memory == a valid clean state


# --------------------------------------------------------------------------- #
# pulse — conditional skip when the verb is absent
# --------------------------------------------------------------------------- #

def test_pulse_absent_is_a_skip_not_a_fail(monkeypatch):
    _install(monkeypatch, {"pulse --help": (2, "", "no such command")})
    r = cs.step_pulse(Path("/ws"))
    assert not r.ran and r.ok                 # absent verb == skip, sweep stays green
    assert "not on this checkout" in r.summary


def test_pulse_present_runs_and_reports(monkeypatch):
    _install(monkeypatch, {
        "pulse --help": (0, "usage: dos pulse", ""),
        "pulse": (0, "STALLED run RID-7 needs a human", ""),
    })
    r = cs.step_pulse(Path("/ws"))
    assert r.ran and r.ok
    assert "STALLED" in r.summary


# --------------------------------------------------------------------------- #
# proc_reaper — runaway-process reaping, conditional on psutil
# --------------------------------------------------------------------------- #

def test_proc_reaper_folds_counts(monkeypatch):
    payload = json.dumps({
        "counts": {"KEEP": 500, "REAP": 2, "REFUSE": 1},
        "reaped": [{"pid": 42, "ok": True}, {"pid": 43, "ok": True}],
        "surfaced": [{"pid": 99}],
    })
    _install(monkeypatch, {"proc_reaper.py": (0, payload, "")})
    r = cs.step_proc_reaper(Path("/ws"), apply=True)
    assert r.ran and r.ok and r.acted
    assert "reaped 2" in r.summary and "1 surfaced" in r.summary


def test_proc_reaper_psutil_absent_is_a_skip(monkeypatch):
    # The reaper emits {"error": ...} when psutil is missing → a clean recorded skip.
    payload = json.dumps({"error": "psutil not installed — proc-reaper skipped",
                          "counts": {}, "reaped": [], "surfaced": []})
    _install(monkeypatch, {"proc_reaper.py": (0, payload, "")})
    r = cs.step_proc_reaper(Path("/ws"), apply=True)
    assert not r.ran and r.ok                 # absent dep == skip, sweep stays green
    assert "psutil" in r.summary


def test_proc_reaper_kill_failure_marks_not_ok(monkeypatch):
    payload = json.dumps({
        "counts": {"REAP": 1},
        "reaped": [{"pid": 42, "ok": False, "note": "access denied"}],
        "surfaced": [],
    })
    _install(monkeypatch, {"proc_reaper.py": (1, payload, "")})
    r = cs.step_proc_reaper(Path("/ws"), apply=True)
    assert r.ran and not r.ok
    assert "1 kill(s) failed" in r.summary


# --------------------------------------------------------------------------- #
# the whole sweep — fail-soft + exit code
# --------------------------------------------------------------------------- #

def test_sweep_is_failsoft_one_bad_step_does_not_abort(monkeypatch):
    # reap blows up; every other chore still runs.
    _install(monkeypatch, {
        "reap": (2, "", "boom"),
        "pulse --help": (2, "", ""),     # pulse absent → skip
    })
    results = cs.run_sweep(Path("/ws"), apply=True)
    names = [r.name for r in results]
    assert names == ["reap", "reindex", "git_cleanup", "memory_index", "pulse",
                     "proc_reaper"]
    assert any(r.name == "reap" and not r.ok for r in results)
    assert any(r.name == "reindex" and r.ok for r in results)   # ran despite reap failing


def test_dryrun_exit_zero_even_with_noise(monkeypatch):
    _install(monkeypatch, {"reap": (2, "", "x"), "pulse --help": (2, "", "")})
    rc = cs.main(["--workspace", ".", "--json"])
    assert rc == 0                            # dry-run is always exit 0


def test_apply_failure_exits_nonzero(monkeypatch):
    _install(monkeypatch, {"reap": (2, "", "x"), "pulse --help": (2, "", "")})
    rc = cs.main(["--workspace", ".", "--apply", "--json"])
    assert rc == 1                            # a chore asked to act failed


def test_apply_all_clean_exits_zero(monkeypatch):
    # Everything returns clean JSON; pulse absent (skip). No acting failure → 0.
    _install(monkeypatch, {"pulse --help": (2, "", "")})
    rc = cs.main(["--workspace", ".", "--apply", "--json"])
    assert rc == 0
