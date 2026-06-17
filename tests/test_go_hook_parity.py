"""GHF3 — the differential parity gate over the Go hook fast-path (docs/125/385).

The cross-engine ratchet that keeps the native `dos-hook` decider and the Python
`dos hook pretool` verb byte-identical on the gated decision projection (the
docs/124 contract). **As of docs/385 TP1 the truth-pointer has FLIPped: Go is the
canonical engine for the hook deciders, and Python is the corpus-pinned SHADOW.**
The corpus is byte-pinned to the *Go* decider's output by the Go `TestParityCorpus`
(`go == corpus`, asserted every CI run); this pytest is the SHADOW side, proving
the surviving Python decider still reproduces that Go-canonical corpus (the
docs/100 fallback a pure-Python install relies on — docs/385 §6: the fallback
stands, nothing is deleted yet). The two halves:

  1. it regenerates the hermetic parity corpus from the in-process Python shadow
     (`go/internal/hook/parity/gen_corpus.py`) and asserts it still reproduces the
     committed, Go-canonical bytes — a tripwire on the Python shadow drifting out
     from under the Go spec; then
  2. it runs `go test` over the same corpus, which replays each case through the
     native decider and asserts byte-equality (`TestParityCorpus` — the canonical
     pin: `go == corpus`).

Direction of authority (the docs/385 §3 flip). A divergence is reconciled toward
**Go**, not Python: if the Python shadow drifts, fix Python to match the
Go-canonical corpus; an intended behavior change is authored Go-first, re-soaked,
and only then does the corpus follow. (Before the flip the arrow pointed the
other way — the corpus was whatever Python emitted and Go had to match it.)

If the Go toolchain is absent, the cross-engine half SKIPS (the gate runs wherever
Go is available — CI installs it; a pure-Python dev box without Go still gets a
green suite, just without the cross-engine check). If `go test` reports a byte
drift, this FAILS loudly with the Go diff.

Run: `python -m pytest tests/test_go_hook_parity.py -q`
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GO_DIR = REPO / "go"
PARITY_DIR = GO_DIR / "internal" / "hook" / "parity"
GEN = PARITY_DIR / "gen_corpus.py"
CORPUS = PARITY_DIR / "corpus.jsonl"
GEN_POST = PARITY_DIR / "gen_corpus_posttool.py"
CORPUS_POST = PARITY_DIR / "corpus_posttool.jsonl"


def _have_go() -> bool:
    return shutil.which("go") is not None


def _regen(gen: Path) -> str:
    """Regenerate a corpus from the live Python decider; return its text."""
    out = subprocess.run(
        ["python", str(gen)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout


def _regen_corpus() -> str:
    return _regen(GEN)


def test_corpus_regenerates_and_is_self_consistent():
    """The Python SHADOW must still reproduce the committed, Go-canonical corpus
    (docs/385 TP1): re-running the in-process Python decider over the same injected
    inputs yields byte-identical lines. A drift means the Python shadow has moved
    out from under the Go spec. Regenerates and compares line-by-line."""
    fresh = _regen_corpus().strip().splitlines()
    committed = CORPUS.read_text(encoding="utf-8").strip().splitlines()
    assert fresh == committed, (
        "the Python shadow drifted from the Go-canonical corpus (docs/385 §3).\n"
        "Reconcile toward Go: fix the Python decider to match the committed\n"
        f"  {CORPUS.relative_to(REPO)}\n"
        "An INTENDED behavior change is authored Go-first, re-soaked, and only then\n"
        f"does the corpus follow (regen the shadow's view with `python {GEN.relative_to(REPO)}`)."
    )


def test_posttool_corpus_regenerates_and_is_self_consistent():
    """The stream-stateful posttool shadow must likewise still reproduce the
    committed, Go-canonical bytes (docs/385 TP1) — the tripwire on the Python
    posttool decider / tool_stream fold drifting out from under the Go spec."""
    fresh = _regen(GEN_POST).strip().splitlines()
    committed = CORPUS_POST.read_text(encoding="utf-8").strip().splitlines()
    assert fresh == committed, (
        "the Python posttool shadow drifted from the Go-canonical corpus (docs/385 §3).\n"
        "Reconcile toward Go: fix the Python decider to match the committed\n"
        f"  {CORPUS_POST.relative_to(REPO)}\n"
        f"(an intended change is Go-first + re-soaked; regen the shadow with `python {GEN_POST.relative_to(REPO)}`)."
    )


def test_corpus_covers_every_decision_branch():
    """The gate is only as good as its coverage — assert the corpus exercises all
    four decision branches (deny / warn / passthrough) AND the two deny rungs
    (self-modify + disjointness-collision) + the WARN-and-pass branch, so a future
    edit that drops a branch from the corpus is caught."""
    cases = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    tags = {c["decision"] for c in cases}
    assert {"deny", "warn", "passthrough"} <= tags, f"missing decision tags: {tags}"
    names = {c["name"] for c in cases}
    # The load-bearing branches the gate must always cover.
    assert any("selfmodify" in n for n in names), "no self-modify deny case"
    assert any("collision" in n for n in names), "no disjointness-collision deny case"
    assert any("warn" in n for n in names), "no WARN-and-pass case"
    assert any("foreign" in n for n in names), "no foreign-repo (no runtime files) case"


@pytest.mark.skipif(not _have_go(), reason="Go toolchain not installed — cross-engine gate skipped")
def test_go_decider_byte_parity():
    """Run `go test` over the hook decider — the CANONICAL pin (docs/385 TP1):
    `TestParityCorpus` asserts the native Go decider's bytes ARE the committed
    corpus on every case (`go == corpus`), so a Python-shadow drift surfaces here
    as a Go-test failure. Plus the Go unit + pyjson tests."""
    # Ensure the corpora the Go tests will read are the freshly-regenerated ones
    # (both the pretool decision corpus and the stream-stateful posttool corpus).
    # newline="\n": the committed blobs are LF; without it, Windows' text-mode
    # translation rewrites them CRLF and a cold clone's tracked tree turns dirty
    # just from RUNNING the suite (caught by the 2026-06-10 agent-view A/B).
    CORPUS.write_text(_regen_corpus(), encoding="utf-8", newline="\n")
    CORPUS_POST.write_text(_regen(GEN_POST), encoding="utf-8", newline="\n")
    proc = subprocess.run(
        ["go", "test", "./internal/hook/"],
        cwd=str(GO_DIR),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "Go hook decider parity/unit tests FAILED — a decision drift between the "
        "native and Python deciders, or a unit regression:\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
