"""The `hook-observation` family — the kernel-owned per-call telemetry contract (docs/297).

Pins the three clauses of issue #24's done-condition plus the contract mechanics:

* the PURE fold (`intervention_rate`) computes the rate from observation records
  ONLY — lane-journal-shaped records cannot enter either side of the ratio, and a
  `delegate` handoff leaves the denominator (the rule that counts each call once
  in the binary-only / Python-only / mixed writer worlds);
* `dos helped` on a workspace WITH an observation log renders the honest
  "of N adjudicated calls" rate; WITHOUT one it renders today's output
  byte-identically (and `--json` carries no `tool_calls` key);
* the writer half: the Python hook verbs append the same schema-tagged records
  the native binary writes (fail-soft, `DOS_HOOK_METRICS=0` opt-out), and the
  reader parses a binary-written line verbatim (the cross-writer fixture).
"""

from __future__ import annotations

import io
import json

import pytest

from dos import help_summary as hs
from dos import hook_observation as hobs


# ---------------------------------------------------------------------------
# Helpers — records in the shapes the two conforming writers produce.
# ---------------------------------------------------------------------------


def _obs(verb="pretool", outcome="passthrough", ts="2026-06-10T10:00:00Z", **extra):
    rec = {
        "schema": {"family": "hook-observation", "version": 1},
        "op": "OBSERVE",
        "verb": verb,
        "outcome": outcome,
        "exit": 0,
        "latency_ms": 1.0,
        "ts": ts,
    }
    rec.update(extra)
    return rec


# One line byte-for-byte in the shape the native Go binary writes (synthetic
# values, neutral content) — the cross-writer conformance fixture.
_GO_WRITTEN_LINE = (
    '{"dialect": "claude-code", "exit": 0, "latency_ms": 0.503, "op": "OBSERVE", '
    '"outcome": "passthrough", "rung": "none", '
    '"schema": {"family": "hook-observation", "version": 1}, "tree_known": true, '
    '"ts": "2026-06-10T21:28:50Z", "verb": "pretool"}'
)


def _enforce(*, intervention="BLOCK", reason_class="SELF_MODIFY", tool="Write",
             withheld=True, holder="S1", ts="2026-06-10T10:00:00Z"):
    """An OP_ENFORCE lane-journal record (the OTHER log — must never enter the rate)."""
    return {
        "op": "ENFORCE", "intervention": intervention, "reason_class": reason_class,
        "tool": tool, "withheld": withheld, "holder": holder, "ts": ts,
        "handler": "admission", "reason": "",
    }


# ---------------------------------------------------------------------------
# The PURE fold — the denominator rule, the like-for-like rule, the window.
# ---------------------------------------------------------------------------


def test_rate_counts_pretool_only_and_excludes_delegates():
    """pretool is the denominator; a delegate is a handoff, not an adjudication."""
    recs = [
        _obs(outcome="passthrough"),
        _obs(outcome="passthrough"),
        _obs(outcome="deny"),
        _obs(outcome="warn"),
        _obs(outcome="delegate"),          # handoff — its verdict is another record
        _obs(verb="posttool", outcome="passthrough"),  # not a tool-call admission
        _obs(verb="stop", outcome="block"),
        _obs(verb="marker", outcome="allow"),
    ]
    rate = hobs.intervention_rate(recs)
    assert rate.pretool_records == 5
    assert rate.adjudicated == 4           # 5 pretool − 1 delegate
    assert rate.passed == 2
    assert rate.intervened == 2            # deny + warn
    assert rate.delegated == 1
    assert rate.passed_pct == pytest.approx(50.0)
    assert rate.intervened_pct == pytest.approx(50.0)


def test_rate_fold_ignores_lane_journal_shaped_records():
    """The like-for-like rule: a lane-journal ENFORCE record cannot enter the fold.

    The journal record IS an intervention (a BLOCK) — if it leaked into either
    side of the ratio the rate would mix logs. It carries no `verb`, so the fold
    structurally skips it."""
    recs = [
        _enforce(),                        # the journal numerator — must not count
        _obs(outcome="passthrough"),
        _obs(outcome="deny"),
    ]
    rate = hobs.intervention_rate(recs)
    assert rate.adjudicated == 2
    assert rate.intervened == 1            # the deny — never the journal BLOCK


