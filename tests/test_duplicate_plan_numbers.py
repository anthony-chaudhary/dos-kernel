"""docs/317 P1 — duplicate plan numbers must not cross-witness (issue #80).

Two concurrently-working agents can mint the same `docs/NN` number. The grep
rung's trailer spelling reduced a full plan id to its number head, so a bare
`(docs/NN Pk)` stamp witnessed EITHER same-numbered plan — one loop's stamps
satisfied another loop's claims (an accidental forgery channel, live on
2026-06-12 with two docs/306 plans).

The rule pinned here is slug-or-nothing, in the refuse-more-only direction:

  * while ≥ 2 DECLARED plans share a number head, a bare-head stamp
    witnesses NO plan and a bare-head QUERY answers a typed ambiguity
    naming both files;
  * a slug-carrying stamp always witnesses exactly its own plan;
  * a number carried by ONE declared plan behaves byte-identically to
    before (the short trailer spelling keeps working);
  * a workspace with no plans builds an empty index — `verify` still needs
    no plan (`test_verify_no_plan.py` is the standing pin).

Fixture discipline: a throwaway repo per test; this suite never touches the
kernel repo's own state.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

import pytest

from dos import oracle
from dos.config import default_config
from dos.phase_shipped import _series_variants, duplicate_plan_heads
from dos.stamp import GENERIC_STAMP_CONVENTION

_TRAILER_CONVENTION = dataclasses.replace(GENERIC_STAMP_CONVENTION, trailer_stamp=True)

PLAN_A = "docs/306_alpha-widget-plan"
PLAN_B = "docs/306_beta-gadget-plan"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _repo_with_plans(root: Path, basenames: "list[str]") -> Path:
    """A git repo whose `docs/` declares the given plan files (default glob)."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    docs = root / "docs"
    docs.mkdir()
    for name in basenames:
        (docs / name).write_text(f"# {name}\n\n### P1\n\n### P2\n", encoding="utf-8")
    _git(root, "add", "docs")
    _git(root, "commit", "-m", "chore: declare the plan portfolio")
    return root


def _cfg(repo: Path):
    return dataclasses.replace(default_config(repo), stamp=_TRAILER_CONVENTION)


@pytest.fixture()
def two_306(tmp_path: Path) -> Path:
    return _repo_with_plans(
        tmp_path, ["306_alpha-widget-plan.md", "306_beta-gadget-plan.md"]
    )


# ---------------------------------------------------------------------------
# The pure folds.
# ---------------------------------------------------------------------------


def test_duplicate_plan_heads_pure():
    dupes = duplicate_plan_heads(
        [
            "306_alpha-widget-plan.md",
            "docs/306_beta-gadget-plan.md",  # dir + .md both stripped
            "310_unique-plan.md",            # unique number → absent
            "README.md",                     # no <digits>_ head → ignored
            "my_plan.md",                    # head has no digit → ignored
        ]
    )
    assert dupes == {
        "306": ("306_alpha-widget-plan", "306_beta-gadget-plan"),
    }
    assert duplicate_plan_heads([]) == {}
    assert duplicate_plan_heads(["82_liveness-plan.md"]) == {}


def test_series_variants_drop_ambiguous_head():
    # Without the ambiguous set the short spelling is offered (docs/289)…
    assert any("306" == v.replace("\\", "") .rsplit("/", 1)[-1]
               for v in _series_variants("docs/306_alpha-widget-plan"))
    # …with it, slug-or-nothing: only the (regex-escaped) full id survives.
    variants = _series_variants("docs/306_alpha-widget-plan", {"306"})
    assert variants == [re.escape("docs/306_alpha-widget-plan")]
    # An unrelated head is untouched.
    assert len(_series_variants("docs/310_unique-plan", {"306"})) == 2


# ---------------------------------------------------------------------------
# End-to-end through the oracle (the surfaces a host actually calls).
# ---------------------------------------------------------------------------


def test_bare_head_stamp_witnesses_neither_plan(two_306: Path):
    """The issue #80 moment, refused: a `(docs/306 Pk)` trailer while two
    docs/306 plans exist witnesses NO plan — neither A nor B can close a
    phase off the other's (or its own) bare-number stamp."""
    _git(two_306, "commit", "--allow-empty", "-m",
         "feat: ship the widget (docs/306 P1)")
    cfg = _cfg(two_306)

    assert oracle.is_shipped(PLAN_A, "P1", cfg=cfg).shipped is False
    assert oracle.is_shipped(PLAN_B, "P1", cfg=cfg).shipped is False


