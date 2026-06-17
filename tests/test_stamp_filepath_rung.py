"""The file-path backstop + progress/fallback grammar are per-workspace DATA.

The SCV seam (`test_stamp_convention.py`) lifted the commit-SUBJECT grammar into
`dos.stamp.StampConvention`. This module pins the SECOND wave of the same
genericization: the rungs that were *still* hardcoded to the reference app's
conventions after SCV, every one of which broke (or silently degraded) a foreign
repo's `verify`:

  * **file-path backstop** (`code_dirs` / `infra_basenames` / `infra_doc_basenames`)
    — the artefact rung that re-derives a ship from the files a phase's plan-doc
    section names. Its dir allowlist was the reference app's own top-level dirs
    (`agents|job_search|go|…`), so a repo whose deliverables live under
    `engine/`/`models/`/`commands/` harvested NOTHING — the rung was dead.
  * **progress-marker demotion** (`progress_markers`) — a hardcoded soak
    vocabulary (`audit`/`week-1`/`soak`/…) that demoted a `<PHASE> <marker>`
    subject from a ship to "progress on." It fired on EVERY repo, so a foreign
    repo's genuine `cache: Phase 0 audit of …` direct ship was silently demoted
    to NOT_SHIPPED — the worst failure mode (a *lost* ship).
  * **sub-phase-parent fallback** (`sub_phase_parent_fallback`) — gated on the
    QUERY shape `"-" in phase`, so a fabricated `P2-CLI` false-resolved against a
    real `P2` whose subject merely contained `CLI`.
  * **bundle-slug fallback** (derived from `summary_bundle_prefixes`) — gated on
    the hardcoded series literal `"HYG"`.
  * **run-archive bookkeeping** (universal `… archive <RUN-ID>` guard) — a
    fan-out rollup (`docs/fanout: archive 20260530T093407Z chain (…)`) that quotes
    phase ids but ships nothing false-resolved as a direct ship under the generic
    dir-free grammar.

The contract pinned both ways for each: GENERIC (the foreign-repo default) does
the right thing out of the box, and JOB (the reference app's opt-in) is
byte-for-byte unchanged so the existing suite stays green.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from dos import config as C
from dos import oracle
from dos import phase_shipped as PS
from dos.stamp import (
    GENERIC_STAMP_CONVENTION,
    JOB_STAMP_CONVENTION,
    StampConvention,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init: empty repo")


def _commit(repo: Path, subject: str, *files: str) -> None:
    """Empty-ish commit carrying `subject`, touching `files` (created if absent)."""
    for f in files:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text() if p.exists() else "") + "\nx", encoding="utf-8")
    if files:
        _git(repo, "add", *files)
        _git(repo, "commit", "-m", subject)
    else:
        _git(repo, "commit", "--allow-empty", "-m", subject)


def _generic_cfg(repo: Path) -> "C.SubstrateConfig":
    return dataclasses.replace(C.default_config(repo), stamp=GENERIC_STAMP_CONVENTION)


# ===========================================================================
# StampConvention: the new fields round-trip and JOB stays byte-identical
# ===========================================================================


def test_new_fields_round_trip_through_dict():
    """`to_dict`/`from_dict` carry the file-path + progress + fallback fields.

    This is the cross-subprocess contract: the grep rung shells out to a fresh
    process whose convention is rebuilt from the env-marshalled dict. A field that
    didn't round-trip would silently revert to the default in the `--batch` child.
    """
    for conv in (JOB_STAMP_CONVENTION, GENERIC_STAMP_CONVENTION):
        assert StampConvention.from_dict(conv.to_dict()) == conv


def test_job_convention_byte_identical_filepath_allowlist():
    """JOB's `repo_path_re()` reproduces the old hardcoded `_REPO_PATH_RE` exactly.

    The reference app's artefact rung must be byte-for-byte unchanged — the
    genericization is additive, not a behavior change for the host that already
    had the tight allowlist.
    """
    old = (
        r"(?:\.\.?/)*((?:agents|job_search|go|scripts|templates|config|docs|tests)"
        r"/[\w./-]+\.[A-Za-z0-9]+)"
    )
    assert JOB_STAMP_CONVENTION.repo_path_re().pattern == old
    # And the back-compat module alias is the same object's pattern.
    assert PS._REPO_PATH_RE.pattern == old


def test_job_infra_sets_byte_identical():
    """JOB's resolved infra sets equal the old hardcoded frozensets."""
    assert JOB_STAMP_CONVENTION.infra_basename_set() == frozenset({
        "config.py", "__init__.py", "models.py", "cli.py", "utils.py",
        "fanout_state.py", "constants.py", "settings.py", "conftest.py",
    })
    assert JOB_STAMP_CONVENTION.infra_doc_basename_set() == frozenset({
        "00_subsystems-reference.md", "architecture.mmd", "data-flow.mmd",
        "pipeline-flow.mmd", "state-machine.mmd", "scoring-model.mmd",
        "model-tiering.mmd",
    })
    assert PS._SHARED_INFRA_BASENAMES == JOB_STAMP_CONVENTION.infra_basename_set()


