"""Tests for the legalcite LIVE arm — the scaled, real-corpus runner (docs/279 §2).

The frozen harness proved the *mechanism* over n=18 with no network. `live_corpus.py`
turns that into a *measured-at-scale* run against the live CourtListener reporter. An
agent cannot run the live arm here (no token), so these tests exercise the entire
runner OFFLINE through the **recorded transport** — the same code path, over committed
reporter bytes, zero network. They pin the properties that make the live number
trustworthy:

  * PARITY — the live code path reproduces the frozen harness's verdicts on the same
    bytes (a fabrication is UNRESOLVED, a real cite is not flagged, the collision is
    caught via the name guard). The live path is not a different classifier.
  * THREE-WAY accounting — ABSTAIN (no corpus access) is its own column, EXCLUDED from
    both rate denominators, never a silent pass and never a flag. This is the property
    a careless harness would get wrong (folding abstains into "passed").
  * EXIT GATE — the false-fire floor breaches only on a REACHABLE real-cite flag; an
    all-abstain run is "could not tell" (exit 0, surfaced), never a false green/red.
  * CHECKPOINT / resume — an interrupted run resumes from the checkpoint without
    re-querying done cites (the transport is asserted untouched on resumed cites).
  * SCALE — the generated synth fabrications flag at scale (the n=18 → n=hundreds jump
    is real detect, not padding).

Token-free, network-free, deterministic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LEGALCITE = _REPO / "benchmark" / "legalcite"
# The runner imports a sibling `corpus` by directory; mirror its path setup here.
sys.path.insert(0, str(_LEGALCITE))

from dos.drivers.citation_resolve import Citation  # noqa: E402

import corpus  # noqa: E402  (benchmark/legalcite on sys.path)
from benchmark.legalcite import live_corpus  # noqa: E402


# ---------------------------------------------------------------------------
# corpus.py — the scaled labeled set
# ---------------------------------------------------------------------------

def test_corpus_scales_beyond_the_frozen_seed():
    s = corpus.summary()
    # The n=18 → hundreds jump is the whole point of the live arm.
    assert s["n_real"] >= 40
    assert s["n_fabricated_total"] >= 100
    # The documented Mata fabrications are preserved verbatim (un-forgeable provenance).
    assert s["n_documented"] >= 4
    assert s["n_synth"] >= 90


def test_corpus_never_labels_a_real_cite_fabricated():
    real = {lc.cite for lc in corpus.real_cites()}
    fab = {lc.cite for lc in corpus.fabricated_cites()}
    # A generated perturbation that collided with a real cite must have been dropped.
    assert real.isdisjoint(fab)


def test_synth_perturbations_keep_the_claimed_name():
    # A synth fabrication is a real case's NUMBER moved — the name is still claimed, so
    # the verdict is "this case does not appear at the claimed cite", auditable.
    synth = [lc for lc in corpus.fabricated_cites() if lc.note.startswith("synth")]
    assert synth and all(lc.claimed_name for lc in synth)


# ---------------------------------------------------------------------------
# The recorded transport + run() — the offline proof of the live path
# ---------------------------------------------------------------------------

def _recorded_run(n_synth=None):
    transport = live_corpus.recorded_transport()
    return live_corpus.run(corpus.full_corpus(n_synth=n_synth), transport, min_interval=0.0)


def test_recorded_run_holds_the_false_fire_floor_at_scale():
    r = _recorded_run()
    # FALSE-FIRE over REACHABLE real cites is 0 — the docs/277 §6 prediction, at scale.
    assert r["false_fire_rate"] == 0.0
    assert r["false_fires"] == 0
    # DETECT recall over REACHABLE fabrications is 100% (every documented + synth flagged).
    assert r["detect_recall"] == 1.0
    assert r["detected"] == r["n_fabricated_reachable"]
    # The exit gate agrees.
    assert live_corpus._floor_breached(r) is False


def test_parity_with_frozen_on_the_documented_mata_set():
    # The live code path must produce the SAME verdicts the frozen harness does on the
    # same bytes — it is not a different classifier.
    r = _recorded_run()
    rows = {row["cite"]: row for row in r["rows"]}
    # Pure-invention Mata cite → UNRESOLVED (no reporter carries it).
    assert rows["925 F.3d 1339"]["verdict"] == Citation.UNRESOLVED.value
    assert rows["925 F.3d 1339"]["flagged"] is True
    # A real landmark cite with recorded bytes → not flagged.
    assert rows["576 U.S. 644"]["flagged"] is False
    assert rows["576 U.S. 644"]["abstained"] is False


def test_collision_caught_through_the_live_path():
    # 92 F.3d 1074 is a REAL reporter slot that resolves to Grilli, not the claimed
    # Hyatt — the docs/279 §3 name-guard collision. It must be UNRESOLVED (flagged),
    # never rubber-stamped because the slot exists.
    r = _recorded_run()
    rows = {row["cite"]: row for row in r["rows"]}
    coll = rows["92 F.3d 1074"]
    assert coll["verdict"] == Citation.UNRESOLVED.value
    assert coll["flagged"] is True
    assert "DIFFERENT" in coll["why"] or "Grilli" in coll["why"]


def test_abstain_is_its_own_column_excluded_from_both_rates():
    # The extra landmark cites have no recorded bytes offline → ABSTAIN, and they must
    # NOT be counted in either denominator (an abstain is "could not tell").
    r = _recorded_run()
    assert r["real_abstained"] > 0
    # Reachable real = total real - abstained real; the false-fire denominator is the
    # reachable count, not the total.
    assert r["n_real_reachable"] == r["n_real"] - r["real_abstained"]
    # An abstained row is neither flagged nor a pass — it is simply excluded.
    abstained_rows = [row for row in r["rows"] if row["abstained"]]
    assert abstained_rows and all(not row["flagged"] for row in abstained_rows)


def test_explicit_abstain_and_mismatch_demonstrators():
    # The two synthetic demonstrator keys pin the ABSTAIN and RESOLVED_MISMATCH columns
    # directly (they are not in the corpus, so call the transport by key).
    transport = live_corpus.recorded_transport()
    v_abstain = transport("__ABSTAIN_DEMO__", "Whoever v. Whomever", "", "real")
    assert v_abstain.verdict == Citation.ABSTAIN
    # A real cluster whose claimed quote is absent from the FULL opinion → MISMATCH.
    v_mismatch = transport(
        "__MISMATCH_DEMO__", "Obergefell v. Hodges",
        "this exact sentence is nowhere in the recorded opinion text at all", "real")
    assert v_mismatch.verdict == Citation.RESOLVED_MISMATCH


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------

def test_checkpoint_resume_skips_done_cites(tmp_path):
    ckpt = tmp_path / "ck.json"
    small = corpus.full_corpus(n_synth=4)

    # First pass over the FIRST half only — simulate an interruption by running a slice.
    half = small[: len(small) // 2]
    live_corpus.run(half, live_corpus.recorded_transport(), checkpoint=ckpt)
    assert ckpt.exists()
    first_rows = json.loads(ckpt.read_text(encoding="utf-8"))["rows"]
    done_cites = {r["cite"] for r in first_rows}
    assert done_cites

    # Resume over the FULL corpus with a transport that REFUSES to serve an
    # already-done cite — proving resume does not re-query them.
    served: list[str] = []
    base = live_corpus.recorded_transport()

    def _counting_transport(cite, name, quote, label):
        served.append(cite)
        return base(cite, name, quote, label)

    r = live_corpus.run(small, _counting_transport, checkpoint=ckpt, resume=True)
    # No already-done cite was re-served.
    assert done_cites.isdisjoint(set(served))
    # The final result covers the whole corpus.
    assert len(r["rows"]) == len(small)


def test_pacing_is_skipped_at_zero_interval(monkeypatch):
    # min_interval=0 (the recorded/test path) must never call time.sleep.
    calls: list[float] = []
    monkeypatch.setattr(live_corpus.time, "sleep", lambda s: calls.append(s))
    _recorded_run(n_synth=2)
    assert calls == []


def test_pacing_sleeps_when_interval_positive(monkeypatch):
    # With a positive interval and an instantaneous transport, the pacer must sleep
    # between calls (the rate-limit guard) — using a mocked clock, no real wait.
    slept: list[float] = []
    monkeypatch.setattr(live_corpus.time, "sleep", lambda s: slept.append(s))
    # A monotonic clock that does not advance, so each call is "too soon" → sleep.
    monkeypatch.setattr(live_corpus.time, "monotonic", lambda: 0.0)
    live_corpus.run(corpus.full_corpus(n_synth=3), live_corpus.recorded_transport(),
                    min_interval=5.0)
    # At least one sleep of ~5s was requested (the first call sets the baseline).
    assert any(s > 0 for s in slept)


# ---------------------------------------------------------------------------
# The no-token live transport is honest (all-abstain, not a crash, not a false pass)
# ---------------------------------------------------------------------------

def test_live_transport_with_no_token_abstains_not_fabricates(monkeypatch):
    # Poison the network so the (token-less) live transport cannot reach the corpus;
    # it must ABSTAIN on every cite, never fabricate a RESOLVED or raise.
    import dos.drivers.citation_resolve as drv

    def _boom(*a, **k):
        raise OSError("network poisoned for the test")

    monkeypatch.setattr(drv.urllib.request, "urlopen", _boom)
    transport = live_corpus.live_transport(token="")  # no token + dead network
    v = transport("925 F.3d 1339", "Varghese v. China Southern", "", "fabricated")
    assert v.verdict == Citation.ABSTAIN
