"""The config seam — the one place the substrate learns *which workspace* it serves.

This module is the load-bearing half of the Dispatch-OS port (the
"Stage-1 kernel extraction").

The reference userland app's spine bound its state location at import time::

    REPO_ROOT = Path(__file__).resolve().parent.parent          # "the repo I live in"
    STATE_PATH = REPO_ROOT / "docs" / "_plans" / "execution-state.yaml"

That single assumption — *my code and my managed state share a tree* — is the
entire thing standing between a repo-bound script and a separable OS. DOS
replaces it with "the workspace I was pointed at": an injected workspace root.

The mechanism (the verdict enum, the oracle, the lease algebra) lives in the
`dos` package and carries **no policy**. The policy (which lanes exist, where
plans live, what counts as a ship stamp) is per-workspace and lives in a
`SubstrateConfig` the host supplies — the reference userland app builds
`JOB_CONFIG`, a foreign repo builds its own, a throwaway directory gets
`default_config()`. Co-location was always about keeping *policy* at its call
site; a per-workspace config object *is* that call site, expressed as data the
shared mechanism reads.

Resolution order for the active workspace (highest precedence first):
  1. an explicit `SubstrateConfig` / `--workspace` passed by the caller,
  2. the ``DISPATCH_WORKSPACE`` environment variable,
  3. the current working directory.

So `dos` run from inside any workspace defaults to serving that workspace, and a
host that installed `dos` as a dependency points it elsewhere with one env var
or one constructor argument — never by editing the package.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from dos.reasons import ReasonRegistry, BASE_REASONS
from dos.intervention import InterventionLadder, BASE_INTERVENTIONS
from dos.tool_stream import StreamPolicy, DEFAULT_POLICY as DEFAULT_STREAM_POLICY
from dos.marker_gate import MarkerPolicy, DEFAULT_POLICY as DEFAULT_MARKER_POLICY
from dos.precursor_gate import PrecursorGrammar, EMPTY_GRAMMAR as EMPTY_PRECURSOR_GRAMMAR
from dos.stamp import StampConvention, JOB_STAMP_CONVENTION, GENERIC_STAMP_CONVENTION
from dos.enumerate import EnumerateGrammar, GENERIC_GRAMMAR
from dos.cooldown import CooldownPolicy, DEFAULT_COOLDOWN_POLICY
from dos.supervise import SupervisePolicy, DEFAULT_POLICY as DEFAULT_SUPERVISE_POLICY
from dos.lifecycle import LifecyclePolicy, GENERIC_LIFECYCLE
from dos.reason_morphology import MorphologyRuleset, GENERIC_REASON_MORPHOLOGY
from dos.concurrency_class import ClassBudgets, NO_CLASS_BUDGETS
from dos.env_print import EnvPrint, gather_env_print
from dos.retention import RetentionPolicy, GENERIC_RETENTION
from dos.data_class import DataClassPolicy, GENERIC_DATA_CLASS

# The default soft-overlap tolerance — mirrored from `dos.lane_overlap.
# OVERLAP_RATIO_MAX` (⅓) by VALUE, not import: `config` (layer 2a) must not import
# a kernel module (layer 1), or it would couple the config seam to the
# `admission`→`lane_overlap`→`_tree` chain (see the import-cycle note below).
# `overlap_policy._ratio_max_from_config` reads the field; `lane_overlap` is the
# canonical home of the constant. The two are pinned equal by
# `tests/test_overlap_policy.py` so they cannot drift.
_DEFAULT_OVERLAP_RATIO_MAX = 1 / 3

# The env var a host sets to point the installed package at a workspace that is
# NOT the cwd (e.g. the reference userland app pointing `dos` at its own tree, or
# a sidecar checkout pointed at a sibling repo). Mirrors the reference userland
# app's `JOB_*_PATH` override idiom.
ENV_WORKSPACE = "DISPATCH_WORKSPACE"

# The env var that points the machine-local DOS_HOME (the central projection
# store: ~/.dos by default) somewhere else — the home-tier analogue of
# ENV_WORKSPACE. Highest precedence in `resolve_dos_home`.
ENV_DOS_HOME = "DISPATCH_HOME"

# The env var the ship oracle uses to carry the ACTIVE stamp convention into the
# grep-rung subprocess (`python -m dos.phase_shipped`). That child re-derives its
# own `active()` config from scratch, so without this it would lose a caller-
# installed (`set_active`) or `dos.toml`-declared convention and fall back to the
# reference default. The parent JSON-encodes `cfg.stamp.to_dict()` here; the child's
# bootstrap reads it back (see `phase_shipped._bootstrap_active_config`). This is
# what makes the convention authoritative across the process boundary.
ENV_STAMP_CONVENTION = "DISPATCH_STAMP_CONVENTION"


@dataclass(frozen=True)
class LaneTaxonomy:
    """The concurrency policy as data — which lanes may run together.

    * ``concurrent`` — cluster lanes that run in parallel iff their file trees
      are provably disjoint (the reference userland app's ``apply`` / ``tailor``
      / ``discovery``).
    * ``exclusive`` — lanes that never run alongside anything (``orchestration`` /
      ``global``): holding one refuses every other request.
    * ``autopick`` — the ordered set a bare (lane-less) request walks to find a
      free, non-empty lane.
    * ``trees`` — each named lane's canonical file tree (repo-relative globs).
      This is what the reference userland app hard-coded in its renderer; here it
      is per-workspace data, so the arbiter never mentions a domain lane name.
    * ``aliases`` — keyword → named-lane routing (e.g. ``ff`` → ``fleet``).
    """

    concurrent: tuple[str, ...] = ()
    exclusive: tuple[str, ...] = ()
    autopick: tuple[str, ...] = ()
    trees: dict[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def tree_for(self, lane: str) -> list[str]:
        """The canonical file tree for ``lane`` (empty list if unknown)."""
        return list(self.trees.get(lane, ()))

    def is_exclusive(self, lane: str) -> bool:
        return lane in self.exclusive

    def is_concurrent(self, lane: str) -> bool:
        return lane in self.concurrent

    @classmethod
    def from_table(cls, table: dict) -> "LaneTaxonomy":
        """Build a `LaneTaxonomy` from a parsed `[lanes]` TOML table (WCR Phase 1).

        Pure (no I/O); mirrors `reasons.specs_from_table` / `stamp.convention_from_table`.
        The table shape mirrors the dataclass::

            [lanes]
            concurrent = ["api", "worker"]    # cluster lanes, parallel iff disjoint
            exclusive  = ["infra"]            # lanes that run alone
            autopick   = ["api", "worker"]    # the bare-request walk order
            [lanes.trees]
            api    = ["src/api/**"]
            worker = ["src/worker/**"]
            [lanes.aliases]
            ff = "fleet"

        Tolerant of missing keys (each list defaults to ``()``; ``trees`` /
        ``aliases`` default to ``{}``). Rejects, with a `ValueError` naming the
        offending lane/key, a value that is not the shape the dataclass needs:

          * a non-table ``table`` (a host wrote ``[lanes]`` as a scalar),
          * a list field (``concurrent``/``exclusive``/``autopick``) that is not a
            list of strings,
          * a ``[lanes.trees]`` entry whose value is not a list of strings,
          * a ``[lanes.aliases]`` entry whose value is not a string.

        Loud-on-malformed matches the sibling seams: a host that declared its
        taxonomy wrong wants that surfaced at load, not silently dropped to a lane
        with no tree (which the disjointness algebra can't arbitrate). This builds
        a *value* and names no job lane — Law 1 (kernel imports no host) holds: a
        TOML-declared lane is pure workspace data.
        """
        if not isinstance(table, dict):
            raise ValueError(f"[lanes] must be a table, got {type(table).__name__}")

        def _str_list(value: object, key: str) -> tuple[str, ...]:
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"[lanes].{key} must be a list of strings, "
                    f"got {type(value).__name__}"
                )
            out: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(
                        f"[lanes].{key} must be a list of strings; got a "
                        f"{type(item).__name__} element ({item!r})"
                    )
                out.append(item)
            return tuple(out)

        trees_table = table.get("trees", {}) or {}
        if not isinstance(trees_table, dict):
            raise ValueError(
                f"[lanes.trees] must be a table, got {type(trees_table).__name__}"
            )
        trees: dict[str, tuple[str, ...]] = {}
        for lane, globs in trees_table.items():
            if not isinstance(globs, (list, tuple)):
                raise ValueError(
                    f"[lanes.trees].{lane} must be a list of glob strings, "
                    f"got {type(globs).__name__}"
                )
            tree: list[str] = []
            for g in globs:
                if not isinstance(g, str):
                    raise ValueError(
                        f"[lanes.trees].{lane} must be a list of glob strings; "
                        f"got a {type(g).__name__} element ({g!r})"
                    )
                tree.append(g)
            trees[str(lane)] = tuple(tree)

        aliases_table = table.get("aliases", {}) or {}
        if not isinstance(aliases_table, dict):
            raise ValueError(
                f"[lanes.aliases] must be a table, got {type(aliases_table).__name__}"
            )
        aliases: dict[str, str] = {}
        for keyword, lane in aliases_table.items():
            if not isinstance(lane, str):
                raise ValueError(
                    f"[lanes.aliases].{keyword} must be a string lane name, "
                    f"got {type(lane).__name__}"
                )
            aliases[str(keyword)] = lane

        return cls(
            concurrent=_str_list(table.get("concurrent", ()), "concurrent"),
            exclusive=_str_list(table.get("exclusive", ()), "exclusive"),
            autopick=_str_list(table.get("autopick", ()), "autopick"),
            trees=trees,
            aliases=aliases,
        )


@dataclass(frozen=True)
class PathLayout:
    """Where this workspace keeps the state the substrate reads/writes.

    Every path the ported spine hard-coded relative to ``REPO_ROOT`` is named
    here, resolved against an injected ``root``. The defaults reproduce the
    reference userland app's layout so it is a zero-surprise consumer; a foreign
    repo overrides the ones that differ (a foreign repo's plans live in
    ``docs/active-plans.md``, say) and leaves the rest.
    """

    root: Path
    execution_state: Path
    plans_glob: str
    findings_queue: Path
    fanout_runs: Path
    dispatch_loops: Path
    chained_runs: Path
    next_packets: Path
    replan_dir: Path
    soaks_index: Path
    picker_audits: Path
    archive_lock: Path
    lane_journal: Path
    # --- new fields (DOS-HOME / docs/74) -----------------------------------
    # Added keyword-only with defaults AT THE END of the dataclass so the
    # 13-field positional construction `for_root` and any positional caller use
    # is byte-unchanged (a non-default field after a default field is a Python
    # error; appending defaulted fields is the only back-compatible widening).
    #   leases_dir   — where the lease state + archive lock live. In `for_root`
    #                  this is `docs/_plans` (the lock keeps its literal path,
    #                  NOT re-derived from this); in `for_dos_dir` it is
    #                  `.dos/leases` and the lock IS derived from it.
    #   project_card — the `.dos/project.json` identity card (None under the
    #                  reference layout, which has no `.dos/`).
    #   style        — the layout discriminator: "repo" (the reference docs/
    #                  layout) vs "dos" (the generic `.dos/` layout). `with_root`
    #                  branches on this to re-point a config without dragging a
    #                  `.dos/` layout back onto the reference tree.
    #   verdict_journal — the verdict WAL (docs/262), the lane journal's lateral
    #                  sibling: a durable, append-only, run-id-correlated record of
    #                  every adjudication the kernel makes (`verify`/`liveness`/…),
    #                  read by `dos observe`. Defaulted keyword-only (the same
    #                  back-compatible widening as the DOS-HOME fields above) so a
    #                  positional caller is byte-unchanged; defaults to a sibling of
    #                  `lane_journal` under each layout (set in `for_root`/`for_dos_dir`).
    leases_dir: Path | None = None
    project_card: Path | None = None
    style: str = "repo"
    verdict_journal: Path | None = None

    @property
    def dot_dos(self) -> Path:
        """The per-project `.dos/` home (derived, not stored — never duplicates
        ``root``). Vocabulary for callers; the generic layout's emissions live
        under here."""
        return self.root / ".dos"

    @property
    def verdicts_dir(self) -> Path:
        """The verdict-envelope directory. It IS ``next_packets`` (one directory,
        one name — a separate field would invite drift); this read-only alias
        gives the `.dos/verdicts` vocabulary without a second source of truth."""
        return self.next_packets

    @classmethod
    def for_root(cls, root: Path | str) -> "PathLayout":
        """Build the reference-app-shaped default layout under ``root``.

        A foreign workspace calls this then `dataclasses.replace(...)` for the
        handful of paths that genuinely differ.
        """
        r = Path(root).resolve()
        plans = r / "docs" / "_plans"
        return cls(
            root=r,
            execution_state=plans / "execution-state.yaml",
            plans_glob="docs/**/*-plan.md",
            findings_queue=plans / "findings-followup-queue.md",
            fanout_runs=r / "docs" / "_fanout_runs",
            dispatch_loops=r / "docs" / "_dispatch_loops",
            chained_runs=r / "docs" / "_chained_runs",
            next_packets=r / "output" / "next-up",
            replan_dir=r / "docs" / "_replan",
            soaks_index=r / "docs" / "_soaks" / "index.yaml",
            picker_audits=r / "docs" / "_picker_audits",
            archive_lock=r / "docs" / "_fanout_runs" / ".archive.lock",
            lane_journal=plans / "lane-journal.jsonl",
            leases_dir=plans,
            project_card=None,
            style="repo",
            verdict_journal=plans / "verdict-journal.jsonl",
        )

    # The fields a `[paths]` table may override, and how each is coerced.
    #   * `plans_glob` is a plain string (a glob) — NOT resolved against root.
    #   * every other override is a path: a RELATIVE value resolves against
    #     `self.root` (so a host writes `planning/*.md`, not an absolute path),
    #     an absolute value is taken as-is.
    # `root` and `style` are deliberately NOT here:
    #   * `root` — re-pointing the workspace is `with_root`'s job (it rebuilds the
    #     whole layout under the new root); letting `[paths]` set `root` would
    #     desync `root` from the paths derived off it.
    #   * `style` — it is a DERIVED discriminator over the SHAPE of the other path
    #     fields (`repo` = reference docs/ layout, `dos` = `.dos/` layout), set by
    #     `for_root`/`for_dos_dir`, not an independent value. Letting `[paths]`
    #     override it lets the discriminator LIE about the field shapes, and a
    #     later `with_root` (which branches on `style`) would then rebuild the
    #     layout in the wrong shape — the exact Law-1 hazard `with_root`'s
    #     docstring warns against. Same desync rationale as `root`.
    _OVERRIDABLE_STR_FIELDS = frozenset({"plans_glob"})
    _OVERRIDABLE_PATH_FIELDS = frozenset({
        "execution_state", "findings_queue", "fanout_runs", "dispatch_loops",
        "chained_runs", "next_packets", "replan_dir", "soaks_index",
        "picker_audits", "archive_lock", "lane_journal", "leases_dir",
        "project_card", "verdict_journal",
    })

    def with_overrides(self, table: dict) -> "PathLayout":
        """Return a copy with the layout fields named in ``table`` overridden (WCR Phase 2).

        Pure. Starts from ``self`` (the caller passes the base layout, already
        built `for_root`/`for_dos_dir` under the workspace), then
        `dataclasses.replace`s only the fields the table names:

          * ``plans_glob`` / ``style`` are strings, taken verbatim.
          * every other known field is a path — a RELATIVE value resolves against
            ``self.root``, an absolute value is kept as-is. So a foreign repo whose
            plans live in ``planning/`` writes ``plans_glob = "planning/*.md"`` and
            (if it relocates state) ``execution_state = "planning/state.yaml"``.

        An UNKNOWN key raises `ValueError` (a typo'd path field — ``plnas_glob`` —
        is a host mistake worth surfacing loudly, not silently ignoring; the same
        posture `stamp.convention_from_table` takes on an unknown `[stamp]` key).
        ``root`` is not overridable (see the field-set note above).
        """
        if not isinstance(table, dict):
            raise ValueError(f"[paths] must be a table, got {type(table).__name__}")
        known = self._OVERRIDABLE_STR_FIELDS | self._OVERRIDABLE_PATH_FIELDS
        unknown = set(table) - known
        if unknown:
            raise ValueError(
                f"[paths] has unknown key(s) {sorted(unknown)}; "
                f"known keys are {sorted(known)}"
            )
        changes: dict[str, object] = {}
        for key, value in table.items():
            if key in self._OVERRIDABLE_STR_FIELDS:
                if not isinstance(value, str):
                    raise ValueError(
                        f"[paths].{key} must be a string, got {type(value).__name__}"
                    )
                changes[key] = value
            else:  # a path field
                if not isinstance(value, str):
                    raise ValueError(
                        f"[paths].{key} must be a path string, "
                        f"got {type(value).__name__}"
                    )
                p = Path(value)
                changes[key] = p if p.is_absolute() else (self.root / p)
        return replace(self, **changes)

    @classmethod
    def for_dos_dir(cls, root: Path | str) -> "PathLayout":
        """Build the generic ``.dos/`` layout under ``root`` (docs/74).

        DOS's own emissions (run dirs, the lane WAL, leases, verdict envelopes,
        the soak index, picker audits) move under a single per-project ``.dos/``
        home — a re-derivable, deletable, gitignored-by-default tree separate
        from the served repo's content. The host's plan registry — the truth DOS
        *reads*, not the scratch it *writes* — stays repo-relative, at a
        GENERIC, non-reference-shaped location (``dos.state.yaml`` at the root, NOT
        ``docs/_plans/execution-state.yaml`` — copying the reference app's path
        would re-bake a host's directory dialect into the domain-free default).

        The three run-dir fields collapse to one value (``.dos/runs``): they
        stay three *fields* for back-compat with `for_root`, but here they alias
        one directory. Run dirs keep their UTC-timestamp NAMES (the run-dir
        consumers — `picker_oracle._list_recent_runs`, `timeline` — parse a
        ``^\\d{8}T\\d{6}Z`` name); the ``run_id`` lineage lives INSIDE each
        run dir's ``run.json``, not in the dir name.
        """
        r = Path(root).resolve()
        d = r / ".dos"
        leases = d / "leases"
        runs = d / "runs"
        return cls(
            root=r,
            # Host registry — repo-relative, generic (NOT under .dos/, NOT reference-shaped).
            execution_state=r / "dos.state.yaml",
            plans_glob="docs/**/*-plan.md",
            findings_queue=r / "dos.findings.md",
            # DOS emissions — all under .dos/. The three run trees alias one dir.
            fanout_runs=runs,
            dispatch_loops=runs,
            chained_runs=runs,
            next_packets=d / "verdicts",
            replan_dir=d / "replan",
            soaks_index=d / "soaks" / "index.yaml",
            picker_audits=d / "picker_audits",
            archive_lock=leases / ".archive.lock",
            lane_journal=d / "lane-journal.jsonl",
            leases_dir=leases,
            project_card=d / "project.json",
            style="dos",
            verdict_journal=d / "verdict-journal.jsonl",
        )


@dataclass(frozen=True)
class WorkspaceFacts:
    """Facts ABOUT the served workspace, discovered once via I/O at build time.

    This is the third seam-value on ``SubstrateConfig`` — after ``lanes`` (which
    lanes exist) and ``paths`` (where state lives), it answers *what is true of
    this particular tree*. The motivating fact: **which of the kernel's own
    runtime files actually EXIST under this root.** The SELF_MODIFY admission
    guard must refuse a whole-repo (`**/*`) lease only when DOS is serving its
    OWN repo (those files are present, so a lease really could rewrite the live
    kernel) — and admit the same lease against a foreign repo (they are not, so
    nothing self-modifying is possible). That decision needs a filesystem probe,
    which a *pure* kernel verdict (`arbiter.arbitrate`) may not perform.

    Resolving it HERE — at config-build time, the same boundary that already does
    the `dos.toml` reads — keeps the arbiter pure: the probe runs once, the result
    is cached as data, and every later admission reads `cfg.workspace` instead of
    re-touching the disk. This is the same "I/O at the boundary, data to the pure
    core" discipline as `git_delta`/`journal_delta` feeding `liveness.classify`,
    lifted to the config seam so the *workspace itself* is a first-class object
    with discovered properties, not a bare root path re-probed ad hoc.

    ``None`` on a ``SubstrateConfig`` means "facts were not gathered" — the pure,
    I/O-free construction path (the dataclass default, a hand-built test config).
    A consumer that needs a fact treats ``None`` conservatively (the safe
    direction for a *safety* guard: assume the kernel files MIGHT be present when
    we never looked), exactly as `built_in_predicates(workspace=None)` does today.

      root                  — the resolved workspace root these facts describe
                              (carried so a fact set is self-identifying / never
                              silently applied to the wrong tree after a re-point).
      kernel_runtime_files  — the subset of `self_modify._DISPATCH_RUNTIME_FILES`
                              that exist under ``root``. Empty ⇒ a foreign repo;
                              the full set ⇒ DOS serving itself. The one fact that
                              makes the SELF_MODIFY guard workspace-aware without
                              an I/O call inside the pure arbiter.
      is_kernel_repo        — convenience flag (``kernel_runtime_files`` non-empty):
                              "is this the DOS kernel's own tree?" A `dos doctor`
                              row and a future self-host guard read it.
    """

    root: Path
    kernel_runtime_files: tuple[str, ...] = ()
    is_kernel_repo: bool = False


def gather_workspace_facts(workspace: Path | str | None = None) -> WorkspaceFacts:
    """Probe ``workspace`` once and freeze the discovered facts (the ONE I/O home).

    Called by the config BUILDERS (`default_config` / `job_config` /
    `load_workspace_config`) — the boundary that is already allowed to touch the
    disk — never by a pure verdict. Mirrors `self_modify.existing_runtime_files`
    (and reuses it): a foreign repo yields ``kernel_runtime_files=()`` (and
    `is_kernel_repo=False`); the DOS repo serving itself yields the full set.

    Imported lazily from `dos.self_modify` to keep the import graph a strict DAG
    — `config` is a near-leaf (only `reasons`/`stamp`), and `self_modify` pulls
    `admission`→`lane_overlap`→`_tree`; a top-level import here would couple the
    config seam to the admission chain. The lazy import keeps `config` importable
    on its own (the `admission.built_in_predicates` lazy-import rule, applied in
    the other direction).
    """
    root = resolve_workspace_root(workspace)
    from dos.self_modify import existing_runtime_files
    files = existing_runtime_files(root)
    return WorkspaceFacts(
        root=root,
        kernel_runtime_files=tuple(files),
        is_kernel_repo=bool(files),
    )


@dataclass(frozen=True)
class SubstrateConfig:
    """The complete per-workspace policy the domain-free mechanism reads.

    Constructed once by the host and threaded into the spine functions that used
    to read module-level constants. ``plan_meta_schema`` is a forward hook for a
    workspace whose plans carry a different frontmatter grammar (the §3
    derive-from-prose adapter for a brownfield repo); v0 leaves it ``None`` and
    the reference-shaped parsers are used.

    ``reasons`` is the refusal vocabulary as data (the second mechanism/policy
    split, after ``lanes``): which closed ``reason_class`` tokens a no-pick /
    blocked verdict may carry, each with its category / refusal-ness / man-page
    fields. Defaults to ``BASE_REASONS`` (the seven the reference spine shipped as
    a closed enum) so every existing consumer is byte-unchanged; a workspace adds
    its own with ``reasons=BASE_REASONS.extend([...])`` (or declares them in
    ``dos.toml`` — see ``dos.reasons``). The kernel's emit / verify / refuse / man
    surfaces all read this one declaration, so a declared reason is simultaneously
    emittable, verifiable, and refusable.

    ``stamp`` is the ship-stamp convention as data (the third mechanism/policy
    split): the grep rung's *subject grammar* — which commit-subject shapes count
    as a direct ship — that ``phase_shipped`` used to hardcode as the reference
    app's ``(docs|go|agents|…)/<SERIES>:`` prefix. Defaults to
    ``JOB_STAMP_CONVENTION`` (that exact grammar, lifted verbatim) so the
    reference userland app and the existing kernel suite are byte-for-byte
    unchanged; a foreign workspace declares its own dirs in
    ``dos.toml`` ``[stamp]`` (``dos.stamp.load_from_toml``) or installs
    ``GENERIC_STAMP_CONVENTION`` to recognise a bare ``<SERIES>: <PHASE>``. The
    truth syscall reads this one declaration, so ``verify`` is domain-free for any
    repo that declares (or inherits the generic) ship grammar.

    ``reason_morphology`` is rung 2 of the reason-class recognizer as data
    (``docs/105``): an ordered ``(substring → category)`` ``MorphologyRuleset`` the
    picker oracle's ``resolve_cause`` consults AFTER the exact rungs (frozen map +
    ``reasons`` registry) to classify the legible tail of LLM-authored compound
    ``reason_class`` tokens (``*FALSE_SHIP*``/``*OPERATOR*``/…) that exact equality
    misses. Defaults to ``GENERIC_REASON_MORPHOLOGY`` (domain-free shapes, no host
    lanes) so every workspace gets the legible-tail recovery out of the box; a host
    extends it in ``dos.toml`` ``[[reasons.morphology]]``
    (``dos.reason_morphology.load_from_toml``). Same mechanism/policy split as
    ``stamp``: the host widens what is *recognized*; the kernel keeps the closed
    ``NoPickCause`` set and every cross-check downstream of it.

    ``overlap_ratio_max`` / ``overlap_policy_name`` are the **overlap seam** (Axis
    7, ``docs/113``) — the pluggable disjointness scorer that decides whether two
    known trees may run concurrently. ``overlap_ratio_max`` (default ⅓) is the
    *data* knob: the soft-overlap tolerance the built-in ``prefix`` scorer admits
    under, declarable in ``dos.toml`` ``[overlap] ratio_max`` — the calibrated
    elbow `docs/90 §2` named a research stand-in, now a value not a hardcode.
    ``overlap_policy_name`` (default ``"prefix"``) names the *scorer* itself: the
    built-in deterministic prefix-ratio, or a workspace's ``dos.overlap_policies``
    entry-point plugin (an import-graph / semantic / model-backed scorer). Whatever
    the scorer, ``overlap_policy.admissible_under_floor`` AND-s it under the
    unforgeable prefix floor, so a swappable scorer can only refuse-MORE, never
    admit a collision (the structural soundness floor — `docs/113 §3`). Both are
    resolved at the call boundary and threaded into the arbiter's
    ``DisjointnessPredicate``; the pure ``arbitrate`` default path is unchanged.

    ``env`` is the **environment print** (Axis "under-what", ``docs/115``): a
    content-addressed `EnvPrint` of the runtime the config was built in — kernel
    version + kernel git SHA + Python + OS/arch + declared tool versions — gathered
    ONCE at the build boundary (the `gather_workspace_facts` sibling) and stamped
    onto the durable surfaces so an adjudication records *under what* it ran, not
    just *what* it decided. ``None`` on the pure construction path (a hand-built test
    config never probes the runtime), treated as "not recorded" by every consumer —
    exactly as ``workspace=None`` is. A pure verdict is HANDED a print to stamp, the
    way it is handed a clock; it never requires one. The `EnvPrint.digest` is the
    `EnvId` docs/115 primitive 3's `FLEET_ENV_MISMATCH` gate compares to a declared
    pin (not yet wired — Phase 1 records the print; the refuse is a later phase).

    ``retention`` is the **retention seam** (`docs/106 §3.3`, the answer to
    `docs/94 §7`'s open question): the size/recency caps governing how much DOS
    scratch to keep — the WAL compaction threshold (``journal_max_entries`` /
    ``journal_max_age_days``) and the keep-last-N reaper caps for `.dos/runs/`,
    `.dos/**/.verdict-*.json`, and `.dos/audits/` (the audit-report class the
    2026-06-03 trajectory audit surfaced). Defaults to ``GENERIC_RETENTION``
    (generous caps, never zero) so every workspace self-bounds out of the box; a
    host declares its own in `dos.toml [retention]` (`dos.retention.load_from_toml`).
    Same mechanism/policy split as ``stamp``/``overlap_*``: this object carries only
    the *numbers* and the one pure threshold (`retention.should_compact`); the
    collector's load-bearing floor — **never reap a live lease** — is enforced
    independently of these caps (the journal `compact` fold + the reaper's liveness
    gate), so a misconfigured cap can waste disk but can never collect live state.

    ``data_class`` is the **data-class seam** (the "tag agent-trajectory data vs
    actual product changes" answer): WHICH paths hold re-derivable agent-run
    scratch (``TRAJECTORY``/``AUDIT``) vs measure-then-change anchors (``BASELINE``)
    vs deliverables (``PRODUCT``), as declared glob patterns. The retention reaper
    and any clutter audit read this ONE classifier instead of each hard-coding a
    root list. Defaults to ``GENERIC_DATA_CLASS`` — `.dos/`-relative patterns only,
    so DOS stays domain-free (it names no host's `docs/` tree; the host declares its
    own in `dos.toml [data_class]` via `dos.data_class.load_from_toml`). Same
    mechanism/policy split as ``stamp``/``retention``: this carries only the
    *patterns* and the one pure classifier (`DataClassPolicy.classify`); what a
    consumer DOES with a class (reap / keep / grace) is the consumer's policy.

    ``supervise`` is the **supervisor seam** (`docs/99`, the always-on population
    program): the `SupervisePolicy` that shapes how many dispatch-loop workers the
    supervisor keeps alive across the lane roster — ``target`` (the desired live
    population), ``count_spinning_as_alive`` (whether a SPINNING worker counts as
    up), and ``reap_stalled`` (whether a STALLED worker yields a REAP). Before this
    seam those three were reachable ONLY as a Python parameter / the ``dos loop
    --target`` flag, with the two booleans not surfaced at all. Now a workspace
    declares the standing policy ONCE in `dos.toml [supervise]`
    (`dos.supervise.load_from_toml`) and BOTH the `dos loop` emitter and the
    long-lived watchdog driver read the same declaration; an explicit ``--target``
    still overrides the config target at the call boundary. Defaults to
    ``DEFAULT_SUPERVISE_POLICY`` (target 1, count spinners, reap the dead) — the
    same mechanism/policy split as ``cooldown``/``stamp``: the kernel owns the
    population verdict, the workspace owns the numbers.

    ``non_git_oracle`` / ``ci`` are the **non-git evidence seam** (`docs/109`/`docs/265`):
    WHICH out-of-kernel witness ``verify`` consults *beyond git*, and that witness's
    own policy knobs. ``non_git_oracle`` is the `dos.evidence_sources` name (e.g.
    ``"ci_status"``) the truth syscall upgrades a git ship-verdict against; default
    ``""`` = **off** = git-only ``verify``, byte-identical to today (the
    back-compatible widening rule, the `test_verify_no_plan.py` contract untouched).
    It is read from `dos.toml [verify] non_git_oracle`. ``ci`` is the raw
    `dos.toml [ci]` table (``provider``/``repo``/``required`` …) passed THROUGH to the
    named driver, never interpreted by the kernel (the `_resolve_driver_config`
    posture — the kernel folds the table to data and hands it to the boundary; the
    driver decides what its keys mean). The asymmetry the seam keeps sound is in
    `oracle.is_shipped`: a non-git rung may only make ``verify`` answer MORE
    skeptically (upgrade a `source` over a commit git ALREADY found, or withhold the
    upgrade), never promote ``shipped=False → True`` — so wiring this can only add
    accountability, never manufacture a ship (`docs/265 §1`). The kernel verb stays
    provider-blind: the `gh api` subprocess lives in the driver's ``gather``/
    ``status_of``, resolved BY NAME at the `cmd_verify` boundary.
    """

    lanes: LaneTaxonomy
    paths: PathLayout
    plan_meta_schema: object | None = None
    reasons: ReasonRegistry = BASE_REASONS
    stamp: StampConvention = JOB_STAMP_CONVENTION
    reason_morphology: MorphologyRuleset = GENERIC_REASON_MORPHOLOGY
    workspace: "WorkspaceFacts | None" = None
    env: "EnvPrint | None" = None
    overlap_ratio_max: float = _DEFAULT_OVERLAP_RATIO_MAX
    overlap_policy_name: str = "prefix"
    class_budgets: ClassBudgets = NO_CLASS_BUDGETS
    retention: RetentionPolicy = GENERIC_RETENTION
    data_class: DataClassPolicy = GENERIC_DATA_CLASS
    interventions: InterventionLadder = BASE_INTERVENTIONS
    stream_policy: StreamPolicy = DEFAULT_STREAM_POLICY
    precursors: PrecursorGrammar = EMPTY_PRECURSOR_GRAMMAR
    enumerate_grammar: "EnumerateGrammar" = GENERIC_GRAMMAR
    cooldown: "CooldownPolicy" = DEFAULT_COOLDOWN_POLICY
    lifecycle: "LifecyclePolicy" = GENERIC_LIFECYCLE
    supervise: "SupervisePolicy" = DEFAULT_SUPERVISE_POLICY
    marker: MarkerPolicy = DEFAULT_MARKER_POLICY
    non_git_oracle: str = ""
    ci: dict = field(default_factory=dict)
    # docs/342 M3 / docs/126 P3 / docs/114 §A3 — the BINDING completion rung.
    # ``exec_cmd`` is a HOST-SUPPLIED acceptance command (`dos.toml [verify] exec_cmd`):
    # when set, `verify` runs it through `drivers/os_acceptance` at the very commit the
    # git rung matched and BINDS completion on the OS exit code — an un-authored rung an
    # `--allow-empty` forged subject cannot clear (a non-zero exit DEMOTES the ship to
    # NOT_SHIPPED; exit 0 mints `source='exec-attested'`). Default ``""`` = off =
    # git-only `verify`, byte-identical (the kernel NEVER fabricates a command — where
    # none is declared the binding default is the file-path rung, never an invented
    # exec). ``prefer_artifact_rung`` (`dos.toml [verify] prefer_artifact_rung`) is the
    # NO-exec binding default: when True, a ship resting only on the forgeable
    # grep-SUBJECT is demoted (the file-path artefact rung is preferred). Default
    # ``False`` = off = byte-identical. Both are refuse-MORE only.
    exec_cmd: str = ""
    prefer_artifact_rung: bool = False

    @property
    def root(self) -> Path:
        return self.paths.root

    @property
    def kernel_runtime_files(self) -> tuple[str, ...] | None:
        """The cached subset of kernel-runtime files present under this workspace.

        ``None`` when facts were never gathered (the pure construction path) — a
        consumer reads that as "unknown, stay conservative." A gathered fact set
        returns the tuple (empty for a foreign repo, full for the DOS repo). The
        SELF_MODIFY guard reads THIS instead of re-probing the disk, which is what
        lets `arbitrate` stay pure while still being workspace-aware (the whole
        point of caching the facts on the config — see `WorkspaceFacts`).
        """
        return self.workspace.kernel_runtime_files if self.workspace else None

    def state_path(self) -> Path:
        """The execution-state registry this workspace keeps (may not exist).

        A convenience over ``paths.execution_state`` so callers — and the truth
        syscall's no-plan contract — can ask the *config* for the registry path
        without reaching through the layout. A workspace with no phased plan
        simply has no file here; ``verify`` then answers from git alone.
        """
        return self.paths.execution_state

    def with_root(self, root: Path | str) -> "SubstrateConfig":
        """Return a copy re-pointed at a different workspace root.

        Rebuilds the layout under the new root, preserving the layout STYLE: a
        config built with the generic ``.dos/`` layout re-points to a `.dos/`
        layout under the new root, a reference (`for_root`) config re-points to a
        reference layout. We branch on ``paths.style`` rather than always calling
        ``for_root`` — the latter would silently drag a `.dos/`-configured
        workspace back onto the reference ``docs/_plans`` tree (a correctness
        hazard for Law 1: the reference layout must not move, and a `.dos/` config
        must not become a reference config). The common case stays "same layout,
        different tree".

        Workspace FACTS are root-specific, so a re-point must re-gather them (a
        stale fact set would describe the OLD tree — e.g. claim the new root is
        the kernel repo because the old one was). We only re-probe when this
        config already HAD gathered facts (``workspace is not None``): a pure,
        never-probed config stays pure under re-point (facts remain ``None``), so
        `with_root` does no surprise I/O for a hand-built test config — it gathers
        only if the original did.
        """
        factory = (
            PathLayout.for_dos_dir if self.paths.style == "dos"
            else PathLayout.for_root
        )
        new_facts = (
            gather_workspace_facts(root) if self.workspace is not None else None
        )
        return replace(self, paths=factory(root), workspace=new_facts)


# ---------------------------------------------------------------------------
# The reference userland app's lane taxonomy now lives in the `dos._job_policy`
# leaf, NOT in this near-leaf config module — domain lane names (apply/tailor/
# discovery) do not belong in the kernel core (the 2026-06-01 layering audit in
# `dos.drivers.job` named exactly this relocation: "a third home BOTH layers may
# import — a `dos._job_policy` leaf, say"). `config` is layer 2; `_job_policy`
# imports only `LaneTaxonomy` from here, so a module-top import of the literal
# back into `config` would cycle. We expose `JOB_LANE_TAXONOMY` as a
# backward-compatible attribute via PEP-562 `__getattr__` (lazy, resolved on
# access — after both modules are loaded), so `from dos.config import
# JOB_LANE_TAXONOMY` still works for legacy callers while the literal's *home* is
# the leaf. `job_config()` reads it the same lazy way (below).
# ---------------------------------------------------------------------------


def __getattr__(name: str):
    """PEP-562 lazy attribute: resolve `JOB_LANE_TAXONOMY` from the leaf.

    Keeps the legacy `from dos.config import JOB_LANE_TAXONOMY` import working
    without a module-load cycle (the literal's home moved to `dos._job_policy`,
    which imports `LaneTaxonomy` from THIS module). Any other unknown attribute
    raises the normal `AttributeError`.
    """
    if name == "JOB_LANE_TAXONOMY":
        from dos._job_policy import JOB_LANE_TAXONOMY  # noqa: PLC0415
        return JOB_LANE_TAXONOMY
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve_workspace_root(workspace: Path | str | None = None) -> Path:
    """The active workspace root, per the resolution order in the module doc."""
    if workspace is not None:
        return Path(workspace).resolve()
    env = os.environ.get(ENV_WORKSPACE)
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def resolve_dos_home(home: Path | str | None = None) -> Path:
    """The machine-local DOS_HOME root, per precedence (highest first):

      1. an explicit ``home`` arg (a caller / test pointing it directly),
      2. the ``DISPATCH_HOME`` env var,
      3. ``$XDG_DATA_HOME/dos`` (the XDG base-dir spec on Linux/macOS),
      4. (win32 only) ``%APPDATA%\\dos``,
      5. ``~/.dos`` (the universal fallback).

    Every branch is ``Path(...).resolve()``'d so a project-id keyed on a path is
    stable. This NEVER creates the directory — a read-only syscall must be able
    to ASK for the home path without a write happening; ``home.ensure_dos_home``
    is the only creator. Mirrors ``resolve_workspace_root``'s precedence idiom.
    """
    if home is not None:
        return Path(home).resolve()
    env = os.environ.get(ENV_DOS_HOME)
    if env:
        return Path(env).resolve()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return (Path(xdg) / "dos").resolve()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return (Path(appdata) / "dos").resolve()
    return (Path.home() / ".dos").resolve()


@dataclass(frozen=True)
class HomeLayout:
    """The machine-local DOS_HOME paths — per-MACHINE and root-invariant.

    Distinct from ``PathLayout`` (which is per-workspace and rebuilt by
    ``with_root``): DOS_HOME does not move when the active workspace changes, so
    it is NOT a field on ``PathLayout``. It holds the central, rebuildable
    projection store (docs/74): a registry of every project DOS has touched and
    a log of resolved-decision digests, plus the cross-process mutex that
    serializes their writes.
    """

    home: Path
    config_toml: Path        # home / "config.toml"  — machine-global prefs
    projects_index: Path     # home / "projects" / "index.jsonl"  (rich, rewritten by reindex)
    roots_log: Path          # home / "projects" / "roots.log"  (durable path registry, append-only)
    decisions_log: Path      # home / "decisions.jsonl"
    home_lock: Path          # home / ".home.lock"  — cross-process write mutex

    @classmethod
    def for_home(cls, home: Path | str | None = None) -> "HomeLayout":
        h = resolve_dos_home(home)
        return cls(
            home=h,
            config_toml=h / "config.toml",
            projects_index=h / "projects" / "index.jsonl",
            roots_log=h / "projects" / "roots.log",
            decisions_log=h / "decisions.jsonl",
            home_lock=h / ".home.lock",
        )


def job_config(workspace: Path | str | None = None, *,
              gather_env: bool = True) -> SubstrateConfig:
    """The reference userland app's policy, pointed at ``workspace``.

    The reference userland app imports this and passes it everywhere. The lane
    taxonomy is sourced from the workspace's ``dos.toml`` ``[lanes]`` table (the
    userland policy now lives where it belongs — in the consumer repo, not baked
    into this kernel package); ``dos._job_policy.JOB_LANE_TAXONOMY`` is only the
    domain-free *structural fallback* used when the workspace has no ``[lanes]``
    declaration (a foreign checkout, a test tmp_path). The path layout is the
    reference app's default. Pointing it at a different root (e.g. for a test
    fixture) is one argument.

    Implementation: build the base ``SubstrateConfig`` from the structural
    fallback literal, then layer the workspace ``dos.toml`` over it via
    ``load_workspace_config(root, base=base)``. Passing an explicit ``base`` takes
    the ``base is not None`` branch in ``load_workspace_config`` (it never
    re-enters ``job_config``), so this is recursion-safe by construction — and it
    means every direct ``job_config()`` caller (the live arbiter, the TUI,
    ``check_phase_shipped``, ``decisions``) reads the SAME ``dos.toml``-sourced
    taxonomy instead of the raw literal diverging from what the CLI/MCP see.
    """
    # Lazy import: the literal's home is the `dos._job_policy` leaf (it imports
    # `LaneTaxonomy` from here, so a module-top import would cycle). Resolved at
    # call time, after both modules are loaded — same lazy-import discipline as
    # `gather_workspace_facts` deferring `dos.self_modify`.
    from dos._job_policy import JOB_LANE_TAXONOMY  # noqa: PLC0415

    root = resolve_workspace_root(workspace)
    base = SubstrateConfig(
        lanes=JOB_LANE_TAXONOMY,
        paths=PathLayout.for_root(root),
        workspace=gather_workspace_facts(root),
        env=gather_env_print() if gather_env else None,
    )
    # Layer the workspace's `dos.toml` ([lanes] REPLACES the base taxonomy when
    # declared; absent → the structural fallback above stands). `base=` keeps this
    # out of the `job_config()` re-entry path in `load_workspace_config`.
    return load_workspace_config(root, base=base)


def default_config(workspace: Path | str | None = None, *,
                   gather_env: bool = True) -> SubstrateConfig:
    """A minimal, domain-free config for an arbitrary workspace.

    The third-directory / `dos init` case: a folder of plan-markdown with no
    declared lanes yet. One generic ``main`` cluster lane + the standard
    exclusive ``global``, so `dos dispatch` produces a typed verdict out of the
    box without any workspace-specific policy. Hosts that want real concurrency
    declare their own taxonomy.

    Ship-stamp grammar: the generic config carries ``GENERIC_STAMP_CONVENTION``
    (no dir prefix — a bare ``<SERIES>: <PHASE>`` / ``<slug> Phase <N>:`` counts
    as a direct ship), NOT the reference-strict ``(docs|go|…)/`` grammar the
    `SubstrateConfig` dataclass defaults to. This is what makes ``verify`` work
    **out of the box** against a foreign repo whose commits don't carry the
    reference app's dir prefixes (`hybrid-cache-type Phase 4:`): the no-`dos.toml`
    path now matches the convention the repo actually uses instead of resolving
    every real ship `via none` (F9). The asymmetry with `job_config` is
    deliberate and safe: the reference userland app consumes `job_config` (which
    keeps the strict grammar + its bookkeeping guards), so the reference app and
    the kernel suite are byte-unchanged; only the generic foreign-repo path
    loosens — and the generic convention still
    carries the universal release-bundle + bulk-snapshot guards, so it is not a
    free-for-all. A repo that needs the strict grammar still declares it in
    `dos.toml [stamp]` (or passes `--job`).

    ``gather_env`` (default ``True``) controls whether the runtime `EnvPrint` is
    probed and stamped onto ``env``. A caller that never reads ``cfg.env`` — the
    MCP server's per-tool-call config build is the motivating one — passes
    ``gather_env=False`` to skip the probe entirely, leaving ``env=None`` (the
    documented "not recorded" state every consumer already handles, identical to
    the pure-construction path). The default stays ``True`` so the CLI / doctor /
    intent-ledger callers are byte-unchanged. (Even when ``True`` the probe is
    cheap after the first call thanks to `env_print.gather_env_print`'s per-process
    memo; ``gather_env=False`` removes it from the path altogether.)
    """
    root = resolve_workspace_root(workspace)
    lanes = LaneTaxonomy(
        concurrent=("main",),
        exclusive=("global",),
        autopick=("main",),
        trees={"main": ("**/*",), "global": ("**/*",)},
        aliases={},
    )
    # The generic default adopts the `.dos/` layout (docs/74): DOS's own
    # emissions live under a per-project `.dos/` home, not scattered into the
    # served repo's `docs/` tree. `job_config` keeps `for_root` — the reference
    # layout must not move. This is the ONLY place the layout flips to `.dos/`.
    return SubstrateConfig(
        lanes=lanes,
        paths=PathLayout.for_dos_dir(root),
        stamp=GENERIC_STAMP_CONVENTION,
        workspace=gather_workspace_facts(root),
        env=gather_env_print() if gather_env else None,
    )


# ---------------------------------------------------------------------------
# The declarative on-ramp for lanes & paths: read the `[lanes]` / `[paths]`
# tables out of a workspace's `dos.toml` (WCR — docs/71). These mirror
# `reasons.load_from_toml` / `stamp.load_from_toml` in shape, but with the
# deliberate asymmetry the plan calls out:
#
#   * reasons are ADDITIVE (`base.extend(...)`) — declaring a reason means
#     "these on top of the base set".
#   * lanes/paths are REPLACE / OVERRIDE — a host declaring `[lanes]` means
#     "these are MY lanes" (not the reference app's plus mine); a host declaring
#     `[paths]` overrides only the layout fields it names and inherits the rest.
#
# Same additive-degradation guarantee on the file axis, though: absent file,
# absent/empty table, or no `tomllib` → the supplied ``base`` unchanged, so a
# workspace that declared nothing is byte-identical to today. A present-but-
# malformed table raises (surfaced, not swallowed), exactly like the siblings.
# ---------------------------------------------------------------------------


def _load_toml_table(path: Path | str, key: str) -> dict | None:
    """Read `[<key>]` out of a `dos.toml`, or None if there's nothing to read.

    Returns None when the file is absent, `tomllib` is unavailable (py<3.11 with
    no `tomli`), or the `[<key>]` table is missing/empty/not-a-table — every
    "degrade to base" case the WCR loaders share. A present, non-empty table is
    returned as the raw dict for the caller's `*_from_table` to validate. Mirrors
    the file-handling half of `reasons.load_from_toml` so the two seams behave
    identically on a missing/garbled config.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return None
    # Read via `utf-8-sig` so a UTF-8 BOM is transparently stripped (it is a no-op
    # when absent). PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM by
    # default, and raw `tomllib.load(rb)` chokes on it ("Invalid statement at line
    # 1") — which would silently demote a perfectly valid declared table to the
    # base value (an additive-degradation-law violation: a present, well-formed
    # table must NOT be silently dropped). `loads(read_text(utf-8-sig))` fixes it
    # for both tomllib and tomli.
    data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
    table = data.get(key)
    if not isinstance(table, dict) or not table:
        return None
    return table


