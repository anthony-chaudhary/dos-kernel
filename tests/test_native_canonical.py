"""docs/385 TP1 — the truth-pointer registry + its `dos doctor` surface.

The strongly-typed mandate (CLAUDE.md, 2026-06-17) flips the hook deciders to
Go-canonical. `dos.native_canonical` records that flip as data the kernel reads,
and `dos doctor` reports it so the truth-pointer is an OBSERVED fact (docs/385 §8
litmus), not a doc sentence. These tests pin both:

  * the registry's shape + the FLIPped set (a closed, named cluster — §4.0/§7);
  * that `dos doctor` (text + --json) reports the hook deciders as Go-canonical.

The byte-parity between the engines themselves lives in test_go_hook_parity.py;
here we only pin the *declaration* + its surfacing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dos import native_canonical as nc

REPO = Path(__file__).resolve().parents[1]

# The hook-decider cluster docs/385 §4.0 names as flipped at TP1 (the deciders +
# the ports they already needed to be byte-exact).
_HOOK_CLUSTER = {
    "pretool", "posttool", "marker", "stop",
    "admission", "overlap", "tree", "claim_extract", "dialect", "proc_liveness",
}
# The account-switcher ranking core, ported + flipped on the same ratchet (docs/386 §6,
# phase "386"). A driver-tier decider, not the hook cluster — its own phase label.
_ACCOUNT_DECIDERS = {"account_pick", "serving_pool", "allocate_seats"}
# The registry must cover exactly the union — a closed, named flip, not an open default.
_EXPECTED_FLIPPED = _HOOK_CLUSTER | _ACCOUNT_DECIDERS


def test_flipped_set_is_the_named_closed_set():
    """The Go-canonical set is exactly the named flips — the TP1 hook cluster plus the
    docs/386 account ranking core — no more (a stray flip would be an unreviewed
    truth-pointer move), no less."""
    got = {p.decider for p in nc.go_canonical_deciders()}
    assert got == _EXPECTED_FLIPPED


def test_every_flipped_decider_is_go_with_soak_and_phase():
    """Each flip carries its evidence: canonical engine 'go', a non-empty phase label
    and soak reference, and a recorded shadow side (the surviving Python copy)."""
    for p in nc.go_canonical_deciders():
        assert p.canonical == "go", p
        assert p.phase, f"{p.decider} flipped with no phase"
        assert p.soak, f"{p.decider} flipped with no soak evidence"
        # The Python copy survives as the docs/100 fallback (docs/385 §6): nothing is
        # deleted yet, so the shadow is the fallback, never 'none' yet.
        assert p.shadow == "python-fallback", p


def test_phase_labels_match_their_cluster():
    """The hook cluster flipped at TP1; the account ranking core at its own phase
    (`386`) — the phase label is honest about WHICH flip each decider rode."""
    by_name = {p.decider: p for p in nc.go_canonical_deciders()}
    for name in _HOOK_CLUSTER:
        assert by_name[name].phase == "TP1", name
    for name in _ACCOUNT_DECIDERS:
        assert by_name[name].phase == "386", name


def test_go_canonical_deciders_is_name_sorted():
    """A stable doctor surface — the FLIPped set is returned name-sorted."""
    names = [p.decider for p in nc.go_canonical_deciders()]
    assert names == sorted(names)


def test_truth_pointer_lookup_and_default():
    """`truth_pointer`/`canonical_engine` resolve a flipped decider and default an
    un-flipped one to Python (the standing default outside the named set)."""
    assert nc.truth_pointer("pretool") is not None
    assert nc.canonical_engine("pretool") == "go"
    # The oracle/verify recognizer has NOT flipped (that is TP3, high-risk): it
    # must still report Python as canonical.
    assert nc.truth_pointer("oracle") is None
    assert nc.canonical_engine("oracle") == "python"


def _doctor(*flags: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "dos.cli", "doctor", "--workspace", str(REPO), *flags],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_doctor_text_reports_the_flip():
    """The text report names the Go-canonical deciders on a `go-canonical` line."""
    out = _doctor()
    assert "go-canonical" in out
    # The headline deciders must be named, and the framing must be honest about
    # the surviving Python fallback.
    assert "pretool" in out and "posttool" in out
    assert "docs/385 TP1" in out


def test_doctor_json_reports_the_truth_pointer():
    """`dos doctor --json` carries a `truth_pointer` map: every flipped decider →
    its canonical engine + phase + soak + shadow."""
    report = json.loads(_doctor("--json"))
    tp = report["truth_pointer"]
    assert {p.decider for p in nc.go_canonical_deciders()} <= set(tp)
    assert tp["pretool"]["canonical"] == "go"
    assert tp["pretool"]["phase"] == "TP1"
    assert tp["pretool"]["shadow"] == "python-fallback"
    assert tp["pretool"]["soak"]  # non-empty evidence list
    # the docs/386 account ranking core is surfaced too, with its own phase label
    assert tp["account_pick"]["canonical"] == "go"
    assert tp["account_pick"]["phase"] == "386"
    assert "docs/386" in tp["account_pick"]["soak"]
