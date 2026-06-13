"""Tests for `dos top` — the live fleet-watchdog screen (`dos.dispatch_top` + `_tui`).

`dos top` is a read-only projection over the kernel's OWN lease world
(`lane_journal.replay`) + the verdict envelopes + a git-activity strip — never a
host's `fanout_state`/`execution-state.yaml`. These tests pin the contract that
matters most for this port: **it works in a random new repo** (no `dos.toml`, no
journal, no plan), plus the pure adapters/renderers and the rich-absent floor.

Pure throughout: the clock is injected (`now=`), the oracle `verify` is faked, and
the one git read is exercised against a real tmp git repo (the honest way to prove
the fresh-repo path, since that read IS the fresh-repo content).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from dos import dispatch_top as T
from dos import dispatch_top_tui as TUI
from dos import git_delta
from dos import lane_journal
from dos import liveness as LV
from dos.config import default_config


NOW = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def _iso(minutes_ago: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat()


def _lease(lane: str, *, acquired_min: float, hb_min: float, loop="L", holder="h:1") -> dict:
    return {
        "lane": lane, "loop_ts": loop, "holder": holder,
        "acquired_at": _iso(acquired_min), "heartbeat_at": _iso(hb_min),
    }


def _write_journal(cfg, leases: list[dict]) -> None:
    """Write ACQUIRE entries for each lease so replay() reconstructs them live."""
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("w", encoding="utf-8") as fh:
        for lease in leases:
            fh.write(json.dumps(lane_journal.acquire_entry(lease)) + "\n")


def _git_repo(path: Path, *, commits=("initial commit",), dirs=()) -> Path:
    """A real, minimal git repo at `path` with `commits` and optional `dirs`."""
    path.mkdir(parents=True, exist_ok=True)

    def _git(*args):
        subprocess.run(["git", *args], cwd=str(path), check=True,
                       capture_output=True, text=True)

    _git("init")
    _git("config", "user.email", "t@t.t")
    _git("config", "user.name", "t")
    for d in dirs:
        (path / d).mkdir(parents=True, exist_ok=True)
        (path / d / "f.py").write_text("x = 1\n", encoding="utf-8")
    for i, msg in enumerate(commits):
        (path / f"file{i}.txt").write_text(f"content {i}\n", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-m", msg)
    return path


# ---------------------------------------------------------------------------
# THE HEADLINE: dos top works in a random new repo (no dos.toml, no journal).
# ---------------------------------------------------------------------------


class TestFreshRepo:
    def test_snapshot_in_bare_git_repo_no_config_no_journal(self, tmp_path: Path):
        """A plain git repo with one commit and NOTHING DOS-specific renders a frame.

        This is the whole point of the port: `dos top` must be useful the moment an
        operator `cd`s into any checkout. No `dos.toml`, no `.dos/`, no plan — just
        git. The frame must come back renderable, with the lanes from the generic
        default (all FREE, zero leases) and the commit in the activity strip.
        """
        repo = _git_repo(tmp_path, commits=("first commit",))
        cfg = default_config(repo)  # generic main/global; nothing seeded

        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)

        # Lanes: the generic roster, every one FREE (no journal => no leases).
        assert [s.lane for s in frame.lanes] == ["main", "global"]
        assert all(s.chip == T.CHIP_FREE for s in frame.lanes)
        # No verdicts (no envelope dir), but real git activity (the fresh content).
        assert frame.verdicts == ()
        assert any(c["subject"] == "first commit" for c in frame.activity)
        # The whole screen renders without raising and names the workspace.
        text = T.render_frame_text(frame)
        assert "dos top" in text
        assert "RECENT COMMITS" in text
        assert "first commit" in text

    def test_snapshot_in_empty_repo_no_commits_is_still_a_frame(self, tmp_path: Path):
        """Even a repo with ZERO commits (unborn HEAD) degrades to a clean frame."""
        repo = tmp_path / "empty"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True,
                       capture_output=True, text=True)
        cfg = default_config(repo)

        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)

        assert frame.activity == ()           # no commits -> empty strip, no crash
        assert [s.lane for s in frame.lanes] == ["main", "global"]
        assert "no commits" in T.render_activity_text(frame.activity)

    def test_snapshot_does_not_create_state(self, tmp_path: Path):
        """`snapshot()` is read-only — it writes no `.dos/`, no journal, no config."""
        repo = _git_repo(tmp_path)
        cfg = default_config(repo)
        before = {p.name for p in repo.iterdir()}

        T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)

        after = {p.name for p in repo.iterdir()}
        assert before == after  # snapshot mutates nothing (auto-init is the CLI's job)


# ---------------------------------------------------------------------------
# Lane roster + liveness chips — the kernel verdict drives each chip.
# ---------------------------------------------------------------------------


class TestLaneStates:
    def test_roster_is_concurrent_then_exclusive_deduped(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        assert T.lane_roster(cfg) == ["main", "global"]

    def test_free_lane_when_no_lease(self, tmp_path: Path):
        states = T.build_lane_states(
            {"leases": [], "events_by_lane": {}},
            roster=["main", "global"], exclusive=("global",), now=NOW,
        )
        assert all(s.chip == T.CHIP_FREE for s in states)
        assert states[1].is_exclusive is True  # global flagged exclusive

    def test_held_lease_chips_match_liveness_ladder(self, tmp_path: Path):
        """A young lease => ADVANCING; a stale-heartbeat lease => STALLED; an old
        alive idle lease => SPINNING. The chip IS `liveness.classify`."""
        payload = {
            "leases": [
                _lease("main", acquired_min=5, hb_min=2),      # young+fresh
                _lease("global", acquired_min=50, hb_min=40),  # stale heartbeat
            ],
            "events_by_lane": {},
        }
        states = T.build_lane_states(
            payload, roster=["main", "global"], exclusive=("global",), now=NOW)
        by_lane = {s.lane: s for s in states}
        assert by_lane["main"].chip == T.CHIP_ADVANCING     # grace guard: too young to judge
        assert by_lane["global"].chip == T.CHIP_STALLED     # hb older than spin window

    def test_old_alive_idle_lease_is_spinning(self, tmp_path: Path):
        payload = {
            "leases": [_lease("main", acquired_min=60, hb_min=1)],  # old run, fresh hb, idle
            "events_by_lane": {},
        }
        states = T.build_lane_states(
            payload, roster=["main", "global"], exclusive=("global",), now=NOW)
        assert {s.lane: s.chip for s in states}["main"] == T.CHIP_SPINNING

    def test_held_unknown_lane_is_never_invisible(self):
        """A lease on a lane NOT in the roster is surfaced last, never dropped."""
        payload = {
            "leases": [_lease("hotfix", acquired_min=3, hb_min=1)],
            "events_by_lane": {},
        }
        states = T.build_lane_states(
            payload, roster=["main", "global"], exclusive=("global",), now=NOW)
        lanes = [s.lane for s in states]
        assert lanes == ["main", "global", "hotfix"]  # appended after the roster
        assert states[-1].chip != T.CHIP_FREE          # it's held, so not FREE

    def test_forward_event_after_acquire_counts_as_advancing(self):
        """A state-mutating event AFTER acquire is forward motion => ADVANCING even
        with a stale heartbeat (the liveness commit/event rung beats heartbeat)."""
        payload = {
            "leases": [_lease("main", acquired_min=50, hb_min=40)],  # would be STALLED alone
            "events_by_lane": {"main": 1},                            # but a later event landed
        }
        states = T.build_lane_states(
            payload, roster=["main"], exclusive=(), now=NOW)
        assert states[0].chip == T.CHIP_ADVANCING

    def test_chip_map_covers_every_liveness_value(self):
        """Guard: every Liveness enum value has a chip (a new state fails loudly)."""
        for v in LV.Liveness:
            assert v in T._CHIP_BY_LIVENESS


# ---------------------------------------------------------------------------
# events_by_lane fold — the bug the first smoke-test caught.
# ---------------------------------------------------------------------------


class TestEventsFold:
    def test_acquire_itself_is_not_counted_as_progress(self, tmp_path: Path):
        """The ACQUIRE that created the live lease must NOT count as an event-since
        (else an idle just-acquired lease reads ADVANCING forever)."""
        cfg = default_config(tmp_path)
        _write_journal(cfg, [_lease("main", acquired_min=50, hb_min=40)])
        entries = lane_journal.read_all(cfg.paths.lane_journal)
        live = {l["lane"]: l for l in lane_journal.replay(entries)}
        counts = T._events_by_lane(entries, live)
        assert counts.get("main", 0) == 0  # only its own ACQUIRE => zero progress

    def test_event_after_acquire_is_counted(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        lj = cfg.paths.lane_journal
        lj.parent.mkdir(parents=True, exist_ok=True)
        with lj.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(lane_journal.acquire_entry(
                _lease("main", acquired_min=50, hb_min=40))) + "\n")
            # A RECONCILE recorded later (after acquire) = lease-layer progress.
            fh.write(json.dumps({
                "op": lane_journal.OP_RECONCILE, "lane": "main", "loop_ts": "L",
                "ts": _iso(5), "lease": _lease("main", acquired_min=50, hb_min=40),
            }) + "\n")
        entries = lane_journal.read_all(lj)
        live = {l["lane"]: l for l in lane_journal.replay(entries)}
        assert T._events_by_lane(entries, live).get("main", 0) == 1


# ---------------------------------------------------------------------------
# SPAWNING chip — the SPAWN→ACQUIRE visibility gap (the dos-top audit fix).
# A journaled OP_SPAWN makes a loop visible the instant it commits to a lane,
# before the durable ACQUIRE — a held lease then WINS the chip, and a launch that
# dies in preflight ages out of the SPAWNING set on a short TTL.
# ---------------------------------------------------------------------------


def _spawn(lane: str, *, age_min: float, holder="h:1", loop="L") -> dict:
    """An OP_SPAWN journal entry `age_min` minutes old (its `ts` drives the TTL)."""
    return {"op": lane_journal.OP_SPAWN, "lane": lane, "loop_ts": loop,
            "holder": holder, "ts": _iso(age_min)}


class TestSpawningChip:
    def test_fresh_spawn_no_lease_reads_spawning(self):
        """A recent OP_SPAWN with no live lease => the lane reads SPAWNING (the loop
        is visible the instant it commits, not only once it has acquired)."""
        entries = [_spawn("main", age_min=0.1)]
        live = {l["lane"]: l for l in lane_journal.replay(entries)}
        assert live == {}  # the SPAWN granted no lease
        spawning = T._spawning_lanes(entries, live, now=NOW)
        assert "main" in spawning
        states = T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": spawning},
            roster=["main", "global"], now=NOW)
        by_lane = {s.lane: s for s in states}
        assert by_lane["main"].chip == T.CHIP_SPAWNING
        assert by_lane["main"].holder == "h:1"
        assert by_lane["global"].chip == T.CHIP_FREE

    def test_held_lease_wins_over_spawn(self):
        """Once the ACQUIRE lands, the held lease takes the liveness chip — a lane is
        never both SPAWNING and held (the no-live-lease gate)."""
        entries = [_spawn("main", age_min=0.2),
                   lane_journal.acquire_entry(_lease("main", acquired_min=0.1, hb_min=0.05))]
        live = {l["lane"]: l for l in lane_journal.replay(entries)}
        assert "main" in live  # the ACQUIRE granted the lease
        spawning = T._spawning_lanes(entries, live, now=NOW)
        assert "main" not in spawning  # held => not SPAWNING
        states = T.build_lane_states(
            {"leases": list(live.values()), "events_by_lane": {},
             "spawning_by_lane": spawning},
            roster=["main"], now=NOW)
        assert states[0].chip != T.CHIP_SPAWNING

    def test_stale_spawn_ages_out_to_free(self):
        """A launch that DIES in preflight (a SPAWN older than the TTL, never
        acquired) ages out — no phantom SPAWNING wedged forever."""
        stale_min = (T.SPAWN_TTL_MS / 60_000) + 1  # one minute past the TTL
        entries = [_spawn("main", age_min=stale_min)]
        spawning = T._spawning_lanes(entries, {}, now=NOW)
        assert "main" not in spawning
        states = T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": spawning},
            roster=["main"], now=NOW)
        assert states[0].chip == T.CHIP_FREE

    def test_spawn_just_inside_ttl_still_spawning(self):
        """The boundary: a SPAWN just YOUNGER than the TTL is still SPAWNING (the
        cutoff is `> ttl`, so the TTL instant itself is inclusive)."""
        fresh_min = (T.SPAWN_TTL_MS / 60_000) - 0.1
        spawning = T._spawning_lanes([_spawn("main", age_min=fresh_min)], {}, now=NOW)
        assert "main" in spawning

    def test_spawning_lane_outside_roster_is_never_invisible(self):
        """A SPAWN on a lane the taxonomy doesn't name is surfaced last, never
        dropped — the 'never invisible' rule, applied to a COMING run (not just a
        HELD one)."""
        spawning = T._spawning_lanes([_spawn("hotfix", age_min=0.1)], {}, now=NOW)
        states = T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": spawning},
            roster=["main", "global"], now=NOW)
        lanes = [s.lane for s in states]
        assert lanes == ["main", "global", "hotfix"]
        assert states[-1].chip == T.CHIP_SPAWNING

    def test_re_spawn_refreshes_to_the_newer_intent(self):
        """A second SPAWN on the same lane (a re-launch) wins — the most-recent
        intent's holder/age is what renders (append order => last wins)."""
        entries = [_spawn("main", age_min=5, holder="old:1"),
                   _spawn("main", age_min=0.1, holder="new:2")]
        spawning = T._spawning_lanes(entries, {}, now=NOW)
        assert spawning["main"].holder == "new:2"

    def test_snapshot_surfaces_spawning_from_the_wal(self, tmp_path: Path):
        """End-to-end: a SPAWN written to the WAL makes snapshot() render the lane
        SPAWNING — the projection reads the durable record, no in-memory state."""
        cfg = default_config(tmp_path)
        lj = cfg.paths.lane_journal
        lj.parent.mkdir(parents=True, exist_ok=True)
        lj.write_text(json.dumps(_spawn("main", age_min=0.1)) + "\n", encoding="utf-8")
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        by_lane = {s.lane: s for s in frame.lanes}
        assert by_lane["main"].chip == T.CHIP_SPAWNING

    def test_render_tally_shows_spawning_only_when_present(self):
        """The summary line gains a 'N spawning' segment ONLY when a lane is
        SPAWNING — the steady-state (no-spawn) line is byte-unchanged."""
        spawning = T._spawning_lanes([_spawn("main", age_min=0.1)], {}, now=NOW)
        with_spawn = T.render_lanes_text(tuple(T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": spawning},
            roster=["main"], now=NOW)))
        assert "1 spawning" in with_spawn
        no_spawn = T.render_lanes_text(tuple(T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": {}},
            roster=["main"], now=NOW)))
        assert "spawning" not in no_spawn

    def test_spawning_row_labels_age_as_spawn_not_hb(self):
        """A SPAWNING lane has no lease to beat, so its age reads `spawn <age>`, not
        the misleading `hb <age>` a held lane shows."""
        spawning = T._spawning_lanes([_spawn("main", age_min=0.1)], {}, now=NOW)
        text = T.render_lanes_text(tuple(T.build_lane_states(
            {"leases": [], "events_by_lane": {}, "spawning_by_lane": spawning},
            roster=["main"], now=NOW)))
        assert "spawn " in text and "hb " not in text


