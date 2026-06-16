"""Pin the residual_review experiment (the next-generation diff).

These tests are the FALSIFIERS made executable. Each would go red if the
surface's claim were overstated:

  * `test_witnessed_commit_is_cleared` — a `diff-witnessed` claim must land in
    Band 0 with zero residual attention, or the surface isn't subtracting what
    the kernel already cleared.
  * `test_subject_only_is_residual` — a claim the diff did NOT witness must land
    in Band 1 (residual). This is the whole point: the residual is where the
    machine ran dry.
  * `test_no_claim_is_unverifiable_not_residual` — an ABSTAIN (no claim) must NOT
    pollute the must-read residual; it drops to the lower-priority band. Folding
    the two would dilute the signal the surface exists to concentrate.
  * `test_semantic_is_advisory_and_a_subset_of_cleared` — Band 2 may only flag
    ALREADY-cleared commits, and may only ask for MORE eyes. It can never rescue
    a residual item or block one — the one-sided, fail-to-ABSTAIN guarantee.
  * `test_bands_partition_exactly` — every commit lands in exactly one of the
    three attention bands (cleared / residual / unverifiable); none is dropped or
    double-counted, so a reviewer who reads the residual has read everything the
    kernel couldn't.
  * `test_projection_equals_the_shipped_verdict` — on real git history the bands
    are a pure re-projection of `commit_audit.audit_range`, not a recomputation.
    The residual carries ZERO new trust over the shipped tool.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from dos.commit_audit import (  # noqa: E402
    ClaimKind,
    ClaimVerdict,
    Verdict,
    Witness,
    audit_range,
)
from residual_review import (  # noqa: E402
    ReviewPlan,
    build_plan,
    plan_review,
    render_walk,
)


def _v(sha, verdict, witness, kind=ClaimKind.CODE_EFFECT, source=(), reason="r"):
    """A synthetic ClaimVerdict — the kernel's output, hand-built for the pure layer."""
    return ClaimVerdict(
        sha=sha, verdict=verdict, claim_kind=kind, witness=witness,
        reason=reason, source_files=tuple(source),
    )


