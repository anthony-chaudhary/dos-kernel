"""verify() works with NO phased plan — the load-bearing directional property.

A user must be able to ask the truth syscall "did (plan, phase) ship?" against a
plain git repo that has **no phased-plan machinery at all**: no `docs/*-plan.md`,
no `execution-state.yaml` registry, no soft-claims, no lane taxonomy. Those are
*host policy* (the phased-plan layer that lives in the `job` repo / a driver), not
kernel mechanism. The kernel answers from git history alone and reports
`source="none"` when there is no registry to consult — it never requires a plan
and never crashes for lack of one.

This test pins that separation so a future change can't quietly couple `verify`
to the phased-plan layer.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from dos import oracle
from dos.config import default_config
from dos.stamp import GENERIC_STAMP_CONVENTION


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _plainest_repo(repo: Path) -> None:
    """A git repo with zero phased-plan surface: no docs/, no plan, no registry."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init: empty repo, no phased plan")


def test_library_verify_needs_no_plan(tmp_path: Path):
    """oracle.is_shipped() returns a clean NOT-shipped verdict with no plan/registry."""
    _plainest_repo(tmp_path)
    cfg = default_config(tmp_path)

    # No execution-state registry exists in this workspace at all.
    assert not cfg.state_path().exists()

    v = oracle.is_shipped("SOMEPLAN", "PH1", cfg=cfg)
    assert v.shipped is False  # honest "no", not an error


def test_library_verify_finds_a_git_only_claim(tmp_path: Path):
    """A ship recorded ONLY in git history (no plan doc) is still verifiable.

    The commit uses the oracle's direct-ship subject grammar — a
    ``<dir>/<SERIES>: <PHASE>`` attribution (`docs/RS: RS1 …`), the form a real
    phase ship lands under. That grammar is the kernel's deliberate, strict ship
    signal (see `dos.phase_shipped`); the point being pinned here is only that
    `cfg=` wires the git-log grep rung so such a commit verifies with **no plan
    doc and no registry** present, not that any free-form mention counts.
    """
    _plainest_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "docs/RS: RS1 — ship the surfacer")
    cfg = default_config(tmp_path)

    v = oracle.is_shipped("RS", "RS1", cfg=cfg)
    # The grep rung confirms it from the commit SUBJECT — no plan file consulted.
    # Graded `grep-subject` (docs/118): the ship rests on the agent-authored
    # subject of an `--allow-empty` commit (zero files), the FORGEABLE rung — which
    # is exactly the honesty this grading surfaces (it was a flat `grep` before).
    assert v.shipped is True
    assert v.source == "grep-subject"  # the git-log SUBJECT rung, not a registry
    assert v.rung == "direct"


def test_no_plan_is_domain_free_under_generic_convention(tmp_path: Path):
    """The no-plan contract holds for a FOREIGN repo's grammar too (SCV 3a).

    The sibling test above uses the job `<dir>/<SERIES>: <PHASE>` subject; this
    one proves the same "verify from git history alone, no plan, no registry"
    property when the active convention is the GENERIC one and the repo stamps a
    bare `<SERIES><PHASE>:` ship (the external-repo shape that returned
    NOT_SHIPPED before this plan). The point: the no-plan contract is not tied to
    the job convention — it is the truth syscall being domain-free.
    """
    _plainest_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "AUTH2: ship token refresh")
    cfg = dataclasses.replace(default_config(tmp_path), stamp=GENERIC_STAMP_CONVENTION)

    v = oracle.is_shipped("AUTH", "2", cfg=cfg)
    assert v.shipped is True
    # The git-log SUBJECT rung under the generic grammar → `grep-subject`
    # (docs/118): an `--allow-empty` subject match is the forgeable rung.
    assert v.source == "grep-subject"

    # The honest-negative still holds: a phase that did not ship is False/none,
    # even under the looser generic grammar (verify does not match every mention).
    v = oracle.is_shipped("AUTH", "9", cfg=cfg)
    assert v.shipped is False
    assert v.source == "none"