# ---------------------------------------------------------------------------
# Verdict rows + trust cross-check.
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_parse_accept_envelope(self):
        env = {"all_clear": True, "scope": "main",
               "picks": [{"plan_id": "P1", "phase_id": "A"}],
               "generated_at": _iso(3)}
        row = T.parse_verdict_envelope(env, "next-up-2026-06-01-1", now=NOW)
        assert row.verdict == "ACCEPT"
        assert row.lane == "main"
        assert row.pick == "P1 A"

    def test_parse_wedge_envelope(self):
        env = {"verdict": "WEDGE", "reason_class": "lane_all_inflight",
               "blocked": True, "scope": {"lane": "global"}}
        row = T.parse_verdict_envelope(env, "t", now=NOW)
        assert row.verdict == "WEDGE"
        assert row.reason_token == "LANE_ALL_INFLIGHT"
        assert row.lane == "global"

    def test_trust_pending_for_unshipped_accept_not_false_ship(self):
        """An ACCEPT the oracle hasn't seen ship reads ·pending, NOT a false-ship
        warn (the correction job's DTOP2 made: accept is a go-ahead, not a claim)."""
        row = T.VerdictRow(tag="t", lane="main", verdict="ACCEPT", pick="P1 A")
        out = T.attach_trust(row, verify=lambda p, ph: False)
        assert out.trust == T.TRUST_PENDING

    def test_trust_ok_when_oracle_confirms(self):
        row = T.VerdictRow(tag="t", lane="main", verdict="ACCEPT", pick="P1 A")
        out = T.attach_trust(row, verify=lambda p, ph: True)
        assert out.trust == T.TRUST_OK

    def test_trust_na_for_no_pick(self):
        row = T.VerdictRow(tag="t", lane="main", verdict="WEDGE", pick="")
        out = T.attach_trust(row, verify=lambda p, ph: True)
        assert out.trust == T.TRUST_NA

    def test_trust_failsafe_on_verify_raise(self):
        row = T.VerdictRow(tag="t", lane="main", verdict="ACCEPT", pick="P1 A")

        def _boom(p, ph):
            raise RuntimeError("oracle down")

        out = T.attach_trust(row, verify=_boom)
        assert out.trust == T.TRUST_NA  # degrades, never crashes the screen

    def test_snapshot_reads_verdict_envelopes(self, tmp_path: Path):
        repo = _git_repo(tmp_path)
        cfg = default_config(repo)
        nd = cfg.paths.next_packets
        nd.mkdir(parents=True, exist_ok=True)
        (nd / ".verdict-next-up-2026-06-01-1.json").write_text(json.dumps({
            "verdict": "WEDGE", "reason_class": "lane_drain", "blocked": True,
            "scope": "main", "generated_at": _iso(10),
        }), encoding="utf-8")
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert len(frame.verdicts) == 1
        assert frame.verdicts[0].verdict == "WEDGE"