def test_slug_stamp_witnesses_only_its_own_plan(two_306: Path):
    """A slug-carrying trailer stays a witness — for exactly one plan."""
    _git(two_306, "commit", "--allow-empty", "-m",
         "feat: gadget work (docs/306_beta-gadget-plan P2)")
    cfg = _cfg(two_306)

    v = oracle.is_shipped(PLAN_B, "P2", cfg=cfg)
    assert v.shipped is True, v
    assert oracle.is_shipped(PLAN_A, "P2", cfg=cfg).shipped is False


def test_bare_head_query_answers_typed_ambiguity(two_306: Path):
    """Asking about the bare head while it is shared has no honest answer:
    the verdict refuses with the `ambiguous-number` rung and names both
    files, instead of silently picking one plan's stamps."""
    _git(two_306, "commit", "--allow-empty", "-m",
         "feat: ship the widget (docs/306 P1)")
    cfg = _cfg(two_306)

    v = oracle.is_shipped("docs/306", "P1", cfg=cfg)
    assert v.shipped is False
    assert v.rung == "ambiguous-number", v
    assert "306_alpha-widget-plan" in v.summary
    assert "306_beta-gadget-plan" in v.summary


def test_unique_number_keeps_short_trailer_byte_identical(tmp_path: Path):
    """The no-regression direction: a number carried by ONE declared plan
    still resolves its bare-head trailer — today's behavior, untouched."""
    repo = _repo_with_plans(tmp_path, ["410_solo-plan.md"])
    _git(repo, "commit", "--allow-empty", "-m", "feat: solo work (docs/410 P1)")
    cfg = _cfg(repo)

    v = oracle.is_shipped("docs/410_solo-plan", "P1", cfg=cfg)
    assert v.shipped is True, v
    assert v.rung == "trailer"


# ---------------------------------------------------------------------------
# docs/317 P2 — the PLAN_NUMBER_DUPLICATE lint finding.
# ---------------------------------------------------------------------------


def test_lint_plans_pure():
    from dos import config_lint as cl

    dupes = {"306": ("306_alpha-widget-plan", "306_beta-gadget-plan")}
    findings = cl.lint_plans(dupes)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind is cl.LintKind.PLAN_NUMBER_DUPLICATE
    assert f.severity is cl.Severity.WARN
    assert f.subject == "306"
    # The disjoin move is a rename — the finding must name BOTH files.
    assert "306_alpha-widget-plan" in f.detail
    assert "306_beta-gadget-plan" in f.detail

    assert cl.lint_plans({}) == ()
    assert cl.lint_plans(None) == ()


def test_lint_threads_duplicate_plans(tmp_path: Path):
    """`lint()` carries the plan rail; omitted, the call is byte-identical."""
    from dos import config_lint as cl

    taxonomy = default_config(tmp_path).lanes
    base = cl.lint(taxonomy)
    with_dupes = cl.lint(
        taxonomy,
        duplicate_plans={"306": ("306_alpha-widget-plan", "306_beta-gadget-plan")},
    )
    new = [f for f in with_dupes if f.kind is cl.LintKind.PLAN_NUMBER_DUPLICATE]
    assert len(new) == 1
    assert set(with_dupes) - set(new) == set(base)
    assert not any(f.kind is cl.LintKind.PLAN_NUMBER_DUPLICATE for f in base)


def test_cli_lint_surfaces_duplicate_number(two_306: Path):
    """`dos lint --json` on a two-same-number workspace emits the finding
    (and gates: warn fails the default lint exit)."""
    import json as _json
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "dos.cli", "lint",
         "--workspace", str(two_306), "--json"],
        capture_output=True, text=True,
    )
    payload = _json.loads(proc.stdout)
    kinds = {f["kind"] for f in payload["findings"]}
    assert "PLAN_NUMBER_DUPLICATE" in kinds, payload
    dup = next(f for f in payload["findings"] if f["kind"] == "PLAN_NUMBER_DUPLICATE")
    assert dup["subject"] == "306"
    assert "306_alpha-widget-plan" in dup["detail"]
    assert "306_beta-gadget-plan" in dup["detail"]
    assert proc.returncode == 1  # warn gates the default lint verdict


def test_cli_lint_clean_without_duplicates(tmp_path: Path):
    """A unique-number workspace emits no plan-namespace finding."""
    import json as _json
    import sys

    repo = _repo_with_plans(tmp_path, ["410_solo-plan.md"])
    proc = subprocess.run(
        [sys.executable, "-m", "dos.cli", "lint",
         "--workspace", str(repo), "--json"],
        capture_output=True, text=True,
    )
    payload = _json.loads(proc.stdout)
    assert not any(
        f["kind"] == "PLAN_NUMBER_DUPLICATE" for f in payload["findings"]
    ), payload