def test_generic_flags_are_all_off():
    """The generic convention declares NONE of the reference app's behaviors.

    Empty/off across the board is what makes a foreign repo safe and unsurprising
    out of the box: no progress demotion, no parent fallback, no bundle slugs, no
    dir allowlist (match-any).
    """
    g = GENERIC_STAMP_CONVENTION
    assert g.code_dirs == ()
    assert g.progress_markers == ()
    assert g.sub_phase_parent_fallback is False
    assert g.bundle_slugs() == frozenset()
    assert g.infra_basenames == () and g.infra_doc_basenames == ()


# ===========================================================================
# file-path rung: code_dirs harvest (the dead-on-foreign-repo leak)
# ===========================================================================


def test_generic_harvests_paths_under_any_top_level_dir(tmp_path: Path):
    """Generic `code_dirs=()` harvests file paths rooted at ANY top-level dir.

    The reference allowlist named `agents|job_search|go|…`; a foreign repo's
    deliverables under `engine/`/`models/`/`commands/` were invisible. The
    match-any-dir generic matcher harvests them.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### Phase 3 — metrics parity\n"
        "Touches `models/metrics.py` and [`server/_ssh.py`](../server/_ssh.py)\n"
        "and `commands/_serve.py`.\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "Phase 3", "vllm", m)
    assert set(files) == {"models/metrics.py", "server/_ssh.py", "commands/_serve.py"}


def test_job_allowlist_drops_foreign_dirs(tmp_path: Path):
    """JOB's tight allowlist still drops dirs it never named (the old behavior).

    Proves the allowlist is doing real narrowing under JOB — a `models/`/`server/`
    path is NOT harvested when `code_dirs` is the reference set. This is exactly
    why the rung was dead on a foreign repo before the generic default.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### Phase 3\n`models/metrics.py` and `docs/x.md` and `scripts/y.py`\n",
        encoding="utf-8",
    )
    job_cfg = dataclasses.replace(C.default_config(tmp_path), stamp=JOB_STAMP_CONVENTION)
    m = PS._subject_matchers(job_cfg)
    files = PS._extract_phase_files(str(plan), "Phase 3", "vllm", m)
    # `docs/` and `scripts/` are in the JOB allowlist; `models/` is not.
    assert "docs/x.md" in files and "scripts/y.py" in files
    assert "models/metrics.py" not in files


