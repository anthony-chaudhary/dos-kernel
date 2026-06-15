"""Tests for scripts/scoreboard_rollup.py — the published-set roll-up.

The load-bearing assertion is the HONESTY GATE: the committed
`docs/scoreboard/rollup.md` must match what the fold derives from the per-repo
`sweep.json` files. A test re-derives a couple of totals INDEPENDENTLY (raw
`json.load`, not the module's fold) and asserts they appear in the report — so a
hand-edited number in the report, or a sweep.json change without a regenerate,
fails CI. The `--check` path is exercised the same way the CLI runs it.
"""
from __future__ import annotations

import glob
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "scoreboard_rollup.py"
SCOREBOARD = REPO / "docs" / "scoreboard"
ROLLUP = SCOREBOARD / "rollup.md"


def _load_module():
    """Load scoreboard_rollup.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("scoreboard_rollup", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raw_sweeps() -> list[dict]:
    """Every published sweep.json, loaded RAW — independent of the module's
    fold, so the test is a real second opinion on the numbers."""
    out = []
    for f in sorted(glob.glob(str(SCOREBOARD / "*" / "*" / "sweep.json"))):
        out.append(json.loads(Path(f).read_text(encoding="utf-8")))
    return out


def _summary(doc: dict) -> dict:
    return doc.get("summary", doc)


def test_finds_the_published_sweeps():
    mod = _load_module()
    paths = mod.find_sweeps()
    raw = _raw_sweeps()
    assert len(paths) == len(raw)
    assert len(paths) >= 15  # the named clean set + the self page


def test_fold_attributed_matches_independent_sum():
    """Total attributed in the fold == raw sum across the JSON files."""
    mod = _load_module()
    agg = mod.fold(mod.find_sweeps())
    raw_attr = sum(int(d.get("attributed_commits", 0) or 0) for d in _raw_sweeps())
    assert agg["attributed"] == raw_attr


def test_fold_checkable_and_witnessed_match_independent_sum():
    mod = _load_module()
    agg = mod.fold(mod.find_sweeps())
    raw_check = sum(int(_summary(d).get("checkable", 0) or 0) for d in _raw_sweeps())
    raw_wit = sum(int(_summary(d).get("witnessed", 0) or 0) for d in _raw_sweeps())
    assert agg["checkable"] == raw_check
    assert agg["witnessed"] == raw_wit


def test_pooled_backed_rate_computed_independently_matches():
    """The pooled backed rate the report renders == witnessed/checkable computed
    here from the raw JSON, to one decimal place (the report's precision)."""
    mod = _load_module()
    agg = mod.fold(mod.find_sweeps())
    raw_check = sum(int(_summary(d).get("checkable", 0) or 0) for d in _raw_sweeps())
    raw_wit = sum(int(_summary(d).get("witnessed", 0) or 0) for d in _raw_sweeps())
    independent_rate = raw_wit / raw_check
    rendered = mod.render(agg, stamp="2026-06-15")
    assert f"{independent_rate:.1%}" in rendered


def test_report_contains_the_grep_verifiable_totals():
    """The committed report shows the independent totals verbatim (the spot-check
    the goal asks for, pinned as a test)."""
    text = ROLLUP.read_text(encoding="utf-8")
    raw = _raw_sweeps()
    attr = sum(int(d.get("attributed_commits", 0) or 0) for d in raw)
    check = sum(int(_summary(d).get("checkable", 0) or 0) for d in raw)
    wit = sum(int(_summary(d).get("witnessed", 0) or 0) for d in raw)
    assert f"{attr:,}" in text
    assert f"{check:,}" in text
    assert f"{wit:,}" in text


def test_clean_repo_count_matches():
    """The 'N of M repos: 100% backed' count == repos with unwitnessed==0."""
    mod = _load_module()
    agg = mod.fold(mod.find_sweeps())
    clean = sum(1 for d in _raw_sweeps()
                if int(_summary(d).get("unwitnessed", 0) or 0) == 0)
    assert agg["clean_repos"] == clean


def test_render_is_deterministic():
    """Same inputs + stamp ⇒ byte-identical output (the --check contract)."""
    mod = _load_module()
    sweeps = mod.find_sweeps()
    a = mod.render(mod.fold(sweeps), stamp="2026-06-15")
    b = mod.render(mod.fold(sweeps), stamp="2026-06-15")
    assert a == b


def test_stamp_appears_in_output():
    mod = _load_module()
    out = mod.build(stamp="2099-01-02")
    assert "2099-01-02" in out


def test_committed_report_matches_data_via_check():
    """The honesty gate: the committed rollup.md == the fold. Runs the CLI
    --check path exactly as a developer / CI would."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    assert r.returncode == 0, (
        "rollup.md has drifted from the per-repo sweep.json data — re-run "
        "`python scripts/scoreboard_rollup.py`.\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr}")


def test_check_fails_on_drift(tmp_path, monkeypatch):
    """--check returns non-zero when the on-disk report disagrees with the fold —
    proving the gate actually bites (not a no-op pass)."""
    mod = _load_module()
    # Point the module at a temp rollup with deliberately wrong content.
    bad = tmp_path / "rollup.md"
    bad.write_text("not the real report\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROLLUP_PATH", bad)
    rc = mod.main(["--check"])
    assert rc == 1


def test_ethics_line_present():
    """The load-bearing advisory line is reused verbatim from scoreboard_copy."""
    mod = _load_module()
    out = mod.build(stamp="2026-06-15")
    assert mod.copy.ETHICS_LINE in out


@pytest.mark.parametrize("needle", [
    "## The numbers",
    "## Reproduce",
    "python scripts/scoreboard_rollup.py",
    "methodology.md",
])
def test_report_has_the_expected_structure(needle):
    text = ROLLUP.read_text(encoding="utf-8")
    assert needle in text
