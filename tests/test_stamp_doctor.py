"""SCV Phase 3 — `dos doctor` names the active stamp convention (3b), and the
`--check` completeness rail flags a declared `[stamp]` that matches none of the
repo's own ship-shaped commits (3c).

The convention readback + verify wiring is pinned by `test_stamp_convention.py`
and `test_verify_no_plan.py`; this file pins the OPERATOR-FACING surfaces SCV
Phase 3 adds — the doctor report and the completeness finding — so an operator
can see which grammar verify will use and be warned when a declared grammar
silently misses real ships.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dos.stamp import (
    JOB_STAMP_CONVENTION,
    GENERIC_STAMP_CONVENTION,
    StampConvention,
    ship_shaped_under_generic,
    convention_coverage_finding,
    is_conventional_commit,
    conventional_commits_unstamped_finding,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
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


def _doctor(repo: Path, *extra: str):
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "doctor", "--workspace", str(repo), *extra],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# 3b — doctor names the active convention
# ---------------------------------------------------------------------------
def test_doctor_reports_generic_convention_by_default(tmp_path):
    """No `[stamp]` table → the generic grammar (F9 out-of-the-box default)."""
    _repo(tmp_path)
    out = _doctor(tmp_path).stdout
    assert "stamp convention" in out
    assert "generic (any/no dir prefix)" in out


def test_doctor_reports_job_convention_under_job_flag(tmp_path):
    """`--job` opts into the strict job grammar — still reachable, just not default."""
    _repo(tmp_path)
    out = _doctor(tmp_path, "--job").stdout
    assert "job (docs|go|agents|job_search|scripts)" in out


def test_doctor_reports_generic_convention(tmp_path):
    _repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nsubject_dirs = []\n', encoding="utf-8"
    )
    out = _doctor(tmp_path).stdout
    assert "generic (any/no dir prefix)" in out


def test_doctor_reports_declared_dirs(tmp_path):
    _repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nsubject_dirs = ["src", "lib"]\n', encoding="utf-8"
    )
    out = _doctor(tmp_path).stdout
    assert "src|lib" in out


# ---------------------------------------------------------------------------
# 3c — the completeness rail (pure)
# ---------------------------------------------------------------------------
def test_ship_shaped_detector():
    assert ship_shaped_under_generic("AUTH2: ship token refresh") is True
    assert ship_shaped_under_generic("docs/RS: RS1 — surface") is True
    assert ship_shaped_under_generic("working-dir snapshot: bulk sweep") is False
    assert ship_shaped_under_generic("") is False


def test_ship_shaped_detector_multiword_and_release():
    """F8 regression: the detector must SEE multi-word `<slug> Phase <N>:` ships
    (so the rail can judge against them) and must NOT count release anchors or
    ordinary conventional-commit subjects as ships (so the rail doesn't cite the
    wrong commit / over-match).

    The original `[A-Za-z][A-Za-z0-9]*` series + glued matching saw none of the
    real hyphenated/spaced ships and DID match `vX.Y.Z:` + `chore:`/`fix:` — the
    two halves of the F8 false all-clear.
    """
    # Multi-word / hyphenated / spaced slugs with a `Phase N` token → ship-shaped.
    assert ship_shaped_under_generic("hardware-thing Phase 1: do work") is True
    assert ship_shaped_under_generic("blktrace auto-install Phase 1: flag") is True
    assert ship_shaped_under_generic("SGLang charts Phase 3b.2: histogram") is True
    assert ship_shaped_under_generic("chart-audit-38-44 P1+P2: memo") is True
    # Release-cut bundles (2- and 3-component) → NOT a direct ship.
    assert ship_shaped_under_generic("v25.4: amoprof-integration Phase 2 + …") is False
    assert ship_shaped_under_generic("v0.378.0: GBA6 + FQ-375 closer") is False
    # Ordinary conventional commits (no numbered phase) → NOT ship-shaped.
    assert ship_shaped_under_generic("chore: refactor") is False
    assert ship_shaped_under_generic("fix: typo") is False
    assert ship_shaped_under_generic("Merge branch main") is False


def test_coverage_finding_fires_on_multiword_mismatch():
    """F8 core: a declared src-only grammar with real `<slug> Phase <N>:` ships →
    a finding. Before the detector widening the rail found 'nothing ship-shaped'
    and stayed silent — the false all-clear."""
    src_only = StampConvention(subject_dirs=("src",))
    finding = convention_coverage_finding(
        src_only,
        ["hardware-thing Phase 1: x", "hardware-thing Phase 2: y"],
        declared=True,
    )
    assert finding is not None
    assert "recognizes none" in finding


def test_coverage_finding_fires_on_mismatch():
    """A declared src-only grammar that recognizes none of the repo's bare
    `<SERIES><PHASE>:` commits → a finding."""
    src_only = StampConvention(subject_dirs=("src",))
    finding = convention_coverage_finding(
        src_only, ["AUTH2: ship it", "PAY3: pay"], declared=True
    )
    assert finding is not None
    assert "recognizes none" in finding


def test_coverage_finding_quiet_when_grammar_matches():
    src_only = StampConvention(subject_dirs=("src",))
    assert convention_coverage_finding(
        src_only, ["src/AUTH: AUTH2 — ok", "fix typo"], declared=True
    ) is None


def test_coverage_finding_quiet_when_not_declared():
    """An INHERITED default is never audited — only the host's own declaration is."""
    src_only = StampConvention(subject_dirs=("src",))
    assert convention_coverage_finding(
        src_only, ["AUTH2: ship it"], declared=False
    ) is None


