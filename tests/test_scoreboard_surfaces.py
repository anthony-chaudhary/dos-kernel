"""Tests for the scoreboard consumption surfaces (`scripts/scoreboard_surfaces.py`,
docs/312, issue #85).

Dev tooling, not a kernel module — imported by path like the
`drift_scoreboard.py` suite. What is pinned, and why:

  * **the `verdict.json` key roster IS the schema** — `dos-scoreboard-verdict/v1`
    is a version contract; a key change without a version bump is the drift
    these tests exist to catch;
  * **the badge's closed mapping** (clean / drift / empty denominator) and the
    shields.io `schemaVersion: 1` envelope;
  * **badge↔verdict consistency** — the badge is a pure projection of the
    verdict; the counts in its message are the verdict's counts, never a
    second computation;
  * **the traversal guard** — the repo string becomes an output path, so the
    `<org>/<repo>` form (and the `.`/`..` refusals) is load-bearing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Import the script-under-test by path (it is not an installed package).
_HELPER_PATH = (Path(__file__).resolve().parent.parent
                / "scripts" / "scoreboard_surfaces.py")
_spec = importlib.util.spec_from_file_location("scoreboard_surfaces", _HELPER_PATH)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


SUMMARY = {
    "commits": 10,
    "checkable": 6,
    "abstained": 4,
    "witnessed": 5,
    "unwitnessed": 1,
    "drift_rate": 1 / 6,
    "by_kind": {
        "code-effect": {"unwitnessed": 1, "witnessed": 3, "abstain": 1},
        "test": {"unwitnessed": 0, "witnessed": 2, "abstain": 3},
    },
    "unwitnessed_shas": ["deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"],
}


def _verdict(summary=None, **over):
    kw = dict(repo="acme/widgets", generated="2026-06-12",
              grader_version="0.24.1", range_described="full visible history",
              head_sha="cafebabe")
    kw.update(over)
    return ss.verdict_payload(summary if summary is not None else SUMMARY, **kw)


# ---------------------------------------------------------------------------
# verdict.json — the v1 schema pin.
# ---------------------------------------------------------------------------


def test_verdict_key_roster_is_the_v1_schema():
    v = _verdict()
    assert v["schema"] == "dos-scoreboard-verdict/v1"
    assert set(v) == {"schema", "repo", "generated", "grader", "methodology",
                      "opt_in", "range", "claims", "by_kind", "receipts",
                      "advisory"}
    assert set(v["grader"]) == {"tool", "version"}
    assert set(v["range"]) == {"described", "head_sha", "commits_audited"}
    assert set(v["claims"]) == {"checkable", "witnessed", "unwitnessed",
                                "abstained", "drift_rate"}
    assert set(v["receipts"]) == {"unwitnessed_shas"}


def test_verdict_copies_the_fold_and_the_receipts():
    v = _verdict()
    assert v["claims"] == {"checkable": 6, "witnessed": 5, "unwitnessed": 1,
                           "abstained": 4, "drift_rate": 1 / 6}
    assert v["range"]["commits_audited"] == 10
    assert v["range"]["head_sha"] == "cafebabe"
    assert v["by_kind"] == SUMMARY["by_kind"]
    assert v["receipts"]["unwitnessed_shas"] == SUMMARY["unwitnessed_shas"]
    assert v["opt_in"] is True
    assert v["grader"] == {"tool": "dos-kernel commit-audit --sweep",
                           "version": "0.24.1"}
    assert v["methodology"].startswith("https://")
    assert "never a correctness or malice grade" in v["advisory"]


def test_verdict_accepts_the_per_repo_wrapper_shape():
    wrapper = {"repo": "ignored", "commits_scanned": 99,
               "attributed_commits": 12, "markers": {"claude": 12},
               "summary": SUMMARY}
    assert _verdict(wrapper)["claims"] == _verdict()["claims"]


@pytest.mark.parametrize("bad", [
    "", "acme", "acme/widgets/extra", "acme\\widgets",
    "../widgets", "acme/..", "./widgets", "acme/.",
    "acme/wid gets", "acme/wid;gets",
])
def test_verdict_refuses_a_malformed_repo(bad):
    with pytest.raises(ValueError):
        _verdict(repo=bad)


# ---------------------------------------------------------------------------
# badge.json — the shields endpoint envelope + the closed mapping.
# ---------------------------------------------------------------------------


def test_badge_envelope_is_the_shields_contract():
    b = ss.badge_payload(_verdict())
    assert set(b) == {"schemaVersion", "label", "message", "color"}
    assert b["schemaVersion"] == 1
    assert b["label"] == "commit-claims"


def test_badge_clean_row():
    clean = dict(SUMMARY, unwitnessed=0, witnessed=6, drift_rate=0.0,
                 unwitnessed_shas=[])
    b = ss.badge_payload(_verdict(clean))
    assert b["message"] == "audited clean (as of 2026-06-12)"
    assert b["color"] == "brightgreen"


def test_badge_drift_row_carries_the_verdicts_counts():
    b = ss.badge_payload(_verdict())
    assert b["message"] == "1 unwitnessed of 6 (as of 2026-06-12)"
    assert b["color"] == "orange"


def test_badge_empty_denominator_row():
    empty = dict(SUMMARY, checkable=0, witnessed=0, unwitnessed=0,
                 drift_rate=0.0, unwitnessed_shas=[])
    b = ss.badge_payload(_verdict(empty))
    assert b["message"] == "no checkable claims (as of 2026-06-12)"
    assert b["color"] == "lightgrey"


def test_badge_is_a_pure_projection_of_the_verdict():
    """Same verdict in → same badge out; the verdict object is not mutated."""
    v = _verdict()
    before = repr(v)
    assert ss.badge_payload(v) == ss.badge_payload(v)
    assert repr(v) == before