def test_filepath_backstop_resolves_foreign_ship_end_to_end(tmp_path: Path):
    """The file-path rung confirms a ship under foreign dirs (the Benchmark case).

    A phase section names 3 files under `server/`+`commands/`; one commit touches
    all 3. Under the generic convention the rung harvests them and the 2-file
    overlap fires → SHIPPED via file-path. Under JOB it would harvest nothing →
    NOT_SHIPPED. This is the live `6bf7bbe vllm serve-launch P2` shape from the
    Benchmark repo, reproduced hermetically.
    """
    _init_repo(tmp_path)
    plan = tmp_path / "docs" / "plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "### Phase 2 — wire config\n"
        "`server/_config.py`, `commands/_serve_presets.py`, `commands/_subparsers.py`\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "docs/plan.md")
    _git(tmp_path, "commit", "-m", "docs: add plan")
    # One commit touches all three named files, subject does NOT carry the phase id.
    _commit(
        tmp_path,
        "engine refactor: --engine CLI + config-builder branch",
        "server/_config.py", "commands/_serve_presets.py", "commands/_subparsers.py",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    C.set_active(_generic_cfg(tmp_path))
    r = PS._check_phase_by_filepath("vllm", "Phase 2", str(plan), m)
    assert r["shipped"] is True, r
    assert r["via"] == "file-path"
    # The `file-path` rung is the NON-forgeable one (docs/118): a commit cannot
    # fake which files it touched. Its end-to-end grading to `grep-artifact` is
    # pinned in `test_oracle_forgeability_provenance` (which drives the oracle
    # boundary with this exact `via`), where the rung-wiring is exercised directly.


def test_declared_code_dirs_via_toml(tmp_path: Path):
    """A `dos.toml [stamp] code_dirs=[...]` narrows the harvest to declared dirs."""
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\ncode_dirs = ["engine"]\n', encoding="utf-8"
    )
    plan = tmp_path / "plan.md"
    plan.write_text("### Phase 1\n`engine/run.py` and `models/x.py`\n", encoding="utf-8")
    cfg = C.load_workspace_config(tmp_path)
    assert cfg.stamp.code_dirs == ("engine",)
    m = PS._subject_matchers(cfg)
    files = PS._extract_phase_files(str(plan), "Phase 1", "x", m)
    assert files == ["engine/run.py"]  # models/ excluded by the declared allowlist


# ===========================================================================
# L1: progress-marker false NEGATIVE — generic never demotes a real ship
# ===========================================================================


def test_generic_does_not_demote_a_real_ship_with_marker_tail(tmp_path: Path):
    """Generic recognizes `cache: Phase 0 audit of …` as a ship (the L1 fix).

    `audit` follows the phase id, which under the hardcoded marker set demoted the
    commit to progress-only → NOT_SHIPPED, silently losing a real foreign-repo
    ship. The generic convention declares no markers, so the ship resolves.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "cache: Phase 0 audit of dgx2 hybrid-counter emission")
    v = oracle.is_shipped("cache", "Phase 0", cfg=_generic_cfg(tmp_path))
    assert v.shipped is True, v
    # `grep-subject` (docs/118): a subject-rung ship (not the file-path artefact
    # rung this module otherwise exercises) — graded forgeable.
    assert v.source == "grep-subject"


def test_job_still_demotes_marker_tail(tmp_path: Path):
    """JOB keeps the soak-marker demotion (byte-identical behavior).

    Under JOB, a `docs/cache: Phase 0 audit …` subject is progress-on, not a ship
    — the reference app's whole reason for the marker set. Pins that the generic
    default-flip did not delete the demotion, only make it opt-in.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "docs/cache: Phase 0 audit of dgx2")
    job_cfg = dataclasses.replace(C.job_config(tmp_path), stamp=JOB_STAMP_CONVENTION)
    v = oracle.is_shipped("cache", "Phase 0", cfg=job_cfg)
    assert v.shipped is False, v
    # Control: the same phase WITHOUT a marker tail is a ship under JOB.
    _commit(tmp_path, "docs/cache: Phase 1 — real ship")
    v2 = oracle.is_shipped("cache", "Phase 1", cfg=job_cfg)
    assert v2.shipped is True, v2


def test_declared_progress_markers_via_toml_demote(tmp_path: Path):
    """A host can declare its own soak vocabulary in `[stamp] progress_markers`."""
    _init_repo(tmp_path)
    _commit(tmp_path, "cache: Phase 0 dryrun of the thing")
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nprogress_markers = ["dryrun"]\n', encoding="utf-8"
    )
    cfg = C.load_workspace_config(tmp_path)
    assert "dryrun" in cfg.stamp.progress_marker_set()
    v = oracle.is_shipped("cache", "Phase 0", cfg=cfg)
    assert v.shipped is False, v  # demoted by the declared marker