def test_rate_zero_adjudicated_degrades_to_zero_pcts():
    """A delegate-only (or empty) log yields no divisible denominator — 0.0, not a crash."""
    assert hobs.intervention_rate([]).adjudicated == 0
    rate = hobs.intervention_rate([_obs(outcome="delegate")])
    assert rate.adjudicated == 0
    assert rate.passed_pct == 0.0
    assert rate.intervened_pct == 0.0


def test_rate_since_window_skips_older_and_undatable():
    """`since` keeps ts >= since; a windowed fold never counts an undatable record."""
    recs = [
        _obs(outcome="deny", ts="2026-06-09T10:00:00Z"),   # before the window
        _obs(outcome="deny", ts="2026-06-10T12:00:00Z"),   # inside
        _obs(outcome="passthrough", ts=""),                # undatable
    ]
    rate = hobs.intervention_rate(recs, since="2026-06-10T00:00:00Z")
    assert rate.adjudicated == 1 and rate.intervened == 1
    # No window → everything (the undatable record counts again).
    assert hobs.intervention_rate(recs).adjudicated == 3


# ---------------------------------------------------------------------------
# The entry builder + writer + reader — the contract's mechanics.
# ---------------------------------------------------------------------------


def test_observation_entry_minimal_and_only_when_set():
    e = hobs.observation_entry("pretool", "passthrough", latency_ms=1.25)
    assert e["schema"] == {"family": "hook-observation", "version": 1}
    assert e["op"] == "OBSERVE"
    assert e["verb"] == "pretool" and e["outcome"] == "passthrough"
    assert e["exit"] == 0 and e["latency_ms"] == 1.25
    # Absent verb-specific fields are OMITTED, not nulled (the additive contract).
    for absent in ("run_id", "rung", "reason_class", "dialect", "tree_known",
                   "stream_state", "marker_count", "claims_seen", "verify_source",
                   "blocked_plan", "panic_recovered", "ts"):
        assert absent not in e
    rich = hobs.observation_entry("stop", "block", claims_seen=2,
                                  verify_source="none", blocked_plan="P",
                                  blocked_phase="X")
    assert rich["claims_seen"] == 2 and rich["blocked_plan"] == "P"


def test_observation_entry_requires_verb_and_outcome():
    with pytest.raises(ValueError):
        hobs.observation_entry("", "passthrough")
    with pytest.raises(ValueError):
        hobs.observation_entry("pretool", "")


def test_append_read_roundtrip_stamps_ts(tmp_path, monkeypatch):
    monkeypatch.delenv("DOS_HOOK_METRICS", raising=False)
    p = tmp_path / ".dos" / "metrics" / "observations.jsonl"
    assert hobs.append(hobs.observation_entry("pretool", "deny"), path=p) is True
    recs = hobs.read_observations(p)
    assert len(recs) == 1
    assert recs[0]["outcome"] == "deny"
    assert recs[0]["ts"]  # stamped at write time


def test_append_gated_by_metrics_env(tmp_path, monkeypatch):
    p = tmp_path / "obs.jsonl"
    monkeypatch.setenv("DOS_HOOK_METRICS", "0")
    assert hobs.append(hobs.observation_entry("pretool", "deny"), path=p) is False
    assert not p.exists()
    # --debug always logs (a trace run asked to see everything).
    assert hobs.append(hobs.observation_entry("pretool", "deny"), path=p,
                       debug=True) is True


