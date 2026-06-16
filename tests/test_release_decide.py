"""The cadence DECISION — the semver auto-rule and the should-release gates.

`scripts/release_decide.py` is the automated form of the judgment a human makes
in `/release` Step 2: should we release now, and at what semver level? It stalled
as a manual gate (240+ commits behind v0.26.0 with the version unmoved), so it
was made mechanical for the `release-cadence.yml` cron.

This test pins the two halves of that judgment as PURE functions (no git, no
network): the semver auto-rule (`classify_subject` / `decide_level` /
`next_version`) and the should-release predicate (`decide` over a synthetic
`release_context`-shaped payload). It is the witness that FAILS before the script
exists and passes after — the conventional-commit → bump mapping and every gate
(nothing-to-ship, red CI base, unparseable workflow, version drift) are locked so
a refactor can't silently change the cadence's mind.

Loaded by path (scripts/ is not an importable package) — the same convention as
`tests/test_release_bump.py`. Dev/release TOOLING, never imported by the kernel.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import dos

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_DECIDE_PY = _REPO_ROOT / "scripts" / "release_decide.py"


def _load():
    spec = importlib.util.spec_from_file_location("_release_decide", _DECIDE_PY)
    assert spec and spec.loader, f"cannot load {_DECIDE_PY}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the semver auto-rule (pure) -------------------------------------------

def test_classify_subject_maps_conventional_prefixes():
    rd = _load()
    # feat → minor
    assert rd.classify_subject("feat(arbiter): admit in-lane child edits") == "minor"
    assert rd.classify_subject("feat: a bare feature") == "minor"
    # fix / docs / chore / build / refactor → patch
    assert rd.classify_subject("fix(_tree): close a false-disjoint") == "patch"
    assert rd.classify_subject("docs(readme): trim the install deep-dive") == "patch"
    assert rd.classify_subject("chore: bump a dev dep") == "patch"
    assert rd.classify_subject("build(dos-hook): rebuild binaries") == "patch"
    # a bare `area:` prefix and an unknown word both default to patch (conservative)
    assert rd.classify_subject("scoreboard: seed three repos") == "patch"
    assert rd.classify_subject("an unprefixed subject") == "patch"


def test_classify_subject_breaking_marker_is_major():
    rd = _load()
    # the `!` shorthand, with and without a scope
    assert rd.classify_subject("feat!: change the verdict vocabulary") == "major"
    assert rd.classify_subject("fix(config)!: rename SubstrateConfig.root") == "major"
    # the body marker (long form)
    assert rd.classify_subject("feat(api): new flag", "BREAKING CHANGE: drops --old") == "major"
    assert rd.classify_subject("refactor: tidy", "BREAKING-CHANGE: moved module") == "major"


def test_decide_level_takes_the_highest_signal():
    rd = _load()
    # only fixes/docs → patch
    lvl, _ = rd.decide_level([{"subject": "fix(x): a"}, {"subject": "docs(y): b"}])
    assert lvl == "patch"
    # one feat among fixes → minor
    lvl, _ = rd.decide_level([{"subject": "fix(x): a"}, {"subject": "feat(z): c"}])
    assert lvl == "minor"
    # any breaking marker → major
    lvl, _ = rd.decide_level([{"subject": "feat(z): c"}, {"subject": "fix!: d"}])
    assert lvl == "major"
    # empty range → patch (the floor, never crashes)
    lvl, _ = rd.decide_level([])
    assert lvl == "patch"


def test_decide_level_collects_scopes_as_themes_and_skips_release_commits():
    rd = _load()
    commits = [
        {"subject": "feat(arbiter): x"},
        {"subject": "fix(arbiter): y"},      # duplicate scope — ordered-unique
        {"subject": "docs(readme): z"},
        {"subject": "v0.27.0: a prior release commit"},  # must NOT count
    ]
    lvl, themes = rd.decide_level(commits)
    assert lvl == "minor"
    assert themes == ["arbiter", "readme"]  # dedup, order preserved, release-commit skipped


def test_next_version_arithmetic():
    rd = _load()
    assert rd.next_version("v0.26.0", "patch") == "0.26.1"
    assert rd.next_version("v0.26.0", "minor") == "0.27.0"
    assert rd.next_version("v0.26.3", "major") == "1.0.0"
    # no tag yet → base 0.0.0
    assert rd.next_version(None, "minor") == "0.1.0"
    assert rd.next_version(None, "patch") == "0.0.1"
    # a malformed tag falls back to 0.0.0 base, never crashes
    assert rd.next_version("not-a-tag", "minor") == "0.1.0"


# ---- the should-release predicate (pure, synthetic payloads) ---------------

def _payload(**over) -> dict:
    """A green release_context-shaped payload; override fields per test."""
    base = {
        "last_tag": "v0.26.0",
        "commits_since_tag": [{"subject": "feat(x): a"}, {"subject": "fix(y): b"}],
        "clean_tree": True,
        "version_files": {"drift": False},
        "ci_on_head": {"status": "green"},
        "workflows_parse_ok": {"ok": True},
    }
    base.update(over)
    return base


def test_decide_releases_on_a_green_base_with_commits():
    rd = _load()
    v = rd.decide(_payload(), require_ci_green=True)
    assert v["decision"] == "release"
    assert v["level"] == "minor"          # the feat wins
    assert v["next_version"] == "0.27.0"
    assert v["n_commits"] == 2
    assert v["blockers"] == []


def test_decide_holds_when_nothing_to_ship():
    rd = _load()
    v = rd.decide(_payload(commits_since_tag=[]), require_ci_green=True)
    assert v["decision"] == "hold"
    assert "NOTHING_TO_SHIP" in v["blockers"]
    assert v["next_version"] is None


def test_decide_holds_on_a_red_ci_base():
    rd = _load()
    v = rd.decide(_payload(ci_on_head={"status": "red"}), require_ci_green=True)
    assert v["decision"] == "hold"
    assert "CI_BASE_RED" in v["blockers"]


def test_decide_holds_on_unparseable_workflow_and_on_drift():
    rd = _load()
    v = rd.decide(_payload(workflows_parse_ok={"ok": False}), require_ci_green=True)
    assert v["decision"] == "hold" and "WORKFLOW_UNPARSEABLE" in v["blockers"]
    v = rd.decide(_payload(version_files={"drift": True}), require_ci_green=True)
    assert v["decision"] == "hold" and "VERSION_DRIFT" in v["blockers"]


def test_unknown_ci_is_soft_pass_unless_required():
    rd = _load()
    # default (soft): unknown CI does not block
    v = rd.decide(_payload(ci_on_head={"status": "unknown"}), require_ci_green=False)
    assert v["decision"] == "release"
    # strict: unknown CI is a blocker
    v = rd.decide(_payload(ci_on_head={"status": "unknown"}), require_ci_green=True)
    assert v["decision"] == "hold" and "CI_STATE_UNKNOWN" in v["blockers"]


# ---- the CLI contract (subprocess) -----------------------------------------

def test_cli_emits_valid_json_and_a_decision_exit_code():
    """The live CLI over the real repo: it must emit a well-formed verdict and
    use the documented exit codes (0=release, 2=hold). We assert the SHAPE and
    the exit-code contract, not the decision itself (the live CI base varies)."""
    proc = subprocess.run(
        [sys.executable, str(_DECIDE_PY), "--json"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert proc.returncode in (0, 2), f"unexpected exit {proc.returncode}: {proc.stderr[:300]}"
    verdict = json.loads(proc.stdout)
    for key in ("decision", "level", "next_version", "last_tag", "n_commits",
                "reason", "themes", "blockers"):
        assert key in verdict, f"missing key {key!r} in verdict"
    assert verdict["decision"] in ("release", "hold")
    # exit code agrees with the decision
    assert (proc.returncode == 0) == (verdict["decision"] == "release")