# ---------------------------------------------------------------------------
# git_delta.recent_commits — the new unanchored reader.
# ---------------------------------------------------------------------------


class TestRecentCommits:
    def test_recent_commits_newest_first(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("one", "two", "three"))
        out = git_delta.recent_commits(10, root=repo)
        assert [c["subject"] for c in out] == ["three", "two", "one"]

    def test_recent_commits_respects_n(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("a", "b", "c", "d"))
        assert len(git_delta.recent_commits(2, root=repo)) == 2

    def test_recent_commits_non_git_is_empty(self, tmp_path: Path):
        assert git_delta.recent_commits(5, root=tmp_path) == []

    def test_recent_commits_nonpositive_is_empty(self, tmp_path: Path):
        repo = _git_repo(tmp_path)
        assert git_delta.recent_commits(0, root=repo) == []


# ---------------------------------------------------------------------------
# Renderers — pure, deterministic plain text.
# ---------------------------------------------------------------------------


class TestRenderers:
    def test_lanes_text_tally(self):
        states = T.build_lane_states(
            {"leases": [_lease("main", acquired_min=5, hb_min=2)], "events_by_lane": {}},
            roster=["main", "global"], exclusive=("global",), now=NOW)
        text = T.render_lanes_text(tuple(states))
        assert "LANES" in text
        assert "1 advancing" in text and "1 free" in text

    def test_empty_lanes_text(self):
        assert "(no lanes" in T.render_lanes_text(())

    def test_frame_text_has_all_sections(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("hello",))
        cfg = default_config(repo)
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        text = T.render_frame_text(frame)
        for section in ("LANES", "RECENT VERDICTS", "RECENT COMMITS", "read-only"):
            assert section in text

    def test_long_workspace_path_not_truncated(self):
        frame = T.Frame(workspace="C:\\" + "x" * 120, now_iso="2026-06-01T12:00:00+00:00")
        head = T.render_frame_text(frame).splitlines()[0]
        assert "x" * 120 in head  # full path survives, never chopped

    def test_to_dict_is_json_serializable(self, tmp_path: Path):
        repo = _git_repo(tmp_path)
        cfg = default_config(repo)
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        json.dumps(frame.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# The TUI floor — rich-absent / non-tty degrades to one plain frame, exit 0.
# ---------------------------------------------------------------------------


class TestTuiFloor:
    def test_once_prints_plain_frame_returns_zero(self, tmp_path, capsys):
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        rc = TUI.run_top(cfg, once=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "dos top" in out and "RECENT COMMITS" in out

    def test_non_tty_is_the_floor(self, tmp_path, capsys, monkeypatch):
        """A non-interactive stdout (pytest's capture) never enters the live loop."""
        repo = _git_repo(tmp_path)
        cfg = default_config(repo)
        # capsys already makes stdout non-tty; assert we still get a single frame.
        rc = TUI.run_top(cfg, once=False)
        assert rc == 0
        assert "LANES" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The spend column (issue #38) — the per-lane latest efficiency chip, read from
# the verdict journal. Orthogonal to liveness; blank when nothing recorded.
# ---------------------------------------------------------------------------
from dos import verdict_journal as VJ


class _Ev:
    """A minimal duck-typed verdict event for the pure-fold tests."""
    def __init__(self, syscall, verdict, lane, work=None, tokens=None):
        self.syscall, self.verdict, self.lane = syscall, verdict, lane
        self.detail = {}
        if work is not None:
            self.detail["evidence.work"] = work
        if tokens is not None:
            self.detail["evidence.tokens"] = tokens


class TestSpendFold:
    def test_latest_efficiency_per_lane_last_wins(self):
        """The fold keeps the NEWEST efficiency verdict per lane (events are oldest
        first, so a later record overwrites an earlier)."""
        events = [
            _Ev("efficiency", "EFFICIENT", "src", work=10, tokens=100),
            _Ev("efficiency", "WASTEFUL", "src", work=0, tokens=900),   # newer wins
            _Ev("efficiency", "COSTLY", "docs", work=2, tokens=500),
        ]
        out = T.latest_efficiency_by_lane(events)
        assert out["src"].verdict == "WASTEFUL"
        assert out["src"].work == 0 and out["src"].tokens == 900
        assert out["docs"].verdict == "COSTLY"

    def test_fold_ignores_non_efficiency_and_laneless_events(self):
        events = [
            _Ev("liveness", "SPINNING", "src"),       # wrong syscall
            _Ev("efficiency", "EFFICIENT", ""),        # no lane → no column
            _Ev("efficiency", "EFFICIENT", "tests", work=5, tokens=50),
        ]
        out = T.latest_efficiency_by_lane(events)
        assert set(out) == {"tests"}

    def test_fold_tolerates_a_fossil_with_no_counts(self):
        out = T.latest_efficiency_by_lane([_Ev("efficiency", "EFFICIENT", "src")])
        assert out["src"].verdict == "EFFICIENT"
        assert out["src"].work is None and out["src"].tokens is None


class TestSpendColumnRender:
    def test_held_lane_carries_the_spend_chip(self):
        payload = {
            "leases": [_lease("src", acquired_min=60, hb_min=1)],
            "spend_by_lane": {
                "src": T.LaneSpend(verdict="WASTEFUL", work=0, tokens=900),
            },
        }
        states = T.build_lane_states(
            payload, roster=["src", "global"], exclusive=("global",), now=NOW)
        src = next(s for s in states if s.lane == "src")
        assert src.spend_chip == T._SPEND_CHIP["WASTEFUL"]
        assert src.work == 0 and src.tokens == 900
        text = T.render_lanes_text(tuple(states))
        assert T._SPEND_CHIP["WASTEFUL"] in text
        assert "(0w/900t)" in text

    def test_unrecognized_verdict_token_renders_blank_not_crash(self):
        payload = {
            "leases": [_lease("src", acquired_min=60, hb_min=1)],
            "spend_by_lane": {"src": T.LaneSpend(verdict="FUTURE_TOKEN")},
        }
        states = T.build_lane_states(
            payload, roster=["src"], exclusive=(), now=NOW)
        assert next(s for s in states).spend_chip == ""  # fail-soft

    def test_no_spend_journal_is_byte_identical(self):
        """A lane with no recorded efficiency verdict renders exactly as before #38
        (the spend chip is absent; the row is unchanged)."""
        payload = {"leases": [_lease("src", acquired_min=5, hb_min=1)]}
        states = T.build_lane_states(
            payload, roster=["src", "global"], exclusive=("global",), now=NOW)
        src = next(s for s in states if s.lane == "src")
        assert src.spend_chip == "" and src.work is None and src.tokens is None
        # The rendered row carries no spend glyph.
        text = T.render_lanes_text(tuple(states))
        for chip in T._SPEND_CHIP.values():
            assert chip not in text


class TestSpendColumnSnapshot:
    def test_snapshot_reads_efficiency_from_the_verdict_journal(self, tmp_path: Path):
        """End-to-end (the done-condition): an efficiency verdict recorded for a held
        lane's run surfaces as that lane's spend chip in a real snapshot frame."""
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        # Hold the `main` lane via the lane journal.
        _write_journal(cfg, [_lease("main", acquired_min=60, hb_min=1)])
        # Record an efficiency verdict for that lane into the verdict journal.
        VJ.record(
            VJ.VerdictEvent(
                syscall="efficiency", verdict="WASTEFUL", lane="main",
                detail={"evidence.work": 0, "evidence.tokens": 1200}),
            path=cfg.paths.verdict_journal)

        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        main = next(s for s in frame.lanes if s.lane == "main")
        assert main.spend_chip == T._SPEND_CHIP["WASTEFUL"]
        assert main.tokens == 1200
        assert T._SPEND_CHIP["WASTEFUL"] in T.render_lanes_text(frame.lanes)

    def test_snapshot_without_journal_has_no_spend_chips(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert all(s.spend_chip == "" for s in frame.lanes)


# ---------------------------------------------------------------------------
# The SELF_MODIFY-blocked-edit banner (issue #145): surface a recent kernel-edit
# deny + the override-window state, with the one-step unblock — and NOTHING when
# there is no fresh block (the byte-identical pre-#145 frame).
# ---------------------------------------------------------------------------


def _deny_rec(*, minutes_ago: float, target: str = "") -> dict:
    from dos import hook_observation as hobs
    rec = hobs.observation_entry(
        "pretool", "deny", reason_class="SELF_MODIFY", ts=_iso(minutes_ago))
    if target:
        rec["target"] = target
    return rec


def _write_observations(cfg, recs: list[dict]) -> None:
    from dos import hook_observation as hobs
    p = hobs.observations_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


class TestSelfModifyBanner:
    # ---- the PURE fold (latest_self_modify_block) — no disk -----------------
    def test_fold_none_when_no_deny(self):
        assert T.latest_self_modify_block([], now=NOW) is None

    def test_fold_ignores_non_self_modify_and_non_deny(self):
        recs = [
            {"verb": "pretool", "outcome": "deny", "reason_class": "OVERLAP",
             "ts": _iso(1)},
            {"verb": "pretool", "outcome": "passthrough",
             "reason_class": "SELF_MODIFY", "ts": _iso(1)},
            {"verb": "stop", "outcome": "block", "ts": _iso(1)},
        ]
        assert T.latest_self_modify_block(recs, now=NOW) is None

    def test_fold_picks_newest_self_modify_deny(self):
        recs = [
            _deny_rec(minutes_ago=30, target="a.py"),
            _deny_rec(minutes_ago=2, target="b.py"),
            _deny_rec(minutes_ago=10, target="c.py"),
        ]
        block = T.latest_self_modify_block(recs, now=NOW)
        assert block is not None and block.target == "b.py"  # the 2-min-ago one
        assert block.armed is False

    def test_fold_ages_off_stale_deny_when_unarmed(self):
        # Older than the 2h TTL → no banner when no window is armed.
        recs = [_deny_rec(minutes_ago=3 * 60, target="old.py")]
        assert T.latest_self_modify_block(recs, now=NOW) is None

    def test_fold_keeps_stale_deny_when_armed(self):
        # An open window is always worth showing, regardless of the deny's age.
        recs = [_deny_rec(minutes_ago=3 * 60, target="old.py")]
        block = T.latest_self_modify_block(
            recs, now=NOW, armed=True, armed_until="2099-01-01T00:00:00+00:00")
        assert block is not None and block.armed is True

    # ---- the renderer -------------------------------------------------------
    def test_render_banner_none_is_empty(self):
        assert T.render_self_modify_banner(None) == ""

    def test_render_banner_unarmed_offers_suggest_prescoped(self):
        block = T.SelfModifyBlock(ts=_iso(5), age_ms=5 * 60 * 1000,
                                  target="pkg/widget.py", armed=False)
        txt = T.render_self_modify_banner(block)
        assert "⛔" in txt and "SELF_MODIFY" in txt
        assert "dos override suggest pkg/widget.py" in txt  # pre-scoped to the block
        assert "never arms" in txt

    def test_render_banner_armed_shows_allowed_and_disarm(self):
        block = T.SelfModifyBlock(ts=_iso(5), age_ms=5 * 60 * 1000, target="x.py",
                                  armed=True, armed_until="2026-06-01T12:30:00+00:00")
        txt = T.render_self_modify_banner(block)
        assert "ARMED" in txt and "dos override disarm" in txt

    # ---- snapshot integration + the byte-identical litmus -------------------
    def test_snapshot_surfaces_a_recent_deny(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        _write_observations(cfg, [_deny_rec(minutes_ago=5, target="pkg/widget.py")])
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert frame.self_modify_block is not None
        text = T.render_frame_text(frame)
        assert "⛔ kernel edit blocked (SELF_MODIFY)" in text
        assert "dos override suggest pkg/widget.py" in text

    def test_snapshot_no_deny_renders_byte_identical_frame(self, tmp_path: Path):
        """LITMUS 3: with no fresh SELF_MODIFY deny, the frame and its render are
        exactly the pre-#145 output — the banner is reachable only via a block."""
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        # No observation log at all.
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert frame.self_modify_block is None
        text = T.render_frame_text(frame)
        assert "⛔" not in text
        assert "override" not in text.lower()
        # An empty observation log (present but no SELF_MODIFY deny) is also silent.
        _write_observations(cfg, [
            {"verb": "pretool", "outcome": "passthrough", "ts": _iso(1)}])
        frame2 = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert frame2.self_modify_block is None
        assert T.render_frame_text(frame2) == text  # byte-identical

    def test_snapshot_armed_window_flips_banner(self, tmp_path: Path):
        from dos import override_facts as ovr
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        _write_observations(cfg, [_deny_rec(minutes_ago=5, target="pkg/widget.py")])
        armp = ovr.arm_path(cfg.root)
        armp.parent.mkdir(parents=True, exist_ok=True)
        armp.write_text(
            ovr.render_arm_toml("issue #145 supervised",
                                until=NOW + dt.timedelta(minutes=30)),
            encoding="utf-8")
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert frame.self_modify_block is not None and frame.self_modify_block.armed
        text = T.render_frame_text(frame)
        assert "ARMED" in text and "dos override disarm" in text

    def test_snapshot_torn_observation_log_never_crashes(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        from dos import hook_observation as hobs
        p = hobs.observations_path(cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not json\n{partial\n", encoding="utf-8")
        # Fail-soft: a torn log yields no banner, never an error.
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        assert frame.self_modify_block is None

    def test_frame_to_dict_carries_block(self, tmp_path: Path):
        repo = _git_repo(tmp_path, commits=("seed",))
        cfg = default_config(repo)
        _write_observations(cfg, [_deny_rec(minutes_ago=5, target="z.py")])
        frame = T.snapshot(cfg, verify=lambda p, ph: False, now=NOW)
        d = frame.to_dict()
        assert d["self_modify_block"]["target"] == "z.py"
        # And None serializes as null, not a missing key.
        empty = T.snapshot(default_config(_git_repo(tmp_path / "e", commits=("s",))),
                           verify=lambda p, ph: False, now=NOW)
        assert empty.to_dict()["self_modify_block"] is None
