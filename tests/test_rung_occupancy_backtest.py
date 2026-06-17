"""Pin the rung-occupancy backtest harvester (`scripts/rung_occupancy_backtest.py`).

The script is the I/O boundary for the pure `dos.rung_occupancy` fold: it harvests
the (series, phase) corpus from real ship-stamps, grades each via the oracle, and
re-checks the forgeable greens against the artefact (file-path) rung DIRECTLY. These
tests pin the harvest grammar, the applicability gate (a phase with no distinctive
deliverable is skipped, not refuted), and the floor-holds / floor-breaches contract.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "rung_occupancy_backtest.py"
_spec = importlib.util.spec_from_file_location("rung_occupancy_backtest", _HELPER)
rob = importlib.util.module_from_spec(_spec)
sys.modules["rung_occupancy_backtest"] = rob
_spec.loader.exec_module(rob)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo with a docs/ tree and a few ship-stamped commits."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "342_the-long-descriptive-name.md").write_text("# plan\n", encoding="utf-8")
    (docs / "342_SHORT.md").write_text("# sidecar\n", encoding="utf-8")
    (docs / "357_release-cadence.md").write_text("# plan\n", encoding="utf-8")
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: thing (docs/342 M4)")
    (tmp_path / "g.txt").write_text("y\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: more (docs/357)")
    (tmp_path / "h.txt").write_text("z\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "chore: no stamp here")
    return tmp_path


def test_harvest_extracts_series_and_phase_tokens(repo):
    cands = rob._harvest_git_stamps(str(repo), window=50)
    keys = {(c.plan, c.phase) for c in cands}
    # docs/342 M4 → phase token M4; docs/357 with no phase → the doc number itself.
    assert ("docs/342", "M4") in keys
    assert ("docs/357", "357") in keys
    # the un-stamped commit contributes nothing.
    assert all(c.plan.startswith("docs/") for c in cands)


def test_harvest_resolves_the_most_specific_doc(repo):
    cands = {c.phase: c for c in rob._harvest_git_stamps(str(repo), window=50)
             if c.plan == "docs/342"}
    c = cands["M4"]
    # Among docs/342_*.md, the longer descriptive name wins over the SHORT sidecar.
    assert c.doc_path.endswith("the-long-descriptive-name.md")


def test_harvest_dedups_repeated_pairs(repo):
    # A second commit re-stamping the same pair must not double-count it.
    _git(repo, "commit", "-q", "--allow-empty", "-m", "redo (docs/342 M4)")
    cands = rob._harvest_git_stamps(str(repo), window=50)
    m4 = [c for c in cands if (c.plan, c.phase) == ("docs/342", "M4")]
    assert len(m4) == 1


def test_harvest_fails_to_empty_on_a_non_repo(tmp_path):
    # No git history → no candidates, never a crash.
    assert rob._harvest_git_stamps(str(tmp_path / "nope"), window=50) == []


def test_artifact_recheck_false_without_a_doc():
    # No plan doc → the file-path backstop can't engage → not corroborated.
    assert rob._artifact_recheck("docs/999", "M1", "") is False


def test_run_uses_git_stamp_fallback_when_no_plans(repo, monkeypatch):
    # The repo has docs/*.md but no *-plan.md, so the markdown plan source is empty
    # and run() must fall through to the git-stamp corpus and produce a census.
    monkeypatch.chdir(repo)
    occ, verdicts = rob.run(str(repo), git_window=50)
    assert occ is not None
    # Every harvested candidate was graded into the census.
    assert occ.n >= 2
    # The census is internally consistent: the three occupancy classes partition
    # the greens, and any breach implies at least one forgeable green.
    assert occ.green == occ.nonforgeable + occ.forgeable + occ.ungraded
    if not occ.floor_held:
        assert occ.forgeable >= 1


def _floor_repo(tmp_path, files_line, ship_files):
    """A git repo whose docs/700 plan declares `files_line` and whose DT2-shape ship
    commit (subject names NO phase token) touches `ship_files`.

    This mirrors the production corroboration shape exactly: the only artefact tying
    the phase to git is the file-path rung. `_artifact_recheck` calls the file-path
    backstop directly, so `via=file-path` iff a real commit co-touched >= 2 of the
    declared files. The plan doc is committed under a bare `docs:` subject (no
    trailer) so it adds no subject-rung green of its own."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "700_floor-plan.md").write_text(
        "# 700 — floor plan\n\n## Phase 1 — do the thing\n\n"
        f"{files_line}\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "docs: add the floor plan")
    # The ship: subject names NO phase token (the DT2 shape), diff touches
    # `ship_files` — the only artefact the file-path re-check can read.
    for f in ship_files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", *ship_files)
    _git(tmp_path, "commit", "-q", "-m", "feat: implement the write gate")
    return tmp_path


