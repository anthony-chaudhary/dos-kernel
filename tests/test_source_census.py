"""Pin the data-source coverage census (`scripts/source_census.py`).

The census turns the operator goal "cover 98% of industry-known data sources DOS
can witness" into a counted, re-runnable number read from the repo's own ground
truth. These tests pin the contract that makes that number trustworthy:

  * the headline carries exactly the documented keys, all the right types;
  * RESIDUE (the irreducible no-sound-witness classes) is NEVER in the denominator
    — the honesty rule that keeps the headline from inflating by counting the
    un-witnessable as covered;
  * a source is COVERED only when its witness SHAPE resolves to a backend ACTUALLY
    registered in `dos.evidence_sources` (or the built git oracle) — recomputed at
    runtime, never a hardcoded yes (the rot pin: a renamed/removed backend flips the
    source to UNCOVERED_ROT and `--check` exits non-zero);
  * the per-shape rung is READ OFF THE LIVE SOURCE OBJECT, not the DATA (catches the
    content_diff/provider_ledger THIRD_PARTY-ceiling trap);
  * no AGENT_AUTHORED-floor source is ever counted as covered;
  * coverage clears the operator's 98% target on the real tree.

No network: the registry read degrades to the built-in floor rather than crashing.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "source_census.py"
_spec = importlib.util.spec_from_file_location("source_census", _HELPER_PATH)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


_HEADLINE_KEYS = {
    "sources_total", "covered", "spec", "residue", "uncovered_rot",
    "witnessable_universe", "coverage_pct", "shapes_total", "shapes_built",
}


# ── headline contract ───────────────────────────────────────────────────────

def test_headline_has_exactly_the_documented_keys():
    h = sc.headline(sc.gather())
    assert set(h.keys()) == _HEADLINE_KEYS


def test_headline_value_types_and_ranges():
    h = sc.headline(sc.gather())
    for k in _HEADLINE_KEYS - {"coverage_pct"}:
        assert isinstance(h[k], int), f"{k} is {type(h[k])}"
        assert h[k] >= 0, f"{k} = {h[k]}"
    assert isinstance(h["coverage_pct"], float)
    assert 0.0 <= h["coverage_pct"] <= 1.0


# ── the honesty rule: residue is never in the denominator ───────────────────

def test_residue_never_in_the_denominator():
    inv = sc.gather()
    h = sc.headline(inv)
    # the denominator is exactly covered + spec — residue is structurally absent.
    assert h["witnessable_universe"] == h["covered"] + h["spec"]
    assert h["residue"] > 0, "the census must name the irreducible residue, not hide it"
    # recompute the fraction independently — it must match, and must NOT include residue.
    denom = h["covered"] + h["spec"]
    expected = round(h["covered"] / denom, 4) if denom else 0.0
    assert h["coverage_pct"] == expected


def test_removing_residue_rows_does_not_move_coverage():
    # the load-bearing invariant: residue is out of the denominator, so deleting
    # residue rows cannot change coverage_pct (proves they were never counted).
    inv = sc.gather()
    base = sc.headline(inv)["coverage_pct"]
    inv2 = dict(inv)
    inv2["sources"] = [s for s in inv["sources"] if s["effective_status"] != "RESIDUE"]
    assert sc.headline(inv2)["coverage_pct"] == base


# ── the rot pin: COVERED is computed from the live registry, never trusted ───

def test_every_covered_source_shape_resolves_to_a_real_backend():
    # If this fails, a source declared COVERED maps to a witness shape whose backend
    # is not registered in dos.evidence_sources — fix the backend or the DATA, do not
    # delete the assertion (the rot pin, the discoverability-inventory llms.txt rule).
    registered = sc._registered_backends()
    inv = sc.gather()
    for s in inv["sources"]:
        if s["effective_status"] != "COVERED":
            continue
        backends = sc.WITNESS_SHAPES[s["shape"]]
        ok = any(b in registered for b in backends) or any(
            b == sc.GIT_ORACLE_SENTINEL and sc._git_oracle_built() for b in backends
        )
        assert ok, f"COVERED source {s['name']!r} → shape {s['shape']} has no registered backend"


def test_no_uncovered_rot_on_the_real_tree():
    # On a healthy tree every declared-COVERED source's backend is present.
    h = sc.headline(sc.gather())
    assert h["uncovered_rot"] == 0


def test_rung_is_read_off_the_live_source_not_hardcoded():
    # The rung must equal what the live source object reports — catches the
    # content_diff/provider_ledger THIRD_PARTY-ceiling trap (a hardcoded rung would
    # be a self-report). Check a non-git shape whose backend resolves.
    from dos.evidence import resolve_evidence_source
    inv = sc.gather()
    for shape, meta in inv["shapes"].items():
        if not meta["built"] or shape == "git_presence":
            continue
        # the census rung is the strongest across the shape's backends; assert each
        # resolvable backend's live rung is consistent with what the census reports.
        live = []
        for b in meta["backends"]:
            try:
                live.append(resolve_evidence_source(b).accountability.value)
            except Exception:
                pass
        if live:
            rank = {"AGENT_AUTHORED": 0, "OS_RECORDED": 1, "THIRD_PARTY": 2}
            assert meta["rung"] == max(live, key=lambda r: rank[r])


# ── the floor can never count as covered ────────────────────────────────────

def test_no_agent_authored_floor_source_is_covered():
    for s in sc.gather()["sources"]:
        if s["rung"] == "AGENT_AUTHORED":
            assert s["effective_status"] != "COVERED"
            assert s["witnessable"] is False


def test_residue_sources_are_not_witnessable():
    for s in sc.gather()["sources"]:
        if s["effective_status"] == "RESIDUE":
            assert s["witnessable"] is False


# ── closed sets ─────────────────────────────────────────────────────────────

def test_every_declared_status_is_from_the_closed_set():
    for _name, _shape, declared in sc.SOURCES:
        assert declared in {"COVERED", "SPEC", "RESIDUE"}


def test_every_source_shape_is_a_known_shape():
    known = set(sc.WITNESS_SHAPES) | sc.RESIDUE_SHAPES
    for _name, shape, _declared in sc.SOURCES:
        assert shape in known, f"unknown shape {shape!r}"


# ── the operator target ─────────────────────────────────────────────────────

def test_coverage_target_met():
    # The operator goal: cover 98% of the witnessable universe. If this drops below
    # 0.98, EITHER build the missing witness (so the source becomes COVERED) OR, if a
    # source is genuinely un-witnessable, move it to RESIDUE with a docs/358 §4 note —
    # do NOT lower this threshold to make a red bar green (that is the inflate-on-a-
    # promise sin the census exists to prevent).
    h = sc.headline(sc.gather())
    assert h["coverage_pct"] >= 0.98, f"coverage {h['coverage_pct']} < 0.98"


# ── --check rot pin + json surface ──────────────────────────────────────────

def test_check_passes_on_the_real_tree():
    r = subprocess.run([sys.executable, str(_HELPER_PATH), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_check_fails_when_a_covered_backend_disappears(monkeypatch):
    # Drop a backend the census relies on; a declared-COVERED source must flip to
    # UNCOVERED_ROT and --check must surface it as exit 1.
    real = sc._registered_backends()
    monkeypatch.setattr(sc, "_registered_backends", lambda: real - {"provider_ledger"})
    rc = sc.main(["--check"])
    assert rc == 1


def test_json_output_is_valid_and_carries_headline():
    r = subprocess.run([sys.executable, str(_HELPER_PATH), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert set(payload["headline"].keys()) == _HEADLINE_KEYS
    assert "census" in payload