# ===========================================================================
# L3: sub-phase-parent fallback — generic does not fire on `-` queries
# ===========================================================================


def test_generic_no_subphase_parent_fallback(tmp_path: Path):
    """A fabricated `P2-CLI` does NOT resolve under generic (the L3 fix).

    The real ship `… P2: --engine CLI …` exists; under the old query-shape gate
    (`"-" in phase`) the parent `P2` matched and `CLI` appeared in the subject, so
    `P2-CLI` false-shipped. Generic leaves the fallback off → NOT_SHIPPED. The
    genuine `P2` still resolves (control).
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "vllm serve-launch P2: --engine CLI + config-builder branch")
    cfg = _generic_cfg(tmp_path)
    assert oracle.is_shipped("vllm serve-launch", "P2-CLI", cfg=cfg).shipped is False
    assert oracle.is_shipped("vllm serve-launch", "P2", cfg=cfg).shipped is True


def test_job_keeps_subphase_parent_fallback(tmp_path: Path):
    """JOB keeps the parent fallback for its hyphen-suffixed sub-phase ids.

    `docs/UP: UP6 — /ui/system/diagnostics …` resolves a `UP6-diagnostics` query
    (parent `UP6` matches, suffix slug `diagnostics` in the subject) — the queue
    #167 convenience the reference app relies on. Pins it stays on under JOB.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "docs/UP: UP6 — /ui/system/diagnostics tile fan-out")
    job_cfg = dataclasses.replace(C.job_config(tmp_path), stamp=JOB_STAMP_CONVENTION)
    assert oracle.is_shipped("UP", "UP6-diagnostics", cfg=job_cfg).shipped is True


# ===========================================================================
# L2: run-archive rollup — universal guard catches it zero-config
# ===========================================================================


def test_generic_run_archive_rollup_not_a_ship(tmp_path: Path):
    """`<prefix>: archive <RUN-ID> …` is bookkeeping under generic (the L2 fix).

    A fan-out rollup quotes phase ids but ships nothing. The universal run-archive
    guard catches it with ZERO config (no declared `bookkeeping_prefixes`), so a
    foreign repo is safe out of the box.
    """
    _init_repo(tmp_path)
    _commit(
        tmp_path,
        "docs/fanout: archive 20260530T093407Z chain (vllm-p2p3, next-staleness)",
    )
    v = oracle.is_shipped("fanout", "archive", cfg=_generic_cfg(tmp_path))
    assert v.shipped is False, v


def test_run_archive_guard_does_not_overexclude_real_archive_phase():
    """A legitimately-named `archive` phase/series is NOT excluded.

    The guard requires a run-id-shaped DATE tail after `archive`; a real
    `AUTH: archive token rotation` ship (no date) and a `docs/ARCHIVE: ARCHIVE2 …`
    series ship are both left alone. This is the false-positive bound that lets
    the guard be universal.
    """
    g = GENERIC_STAMP_CONVENTION
    bk = g.bookkeeping_subject_re()
    assert bk.match("docs/fanout: archive 20260530T093407Z chain")
    assert bk.match("docs/fanout: archive 20260529T0233Z cross-replica")  # short time
    assert bk.match("archive 20260601 nightly")  # bare, no prefix
    assert not bk.match("AUTH: archive token rotation policy")
    assert not bk.match("docs/ARCHIVE: ARCHIVE2 ship cold-storage tier")


# ===========================================================================
# L4: bundle-slug fallback — gated on declared prefixes, not a "HYG" literal
# ===========================================================================