def test_coverage_finding_quiet_when_no_ship_shaped_commits():
    """Nothing ship-shaped to judge against → no finding (can't conclude mismatch)."""
    assert convention_coverage_finding(
        GENERIC_STAMP_CONVENTION, ["just some prose", "more prose"], declared=True
    ) is None


# ---------------------------------------------------------------------------
# 3c — the completeness rail (through the real `dos doctor --check` CLI)
# ---------------------------------------------------------------------------
def test_doctor_check_flags_mismatched_stamp(tmp_path):
    """Declares src-only but commits are bare `AUTH2:` → `--check` exits 1 + finding."""
    _repo(tmp_path, "AUTH2: ship it", "PAY3: pay")
    (tmp_path / "dos.toml").write_text('[stamp]\nsubject_dirs = ["src"]\n', encoding="utf-8")
    proc = _doctor(tmp_path, "--check")
    assert proc.returncode == 1, proc.stdout
    assert "finding:" in proc.stderr
    assert "recognizes none" in proc.stderr


def test_doctor_check_passes_when_grammar_matches(tmp_path):
    """Generic decl + bare commits → `--check` is clean (exit 0, no finding)."""
    _repo(tmp_path, "AUTH2: ship it")
    (tmp_path / "dos.toml").write_text('[stamp]\nsubject_dirs = []\n', encoding="utf-8")
    proc = _doctor(tmp_path, "--check")
    assert proc.returncode == 0, proc.stderr
    assert "finding:" not in proc.stderr


def test_doctor_check_quiet_without_declaration(tmp_path):
    """No `[stamp]` table → no finding even if commits don't match the default."""
    _repo(tmp_path, "AUTH2: ship it")
    (tmp_path / "dos.toml").write_text("[reasons]\n", encoding="utf-8")
    proc = _doctor(tmp_path, "--check")
    assert proc.returncode == 0, proc.stderr
    assert "finding:" not in proc.stderr


# ---------------------------------------------------------------------------
# The always-honest verifiability cold-open — one line, correct on EVERY repo,
# saying whether `dos verify` can check this repo's claims at all. The iconicity
# on-ramp: it tells the truth about coverage instead of `verify`-ing a phase and
# false-accusing `via none` on a Conventional-Commits repo (the "never cries
# wolf" discipline the kill-list demanded of any naive verify-on-cold-repo).
# ---------------------------------------------------------------------------
def test_verifiability_headline_present_on_every_doctor(tmp_path):
    """The line appears on a plain `dos doctor` (no flags) — it's a default, not
    gated behind `--check`."""
    _repo(tmp_path, "AUTH2: ship the thing")
    out = _doctor(tmp_path).stdout
    assert "verifiability" in out


def test_verifiability_counts_recognized_ships(tmp_path):
    """A repo whose commits ARE ship-shaped under the active (generic) grammar →
    the affirmative form names a non-zero count."""
    _repo(tmp_path, "AUTH2: ship token refresh", "PAY3: charge card")
    out = _doctor(tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("verifiability"))
    assert "can check" in line
    # init + 2 ships = 3 commits read; both ships recognized under generic.
    assert "2 of your last 3 commits" in line


def test_verifiability_honest_on_conventional_commits(tmp_path):
    """The Conventional-Commits majority repo (no unit-of-work ids) → the honest
    'no referee can check' form, NOT a false accusation. This is the kill-list's
    'never cries wolf' contract made executable."""
    _repo(tmp_path, "fix: typo", "chore: bump deps", "feat: add button")
    out = _doctor(tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("verifiability"))
    assert "no referee can check" in line
    # Crucially it does NOT claim any verifiable ships, and does NOT error.
    assert "can check `dos verify`" not in line


def test_verifiability_flags_declared_grammar_mismatch(tmp_path):
    """Ship-shaped commits the DECLARED grammar misses → the 'will resolve via
    none; reconcile' form (the cold-open mirror of the `--check` finding)."""
    _repo(tmp_path, "AUTH2: ship it", "PAY3: pay")
    (tmp_path / "dos.toml").write_text('[stamp]\nsubject_dirs = ["src"]\n', encoding="utf-8")
    out = _doctor(tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("verifiability"))
    assert "via none" in line


