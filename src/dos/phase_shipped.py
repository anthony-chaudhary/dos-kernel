"""Check whether a plan phase has already shipped, by scanning git log for
plan-doc commits.

Both /next-up (when listing next-unblocked phases) and
/fanout-true-headless-multi-agent (Step 1.5 at-launch re-validation) use this.
The whole point is to catch picks that shipped *before* the packet's own
"Last commit" SHA — pure generator-side staleness that the packet's own
freshness header cannot detect.

Usage:
    python scripts/check_phase_shipped.py <series> <phase> [<plan-doc-path>]

Examples:
    python scripts/check_phase_shipped.py RS RS4 docs/resume-surfacing-plan.md
    python scripts/check_phase_shipped.py TF TF11.1 docs/38_top-of-funnel-quality-plan.md
    python scripts/check_phase_shipped.py UP UP2.1.4
    python scripts/check_phase_shipped.py LP LP10c.1 docs/33_apply-driver-consolidation-plan.md

    # JSON output for /next-up batch mode:
    python scripts/check_phase_shipped.py --json AT AT5

Exit codes (single-pick / --batch / --json):
    0  — phase has shipped (caller should drop this pick)
    1  — phase has not shipped (caller can proceed)
    2  — usage / error
    3  — UNCERTAIN (text mode only): a WEAK git verdict was found but no
         plan-doc path was given to cross-check it. Re-run with the plan doc.

Exit codes (--check-packet):
    0  — found-shipped (any pick is already in git log → caller should re-run)
    1  — not-shipped   (all picks clean)
    2  — no-coverage   (file parsed but no recognisable `### N. ...` headers)
    3  — parse-error   (file IO / decode error)

  --check-packet false-NEGATIVE backstop (AAR-FQ230, finding #230): when all
  subject-token passes miss but a pick names a plan doc, the verdict is
  re-derived from the FILE PATHS the doc's phase row names vs the file paths
  recent commits touched (`git log -- <file>`). This catches DT2-shape ships
  whose commit subject lacks the phase token — the subject-token lineage
  (#62/#63/#77/#103/#136/#226) is subject-regex widening; this matches the
  artefact instead. SHIPPED requires one commit touching >= 2 of the phase's
  named load-bearing files (false-positive guard). See `_check_phase_by_filepath`.

Stdout (text mode):
    SHIPPED <short-sha> <commit summary>     # if shipped (exit 0)
    NOT_SHIPPED                               # if not    (exit 1)
    UNCERTAIN <sha> <summary> — ...           # WEAK verdict, no plan_doc (exit 3)

Pass the plan-doc path whenever you have it. A bare two-arg call cannot
cross-check a WEAK (release-prefix / body-mention) verdict against the plan
body's SHIPPED stamp, so a stale plan doc can fool it — the exact divergence
behind /next-up packet false-positives (a renderer pre-screen that passes
plan_doc said NOT_SHIPPED while a bare spot-check said SHIPPED). With the doc
path supplied the WEAK verdict is demoted when the stamp is absent.

Stdout (--json mode):
    {"shipped": true, "sha": "8ea6ee8", "summary": "docs/RS: RS4 — ..."}

Detection:
  Scans `git log --oneline -1500` for commits whose summary matches either
    1. `(docs|go)/<SERIES>:?\\s+<PHASE><not-suffix>` (direct ship; Go-side
       handlers ship under `go/<SERIES>:` rather than `docs/<SERIES>:`), or
    2. `<summary-bundle>:.*<PHASE><not-suffix>`     (a summary commit bundles
       several phases into its free-form summary line).
  A *summary-bundle* subject is either a `vX.Y.Z:` release commit OR the
  `docs/HYG:` hygiene-audit prefix. These are the only non-direct subjects a
  real phase ship lands under: a release commit bundles several ships into one
  `vX.Y.Z:` summary, and HYG phases ship under `docs/HYG:`. Also scans the
  *bodies* of the most recent summary-bundle commits for a phase that appears
  only inside an extended body; the summary-line mention is caught by the
  cheaper oneline pass first.

  ⚓ Ship-shaped, not mentioned (FQ-77 — recurring-5, BOTH directions). The
  pass condition is "this commit SHIPPED <PHASE>", not "this commit's subject
  NAMES <PHASE>". Two distinct shapes carry phase ids without shipping them:

    - **Bookkeeping subjects.** `docs/_plans:` (next-up soft-claims / replan
      sweeps), `docs/fanout:` / `docs/dispatch:` / `docs/dispatch-loop:`
      (run-archive rollups that QUOTE other runs' git-log history), and bulk
      `working-dir snapshot:` commits all name phase ids as narrative. Every
      such commit in this repo's history is bookkeeping — none is a real ship
      attribution (verified 2026-05-19). OS-FQ63 wrongly allowlisted the first
      three as summary-bundle ship-evidence, which produced the FQ-77
      false-POSITIVE: `8d4d2851 docs/fanout: archive ... (FB0 shipped,
      FB2/FB3 halted)` counted as an FB2 ship, culling the only live pick →
      empty packet → `verdict=DRAIN`. FQ-77 supersedes #62/#63's coverage-gap
      framing (it is a *specificity* defect, not a missing prefix): these
      prefixes are EXCLUDED from counting as a ship — see
      `_is_bookkeeping_subject` / `_BOOKKEEPING_SUBJECT_RE`.

    - **Bulk-snapshot file-path coincidence.** A `working-dir snapshot:`
      commit sweeps hundreds of files at once, so it incidentally touches a
      phase's load-bearing files even when the phase's deliverables do not
      exist (live false-positive 2026-05-19: `1647b0c0` flagged OC4 shipped).
      The file-path backstop (`_check_phase_by_filepath`) excludes such
      commits from the overlap count for the same reason.

  The original FQ-77 false-NEGATIVE (short phase tags following a `vX.Y.Z:`
  prefix, e.g. `a6b1a785 v0.307.0: AAR15.3 ..., TF9 ..., ACR1 ...`) stays
  closed: `vX.Y.Z:` is a ship-shaped subject and its bundled tags resolve via
  the release-prefix scan.

  Phase-id alternation: queries containing `'` also try the `prime` spelling
  (and vice versa), since plan docs use `MG3'-1` (canonical) but commits use
  `MG3prime-1` (Windows-quoting workaround).

  Free-form HYG fallback: HYG phase IDs are snake-case slugs but commit
  subjects often carry prose form. After the literal passes miss, HYG
  queries normalize subjects (lowercase + collapse `[\\s\\-_]+` → `_`) and
  substring-match the slug. Catches `dropbox_zero_apply` ↔
  `docs/HYG: Dropbox zero-apply picker audit (queue #20)`.

  Phase id matching is **strict**: `RS4` matches `RS4` but not `RS40`, not
  `RS4.1`, and not `RS4-port`. The boundary set disallows alnum, dot, AND
  hyphen on either side of the token — so suffix variants like
  `SF1.2-port` are recognised as distinct from base phase `SF1.2`.
  Operators who want to verify a sub-phase (e.g. `RS4.1` or `SF1.2-port`)
  should pass it exactly.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

# The reference-app convention, imported once at the top so the back-compat
# aliases below (`_PROGRESS_MARKER_WORDS`, `_REPO_PATH_RE`, the infra sets) can be
# derived from it byte-identically. The lifted file-path / progress / fallback
# grammar all lives on `StampConvention` now (the genericization); these aliases
# exist only for `from dos.phase_shipped import *` consumers, never for live code.
from dos.stamp import (  # noqa: F401 — re-exported for `from dos.phase_shipped import *` back-compat
    JOB_STAMP_CONVENTION as _JOB_STAMP_CONVENTION,
    _UNIVERSAL_DIAGRAM_SUFFIXES as _DIAGRAM_SUFFIXES,
)

# Windows default cp1252 stdout raises UnicodeEncodeError on `→`/`—`/`±` chars
# that show up in commit summaries when this helper smoke-tests series-port
# phase ids. UTF-8 reconfigure is the canonical fix (Python 3.7+); guard with
# hasattr so older runtimes / detached stdout wrappers don't blow up.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _workspace_root():
    """The served workspace whose git history is scanned (separation refactor)."""
    from dos import config as _config

    return _config.active().paths.root


def _git_log(args: list[str]) -> list[str]:
    """Run `git log` with the given args. Returns the lines. Raises on error.

    Runs in the served workspace so the grep rung scans the target repo's
    history, not the dos package's own (the workspace-parameterized port).
    """
    result = subprocess.run(
        ["git", "log"] + args,
        cwd=str(_workspace_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # Never inherit the caller's stdin: inside a long-lived stdio server
        # (dos-mcp) it is the live transport pipe, and a git child holding it
        # wedges on Windows — the docs/295 stall.
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")
    return result.stdout.splitlines()


# Global oneline-scan window (commit count). SIZED BY CADENCE, NOT TIME — a phase
# that genuinely shipped but fell out of this window reads as a false-NEGATIVE
# (`shipped=false`), which the picker then treats as a *live pick that no longer
# exists*, contributing to the apply-lane WEDGE-storm. FQ-409 root cause: the LF
# series shipped 2026-05-09..13 (commits `34f8032e`/`f60b8217`) but the
# dispatch-loop + concurrent-loop archive churn pushed them to position ~3000
# from HEAD in ~18 days — past the old -1500 window — so every LF phase
# false-NEGATIVED and re-appeared as a phantom "remaining" pick. The window must
# stay ahead of the real ship-to-now commit distance; -4000 ≈ the current
# ~1.5-month depth at today's cadence (cost: ~0.15s vs 0.12s for -1500,
# negligible). When cadence rises again, raise this — or move ship-truth to the
# durable plan-body SHIPPED stamp (the real fix; see `_consult_plan_body`).
_ONELINE_WINDOW = 4000
# Per-file backstop window. A phase's named files churn far slower than the
# global log, so an explicit-pathspec `-- <file>` scan reaches much further back
# in time than _ONELINE_WINDOW for the same commit budget — this is the
# cadence-resilient path. Kept generous so a slow-moving phase's artefact is
# still found.
_FILEPATH_WINDOW = 800


# Phase-boundary character class.
# A phase id `SF1.2` must NOT match `SF1.2-port`, `SF1.2.1`, or `SF1.20`.
# The original regex used `\b`, which treats `-`/`.` as boundaries and
# yields a false-positive match against `SF1.2-port` and `RS4.1`.
# Disallow alnum, dot, AND hyphen on either side of the token.
_BOUNDARY_NEG = r"(?![A-Za-z0-9.\-])"
_BOUNDARY_PRE_NEG = r"(?<![A-Za-z0-9.\-])"

# FQ-326 — open-soak markers. A plan-doc phase section can carry a `SHIPPED`
# token (a pre-soak substrate stamp, or a heading stamp that drifted) while the
# phase's ACTUAL completion still gates on an UNCLOSED soak window. A naive
# `"SHIPPED" in section` then false-positives that phase as shipped (the #326
# surface: the picker thinks soak-gated work shipped, masking the live close-out
# pick). These structural phrases name a phase whose own close is soak-gated and
# still open — date-blind on purpose (the kernel never reads a clock; a future
# `closes <date>` is matched as text, the registry/picker owns the date math).
# Conservative: each phrase indicates the soak gates THIS phase's close, not a
# soak merely mentioned as separate downstream follow-up.
#
# The cross-clause alternatives bound their gap with `[^.\n]*` (NOT `.*`) so a
# match cannot cross a sentence/line boundary — "gates on the upstream commit.
# The soak audit …" must NOT match (two separate sentences), only a same-clause
# "gates on a 30d soak". This tightening is load-bearing now that the guard
# DEMOTES (returns False) rather than defers: an over-match would false-demote a
# genuinely-shipped section that merely mentions a soak follow-up downstream.
_OPEN_SOAK_MARKER_RE = re.compile(
    r"(?im)("
    r"status:\s*in_progress"                 # a soak-ledger row pasted inline
    r"|gate[sd]?\s+on\b[^.\n]*\bsoak"        # "gates on a ... soak" (same clause)
    r"|soak\b[^.\n]*\bgate[sd]?\b"           # "soak ... gates the delete" (same clause)
    r"|until\s+the\s+soak\s+(?:window\s+)?closes"
    r"|soak\s+window\b[^.\n]*\bcloses\b"     # "soak window ... closes 2026-06-27"
    r"|→\s*tomb\b|->\s*tomb\b"               # "### CRSn — 7d soak → tomb"
    r"|\bzero-emit\s+(?:window|floor|read)\b"
    r")"
)

# ---------------------------------------------------------------------------
# The ship-stamp grammar is now per-workspace DATA (the SCV genericization).
# What a commit subject must look like to count as a ship moved out of these
# module constants into `dos.stamp.StampConvention`; the active workspace's
# convention (`SubstrateConfig.stamp`, defaulting to `JOB_STAMP_CONVENTION`)
# supplies the three regex fragments the matchers below interpolate. This file
# keeps the *mechanism* (the scans, the demotions); the *grammar* is the seam.
#
# The historical why-it's-shaped-this-way notes that used to live on the raw
# constants now annotate `JOB_STAMP_CONVENTION` in `dos.stamp` — the one bit of
# curated prose lives beside the data it explains. The load-bearing facts, in
# brief, so a reader here isn't sent away:
#
#   * direct-ship dirs (`docs|go|agents|job_search|scripts`) — ships land under
#     the top-level dir the deliverable lives in (FQ-409 widened `(docs|go)` to
#     include the code dirs; the bookkeeping exclusion is all `docs/…`, so the
#     widening added no false-POSITIVE surface).
#   * summary-bundle prefixes (`docs/HYG:`, plus the universal `vX.Y.Z:`) — the
#     only NON-direct subjects a real ship lands under; an allowlist, never a
#     relaxed `docs/<anything>:`, because the release/body scans are unanchored.
#   * bookkeeping prefixes (`docs/_plans:` soft-claims, `docs/fanout|dispatch|
#     dispatch-loop:` archive rollups, `docs/_soaks:` ledger rows, the universal
#     `… snapshot:` bulk commit) — subjects that NAME phase ids as narrative and
#     must never count as a ship, on ANY scan path (FQ-77, both directions).
#
# A foreign repo with no `[stamp]` table inherits the GENERIC convention (no dir
# prefix → a bare `<SERIES>: <PHASE>` ships), which is what makes `verify`
# domain-free (the SCV North Star). See `dos.stamp` for the full provenance.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Matchers:
    """The compiled subject-matcher fragments for ONE workspace's convention.

    Built once per entrypoint by `_subject_matchers(cfg)` and threaded down into
    every scan (`_check_phase_with_cache`, `_check_phase_by_filepath`) and the
    bookkeeping pre-filter (`_is_bookkeeping_subject`). Carrying the resolved
    convention + fragments in one value — rather than re-reading `_config.active()`
    in each helper — is what makes design-law 2 (multi-entrypoint oracle
    consistency) structural: every path reads the SAME convention, resolved once,
    because they are handed the same `_Matchers`. A fix to the grammar lands in
    `dos.stamp` and reaches all three scan paths through this one object.

      convention     — the resolved `StampConvention`; `direct_ship_core` defers
                       to it because the direct-ship shape depends on the
                       per-call series/phase (the glued `<SERIES><PHASE>:` form).
      direct_prefix  — the `(?:docs|go|…)/` (or generic optional-path) fragment,
                       used by the HYG / sub-phase-parent patterns (job-shaped).
      summary_subject— the `(?:vX.Y.Z:|docs/HYG:)` fragment gating the
                       release-prefix scan and the body-scan's in-summary check.
      bookkeeping    — the compiled, start-anchored, case-insensitive matcher for
                       a NAMES-but-doesn't-ship subject.
    """

    convention: object
    direct_prefix: str
    summary_subject: str
    bookkeeping: "re.Pattern[str]"
    repo_path: "re.Pattern[str]"

    def direct_ship_core(self, series_re: str, phase_alt: str) -> str:
        """The full direct-ship regex core for this convention (see `dos.stamp`).

        Delegates to `StampConvention.direct_ship_core`: the dir prefix + the
        series/phase shape (prefixed for job, prefixed-OR-glued for generic). The
        caller anchors a boundary after it.
        """
        return self.convention.direct_ship_core(series_re, phase_alt)

    def is_bookkeeping_subject(self, subject: str) -> bool:
        """True when `subject` NAMES phase ids as narrative (see `dos.stamp`).

        `subject` is the bare commit summary — NOT prefixed with the sha. Callers
        holding an `<sha> <summary>` oneline must strip the sha first.
        """
        return bool(self.bookkeeping.match((subject or "").strip()))

    def is_shared_infra(self, path: str) -> bool:
        """True if `path` is a shared-infra hub excluded from the file-path
        overlap count (see `StampConvention.is_shared_infra`). Per-workspace data:
        universal hubs ∪ the convention's declared extras, plus the universal
        `docs/…*.mmd|*.png` diagram rule."""
        return self.convention.is_shared_infra(path)


def _subject_matchers(cfg: object | None = None) -> _Matchers:
    """Resolve the active workspace's `StampConvention` into compiled matchers.

    The ONE place every entrypoint funnels through to learn the ship grammar
    (design-law 2). With no `cfg`, reads `dos.config.active().stamp` — which the
    CLI has already populated from `dos.toml` (`cli._apply_workspace`) and which
    a `phase_shipped` SUBPROCESS bootstraps from its own workspace (see
    `_bootstrap_active_config`), so the grep rung honours the same convention
    whether it runs in-process or shelled out. An explicit `cfg` (a
    `SubstrateConfig`) overrides — for a library caller verifying a specific
    workspace without installing it as active.
    """
    if cfg is None:
        from dos import config as _config

        cfg = _config.active()
    conv = getattr(cfg, "stamp", None)
    if conv is None:  # defensive — a config without a stamp falls back to job
        from dos.stamp import JOB_STAMP_CONVENTION

        conv = JOB_STAMP_CONVENTION
    return _Matchers(
        convention=conv,
        direct_prefix=conv.direct_prefix_re(),
        summary_subject=conv.summary_subject_re(),
        bookkeeping=conv.bookkeeping_subject_re(),
        repo_path=conv.repo_path_re(),
    )


def _parse_batch_line(line: str) -> tuple[str, str, str | None]:
    """Split one `--batch` stdin line into (series, phase, plan_doc).

    Two wire formats, auto-detected by the presence of a TAB:

      * **Tab-delimited** (the programmatic producer — `oracle.default_grep_
        fallback_batch`): ``series \\t phase [\\t plan_doc]``. The fields are
        taken VERBATIM between tabs, so a series OR phase containing spaces
        survives intact — the benchmark's ``hybrid-cache-type`` + ``Phase 4``
        (space in the phase) and even ``SGLang charts`` + ``Phase 3b.2`` (space
        in the series) round-trip correctly. This is the F7 fix: the old
        ``line.split(None, 2)`` truncated ``"Phase 4"`` to ``"Phase"`` and shoved
        ``"4"`` into ``plan_doc``, so the parent's ``(series, phase)`` lookup key
        never matched and a SHIPPED phase resolved ``via none``.

      * **Whitespace-delimited** (the legacy / manual form — a human running
        ``python -m dos.phase_shipped --batch`` and typing ``RS RS4 docs/x.md``):
        ``line.split(None, 2)``. Preserved byte-for-byte so the documented CLI
        and any existing caller that feeds space-separated single-token ids is
        unchanged. Multi-word ids are not expressible in this form (they never
        were) — a producer that needs them emits tabs.

    Returns ``("", "", None)`` for a blank/garbage line (caller skips it).
    Pure — no I/O, unit-testable.
    """
    if "\t" in line:
        parts = [p.strip() for p in line.split("\t")]
        series = parts[0] if parts else ""
        phase = parts[1] if len(parts) > 1 else ""
        # A 3rd tab field is the plan doc; trailing empty fields (a doc-less pair
        # emitted as `series\tphase\t`) collapse to None.
        plan_doc = parts[2] if len(parts) > 2 and parts[2] else None
        return series, phase, plan_doc
    parts = line.split(None, 2)
    if len(parts) < 2:
        return "", "", None
    return parts[0], parts[1], (parts[2] if len(parts) >= 3 else None)


def _bootstrap_active_config() -> None:
    """Install the right ship-stamp convention into this process's active config.

    Called once at the top of `main()` — i.e. when `phase_shipped` runs as the
    grep-rung SUBPROCESS (`python -m dos.phase_shipped ...`). That child re-derives
    `config.active()` from scratch (env-resolved `default_config()`), so without
    this it would always use the JOB default and ignore a convention the PARENT
    installed (`oracle.is_shipped(cfg=...)` → `set_active`) or one the workspace
    DECLARED in `dos.toml`. The bootstrap resolves the convention in precedence
    order and re-installs the active config carrying it:

      1. ``DISPATCH_STAMP_CONVENTION`` env var (JSON `to_dict` form) — the parent's
         active convention, the authoritative cross-process signal. This covers
         the library `cfg=` path even when the workspace has no `dos.toml`.
      2. the workspace's ``dos.toml`` ``[stamp]`` table — the declarative path for a
         repo invoked directly (`dos verify --workspace <repo>` shells the rung in
         that repo, where its own `dos.toml` lives). Read relative to the active
         workspace root, the same root the rung greps.
      3. otherwise leave the default (job) convention untouched — byte-identical
         to the pre-SCV subprocess.

    Best-effort and total: any parse/IO fault degrades to the current active
    config rather than crashing the rung (the truth syscall must never crash for
    a malformed override — it answers honestly from whatever convention it could
    resolve). A malformed `dos.toml [stamp]` IS surfaced on the CLI's own
    `_apply_workspace` path; here, in the shelled-out rung, we stay defensive.
    """
    import json
    import os
    from dos import config as _config
    from dos import stamp as _stamp

    cur = _config.active()
    # (1) explicit convention handed down by the parent process.
    raw = os.environ.get(_config.ENV_STAMP_CONVENTION)
    if raw:
        try:
            conv = _stamp.StampConvention.from_dict(json.loads(raw))
            import dataclasses
            _config.set_active(dataclasses.replace(cur, stamp=conv))
            return
        except Exception:
            pass  # fall through to the dos.toml / default path
    # (2) the workspace's own dos.toml [stamp] table.
    try:
        toml_path = cur.paths.root / "dos.toml"
        conv = _stamp.load_from_toml(toml_path, base=cur.stamp)
        if conv is not cur.stamp:
            import dataclasses
            _config.set_active(dataclasses.replace(cur, stamp=conv))
    except Exception:
        pass  # (3) leave the default convention in place


def _is_bookkeeping_subject(subject: str, matchers: "_Matchers | None" = None) -> bool:
    """True when `subject` is a bookkeeping commit that NAMES phases as narrative.

    Backward-compatible shim over `_Matchers.is_bookkeeping_subject`: a caller
    that already resolved `matchers` passes it (every in-module scan does, so the
    convention is read once per entrypoint); a caller that doesn't resolves the
    active convention on the spot. Soft-claims, replan sweeps, run-archive
    rollups, and bulk working-dir snapshots quote phase ids without shipping them;
    FQ-77 excludes them from every ship-detection path so a mention can never be
    misread as a ship (the false-POSITIVE half of the recurring-5).

    `subject` is the bare commit summary — NOT prefixed with the sha. Callers
    that hold an `<sha> <summary>` oneline must strip the sha first.
    """
    m = matchers if matchers is not None else _subject_matchers()
    return m.is_bookkeeping_subject(subject)


def _oneline_subject(line: str) -> str:
    """Return the bare summary from a `<sha> <summary>` oneline string.

    `git log --oneline` emits `<short-sha> <summary>`; the bookkeeping filter
    keys on the summary, so split off the leading sha token. A line with no
    space (degenerate) yields ``""``.
    """
    parts = line.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


# The progress-marker vocabulary is now per-workspace DATA
# (`StampConvention.progress_markers`) — the L1 genericization. Words that, when
# they appear immediately after the phase id with a bare space (no `:`/`—`/`-`),
# mark a commit as PROGRESS on a multi-step phase rather than a SHIP of it (a
# `<PHASE> week-1`/`<PHASE> audit` soak commit). Hardcoding them fired on EVERY
# repo, so a foreign repo's genuine `cache: Phase 0 audit of …` direct ship was
# silently demoted to NOT_SHIPPED (a real Benchmark false-negative). The vocabulary
# moved to `JOB_STAMP_CONVENTION.progress_markers`; the generic convention declares
# none, so a foreign repo's ships are never demoted. The historical why-each-word
# notes (the CS6 `§why`/`todo` and the AAR10 `week-1` provenance) now annotate the
# tuple in `dos.stamp` beside the data they explain. `_is_progress_only` reads the
# active convention through `matchers`, never this alias.
#
# Kept as a BACK-COMPAT alias derived from `JOB_STAMP_CONVENTION` so a
# `from dos.phase_shipped import *` consumer still sees the byte-identical frozenset.
_PROGRESS_MARKER_WORDS = _JOB_STAMP_CONVENTION.progress_marker_set()


# Generic "Phase N" token — a phase id that carries NO series prefix of its
# own (`Phase 6`, `Phase 2.6`). Plan docs whose phases use the bare-ordinal
# heading style (PSC, docs/09, the login subsystem) name phases this way.
# Such a token is NOT self-qualifying: `Phase 6` collides literally across
# every such plan. OS-FQ136 series-qualifies these — see `_phase_variants`
# (synonym half) and `_check_phase_with_cache` (release/body guard half).
_GENERIC_PHASE_RE = re.compile(r"(?i)^phase\s*(\d+(?:\.\d+)?)$")
# No-space series-prefixed form — `PSC5`, `AAR15.3`. The series prefix here
# IS the discriminator, so this form is self-qualifying. The OS-FQ136
# synonym pairs a generic `Phase N` with the `<SERIES>N` form and vice versa.
_SERIES_NUM_RE = re.compile(r"(?i)^([A-Za-z]+)\s*(\d+(?:\.\d+)?)$")


def _is_generic_phase_token(phase: str) -> bool:
    """True when `phase` is a bare `Phase N` token with no series prefix.

    Generic tokens collide literally across plans — PSC `Phase 6` and the
    docs/09 pipeline-events `Phase 6` are unrelated work. The release-prefix
    and body scans must series-qualify these (see `_check_phase_with_cache`).
    """
    return bool(_GENERIC_PHASE_RE.match(phase.strip()))


def _release_body_alternation(series: str, phase: str) -> str:
    """Return the series-qualified phase alternation for the release/body scans.

    The release-prefix and body scans have no `docs/<SERIES>:` prefix to anchor
    them — a `.*?` jumps across the whole `vX.Y.Z:` summary/body. So any bare
    `Phase N` literal in their alternation matches an *unrelated* plan's phase
    (finding #226: `EV EV1` matched `v0.37.0: ... Login Subsystem Phase 1`).

    OS-FQ136 series-qualified the case where the *query itself* is a generic
    `Phase N`. This helper closes the symmetric leak: a series-prefixed query
    (`EV1`) whose `_phase_variants` *expands into* a bare `Phase N` synonym —
    that synonym must not reach the unanchored alternation bare. Only forms
    that carry the series token (self-qualifying) are accepted bare; the
    generic `Phase N` synonym is allowed only when `<SERIES>`-adjacent
    (`EV Phase 1` / `EV: Phase 1`).

    The predicate: a `_phase_variants` form is safe-bare iff it contains the
    series token case-insensitively. Generic `Phase N` synonyms are rebuilt as
    `<SERIES>\\s*:?\\s+Phase\\s*N`. Series-prefixed query tokens therefore keep
    their self-qualifying forms; only the leaked generic synonym is constrained.
    """
    series_re = re.escape(series)
    safe: list[str] = []
    for variant in _phase_variants(phase, series):
        # `variant` is already re.escape()'d. Unescape just enough to test
        # for the series substring (escaping only inserts backslashes before
        # metacharacters; the series token is alnum so it is unaffected).
        if series and series.lower() in variant.lower():
            safe.append(variant)
        else:
            gm = _GENERIC_PHASE_RE.match(re.sub(r"\\(.)", r"\1", variant))
            if gm:
                num_re = re.escape(gm.group(1))
                # Series-adjacent literal: `EV Phase 1` / `EV: Phase 1`.
                safe.append(rf"{series_re}\s*:?\s+Phase\s*{num_re}")
            else:
                # No series token and not a generic `Phase N` — keep it
                # (apostrophe/prime spellings of a series-prefixed id land
                # here only if the series substring test missed, which it
                # does not for alnum series tokens; defensive).
                safe.append(variant)
    return "|".join(safe)


def _phase_variants(phase: str, series: str = "") -> list[str]:
    """Return regex-escaped phase-id forms to try, covering known conventions.

    Apostrophe ↔ `prime`: plan docs use `MG3'-1` (canonical) but commits use
    `MG3prime-1` (Windows-quoting workaround). Either spelling may appear on
    either side of the lookup. This keeps the script convention-blind:
    callers do not need to know which form the commit happens to use.

    OS-FQ136 — `Phase N` ↔ `<SERIES>N` synonym. Plans like PSC use bare
    `Phase 6` headings, but ship commits write either form: `docs/PSC: Phase 6
    TOMB` (spaced) and `v0.315.0: PSC5 score worker pool` (no-space, series-
    prefixed). When `series` is supplied, a generic `Phase N` query also tries
    `<SERIES>N` / `<SERIES> N`, and a `<SERIES>N` query also tries `Phase N` /
    `Phase<space>N`. Closes the finding #136 false-negative (no-space form
    missed). The release/body series-qualification guard (in the caller)
    closes the matching false-positive.
    """
    variants = {phase}
    if "'" in phase:
        variants.add(phase.replace("'", "prime"))
    if "prime" in phase:
        variants.add(phase.replace("prime", "'"))
    if series:
        gm = _GENERIC_PHASE_RE.match(phase.strip())
        if gm:
            num = gm.group(1)
            # `Phase 6` → also try `PSC6` / `PSC 6` (the no-space series form).
            variants.add(f"{series}{num}")
            variants.add(f"{series} {num}")
        else:
            sm = _SERIES_NUM_RE.match(phase.strip())
            if sm and sm.group(1).upper() == series.upper():
                num = sm.group(2)
                # `PSC5` → also try `Phase 5` / `Phase5` (the generic form).
                variants.add(f"Phase {num}")
                variants.add(f"Phase{num}")
    return sorted(re.escape(v) for v in variants)


def _series_variants(series: str) -> list[str]:
    """Regex-escaped plan-id spellings for the TRAILER rung (docs/289).

    A trailer names the plan as registered, and the two spellings in the wild
    differ: the QUERY usually carries the full plan id
    (``docs/286_shipping-the-go-binary-through-pypi-per-platform-wheels``) while
    the TRAILER carries its short series head (``docs/286``). The bridge is the
    underscore convention of plan-doc basenames — ``<head>_<slug>`` with
    ``<head>`` ending in a digit run — so this returns the full id plus
    ``<head>`` when the query has that shape, and just the full id otherwise
    (a hyphenated slug like ``RS4-port`` or a multi-word series gains NO extra
    spelling; the sub-phase-parent fallback is a different, separately-gated
    feature). Pure; the sibling of `_phase_variants`.

    "docs/286_shipping-…-wheels" -> ["docs/286", "docs/286_shipping-…-wheels"]
    "82_liveness-oracle-plan"    -> ["82", "82_liveness-oracle-plan"]
    "docs/286"                   -> ["docs/286"]          (already the head)
    "RS4-port"                   -> ["RS4-port"]          (no underscore head)
    "my_plan"                    -> ["my_plan"]           (head has no digit)
    """
    variants = {series}
    m = re.match(r"^([^_\s]*\d[a-z0-9]*)_", series, re.IGNORECASE)
    if m:
        variants.add(m.group(1))
    return sorted(re.escape(v) for v in variants)


def _build_log_cache() -> tuple[list[str], list[str]]:
    """Pre-fetch git log once for batch mode. Returns (oneline_lines, body_lines).

    Oneline window is wide enough (-1500 ≈ 1-2 months at current cadence)
    to catch the CR2-class case where a phase shipped 330+ commits ago and
    a tighter window dropped it. Body window stays narrow because release-
    summary mentions are caught by the cheaper oneline pass.
    """
    try:
        oneline = _git_log(["--oneline", f"-{_ONELINE_WINDOW}"])
    except RuntimeError:
        oneline = []
    try:
        body = _git_log(["-50", "--format=%h%n%B%n--END--"])
    except RuntimeError:
        body = []
    return oneline, body


def _is_progress_only(line: str, match_end: int, matchers: "_Matchers | None" = None) -> bool:
    """True if what follows the matched phase id reads as a progress marker.

    Called after `direct_pat` matches `^<sha> docs/<SERIES>: <PHASE>` at the
    given char offset. If the next char is whitespace and the next token is
    a known progress marker word (`week-1`, `audit`, `baseline`, …), the
    commit is incremental progress on a multi-step phase, not a ship of it.

    The marker vocabulary is per-workspace DATA (`StampConvention.progress_markers`,
    read through `matchers`): the reference app declares its soak vocabulary, a
    generic repo declares none so a real foreign-repo ship (`cache: Phase 0 audit
    of …`) is NEVER demoted. Falls back to the active convention when a caller
    didn't thread `matchers` in.

    Separators (`:`, `—`, `-`, em-dashes) and end-of-line continue to read
    as ship attributions — only the bare `<PHASE> <progress-word>` shape
    triggers the demotion. Keeps the false-positive surface narrow.
    """
    tail = line[match_end:]
    if not tail or not tail[0].isspace():
        return False  # `:`, `—`, `-`, EOL — ship
    markers = (
        matchers.convention.progress_marker_set() if matchers is not None
        else _subject_matchers().convention.progress_marker_set()
    )
    next_token = tail.lstrip().split(None, 1)[0] if tail.strip() else ""
    return next_token.lower() in markers


def _check_phase_with_cache(
    series: str,
    phase: str,
    oneline_lines: list[str],
    body_lines: list[str],
    matchers: "_Matchers | None" = None,
) -> dict:
    """Check one phase against pre-fetched log caches (zero subprocess cost).

    `matchers` carries the active workspace's ship-stamp grammar (the SCV seam).
    Defaults to resolving the active convention, so a direct call (tests, the
    single-pick CLI) still works; the batch/check-packet/library entrypoints
    resolve it ONCE and thread it in, so every pick in a packet reads the same
    convention (design-law 2).
    """
    if matchers is None:
        matchers = _subject_matchers()
    series_re = re.escape(series)
    # Direct-ship variant set: includes the `Phase N` ↔ `<SERIES>N` synonyms.
    # The direct-ship prefix (`docs/<SERIES>:`) IS the series qualifier, so
    # the bare `Phase N` literal is safe here — `docs/PSC: Phase 6 TOMB` is
    # unambiguously a PSC phase.
    phase_alt = "|".join(_phase_variants(phase, series))
    direct_pat = re.compile(
        rf"^([a-f0-9]+)\s+{matchers.direct_ship_core(series_re, phase_alt)}{_BOUNDARY_NEG}",
        re.IGNORECASE,
    )
    # OS-FQ136 — release/body series-qualification.
    # The release-prefix and body scans have no `docs/<SERIES>:` prefix to
    # anchor them: `.*?` jumps across the whole summary, so a bare `Phase 6`
    # would match `v0.62.0: docs/09 Phase 6 UI` even when the query is a PSC
    # phase (finding #136, 5th recurrence of the cross-series literal-collision
    # class). For a GENERIC `Phase N` token we therefore drop the bare literal
    # from the release/body alternation and accept only series-qualified forms:
    #   - the no-space `<SERIES>N` / `<SERIES> N` synonym (self-qualifying), or
    #   - `<SERIES>` immediately preceding the `Phase N` literal (`PSC Phase 8`).
    # Series-prefixed query tokens (`PSC6`, `RS4`) are already self-qualifying,
    # so their release/body alternation is unchanged.
    if _is_generic_phase_token(phase):
        gm = _GENERIC_PHASE_RE.match(phase.strip())
        num = gm.group(1)
        num_re = re.escape(num)
        # Self-qualifying synonym forms: `PSC6`, `PSC 6`.
        synonym_alt = rf"{series_re}\s*{num_re}"
        # Series-prefixed-adjacent literal: `PSC Phase 8` / `PSC: Phase 8`.
        adjacent_alt = rf"{series_re}\s*:?\s+Phase\s*{num_re}"
        qualified_alt = rf"(?:{synonym_alt}|{adjacent_alt})"
        # OS-FQ63 — the release-prefix scan also fires on the allowlisted
        # standalone-summary subjects (`docs/_plans:`, `docs/HYG:`, …), not
        # only `vX.Y.Z:` releases. The alternation is unchanged, so the
        # generic-token series-qualification guard is preserved.
        release_pat = re.compile(
            rf"^([a-f0-9]+)\s+{matchers.summary_subject}.*?{_BOUNDARY_PRE_NEG}{qualified_alt}{_BOUNDARY_NEG}",
            re.IGNORECASE,
        )
    else:
        # Release-prefix oneline pattern: catches phases bundled into the
        # `vX.Y.Z: ...` summary line of a release commit (e.g.
        # `cae674f v0.268.0: EC17.2 escalation + RS4 archetype HTML + ...`).
        # Body-scan still runs below for phases that appear only in an
        # extended commit body, not in the summary itself.
        #
        # OS-FQ226 — the alternation must be series-qualified even when the
        # query is series-prefixed. `_phase_variants("EV1","EV")` expands to
        # include the bare generic synonym `Phase 1`; left bare in this
        # unanchored `.*?` scan it matches `v0.37.0: ... Login Subsystem
        # Phase 1` (finding #226). `_release_body_alternation` keeps the
        # self-qualifying `EV1`/`EV 1` forms bare and constrains the generic
        # synonym to a `<SERIES>`-adjacent shape.
        release_alt = _release_body_alternation(series, phase)
        # OS-FQ63 — same subject generalisation as the generic-token branch:
        # the scan fires on `docs/_plans:` / `docs/HYG:` summary bundles too.
        # `_release_body_alternation` already series-qualifies the alternation
        # (OS-FQ226), so no false-positive cross-series leak is introduced.
        release_pat = re.compile(
            rf"^([a-f0-9]+)\s+{matchers.summary_subject}.*?{_BOUNDARY_PRE_NEG}(?:{release_alt}){_BOUNDARY_NEG}",
            re.IGNORECASE,
        )
    # Pass 1a: direct-ship lines win (`docs/<SERIES>: <PHASE> ...`). They are
    # the canonical attribution, so even if a release-prefix mention sits
    # higher in the log we want the direct ship's SHA.
    #
    # Demotion: if the matched suffix is a progress marker (`<PHASE> week-1`,
    # `<PHASE> audit`, …) this commit is incremental work on a multi-week
    # soak / observation phase, not a ship. Skip it and keep scanning — a
    # real ship may sit further back in the log.
    for line in oneline_lines:
        m = direct_pat.match(line)
        # FQ-77 — never count a bookkeeping subject as a ship, even if it
        # carries the `docs/<SERIES>:` shape. (A `docs/_plans:`/`docs/dispatch:`
        # subject cannot match `direct_pat`'s `(docs|go)/<SERIES>:` anchor for a
        # real series, but a series literally named `_plans`/`dispatch` would;
        # the guard makes the exclusion total rather than incidental.)
        if m and matchers.is_bookkeeping_subject(_oneline_subject(line)):
            continue
        if m and not _is_progress_only(line, m.end(), matchers):
            return {"shipped": True, "sha": m.group(1), "summary": line, "via": "direct"}
    # Pass 1a′ (docs/289): trailer-form stamp — a `(<PLAN> <PHASE>)` group at
    # the END of the subject, the Conventional-Commits shape (`feat(pypi): …
    # (docs/286 Phase 3)`), per-convention OPT-IN (`trailer_stamp`). Runs after
    # the direct pass (a start-anchored ship anywhere in the window stays the
    # canonical attribution) and before the release-prefix pass (the trailer is
    # the commit's OWN claim about itself; a release bundle is a weaker,
    # footprint-guarded mention). The core carries its own end anchor, so the
    # tightness the start anchor provides elsewhere comes from the parens +
    # `$` here (mid-subject mentions, prose ids, `Phase 30` vs `Phase 3`, and
    # progress-marked trailers all fail the shape — see `trailer_ship_core`).
    # Two guards mirror the sibling passes: a bookkeeping subject never ships
    # (FQ-77), and a summary/release subject is SKIPPED — it falls through to
    # Pass 1b where the release-bump footprint guards apply, so a version cut
    # ending in a phrase-shaped paren can never be promoted to a direct ship.
    if getattr(matchers.convention, "trailer_stamp", False):
        series_alt = "|".join(_series_variants(series))
        trailer_core = matchers.convention.trailer_ship_core(series_alt, phase_alt)
        trailer_pat = re.compile(
            rf"^([a-f0-9]+)\s+.*{trailer_core}", re.IGNORECASE
        )
        summary_start = re.compile(rf"^{matchers.summary_subject}", re.IGNORECASE)
        for line in oneline_lines:
            m = trailer_pat.match(line)
            if not m:
                continue
            subject = _oneline_subject(line)
            if matchers.is_bookkeeping_subject(subject):
                continue
            if summary_start.match(subject):
                continue
            return {"shipped": True, "sha": m.group(1), "summary": line, "via": "trailer"}
    # Pass 1b: release-prefix bundled mentions, only if no direct ship was
    # found above. Newest release commit wins (oneline is newest-first).
    # Same progress-marker demotion as Pass 1a — a release commit that
    # bundles `<PHASE> audit` or `<PHASE> dual-key attribution` (substrate
    # work on a multi-step phase) is not the close-out ship.
    for line in oneline_lines:
        m = release_pat.match(line)
        # FQ-77 — bookkeeping subjects are excluded from `_SUMMARY_BUNDLE_PREFIXES`,
        # so `release_pat` no longer matches a `docs/_plans:`/`docs/fanout:`/
        # `docs/dispatch:` subject. The guard is belt-and-suspenders: a future
        # widening of the summary anchor must still not let a quoted phase id in
        # a soft-claim / archive-rollup subject read as a ship.
        if m and matchers.is_bookkeeping_subject(_oneline_subject(line)):
            continue
        if m and not _is_progress_only(line, m.end(), matchers):
            return {
                "shipped": True,
                "sha": m.group(1),
                "summary": f"{line} (release-prefix mention)",
                "via": "release-prefix",
            }

    # Pass 1c: bundle-slug free-form subject fallback. A standalone-summary
    # series like job's HYG has snake-case phase IDs (`dropbox_zero_apply`) but
    # commit subjects in prose form (`docs/HYG: Dropbox zero-apply picker audit
    # (queue #20)`). Normalize both sides — lowercase + collapse [\s\-_]+ → single
    # underscore — and substring-match the slug. Runs ONLY for a series the active
    # convention declared as a summary-bundle prefix (`bundle_slugs()`, derived
    # from `summary_bundle_prefixes` — the L4 fix: no hardcoded `"HYG"` literal),
    # to bound the false-positive surface; a generic repo declares none so this
    # never runs, and non-bundle slugs would conflict with how phase ids are
    # formed in ordinary series (`SF1.2`, `RS4`, etc.).
    if series.upper() in matchers.convention.bundle_slugs():
        series_re_local = re.escape(series)
        slug = re.sub(r"[\s\-_]+", "_", phase.lower())
        hyg_subject_pat = re.compile(
            rf"^([a-f0-9]+)\s+{matchers.direct_prefix}{series_re_local}:?\s+(.+)$",
            re.IGNORECASE,
        )
        for line in oneline_lines:
            m = hyg_subject_pat.match(line)
            if not m:
                continue
            normalized = re.sub(r"[\s\-_]+", "_", m.group(2).lower())
            if slug in normalized:
                return {
                    "shipped": True,
                    "sha": m.group(1),
                    "summary": f"{line} (HYG slug fallback)",
                    "via": "hyg-slug",
                }

    # Pass 1d (OS12, 2026-05-16, finding #167): sub-phase parent fallback.
    # When the query is a sub-phase tag (`<phase>-<suffix>`) and none of the
    # direct passes hit, try the bare-parent form (`<phase>` without the
    # `-<suffix>`) and accept the match only if the matched commit's subject
    # contains a normalized substring match of `<suffix>` (case-insensitive,
    # `[\s\-_]+` collapsed to `_`). This catches the queue #167 class:
    # UP6-diagnostics was queried against commit `647c6eaf docs/UP: UP6 —
    # /ui/system/diagnostics tile fan-out`; the parent UP6 matches, and
    # "diagnostics" appears in the subject — so the ship is real.
    #
    # Guard against false positives: a `docs/AAR: AAR15 — apply lessons`
    # commit must NOT match a `AAR15-foo` query unless `foo` appears in the
    # subject. The suffix slug is the gate, not the parent match alone.
    #
    # L4-fix: this fallback is now a per-convention FEATURE FLAG
    # (`sub_phase_parent_fallback`), not an unconditional `"-" in phase` query
    # test. The reference app declares it on (it uses hyphen-suffixed sub-phase
    # ids); a generic repo leaves it off, so a fabricated `P2-CLI` no longer
    # false-resolves against a real `P2` whose subject merely contains `CLI`.
    if matchers.convention.sub_phase_parent_fallback and "-" in phase:
        parent, _, suffix = phase.partition("-")
        suffix_slug = re.sub(r"[\s\-_]+", "_", suffix.lower())
        parent_alt = "|".join(_phase_variants(parent))
        parent_direct_pat = re.compile(
            rf"^([a-f0-9]+)\s+{matchers.direct_prefix}{series_re}:?\s+(?:{parent_alt}){_BOUNDARY_NEG}",
            re.IGNORECASE,
        )
        for line in oneline_lines:
            m = parent_direct_pat.match(line)
            if not m:
                continue
            # FQ-77 — same bookkeeping exclusion as the direct pass.
            if matchers.is_bookkeeping_subject(_oneline_subject(line)):
                continue
            if _is_progress_only(line, m.end(), matchers):
                continue
            # Confirm the suffix slug appears in the subject (substring match
            # after normalization). Without this gate the bare parent match
            # would over-claim — every sub-phase id would resolve to the
            # parent's most recent ship regardless of topic.
            normalized_subject = re.sub(r"[\s\-_]+", "_", line.lower())
            if suffix_slug and suffix_slug in normalized_subject:
                return {
                    "shipped": True,
                    "sha": m.group(1),
                    "summary": f"{line} (sub-phase parent fallback: '{suffix}' in subject)",
                    "via": "sub-phase-parent",
                }

    # OS-FQ136 — the body-scan only fires inside summary-bundle commit
    # bodies (see `in_summary` below), so it shares the release-prefix scan's
    # series-blindness. Reuse the same series-qualified alternation for a
    # generic `Phase N` token; series-prefixed tokens keep the full variant set.
    if _is_generic_phase_token(phase):
        body_pat = re.compile(rf"{_BOUNDARY_PRE_NEG}{qualified_alt}{_BOUNDARY_NEG}", re.IGNORECASE)
    else:
        # OS-FQ226 — same series-qualification as the release-prefix scan: the
        # body-scan only fires inside `vX.Y.Z:` bodies and shares the series-
        # blindness, so a series-prefixed query must not leak its generic
        # `Phase N` synonym bare into the alternation.
        body_pat = re.compile(
            rf"{_BOUNDARY_PRE_NEG}(?:{_release_body_alternation(series, phase)}){_BOUNDARY_NEG}",
            re.IGNORECASE,
        )
    sha = ""
    summary = ""
    in_summary = False
    for line in body_lines:
        if line == "--END--":
            sha = ""
            summary = ""
            in_summary = False
            continue
        if not sha:
            sha = line.strip()
            continue
        if not summary:
            summary = line.strip()
            # Gate the body-scan on a summary-bundle subject (`vX.Y.Z:` release
            # OR the `docs/HYG:` standalone-summary prefix). FQ-77 — a
            # bookkeeping subject (soft-claim / archive rollup / snapshot) is
            # never a summary bundle, so its body's quoted phase ids must not
            # resolve a ship. The `_SUMMARY_SUBJECT_RE` no longer lists the
            # bookkeeping prefixes; the explicit guard makes that total.
            in_summary = bool(re.match(rf"^{matchers.summary_subject}", summary)) and not matchers.is_bookkeeping_subject(summary)
            continue
        if in_summary and body_pat.search(line):
            return {
                "shipped": True,
                "sha": sha,
                "summary": f"{summary} (body mention)",
                "via": "body-mention",
            }
    return {"shipped": False, "sha": "", "summary": "", "via": ""}


def _plan_body_says_shipped(plan_doc: str, phase: str, series: str = "") -> bool | None:
    """Consult the plan body for an authoritative SHIPPED stamp on `phase`.

    Returns:
      True   — phase has a section AND the section contains `SHIPPED`
      False  — phase has a section AND the section does NOT contain `SHIPPED`
      None   — plan body doesn't mention this phase as a section header, OR
               the doc is unreadable. Caller should fall back to git log.

    "Section" means either the top-level `### <PHASE>` heading (bounded by the
    next `##`/`###` heading or EOF) OR a bullet sub-phase `- **<PHASE> — ...`
    (bounded by the next sibling bullet or `##` heading). Both shapes are
    common — AAR13.0-AAR13.4 use the bullet shape under `### AAR13`, while
    AAR10 uses the `### AAR10` shape. The phase id boundary disallows
    `[A-Za-z0-9.\\-]` on either side to avoid `AAR1` matching `AAR10`.

    Used as a tiebreaker for WEAK git verdicts (release-prefix or body-mention)
    — a release commit that bundles `<PHASE> dual-key attribution` is reading
    substrate work as a ship; the plan body's stamp (or lack thereof) is the
    operator's intent. Direct-ship verdicts are NOT cross-checked — stamp drift
    is a known operator habit (`AAR6` shipped 2026-05-04 with no stamp; `AFR2`
    shipped without one). Trusting direct-ship despite stamp drift was a
    deliberate call.
    """
    try:
        with open(plan_doc, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    # OS-FQ136 — `series` (when supplied) threads the `Phase N` ↔ `<SERIES>N`
    # synonym in, so a `PSC6` query resolves against a `### Phase 6` heading
    # and vice versa. Optional: pre-OS-FQ136 callers omit it harmlessly.
    phase_re = "|".join(_phase_variants(phase, series))
    # Try ### header section first.
    h3_pat = re.compile(
        rf"(?m)^###\s+(?:Phase\s+)?(?:{phase_re}){_BOUNDARY_NEG}",
    )
    m = h3_pat.search(text)
    if m:
        rest = text[m.end():]
        nm = re.search(r"(?m)^##+\s+", rest)
        section = text[m.start() : m.end() + (nm.start() if nm else len(rest))]
        return _section_says_shipped(section)
    # Try bullet sub-phase: `- **<PHASE> — ...` (optionally `Phase <PHASE>`).
    bullet_pat = re.compile(
        rf"(?m)^[\s]*-\s+\*\*(?:Phase\s+)?(?:{phase_re}){_BOUNDARY_NEG}[^\n]*",
    )
    m = bullet_pat.search(text)
    if m:
        rest = text[m.end():]
        nm = re.search(r"(?m)^[\s]*-\s+\*\*|^##+\s+", rest)
        section = text[m.start() : m.end() + (nm.start() if nm else len(rest))]
        return _section_says_shipped(section)
    return None


def _section_says_shipped(section: str) -> bool | None:
    """The SHIPPED verdict for ONE bounded plan-doc phase section.

    Returns True iff the section carries a `SHIPPED` token, False iff it has the
    section but no stamp — EXCEPT the FQ-326 soak guard: a section that contains
    `SHIPPED` but ALSO an OPEN-soak marker (the phase's own close gates on an
    unclosed soak — `_OPEN_SOAK_MARKER_RE`) returns **False**, not True. The
    `SHIPPED` there is a pre-soak substrate stamp (or a drifted heading stamp)
    for work that landed ahead of the soak the phase actually gates on; trusting
    it false-positives the soak-gated phase as done and masks the live close-out
    pick (#326).

    Why False (the demote) and not None (defer): `_consult_plan_body` only
    challenges WEAK verdicts (`via != "direct"` early-returns before this is even
    reached) and only DEMOTES on `body_verdict is False`. Returning None would
    leave the already-`shipped=True` weak verdict un-demoted — i.e. the guard
    would *stop confirming* the false ship but not actually *kill* it, defeating
    the #326 fix (a release-prefix / body-mention match would ride the pre-soak
    stamp straight to SHIPPED). Returning False makes `_consult_plan_body` demote
    it, which is the whole point. Direct-ship is untouched (it never reaches
    here), so a genuinely direct-shipped soak phase is never false-demoted.

    Date-blind: the marker is structural prose, never a clock read — the soak
    registry / picker owns the open-vs-closed date math.
    """
    if "SHIPPED" not in section:
        return False
    if _OPEN_SOAK_MARKER_RE.search(section):
        return False
    return True


def _consult_plan_body(result: dict, plan_doc: str | None, phase: str, series: str = "") -> dict:
    """Apply the plan-body cross-check to a `_check_phase_with_cache` result.

    Only WEAK ship verdicts (`via != "direct"`) are challenged. Direct-ship
    verdicts win unconditionally because their attribution is unambiguous
    (the commit author named `<PHASE>` as the subject). Release-prefix and
    body-mention verdicts are reading bundled mentions of related work —
    when the plan body has the phase's section AND the section carries no
    SHIPPED stamp, treat git's verdict as a false positive and demote.

    Returns the same shape dict; if demoted, `shipped=False`, `via=demoted`,
    and `summary` carries a one-line note for Course-corrections rendering.
    """
    if not plan_doc or not result.get("shipped"):
        return result
    if result.get("via") == "direct":
        return result
    body_verdict = _plan_body_says_shipped(plan_doc, phase, series)
    if body_verdict is False:
        return {
            "shipped": False,
            "sha": "",
            "summary": (
                f"git matched {result.get('sha','')} via {result.get('via','?')} but "
                f"plan body has no SHIPPED stamp on {phase} — demoted"
            ),
            "via": "demoted-by-plan-body",
            "git_match_sha": result.get("sha", ""),
            "git_match_summary": result.get("summary", ""),
        }
    return result


# ---------------------------------------------------------------------------
# The file-path backstop's grammar is now per-workspace DATA (the file-path-rung
# genericization, sibling to the SCV subject-grammar lift). What a plan-doc token
# must look like to count as a load-bearing FILE PATH — the top-level dir
# allowlist — and which basenames count as shared-infra hubs moved out of these
# module constants into `dos.stamp.StampConvention` (the `code_dirs` /
# `infra_basenames` / `infra_doc_basenames` fields + the `repo_path_re()` /
# `is_shared_infra()` accessors). The active workspace's convention supplies them
# through `_Matchers`, exactly as it supplies the subject-grammar fragments.
#
# Why this was a leak: the reference app hardcoded its OWN top-level dirs
# (`agents|job_search|go|…`) in `_REPO_PATH_RE`, so on a foreign repo whose
# deliverables live under `engine/`/`models/`/`commands/` the rung harvested
# NOTHING and the artefact backstop was dead — every subject-drifted ship
# resolved `via none`. The generic convention (`code_dirs=()`) harvests a path
# rooted at ANY top-level dir, which is sound because the dir allowlist was only
# ever a recognition narrowing; the false-positive gates (2-file overlap,
# distinctive-file, bookkeeping, cross-series) all live downstream and are
# unchanged. A host that wants the tight allowlist declares `code_dirs` (the
# reference app does, in `JOB_STAMP_CONVENTION`).
#
# The names below are kept as BACK-COMPAT aliases derived from
# `JOB_STAMP_CONVENTION` so a `from dos.phase_shipped import *` consumer (the
# reference app's thin shim) still sees byte-identical values; live code reads the
# active convention through `matchers`, never these constants.
# ---------------------------------------------------------------------------
# (`_JOB_STAMP_CONVENTION` / `_DIAGRAM_SUFFIXES` are imported at the top of the
# module.) These are the back-compat aliases for the file-path-rung constants.
_REPO_PATH_RE = _JOB_STAMP_CONVENTION.repo_path_re()
_SHARED_INFRA_BASENAMES = _JOB_STAMP_CONVENTION.infra_basename_set()
_SHARED_INFRA_DOC_BASENAMES = _JOB_STAMP_CONVENTION.infra_doc_basename_set()

# Non-source artifact suffixes — a harvested token ending in one of these is a
# release tarball / archive / binary / vendored-repo URL, NEVER a load-bearing
# SOURCE deliverable a phase ships. The generic harvester is deliberately loose
# (match-any top-level dir), so without this a plan row that mentions a release
# tarball (`v1.2.3/release.tar.gz`) or a git remote (`github.com/a/b.git`) lets a
# series-attributed commit touching that committed artifact false-ship an
# unshipped phase via the single-file gate (adversarial-review finding). Modeled
# on `stamp._UNIVERSAL_DIAGRAM_SUFFIXES` (which excludes regenerated diagrams).
# This is the artefact analogue: a phase's *distinctive* deliverable is source, so
# an archive/binary/URL is dropped at harvest time on every scan path.
_NON_SOURCE_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".zip", ".gz", ".bz2", ".xz", ".whl", ".egg",
    ".git", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".bin", ".so", ".dll",
    ".dylib", ".exe", ".o", ".a", ".jar", ".class",
)


def _is_shared_infra(path: str, matchers: "_Matchers | None" = None) -> bool:
    """True if `path` is a hub file excluded from the overlap count.

    Reads the active workspace's shared-infra set through `matchers` (the SCV
    seam); a caller that didn't resolve one falls back to the active convention.
    Three classes are excluded — all too widely-touched for a coincidental edit
    to be ship evidence:
      - hub *code* files (universal ∪ the convention's `infra_basenames`); and
      - the convention's named *documentation* hubs (`infra_doc_basenames`); and
      - any `docs/…*.mmd`/`*.png` diagram (the universal regenerated-hub rule).
    """
    m = matchers if matchers is not None else _subject_matchers()
    return m.is_shared_infra(path)


def _extract_phase_files(
    plan_doc: str, phase: str, series: str = "", matchers: "_Matchers | None" = None
) -> list[str]:
    """Return the repo-relative file paths named in `phase`'s plan-doc section.

    Reuses the section-bounding logic of `_plan_body_says_shipped` — the phase
    is located as either a `### <PHASE>` heading section or a `- **<PHASE> — ...`
    bullet sub-phase, bounded by the next sibling heading/bullet — then every
    repo-path-shaped token inside that section is harvested and de-duplicated.

    These are the phase's *load-bearing* files: the files its plan row names as
    the ones the phase adds or edits. They are the artefact the file-path
    verdict path matches ship commits against (AAR-FQ230, finding #230).

    Returns `[]` when the doc is unreadable, the phase has no section, or the
    section names no repo-path-shaped tokens — in every such case the caller's
    file-path fallback yields no verdict and the phase stays NOT_SHIPPED.
    """
    try:
        with open(plan_doc, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    phase_re = "|".join(_phase_variants(phase, series))
    section = ""
    h3_pat = re.compile(rf"(?m)^###\s+(?:Phase\s+)?(?:{phase_re}){_BOUNDARY_NEG}")
    m = h3_pat.search(text)
    if m:
        rest = text[m.end():]
        nm = re.search(r"(?m)^##+\s+", rest)
        section = text[m.start() : m.end() + (nm.start() if nm else len(rest))]
    else:
        bullet_pat = re.compile(
            rf"(?m)^[\s]*-\s+\*\*(?:Phase\s+)?(?:{phase_re}){_BOUNDARY_NEG}[^\n]*",
        )
        m = bullet_pat.search(text)
        if m:
            rest = text[m.end():]
            nm = re.search(r"(?m)^[\s]*-\s+\*\*|^##+\s+", rest)
            section = text[m.start() : m.end() + (nm.start() if nm else len(rest))]
    if not section:
        return []
    # Harvest file-path tokens with the ACTIVE workspace's repo-path matcher (the
    # `code_dirs` seam): the reference app's tight dir allowlist when declared, a
    # match-any-top-level-dir matcher generically. Falls back to the active
    # convention when a caller didn't thread `matchers` in.
    repo_path = matchers.repo_path if matchers is not None else _subject_matchers().repo_path
    seen: list[str] = []
    for pm in repo_path.finditer(section):
        path = pm.group(1)
        # Drop non-source artifacts (tarballs, archives, binaries, vendored-repo
        # URLs): they are never a phase's distinctive SOURCE deliverable, and the
        # loose generic harvester would otherwise lift one out of plan prose and
        # let it carry a false ship (adversarial-review finding).
        if path.lower().endswith(_NON_SOURCE_SUFFIXES):
            continue
        if path not in seen:
            seen.append(path)
    return seen


def phase_deliverable_touched(
    plan: str,
    phase: str,
    plan_doc: str | None,
    touched_files: "set[str] | list[str] | None",
    *,
    series: str = "",
    drop_shared_infra: bool = True,
    matchers: "_Matchers | None" = None,
) -> bool | None:
    """The ONE shared deliverable-overlap ground-truth predicate.

    Answers: "did `touched_files` touch any of `phase`'s declared *distinctive*
    deliverable files?" — the SAME question the read-side file-path verdict
    (`_check_phase_by_filepath`) and the write-side stamp guards (job's
    `_gh4_plandoc_only_lacks_deliverable` / `_gh4_subject_is_prelaunch_staging_only`)
    each re-implemented over their own footprint source. Both sides now feed this
    one predicate via a trivial footprint adapter — ending the recurring
    "build the deliverable check (it already exists, just duplicated)" loop that
    let the CRS3/#387/#365 false-stamps and the 84% zero-ship false-drains
    recur. It is footprint-source-AGNOSTIC (takes a touched-file SET, not a sha
    and not a committed-list) and PURE (no git, no clock) — so the two sources
    (a git-show-on-sha set on the read side, the committed pathspec on the write
    side) are thin adapters and this core is unit-testable without git.

    Returns:
      * True  — the phase declares >= 1 distinctive deliverable file AND the
                touched set hit at least one of them (a real deliverable shipped).
      * False — the phase declares >= 1 distinctive deliverable file AND the
                touched set hit NONE of them (coverage with zero deliverable —
                the CRS3 / plan-doc-only / prelaunch-staging shape: demote/refuse).
      * None  — PERMISSIVE: the phase declares no distinctive deliverable file
                (a genuinely doc-only phase, or every declared file is a
                shared-infra hub), OR the inputs are unresolvable (no plan_doc,
                empty/None touched set). The caller must treat None as "no
                evidence to demote/refuse" — i.e. trust the prior verdict —
                so this can only ever ADD a refusal where there is zero
                distinctive evidence, never manufacture a false-negative on a
                real ship. This None=permissive posture is the load-bearing
                contract both call sites already honor; preserve it exactly.

    `drop_shared_infra=True` (default) excludes hub files (`config.py`,
    `fanout_state.py`, doc hubs, regenerated diagrams — see `_is_shared_infra`)
    from the "distinctive" set, matching the read side. The write side historically
    counted hubs; converging on drop_shared_infra=True only ever makes the write
    guard refuse a stamp that had *no distinctive* evidence (a hub-only edit),
    never a real ship — but a caller that must preserve the legacy hub-counting
    behavior can pass drop_shared_infra=False.
    """
    if not plan_doc or not touched_files:
        return None
    declared = _extract_phase_files(plan_doc, phase, series, matchers)
    # Drop the plan doc itself — editing the plan doc is the 3b coverage under
    # scrutiny, never a deliverable for this check (mirrors the write side's
    # plan-doc-self drop).
    pdp = plan_doc.replace("\\", "/")
    distinctive = [
        f for f in declared
        if f.replace("\\", "/") != pdp
        and not (drop_shared_infra and _is_shared_infra(f, matchers))
    ]
    if not distinctive:
        return None  # doc-only / hub-only phase → no distinctive evidence → permissive
    norm = {str(p).replace("\\", "/") for p in touched_files if p}
    for f in distinctive:
        ff = f.replace("\\", "/")
        # exact path, dir-prefix (declared "dir/"), or basename match — the union
        # of the read side's basename match and the write side's path/dir-prefix
        # match, so the merged predicate is at least as accepting as either source.
        if ff in norm:
            return True
        if ff.endswith("/") and any(p.startswith(ff) for p in norm):
            return True
        base = ff.rsplit("/", 1)[-1]
        if base and any(p == ff or p.rsplit("/", 1)[-1] == base for p in norm):
            return True
    return False  # declares distinctive files, touched none → no deliverable shipped


# #394 — cross-series guard for the file-path backstop.
#
# A `vX.Y.Z:` release commit (or a `docs/<OTHER>:` ship) bundles one plan
# series' work, but its diff can incidentally touch >= 2 of a DIFFERENT,
# genuinely-unshipped plan's load-bearing files when the two series share an
# infra-adjacent file. Live recurrence (finding #394): RTN0 (drafted, zero
# commits ever) false-flagged SHIPPED by `c7c87566`
# ("v0.365.0: lane gardener — LG0 baseline + LG1 /lane-audit skill"), which
# touches scripts/lane_gardener_audit.py + tests/test_lane_gardener.py — 2/5
# of RTN0's named files — because the LG-series release shares those files.
# A shared load-bearing file is a SIBLING-SERIES ship, not a ship of `series`.
#
# The discriminator: a commit subject that names a CONCRETE other plan-series
# (a `docs/<SERIES>:` / `go/<SERIES>:` prefix whose series token != the queried
# series, or a `vX.Y.Z: <other-series> …` release whose summary leads with a
# different series id) must carry an explicit phase-id / series token for the
# QUERIED series before the file-path overlap counts as a ship. The conservative
# direction: when the subject does NOT clearly name another series (a generic
# release summary, an unparseable subject), the guard does NOT fire — it only
# refuses when it can prove the commit belongs to a different series.
_OTHER_SERIES_PREFIX_RE = re.compile(
    r"^(?:docs|go)/([A-Za-z][A-Za-z0-9]*)\s*:", re.IGNORECASE
)
# A `vX.Y.Z:` release whose summary names a series-prefixed phase id
# (`v0.365.0: ... LG0 ...` / `v0.378.0: ... GBA6 ...`). Captures the first
# such series token after the version tag.
_RELEASE_SERIES_TOKEN_RE = re.compile(
    r"^v\d+\.\d+\.\d+:\s.*?\b([A-Za-z]{2,})\d", re.IGNORECASE
)


def _subject_names_other_series(subject: str, series: str) -> bool:
    """True iff `subject` clearly attributes to a plan-series OTHER than `series`.

    Used by the file-path backstop's cross-series guard (#394): a file-path
    overlap whose commit subject names a *different* series is a sibling-series
    ship, not a ship of `series`, unless the subject also carries `series`'s own
    token (checked separately by the caller).

    Conservative: returns False (guard does not fire) when the subject does not
    parse to a concrete series — so an ambiguous/generic subject is treated as
    "could be this series", preserving the pre-#394 behaviour for everything
    except a provable cross-series collision.
    """
    s = (subject or "").strip()
    want = (series or "").strip().lower()
    if not s or not want:
        return False
    # `docs/<OTHER>:` / `go/<OTHER>:` direct prefix naming a different series.
    m = _OTHER_SERIES_PREFIX_RE.match(s)
    if m and m.group(1).lower() != want:
        return True
    # `vX.Y.Z: ... <OTHER><digit> ...` release summary leading with a different
    # series-prefixed phase id (and the queried series is NOT mentioned at all).
    m = _RELEASE_SERIES_TOKEN_RE.match(s)
    if m and m.group(1).lower() != want:
        # Only treat as cross-series if the queried series token is absent from
        # the whole subject — a release that bundles BOTH series should not be
        # refused (the caller's same-series token check handles that case).
        token_re = re.compile(
            rf"{_BOUNDARY_PRE_NEG}{re.escape(series)}(?![A-Za-z])", re.IGNORECASE
        )
        if not token_re.search(s):
            return True
    return False


# A batched file-path log cache: `{repo-relative-path: [(sha, subject), …]}`,
# newest-first, capped per file at `_FILEPATH_WINDOW`. Built ONCE by
# `_build_filepath_log_cache` from a single `git log --name-only` scan over the
# UNION of every pair's named files, then handed to `_check_phase_by_filepath`
# so the per-pair file-path backstop does ZERO subprocesses (docs/284). The
# per-file list is byte-identical to what `git log --oneline -<window> -- <path>`
# returns for that path, so an overlap test reading from the cache produces the
# SAME verdict as the per-file-subprocess path (the never-under-count pin).
_FilepathLogCache = "dict[str, list[tuple[str, str]]]"


def _build_filepath_log_cache(
    files: "set[str] | list[str]",
) -> "dict[str, list[tuple[str, str]]] | None":
    """Build the per-file commit-history cache in ONE `git log --name-only` scan.

    docs/284 — the file-path backstop's batch path. The per-pair path runs one
    ``git log --oneline -800 -- <file>`` subprocess PER named file (364 git
    subprocesses for a 262-pair job snapshot, ~19s). Every one asks the same git
    history "which commits touched which files"; only the per-pair overlap test
    differs. This builds `{path: [(sha, subject), …]}` for every named file from a
    SINGLE windowed scan over the union of those files as pathspecs, so the
    per-pair overlap becomes a pure in-memory lookup.

    Byte-identical-by-construction (the ⚓ never-under-count invariant, docs/284):
    a `git log --name-only -- f1 f2 … fk` over the union charges the SAME commit
    its `--name-only` block lists for whichever of f1..fk it touched, and we then
    truncate each path's list to its `_FILEPATH_WINDOW` most-recent touches —
    exactly what the per-file ``-{_FILEPATH_WINDOW} -- <path>`` subprocess returns.
    The scan is bounded by `_FILEPATH_WINDOW * _BATCH_SCAN_CAP_FACTOR` UNION
    commits; if that cap is HIT (a pathological deep history where a per-file
    800-window could reach a commit the union cap dropped) we return ``None`` so
    the caller degrades to the exact per-file path rather than risk an
    under-counted (false-NEGATIVE) verdict. Returns ``None`` on any git error for
    the same fail-to-the-safe-path reason.

    The returned subjects are the RAW commit subjects (no bookkeeping filtering) —
    the per-pair path filters `is_bookkeeping_subject` at overlap-build time, and
    the cache consumer does the same, so the filtering stays at exactly one place.
    """
    paths = sorted({f for f in files if f})
    if not paths:
        return {}
    cap = _FILEPATH_WINDOW * _BATCH_SCAN_CAP_FACTOR
    # `%x00`-delimited so a subject containing the format chars survives, and
    # `--name-only` lists each commit's touched paths on their own lines. `%h` is
    # the ABBREVIATED sha — byte-identical to what the per-file `git log --oneline`
    # path returns (the full `%H` would break the `sha`/`summary` parity pin). The
    # union pathspec restricts the scan to commits touching at least one named file
    # (cheap on a wide history). The `-cap` bounds the worst case.
    #
    # ⚓ `--no-merges` is LOAD-BEARING for byte-identity (docs/284): a union
    # `git log --name-only` over many pathspecs cannot reproduce git's per-PATH
    # history simplification through MERGE commits — default `--oneline -- <file>`
    # FOLLOWS a TREESAME parent and prunes a merge that introduced no new change to
    # <file>, but lists an "evil merge" that did; `--name-only` over the union has
    # no single parent to follow, so a flag like `--diff-merges=first-parent`
    # over-counts the pruned merges while the combined-diff default under-counts the
    # carried ones. `--no-merges` removes the whole ambiguity at the source: a
    # merge commit is NEVER a phase's ship of record (the ship is the underlying
    # feature commit, which both paths always retain), so dropping merges from the
    # overlap evidence is sound AND makes the union scan byte-identical to the
    # per-file `--oneline --no-merges` path (verified 129/129 src files on this
    # repo, 0 divergence). The per-file path adds `--no-merges` in lock-step.
    try:
        lines = _git_log(
            [
                "--name-only",
                "--no-merges",
                "--format=%x00%h%x00%s",
                f"-{cap}",
                "--",
                *paths,
            ]
        )
    except RuntimeError:
        return None
    wanted = set(paths)
    cache: dict[str, list[tuple[str, str]]] = {p: [] for p in paths}
    cur_sha = ""
    cur_subj = ""
    n_commits = 0
    for line in lines:
        if line.startswith("\x00"):
            # A new commit header: `\x00<sha>\x00<subject>`.
            _, _, rest = line.partition("\x00")
            sha, _, subj = rest.partition("\x00")
            cur_sha, cur_subj = sha, subj
            n_commits += 1
            continue
        path = line.strip()
        if path in wanted and cur_sha:
            # Newest-first order is preserved by append (git log is newest-first).
            cache[path].append((cur_sha, cur_subj))
    if n_commits >= cap:
        # The union window was saturated: a per-file 800-window could reach a
        # commit this scan dropped. Refuse to answer from a possibly-narrower
        # window — the caller re-runs the exact per-file path (never-under-count).
        return None
    # Truncate each path's list to its own `_FILEPATH_WINDOW` (the per-file cap
    # the subprocess path applied via `-{_FILEPATH_WINDOW}`).
    for p in cache:
        if len(cache[p]) > _FILEPATH_WINDOW:
            cache[p] = cache[p][:_FILEPATH_WINDOW]
    return cache


# Headroom multiplier for the batched union scan vs the per-file window. The
# per-pair path caps each file at `_FILEPATH_WINDOW` commits-touching-THAT-file;
# the batch scan caps the UNION at this multiple of that window. A file's touches
# are sparse in the union stream (most union commits touch OTHER files), so this
# headroom lets every file reach its own 800-window in the common case; when it
# is not enough the cache builder returns None and the caller falls back to the
# exact per-file path. Sized generously (the scan is ~50ms regardless of cap on a
# union-restricted history) — correctness never rides on it, only the fast path.
_BATCH_SCAN_CAP_FACTOR = 12


def _check_phase_by_filepath(
    series: str,
    phase: str,
    plan_doc: str,
    matchers: "_Matchers | None" = None,
    fp_cache: "dict[str, list[tuple[str, str]]] | None" = None,
) -> dict:
    """Re-derive a ship verdict from the file paths a phase's plan row names.

    ⚓ Data-driven decisions — evidence-over-narrative: subject-token matching
    keys on the *commit subject string*, which drifts (`8b0aec12 DT2: …` ships
    DT2 with a bare `DT2:` prefix, not the canonical `docs/DT: DT2 —`). Six
    subject-regex widenings have failed to close this false-NEGATIVE class
    (#62/#63/#77/#103/#136/#226). This path matches the *artefact* instead:
    when the commit subject and the file-path-touched set disagree, the
    file-path set wins.

    Overlap rule (the false-POSITIVE guard — see plan AAR-FQ230):
      - Harvest the phase's load-bearing files via `_extract_phase_files`.
      - For each file, `git log --oneline -400 -- <file>` (capped window,
        explicit pathspec).
      - SHIPPED requires a SINGLE commit touching >= 2 of those named files,
        AND at least one of the matched files must be *distinctive* (not a
        shared-infra hub file — see `_is_shared_infra`). A real phase ship
        touches the cluster of files its plan row names together; an
        incidental edit touches one. The 2-file-coincidence threshold guards
        against over-matching; the distinctive-file requirement guards the
        residual case where a section names two infra files an unrelated
        commit happened to touch together (the false STALE-STAMP class).
      - Degenerate case: a phase that names only ONE load-bearing file cannot
        meet the 2-file rule, so the single file is accepted only when the
        touching commit's subject still carries the `<SERIES>` token (a
        weaker, bounded gate — series attribution without the phase id). A
        sole shared-infra file is never enough — it yields no verdict.
      - FQ-77 bookkeeping exclusion: a `working-dir snapshot:` / run-archive /
        soft-claim commit is dropped before the overlap count — a bulk sweep
        that incidentally co-touches a phase's files is not ship evidence.

    Returns the standard verdict dict. `via="file-path"` is a WEAK verdict —
    the caller still routes it through `_consult_plan_body`, so a plan body
    with no SHIPPED stamp demotes it (no new false-positive surface beyond
    the existing release-prefix / body-mention WEAK verdicts).
    """
    if matchers is None:
        matchers = _subject_matchers()
    files = _extract_phase_files(plan_doc, phase, series, matchers)
    if not files:
        return {"shipped": False, "sha": "", "summary": "", "via": ""}
    # Map each commit sha -> (set of named files it touched, subject line).
    touched: dict[str, set[str]] = {}
    subjects: dict[str, str] = {}
    series_lc = series.lower()
    for path in files:
        # docs/284 — the per-file commit list comes from the shared batch cache
        # when one was built (a single `git log --name-only` over the union of all
        # pairs' files), else from a per-file `git log` subprocess. Both yield the
        # SAME `[(sha, subject), …]` newest-first list capped at `_FILEPATH_WINDOW`
        # for this path, so the overlap below is byte-identical either way.
        if fp_cache is not None:
            file_commits = fp_cache.get(path, [])
        else:
            try:
                # `--no-merges` in lock-step with the batch cache builder
                # (docs/284): a merge commit is never a phase's ship of record, and
                # excluding it keeps this per-file path byte-identical to the batched
                # `--name-only --no-merges` union scan. The underlying feature commit
                # (the real ship) is retained either way.
                lines = _git_log(
                    ["--oneline", "--no-merges", f"-{_FILEPATH_WINDOW}", "--", path]
                )
            except RuntimeError:
                continue
            file_commits = []
            for line in lines:
                parts = line.split(None, 1)
                if not parts:
                    continue
                file_commits.append((parts[0], parts[1] if len(parts) > 1 else ""))
        for sha, subj in file_commits:
            # FQ-77 — exclude bookkeeping commits from the overlap count. A
            # `working-dir snapshot:` commit sweeps hundreds of files in one
            # commit, so it incidentally touches >= 2 of a phase's load-bearing
            # files even when the phase's deliverables do not exist on disk
            # (live false-positive 2026-05-19: `1647b0c0` flagged OC4 shipped).
            # Run-archive rollups / soft-claims are excluded for the same reason
            # the subject scan excludes them — a sweep is not a ship of any one
            # phase. Such a commit never enters `touched`, so it can satisfy
            # neither the >=2-file rule nor the single-file series-attr gate.
            if matchers.is_bookkeeping_subject(subj):
                continue
            subjects[sha] = subj
            touched.setdefault(sha, set()).add(path)
    if not touched:
        return {"shipped": False, "sha": "", "summary": "", "via": ""}
    # Route the multi-vs-single decision on the files that actually HAVE commit
    # history in the capped window — NOT the raw harvested count. A harvested
    # token that touches zero commits (a prose URL, a release-version string, a
    # `Phase 1/summary.txt` fragment the loose generic matcher lifted) is provably
    # not ship evidence: it can satisfy neither the >=2 overlap nor the single-file
    # gate. Counting it toward `len(files)` was a real FALSE-NEGATIVE — it pushed a
    # genuine single-file phase into the >=2 branch (where its lone commit can't
    # meet the overlap), silently losing a true ship (adversarial-review finding).
    # Routing on `live_files` is behaviour-preserving for every REAL verdict (a
    # commitless file changes no outcome) and only removes the inert-noise
    # inflation. `files[0]` for the single-file gate likewise becomes `live_files[0]`
    # so noise-before-real harvest order can't make the noise token the "sole file".
    live_files = [f for f in files if any(f in hit for hit in touched.values())]
    if not live_files:
        return {"shipped": False, "sha": "", "summary": "", "via": ""}
    if len(live_files) >= 2:
        # Multi-file phase: a single commit touching >= 2 named files is the
        # ship — BUT at least one matched file must be distinctive (not a
        # shared-infra hub file). `_git_log` is newest-first, but dict
        # insertion order tracks first-seen across files — scan for the
        # strongest (most-files) hit that satisfies both gates.
        best_sha = ""
        best_n = 1
        for sha, hit in touched.items():
            if len(hit) <= best_n:
                continue
            # False-positive guard: an overlap made up entirely of shared-
            # infra files (`config.py` + `fanout_state.py`, …) is not ship
            # evidence — an unrelated commit touches those together routinely.
            if not any(not _is_shared_infra(f, matchers) for f in hit):
                continue
            # #394 cross-series guard: a commit whose subject names a DIFFERENT
            # plan-series (`vX.Y.Z: ... LG0 ...`, `docs/LG: ...`) that does not
            # also carry THIS series' token is a sibling-series ship — the
            # file-path overlap is a shared load-bearing file, not a ship of
            # `phase`. Refuse it (RTN0 vs `c7c87566`, finding #394).
            if _subject_names_other_series(subjects.get(sha, ""), series):
                continue
            best_n, best_sha = len(hit), sha
        if best_sha:
            return {
                "shipped": True,
                "sha": best_sha,
                "summary": (
                    f"{best_sha} {subjects.get(best_sha, '')} "
                    f"(file-path match: touched {best_n}/{len(live_files)} "
                    f"load-bearing files of {phase})"
                ),
                "via": "file-path",
            }
        return {"shipped": False, "sha": "", "summary": "", "via": ""}
    # Single-file phase: accept only when the touching commit's subject
    # carries the series token — series attribution without the full phase
    # id. The series token may stand alone (`docs/DT: ...`) OR merge into a
    # phase id (`DT7: ...`, `DT3.1 —`); both attribute to the series, so a
    # trailing digit/dot is allowed after the token but a trailing LETTER is
    # not (`DTX` is a different series). A preceding alnum is always rejected.
    #
    # False-positive guard: if the sole load-bearing file is a shared-infra
    # hub (`config.py`, `fanout_state.py`, …), even a series-attributed edit
    # to it is too weak to be ship evidence — a `docs/DT:` commit touching
    # `agents/config.py` is routine. Such a phase yields no file-path verdict.
    if series_lc and not _is_shared_infra(live_files[0], matchers):
        series_attr_re = re.compile(
            rf"{_BOUNDARY_PRE_NEG}{re.escape(series)}(?![A-Za-z])",
            re.IGNORECASE,
        )
        for sha, hit in touched.items():
            subj = subjects.get(sha, "")
            if live_files[0] in hit and series_attr_re.search(subj):
                return {
                    "shipped": True,
                    "sha": sha,
                    "summary": (
                        f"{sha} {subj} (file-path match: sole load-bearing "
                        f"file of {phase}, series-attributed subject)"
                    ),
                    "via": "file-path",
                }
    return {"shipped": False, "sha": "", "summary": "", "via": ""}


def _apply_filepath_backstop(
    result: dict, series: str, phase: str, plan_doc: str | None,
    matchers: "_Matchers | None" = None,
    fp_cache: "dict[str, list[tuple[str, str]]] | None" = None,
) -> dict:
    """Run the AAR-FQ230 file-path false-NEGATIVE backstop on a verdict.

    The single shared entry point for the artefact-based backstop. When the
    subject-token passes (`_check_phase_with_cache` + `_consult_plan_body`)
    all miss BUT the pick names a plan doc, re-derive the verdict from the
    file paths the doc's phase row names vs the file paths recent commits
    touched. Catches DT2-shape ships whose commit subject lacks the phase
    token (#230, recurring 9×).

    Until 2026-05-18 this ran ONLY in `--check-packet`. `/next-up` and
    `/replan` pick work through `--batch` and `check_phase_shipped()`, which
    skipped it — so the fix never ran on the path that picks work, and #230
    kept recurring. This helper closes that gap: all three entry points now
    route through it.

    The file-path verdict is NOT routed through `_consult_plan_body`: the
    file-path set IS an artefact derived from the plan doc itself, so
    re-consulting that doc for a SHIPPED *stamp* would let a missing stamp (a
    known operator habit) overturn the artefact. The overlap rule inside
    `_check_phase_by_filepath` is the false-positive guard, not the stamp.
    """
    if result.get("shipped") or not plan_doc:
        return result
    fp = _check_phase_by_filepath(series, phase, plan_doc, matchers, fp_cache)
    return fp if fp.get("shipped") else result


def build_batch_filepath_cache(
    triples: "list[tuple[str, str, str | None]]",
    matchers: "_Matchers | None" = None,
) -> "dict[str, list[tuple[str, str]]] | None":
    """Build the shared file-path log cache for a whole `--batch` pair-set (docs/284).

    Harvests the union of every pick's load-bearing files (the SAME
    `_extract_phase_files` each `_check_phase_by_filepath` would call, using that
    pick's OWN plan-doc) and folds them into ONE `git log --name-only` scan via
    `_build_filepath_log_cache`. The returned cache is handed to
    `_apply_filepath_backstop(..., fp_cache=…)` for every pick, so the backstop
    does zero per-pick git subprocesses.

    `triples` is `(series, phase, plan_doc | None)` per pick — taken per-pick (not
    a collapsed series→doc map) so two picks of the same series with different
    docs each contribute their own files to the union.

    Returns:
      * the cache — when the union scan stayed within the bounded window; or
      * ``{}`` — when no pick names any file (nothing to scan, no fallback
        needed); or
      * ``None`` — when the union window saturated or git errored, signalling the
        caller to fall back to the exact per-file path (the never-under-count
        safety degrade — a None cache makes `_apply_filepath_backstop` re-run the
        per-file subprocesses, identical to the pre-docs/284 behaviour).
    """
    if matchers is None:
        matchers = _subject_matchers()
    union: set[str] = set()
    for series, phase, plan_doc in triples:
        if not phase or not plan_doc:
            continue
        try:
            union.update(_extract_phase_files(plan_doc, phase, series, matchers))
        except Exception:  # pragma: no cover — a harvest hiccup just skips this pick's files
            continue
    if not union:
        return {}
    return _build_filepath_log_cache(union)


def check_phase_shipped(series: str, phase: str, plan_doc: str | None = None) -> dict:
    """Return {'shipped': bool, 'sha': str, 'summary': str, 'via': str} for the given phase.

    Searches `git log --oneline -1500` repo-wide for two patterns:
      - direct ship: `docs/<SERIES>:?\\s+<PHASE><not-suffix>`
      - summary-bundle: `<vX.Y.Z:|docs/_plans:|docs/HYG:|…>.*<PHASE><not-suffix>`
    If neither hits, also scans the most recent summary-bundle commit *bodies*
    for the phase token (rare case where phase only appears in extended body).
    Finally, when all subject-token passes miss and `plan_doc` is supplied, the
    AAR-FQ230 file-path backstop re-derives the verdict from the artefact (the
    files the phase's plan-doc row names) — see `_apply_filepath_backstop`.

    `plan_doc` (when supplied) cross-checks WEAK git verdicts (release-prefix,
    body-mention) against the plan body's SHIPPED stamps. Direct-ship verdicts
    are never overridden — stamp drift is a known operator habit and the
    direct attribution is unambiguous. See `_consult_plan_body` for the rule.
    """
    # Resolve the active workspace's ship-stamp grammar ONCE and thread it
    # through both scan paths (subject-token + file-path backstop), so a single
    # library call reads one convention end-to-end (design-law 2).
    matchers = _subject_matchers()
    try:
        oneline = _git_log(["--oneline", f"-{_ONELINE_WINDOW}"])
    except RuntimeError as e:
        return {"shipped": False, "sha": "", "summary": "", "via": "", "error": str(e)}
    try:
        body_out = _git_log(["-50", "--format=%h%n%B%n--END--"])
    except RuntimeError:
        body_out = []
    result = _check_phase_with_cache(series, phase, oneline, body_out, matchers)
    result = _consult_plan_body(result, plan_doc, phase, series)
    return _apply_filepath_backstop(result, series, phase, plan_doc, matchers)


def _extract_picks_from_packet(path: str) -> list[tuple[str, str]]:
    """Parse a /next-up packet markdown for pick headers.

    Three header shapes are recognised in pick order:
      1. Standard `### N. <SERIES> <PHASE> — ...` (e.g., `### 1. PLA PLA9 — ...`).
      2. Hygiene `### N. HYG <hygiene_id> — ...` (e.g., `### 3. HYG worker_id_audit — ...`),
         with optional backtick-wrap on the id.
      3. Single-token `### N. <SERIES><N>prime-<n> — ...` (e.g., `### 2. MG3prime-1 — ...`),
         the Windows-quoting workaround for canonical `MG3'-1` style ids.

    A pick may also carry a **depth-slot table** of chained hops:

        **Depth slot — sequential hops (per-hop subprocess):**
        | Hop | Phase | Files | Validate |
        |---|---|---|---|
        | 1 | TS3.2 — ... | ... | ... |
        | 2 | TS5 — ... | ... | ... |

    Each hop is its own dispatchable phase, so every hop row is extracted
    as a `(series, phase)` pair (series inherited from the enclosing
    `### N.` header). Without this, a chain like `TS3.2 → TS5` only got
    its primary hop (TS3.2) staleness-checked — the already-shipped TS5
    slipped through `/fanout`'s Step 1.5 net (dispatch run 20260516T0004Z).

    Returns (series, phase, plan_doc) triples. `plan_doc` is the path from
    the pick body's ``- **Plan doc:** `<path>` `` line (`""` when missing —
    plan-body cross-check is skipped for that pick). Phase ids may carry a
    hyphen-suffix (`SF1.2-port`, `SF7-retry`, `TF-close-out`); preserve them
    — without `\\-` the picker truncates `SF1.2-port` to `SF1.2`, which
    would trigger the suffix false-positive because base `SF1.2` is shipped.
    """
    picks: list[tuple[str, str, str]] = []
    pat_standard = re.compile(r"^###\s+\d+\.\s+([A-Z]+)\s+([A-Z][A-Za-z0-9.\-]+?)\s+[—\-]")
    pat_hyg = re.compile(r"^###\s+\d+\.\s+(HYG)\s+`?([A-Za-z][\w\-]*)`?\s+[—\-]")
    pat_prime = re.compile(r"^###\s+\d+\.\s+(([A-Z]+)\d+prime-\d+(?:\.\d+)?)\s+[—\-]")
    # Depth-slot hop row: `| 2 | TS5 — 7-day soak ... | files | validate |`.
    pat_hop = re.compile(r"^\|\s*\d+\s*\|\s*`?([A-Z][A-Za-z0-9.\-]+?)`?\s+[—\-]")
    # Per-pick plan-doc line: `- **Plan doc:** `docs/foo-plan.md``
    pat_plan_doc = re.compile(r"^\s*[-*]\s*\*\*Plan doc:\*\*\s*`?([^`\n]+?)`?\s*$")
    cur_series: str | None = None
    in_depth_table = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat_standard.match(line)
            if m:
                cur_series = m.group(1)
                in_depth_table = False
                picks.append((m.group(1), m.group(2), ""))
                continue
            m = pat_hyg.match(line)
            if m:
                cur_series = m.group(1)
                in_depth_table = False
                picks.append((m.group(1), m.group(2), ""))
                continue
            m = pat_prime.match(line)
            if m:
                cur_series = m.group(2)
                in_depth_table = False
                picks.append((m.group(2), m.group(1), ""))
                continue
            # Stamp the most recently appended pick's plan-doc field when the
            # `- **Plan doc:** ...` line appears in its body.
            m = pat_plan_doc.match(line)
            if m and picks:
                series, phase, _ = picks[-1]
                picks[-1] = (series, phase, m.group(1).strip())
                continue
            # Track entry into a pick's depth-slot hop table, then extract
            # every hop row as its own (series, phase) pair — inheriting the
            # enclosing pick's plan_doc since hops live in the same plan.
            if "Depth slot — sequential hops" in line:
                in_depth_table = True
                continue
            if in_depth_table and cur_series:
                m = pat_hop.match(line)
                if m:
                    inherited_doc = picks[-1][2] if picks else ""
                    triple = (cur_series, m.group(1), inherited_doc)
                    # Hop 1 is usually the primary phase already captured
                    # by the `### N.` header — dedupe.
                    if not any(t[0] == triple[0] and t[1] == triple[1] for t in picks):
                        picks.append(triple)
                    continue
                # A non-table, non-blank line ends the depth-slot table.
                if line.strip() and not line.lstrip().startswith("|"):
                    in_depth_table = False
    return picks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", nargs="?", help="Plan series prefix, e.g. RS, UP, TF, AO, AT")
    ap.add_argument("phase", nargs="?", help="Exact phase id, e.g. RS4, UP2.1.4, TF11.1")
    ap.add_argument(
        "plan_doc",
        nargs="?",
        help="Plan doc path. STRONGLY recommended: when supplied, a WEAK git "
        "verdict (release-prefix / body-mention bundle) is cross-checked "
        "against the plan body's SHIPPED stamp and demoted if the stamp is "
        "absent. Omit it and a WEAK verdict prints UNCERTAIN instead of a "
        "(possibly wrong) SHIPPED — see _consult_plan_body.",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ap.add_argument(
        "--batch",
        action="store_true",
        help='Read "<series> <phase>" pairs from stdin (one per line); emit one JSON line per pair. '
        '/next-up calls this once per packet to amortize the git-log cost across all candidate picks.',
    )
    ap.add_argument(
        "--check-packet",
        metavar="PATH",
        help="Parse a /next-up packet markdown and check every Pick header against git log. "
        "Prints a per-pick disposition table; exits 0 if any pick is shipped (caller should re-run /next-up), "
        "1 if all picks are clean.",
    )
    args = ap.parse_args()

    # Install the ship-stamp convention this rung must use BEFORE any scan — from
    # the parent's env hand-off, else the workspace's dos.toml, else the job
    # default (see `_bootstrap_active_config`). This is what lets the shelled-out
    # grep rung honour a generic / declared convention, not just the job default.
    _bootstrap_active_config()

    if args.check_packet:
        # Distinct exit codes so callers can tell "no recognizable headers"
        # apart from "all clean":
        #   0 = found-shipped (any pick is in git log → caller should re-run /next-up)
        #   1 = not-shipped   (all picks clean)
        #   2 = no-coverage   (file parsed OK but no picks recognised)
        #   3 = parse-error   (file IO / decode error)
        try:
            picks = _extract_picks_from_packet(args.check_packet)
        except (OSError, UnicodeDecodeError) as e:
            print(f"ERROR could not read {args.check_packet}: {e}", file=sys.stderr)
            return 3
        if not picks:
            print(f"ERROR no picks found in {args.check_packet}", file=sys.stderr)
            return 2
        # Pre-fetch git log once for all picks (same amortization as --batch).
        oneline_lines, body_lines = _build_log_cache()
        # Resolve the workspace's ship-stamp grammar ONCE for every pick, so the
        # whole packet is checked against one convention (design-law 2).
        matchers = _subject_matchers()
        # docs/284 — build the file-path backstop's log cache ONCE over the union
        # of every pick's named files, so the backstop does no per-pick git work.
        fp_cache = build_batch_filepath_cache(
            [(s, ph, pd or None) for s, ph, pd in picks], matchers
        )
        any_stale = False
        print(f"Pre-flight check on {args.check_packet} ({len(picks)} picks):")
        for series, phase, plan_doc in picks:
            r = _check_phase_with_cache(series, phase, oneline_lines, body_lines, matchers)
            r = _consult_plan_body(r, plan_doc or None, phase, series)
            # AAR-FQ230 — subject-token false-NEGATIVE backstop. Shared with
            # `--batch` and `check_phase_shipped()` so every entry point that
            # picks work runs the same artefact check (see helper docstring).
            r = _apply_filepath_backstop(r, series, phase, plan_doc or None, matchers, fp_cache)
            mark = "DROP" if r["shipped"] else "KEEP"
            extra = f"shipped in {r['sha']}" if r["shipped"] else ""
            print(f"  {mark:5s} {series:5s} {phase:12s} {extra}")
            if r["shipped"]:
                any_stale = True
        return 0 if any_stale else 1

    if args.batch:
        # Pre-fetch git log once, then check all pairs against the shared cache.
        # This collapses N git subprocess calls into 2 regardless of batch size.
        # Stdin lines: `<series> <phase> [<plan_doc>]`. When `<plan_doc>` is
        # present, WEAK git verdicts (release-prefix / body-mention) are
        # cross-checked against the plan body for a SHIPPED stamp.
        oneline_lines, body_lines = _build_log_cache()
        # Resolve the workspace convention ONCE for every stdin pair (design-law 2).
        matchers = _subject_matchers()
        # Drain stdin into a pick list FIRST so the file-path backstop's log cache
        # can be built once over the union of every pick's files (docs/284). The
        # per-line output ordering is preserved — only the emit moves after the cache.
        batch_picks: list[tuple[str, str, str]] = []
        for line in sys.stdin:
            line = line.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            series, phase, plan_doc = _parse_batch_line(line)
            if not (series and phase):
                continue
            batch_picks.append((series, phase, plan_doc))
        # docs/284 — one `git log --name-only` over the union, shared across picks.
        fp_cache = build_batch_filepath_cache(
            [(s, ph, pd or None) for s, ph, pd in batch_picks], matchers
        )
        any_shipped = False
        for series, phase, plan_doc in batch_picks:
            result = _check_phase_with_cache(series, phase, oneline_lines, body_lines, matchers)
            result = _consult_plan_body(result, plan_doc, phase, series)
            # AAR-FQ230 — file-path false-NEGATIVE backstop. /next-up and
            # /replan pick work through --batch; without this the #230 fix
            # (previously --check-packet only) never ran on the picker path.
            result = _apply_filepath_backstop(result, series, phase, plan_doc, matchers, fp_cache)
            result["series"] = series
            result["phase"] = phase
            print(json.dumps(result))
            if result["shipped"]:
                any_shipped = True
        return 0 if any_shipped else 1

    if not args.series or not args.phase:
        ap.error("series and phase are required (or use --batch / --check-packet)")

    result = check_phase_shipped(args.series, args.phase, args.plan_doc)

    # A WEAK git verdict (release-prefix / body-mention bundle) with NO plan_doc
    # supplied is the footgun case: _consult_plan_body could not run, so the
    # verdict was NOT cross-checked against the plan body's SHIPPED stamp. This
    # is exactly how a stale plan doc fools a bare `check_phase_shipped` call
    # while /next-up's renderer (which always passes plan_doc) gets it right.
    # Surface it as UNCERTAIN rather than a confident — possibly wrong — SHIPPED.
    weak_unchecked = (
        result.get("shipped")
        and not args.plan_doc
        and result.get("via") in ("release-prefix", "body-mention")
    )

    if args.json:
        if weak_unchecked:
            result["uncertain"] = True
        print(json.dumps(result))
    else:
        if result.get("error"):
            print(f"ERROR {result['error']}", file=sys.stderr)
            return 2
        if weak_unchecked:
            print(
                f"UNCERTAIN {result['sha']} {result['summary']} "
                f"— WEAK verdict, no plan_doc given to cross-check; "
                f"re-run with the plan doc path as the 3rd argument"
            )
            return 3
        if result["shipped"]:
            print(f"SHIPPED {result['sha']} {result['summary']}")
        else:
            print("NOT_SHIPPED")

    return 0 if result["shipped"] else 1


if __name__ == "__main__":
    sys.exit(main())