def load_lanes_from_toml(
    path: Path | str, *, base: LaneTaxonomy
) -> LaneTaxonomy:
    """Build a `LaneTaxonomy` from a `dos.toml`'s `[lanes]` table (WCR Phase 1).

    A present `[lanes]` table REPLACES ``base`` wholesale — lanes are not additive
    the way reasons are: a host declaring lanes means "these are my lanes," not
    "these plus the reference taxonomy's." Absent file / absent-or-empty table →
    ``base`` unchanged (additive degradation). Present-but-malformed → raise (via
    `LaneTaxonomy.from_table`), surfaced rather than swallowed.
    """
    table = _load_toml_table(path, "lanes")
    if table is None:
        return base
    return LaneTaxonomy.from_table(table)


def load_class_budgets_from_toml(
    path: Path | str, *, base: ClassBudgets = NO_CLASS_BUDGETS
) -> ClassBudgets:
    """Build a `ClassBudgets` from a `dos.toml`'s `[[concurrency_class]]` array (C13).

    `[[concurrency_class]]` parses to a top-level LIST (not a `[key]` dict like
    `[lanes]`), so this reads the raw `data` and pulls the array directly rather than
    via `_load_toml_table`. A present array REPLACES ``base`` (a host declaring its
    classes means "these are my budgets"). Absent file / absent-or-empty array →
    ``base`` (additive degradation: no budgets = today's unbounded-per-kind behavior).
    Present-but-malformed → raise (via `ClassBudgets.from_table`)."""
    p = Path(path)
    if not p.exists():
        return base
    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return base
    data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
    arr = data.get("concurrency_class")
    if not arr:  # absent or empty → base (degrade)
        return base
    return ClassBudgets.from_table(arr)


