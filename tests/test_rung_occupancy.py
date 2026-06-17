"""The pure rung-occupancy fold (`dos.rung_occupancy`) — no git, no I/O.

Pins the forgeability classification, the occupancy partition over green verdicts,
and the re-check delta (corroborated / refuted / skipped) that drives `floor_held`.
Verdicts are tiny duck-typed stubs — the fold never imports the oracle.
"""
from __future__ import annotations

from dataclasses import dataclass

from dos import rung_occupancy as ro


@dataclass
class _V:
    """A minimal ShipVerdict stand-in: the four fields the census reads."""

    shipped: bool
    source: str
    plan: str = ""
    phase: str = ""


def test_classify_forgeability_partition():
    assert ro.classify_forgeability(True, "registry") is ro.Forgeability.NONFORGEABLE
    assert ro.classify_forgeability(True, "grep-artifact") is ro.Forgeability.NONFORGEABLE
    assert ro.classify_forgeability(True, "grep-subject") is ro.Forgeability.FORGEABLE
    # bare grep / unknown / empty → UNGRADED (never credited to the floor).
    assert ro.classify_forgeability(True, "grep") is ro.Forgeability.UNGRADED
    assert ro.classify_forgeability(True, "weird") is ro.Forgeability.UNGRADED
    # not green → NONE regardless of source.
    assert ro.classify_forgeability(False, "registry") is ro.Forgeability.NONE


def test_census_partitions_greens_and_rates():
    verdicts = [
        _V(True, "registry"),       # floor
        _V(True, "grep-artifact"),  # floor
        _V(True, "grep-subject", "docs/1", "P1"),   # forgeable
        _V(True, "grep", "docs/2", "P2"),           # ungraded
        _V(False, "none"),          # miss
    ]
    occ = ro.census(verdicts)
    assert occ.n == 5 and occ.green == 4
    assert occ.nonforgeable == 2 and occ.forgeable == 1 and occ.ungraded == 1
    assert occ.green == occ.nonforgeable + occ.forgeable + occ.ungraded
    assert occ.floor_load == 0.5  # 2/4
    assert occ.forgeable_green_rate == 0.25  # 1/4


def test_recheck_corroborated_holds_the_floor():
    verdicts = [_V(True, "grep-subject", "docs/1", "P1")]
    occ = ro.census(verdicts, rechecks={("docs/1", "P1"): True})
    assert occ.recheck_corroborated == 1 and occ.recheck_refuted == 0
    assert occ.floor_held is True


def test_recheck_refuted_breaches_the_floor():
    verdicts = [_V(True, "grep-subject", "docs/1", "P1")]
    occ = ro.census(verdicts, rechecks={("docs/1", "P1"): False})
    assert occ.recheck_refuted == 1
    assert occ.floor_held is False
    assert occ.recheck_refute_rate == 1.0


def test_skipped_recheck_is_not_a_breach():
    # A forgeable green with NO re-check entry is `recheck_skipped`, excluded from the
    # refute denominator — a vacuous hold, disambiguated by the counts.
    verdicts = [_V(True, "grep-subject", "docs/1", "P1")]
    occ = ro.census(verdicts, rechecks={})
    assert occ.recheck_skipped == 1
    assert occ.recheck_corroborated == 0 and occ.recheck_refuted == 0
    assert occ.floor_held is True  # vacuously — nothing was tested
    assert occ.recheck_refute_rate == 0.0  # guarded divide-by-zero


def test_only_forgeable_greens_consult_the_recheck():
    # A non-forgeable green never consults the re-check map even if a key exists.
    verdicts = [_V(True, "registry", "docs/1", "P1")]
    occ = ro.census(verdicts, rechecks={("docs/1", "P1"): False})
    assert occ.recheck_refuted == 0 and occ.recheck_skipped == 0
    assert occ.floor_held is True


def test_empty_is_vacuously_held():
    occ = ro.census([])
    assert occ.n == 0 and occ.green == 0
    assert occ.floor_held is True
    assert occ.to_dict()["floor_held"] is True


def test_to_dict_shape():
    occ = ro.census([_V(True, "grep-subject", "d", "p")],
                    rechecks={("d", "p"): False})
    d = occ.to_dict()
    assert d["occupancy"]["forgeable"] == 1
    assert d["recheck"]["refuted"] == 1
    assert d["floor_held"] is False
    assert "floor_load" in d["rates"]
