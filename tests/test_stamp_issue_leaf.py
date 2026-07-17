"""fak #3132 — split-paren issue+leaf ship stamps: `(#<issue>) … (<ns> <leaf>)`.

The this-family shape (`fix(pythongate): add skill-slop scorecard paths to the
de-Python baseline (#2911) (fak tools)`) splits the unit of work across TWO
parens — a `(#<issue>)` cite and a `(<ns> <leaf>)` named trailer — and the loop
verifies it as `dos verify tools 2911` (plan = the trailer's LEAF token, phase =
the all-digits issue number). No prior rung binds that split, so every witnessed
ship reconciled QUIET_INCOMPLETE and `dos doctor` reported `recognized: 0`. The
fix is a per-convention OPT-IN (`[stamp] issue_leaf_stamp = true`); these tests
pin:

  * the flag is data (defaults off everywhere; TOML-declared; dict round-trip
    across the grep-rung subprocess boundary);
  * the Pass 1a″ matcher binds exactly the split-paren shape — leaf==plan
    (case-insensitive) AND its own `(#<phase>)` paren with an all-digits phase —
    and refuses everything else (key off, non-digit phase, wrong leaf, issue
    prefix collisions, cite-inside-the-trailer, plain conventional commits);
  * the bookkeeping / snapshot / release guards still exclude (FQ-77 posture);
  * the probes (`recognizes_direct_ship` / `ship_shaped_under_generic`) and the
    doctor `recognized` count see the shape when the key is declared;
  * the end-to-end path (library `is_shipped(cfg=…)` and the real `dos verify`
    CLI over a `dos.toml`-declared workspace) flips to SHIPPED, graded
    `grep-subject` with the distinct `issue-leaf` rung (Go parity: non-direct,
    so the Go port abstains).
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dos import phase_shipped as ps
from dos.stamp import (
    GENERIC_STAMP_CONVENTION,
    JOB_STAMP_CONVENTION,
    StampConvention,
    convention_from_table,
    ship_shaped_under_generic,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
ISSUE_LEAF_ON = StampConvention(style="grep", issue_leaf_stamp=True)

# The real-world line that motivated the feature (fak #3132), verbatim shape.
FAK_LINE = (
    "abc1234 fix(pythongate): add skill-slop scorecard paths to the "
    "de-Python baseline (#2911) (fak tools)"
)
FAK_SUBJECT = FAK_LINE.split(" ", 1)[1]


def _check(series: str, phase: str, lines: list[str], conv: StampConvention) -> dict:
    """Run the oneline scan with an explicit convention (no git, no I/O)."""
    matchers = ps._subject_matchers(SimpleNamespace(stamp=conv))
    return ps._check_phase_with_cache(series, phase, lines, [], matchers)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _repo(repo: Path, *commits: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init: empty repo")
    for c in commits:
        _git(repo, "commit", "--allow-empty", "-m", c)


def _cli(repo: Path, *argv: str):
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# the flag is data: defaults, TOML, dict round-trip
# ---------------------------------------------------------------------------
def test_flag_defaults_off_everywhere():
    """Opt-in means opt-in: neither shipped default recognizes the shape."""
    assert JOB_STAMP_CONVENTION.issue_leaf_stamp is False
    assert GENERIC_STAMP_CONVENTION.issue_leaf_stamp is False
    assert GENERIC_STAMP_CONVENTION.recognizes_direct_ship(FAK_SUBJECT) is False


def test_from_table_parses_issue_leaf_stamp():
    conv = convention_from_table(
        {"issue_leaf_stamp": True}, base=GENERIC_STAMP_CONVENTION
    )
    assert conv.issue_leaf_stamp is True
    # Omitted → inherits the base (off).
    conv = convention_from_table({"style": "grep"}, base=GENERIC_STAMP_CONVENTION)
    assert conv.issue_leaf_stamp is False


def test_from_table_rejects_non_bool_issue_leaf_stamp():
    with pytest.raises(ValueError, match="issue_leaf_stamp must be a boolean"):
        convention_from_table(
            {"issue_leaf_stamp": "yes"}, base=GENERIC_STAMP_CONVENTION
        )


def test_dict_round_trip_carries_the_flag():
    """The convention crosses the grep-rung subprocess boundary via
    to_dict/from_dict (ENV_STAMP_CONVENTION) — the flag must survive, and an
    OLD payload without the key must default off (forward compatibility)."""
    assert StampConvention.from_dict(ISSUE_LEAF_ON.to_dict()) == ISSUE_LEAF_ON
    legacy = ISSUE_LEAF_ON.to_dict()
    legacy.pop("issue_leaf_stamp")
    assert StampConvention.from_dict(legacy).issue_leaf_stamp is False


# ---------------------------------------------------------------------------
# the Pass 1a″ matcher (synthetic, no git)
# ---------------------------------------------------------------------------
def test_issue_leaf_ships_the_fak_shape():
    r = _check("tools", "2911", [FAK_LINE], ISSUE_LEAF_ON)
    assert r["shipped"] is True
    assert r["sha"] == "abc1234"
    assert r["via"] == "issue-leaf"


def test_issue_leaf_plan_match_is_case_insensitive():
    r = _check("TOOLS", "2911", [FAK_LINE], ISSUE_LEAF_ON)
    assert r["shipped"] is True and r["via"] == "issue-leaf"


def test_issue_leaf_requires_opt_in():
    """The same line under the plain generic convention stays `via none`."""
    r = _check("tools", "2911", [FAK_LINE], GENERIC_STAMP_CONVENTION)
    assert r["shipped"] is False


def test_non_digit_phase_never_binds():
    """The rung is issue-number-keyed: a named/mixed phase query must not
    ride it, even against the exact same subject."""
    assert _check("tools", "P2911", [FAK_LINE], ISSUE_LEAF_ON)["shipped"] is False
    assert _check("tools", "tools", [FAK_LINE], ISSUE_LEAF_ON)["shipped"] is False


def test_wrong_leaf_or_wrong_issue_never_binds():
    assert _check("gateway", "2911", [FAK_LINE], ISSUE_LEAF_ON)["shipped"] is False
    assert _check("tools", "9999", [FAK_LINE], ISSUE_LEAF_ON)["shipped"] is False
    # Digit-boundary: the cite `(#2911)` is not a ship of issue 291.
    assert _check("tools", "291", [FAK_LINE], ISSUE_LEAF_ON)["shipped"] is False


def test_cite_must_be_its_own_paren():
    """`(fak tools #2911)` folds the cite INTO the trailer — that is the
    trailer rung's admitted tail, not the split-paren shape this rung binds."""
    folded = "abc1234 fix(x): repair the gate (fak tools #2911)"
    assert _check("tools", "2911", [folded], ISSUE_LEAF_ON)["shipped"] is False