def test_generic_no_bundle_slug_fallback(tmp_path: Path):
    """A bare `HYG:` subject is NOT a bundle-slug ship under generic (the L4 fix).

    The prose-slug fallback used to fire on the hardcoded series literal `"HYG"`;
    a foreign repo whose series happened to be `HYG` would leak. Generic declares
    no `summary_bundle_prefixes`, so `bundle_slugs()` is empty and the fallback is
    inert.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "HYG: dropbox zero-apply picker audit")
    v = oracle.is_shipped("HYG", "dropbox_zero_apply", cfg=_generic_cfg(tmp_path))
    assert v.shipped is False, v


# ===========================================================================
# Adversarial-review regressions — the generic harvester's loose matcher must
# not (a) harvest URL/version noise, (b) lose a real single-file ship to noise
# inflation, (c) false-ship a committed release artifact, or (d) match a
# multi-segment direct-ship prefix.
# ===========================================================================


def test_generic_does_not_harvest_urls_or_version_strings(tmp_path: Path):
    """The generic matcher excludes URL hosts and `vX.Y.Z/` version roots.

    Plan prose routinely mentions a git remote or a release tarball; the loose
    match-any-dir generic harvester used to lift `github.com/user/repo.git` /
    `v1.2.3/release.tar.gz` as if they were source files. The no-dot-first-segment
    + left-anchor fix drops them while still harvesting real source paths.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### Phase 1\n"
        "clone https://github.com/user/repo.git and download v1.2.3/release.tar.gz,\n"
        "then edit `models/metrics.py` and [`engine/run.py`](../engine/run.py).\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "Phase 1", "x", m)
    assert set(files) == {"models/metrics.py", "engine/run.py"}, files


def test_noise_prose_does_not_lose_a_real_single_file_ship(tmp_path: Path):
    """A single-file phase still ships when its section also has prose noise.

    The false-NEGATIVE the review found: a noise token (URL / version / numeric
    `Phase 1/summary.txt`) inflated `len(files)` and pushed a genuine single-file
    phase into the >=2 branch (which its lone commit can't satisfy) → ship lost.
    Routing on `live_files` (files with real commit history) fixes it.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "mamba P1.3: wire pool flags", "server/_config.py")
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### P1.3 — wire mamba pool flags\n"
        "Edits `server/_config.py`. Phase 1/summary.txt notes. See v1.2.3/release.tar.gz.\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    C.set_active(_generic_cfg(tmp_path))
    r = PS._check_phase_by_filepath("mamba", "P1.3", str(plan), m)
    assert r["shipped"] is True, r  # the real file ships; noise doesn't bury it
    assert r["via"] == "file-path"


def test_committed_release_artifact_does_not_false_ship(tmp_path: Path):
    """A committed release tarball named in prose does NOT carry a false ship.

    The false-POSITIVE the review found: `v1.2.3/release.tar.gz` (git-tracked,
    co-committed with a series-attributed subject) satisfied the single-file gate
    for an unshipped phase. The non-source-suffix harvest filter drops it.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "docs/DT: prepared the release archive", "v1.2.3/release.tar.gz")
    plan = tmp_path / "dt-plan.md"
    plan.write_text(
        "### DT5 — ship the release tarball\nSee `v1.2.3/release.tar.gz` (the binary).\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    C.set_active(_generic_cfg(tmp_path))
    assert PS._extract_phase_files(str(plan), "DT5", "DT", m) == []  # tarball dropped
    r = PS._check_phase_by_filepath("DT", "DT5", str(plan), m)
    assert r["shipped"] is False, r


def test_generic_direct_ship_prefix_is_single_segment(tmp_path: Path):
    """A multi-segment path prefix does NOT count as a direct ship (correctness).

    `direct_prefix_re()`'s generic branch let `docs/notes/sub/AUTH2:` (a deep,
    unrelated note that merely NAMES the id) false-match a direct ship. The
    single-component fix recognizes bare `AUTH2:` and one-dir `docs/AUTH2:` only.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "docs/notes/subfolder/AUTH2: a deep unrelated note naming the id")
    assert oracle.is_shipped("AUTH", "AUTH2", cfg=_generic_cfg(tmp_path)).shipped is False
    # Control: a single-dir and a bare prefix still ship.
    _commit(tmp_path, "docs/AUTH3: real single-dir ship")
    _commit(tmp_path, "AUTH4: real bare ship")
    assert oracle.is_shipped("AUTH", "AUTH3", cfg=_generic_cfg(tmp_path)).shipped is True
    assert oracle.is_shipped("AUTH", "AUTH4", cfg=_generic_cfg(tmp_path)).shipped is True


def test_job_keeps_hyg_bundle_slug_fallback(tmp_path: Path):
    """JOB resolves a HYG prose-slug ship via the bundle-slug fallback.

    `docs/HYG: Dropbox zero-apply picker audit (queue #20)` resolves a
    `dropbox_zero_apply` query — the reference app's hygiene-bundle convention.
    JOB declares `docs/HYG:` as a summary-bundle prefix, so `bundle_slugs()` yields
    `HYG` and the prose-slug fallback runs.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "docs/HYG: Dropbox zero-apply picker audit (queue #20)")
    assert JOB_STAMP_CONVENTION.bundle_slugs() == frozenset({"HYG"})
    job_cfg = dataclasses.replace(C.job_config(tmp_path), stamp=JOB_STAMP_CONVENTION)
    v = oracle.is_shipped("HYG", "dropbox_zero_apply", cfg=job_cfg)
    assert v.shipped is True, v