def test_artifact_recheck_corroborates_when_declared_files_were_shipped(
    tmp_path, monkeypatch
):
    """The re-check CORROBORATES a phase whose declared files a real commit touched.

    `_artifact_recheck` is the backtest's independent witness over a forgeable green:
    it lands `via=file-path` exactly when a commit co-touched >= 2 of the phase's
    declared load-bearing files. With the `**Files:**` convention read by
    `_extract_phase_files`, a `## Phase 1` whose declared files shipped is
    corroborated — the floor is load-bearing. The positive witness."""
    from dos import config as C

    repo = _floor_repo(
        tmp_path,
        "**Files:** `src/dos/a.py`, `tests/test_a.py`",
        ["src/dos/a.py", "tests/test_a.py"],
    )
    monkeypatch.chdir(repo)
    C.set_active(C.load_workspace_config(str(repo)))
    doc = str(repo / "docs" / "700_floor-plan.md")
    assert rob._artifact_recheck("docs/700", "P1", doc) is True
    # And the unit IS in the floor's domain (it declares a distinctive artefact).
    assert rob._phase_has_declarable_artefact("docs/700", "P1", doc) is True


def test_artifact_recheck_refutes_when_declared_files_were_never_shipped(
    tmp_path, monkeypatch
):
    """The convention cannot FAKE a pass: files no commit co-touched are refuted.

    The plan declares two files, but the ship touched DIFFERENT files, so no commit
    touched >= 2 declared files — `_artifact_recheck` is False. This pins that the
    declaration only POINTS at diff-witnessed evidence; it cannot manufacture it,
    which keeps the floor non-forgeable."""
    from dos import config as C

    repo = _floor_repo(
        tmp_path,
        "**Files:** `src/dos/declared_x.py`, `tests/test_declared_x.py`",
        ["src/dos/actually_shipped.py", "tests/test_actually_shipped.py"],
    )
    monkeypatch.chdir(repo)
    C.set_active(C.load_workspace_config(str(repo)))
    doc = str(repo / "docs" / "700_floor-plan.md")
    assert rob._artifact_recheck("docs/700", "P1", doc) is False


def test_applicability_gate_skips_a_phase_with_no_distinctive_artefact(
    tmp_path, monkeypatch
):
    """A phase that declares NO distinctive deliverable is OUT of the floor's domain.

    A `**Files:** none — ...` declaration (or a section that names only the plan doc
    / shared-infra hubs) harvests no distinctive file, so `_phase_has_declarable_
    artefact` is False — the backtest folds it as `recheck_skipped`, never refuted.
    This is the honest accounting for a re-stamp / doc-only / design-only phase; it
    is not gameable, because declaring MORE files only moves a unit toward
    corroborated/refuted, never toward skipped."""
    from dos import config as C

    repo = _floor_repo(
        tmp_path,
        "**Files:** none — design-only phase, ships no deliverable.",
        ["docs/700_floor-plan.md"],  # ship touched only the plan doc
    )
    monkeypatch.chdir(repo)
    C.set_active(C.load_workspace_config(str(repo)))
    doc = str(repo / "docs" / "700_floor-plan.md")
    assert rob._phase_has_declarable_artefact("docs/700", "P1", doc) is False
