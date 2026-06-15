"""The `model-call` family — per-MODEL-CALL timing + spend telemetry.

Mirrors `test_hook_observation`: the PURE builder validation, the fail-soft
writer / tolerant reader round-trip, the PURE roll-up fold (grouping, latency
percentiles, summed spend, the `since` window), and a CLI smoke over the
`dos model-calls` verb.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dos import model_call as mc
from dos.spend import SpendBreakdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(model="claude-opus", duration_ms=100.0, ts="2026-06-15T10:00:00Z", **extra):
    """A model-call record in the shape the builder produces."""
    e = {
        "schema": {"family": "model-call", "version": 1},
        "op": "MODELCALL",
        "model": model,
        "duration_ms": duration_ms,
        "ts": ts,
    }
    e.update(extra)
    return e


# ---------------------------------------------------------------------------
# The PURE builder
# ---------------------------------------------------------------------------


def test_builder_required_fields_present():
    e = mc.model_call_entry("claude-sonnet", 42.5)
    assert e["op"] == "MODELCALL"
    assert e["model"] == "claude-sonnet"
    assert e["duration_ms"] == 42.5
    assert e["schema"] == {"family": "model-call", "version": 1}


def test_builder_empty_model_raises():
    with pytest.raises(ValueError, match="name its model"):
        mc.model_call_entry("", 10.0)


def test_builder_negative_durations_raise():
    with pytest.raises(ValueError, match="duration_ms"):
        mc.model_call_entry("m", -1.0)
    with pytest.raises(ValueError, match="ttft_ms"):
        mc.model_call_entry("m", 1.0, ttft_ms=-1.0)


def test_builder_optional_fields_only_when_set():
    bare = mc.model_call_entry("m", 1.0)
    assert "ttft_ms" not in bare and "run_id" not in bare and "ts" not in bare
    full = mc.model_call_entry("m", 1.0, ttft_ms=5.0, run_id="RID-1", ts="2026-06-15T00:00:00Z")
    assert full["ttft_ms"] == 5.0
    assert full["run_id"] == "RID-1"
    assert full["ts"] == "2026-06-15T00:00:00Z"


def test_builder_flattens_spend_breakdown():
    spend = SpendBreakdown.of(input=100, output=50, cache_read=900, reasoning=10)
    e = mc.model_call_entry("m", 1.0, spend=spend)
    assert e["input"] == 100
    assert e["output"] == 50
    assert e["cache_read"] == 900
    assert e["reasoning"] == 10
    # Zero counts are omitted (the additive contract — a bare record stays small).
    assert "cache_creation" not in e


# ---------------------------------------------------------------------------
# The writer / reader round-trip + tolerance
# ---------------------------------------------------------------------------


def test_append_and_read_round_trip(tmp_path: Path):
    p = tmp_path / "model_calls.jsonl"
    assert mc.append(mc.model_call_entry("m1", 10.0), path=p) is True
    assert mc.append(mc.model_call_entry("m2", 20.0, ts="2026-06-15T11:00:00Z"), path=p) is True
    recs = mc.read_model_calls(path=p)
    assert len(recs) == 2
    assert {r["model"] for r in recs} == {"m1", "m2"}
    # `append` stamps `ts` when the entry omits it.
    assert all(r.get("ts") for r in recs)


def test_append_disabled_by_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DOS_MODEL_CALL_METRICS", "0")
    p = tmp_path / "model_calls.jsonl"
    assert mc.append(mc.model_call_entry("m", 1.0), path=p) is False
    assert mc.read_model_calls(path=p) == ()


def test_append_never_raises_on_bad_path(tmp_path: Path):
    # A path whose parent cannot be created degrades to False, never an exception.
    bad = tmp_path / "a-file"
    bad.write_text("not a dir", encoding="utf-8")
    assert mc.append(mc.model_call_entry("m", 1.0), path=bad / "nested" / "x.jsonl") is False


def test_reader_missing_file_degrades_to_empty(tmp_path: Path):
    assert mc.read_model_calls(path=tmp_path / "nope.jsonl") == ()


def test_reader_skips_unreadable_lines(tmp_path: Path):
    p = tmp_path / "model_calls.jsonl"
    lines = [
        json.dumps(_rec(model="good", duration_ms=5.0)),  # kept
        "",                                                # blank — skipped
        "{not json",                                       # torn — skipped
        json.dumps({"schema": {"family": "other", "version": 1}, "op": "MODELCALL",
                    "model": "x", "duration_ms": 1.0}),    # wrong family — skipped
        json.dumps({"schema": {"family": "model-call", "version": 99}, "op": "MODELCALL",
                    "model": "x", "duration_ms": 1.0}),    # version ahead — skipped
        json.dumps({"schema": {"family": "model-call", "version": 1}, "op": "OTHER",
                    "model": "x", "duration_ms": 1.0}),    # wrong op — skipped
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    recs = mc.read_model_calls(path=p)
    assert [r["model"] for r in recs] == ["good"]


# ---------------------------------------------------------------------------
# The PURE roll-up fold
# ---------------------------------------------------------------------------


def test_roll_up_empty_is_zero():
    roll = mc.roll_up([])
    assert roll.call_count == 0
    assert roll.models == ()


def test_roll_up_groups_by_model_busiest_first():
    recs = [
        _rec(model="a", duration_ms=10.0),
        _rec(model="a", duration_ms=30.0),
        _rec(model="b", duration_ms=20.0),
    ]
    roll = mc.roll_up(recs)
    assert roll.call_count == 3
    assert [m.model for m in roll.models] == ["a", "b"]  # a has 2 calls → first
    a = roll.models[0]
    assert a.calls == 2
    assert a.duration.max == 30.0
    assert a.duration.mean == 20.0


def test_roll_up_latency_percentiles():
    # Ten durations 10..100 — nearest-rank p50 = 50th-rank value, p95 = 95th.
    recs = [_rec(model="m", duration_ms=float(v)) for v in range(10, 101, 10)]
    roll = mc.roll_up(recs)
    m = roll.models[0]
    assert m.duration.p50 == 50.0   # ceil(0.50*10)=5th of [10..100] → 50
    assert m.duration.p95 == 100.0  # ceil(0.95*10)=10th → 100
    assert m.duration.max == 100.0


def test_roll_up_sums_spend_and_derives_cache_ratio():
    s1 = SpendBreakdown.of(input=100, output=50, cache_read=850)
    s2 = SpendBreakdown.of(input=100, output=50, cache_read=850)
    recs = [
        _rec(model="m", **{k: getattr(s1, k) for k in ("input", "output", "cache_read")}),
        _rec(model="m", **{k: getattr(s2, k) for k in ("input", "output", "cache_read")}),
    ]
    roll = mc.roll_up(recs)
    m = roll.models[0]
    assert m.spend.input == 200
    assert m.spend.output == 100
    assert m.spend.cache_read == 1700
    # prefill = 200 + 1700 = 1900; cache_hit = 1700/1900
    assert m.spend.cache_hit_ratio == pytest.approx(1700 / 1900)


def test_roll_up_ttft_optional_series():
    recs = [
        _rec(model="m", duration_ms=10.0, ttft_ms=2.0),
        _rec(model="m", duration_ms=20.0),  # no ttft
    ]
    roll = mc.roll_up(recs)
    m = roll.models[0]
    assert m.calls == 2
    assert m.duration.n == 2
    assert m.ttft.n == 1   # only the call that reported ttft contributes
    assert m.ttft.max == 2.0


def test_roll_up_since_window_skips_old_and_undatable():
    recs = [
        _rec(model="m", duration_ms=10.0, ts="2026-06-15T09:00:00Z"),  # before → dropped
        _rec(model="m", duration_ms=20.0, ts="2026-06-15T11:00:00Z"),  # kept
        {"schema": {"family": "model-call", "version": 1}, "op": "MODELCALL",
         "model": "m", "duration_ms": 30.0},                            # no ts → dropped under window
    ]
    roll = mc.roll_up(recs, since="2026-06-15T10:00:00Z")
    assert roll.call_count == 1
    assert roll.models[0].duration.max == 20.0
    assert roll.since == "2026-06-15T10:00:00Z"


def test_roll_up_total_across_models():
    recs = [_rec(model="a", duration_ms=10.0), _rec(model="b", duration_ms=30.0)]
    roll = mc.roll_up(recs)
    assert roll.total.model == "(all)"
    assert roll.total.calls == 2
    assert roll.total.duration.max == 30.0


def test_to_dict_shape():
    roll = mc.roll_up([_rec(model="m", duration_ms=10.0, input=5, output=5)])
    d = roll.to_dict()
    assert d["call_count"] == 1
    assert d["total"]["model"] == "(all)"
    assert d["models"][0]["model"] == "m"
    assert "duration_ms" in d["models"][0]
    assert "spend" in d["models"][0]


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


def test_render_empty_is_honest():
    out = mc.render_roll_text(mc.roll_up([]))
    assert "no model calls recorded yet" in out


def test_render_table_has_rows():
    recs = [_rec(model="claude-opus", duration_ms=100.0, input=10, output=10, cache_read=80)]
    out = mc.render_roll_text(mc.roll_up(recs))
    assert "claude-opus" in out
    assert "p50ms" in out
    assert "(all)" in out


# ---------------------------------------------------------------------------
# The CLI verb (subprocess smoke)
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _seed_log(workspace: Path) -> None:
    p = workspace / ".dos" / "metrics" / "model_calls.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(
            json.dumps(_rec(model="claude-opus", duration_ms=d, input=10, output=10, cache_read=80))
            for d in (50.0, 150.0)
        ) + "\n",
        encoding="utf-8",
    )


def test_cli_model_calls_table(tmp_path: Path):
    _seed_log(tmp_path)
    r = _run_cli("model-calls", "--workspace", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "claude-opus" in r.stdout
    assert "2 call(s)" in r.stdout


def test_cli_model_calls_json(tmp_path: Path):
    _seed_log(tmp_path)
    r = _run_cli("model-calls", "--workspace", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["call_count"] == 2
    assert obj["models"][0]["model"] == "claude-opus"
    assert obj["total"]["calls"] == 2


def test_cli_model_calls_empty_is_honest(tmp_path: Path):
    r = _run_cli("model-calls", "--workspace", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no model calls recorded yet" in r.stdout