def load_paths_from_toml(
    path: Path | str, *, base: PathLayout
) -> PathLayout:
    """Build a `PathLayout` from a `dos.toml`'s `[paths]` table (WCR Phase 2).

    A present `[paths]` table OVERRIDES only the layout fields it names (relative
    paths resolve against ``base.root``); every unnamed field inherits ``base``.
    Absent file / absent-or-empty table → ``base`` unchanged. A present table with
    an unknown key → raise (a typo'd path field is a host mistake worth surfacing,
    `PathLayout.with_overrides`' posture).
    """
    table = _load_toml_table(path, "paths")
    if table is None:
        return base
    return base.with_overrides(table)


def load_overlap_from_toml(
    path: Path | str, *, base_ratio_max: float, base_policy_name: str,
) -> tuple[float, str]:
    """Read the `[overlap]` table (Axis 7, `docs/113`) → ``(ratio_max, policy_name)``.

    The overlap seam's *data* attachment — the soft-overlap tolerance and the
    named scorer. A present `[overlap]` table OVERRIDES the two fields it names;
    an absent/empty table inherits ``base_*``. Keys (both optional):

      * ``ratio_max`` — a float in (0, 1], the soft-overlap fraction the built-in
        ``prefix`` scorer admits under. The kernel default is ⅓.
      * ``policy`` — the scorer name: ``"prefix"`` (built-in) or a
        ``dos.overlap_policies`` plugin name.

    Malformed values RAISE ``ValueError`` (surfaced by `load_workspace_config`'s
    warn-and-fall-back, the shared posture): a ``ratio_max`` that is not a number
    or sits outside (0, 1] is a host mistake worth a one-line notice, not a silent
    no-op that would leave the operator believing a looser tolerance is in force.
    An unknown key is rejected the same way (a typo'd ``[overlap]`` field would
    otherwise silently do nothing). Note: an out-of-range ``ratio_max`` only ever
    affects what the *policy* admits — the deterministic floor it is AND-ed against
    stays ⅓ regardless (`overlap_policy.floor_decision`), so even a malformed
    config that slipped through could never admit a prefix-colliding pair.
    """
    table = _load_toml_table(path, "overlap")
    if table is None:
        return base_ratio_max, base_policy_name
    allowed = {"ratio_max", "policy"}
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(
            f"unknown [overlap] key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )
    ratio_max = base_ratio_max
    if "ratio_max" in table:
        raw = table["ratio_max"]
        try:
            ratio_max = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"[overlap] ratio_max must be a number, got {raw!r}")
        if not (0.0 < ratio_max <= 1.0):
            raise ValueError(
                f"[overlap] ratio_max must be in (0, 1], got {ratio_max!r}"
            )
    policy_name = base_policy_name
    if "policy" in table:
        policy_name = str(table["policy"])
    return ratio_max, policy_name