def test_unrelated_conventional_commit_never_binds():
    assert _check("tools", "2911", ["abc1234 chore: refactor"], ISSUE_LEAF_ON)[
        "shipped"
    ] is False
    assert _check(
        "tools", "2911", ["abc1234 fix(tools): typo sweep"], ISSUE_LEAF_ON
    )["shipped"] is False


def test_issue_leaf_excluded_on_bookkeeping_and_release_subjects():
    """FQ-77 posture: a NAMES-but-doesn't-ship subject never ships, and a
    `vX.Y.Z:` version cut stays off this rung (falls through to the weak,
    footprint-guarded release pass)."""
    snap = "abc1234 working-dir snapshot: bulk sweep (#2911) (fak tools)"
    assert _check("tools", "2911", [snap], ISSUE_LEAF_ON)["shipped"] is False
    rel = "abc1234 v0.21.0: bundled cut (#2911) (fak tools)"
    assert _check("tools", "2911", [rel], ISSUE_LEAF_ON)["via"] != "issue-leaf"


# ---------------------------------------------------------------------------
# the probes: recognizes_direct_ship / ship_shaped_under_generic
# ---------------------------------------------------------------------------
def test_recognizes_fak_shape_iff_opted_in():
    assert ISSUE_LEAF_ON.recognizes_direct_ship(FAK_SUBJECT) is True
    assert GENERIC_STAMP_CONVENTION.recognizes_direct_ship(FAK_SUBJECT) is False
    # Neither operand alone is a ship under the probe: a bare cite is a
    # reference, a bare named trailer is the (recurrence-guarded) other surface.
    assert ISSUE_LEAF_ON.recognizes_direct_ship(
        "fix(x): repair the gate (#2911)"
    ) is False
    assert ISSUE_LEAF_ON.recognizes_direct_ship(
        "fix(x): repair the gate (fak tools)"
    ) is False


