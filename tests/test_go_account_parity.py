"""docs/386 §6 / docs/385 — the Python SHADOW side of the account-ranking parity pin.

After the FLIP (``native_canonical``: ``account_pick`` / ``serving_pool`` /
``allocate_seats`` are Go-canonical), the committed corpus
``go/internal/account/parity/corpus.jsonl`` is the Go-canonical pin. The Go
``TestParityCorpus`` asserts ``go == corpus``; THIS test asserts the Python switcher
still reproduces that same corpus (``python == corpus``) — so a drift between the two
engines surfaces on BOTH sides and is reconciled toward the Go-canonical corpus, never
the reverse (the docs/385 §3 ratchet, post-flip). It re-runs the same hermetic case
builder the generator uses (temp config dirs + an injected probe, driving the REAL
``account_switcher``) and compares to the committed corpus, case-for-case.

This is the account-core twin of ``test_go_hook_parity.py`` for the hook cluster.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_GEN = REPO / "go" / "internal" / "account" / "parity" / "gen_corpus.py"
_CORPUS = REPO / "go" / "internal" / "account" / "parity" / "corpus.jsonl"


def _load_generator():
    spec = importlib.util.spec_from_file_location("acct_gen_corpus", _GEN)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_python_switcher_reproduces_the_committed_corpus():
    gen = _load_generator()
    regenerated = [gen._emit_case(name, ncu, facts) for name, ncu, facts in gen.CASES]
    committed = [json.loads(line) for line in
                 _CORPUS.read_text(encoding="utf-8").strip().splitlines()]
    assert len(regenerated) == len(committed), "case count drifted from the committed corpus"
    for got, want in zip(regenerated, committed):
        # Compare PARSED objects: the Python switcher's live decision must equal the
        # committed (Go-canonical) corpus for every state / pick / pool / allocate / spread.
        assert got == want, f"Python drifted from the corpus on case {want.get('name')!r}"