def test_witnessed_commit_is_cleared():
    """The falsifier: a diff-witnessed OK claim must cost ~0 review attention."""
    v = _v("aaa", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/foo.py",))
    plan = plan_review([v], "range")
    assert [i.sha for i in plan.cleared] == ["aaa"]
    assert plan.residual == []
    assert plan.unverifiable == []
    assert plan.cleared_rate == 1.0  # the whole checkable set was cleared


def test_subject_only_is_residual():
    """The headline: a claim the diff did NOT witness is the human's 100%."""
    v = _v("bbb", Verdict.CLAIM_UNWITNESSED, Witness.SUBJECT_ONLY,
           reason="code verb but 0 source files touched")
    plan = plan_review([v], "range")
    assert [i.sha for i in plan.residual] == ["bbb"]
    assert plan.cleared == []
    assert plan.cleared_rate == 0.0  # nothing cleared — all attention required


def test_data_witnessed_is_cleared_not_residual():
    """A data-witnessed OK claim (a lockfile/config change that IS the effect) is
    WITNESSED on the kernel's own weaker rung (docs/214 §1) — so it clears, and
    must NOT inflate the must-read residual. Dumping it in residual would
    contradict 'residual = what the kernel could not witness'."""
    v = _v("ddw", Verdict.OK, Witness.DATA_WITNESSED, source=("poetry.lock",),
            reason="data change is the claimed effect")
    plan = plan_review([v], "range")
    assert [i.sha for i in plan.cleared] == ["ddw"]
    assert plan.residual == []
    # but the cleared item carries its weaker rung so a reviewer can choose to look
    assert plan.cleared[0].witness == "data-witnessed"


def test_no_claim_is_unverifiable_not_residual():
    """An ABSTAIN must NOT sit in the must-read residual — it's lower priority."""
    v = _v("ccc", Verdict.ABSTAIN, Witness.ABSTAIN, kind=ClaimKind.NONE,
           reason="subject makes no checkable claim")
    plan = plan_review([v], "range")
    assert [i.sha for i in plan.unverifiable] == ["ccc"]
    assert plan.residual == []
    # An ABSTAIN is not checkable, so it never enters the cleared-rate denominator.
    assert plan.checkable == 0


def test_semantic_is_advisory_and_a_subset_of_cleared():
    """Band 2 may only re-flag a CLEARED commit, and only ask for MORE eyes."""
    # A witnessed commit touching a concurrency primitive -> advisory flag.
    risky = _v("ddd", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/dos/lease.py",))
    # A witnessed commit on a plain file -> cleared, no flag.
    plain = _v("eee", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/dos/render.py",))
    plan = plan_review([risky, plain], "range")

    sem_shas = {i.sha for i in plan.semantic}
    cleared_shas = {i.sha for i in plan.cleared}
    # The advisory set is a strict SUBSET of the cleared set — never a residual.
    assert sem_shas <= cleared_shas
    assert "ddd" in sem_shas and "eee" not in sem_shas
    # It only ADDS a note; the underlying verdict is untouched and still cleared.
    assert "ddd" in cleared_shas
    assert plan.semantic[0].semantic_flags  # carries the why, never a block


def test_semantic_never_touches_a_residual_item():
    """The one-sided guarantee: a risky-surface file under an UNWITNESSED claim
    stays in the residual and is NOT softened by the advisory lens."""
    v = _v("fff", Verdict.CLAIM_UNWITNESSED, Witness.SUBJECT_ONLY,
           source=("src/dos/lease.py",), reason="subject-only")
    plan = plan_review([v], "range")
    assert [i.sha for i in plan.residual] == ["fff"]
    assert plan.semantic == []  # the lens runs only over cleared commits


def test_bands_partition_exactly():
    """Every commit lands in exactly one attention band — none dropped or doubled."""
    vs = [
        _v("a", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/x.py",)),
        _v("b", Verdict.CLAIM_UNWITNESSED, Witness.SUBJECT_ONLY),
        _v("c", Verdict.ABSTAIN, Witness.ABSTAIN, kind=ClaimKind.NONE),
        _v("d", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/dos/auth.py",)),
    ]
    plan = plan_review(vs, "range")
    attention = (
        [i.sha for i in plan.cleared]
        + [i.sha for i in plan.residual]
        + [i.sha for i in plan.unverifiable]
    )
    assert sorted(attention) == ["a", "b", "c", "d"]  # exhaustive
    assert len(attention) == len(set(attention)) == 4  # disjoint, no doubles
    # Band 2 is an OVERLAY, not a partition member — it does not add to the count.
    assert all(i.sha in [c.sha for c in plan.cleared] for i in plan.semantic)


def test_walk_shows_only_the_residual_never_the_cleared():
    """The navigation surface steps through the residual; cleared commits never
    appear (showing them would defeat the ~0-attention promise)."""
    cleared = _v("clr", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/x.py",))
    resid = _v("res", Verdict.CLAIM_UNWITNESSED, Witness.SUBJECT_ONLY,
               reason="code verb but 0 source files")
    plan = plan_review([cleared, resid], "range")
    out = render_walk(plan, root=".")
    assert "res" in out  # the residual card is present
    assert "clr" not in out  # the cleared commit is NOT walked
    assert "1 card" in out  # exactly one card to review
    assert "why residual" in out  # the card explains why the kernel couldn't clear it


def test_walk_on_empty_residual_is_a_clean_bill():
    """A fully-cleared range walks to a single 'nothing to review' line."""
    cleared = _v("clr", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/x.py",))
    plan = plan_review([cleared], "range")
    out = render_walk(plan, root=".")
    assert "residual is empty" in out
    assert "clr" not in out  # still never lists the cleared commit


def _has_git_history(n: int = 5) -> bool:
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, check=False,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        )
        return out.returncode == 0 and int(out.stdout.strip() or 0) >= n
    except (OSError, ValueError):
        return False


@pytest.mark.skipif(not _has_git_history(), reason="needs real git history")
def test_subjects_decode_as_utf8_not_platform_default():
    """Regression (adversarial review, 2026-06-16): git emits UTF-8 subjects;
    decoding with the platform default (cp1252 on Windows) mojibakes an
    international contributor's subject INTO the data. `_subjects` must pin UTF-8
    (mirroring commit_audit._git), so no subject carries the cp1252 signature."""
    from residual_review import _subjects

    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    subs = _subjects("HEAD~50..HEAD", root)
    # 'â€' is the cp1252 mis-decode of a UTF-8 em-dash/quote lead byte (e2 80 ..).
    mojibake = [s for s in subs.values() if "â€" in s or "Ã\x83" in s]
    assert mojibake == [], f"cp1252 mojibake leaked into subjects: {mojibake[:2]}"


@pytest.mark.skipif(not _has_git_history(), reason="needs real git history")
def test_projection_equals_the_shipped_verdict():
    """The residual is the shipped commit-audit output re-projected, no new trust.

    Every sha the surface marks as residual must be a sha the shipped
    `audit_range` graded as NOT (diff-witnessed AND OK) — i.e. the projection
    cannot invent a residual the kernel didn't already produce, and cannot hide
    one it did.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    rng = "HEAD~30..HEAD"
    verdicts = audit_range(rng, root=root)
    plan = build_plan(rng, root=root)

    cleared = {v.sha for v in verdicts
               if v.verdict is Verdict.OK
               and v.witness in (Witness.DIFF_WITNESSED, Witness.DATA_WITNESSED)}
    abstain = {v.sha for v in verdicts if v.verdict is Verdict.ABSTAIN}
    expected_residual = {v.sha for v in verdicts} - cleared - abstain

    assert {i.sha for i in plan.cleared} == cleared
    assert {i.sha for i in plan.residual} == expected_residual
    assert {i.sha for i in plan.unverifiable} == abstain