def test_verifiability_in_json(tmp_path):
    """`dos doctor --json` carries the machine form: counts a skill/CI branches on."""
    import json as _json
    _repo(tmp_path, "AUTH2: ship token refresh")
    proc = _doctor(tmp_path, "--json")
    report = _json.loads(proc.stdout)
    v = report["verifiability"]
    assert v["commits_read"] == 2  # init + 1 ship
    assert v["ship_shaped"] == 1
    assert v["recognized"] == 1
    assert "generic" in v["grammar"]


# ---------------------------------------------------------------------------
# The actionable trailer recipe — a DECLARED Conventional-Commits repo whose
# commits carry no stamp the grammar can bind should hear the concrete fix
# (docs/289 trailer), not the bare 'no referee can check' cul-de-sac. The
# complement to the 3c mismatch finding: that flags ship-SHAPED commits the
# declared grammar misses; this flags the OTHER dead-end (nothing ship-shaped at
# all, because the stamp lives in a trailer the start-anchored grammar can't see).
# ---------------------------------------------------------------------------
def test_is_conventional_commit():
    # Canonical CC headers (with/without scope, with `!`) → True.
    assert is_conventional_commit("fix(gateway): treat same-tick ready") is True
    assert is_conventional_commit("feat(fak): add pagination plane") is True
    assert is_conventional_commit("docs(claude): audit dos install") is True
    assert is_conventional_commit("chore!: drop py38") is True
    assert is_conventional_commit("revert: bad merge") is True
    # A bare `<series>:` ship candidate, a release, a merge, prose → NOT CC (a
    # closed type set keeps `tools:`/`AUTH2:` out of the recipe's way).
    assert is_conventional_commit("tools: add DOS canary") is False
    assert is_conventional_commit("AUTH2: ship token refresh") is False
    assert is_conventional_commit("v0.23.0: release") is False
    assert is_conventional_commit("Merge branch main") is False
    assert is_conventional_commit("") is False


def test_unstamped_finding_recommends_trailer_when_off():
    """Declared generic grammar + bare CC commits (trailer OFF) → the 'set
    trailer_stamp = true and stamp' recipe."""
    gen = StampConvention(subject_dirs=())  # generic, trailer_stamp default False
    out = conventional_commits_unstamped_finding(
        gen, ["fix(gateway): harden", "feat(api): paginate"], declared=True
    )
    assert out is not None
    assert "Conventional-Commits-shaped" in out
    assert "trailer_stamp = true" in out


def test_unstamped_finding_nudges_when_trailer_on_but_unstamped():
    """trailer_stamp already ON but no commit carries a trailer yet → the
    early-adoption 'stamp ship commits' nudge, WITHOUT re-recommending the flag."""
    on = StampConvention(subject_dirs=(), trailer_stamp=True)
    out = conventional_commits_unstamped_finding(
        on, ["fix(gateway): harden", "feat(api): paginate"], declared=True
    )
    assert out is not None
    assert "stamp ship commits" in out
    assert "trailer_stamp = true" not in out  # config is right; don't re-recommend


def test_unstamped_finding_quiet_when_not_declared():
    """An inherited default on a foreign CC repo is never nagged (never cries wolf)."""
    gen = StampConvention(subject_dirs=())
    assert conventional_commits_unstamped_finding(
        gen, ["fix(x): y", "feat(z): w"], declared=False
    ) is None


def test_unstamped_finding_quiet_when_something_binds():
    """If the active grammar already binds a ship, the affirmative form wins
    upstream — no recipe (mixed CC + a real `<SERIES><PHASE>:` ship)."""
    gen = StampConvention(subject_dirs=())
    assert conventional_commits_unstamped_finding(
        gen, ["AUTH2: ship it", "fix(x): y"], declared=True
    ) is None


def test_unstamped_finding_quiet_when_no_conventional_commits():
    """No CC commit to advise on → None (the bare/ship-shaped cases own those)."""
    gen = StampConvention(subject_dirs=())
    assert conventional_commits_unstamped_finding(
        gen, ["just prose", "more prose"], declared=True
    ) is None


def test_verifiability_recipe_for_declared_conventional_commits(tmp_path):
    """End-to-end: a DECLARED [stamp] repo whose commits are Conventional-Commits-
    shaped (no trailer) → the actionable trailer recipe on the headline, NOT the
    dead-end 'no referee can check' line."""
    _repo(tmp_path, "fix(gateway): treat same-tick ready", "feat(api): add pagination")
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nsubject_dirs = []\n', encoding="utf-8"
    )
    out = _doctor(tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("verifiability"))
    assert "Conventional-Commits-shaped" in line
    assert "trailer_stamp = true" in line
    assert "no referee can check" not in line


def test_verifiability_recipe_quiet_for_undeclared_conventional_commits(tmp_path):
    """Regression guard for `test_verifiability_honest_on_conventional_commits`: a
    CC repo that declared NO [stamp] table keeps the gentle 'no referee' line — the
    recipe must not nag an un-opted-in repo."""
    _repo(tmp_path, "fix: typo", "chore: bump deps", "feat: add button")
    out = _doctor(tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("verifiability"))
    assert "no referee can check" in line
    assert "trailer_stamp" not in line