# ===========================================================================
# phase_deliverable_touched — the ONE shared deliverable-overlap predicate
# (the convergence symbol both the read-side verdict and the write-side stamp
# guard feed; True/False/None contract + coarse-parity with the write-side shape)
# ===========================================================================


def _pdt_plan(tmp_path: Path, body: str) -> str:
    plan = tmp_path / "plan.md"
    plan.write_text(body, encoding="utf-8")
    return str(plan)


def test_pdt_true_when_a_distinctive_deliverable_is_touched(tmp_path: Path):
    plan = _pdt_plan(
        tmp_path,
        "### CRS3 — token allocator\nTouches `agents/crs_tokens.py`.\n",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS.phase_deliverable_touched(
        "CRS", "CRS3", plan, {"agents/crs_tokens.py"}, series="CRS", matchers=m
    ) is True


def test_pdt_false_when_declares_distinctive_but_touched_none(tmp_path: Path):
    # The CRS3 false-stamp shape: the phase declares a real deliverable, but the
    # commit touched only the (shared) plan doc — coverage with zero deliverable.
    plan = _pdt_plan(
        tmp_path,
        "### CRS3 — token allocator\nTouches `agents/crs_tokens.py`.\n",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS.phase_deliverable_touched(
        "CRS", "CRS3", plan, {plan.replace("\\", "/"), "docs/crs-plan.md"},
        series="CRS", matchers=m,
    ) is False


def test_pdt_none_when_phase_declares_no_distinctive_file(tmp_path: Path):
    # A genuinely doc-only phase (declares only the plan doc) → permissive None,
    # never a false refusal.
    plan = _pdt_plan(
        tmp_path,
        "### CRS4 — docs only\nUpdates this plan doc only.\n",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS.phase_deliverable_touched(
        "CRS", "CRS4", plan, {"agents/anything.py"}, series="CRS", matchers=m
    ) is None


def test_pdt_none_on_empty_or_missing_inputs(tmp_path: Path):
    plan = _pdt_plan(tmp_path, "### CRS3\nTouches `agents/crs_tokens.py`.\n")
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS.phase_deliverable_touched("CRS", "CRS3", plan, set(), matchers=m) is None
    assert PS.phase_deliverable_touched("CRS", "CRS3", plan, None, matchers=m) is None
    assert PS.phase_deliverable_touched(
        "CRS", "CRS3", None, {"agents/crs_tokens.py"}, matchers=m
    ) is None


def test_pdt_hub_only_phase_is_permissive_under_drop_shared_infra(tmp_path: Path):
    # A phase whose only declared file is a shared-infra hub has no DISTINCTIVE
    # deliverable → None (permissive) under the default drop_shared_infra=True,
    # so the merged predicate can only ever ADD a refusal where there is zero
    # distinctive evidence — it never manufactures a false-negative on a hub edit.
    job_m = PS._subject_matchers(
        dataclasses.replace(C.job_config(tmp_path), stamp=JOB_STAMP_CONVENTION)
    )
    hub = sorted(JOB_STAMP_CONVENTION.infra_basename_set())[0]
    plan = _pdt_plan(tmp_path, f"### X1 — hub edit\nTouches `agents/{hub}`.\n")
    assert PS.phase_deliverable_touched(
        "X", "X1", plan, {f"agents/{hub}"}, series="X", matchers=job_m,
        drop_shared_infra=True,
    ) is None


def test_pdt_basename_match_tolerates_path_prefix_drift(tmp_path: Path):
    # The plan doc names `crs_tokens.py` bare; the touched set carries the full
    # repo path. Basename match unifies the read side's basename matching with the
    # write side's path matching so neither footprint source under-counts.
    plan = _pdt_plan(tmp_path, "### CRS3\nTouches `agents/crs_tokens.py`.\n")
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS.phase_deliverable_touched(
        "CRS", "CRS3", plan, {"some/other/agents/crs_tokens.py"},
        series="CRS", matchers=m,
    ) is True


def test_pdt_coarse_parity_refuse_matches_write_side_shape(tmp_path: Path):
    # COARSE PARITY (verifier requirement before any duplicated-loop deletion):
    # the predicate REFUSES (False) exactly the case the write-side stamp guard
    # refuses — a phase that declares a distinctive deliverable file whose commit
    # touched none of it — and ALLOWS (True) the case the write side allows (the
    # deliverable was touched). One row each side of the boundary.
    plan = _pdt_plan(tmp_path, "### CRS3\nTouches `agents/crs_tokens.py`.\n")
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    # write side "lacks deliverable" == predicate is False
    refuse = PS.phase_deliverable_touched(
        "CRS", "CRS3", plan, {plan.replace("\\", "/")}, series="CRS", matchers=m
    )
    allow = PS.phase_deliverable_touched(
        "CRS", "CRS3", plan, {plan.replace("\\", "/"), "agents/crs_tokens.py"},
        series="CRS", matchers=m,
    )
    assert refuse is False and allow is True


# ===========================================================================
# The load-bearing-files convention (make the floor load-bearing):
#   - a `## Phase N` heading is located for a `Pk` trailer-stamp token;
#   - a `**Files:**` line is the canonical, dilution-free declaration;
#   - a phase-LESS `docs/NN` unit declares files at the doc level.
# These only LOCATE text the existing repo_path matcher harvests; the >=2
# distinctive-file floor in `_check_phase_by_filepath` is unchanged.
# ===========================================================================


def test_extract_matches_phase_n_heading_for_pk_token(tmp_path: Path):
    """A `Pk` trailer-stamp token finds a `## Phase k` plan-doc heading.

    Commits stamp `(docs/NN P1)` but plan docs head the phase `## Phase 1`. The
    section search expands `P1` <-> `Phase 1` (and H2..H4), so the section is
    located and its prose paths harvested — the dominant refuted class root cause.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# 314 Title\n\n## Phase 1 — the write gate\n\n"
        "Built `src/dos/a.py` and `tests/test_a.py`.\n\n## Phase 2 — next\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "P1", "docs/314", m)
    assert set(files) == {"src/dos/a.py", "tests/test_a.py"}


def test_extract_reads_in_section_files_marker(tmp_path: Path):
    """An in-section `**Files:**` line is preferred over surrounding prose.

    The declaration is dilution-free: a path the phase merely *references* in
    prose does not pad the load-bearing set; only the declared line is read.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Phase 1\n"
        "prose references `src/dos/zzz.py` and `src/dos/qqq.py`.\n"
        "**Files:** `src/dos/a.py`, `tests/test_a.py`\n"
        "more prose `src/dos/www.py`\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "P1", "docs/314", m)
    assert set(files) == {"src/dos/a.py", "tests/test_a.py"}


def test_extract_reads_doc_level_files_marker_for_phaseless_unit(tmp_path: Path):
    """A phase-LESS `docs/NN` unit declares files at the doc level.

    The stamp carried no `Pk` token, so the unit's `phase` is the bare doc number
    and no `### NN` section exists. The doc-level `**Files:**` line (the FIRST one)
    is read — bounded to that line, never the body.
    """
    plan = tmp_path / "doc.md"
    plan.write_text(
        "# 360 — tool-call error handling\n\n"
        "**Files:** `src/dos/vcs.py`, `tests/test_vcs.py`\n\n"
        "Long prose discussion mentioning `src/dos/other.py` elsewhere.\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "360", "docs/360", m)
    assert set(files) == {"src/dos/vcs.py", "tests/test_vcs.py"}


def test_extract_files_marker_is_bounded_to_the_line(tmp_path: Path):
    """The doc-level fallback scans ONLY the declared line, never the body.

    The anti-over-match guard: if the fallback harvested the whole doc, any commit
    touching two of a dozen prose-mentioned paths would false-corroborate — the
    exact Goodhart failure the floor exists to catch. Only the 2 declared paths
    are returned despite 3 others in the body.
    """
    plan = tmp_path / "doc.md"
    plan.write_text(
        "# 360\n\n**Files:** `src/dos/a.py`, `src/dos/b.py`\n\n"
        "prose `src/dos/c.py` `src/dos/d.py` `src/dos/e.py`\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    files = PS._extract_phase_files(str(plan), "360", "docs/360", m)
    assert set(files) == {"src/dos/a.py", "src/dos/b.py"}


def test_extract_does_not_borrow_another_phases_marker(tmp_path: Path):
    """A doc WITH phase sections never lends one phase's marker to another.

    A `P1` query against a doc that has only a `## Phase 10` section must return
    `[]` — NOT harvest Phase 10's `**Files:**` line via the doc-level fallback.
    The fallback fires only for a genuinely phase-LESS doc.
    """
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# t\n## Phase 10 — later\n"
        "**Files:** `src/dos/ten.py`, `tests/t10.py`\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS._extract_phase_files(str(plan), "P1", "docs/314", m) == []


def test_extract_drops_non_source_in_marker(tmp_path: Path):
    """A declared archive/binary/URL is dropped; only the source file survives."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Phase 1\n**Files:** `dist/x.tar.gz`, `src/dos/a.py`\n",
        encoding="utf-8",
    )
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    assert PS._extract_phase_files(str(plan), "P1", "docs/314", m) == ["src/dos/a.py"]


def test_section_heading_variants_respects_phase_boundary():
    """`P1` must not match a `Phase 10` heading (the strict-boundary regression)."""
    import re as _re

    alt = "|".join(PS._section_heading_variants("P1", "docs/314"))
    pat = _re.compile(rf"(?m)^#{{2,4}}\s+(?:Phase\s+)?(?:{alt}){PS._BOUNDARY_NEG}")
    assert pat.search("## Phase 1 — gate") is not None
    assert pat.search("## Phase 10 — later") is None
    # And the bare ordinal `1` is NOT a variant (plan docs number sections `## 1.`).
    assert "1" not in PS._section_heading_variants("P1")


def test_filepath_rung_corroborates_a_declared_phase_end_to_end(tmp_path: Path):
    """End-to-end: a `## Phase 1` whose declared files a commit co-touched resolves
    `via=file-path` — the non-forgeable rung the backtest re-check reads."""
    _init_repo(tmp_path)
    plan = tmp_path / "docs" / "314_plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "# 314\n\n## Phase 1 — the gate\n\n"
        "**Files:** `src/dos/a.py`, `tests/test_a.py`\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "docs/314_plan.md")
    _git(tmp_path, "commit", "-m", "docs: add plan")
    # A ship commit whose subject does NOT name the phase token, but whose diff
    # touches both declared files — the file-path rung is the only witness.
    _commit(tmp_path, "feat: the write gate", "src/dos/a.py", "tests/test_a.py")
    C.set_active(_generic_cfg(tmp_path))
    m = PS._subject_matchers(_generic_cfg(tmp_path))
    res = PS._check_phase_by_filepath("docs/314", "P1", str(plan), m)
    assert res["shipped"] is True
    assert res["via"] == "file-path"
