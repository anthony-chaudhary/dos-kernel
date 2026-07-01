"""docs/124 §3 Phase 1 / docs/385 TP2 — the Python SHADOW side of the liveness parity pin.

The liveness classifier is the docs/124 §3 Phase-1 port: the cleanest decider (ints +
enums, zero of the three cross-language hazard classes — docs/124 §1.4 / A.4), chosen to
prove the TP2 parity harness on the safest surface. Its Go twin lives in
``go/internal/liveness/liveness.go`` and is pinned to the committed corpus
``go/internal/liveness/parity/corpus.jsonl`` by the Go ``TestParityCorpus``.

THIS test is the twin assertion on the Python side: the real ``dos.liveness.classify``
still reproduces that same corpus, case-for-case. A drift between the two engines
surfaces on BOTH sides. During the PORT phase (Python still canonical, docs/385 §3) the
corpus is regenerated FROM Python, so this proves the committed bytes are faithful to
the live classifier; after a later FLIP it becomes the shadow pin (python == the
Go-canonical corpus). Either way, the corpus never loses its differential guarantee.

This is the liveness twin of ``test_go_account_parity.py`` and ``test_go_hook_parity.py``.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_GEN = REPO / "go" / "internal" / "liveness" / "parity" / "gen_corpus.py"
_CORPUS = REPO / "go" / "internal" / "liveness" / "parity" / "corpus.jsonl"


def _load_generator():
    spec = importlib.util.spec_from_file_location("lvn_gen_corpus", _GEN)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_python_classifier_reproduces_the_committed_corpus():
    gen = _load_generator()
    regenerated = [gen._emit_case(*c) for c in gen.CASES]
    committed = [json.loads(line) for line in
                 _CORPUS.read_text(encoding="utf-8").strip().splitlines()]
    assert len(regenerated) == len(committed), "case count drifted from the committed corpus"
    for got, want in zip(regenerated, committed):
        # Compare PARSED objects: the live Python verdict (verdict + reason + echoed
        # evidence) must equal the committed corpus for every case.
        assert got == want, f"Python drifted from the corpus on case {want.get('name')!r}"


def test_corpus_covers_every_verdict_and_is_nonempty():
    """A liveness corpus that lost its SPINNING or STALLED cases would still pass the
    byte-equality above while silently under-covering the ladder — pin the coverage."""
    committed = [json.loads(line) for line in
                 _CORPUS.read_text(encoding="utf-8").strip().splitlines()]
    assert committed, "corpus is empty"
    verdicts = {c["expect"]["verdict"] for c in committed}
    assert verdicts == {"ADVANCING", "SPINNING", "STALLED"}, verdicts
