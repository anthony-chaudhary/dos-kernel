"""`dos.session_digest` + `dos hook session-start` — the session-start orientation hook.

Two halves, mirroring `test_session_digest`'s plan:

  * the PURE builder (`session_digest.build_digest`) — folds folded leases + a
    HelpSummary + a BreakerVerdict into one line, and obeys the SILENCE RULE
    (nothing worth saying → None);
  * the VERB (`cli.cmd_hook_session_start`, driven via `cli.main`) — stdin event
    in, the Claude-Code SessionStart additionalContext shape out when state
    exists, NOTHING out when it doesn't, and every failure mode degrading to
    "emit nothing" = a normal session start. The compact path re-injects the
    digest persisted before the compaction.

The verb tests run against a throwaway git repo with a pinned lane journal (the
test_lane_lease idiom), so a journaled ACQUIRE gives `live_leases` a real lease to
fold without touching the package tree.
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from dos import cli, session_digest as sd
from dos import config as _config
from dos import hook_dialect as hd
from dos import lane_lease


# ---------------------------------------------------------------------------
# The PURE builder — folds + the silence rule.
# ---------------------------------------------------------------------------
class _Summary:
    """A stand-in for help_summary.HelpSummary with the fields build_digest reads."""

    def __init__(self, total=0, withheld=0, advisory=0):
        self.total = total
        self.withheld = withheld
        self.advisory = advisory


class _Breaker:
    def __init__(self, is_open, escalation=""):
        self.is_open = is_open

        class _E:
            value = escalation

        self.escalation = _E()


def test_no_state_is_silent():
    """Nothing held, nothing caught, no breaker → None (the silence rule)."""
    assert sd.build_digest() is None
    assert sd.build_digest(leases=[], summary=_Summary(0), breaker_verdict=None) is None


def test_held_leases_name_the_lanes():
    out = sd.build_digest(leases=[{"lane": "src"}, {"lane": "docs"}, {"lane": "src"}])
    assert out is not None
    assert "2 lane leases held (src, docs)" in out
    # de-duplicated: src counted once.
    assert out.count("src") == 1


def test_single_lease_is_singular():
    out = sd.build_digest(leases=[{"lane": "src"}])
    assert "1 lane lease held (src)" in out


def test_kernel_repo_caveat_rides_the_lease_leg():
    out = sd.build_digest(leases=[{"lane": "src"}], is_kernel_repo=True)
    assert "this IS the kernel repo" in out
    # but the caveat alone (no lease) never breaks the silence rule:
    assert sd.build_digest(leases=[], is_kernel_repo=True) is None


def test_refused_calls_are_reported():
    out = sd.build_digest(summary=_Summary(total=3, withheld=3, advisory=2))
    assert "DOS refused 3 calls (+2 advisory) earlier this session" in out


def test_advisory_only_is_reported():
    out = sd.build_digest(summary=_Summary(total=2, withheld=0, advisory=2))
    assert "DOS surfaced 2 advisory cautions earlier this session" in out


def test_open_breaker_speaks_closed_is_silent():
    assert "breaker OPEN" in sd.build_digest(
        leases=[{"lane": "src"}], breaker_verdict=_Breaker(True, "HUMAN"))
    # a CLOSED breaker adds no line; on its own it stays silent.
    assert sd.build_digest(breaker_verdict=_Breaker(False)) is None


def test_restored_note_on_compaction():
    out = sd.build_digest(leases=[{"lane": "src"}], restored=True)
    assert "(restored after compaction)" in out


# ---------------------------------------------------------------------------
# The VERB — driven in-process via cli.main (the test_hook_stop idiom).
# ---------------------------------------------------------------------------
def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway git repo with the lane journal pinned under tmp (no package writes)."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / ".lane.lock"))
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(tmp_path / "lane-journal.jsonl"))
    return tmp_path


def _run(monkeypatch, capsys, event, *extra_args, json_mode=True):
    """Drive `dos hook session-start` with `event` on stdin; return (rc, stdout)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    argv = ["hook", "session-start", *extra_args]
    if json_mode:
        argv.append("--json")
    rc = cli.main(argv)
    return rc, capsys.readouterr().out.strip()


def _hold_a_lease(repo):
    """Journal a live ACQUIRE so the verb's live_leases fold sees a held lane."""
    cfg = _config.default_config(str(repo))
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="holder", loop_ts="2026-06-02T12:00Z")
    assert res.decision.outcome == "acquire"


def test_empty_workspace_emits_nothing(repo, monkeypatch, capsys):
    """No leases, nothing caught → emit nothing, exit 0 (the silence rule)."""
    rc, out = _run(monkeypatch, capsys, {"session_id": "s1", "cwd": str(repo)},
                   "--workspace", str(repo))
    assert rc == 0
    assert out == ""


def test_no_stdin_is_safe(repo, monkeypatch, capsys):
    """Empty stdin degrades to a normal session start (emit nothing, exit 0)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = cli.main(["hook", "session-start", "--workspace", str(repo), "--json"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_bad_json_is_safe(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    rc = cli.main(["hook", "session-start", "--workspace", str(repo), "--json"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_held_lease_produces_a_digest_json(repo, monkeypatch, capsys):
    _hold_a_lease(repo)
    rc, out = _run(monkeypatch, capsys, {"session_id": "s2", "cwd": str(repo)},
                   "--workspace", str(repo))
    assert rc == 0
    obj = json.loads(out)
    assert "lane lease" in obj["digest"]
    assert obj["restored"] is False


def test_held_lease_renders_the_cc_sessionstart_shape(repo, monkeypatch, capsys):
    """The DEFAULT surface (no --json) is the exact CC SessionStart additionalContext."""
    _hold_a_lease(repo)
    rc, out = _run(monkeypatch, capsys, {"session_id": "s3", "cwd": str(repo)},
                   "--workspace", str(repo), json_mode=False)
    assert rc == 0
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "lane lease" in obj["hookSpecificOutput"]["additionalContext"]


def test_compact_reinjects_the_persisted_digest(repo, monkeypatch, capsys):
    """A first session-start persists the digest; a compact-sourced one restores it."""
    _hold_a_lease(repo)
    # First call (a normal start) persists the digest under the session stamp.
    rc1, out1 = _run(monkeypatch, capsys, {"session_id": "s4", "cwd": str(repo)},
                     "--workspace", str(repo))
    assert rc1 == 0 and json.loads(out1)["restored"] is False
    # Now a compaction fires a SessionStart with source=compact, same session.
    rc2, out2 = _run(monkeypatch, capsys,
                     {"session_id": "s4", "cwd": str(repo), "source": "compact"},
                     "--workspace", str(repo))
    assert rc2 == 0
    obj = json.loads(out2)
    assert obj["restored"] is True
    assert "(restored after compaction)" in obj["digest"]


def test_render_failsoft_on_unknown_dialect(repo, monkeypatch, capsys):
    """An unknown --dialect emits nothing (the digest is advisory), never crashes."""
    _hold_a_lease(repo)
    rc, out = _run(monkeypatch, capsys, {"session_id": "s5", "cwd": str(repo)},
                   "--workspace", str(repo), "--dialect", "no-such-host", json_mode=False)
    assert rc == 0
    assert out == ""
