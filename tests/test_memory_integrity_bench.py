"""docs/316 P1 — pin the memory-integrity benchmark's corpus expectations.

One candidate per taxonomy class runs through the REAL write gate
(`admit_text`) against the corpus's constructed repo, and the T0→T1 handoff
(admitted-true-then-falsified → RECALL_STALE) runs through the real sweep.
If the gate or the corpus drifts, this fails before RESULTS.md can lie.

The benchmark package is loaded by file path (testpaths = tests only;
`benchmark/` is not importable from the suite's sys.path by default).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from dos.config import default_config
from dos.drivers.memory_recall import admit_text, sweep

_CORPUS_PY = Path(__file__).resolve().parents[1] / "benchmark" / "memory_integrity" / "corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location("membench_corpus", _CORPUS_PY)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations via sys.modules[cls.__module__]
    # — a spec-loaded module must be registered there BEFORE exec.
    sys.modules["membench_corpus"] = mod
    spec.loader.exec_module(mod)
    return mod


def _by_id(corpus, facts):
    return {c.id: c for c in corpus.candidates(facts)}


def test_write_gate_one_candidate_per_class(tmp_path: Path):
    corpus = _load_corpus()
    facts = corpus.build_t0(tmp_path / "repo")
    cands = _by_id(corpus, facts)
    cfg = default_config(facts.root)
    for cid in ("fresh1",            # true+bindable → WITNESSED
                "mixed1",            # true, partly probeable → AS_CLAIM
                "poison1",           # in-grammar lie → REJECT_POISON
                "poison3",           # orphaned-SHA ship claim → REJECT_POISON
                "poison_sha_unknown",  # unknown-SHA lie → contained AS_CLAIM
                "evasive1",          # prose-shaped lie → the OPINION ceiling
                "directive1",        # instruction wearing a memory → OPINION + directive marker (§4, #110)
                "opinion_fb"):       # honest preference → OPINION, NOT directive-marked
        c = cands[cid]
        v = admit_text(c.text, name=c.id, cfg=cfg)
        assert v.admission.value == c.expected_admit, (cid, v.admission.value, v.reason)
        # docs/316 §4, #110 — the directive marker is ORTHOGONAL to the admit rung:
        # the injection-shape candidate types the marker, the honest preference does not.
        assert v.directive == c.expected_directive, (cid, v.directive, v.reason)


def test_t0_to_t1_handoff_aged_stale_caught_fresh_survives(tmp_path: Path):
    corpus = _load_corpus()
    facts = corpus.build_t0(tmp_path / "repo")
    cands = _by_id(corpus, facts)
    store = tmp_path / "store"
    store.mkdir()
    for cid in ("aging1", "fresh1"):
        (store / f"{cid}.md").write_text(cands[cid].text, encoding="utf-8")

    cfg = default_config(facts.root)
    # Both are TRUE at T0 — the write gate must admit both with fact authority.
    for cid in ("aging1", "fresh1"):
        assert admit_text(cands[cid].text, name=cid, cfg=cfg).admission.value == "ADMIT_WITNESSED"

    corpus.mutate_to_t1(facts)
    verdicts = {v.evidence.mem_name: v.verdict.value
                for v in sweep(cfg=default_config(facts.root), store=str(store))}
    assert verdicts["aging1"] == "RECALL_STALE"   # caught at recall, with evidence
    assert verdicts["fresh1"] == "RECALL_FRESH"   # still-true memory not harassed
