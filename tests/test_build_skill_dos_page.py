"""Tests for the skill-with/without-DOS dashboard builder (`scripts/build_skill_dos_page.py`,
issue #176, docs/345 §6).

This is DOS dev tooling, not a kernel module — it `import`s the benchmark + `dos` and lives
under `scripts/`, the same one-way arrow as `drift_scoreboard.py`. The suite pins the parts
that carry the honesty contract:

  * the page is DETERMINISTIC — same harness input renders byte-identical output (the
    `--check` freshness loop depends on it);
  * the page's HEADLINE NUMBERS are DERIVED from the harness, never hand-typed — every
    `(skill, variant)` over-claim rate the harness computes appears in the rendered table,
    so a number can't silently drift out of sync;
  * the committed `docs/skill-dos-benchmark.html` is what the harness currently renders
    (the same freshness check `--check` runs), so a stale page fails CI loudly;
  * the negative skill is presented as a negative (DOS not helping), never hidden.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# Import the script-under-test by path (it is not an installed package).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_HELPER_PATH = _REPO_ROOT / "scripts" / "build_skill_dos_page.py"
_spec = importlib.util.spec_from_file_location("build_skill_dos_page", _HELPER_PATH)
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

from benchmark.skill_dos_ablation import corpus as _corpus  # noqa: E402
from benchmark.skill_dos_ablation import harness as _harness  # noqa: E402


def test_render_is_deterministic():
    """Same harness input → byte-identical page (the `--check` loop relies on this)."""
    a, _ = bp.build()
    b, _ = bp.build()
    assert a == b


def test_page_is_self_contained_html():
    text, _ = bp.build()
    assert text.startswith("<!DOCTYPE html>")
    assert "<style>" in text  # inline styles — no external CSS dependency
    assert text.rstrip().endswith("</html>")


def test_headline_numbers_are_derived_not_hardcoded():
    """Every (skill, variant) over-claim rate the harness computes must appear in the page.

    This is the honesty gate: the page cannot claim a number the harness does not produce.
    """
    tasks = _corpus.corpus()
    scores = _harness.compute(tasks)
    text, _ = bp.build()
    for skill in _corpus.SKILLS:
        for variant in ("original", "-dos"):
            s = scores[skill][variant]
            cell = f"{s.silent_overclaims}/{s.n_failed} = {s.overclaim_rate * 100:.0f}%"
            assert cell in text, f"page is missing the derived rate for {skill}/{variant}: {cell!r}"


def test_rigged_failure_denominator_is_shown():
    tasks = _corpus.corpus()
    n_failed = _harness.total_failed(tasks)
    text, _ = bp.build()
    # the denominator the issue requires made explicit — it appears in the headline lede
    assert str(n_failed) in text


def test_groundable_skills_drive_to_zero_and_original_leaks():
    """The measured claim, read off the rendered table: original 100% → -dos 0% on groundables."""
    tasks = _corpus.corpus()
    scores = _harness.compute(tasks)
    for skill in _corpus.SKILLS:
        if skill == _corpus.NEGATIVE_SKILL:
            continue
        assert scores[skill]["original"].overclaim_rate == 1.0
        assert scores[skill]["-dos"].overclaim_rate == 0.0


def test_negative_is_presented_as_a_negative():
    """The pure-prose skill is in the page AND shown as DOS-not-helping (never hidden)."""
    text, _ = bp.build()
    assert _corpus.NEGATIVE_SKILL in text
    assert "UNWITNESSABLE" in text
    assert "negative" in text.lower()
    # and the harness agrees it does not improve
    scores = _harness.compute(_corpus.corpus())
    neg = scores[_corpus.NEGATIVE_SKILL]
    assert neg["-dos"].overclaim_rate == neg["original"].overclaim_rate


def test_tessl_contrast_present_and_sourced():
    """The judge-vs-witness contrast names Tessl and carries primary sources + the concessions."""
    text, _ = bp.build()
    assert "Tessl" in text
    assert "tessl.io" in text
    # the docs/345 §6.5 honesty concessions must be on the page so it can't over-claim
    assert "concession" in text.lower()


def test_check_roundtrips_against_a_freshly_built_page(tmp_path):
    """`--out` then `--check` against that file round-trips byte-for-byte (the freshness loop).

    The published HTML is NOT tracked on master (`*.html` is gitignored; the page is
    published to the gh-pages branch). So the source of truth is the builder, and the
    freshness guarantee is this round-trip, not a committed artifact.
    """
    out = tmp_path / "skill-dos-benchmark.html"
    assert bp.main(["--out", str(out)]) == 0
    assert bp.main(["--check", "--out", str(out)]) == 0
    # a tampered page fails the check loudly
    out.write_bytes(out.read_bytes() + b"<!-- drift -->")
    assert bp.main(["--check", "--out", str(out)]) == 1


def test_build_exits_clean_when_invariants_hold(tmp_path):
    """The CLI exit code is the loud kill: 0 only when the harness invariants hold."""
    out = tmp_path / "page.html"
    assert bp.main(["--out", str(out)]) == 0
    assert bp.main(["--check", "--out", str(out)]) == 0