def load_workspace_config(
    workspace: str | Path | None = None,
    *,
    job: bool = False,
    base: SubstrateConfig | None = None,
    gather_env: bool = True,
    warn=None,
) -> SubstrateConfig:
    """Build the config for ``workspace``, folding in its ``dos.toml`` tables.

    The single shared implementation of the four-table readback that BOTH the
    `dos` CLI (`cli._apply_workspace`) and the MCP server (`dos_mcp`) need — they
    used to carry byte-identical copies of this loop, which is exactly the drift
    risk the registry-as-data design exists to kill. Factoring it here removes
    the duplication; each caller decides what to DO with the result (the CLI
    `set_active`s it; the server passes it explicitly into each syscall).

    Layering, highest precedence first (WCR Phase 3a):
      the four `dos.toml` tables › a pre-built ``base`` driver config (from the
      CLI's ``--driver`` loader) OR the ``--job`` reference taxonomy (``job=True``)
      › the `default_config` generic. So a `dos.toml [lanes]` overrides whatever
      base it was given, and declaring nothing degrades cleanly to the generic
      default. ``base`` is an already-built `SubstrateConfig` the caller resolved
      (the CLI resolves `dos.drivers.<name>.<name>_config` by convention); this
      function never learns a host name — it only sees an opaque config to layer
      over, which keeps the one-way arrow (kernel/config names no driver) intact.
      When both ``base`` and ``job`` are passed, ``base`` wins.

    The two deliberate asymmetries (kept intact):
      * `[reasons]` is ADDITIVE onto the base set; `[lanes]`/`[paths]`/`[stamp]`
        REPLACE/OVERRIDE.
      * lanes/paths default GENERIC (you declare your real ones — the safe
        direction); stamp defaults STRICT (you loosen it knowingly).

    A missing/empty table always leaves the built-in base, so a workspace that
    declared none is byte-identical to the generic default. A *present but
    malformed* table must not crash a command that never touches that policy
    axis (a `verify` with a broken `[lanes]`, say), so it is warned and the base
    is kept — the shared warn-and-fall-back posture. ``warn`` is the sink for
    that one-line notice ``(label, message)``; it defaults to a stderr print.
    Pass your own to capture/redirect it (a server may not want stderr noise).

    ``gather_env`` (default ``True``) is forwarded to the underlying
    ``default_config`` / ``job_config`` builder: pass ``False`` to skip probing
    the runtime `EnvPrint` (the git-SHA subprocess + platform query) when the
    caller never reads ``cfg.env`` — the MCP server's per-tool-call build. It is a
    no-op when ``base`` is supplied (the builder already decided that base's
    ``env``); the `dos.toml` layering above never touches ``env``.
    """
    import dataclasses
    import sys

    from dos import reasons as _reasons
    from dos import stamp as _stamp
    from dos import reason_morphology as _reason_morphology
    from dos import retention as _retention
    from dos import data_class as _data_class

    if warn is None:
        def warn(label: str, message: str) -> None:
            print(f"warning: ignoring malformed [{label}] in {toml_path}: "
                  f"{message}", file=sys.stderr)

    if base is not None:
        cfg = base
    else:
        cfg = (job_config(workspace, gather_env=gather_env) if job
               else default_config(workspace, gather_env=gather_env))
    toml_path = cfg.paths.root / "dos.toml"

    def _layer(label: str, load, current):
        try:
            return load()
        except ValueError as e:
            warn(label, str(e))
            return current

    # [reasons] — ADDITIVE onto the base registry.
    cfg = dataclasses.replace(cfg, reasons=_layer(
        "reasons", lambda: _reasons.load_from_toml(toml_path, base=cfg.reasons),
        cfg.reasons))
    # [stamp] — OVERRIDE the base ship-subject grammar.
    cfg = dataclasses.replace(cfg, stamp=_layer(
        "stamp", lambda: _stamp.load_from_toml(toml_path, base=cfg.stamp),
        cfg.stamp))
    # [enumerate] — OVERRIDE the phase-list-producer STYLE grammar (docs/207 Phase 2).
    # The repo declares heading levels / table scan / bare-Phase fallback / rollup;
    # the per-plan `series` is layered at the call boundary (`enumerate.with_series`),
    # NOT here. Absent inherits the generic markdown grammar. Malformed warns + keeps base.
    from dos import enumerate as _enumerate
    cfg = dataclasses.replace(cfg, enumerate_grammar=_layer(
        "enumerate",
        lambda: _enumerate.load_from_toml(toml_path, base=cfg.enumerate_grammar),
        cfg.enumerate_grammar))
    # [cooldown] — OVERRIDE the anti-churn windows (docs/207 §3). A present key
    # overrides that window; absent inherits the generic default (6h / 30m). The
    # window is a HINT (a too-long window only delays a re-pick, never wedges a
    # clean unit), so malformed warns + keeps base — the safe direction.
    from dos import cooldown as _cooldown
    cfg = dataclasses.replace(cfg, cooldown=_layer(
        "cooldown",
        lambda: _cooldown.load_from_toml(toml_path, base=cfg.cooldown),
        cfg.cooldown))
    # [supervise] — OVERRIDE the always-on population policy (docs/99): the
    # target live-worker count + whether a spinner counts as up + whether a
    # STALLED worker is reaped. A present key overrides; absent inherits the
    # generic default (target 1, count spinners, reap the dead). Malformed warns +
    # keeps base — the supervisor is advisory/effect (it emits a plan; even the
    # driver's reap is idempotent), so a broken policy degrades to the safe
    # default rather than wedging the roster.
    from dos import supervise as _supervise
    cfg = dataclasses.replace(cfg, supervise=_layer(
        "supervise",
        lambda: _supervise.load_from_toml(toml_path, base=cfg.supervise),
        cfg.supervise))
    # [lifecycle] — OVERRIDE the plan-class taxonomy + transition triggers (docs/207
    # §5c). A present table replaces the class set / transitions / failsafes; absent
    # inherits the generic active/done. A transition naming an unknown class raises
    # (validated shape); malformed warns + keeps base — the safe direction (a broken
    # lifecycle table can never auto-transition a plan, it just keeps the default).
    from dos import lifecycle as _lifecycle
    cfg = dataclasses.replace(cfg, lifecycle=_layer(
        "lifecycle",
        lambda: _lifecycle.load_from_toml(toml_path, base=cfg.lifecycle),
        cfg.lifecycle))
    # [[reasons.morphology]] — OVERRIDE the rung-2 recognizer (docs/105). A present
    # list replaces the base ruleset; an explicit empty list turns rung 2 off;
    # absent inherits the kernel's generic morphology.
    cfg = dataclasses.replace(cfg, reason_morphology=_layer(
        "reasons.morphology",
        lambda: _reason_morphology.load_from_toml(toml_path, base=cfg.reason_morphology),
        cfg.reason_morphology))
    # [lanes] — REPLACE the base taxonomy wholesale (WCR Phase 1).
    cfg = dataclasses.replace(cfg, lanes=_layer(
        "lanes", lambda: load_lanes_from_toml(toml_path, base=cfg.lanes),
        cfg.lanes))
    # [paths] — OVERRIDE only the named layout fields (WCR Phase 2).
    cfg = dataclasses.replace(cfg, paths=_layer(
        "paths", lambda: load_paths_from_toml(toml_path, base=cfg.paths),
        cfg.paths))
    # [overlap] — OVERRIDE the disjointness scorer's tolerance + named policy
    # (Axis 7, docs/113). A two-field table, so it layers both at once via a
    # tuple; a malformed value warns and keeps the base pair (no axis touched).
    _overlap = _layer(
        "overlap",
        lambda: load_overlap_from_toml(
            toml_path,
            base_ratio_max=cfg.overlap_ratio_max,
            base_policy_name=cfg.overlap_policy_name,
        ),
        (cfg.overlap_ratio_max, cfg.overlap_policy_name),
    )
    cfg = dataclasses.replace(
        cfg, overlap_ratio_max=_overlap[0], overlap_policy_name=_overlap[1])
    # [retention] — OVERRIDE the scratch-retention caps (docs/106 §3.3). A present
    # key overrides that cap; absent inherits the generic default (generous, never
    # zero). Malformed warns + keeps the base — a bad cap can never loosen the
    # "never reap a live lease" floor, which is the collector's, not these numbers'.
    cfg = dataclasses.replace(cfg, retention=_layer(
        "retention", lambda: _retention.load_from_toml(toml_path, base=cfg.retention),
        cfg.retention))
    # [data_class] — OVERRIDE the path → data-class glob patterns (the trajectory-
    # vs-product tagging seam). A present key replaces that class's patterns;
    # absent inherits the generic default (.dos/-relative only). Malformed warns +
    # keeps base — an unclassified path falls through to PRODUCT (the safe
    # direction: a class-keyed reaper can never reap what it can't classify).
    cfg = dataclasses.replace(cfg, data_class=_layer(
        "data_class",
        lambda: _data_class.load_from_toml(toml_path, base=cfg.data_class),
        cfg.data_class))
    # [[concurrency_class]] — REPLACE the per-kind lease budgets (docs/97 Phase 2,
    # C13). A present array means "these are my class budgets"; absent/empty keeps
    # the base (no budget = today's unbounded-per-kind). Malformed warns + keeps base.
    cfg = dataclasses.replace(cfg, class_budgets=_layer(
        "concurrency_class",
        lambda: load_class_budgets_from_toml(toml_path, base=cfg.class_budgets),
        cfg.class_budgets))
    # [intervention] — EXTEND the actuation ladder (docs/143 §13). A present
    # [intervention.X] table adds a rung-with-rank to BASE_INTERVENTIONS; absent
    # inherits the built-in OBSERVE<WARN<BLOCK<DEFER set. Purely additive (the
    # [reasons] seam shape) — malformed warns + keeps base.
    from dos import intervention as _intervention
    cfg = dataclasses.replace(cfg, interventions=_layer(
        "intervention",
        lambda: _intervention.load_from_toml(toml_path, base=cfg.interventions),
        cfg.interventions))
    # [tool_stream] — OVERRIDE the stall-reader windows (docs/145). A present
    # [tool_stream] table replaces repeat_n/stall_n/ignore_tools; absent inherits
    # the generic default (REPEATING at 3, STALLED at 5). Malformed warns + keeps base.
    from dos import tool_stream as _tool_stream
    cfg = dataclasses.replace(cfg, stream_policy=_layer(
        "tool_stream",
        lambda: _tool_stream.load_from_toml(toml_path, base=cfg.stream_policy),
        cfg.stream_policy))
    # [marker] — OVERRIDE the wait-marker budget knobs (docs/274). A present [marker]
    # table tunes the no-op-turn cap (max_streak, handed to noop_streak), WHICH env
    # vars arm the budget (arm_on_env — a host names its own loop sentinel), and whether
    # Claude Code's stop_hook_active backstop is honored. Absent inherits the generic
    # interactive-safe default (armed only by an explicit loop signal). Malformed warns +
    # keeps base. The arming DECISION stays the pure marker_gate.decide; this only
    # supplies its inputs.
    from dos import marker_gate as _marker_gate
    cfg = dataclasses.replace(cfg, marker=_layer(
        "marker",
        lambda: _marker_gate.load_from_toml(toml_path, base=cfg.marker),
        cfg.marker))
    # [precursor] — REPLACE the mandated-precursor grammar (docs/147). A present
    # [precursor.requires] / [precursor.aliases] table declares which mutating tool
    # needs which lookup first; absent inherits the EMPTY grammar (the gate
    # NO_SIGNALs everything = today's behavior). Hand-authored from the policy prose,
    # NEVER inferred (inferring it is parsing policy = planner-adjacent). Malformed
    # warns + keeps base.
    from dos import precursor_gate as _precursor_gate
    cfg = dataclasses.replace(cfg, precursors=_layer(
        "precursor",
        lambda: _precursor_gate.load_from_toml(toml_path, base=cfg.precursors),
        cfg.precursors))
    # [verify] / [ci] — OVERRIDE which non-git witness `verify` consults + that
    # witness's pass-through policy (docs/109/265). `[verify] non_git_oracle` names a
    # `dos.evidence_sources` driver (a string); absent → "" → git-only `verify`,
    # byte-identical to today (the no-plan contract is untouched). `[ci]` is the
    # driver's raw policy table (`provider`/`repo`/`required`), handed THROUGH to the
    # named driver and never interpreted by the kernel — so a malformed/foreign key
    # is the driver's to validate, not this fold's. Both reads are inline (a string +
    # a raw dict, no nested grammar to validate), so they degrade to base on a missing
    # table the same way the `*_from_toml` siblings do; a tomllib parse fault is
    # warned + base-kept (the shared warn-and-fall-back posture, the safe direction —
    # a broken table can never turn the conjunctive rung INTO a false ship, only leave
    # it off). An explicit non_git_oracle on the `base` config is overridden by a
    # present `[verify]` table, the same precedence the other tables take.
    def _load_verify_ci():
        v_table = _load_toml_table(toml_path, "verify")
        ci_table = _load_toml_table(toml_path, "ci")
        oracle_name = cfg.non_git_oracle
        exec_cmd = cfg.exec_cmd
        prefer_artifact = cfg.prefer_artifact_rung
        if v_table is not None:
            raw = v_table.get("non_git_oracle", oracle_name)
            if not isinstance(raw, str):
                raise ValueError(
                    f"[verify] non_git_oracle must be a string, got {type(raw).__name__}")
            oracle_name = raw.strip()
            # docs/342 M3 — the host's BINDING-rung declarations. `exec_cmd` is the
            # acceptance command `verify` runs through `os_acceptance` and binds the
            # ship on; `prefer_artifact_rung` is the no-exec stricter default. Both are
            # inline scalars (a string + a bool), so they validate here and degrade to
            # base on a missing key the same way `non_git_oracle` does.
            raw_exec = v_table.get("exec_cmd", exec_cmd)
            if not isinstance(raw_exec, str):
                raise ValueError(
                    f"[verify] exec_cmd must be a string, got {type(raw_exec).__name__}")
            exec_cmd = raw_exec.strip()
            raw_pref = v_table.get("prefer_artifact_rung", prefer_artifact)
            if not isinstance(raw_pref, bool):
                raise ValueError(
                    f"[verify] prefer_artifact_rung must be a bool, got {type(raw_pref).__name__}")
            prefer_artifact = raw_pref
        ci = dict(ci_table) if ci_table is not None else cfg.ci
        return oracle_name, ci, exec_cmd, prefer_artifact
    _verify_ci = _layer(
        "verify", _load_verify_ci,
        (cfg.non_git_oracle, cfg.ci, cfg.exec_cmd, cfg.prefer_artifact_rung))
    cfg = dataclasses.replace(
        cfg, non_git_oracle=_verify_ci[0], ci=_verify_ci[1],
        exec_cmd=_verify_ci[2], prefer_artifact_rung=_verify_ci[3])
    return cfg