def test_ship_shaped_breadth_sees_the_fak_shape():
    """The breadth predicate's contract is "would SOME convention recognize
    it?" — the split-paren shape is ship-shaped even though no DEFAULT
    convention recognizes it; plain conventional commits stay non-ship-shaped
    (the 'never cries wolf' pins in test_stamp_doctor must keep holding)."""
    assert ship_shaped_under_generic(FAK_SUBJECT) is True
    assert ship_shaped_under_generic("fix: typo") is False
    assert ship_shaped_under_generic("chore: bump deps") is False
    # Release cuts and snapshots are excluded before the probe runs.
    assert ship_shaped_under_generic(
        "v0.21.0: bundled cut (#2911) (fak tools)"
    ) is False
    assert ship_shaped_under_generic(
        "working-dir snapshot: bulk sweep (#2911) (fak tools)"
    ) is False


# ---------------------------------------------------------------------------
# end to end: library, CLI, doctor
# ---------------------------------------------------------------------------
def test_library_is_shipped_via_issue_leaf(tmp_path: Path):
    from dos import oracle
    from dos.config import default_config

    _repo(tmp_path, FAK_SUBJECT)
    cfg = dataclasses.replace(default_config(tmp_path), stamp=ISSUE_LEAF_ON)

    v = oracle.is_shipped("tools", "2911", cfg=cfg)
    assert v.shipped is True
    # The stamp is agent-authored subject text → the FORGEABLE rung, graded
    # honestly as grep-subject; the raw rung stays distinct for Go parity
    # (non-direct → the Go port abstains).
    assert v.source == "grep-subject"
    assert v.rung == "issue-leaf"

    # Honest-negative: an uncited issue stays not-shipped under the flag.
    v = oracle.is_shipped("tools", "9999", cfg=cfg)
    assert v.shipped is False
    assert v.source == "none"


def test_cli_verify_issue_leaf_with_declared_toml(tmp_path: Path):
    """The user-facing path: `dos.toml [stamp] issue_leaf_stamp = true` + the
    real `dos verify tools 2911` subprocess — the exact fak #3132 repro."""
    _repo(tmp_path, FAK_SUBJECT)
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nissue_leaf_stamp = true\n', encoding="utf-8"
    )
    proc = _cli(tmp_path, "verify", "tools", "2911", "--json")
    assert proc.stdout, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["shipped"] is True, (proc.stdout, proc.stderr)
    assert payload["source"] == "grep-subject"
    assert proc.returncode == 0

    # Without the declaration the same repo honestly answers via none.
    (tmp_path / "dos.toml").write_text('[stamp]\nstyle = "grep"\n', encoding="utf-8")
    proc2 = _cli(tmp_path, "verify", "tools", "2911", "--json")
    payload2 = json.loads(proc2.stdout)
    assert payload2["shipped"] is False
    assert payload2["source"] == "none"


def test_doctor_recognized_counts_issue_leaf_ships_when_declared(tmp_path: Path):
    """The `recognized: 0` symptom flips: with the key declared, the fak-shape
    subjects flow into the doctor's recognized count (ship_shaped ∩
    active-recognizes); without it they stay ship-shaped-but-unrecognized."""
    _repo(
        tmp_path,
        "fix(pythongate): add skill-slop scorecard paths (#2911) (fak tools)",
        "feat(gateway): treat same-tick ready as positive (#3001) (fak gateway)",
        "fix: typo",
    )
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nissue_leaf_stamp = true\n', encoding="utf-8"
    )
    proc = _cli(tmp_path, "doctor", "--json")
    v = json.loads(proc.stdout)["verifiability"]
    assert v["ship_shaped"] == 2
    assert v["recognized"] == 2

    # Undeclared: the shapes are still SEEN (ship-shaped) but not recognized —
    # the doctor tells the repo to reconcile [stamp] instead of staying silent.
    (tmp_path / "dos.toml").write_text('[stamp]\nstyle = "grep"\n', encoding="utf-8")
    proc2 = _cli(tmp_path, "doctor", "--json")
    v2 = json.loads(proc2.stdout)["verifiability"]
    assert v2["ship_shaped"] == 2
    assert v2["recognized"] == 0
