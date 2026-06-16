"""Pin the `dos market` prototype (`scripts/dos_market.py`) — the trust-verified listing.

The marketplace's whole point is that a listing is WITNESSED, not described (docs/368). These
tests pin the three witness rungs and the typed admission gate:

  - discover reads the package's OWN entry-point metadata (the kernel package registers
    real `dos.*` seam occupants), never a description;
  - conform runs the judge occupants against the fail-to-abstain invariant — a judge that
    AGREEs an evidence-free claim is a conformance FAIL;
  - the gate REFUSES with a typed reason (MARKET_NOT_A_PLUGIN / MARKET_CONFORMANCE_FAIL /
    PROVENANCE_BELOW_FLOOR) from a closed vocabulary, never free text;
  - the file backend is unshadowable and round-trips a listing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dos_market.py"


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("dos_market", _HELPER_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register before exec: dataclass type-resolution looks the module up in sys.modules.
    sys.modules["dos_market"] = mod
    spec.loader.exec_module(mod)
    return mod


_M = _load()


# --- discover: reads packaging metadata, not descriptions -------------------

def test_discover_reads_real_seam_occupants_of_the_kernel_package():
    """The installed dos-kernel registers occupants under real dos.* seams — discover
    finds them from entry-point metadata, with their true module:object targets."""
    occ = _M.discover("dos-kernel")
    groups = {o.group for o in occ}
    # The kernel ships judges, exporters, notifiers, hook dialects, ... — at minimum judges.
    assert "dos.judges" in groups, f"expected dos.judges among discovered seams, got {groups}"
    judges = [o for o in occ if o.group == "dos.judges"]
    assert all(":" in j.target for j in judges), "targets must be real module:object refs"
    assert all(o.is_dos_seam for o in occ), "discover yields only DOS-seam occupants"


def test_discover_of_a_non_plugin_is_empty():
    """A package that registers no dos.* entry point is honestly 'not a DOS plugin'."""
    assert _M.discover("pytest") == []


# --- conform: the judge fail-to-abstain invariant is WITNESSED --------------

def test_real_judges_pass_fail_to_abstain_conformance():
    """Every built-in judge occupant ABSTAINs (or disagrees) on an evidence-free claim —
    none AGREEs. A pass here is a witnessed verdict, not a self-applied badge."""
    occ = [o for o in _M.discover("dos-kernel") if o.group == "dos.judges"]
    results = _M.conform(occ)
    assert results, "expected judge occupants to conform-check"
    for r in results:
        assert r.checked, f"{r.name} should be a checked seam"
        assert r.passed, f"judge {r.name} failed fail-to-abstain: {r.detail}"


def test_an_auto_agree_judge_fails_conformance(monkeypatch):
    """A fabricated judge that AGREEs an evidence-free claim is a real conformance FAIL —
    this is the false-clear the seam exists to make hard, and the harness catches it."""
    from dos.judges import JudgeVerdict

    class _AlwaysAgree:
        name = "rogue"

        def rule(self, claim, config):
            return JudgeVerdict.agree("trust me")  # the forbidden move

    ok, detail = _M._conform_judge(_AlwaysAgree())
    assert ok is False
    assert "fail-to-abstain" in detail.lower() or "false-clear" in detail.lower()


def test_uncovered_seam_is_unverified_not_a_silent_pass():
    """A seam with no probe yet yields checked=False — an honest 'unverified', never a
    fabricated clear. (dos.exporters has no probe in the prototype.)"""
    occ = [o for o in _M.discover("dos-kernel") if o.group == "dos.exporters"]
    if not occ:  # defensive: the kernel does ship exporters, but stay robust
        return
    results = _M.conform(occ)
    for r in results:
        assert r.checked is False
        assert r.passed is False  # unverified is NOT passed


# --- provenance: diff-witnessed ratio over a real git tree ------------------

def test_provenance_of_this_repo_is_diff_witnessed():
    """The kernel repo's recent history is diff-witnessed (commits touch files). The card
    is reachable and the ratio is in [0, 1]."""
    card = _M.provenance(Path(__file__).resolve().parents[1])
    assert card.reachable
    assert card.commits > 0
    assert 0.0 <= card.ratio <= 1.0


def test_provenance_of_a_non_git_dir_is_fail_safe(tmp_path):
    """A non-git directory yields an unreachable, 0-ratio card — never a fabricated clean
    provenance (fail-safe, the witness posture)."""
    card = _M.provenance(tmp_path)
    assert card.reachable is False
    assert card.ratio == 0.0


# --- the gate: typed refusals, not free text --------------------------------

def test_non_plugin_is_refused_with_a_typed_reason():
    occ_repo = Path(__file__).resolve().parents[1]
    listing = _M.build_listing("pytest", occ_repo, _M.SubmitPolicy())
    assert listing.admitted is False
    assert listing.reason == "MARKET_NOT_A_PLUGIN"


def test_kernel_package_is_admitted_by_the_public_policy():
    occ_repo = Path(__file__).resolve().parents[1]
    listing = _M.build_listing("dos-kernel", occ_repo, _M.SubmitPolicy(min_provenance_ratio=0.0))
    assert listing.admitted is True
    assert listing.reason == ""
    # the listing carries the witnessed facts, not a description
    assert listing.occupants
    assert any(c["passed"] for c in listing.conformance)


def test_provenance_floor_bites_below_threshold(tmp_path):
    """A repo below the private provenance floor is refused PROVENANCE_BELOW_FLOOR even if
    it has a conforming occupant — the private/internal gate."""
    # tmp_path is not a git repo → ratio 0.0; a floor of 0.5 must refuse.
    listing = _M.build_listing(
        "dos-kernel", tmp_path,
        _M.SubmitPolicy(require_conformance=True, min_provenance_ratio=0.5))
    assert listing.admitted is False
    assert listing.reason == "PROVENANCE_BELOW_FLOOR"


# --- the index backend seam: file built-in, unshadowable, round-trips -------

def test_file_backend_round_trips_a_listing(tmp_path):
    idx = tmp_path / "index.jsonl"
    be = _M.resolve_backend("file", index_path=idx)
    listing = _M.Listing(package="acme", admitted=True)
    be.put(listing)
    rows = be.listings()
    assert len(rows) == 1
    assert rows[0]["package"] == "acme"
    assert rows[0]["admitted"] is True


def test_unknown_backend_fails_loud(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="unknown market backend"):
        _M.resolve_backend("nope", index_path=tmp_path / "i.jsonl")