# The process-wide active config. Lazily initialised from the environment so a
# bare `import dos; dos.config.active()` works without ceremony, while a host
# that wants explicit control calls `set_active(...)` at startup. This mirrors
# the reference spine's "module-level STATE_PATH read from env" idiom, but the
# value is now a full config object, not a bare path.
_ACTIVE: SubstrateConfig | None = None


def active() -> SubstrateConfig:
    """The process-wide active config (env-resolved on first use)."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = default_config()
    return _ACTIVE


def set_active(config: SubstrateConfig) -> None:
    """Install ``config`` as the process-wide active config."""
    global _ACTIVE
    _ACTIVE = config


def ensure(config: SubstrateConfig | None = None) -> SubstrateConfig:
    """Return ``config``, or the process-active config when it is ``None``.

    The one-liner behind the ``cfg = config if config is not None else
    config.active()`` idiom every projection/reader repeats (`decisions`,
    `dispatch_top`, `timeline`, the watchdog `run`, …). Centralizing it gives
    those call sites a single, typed entry point — and one place to change how a
    ``None`` config resolves — instead of the conditional copy-pasted at each
    boundary. A non-``None`` config is returned unchanged (never re-resolved), so
    an explicit ``cfg=`` passed by a library caller still wins, exactly as before.
    """
    return config if config is not None else active()


# The process-wide active DOS_HOME. Resolved LAZILY (and cached) on first use,
# NOT via a `default_factory` on every SubstrateConfig construction — DOS_HOME is
# per-machine and root-invariant, so re-resolving it on every config build (every
# read-only syscall, every test fixture) would be needless env-churn. A test
# redirects it by `set_active_home(...)` or `DISPATCH_HOME` before first use, or —
# the robust idiom — by passing the optional `home=` arg every `dos.home`
# reader/writer accepts (so it never has to reset this global).
_ACTIVE_HOME: HomeLayout | None = None


def active_home() -> HomeLayout:
    """The process-wide active DOS_HOME layout (env-resolved on first use)."""
    global _ACTIVE_HOME
    if _ACTIVE_HOME is None:
        _ACTIVE_HOME = HomeLayout.for_home()
    return _ACTIVE_HOME


def set_active_home(home: HomeLayout) -> None:
    """Install ``home`` as the process-wide active DOS_HOME layout."""
    global _ACTIVE_HOME
    _ACTIVE_HOME = home
