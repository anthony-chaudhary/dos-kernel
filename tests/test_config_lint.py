"""CFL — the config-integrity linter (docs/227, G1 from the docs/189 CC audit).

These tests pin the pure verdict that finds DEAD POLICY in the lane taxonomy +
reason registry — the `detectUnreachableRules` analogue aimed at DOS's registries.
The load-bearing cases:

  * each closed `LintKind` fires on a crafted-bad config, and ONLY when it should;
  * the subtle SHADOW (strict subset → dead) vs. OVERLAP (incidental intersection
    → order-sensitive) distinction (docs/227 §3) — including the edge cases the
    first draft got wrong (identical regions are overlap NOT shadow; a lane beside a
    universal `**/*` concurrent lane is shadowed);
  * the clean fixtures (this repo / the reference job taxonomy / a foreign config)
    lint to ZERO findings — the dogfood;
  * purity (no I/O), the no-host litmus (no finding string hardcodes a lane name),
    severity ordering, and the `dos lint` verb's verdict-IS-exit-code contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dos import config_lint as cl
from dos import reasons
from dos.config import LaneTaxonomy
from dos.config_lint import (
    Finding,
    LintKind,
    Severity,
    has_error,
    lint,
    lint_lanes,
    lint_reasons,
)


def _kinds(findings) -> set:
    return {f.kind for f in findings}


def _by_kind(findings, kind):
    return [f for f in findings if f.kind is kind]


def _repo_taxonomy() -> LaneTaxonomy:
    """This repo's own lane taxonomy (mirrors the `[lanes]` block of its dos.toml).
    Built as a literal rather than read off disk so the test is hermetic — the
    dogfood claim is 'this SHAPE lints clean,' independent of cwd."""
    return LaneTaxonomy(
        concurrent=("benchmark", "docs", "examples", "scripts", "spikes", "src", "tests"),
        exclusive=("global",),
        autopick=("benchmark", "docs", "examples", "scripts", "spikes", "src", "tests"),
        trees={
            "benchmark": ["benchmark/**"], "docs": ["docs/**"],
            "examples": ["examples/**"], "scripts": ["scripts/**"],
            "spikes": ["spikes/**"], "src": ["src/**"], "tests": ["tests/**"],
            "global": ["**/*"],
        },
    )


# ===========================================================================
# 1. LANE_WITHOUT_TREE — a concurrent/autopick lane with no tree.
# ===========================================================================


def test_treeless_concurrent_lane_is_error():
    tx = LaneTaxonomy(
        concurrent=("api", "worker"), autopick=("api",),
        trees={"api": ["src/api/**"]},  # worker has no tree
    )
    fs = lint_lanes(tx)
    hits = _by_kind(fs, LintKind.LANE_WITHOUT_TREE)
    assert [f.subject for f in hits] == ["worker"]
    assert hits[0].severity is Severity.ERROR
    assert "no tree" in hits[0].detail


def test_treeless_exclusive_lane_is_NOT_a_finding():
    """The arbiter admits an exclusive lane on liveness alone — it never consults a
    tree, so a treeless `global`/`infra` is correct, not dead (the bug
    `_treeless_lane_findings` already learned)."""
    tx = LaneTaxonomy(
        concurrent=("api",), exclusive=("infra",), autopick=("api",),
        trees={"api": ["src/api/**"]},  # infra deliberately treeless
    )
    fs = lint_lanes(tx)
    assert not any(f.subject == "infra" and f.kind is LintKind.LANE_WITHOUT_TREE
                   for f in fs)


def test_treeless_lane_names_every_referencing_role():
    """A lane treeless in BOTH concurrent and autopick names both roles, once."""
    tx = LaneTaxonomy(concurrent=("x",), autopick=("x",), trees={})
    hits = _by_kind(lint_lanes(tx), LintKind.LANE_WITHOUT_TREE)
    assert len(hits) == 1
    assert "concurrent/autopick" in hits[0].detail


# ===========================================================================
# 2. LANE_BOTH_CONCURRENT_AND_EXCLUSIVE — a contradiction.
# ===========================================================================


def test_lane_in_both_concurrent_and_exclusive_is_error():
    tx = LaneTaxonomy(
        concurrent=("api", "dup"), exclusive=("dup",), autopick=("api",),
        trees={"api": ["src/api/**"], "dup": ["src/dup/**"]},
    )
    hits = _by_kind(lint_lanes(tx), LintKind.LANE_BOTH_CONCURRENT_AND_EXCLUSIVE)
    assert [f.subject for f in hits] == ["dup"]
    assert hits[0].severity is Severity.ERROR


# ===========================================================================
# 3 + 4. Dangling references (autopick / alias target undeclared).
# ===========================================================================


def test_autopick_lane_undeclared_is_warn():
    tx = LaneTaxonomy(
        concurrent=("api",), autopick=("api", "ghost"),
        trees={"api": ["src/api/**"]},
    )
    hits = _by_kind(lint_lanes(tx), LintKind.AUTOPICK_LANE_UNDECLARED)
    assert [f.subject for f in hits] == ["ghost"]
    assert hits[0].severity is Severity.WARN


def test_alias_target_undeclared_is_warn():
    tx = LaneTaxonomy(
        concurrent=("api",), autopick=("api",),
        trees={"api": ["src/api/**"]},
        aliases={"svc": "nowhere"},
    )
    hits = _by_kind(lint_lanes(tx), LintKind.ALIAS_TARGET_UNDECLARED)
    assert [f.subject for f in hits] == ["svc"]  # subject is the keyword typed
    assert "nowhere" in hits[0].detail


def test_alias_to_a_declared_lane_is_clean():
    tx = LaneTaxonomy(
        concurrent=("api",), exclusive=("infra",), autopick=("api",),
        trees={"api": ["src/api/**"], "infra": ["deploy/**"]},
        aliases={"svc": "api", "ops": "infra"},  # both targets declared
    )
    assert not _by_kind(lint_lanes(tx), LintKind.ALIAS_TARGET_UNDECLARED)


# ===========================================================================
# 5 + 6. The subtle one: SHADOW (subset) vs OVERLAP (intersection). docs/227 §3.
# ===========================================================================


def test_strict_subset_is_shadow_not_overlap():
    """`src/api` ⊂ `src` → the narrow lane is DEAD (shadowed), reported as SHADOW,
    NOT as an incidental overlap."""
    tx = LaneTaxonomy(
        concurrent=("api", "core"), autopick=("api", "core"),
        trees={"api": ["src/api/**"], "core": ["src/**"]},
    )
    fs = lint_lanes(tx)
    shadow = _by_kind(fs, LintKind.LANE_REGION_SHADOWED)
    assert [f.subject for f in shadow] == ["api"]          # the DEAD (smaller) lane
    assert "core" in shadow[0].detail                       # named as the swallower
    assert not _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)  # NOT also overlap


def test_deeply_nested_subset_is_shadow():
    tx = LaneTaxonomy(
        concurrent=("a", "b"), autopick=("a", "b"),
        trees={"a": ["src/api/**"], "b": ["src/api/helpers/**"]},  # b ⊂ a
    )
    shadow = _by_kind(lint_lanes(tx), LintKind.LANE_REGION_SHADOWED)
    assert [f.subject for f in shadow] == ["b"]


def test_lane_beside_universal_concurrent_lane_is_shadowed():
    """A concurrent lane whose peer is a whole-repo `**/*` lane is dead — the
    universal region swallows it. (The universal lane usually belongs in exclusive;
    surfacing that is the point.)"""
    tx = LaneTaxonomy(
        concurrent=("whole", "narrow"), autopick=("whole", "narrow"),
        trees={"whole": ["**/*"], "narrow": ["src/api/**"]},
    )
    shadow = _by_kind(lint_lanes(tx), LintKind.LANE_REGION_SHADOWED)
    assert [f.subject for f in shadow] == ["narrow"]


def test_identical_regions_are_overlap_not_shadow():
    """Two lanes with the IDENTICAL tree are mutually contained → not strict →
    reported as OVERLAP (order-sensitive), never shadow (neither is 'the smaller')."""
    tx = LaneTaxonomy(
        concurrent=("a", "b"), autopick=("a", "b"),
        trees={"a": ["src/**"], "b": ["src/**"]},
    )
    fs = lint_lanes(tx)
    assert not _by_kind(fs, LintKind.LANE_REGION_SHADOWED)
    assert _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)


def test_incidental_intersection_is_overlap_not_shadow():
    """Lanes that share one prefix but where NEITHER is a subset → OVERLAP."""
    tx = LaneTaxonomy(
        concurrent=("a", "b"), autopick=("a", "b"),
        trees={"a": ["src/api/**", "docs/**"], "b": ["src/api/**", "tests/**"]},
    )
    fs = lint_lanes(tx)
    overlap = _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)
    assert len(overlap) == 1
    assert overlap[0].subject == "a+b"
    assert not _by_kind(fs, LintKind.LANE_REGION_SHADOWED)


def test_a_pair_is_reported_by_exactly_one_of_shadow_or_overlap():
    """No pair is ever BOTH shadowed and overlapping (docs/227 §3 invariant)."""
    tx = LaneTaxonomy(
        concurrent=("a", "b", "c", "d"), autopick=("a", "b", "c", "d"),
        trees={"a": ["src/**"], "b": ["src/api/**"],       # b ⊂ a → shadow
               "c": ["web/x/**", "shared/**"],
               "d": ["web/y/**", "shared/**"]},             # c,d share shared/ → overlap
    )
    fs = lint_lanes(tx)
    shadow_subjects = {f.subject for f in _by_kind(fs, LintKind.LANE_REGION_SHADOWED)}
    overlap_subjects = {f.subject for f in _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)}
    # b is the dead one; c+d are the overlapping pair. Disjoint reports.
    assert shadow_subjects == {"b"}
    assert overlap_subjects == {"c+d"}


def test_exclusive_lanes_do_not_overlap_or_shadow():
    """Only CONCURRENT lanes enter the shadow/overlap algebra — an exclusive lane
    runs alone, so its region containment is moot."""
    tx = LaneTaxonomy(
        concurrent=("api",), exclusive=("whole",), autopick=("api",),
        trees={"api": ["src/api/**"], "whole": ["**/*"]},  # whole is exclusive
    )
    fs = lint_lanes(tx)
    assert not _by_kind(fs, LintKind.LANE_REGION_SHADOWED)
    assert not _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)


def test_root_meta_doc_lane_is_disjoint_and_clean():
    """The `meta` lane (issue #8) — an EXPLICIT root-meta-doc file list — is disjoint
    from the dir-prefixed lanes and lints clean (no shadow/overlap), even though its
    region is a strict subset of the exclusive `global`.

    The fix MUST use an explicit list, NOT a `*.md` glob: the tree algebra is
    prefix-based, so a root glob normalizes to the whole tree and would collide with
    every lane. This pins both halves — the explicit list is disjoint where a glob
    would not be, and the resulting taxonomy is clean."""
    from dos._tree import lane_trees_disjoint
    meta = ["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "README.md"]
    # Disjoint from every dir-prefixed lane (so it runs concurrently with them).
    for other in (["src/**"], ["docs/**"], ["tests/**"], [".github/**"]):
        assert lane_trees_disjoint(meta, other), f"meta must be disjoint from {other}"
    # A root `*.md` GLOB would NOT be disjoint — the prefix algebra treats it as root.
    assert not lane_trees_disjoint(["*.md"], ["docs/**"]), (
        "a root *.md glob normalizes to the whole tree — that is WHY the lane uses an "
        "explicit file list, not a glob")
    # The taxonomy with `meta` beside the dir lanes and the exclusive `global` lints
    # clean: `meta` ⊂ `global`, but `global` is EXCLUSIVE so it never enters the
    # shadow/overlap algebra (the same reason `src`/`docs` don't trip on it).
    tx = LaneTaxonomy(
        concurrent=("src", "docs", "meta"), exclusive=("global",),
        autopick=("src", "docs", "meta"),
        trees={"src": ["src/**"], "docs": ["docs/**"], "meta": meta,
               "global": ["**/*"]},
    )
    fs = lint_lanes(tx)
    assert not _by_kind(fs, LintKind.LANE_REGION_SHADOWED)
    assert not _by_kind(fs, LintKind.CONCURRENT_LANES_OVERLAP)
    assert not _by_kind(fs, LintKind.LANE_WITHOUT_TREE)


def test_treeless_lane_not_double_reported_as_shadow():
    """A treeless concurrent lane is a LANE_WITHOUT_TREE error — it must NOT also
    surface as shadowed/overlapping (it has no tree to compare)."""
    tx = LaneTaxonomy(
        concurrent=("api", "bare"), autopick=("api",),
        trees={"api": ["src/api/**"]},  # bare treeless
    )
    fs = lint_lanes(tx)
    assert _by_kind(fs, LintKind.LANE_WITHOUT_TREE)
    assert not any(f.subject in ("bare", "api+bare", "bare+api")
                   for f in fs
                   if f.kind in (LintKind.LANE_REGION_SHADOWED,
                                 LintKind.CONCURRENT_LANES_OVERLAP))


# ===========================================================================
# 7. REASON_SEE_ALSO_DANGLES — a dead man-page cross-ref.
# ===========================================================================


def test_reason_see_also_dangling_lane_is_info():
    from dos.reasons import ReasonSpec, ReasonRegistry
    reg = ReasonRegistry(specs=(
        ReasonSpec(token="FOO", category="MISROUTE", see_also=("lane ghostlane",)),
    ))
    hits = lint_reasons(reg, known_lanes={"api", "worker"})
    assert [f.subject for f in hits] == ["FOO"]
    assert hits[0].severity is Severity.INFO
    assert "ghostlane" in hits[0].detail


def test_reason_see_also_to_declared_lane_is_clean():
    from dos.reasons import ReasonSpec, ReasonRegistry
    reg = ReasonRegistry(specs=(
        ReasonSpec(token="FOO", category="MISROUTE", see_also=("lane api",)),
    ))
    assert lint_reasons(reg, known_lanes={"api"}) == ()


def test_reason_see_also_placeholder_and_nonlane_refs_are_skipped():
    """`lane <holder>` (a templated placeholder) and non-`lane ` refs (oracles, verbs)
    are documentation, not concrete lanes — never flagged."""
    from dos.reasons import ReasonSpec, ReasonRegistry
    reg = ReasonRegistry(specs=(
        ReasonSpec(token="A", category="MISROUTE", see_also=("lane <holder>",)),
        ReasonSpec(token="B", category="MISROUTE", see_also=("oracle picker_oracle",)),
        ReasonSpec(token="C", category="MISROUTE", see_also=("dos man lane",)),
        ReasonSpec(token="D", category="MISROUTE", see_also=("lane",)),  # bare word
    ))
    assert lint_reasons(reg, known_lanes=set()) == ()


# ===========================================================================
# The dogfood — the shipped registries lint clean.
# ===========================================================================


def test_base_reasons_have_no_dangling_lane_refs_against_repo_taxonomy():
    """BASE_REASONS' `see_also` lane pointers all resolve against THIS repo's lanes
    — the dead `lane orchestration` ref was the linter's first real catch (fixed)."""
    repo = _repo_taxonomy()
    known = set(repo.concurrent) | set(repo.exclusive)
    assert lint_reasons(reasons.BASE_REASONS, known_lanes=known) == ()


def test_repo_taxonomy_lints_clean():
    """This repo's own lane taxonomy + reason registry → ZERO findings (the
    CLAUDE.md dogfood: `dos doctor --check` is clean here)."""
    fs = lint(_repo_taxonomy(), reasons.BASE_REASONS)
    assert fs == (), [f.line() for f in fs]


def test_job_taxonomy_lints_clean():
    """The reference job taxonomy passes its own integrity rail."""
    from dos.drivers import job
    fs = lint(job.JOB_LANE_TAXONOMY, reasons.BASE_REASONS)
    assert fs == (), [f.line() for f in fs]


# ===========================================================================
# Ordering, purity, the no-host litmus, the top-level lint.
# ===========================================================================


def test_findings_sorted_error_then_warn_then_info():
    tx = LaneTaxonomy(
        concurrent=("dup", "api", "narrow", "core"), exclusive=("dup",),
        autopick=("api", "ghost"),
        trees={"dup": ["d/**"], "api": ["src/api/**"], "core": ["src/**"],
               "narrow": ["src/**"]},
        aliases={"x": "nowhere"},
    )
    from dos.reasons import ReasonSpec, ReasonRegistry
    reg = ReasonRegistry(specs=(
        ReasonSpec(token="FOO", category="MISROUTE", see_also=("lane ghost2",)),
    ))
    fs = lint(tx, reg)
    sevs = [f.severity for f in fs]
    # error(s) come first, then warn(s), then info(s) — non-decreasing severity rank.
    ranks = [{"error": 0, "warn": 1, "info": 2}[s.value] for s in sevs]
    assert ranks == sorted(ranks)
    assert Severity.ERROR in sevs and Severity.WARN in sevs and Severity.INFO in sevs


def test_lint_is_pure_no_io(monkeypatch):
    """`lint` reads only the data it is handed — no file/subprocess/clock. We prove
    it by banning `open` and `subprocess.run` for the duration of the call."""
    import builtins
    def _boom(*a, **k):  # pragma: no cover - only hit on a regression
        raise AssertionError("config_lint did I/O")
    monkeypatch.setattr(builtins, "open", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    tx = LaneTaxonomy(concurrent=("a",), autopick=("a",), trees={"a": ["x/**"]})
    assert lint(tx, reasons.BASE_REASONS) == lint(tx, reasons.BASE_REASONS)


def test_no_finding_string_hardcodes_a_lane_name():
    """Law 1 at the finding level: the linter names the lanes IT IS HANDED, never a
    host lane baked into its source. A crafted taxonomy with unique sentinel names
    must see exactly those names — and the kinds/fixes must mention none of the
    reference host lanes."""
    tx = LaneTaxonomy(
        concurrent=("zeta", "zetacore"), autopick=("zeta", "phantomlane"),
        trees={"zeta": ["q/**"], "zetacore": ["q/core/**"]},
        aliases={"kk": "voidlane"},
    )
    fs = lint(tx)
    blob = " ".join(f.line() for f in fs)
    for host_lane in ("apply", "tailor", "discovery", "fleet"):
        assert host_lane not in blob
    # the sentinel names DO appear (it reports what it was handed)
    assert "phantomlane" in blob and "voidlane" in blob and "zetacore" in blob


def test_lint_without_registry_returns_lane_findings_only():
    tx = LaneTaxonomy(concurrent=("a",), autopick=("a",), trees={})
    fs = lint(tx)  # registry omitted
    assert _kinds(fs) == {LintKind.LANE_WITHOUT_TREE}


def test_has_error_predicate():
    err = (Finding(LintKind.LANE_WITHOUT_TREE, Severity.ERROR, "x", "d", "f"),)
    warn = (Finding(LintKind.AUTOPICK_LANE_UNDECLARED, Severity.WARN, "x", "d", "f"),)
    assert has_error(err) is True
    assert has_error(warn) is False
    assert has_error(()) is False


def test_finding_to_dict_roundtrips_fields():
    f = Finding(LintKind.LANE_WITHOUT_TREE, Severity.ERROR, "lane1", "detail", "fix")
    d = f.to_dict()
    assert d == {"kind": "LANE_WITHOUT_TREE", "severity": "error",
                 "subject": "lane1", "detail": "detail", "fix": "fix"}


# ===========================================================================
# The `dos lint` verb — verdict IS the exit code.
# ===========================================================================


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


_CLEAN_TOML = (
    "workspace = '.'\n"
    "[lanes]\n"
    "concurrent = ['api', 'worker']\nexclusive = ['infra']\n"
    "autopick = ['api', 'worker']\n"
    "[lanes.trees]\n"
    "api = ['src/api/**']\nworker = ['src/worker/**']\ninfra = ['deploy/**']\n"
)

_DIRTY_TOML = (
    "workspace = '.'\n"
    "[lanes]\n"
    "concurrent = ['api', 'apicore', 'dup']\nexclusive = ['dup']\n"
    "autopick = ['api', 'ghost']\n"
    "[lanes.trees]\n"
    "api = ['src/api/**']\napicore = ['src/api/core/**']\ndup = ['src/dup/**']\n"
)


def test_lint_cli_clean_exits_zero(tmp_path: Path):
    (tmp_path / "dos.toml").write_text(_CLEAN_TOML, encoding="utf-8")
    r = _run_cli("lint", "--workspace", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "clean" in r.stdout.lower()


def test_lint_cli_dirty_exits_one(tmp_path: Path):
    (tmp_path / "dos.toml").write_text(_DIRTY_TOML, encoding="utf-8")
    r = _run_cli("lint", "--workspace", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    # the contradiction (error), the shadow (warn), and a dangling ref all surface
    assert "LANE_BOTH_CONCURRENT_AND_EXCLUSIVE" in r.stdout
    assert "LANE_REGION_SHADOWED" in r.stdout


def test_lint_cli_json_carries_counts(tmp_path: Path):
    (tmp_path / "dos.toml").write_text(_DIRTY_TOML, encoding="utf-8")
    r = _run_cli("lint", "--workspace", str(tmp_path), "--json", cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["counts"]["error"] >= 1
    assert payload["counts"]["warn"] >= 1
    kinds = {f["kind"] for f in payload["findings"]}
    assert "LANE_REGION_SHADOWED" in kinds


def test_lint_cli_strict_gates_on_error_only(tmp_path: Path):
    """A config with ONLY warn findings: default exit 1, but `--strict` exits 0."""
    warn_only = (
        "workspace = '.'\n"
        "[lanes]\n"
        "concurrent = ['api', 'apicore']\n"
        "autopick = ['api', 'apicore']\n"
        "[lanes.trees]\n"
        "api = ['src/api/**']\napicore = ['src/api/core/**']\n"  # apicore ⊂ api → warn
    )
    (tmp_path / "dos.toml").write_text(warn_only, encoding="utf-8")
    default = _run_cli("lint", "--workspace", str(tmp_path), cwd=tmp_path)
    strict = _run_cli("lint", "--workspace", str(tmp_path), "--strict", cwd=tmp_path)
    assert default.returncode == 1, default.stdout      # warn gates by default
    assert strict.returncode == 0, strict.stdout        # warn does NOT gate --strict
    assert "LANE_REGION_SHADOWED" in default.stdout