def test_cli_verify_multiword_phase_out_of_the_box(tmp_path: Path):
    """A phase containing a SPACE (`Phase 4`) verifies out of the box (F7 + F9).

    This is the foreign-repo shape that broke before: the repo stamps
    `<slug> Phase <N>:` (the benchmark / hardware-host convention), the phase
    token contains a space, and there is no `dos.toml`. Two bugs combined to
    return `via none` for a phase that shipped:

      * F9 — the no-`dos.toml` default was the job-strict grammar (needs a
        `docs/` dir prefix), so a dir-less `hardware-thing Phase 4:` never
        matched. The generic default now in `default_config` fixes that.
      * F7 — even with the generic grammar, the oracle→rung batch wire protocol
        split the stdin line on whitespace, truncating `"Phase 4"` to `"Phase"`
        (doc=`"4"`), so the parent's `(series, phase)` lookup key never matched.
        The tab-delimited protocol fixes that.

    Driven through the real `dos verify` subprocess (which shells the grep rung),
    so a regression in EITHER fix is caught. The series itself also carries a
    space (`blktrace auto-install`) to pin that a multi-word *series* survives
    the round-trip too.
    """
    _plainest_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m",
         "blktrace auto-install Phase 1: opt-in flag")

    proc = subprocess.run(
        [sys.executable, "-m", "dos.cli", "verify",
         "--workspace", str(tmp_path), "blktrace auto-install", "Phase 1", "--json"],
        capture_output=True, text=True,
    )
    assert proc.stdout, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["shipped"] is True, (proc.stdout, proc.stderr)
    # `grep-subject` (docs/118): the multi-word `Phase 1` ship matched on the
    # commit subject of an `--allow-empty` commit — the forgeable rung, now
    # graded honestly instead of a flat `grep`.
    assert payload["source"] == "grep-subject"
    assert proc.returncode == 0


def test_batch_is_shipped_cfg_equals_per_pair_is_shipped(tmp_path: Path):
    """`batch_is_shipped_cfg` is byte-identical to per-pair `is_shipped(cfg=…)`.

    The cost fix for `dos top` resolves a whole screen of picks in ONE git-log
    cache build instead of one rebuild per pick. The contract that makes that safe:
    every pair's verdict must match what `is_shipped(plan, phase, cfg=cfg)` returns
    on its own — same registry-first resolution, same grep rung, same negative
    `source='none'` normalization. A repo with a shipped phase + an unshipped one
    exercises both branches; this is the regression guard that caught a doc-map
    divergence during development.
    """
    _plainest_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "docs/RS: RS1 — ship the surfacer")
    cfg = default_config(tmp_path)

    pairs = [("RS", "RS1"), ("RS", "RS9"), ("OTHER", "PH1")]
    batch = oracle.batch_is_shipped_cfg(pairs, cfg)
    for plan, phase in pairs:
        single = oracle.is_shipped(plan, phase, cfg=cfg)
        b = batch[(plan, phase)]
        assert (b.shipped, b.source, b.sha) == (single.shipped, single.source, single.sha), (
            f"batch≠single for {(plan, phase)}: batch={b} single={single}"
        )
    # And the shipped one is actually found (the test isn't vacuously all-False).
    assert batch[("RS", "RS1")].shipped is True


def test_batch_is_shipped_cfg_empty_pairs_is_empty(tmp_path: Path):
    """No pairs → empty dict, no I/O, no crash (the fail-soft floor)."""
    _plainest_repo(tmp_path)
    cfg = default_config(tmp_path)
    assert oracle.batch_is_shipped_cfg([], cfg) == {}

    # Honest-negative: a phase that did not ship stays not-shipped.
    proc2 = subprocess.run(
        [sys.executable, "-m", "dos.cli", "verify",
         "--workspace", str(tmp_path), "blktrace auto-install", "Phase 9", "--json"],
        capture_output=True, text=True,
    )
    payload2 = json.loads(proc2.stdout)
    assert payload2["shipped"] is False
    assert payload2["source"] == "none"


def test_cli_verify_needs_no_plan(tmp_path: Path):
    """`dos verify --workspace <repo> PLAN PHASE` runs with no phased plan present.

    Drives the real CLI entrypoint so the user-facing path is what's proven, not
    just the library. Expects the documented contract: exit 1 + a structured
    `shipped: false, source: none` verdict for an unshipped claim.
    """
    _plainest_repo(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-m", "dos.cli", "verify",
         "--workspace", str(tmp_path), "SOMEPLAN", "PH1", "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["shipped"] is False
    assert payload["plan"] == "SOMEPLAN"
    assert payload["phase"] == "PH1"
    assert payload["source"] == "none"  # no registry was needed or consulted