def test_append_is_fail_soft(tmp_path):
    """An unwritable path returns False — it NEVER raises into a hook verb."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    # parent "directory" is a regular file → mkdir/open must fail, quietly.
    p = blocker / "sub" / "obs.jsonl"
    assert hobs.append(hobs.observation_entry("pretool", "deny"), path=p) is False


def test_reader_is_tolerant_and_gates_on_schema(tmp_path):
    p = tmp_path / "obs.jsonl"
    lines = [
        _GO_WRITTEN_LINE,                                  # a binary-written record
        "",                                                 # blank
        '{"torn": ',                                        # torn tail
        '"a bare string"',                                  # non-dict JSON
        json.dumps({**_obs(), "schema": {"family": "lane-journal", "version": 1}}),
        json.dumps({**_obs(), "schema": {"family": "hook-observation", "version": 99}}),
        json.dumps({**_obs(), "op": "ENFORCE"}),            # wrong op
        json.dumps(_obs(outcome="deny")),                   # good
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    recs = hobs.read_observations(p)
    assert [r["outcome"] for r in recs] == ["passthrough", "deny"]
    # The Go-written line parsed verbatim — the cross-writer conformance pin.
    assert recs[0]["verb"] == "pretool" and recs[0]["tree_known"] is True


def test_reader_missing_file_degrades_to_empty(tmp_path):
    assert hobs.read_observations(tmp_path / "nope.jsonl") == ()


# ---------------------------------------------------------------------------
# `dos helped` — the issue-#24 done-condition, end to end.
# ---------------------------------------------------------------------------


def _seed_journal(ws, recs):
    dos_dir = ws / ".dos"
    dos_dir.mkdir(exist_ok=True)
    (dos_dir / "lane-journal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _seed_observations(ws, recs):
    mdir = ws / ".dos" / "metrics"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "observations.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_cli_helped_without_observation_log_is_byte_identical(tmp_path, capsys):
    """No observation log → today's output, byte for byte (graceful absence)."""
    from dos import cli

    recs = [_enforce(), _enforce(intervention="WARN", withheld=False)]
    _seed_journal(tmp_path, recs)
    rc = cli.main(["helped", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # Byte-identical to the rate-less pure render — no rate artifacts at all.
    assert out == hs.render_summary_text(hs.summarize(recs)) + "\n"
    assert "adjudicated" not in out


def test_cli_helped_json_without_log_has_no_tool_calls_key(tmp_path, capsys):
    from dos import cli

    _seed_journal(tmp_path, [_enforce()])
    rc = cli.main(["helped", "--workspace", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "tool_calls" not in payload


def test_cli_helped_renders_rate_from_observation_log_only(tmp_path, capsys):
    """The rate's numerator AND denominator come from the observation log — the
    lane journal's own intervention count (1 BLOCK) appears in the headline and
    NOWHERE in the rate (the never-mix pin: 2/9 from the log, never 1/anything)."""
    from dos import cli

    _seed_journal(tmp_path, [_enforce()])                  # journal: 1 BLOCK
    _seed_observations(tmp_path, (
        [_obs(outcome="passthrough") for _ in range(7)]
        + [_obs(outcome="deny"), _obs(outcome="deny"),     # log: 2 intervened
           _obs(outcome="delegate")]                       # …of 9 adjudicated
    ))
    rc = cli.main(["helped", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOS has caught 1 thing" in out                 # the journal headline
    assert "of 9 tool calls adjudicated by the hooks" in out
    assert "7 passed untouched (77.8%)" in out
    assert "2 were intervened on (22.2%)" in out
    # The journal's count never enters the rate line.
    assert "1 were intervened" not in out
    assert "of 10 tool calls" not in out                   # delegate left the denominator


def test_cli_helped_json_rate_object(tmp_path, capsys):
    from dos import cli

    _seed_journal(tmp_path, [_enforce()])
    _seed_observations(tmp_path, [
        _obs(outcome="passthrough"), _obs(outcome="deny"), _obs(outcome="delegate"),
    ])
    rc = cli.main(["helped", "--workspace", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool_calls"] == {
        "adjudicated": 2, "passed": 1, "intervened": 1, "delegated": 1,
        "passed_pct": 50.0, "intervened_pct": 50.0,
    }
    # The journal-side counts are untouched by the rate's presence.
    assert payload["total"] == 1 and payload["blocked"] == 1


def test_cli_helped_session_scope_suppresses_rate(tmp_path, capsys):
    """Observation records carry no session key — a session-scoped rate would
    silently widen to the whole fleet, so --session renders no rate line."""
    from dos import cli

    _seed_journal(tmp_path, [_enforce(holder="S1")])
    _seed_observations(tmp_path, [_obs(outcome="deny")])
    rc = cli.main(["helped", "--workspace", str(tmp_path), "--session", "S1"])
    assert rc == 0
    assert "adjudicated" not in capsys.readouterr().out


def test_cli_helped_delegate_only_log_renders_no_rate(tmp_path, capsys):
    """A log with zero adjudicated calls has no honest rate — graceful absence."""
    from dos import cli

    recs = [_enforce()]
    _seed_journal(tmp_path, recs)
    _seed_observations(tmp_path, [_obs(outcome="delegate")])
    rc = cli.main(["helped", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == hs.render_summary_text(hs.summarize(recs)) + "\n"


# ---------------------------------------------------------------------------
# The second writer — the Python hook verbs append the same family (docs/297 P3).
# ---------------------------------------------------------------------------


def test_hook_pretool_python_path_writes_denominator_record(tmp_path, monkeypatch, capsys):
    """A Python-decided pretool call appends ONE observation — the denominator."""
    from dos import cli

    monkeypatch.setenv("DOS_HOOK_NATIVE", "0")             # force the Python decider
    monkeypatch.delenv("DOS_HOOK_METRICS", raising=False)
    event = {"tool_name": "Read", "tool_input": {"file_path": "a.txt"},
             "cwd": str(tmp_path), "session_id": "S1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "pretool", "--workspace", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()
    recs = hobs.read_observations(tmp_path / ".dos" / "metrics" / "observations.jsonl")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["verb"] == "pretool" and rec["outcome"] == "passthrough"
    assert rec["latency_ms"] >= 0 and rec["ts"]
    assert rec["dialect"]                                  # the resolved dialect name
    # …and the fold sees it as one adjudicated, untouched call.
    rate = hobs.intervention_rate(recs)
    assert rate.adjudicated == 1 and rate.passed == 1


def test_hook_pretool_metrics_opt_out_writes_nothing(tmp_path, monkeypatch, capsys):
    from dos import cli

    monkeypatch.setenv("DOS_HOOK_NATIVE", "0")
    monkeypatch.setenv("DOS_HOOK_METRICS", "0")
    event = {"tool_name": "Read", "tool_input": {"file_path": "a.txt"},
             "cwd": str(tmp_path), "session_id": "S1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "pretool", "--workspace", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()
    assert not (tmp_path / ".dos" / "metrics" / "observations.jsonl").exists()


def test_hook_stop_no_claims_writes_observation(tmp_path, monkeypatch, capsys):
    from dos import cli

    monkeypatch.delenv("DOS_HOOK_METRICS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    rc = cli.main(["hook", "stop", "--json", "--workspace", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()
    recs = hobs.read_observations(tmp_path / ".dos" / "metrics" / "observations.jsonl")
    assert len(recs) == 1
    assert recs[0]["verb"] == "stop" and recs[0]["outcome"] == "no-claims"


def test_hook_posttool_python_path_writes_stream_observation(tmp_path, monkeypatch, capsys):
    from dos import cli

    monkeypatch.setenv("DOS_HOOK_NATIVE", "0")
    monkeypatch.delenv("DOS_HOOK_METRICS", raising=False)
    event = {"tool_name": "Read", "tool_input": {"file_path": "a.txt"},
             "tool_response": {"ok": True}, "cwd": str(tmp_path), "session_id": "S1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "posttool", "--workspace", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()
    recs = hobs.read_observations(tmp_path / ".dos" / "metrics" / "observations.jsonl")
    assert len(recs) == 1
    assert recs[0]["verb"] == "posttool" and recs[0]["outcome"] == "passthrough"
    assert recs[0]["stream_state"]


# ---------------------------------------------------------------------------
# The quotable headline (issue #71) — the share-shaped fold + receipts + zeros.
# ---------------------------------------------------------------------------


def test_headline_counts_each_class_from_its_verb_outcome():
    """Each share-shaped count comes from its (verb, outcome) tuple; adjudicated
    is the intervention_rate denominator (pretool minus delegates)."""
    recs = [
        _obs(verb="pretool", outcome="passthrough"),
        _obs(verb="pretool", outcome="passthrough"),
        _obs(verb="pretool", outcome="deny", reason_class="SELF_MODIFY"),
        _obs(verb="pretool", outcome="delegate"),  # leaves the denominator
        _obs(verb="stop", outcome="block", blocked_plan="AUTH", blocked_phase="AUTH2"),
        _obs(verb="posttool", outcome="warn"),
    ]
    s = hobs.headline_summary(recs)
    assert s.adjudicated == 3            # 3 pretool, minus the 1 delegate
    assert s.false_done_refused == 1     # the stop/block
    assert s.edits_blocked == 1          # the pretool/deny
    assert s.warned == 1                 # the posttool/warn
    # collisions are NOT witnessed by this log — a structural 0, labelled.
    assert s.collisions_admitted == 0
    assert s.collisions_witnessed is False


def test_headline_all_zeros_renders_honest_coverage():
    """A quiet (empty) log renders zeros + the coverage clause, never suppressed
    (the issue's honest-zeros requirement)."""
    s = hobs.headline_summary([])
    assert s.adjudicated == 0 and s.false_done_refused == 0
    txt = hobs.render_headline_text(s)
    assert "0 tool call(s) adjudicated" in txt
    assert "0 false \"done\"(s) refused at stop" in txt
    assert "on the surfaces the hooks gate" in txt
    assert "not witnessed here" in txt   # the collisions coverage note


def test_headline_since_windows_out_older_and_undatable():
    """`since` keeps ts >= since; an undatable record is skipped under a window."""
    recs = [
        _obs(verb="stop", outcome="block", ts="2026-06-09T10:00:00Z"),  # before
        _obs(verb="stop", outcome="block", ts="2026-06-10T12:00:00Z"),  # inside
        _obs(verb="stop", outcome="block", ts=""),                      # undatable
    ]
    s = hobs.headline_summary(recs, since="2026-06-10T00:00:00Z")
    assert s.false_done_refused == 1
    # No window → all datable + undatable counted.
    assert hobs.headline_summary(recs).false_done_refused == 3


def test_headline_receipts_link_to_the_regenerating_command():
    """--receipts banks env-authored records + the command that re-derives the
    verdict: a stop-block → dos verify <plan> <phase>; a typed deny → dos man wedge."""
    recs = [
        _obs(verb="stop", outcome="block", blocked_plan="RS", blocked_phase="RS1"),
        _obs(verb="pretool", outcome="deny", reason_class="SELF_MODIFY"),
    ]
    s = hobs.headline_summary(recs, with_receipts=True)
    fd = s.receipts["false_done_refused"][0]
    assert fd.regen_command == "dos verify RS RS1"
    eb = s.receipts["edits_blocked"][0]
    assert eb.regen_command == "dos man wedge SELF_MODIFY"
    txt = hobs.render_headline_text(s, with_receipts=True)
    assert "dos verify RS RS1" in txt
    assert "dos man wedge SELF_MODIFY" in txt


def test_headline_no_receipts_when_not_requested():
    """The cheap path banks nothing — receipts is empty without with_receipts."""
    recs = [_obs(verb="stop", outcome="block", blocked_plan="A", blocked_phase="A1")]
    assert hobs.headline_summary(recs).receipts == {}


def test_headline_json_round_trips(tmp_path, capsys):
    from dos import cli

    _seed_observations(tmp_path, [
        _obs(verb="pretool", outcome="passthrough"),
        _obs(verb="stop", outcome="block", blocked_plan="AUTH", blocked_phase="AUTH2"),
    ])
    rc = cli.main(["headline", "--workspace", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adjudicated"] == 1
    assert payload["false_done_refused"] == 1
    assert payload["collisions_witnessed"] is False
    # --json banks receipts so the regen command is machine-readable.
    assert payload["receipts"]["false_done_refused"][0]["regen_command"] == \
        "dos verify AUTH AUTH2"


def test_cli_headline_renders_the_quotable_line(tmp_path, capsys):
    from dos import cli

    _seed_observations(tmp_path, [
        _obs(verb="pretool", outcome="passthrough"),
        _obs(verb="pretool", outcome="deny", reason_class="SELF_MODIFY"),
    ])
    rc = cli.main(["headline", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 tool call(s) adjudicated" in out
    assert "1 edit(s) blocked at the kernel boundary" in out


def test_cli_headline_empty_log_is_honest_zeros(tmp_path, capsys):
    from dos import cli

    _seed_observations(tmp_path, [])
    rc = cli.main(["headline", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 tool call(s) adjudicated" in out
    assert "on the surfaces the hooks gate" in out
