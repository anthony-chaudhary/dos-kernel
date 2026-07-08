"""The ship-stamp convention — the grep rung's subject grammar, *as data*.

This is the hackability seam for the kernel's single most-important syscall:
`verify()` (the truth syscall). The registry-first path and the ancestry check
are domain-free already; the one piece that was NOT was the **grep fallback's
grammar** — what a commit subject has to look like to count as a direct ship.
`phase_shipped.py` hardcoded the *reference userland app's* convention:

    _DIRECT_PREFIX = r"(?:docs|go|agents|job_search|scripts)"   # the host's own top-level dirs

so a direct ship had to read `docs/<SERIES>: <PHASE>` / `go/<SERIES>:`. A foreign
repo committing `AUTH: AUTH2 — ship token refresh` (the `<SERIES>: <PHASE>` shape
with no dir prefix) resolved to `NOT_SHIPPED (via none)` even though the subject
literally names the phase. The North Star claims `verify` works against *any* git
repo from history alone; that was true only for the reference userland app's own
subject convention.

This module lifts that grammar into per-workspace data, exactly the way
`LaneTaxonomy` lifted the lane clusters and `ReasonRegistry` lifted the refusal
vocabulary. The *mechanism* (grep `git log`, ancestry-check, registry-first,
the progress-marker / bookkeeping demotions) stays in `phase_shipped.py`; the
*grammar of a ship subject* moves here as a `StampConvention` a host declares.

The shape
=========

A `StampConvention` is the closed set of subject-shape parameters the matcher
needs. It carries no regex itself — it carries the *data* (which dir prefixes,
which summary-bundle prefixes, which bookkeeping prefixes) and exposes the three
**regex fragments** `phase_shipped` interpolates into its compiled patterns:

  * ``direct_prefix_re()`` — the alternation that anchors a direct ship.
    Job: ``(?:docs|go|agents|job_search|scripts)/``. Generic (no ``subject_dirs``):
    an *optional* prefix so a bare ``<SERIES>: <PHASE>`` matches with no dir at all.
  * ``summary_subject_re()`` — the ``vX.Y.Z:`` release shape OR an allowlisted
    standalone-summary prefix (job: ``docs/HYG:``). Gates the release-prefix and
    body scans.
  * ``bookkeeping_subject_re()`` — the ship-SHAPED-but-not-a-ship exclusion
    (soft-claims, archive rollups, bulk snapshots). A subject matching this is
    never counted as a ship on any scan path.

Two named constants ship in the package:

  * ``JOB_STAMP_CONVENTION`` — the current hardcoded grammar, lifted verbatim, so
    the reference userland app and the existing kernel suite are byte-for-byte
    unchanged. It is a plain default the kernel falls back to (NOT an import from
    ``drivers.job``) — the same pattern as the ``main``/``global`` lanes in ``config.py``.
  * ``GENERIC_STAMP_CONVENTION`` — no dir prefix, no host-specific bundle/
    bookkeeping prefixes (only the universal ``vX.Y.Z:`` release shape and the
    universal ``... snapshot:`` bulk-commit guard). This is what an external
    repo's subjects look like: a bare ``<SERIES>: <PHASE>`` / ``<SERIES><PHASE>``.

Pure stdlib — no third-party imports, no I/O — so `phase_shipped` imports it as a
leaf, the same way it would have used the module-level constants it replaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# The universal release-subject anchor — a `vX.Y.Z:` version-cut commit that
# bundles several ships into one free-form summary line. This shape is NOT
# host-specific (every repo that tags releases uses it), so it is baked into the
# fragment builder rather than declared per-workspace; a convention adds only its
# *extra* standalone-summary prefixes (job's `docs/HYG:`) on top of it.
_RELEASE_VERSION_ANCHOR = r"v\d+\.\d+\.\d+:"

# The universal bulk-snapshot guard — a `working-dir snapshot:` / `... snapshot:`
# commit sweeps hundreds of files and quotes phase ids descriptively, never as a
# ship attribution. Like the release anchor this is host-agnostic, so it is part
# of every bookkeeping regex regardless of the declared prefixes.
_SNAPSHOT_BOOKKEEPING_FRAGMENT = r"[^:]*\bsnapshot:"

# The universal run-archive-rollup guard — a `<prefix>: archive <RUN-ID> …` commit
# is a fan-out / dispatch run rollup that QUOTES the phase ids of the runs it
# archives, never a ship of any one of them (live false-positive: a foreign repo's
# `docs/fanout: archive 20260530T093407Z chain (vllm-p2p3, …)` resolved as a ship
# of a `fanout`/`archive` phase under the generic dir-free grammar). The
# discriminator is host-agnostic and TIGHT: the word `archive` (or `rollup`)
# immediately followed by a run-id-shaped timestamp token (`20260530T093407Z` or a
# bare `20260530`). Requiring the timestamp is what keeps it from excluding a
# legitimately-named `archive` PHASE — a real `… : archive` ship has no run-id
# tail.
#
# An OPTIONAL `<prefix>:` is allowed before `archive` (`docs/fanout: archive …`,
# `chore: archive …`, or a bare `archive …`) — a single non-colon prefix segment
# then one colon — so the guard fires regardless of whether the host declared the
# prefix as bookkeeping. This is the zero-config safety net BENEATH the declared
# `bookkeeping_prefixes`: a host that names its rollup prefix (`docs/fanout:`)
# still gets the precise exclusion; a host that declares nothing is still safe
# against the universal `archive <run-id>` shape.
#
# The run-id tail is `<YYYYMMDD>` optionally followed by a `T`-separated time of
# VARIABLE width and an optional trailing `Z` — real fan-out run-ids occur as
# `20260530T093407Z` (full HHMMSS), `20260529T0233Z` (shortened), and bare
# `20260530`. The time component is `t\d+z?` (one-or-more digits) rather than a
# fixed `\d{6}` so every observed run-id shape is caught; the leading 8-digit date
# is the load-bearing discriminator (a real `archive` phase ship has no date tail).
_RUN_ARCHIVE_BOOKKEEPING_FRAGMENT = (
    r"(?:[^:]*:\s*)?(?:archive|rollup)\s+\d{8}(?:t\d+z?)?\b"
)

# The universal shared-infra basenames — hub files nearly every phase touches, so
# a coincidental edit to them is never the *distinctive* ship evidence the
# file-path rung counts on (`_check_phase_by_filepath`'s 2-file overlap rule). A
# section that names two of these alone must NOT let an unrelated commit
# false-ship the phase. This set is host-AGNOSTIC — `config.py`/`__init__.py`/
# `cli.py`/`conftest.py` are hub files in *any* Python repo — so it is baked into
# every convention. A host with its OWN hub file (the reference app's
# `fanout_state.py`) declares it as an EXTRA via `infra_basenames`, layered on top
# of this base (the additive discipline, same as the release anchor).
_UNIVERSAL_INFRA_BASENAMES = frozenset({
    "config.py", "__init__.py", "models.py", "cli.py", "utils.py",
    "constants.py", "settings.py", "conftest.py",
})

# The universal bulk-regenerated documentation guard — any rendered diagram under
# `docs/` (`.mmd` source or `.png` render) is co-regenerated wholesale by unrelated
# release commits, so it is shared-infra for the same reason the hub code files are
# (AAR-FQ-DL4). The *suffix* rule (any `docs/…*.mmd`/`*.png`) is host-agnostic; a
# host's SPECIFIC named reference hubs (the reference app's `architecture.mmd`,
# `00_subsystems-reference.md`) are declared as EXTRAS via `infra_doc_basenames`.
_UNIVERSAL_DIAGRAM_SUFFIXES = (".mmd", ".png")


# Phase-label tokens: `P3`, `P4.6`, `P1c`, `P3b.2`, or `Phase 1c` / `Phase 1`.
# The digit must immediately follow `P` (or `Phase `) so prose like "Python",
# "PR", or "GPT-3" never matches. Body = `<digit>[<sub-letter>][.<digit>]`, so a
# letter-then-decimal sub-phase (`P3b.2`) is captured; the trailing `\b` rejects
# `P3xyz`. Pure-stdlib leaf primitive (no I/O) — the subject grammar this module
# already owns, lifted UP from bench's scripts/next_context.py:_PHASE_LABEL_RE.
_PHASE_LABEL_RE = re.compile(
    r"\b(?:Phase\s+|P)\d+[a-z]?(?:\.\d+)?\b", re.IGNORECASE
)


def parse_phase_labels(subject: str | None) -> list[str]:
    """Extract normalized phase-id tokens from a commit subject.

    "SGLang-Metrics P3 …"  -> ["P3"]
    "exec-sweep P4.6 done" -> ["P4.6"]
    "exec-sweep P3b.2 …"   -> ["P3b.2"]    (letter-then-decimal sub-phase)
    "L3 busy device Phase 1c" -> ["P1c"]   (Phase N -> PN)
    "close out all P0s"    -> ["P0"]       (plural artifact stripped)
    "fix typo in readme"   -> []           (no false positives on prose)
    None                   -> []           (None-safe)

    Returns a sorted, de-duplicated list. Pure (no I/O) — a leaf primitive on
    the same module that owns the ship-subject grammar.
    """
    labels: set[str] = set()
    for m in _PHASE_LABEL_RE.finditer(subject or ""):
        tok = re.sub(r"(?i)^phase\s+", "P", m.group(0))
        tok = tok[0].upper() + tok[1:]            # normalize leading p3 -> P3
        tok = re.sub(r"(?<=\d)s$", "", tok)        # drop plural artifact: P0s -> P0
        labels.add(tok)
    return sorted(labels)


@dataclass(frozen=True)
class StampConvention:
    """How a workspace stamps a shipped phase in its commit subjects — as data.

    Every field is the *data* behind one regex fragment the grep rung compiles;
    no field is a regex itself (a host declares dir names, not patterns). The
    matcher in `phase_shipped` reads the three ``*_re()`` accessors and never the
    raw constants it used to hardcode.

    Fields:
      subject_dirs
          The top-level dirs a *direct* ship subject may carry before
          ``<SERIES>:`` — the reference userland app's ``docs``/``go``/``agents``/
          ``job_search``/``scripts``. An **empty** tuple means "no dir prefix": a bare
          ``<SERIES>: <PHASE>`` (the generic external-repo shape). The accessor
          makes the prefix optional in that case rather than impossible.
      summary_bundle_prefixes
          Standalone-summary subject prefixes (besides the universal ``vX.Y.Z:``)
          that may bundle several phases into one free-form line — job's
          ``docs/HYG:``. A foreign repo usually declares none and relies on the
          release anchor alone.
      bookkeeping_prefixes
          Subject prefixes that NAME phase ids without shipping them (soft-claims,
          run-archive rollups): job's ``docs/_plans:`` / ``docs/fanout:`` / …. A
          subject matching one of these (or the universal ``snapshot:`` guard) is
          excluded from ship-detection on every scan path. Matched
          case-insensitively, anchored at subject start.
      style
          The detection style. Only ``"grep"`` is implemented (scan git-log
          subjects). Kept as the forward extension point for a future tag- or
          trailer-based style; a non-``"grep"`` value is accepted as data but the
          kernel still runs the grep rung (the field is declarative-only for now).

      code_dirs
          The top-level dirs whose files count as a phase's *load-bearing
          deliverables* for the **file-path backstop** rung
          (`phase_shipped._check_phase_by_filepath`). That rung harvests the file
          paths a phase's plan-doc section names, then asks "did one commit touch
          ≥2 of them together?" — an artefact match that catches a ship whose
          commit *subject* drifted off the phase token. To harvest a path the rung
          must first RECOGNISE the token as a repo-file path, which it does by
          rooting it at a known top-level dir. The reference app hardcoded its own
          dirs (``agents|job_search|go|scripts|templates|config|docs|tests``); a
          foreign repo whose deliverables live under ``engine/``/``models/``/
          ``commands/`` saw the rung harvest **nothing**, so the artefact backstop
          was dead and every subject-drifted ship resolved ``via none``.

          This lifts that allowlist to data. An **empty** tuple (the generic
          default) means "any plausible top-level dir": a path token rooted at any
          ``<segment>/…<ext>`` is harvested. That is SOUND — the dir allowlist was
          only ever a *recognition* narrowing, never a false-positive gate (those
          are the 2-file-overlap, distinctive-file, bookkeeping-exclusion, and
          cross-series guards downstream, all preserved). A host that wants the
          tight allowlist (the reference app) declares its dirs here.
      infra_basenames
          EXTRA shared-infra hub *code* file basenames, layered ON TOP of the
          universal set (`_UNIVERSAL_INFRA_BASENAMES`: ``config.py``/``cli.py``/
          ``conftest.py``/…). A file whose basename is shared-infra is excluded
          from the file-path rung's *distinctive*-overlap count — a coincidental
          edit to a hub file is not ship evidence. The universal set covers any
          Python repo; a host's OWN hub (the reference app's ``fanout_state.py``)
          is declared here. Additive, never replace — you cannot un-declare a
          universal hub (it is shared-infra by nature).
      infra_doc_basenames
          EXTRA bulk-regenerated documentation hub basenames, layered on top of
          the universal diagram rule (any ``docs/…*.mmd``/``*.png`` is shared-infra
          regardless). A host's named cross-cutting reference docs (the reference
          app's ``architecture.mmd``/``00_subsystems-reference.md``) go here.
          Additive, same discipline as ``infra_basenames``.
      progress_markers
          Words that, immediately after the phase id with a bare space (no
          ``:``/``—``/``-`` separator), mark a commit as *progress on* a multi-step
          phase rather than a *ship of* it — the reference app's soak/observation
          vocabulary (``week-1``/``audit``/``baseline``/``soak``/…). The grep rung
          DEMOTES a ``<dir>/<SERIES>: <PHASE> <marker>`` subject so an incremental
          commit on a long-running phase is not mistaken for its close-out ship.

          This was a hardcoded module frozenset, so it fired on EVERY repo — a
          foreign repo's genuine direct ship ``cache: Phase 0 audit of …`` was
          silently demoted to NOT_SHIPPED because ``audit`` followed the id (a real
          Benchmark false-negative). An **empty** tuple (the generic default) means
          "no progress vocabulary" → a foreign repo's real ships are never demoted;
          the worst failure mode (a *lost* ship) cannot happen out of the box. The
          reference app declares its markers here; a host with its own soak
          vocabulary declares its own.
      sub_phase_parent_fallback
          Whether a hyphen-suffixed query (``RS4-port``) that misses every direct
          pass should fall back to checking the bare PARENT phase (``RS4``) and
          accept it if the suffix slug appears in the matched commit's subject — a
          reference-app convenience for its sub-phase id habit. It was gated purely
          on the QUERY shape (``if "-" in phase``), so it fired on any repo: a
          fabricated ``P2-CLI`` false-resolved to a real ``P2`` ship whose subject
          merely contained ``CLI`` (a real Benchmark false-positive). Lifting it to
          a per-convention FLAG (default ``False``) makes the behaviour declared,
          not inferred from a query the kernel doesn't control — the closed-enum
          discipline applied to a feature toggle. The reference app sets it
          ``True``; a generic repo never runs the fallback.
      trailer_stamp
          Whether a subject whose TAIL is ``(<PLAN> <PHASE>)`` — also
          ``(<PLAN>: <PHASE>)`` and ``(refs <PLAN> <PHASE>)`` — counts as a
          direct ship of that ``(plan, phase)`` (docs/289). The
          Conventional-Commits shape: ``feat(pypi): … (docs/286 Phase 3)``
          carries the stamp as a parenthesized trailer at the END of the
          subject, which no start-anchored grammar can see. Opt-in (default
          ``False``) because it widens what is *recognized*; the tightness the
          start anchor provided comes from the end anchor + required parens
          instead (`trailer_ship_core`). The trailer is exactly as forgeable as
          the start-anchored subject, so the rung grades `grep-subject` like
          the direct rung it mirrors.
      issue_leaf_stamp
          Whether a subject carrying BOTH a ``(#<issue>)`` cite and a
          ``(<ns> <leaf>)`` named trailer counts as a direct ship of
          ``(plan=<leaf>, phase=<issue>)`` (fak #3132). The split-paren
          Conventional-Commits shape: ``fix(gateway): … (#2911) (fak tools)``
          verifies as ``dos verify tools 2911`` — plan is the trailer's LEAF
          token, phase the all-digits issue number. Opt-in (default ``False``)
          so no other workspace's grammar widens; graded `grep-subject` like
          the trailer rung it mirrors (agent-authored subject text).
    """

    subject_dirs: tuple[str, ...] = ()
    summary_bundle_prefixes: tuple[str, ...] = ()
    bookkeeping_prefixes: tuple[str, ...] = ()
    style: str = "grep"
    code_dirs: tuple[str, ...] = ()
    infra_basenames: tuple[str, ...] = ()
    infra_doc_basenames: tuple[str, ...] = ()
    progress_markers: tuple[str, ...] = ()
    sub_phase_parent_fallback: bool = False
    trailer_stamp: bool = False
    issue_leaf_stamp: bool = False

    # -- serialization (crosses the grep-rung subprocess boundary) ----------
    def to_dict(self) -> dict:
        """Plain-data form (lists, not tuples) — JSON-serializable.

        Used to carry the active convention into the `phase_shipped` SUBPROCESS:
        the grep rung shells out to a fresh Python process whose `config.active()`
        would otherwise re-derive the DEFAULT (job) convention, losing a
        caller-installed or `dos.toml`-declared one. The parent serializes the
        active convention into an env var; the child rebuilds it with `from_dict`.
        This makes the in-process `set_active(cfg)` authoritative across the
        process boundary, the same way it is in-process (design-law 2 — one
        convention, every path, even the shelled-out one).
        """
        return {
            "subject_dirs": list(self.subject_dirs),
            "summary_bundle_prefixes": list(self.summary_bundle_prefixes),
            "bookkeeping_prefixes": list(self.bookkeeping_prefixes),
            "style": self.style,
            "code_dirs": list(self.code_dirs),
            "infra_basenames": list(self.infra_basenames),
            "infra_doc_basenames": list(self.infra_doc_basenames),
            "progress_markers": list(self.progress_markers),
            "sub_phase_parent_fallback": self.sub_phase_parent_fallback,
            "trailer_stamp": self.trailer_stamp,
            "issue_leaf_stamp": self.issue_leaf_stamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StampConvention":
        """Rebuild a convention from its `to_dict` form. Tolerant of missing keys
        (each defaults to the empty/`"grep"` value) so a partial/forward-compatible
        payload never crashes the child — it degrades to the generic shape."""
        return cls(
            subject_dirs=tuple(data.get("subject_dirs", ()) or ()),
            summary_bundle_prefixes=tuple(data.get("summary_bundle_prefixes", ()) or ()),
            bookkeeping_prefixes=tuple(data.get("bookkeeping_prefixes", ()) or ()),
            style=str(data.get("style", "grep") or "grep"),
            code_dirs=tuple(data.get("code_dirs", ()) or ()),
            infra_basenames=tuple(data.get("infra_basenames", ()) or ()),
            infra_doc_basenames=tuple(data.get("infra_doc_basenames", ()) or ()),
            progress_markers=tuple(data.get("progress_markers", ()) or ()),
            sub_phase_parent_fallback=bool(data.get("sub_phase_parent_fallback", False)),
            trailer_stamp=bool(data.get("trailer_stamp", False)),
            issue_leaf_stamp=bool(data.get("issue_leaf_stamp", False)),
        )

    # -- the three regex fragments the grep rung interpolates ---------------
    def direct_prefix_re(self) -> str:
        """The regex fragment anchoring a direct-ship subject's dir prefix.

        With ``subject_dirs`` → ``(?:docs|go|…)/`` (the prefix is REQUIRED, the
        job grammar). With no ``subject_dirs`` → ``(?:\\w[\\w.\\-]*/)?`` — an
        OPTIONAL SINGLE-component path prefix, so both a bare ``AUTH: AUTH2`` (no
        dir) and a ``src/AUTH: AUTH2`` (one dir) match. This is what makes the truth
        syscall domain-free: an external repo that commits ``AUTH2: …`` with no dir
        prefix is recognised, while a repo that scopes ships under a dir still works.

        Returned WITHOUT the trailing ``<SERIES>:`` — the caller appends the
        series + phase alternation, exactly as it did with the old
        ``_DIRECT_PREFIX`` constant.
        """
        if self.subject_dirs:
            alt = "|".join(re.escape(d) for d in self.subject_dirs)
            return rf"(?:{alt})/"
        # No declared dirs: accept an optional leading path segment so a bare
        # `<SERIES>:` subject matches. The segment is a SINGLE path component
        # (`\w[\w.\-]*/`, NO embedded `/`) made optional; it is NOT a greedy `.*`
        # and NOT multi-segment. A `/` in the class let `docs/notes/sub/AUTH2:` (a
        # deep, unrelated note that merely *names* the id) false-match a direct
        # ship — the adversarial-review correctness finding. Keeping it one segment
        # holds the direct anchor tight to the subject start; a release/bookkeeping
        # subject is handled by its own guards.
        return r"(?:\w[\w.\-]*/)?"

    def direct_ship_core(self, series_re: str, phase_alt: str) -> str:
        """The full direct-ship regex core (everything after the `<sha>\\s+`).

        Builds the dir prefix + the series/phase shape, branching on whether the
        convention declares ``subject_dirs``. The caller anchors a boundary after
        it and compiles case-insensitively; ``series_re`` and ``phase_alt`` are
        already-escaped fragments (the caller built them from `_phase_variants`).

        Two distinct ship-subject shapes a host uses — and why generic needs both:

          * **Prefixed** (the JOB shape, and the spaced generic form):
            ``<dir>/<SERIES>:?\\s+<PHASE>`` — series, optional colon, whitespace,
            then the phase token. This is `docs/AUTH: AUTH2` and the spaced
            `AUTH: 2`. The ONLY shape the job convention emits, so when
            ``subject_dirs`` is set this is returned alone — byte-identical to the
            pre-SCV `{_DIRECT_PREFIX}/{series}:?\\s+{phase}` pattern.
          * **Glued** (the bare-id generic shape):
            ``<SERIES><PHASE>:`` — the *concatenated phase id* at subject start
            followed by a colon. This is the North-Star `AUTH2: ship token
            refresh`, where `AUTH2` = series+phase. A no-dir convention adds this
            as a second alternative so a foreign repo that names the phase id
            directly (the common external convention) is recognised.

        A no-dir (generic) convention therefore matches EITHER shape; a
        dir-scoped (job) convention matches only the prefixed shape, so nothing
        about the job grep rung changes.
        """
        prefix = self.direct_prefix_re()
        prefixed = rf"{prefix}{series_re}:?\s+(?:{phase_alt})"
        if self.subject_dirs:
            return prefixed
        # Generic: also accept the glued `<SERIES><PHASE>:` form. The phase
        # alternation already contains the bare phase tokens; gluing the series
        # in front yields the full phase id (`AUTH` + `2` → `AUTH2`). Require the
        # trailing colon so a glued match is unambiguously a ship attribution
        # (`AUTH2:`), not an incidental substring. The series is optional in the
        # glue so a query that already passes the full id as the phase (`AUTH2`)
        # still matches without doubling the series.
        glued = rf"{prefix}(?:{series_re})?(?:{phase_alt}):"
        return rf"(?:{prefixed}|{glued})"

    def trailer_ship_core(self, series_alt: str, phase_alt: str) -> str | None:
        """The trailer-form direct-ship fragment, or None when the convention
        doesn't opt in (docs/289).

        Matches a parenthesized ``(<PLAN> <PHASE>)`` stamp at the END of a
        subject — the Conventional-Commits shape (``feat(pypi): …
        (docs/286 Phase 3)``), which the start-anchored `direct_ship_core` can
        never see. Three spellings: ``(<PLAN> <PHASE>)``, ``(<PLAN>: <PHASE>)``,
        ``(refs <PLAN> <PHASE>)``, each optionally followed by a trailing issue
        ref inside the same paren (``(docs/318 P2, #21)`` — docs/289/#128).
        Unlike the other fragments this one carries its OWN anchor (``\\)\\s*$``)
        — the caller searches rather than appending a boundary; the close paren
        (after the phase token and an OPTIONAL ``, #NN`` issue ref) IS the right
        boundary (a ``Phase 3`` query cannot match ``(… Phase 30)`` or
        ``(… Phase 3 audit)`` — a progress-marked trailer is not a ship,
        fail-closed; the issue-ref group requires a literal ``#`` so it never
        loosens that boundary), and the end anchor is what keeps a subject that
        merely NAMES an id in prose (or in a mid-subject paren) from matching.

        ``series_alt`` is an already-escaped alternation of plan-id spellings
        (the caller bridges ``docs/286_<slug>`` ↔ ``docs/286`` — see
        `phase_shipped._series_variants`); ``phase_alt`` is the same
        `_phase_variants` alternation every other rung uses. The convention's
        dir prefix is admitted OPTIONALLY before the series — even when
        ``subject_dirs`` makes it required at subject start — because a trailer
        names the plan as written in the plan registry (``docs/286``), not as a
        ship-subject prefix; the parens + end anchor carry the tightness the
        required prefix used to.

        Bookkeeping/summary exclusion is the CALLER's job (the same post-match
        guards as the direct pass — `phase_shipped` Pass 1a′), exactly as it is
        for `direct_ship_core`.
        """
        if not self.trailer_stamp:
            return None
        prefix = self.direct_prefix_re()
        # docs/289 (#128) — an optional issue ref may follow the phase token
        # INSIDE the stamp paren: `(docs/318 P2, #21)`, `(docs/286 Phase 3,
        # fixes #5)`. A trailing `, #NN` / `#NN` / `fixes #NN` / `closes #NN`
        # is a common Conventional-Commits habit; without this group it pushes
        # the phase token off the `\)` boundary and the real ship reads
        # NOT_SHIPPED (silently — the worst trust gap). The group REQUIRES a
        # literal `#`, so it never loosens the phase boundary: `Phase 30`,
        # `Phase 3.1`, and the progress-marked `Phase 3 audit` still fail (the
        # char after the phase token is a digit / dot / space-then-word, none of
        # which the `[,;]?\s*(?:fixes|closes|refs)?\s*#` opener can begin), and
        # the `\)\s*$` end anchor is preserved so a mid-subject paren still
        # can't match. Multiple refs (`, #21, #22`) are admitted by the repeat.
        issue_ref = r"(?:\s*[,;]?\s*(?:fixes|closes|refs)?\s*#\d+)*"
        return (
            rf"\(\s*(?:refs\s+)?(?:{prefix})?(?:{series_alt}):?\s+"
            rf"(?:{phase_alt}){issue_ref}\s*\)\s*$"
        )

    def summary_subject_re(self) -> str:
        """The regex fragment matching a summary-bundle subject.

        ``vX.Y.Z:`` (the universal release anchor) OR any declared
        ``summary_bundle_prefixes`` (job: ``docs/HYG:``). Used in place of the
        bare release anchor in the release-prefix scan and the body-scan's
        in-summary gate. Mirrors the old ``_SUMMARY_SUBJECT_RE`` construction.
        """
        parts = [_RELEASE_VERSION_ANCHOR]
        parts += [re.escape(p) for p in self.summary_bundle_prefixes]
        return r"(?:" + "|".join(parts) + r")"

    def bookkeeping_subject_re(self) -> "re.Pattern[str]":
        """The compiled, case-insensitive, start-anchored bookkeeping matcher.

        A subject matching this NAMES phase ids as narrative (soft-claims,
        archive rollups, bulk snapshots) and must never count as a ship. Always
        includes TWO universal, host-agnostic guards — the ``... snapshot:`` bulk
        guard and the ``… archive <RUN-ID>`` run-rollup guard — plus any declared
        ``bookkeeping_prefixes``. Mirrors the old ``_BOOKKEEPING_SUBJECT_RE``,
        extended with the run-archive guard (the L2 zero-config fix).

        A convention with NO declared bookkeeping prefixes still excludes bulk
        snapshots AND run-archive rollups (the two universal guards), so the
        generic convention is safe out of the box against the two commonest
        names-but-ships-nothing shapes; it just doesn't know about a host's named
        rollup prefixes (job's `docs/_plans:`) — a foreign repo declares its own.
        """
        parts = [re.escape(p) for p in self.bookkeeping_prefixes]
        parts.append(_SNAPSHOT_BOOKKEEPING_FRAGMENT)
        parts.append(_RUN_ARCHIVE_BOOKKEEPING_FRAGMENT)
        return re.compile(r"^(?:" + "|".join(parts) + r")", re.IGNORECASE)

    # -- the file-path backstop rung (artefact match, see phase_shipped) ----
    def repo_path_re(self) -> "re.Pattern[str]":
        """The compiled regex that harvests repo-file paths from a plan-doc section.

        The file-path backstop (`phase_shipped._extract_phase_files`) scans a
        phase's plan-doc section for the file paths it names — both markdown link
        targets (``[`engine/run.py`](../engine/run.py)``) and inline backtick paths
        (`` `models/metrics.py` ``). Both reduce to a token rooted at a top-level
        dir and ending in a file extension; the leading ``../`` link-relative
        prefix is stripped. This builds that matcher from ``code_dirs``:

          * ``code_dirs`` declared (the reference app) → a TIGHT allowlist:
            ``(?:agents|job_search|…)/<path>.<ext>``. Only those dirs' paths are
            harvested — byte-identical to the pre-genericization ``_REPO_PATH_RE``
            when ``code_dirs`` is the reference app's dir set.
          * ``code_dirs`` empty (the generic default) → ANY plausible top-level
            dir: a single path segment (``\\w[\\w.\\-]*``) then ``/<path>.<ext>``.
            This is what makes the artefact rung work on a foreign repo whose
            deliverables live under ``engine/``/``models/``/``commands/`` — dirs
            the reference allowlist never named. SOUND because the dir set was
            only ever a recognition narrowing: the false-positive gates (2-file
            overlap, distinctive-file, bookkeeping exclusion, cross-series) all
            live downstream and are unchanged.

        The capture group is group(1): the repo-relative path with the ``../``
        link prefix stripped. The extension is required (``.<ext>``) so a bare
        directory mention (``engine/``) is not harvested as a file.
        """
        if self.code_dirs:
            # Tight allowlist: a closed set of real dir names. This branch is kept
            # BYTE-IDENTICAL to the pre-genericization `_REPO_PATH_RE` (no left
            # boundary) so the reference app's artefact rung is unchanged — its
            # alternation is already a closed set, so a URL host can't sneak in.
            alt = "|".join(re.escape(d) for d in self.code_dirs)
            return re.compile(
                rf"(?:\.\.?/)*((?:{alt})/[\w./-]+\.[A-Za-z0-9]+)"
            )
        # Generic (no declared dirs): a single top-level path component, but with
        # NO dot in the FIRST segment — a real top-level dir (`src/`, `docs/`,
        # `my_pkg/`) never carries a dot, whereas a URL host (`github.com`) and a
        # version root (`v1.2.3`) always do. Excluding the dot stops the harvester
        # lifting a URL / release-version string out of plan prose and treating it
        # as a load-bearing file — the adversarial-review false-positive (and the
        # `len(files)`-inflation false-negative). The `(?<![\w./-])` LEFT boundary
        # is REQUIRED alongside the no-dot segment: without it the matcher just
        # slides its start rightward and still extracts `com/user/repo.git` from a
        # URL (the dot host is skipped, the next segment matches). NOT a greedy
        # `.*`: one named segment, then the rest of the path + extension.
        return re.compile(
            r"(?<![\w./-])(?:\.\.?/)*(\w[\w\-]*/[\w./-]+\.[A-Za-z0-9]+)"
        )

    def infra_basename_set(self) -> frozenset[str]:
        """The full shared-infra *code* basenames: universal ∪ declared extras.

        A file whose basename is in this set is excluded from the file-path rung's
        *distinctive*-overlap count (`is_shared_infra`). The universal set covers
        any repo; ``infra_basenames`` adds a host's own hub (the reference app's
        ``fanout_state.py``). Additive — a host extends, never replaces, the
        universal set, because a universal hub is shared-infra by nature.
        """
        return _UNIVERSAL_INFRA_BASENAMES | frozenset(self.infra_basenames)

    def infra_doc_basename_set(self) -> frozenset[str]:
        """The full shared-infra *doc* basenames: declared extras only.

        The universal diagram rule (any ``docs/…*.mmd``/``*.png``) is applied
        separately in `is_shared_infra` by suffix; this set is the host's NAMED
        reference hubs (the reference app's ``architecture.mmd``/
        ``00_subsystems-reference.md``). Generic repos declare none.
        """
        return frozenset(self.infra_doc_basenames)

    def is_shared_infra(self, path: str) -> bool:
        """True if ``path`` is a hub file excluded from the file-path overlap count.

        Three classes are excluded — all too widely-touched for a coincidental edit
        to be ship evidence (the false-POSITIVE guard, see
        `phase_shipped._check_phase_by_filepath`):

          * hub *code* files (universal ∪ declared ``infra_basenames``), by basename;
          * a host's named *documentation* hubs (declared ``infra_doc_basenames``);
          * ANY rendered diagram under ``docs/`` (``*.mmd``/``*.png``) — host-agnostic.

        **Case is folded** (`str.casefold`) on every comparison — the same discipline
        `_tree.norm_tree_prefix` and the sibling `progress_marker_set` use. On a
        case-INsensitive FS (Windows, the primary platform) ``agents/Config.py`` IS
        ``agents/config.py``; without folding, a mis-cased hub file failed the
        basename membership, was treated as a DISTINCTIVE phase deliverable, and could
        FALSE-SHIP a phase (the file-path rung's all-infra skip never fired, and the
        single-file gate passed a sole mis-cased hub). Folding unconditionally for the
        same cross-platform-determinism reason `_tree` gives.

        Pure (no I/O) so the file-path rung stays replay-testable, matching the
        ``classify(Evidence, Policy)`` discipline of the rest of the kernel.
        """
        p = path.casefold()
        base = p.rsplit("/", 1)[-1]
        # Sets are folded to match (built lowercase by convention, but fold defensively
        # so a host that declares a capitalized extra still matches a real edit).
        code = {b.casefold() for b in self.infra_basename_set()}
        docs = {b.casefold() for b in self.infra_doc_basename_set()}
        if base in code or base in docs:
            return True
        # Any diagram under docs/ is a regenerated hub, not a distinctive deliverable.
        if p.startswith("docs/") and base.endswith(_UNIVERSAL_DIAGRAM_SUFFIXES):
            return True
        return False

    # -- progress-marker demotion + bundle-slug fallback (see phase_shipped) --
    def progress_marker_set(self) -> frozenset[str]:
        """The lowercased progress-marker words for this convention.

        A subject of shape ``<dir>/<SERIES>: <PHASE> <marker>`` (bare space, no
        separator) is demoted from a ship to *progress on* the phase when
        ``<marker>`` is in this set. Empty (generic) → no demotion ever, so a
        foreign repo's real ships are never silently lost (the L1 fix). Lowercased
        here so the caller's comparison is case-insensitive without re-lowering.
        """
        return frozenset(w.lower() for w in self.progress_markers)

    def bundle_slugs(self) -> frozenset[str]:
        """The UPPERCASED series slugs derived from ``summary_bundle_prefixes``.

        A standalone-summary prefix like ``docs/HYG:`` carries a series slug
        (``HYG``) whose plan ids are snake-case (``dropbox_zero_apply``) but whose
        commit subjects use prose (``docs/HYG: Dropbox zero-apply …``). The grep
        rung runs a prose-slug fallback for exactly those series. This derives the
        eligible slugs from the DECLARED bundle prefixes rather than a hardcoded
        ``"HYG"`` literal (the L4 fix): the trailing ``:`` and any leading
        ``<dir>/`` are stripped, the remainder uppercased. A generic convention
        declares no bundle prefixes → no slug is eligible → the fallback is inert.
        """
        out: set[str] = set()
        for p in self.summary_bundle_prefixes:
            slug = p.strip().rstrip(":")
            if "/" in slug:
                slug = slug.rsplit("/", 1)[-1]
            if slug:
                out.add(slug.upper())
        return frozenset(out)

    def recognizes_direct_ship(self, subject: str) -> bool:
        """True iff this convention's direct-ship anchor matches `subject` for
        SOME plausible `<SERIES><PHASE>` — a convention-aware "does this look like
        a ship I would count?" probe used by the completeness rail (SCV 3c).

        Builds the direct-ship core with permissive series/phase placeholders and
        anchors it at subject start (no sha prefix — these are bare subjects). A
        bookkeeping subject, and a `vX.Y.Z:` release-bundle subject, are never a
        direct ship, so both are excluded first. This is a HEURISTIC recognizer
        (it does not know the repo's real series ids), used only to flag a
        declared-but-mismatched grammar — never on the hot verify path, which
        always knows the concrete series/phase it is checking.

        The series placeholder admits **multi-word, hyphenated** slugs (`[A-Za-z]
        [\\w .-]*[A-Za-z0-9]`), because real hosts name plans that way —
        `hardware-thing`, `blktrace auto-install`, `SGLang charts`. The original
        `[A-Za-z][A-Za-z0-9]*` matched none of these, so the rail could not even
        SEE a repo's dominant `<slug> Phase <N>:` ships, found "nothing
        ship-shaped to judge against", and stayed silent on a real mismatch (the
        F8 false all-clear). The phase placeholder admits the `Phase N` / `P N`
        keyword form the same hosts use, plus compound tokens (`P1+P2`, `3b.2`).
        """
        s = (subject or "").strip()
        if not s or self.bookkeeping_subject_re().match(s):
            return False
        # A `vX.Y[.Z]:` release-cut bundles many phases into one free-form line;
        # it is NOT a direct phase-ship attribution (the verify path treats it as
        # the weak release-prefix rung, footprint-guarded). Counting it as
        # "ship-shaped" let the rail cite a release commit as the repo's ship and
        # masked the real convention — exclude it here so the rail judges against
        # genuine direct ships only. Two- AND three-component versions occur in
        # the wild (`v25.4:` and `v0.378.0:`), so match a looser anchor than the
        # strict 3-part `_RELEASE_VERSION_ANCHOR`.
        if re.match(r"^v\d+(?:\.\d+)+:", s):
            return False
        # Permissive placeholders. Series: an UPPER/lower-led run that may carry
        # internal spaces, hyphens, and dots (a multi-word plan slug), ending on
        # an alnum so it doesn't swallow the trailing separator. Phase: the
        # `Phase N` / `P N` keyword form OR a bare id — but in EITHER case the
        # phase token must CONTAIN A DIGIT (a ship references a *numbered* phase:
        # `Phase 1`, `AUTH2`, `P1+P2`, `3b.2`). Requiring the digit is what
        # separates a real ship-shape from an ordinary `chore: refactor` /
        # `fix: typo` commit, which share the bare `<word>: <word>` shape but name
        # no phase. Without it the heuristic flags every conventional-commit
        # subject as ship-shaped (the rail's original over-match).
        # Each placeholder is a SELF-CONTAINED group: `direct_ship_core`
        # interpolates them into `{series}:?\s+{phase}` without adding its own
        # parentheses, so a bare top-level `|` here would re-associate the whole
        # alternation (making `{series-alt-1}` match alone, with no phase) — the
        # `chore`/`Merge branch` false-positive. Wrap both in `(?:…)`.
        series_ph = r"(?:[A-Za-z][\w .\-]*[A-Za-z0-9]|[A-Za-z])"
        phase_ph = (
            r"(?:(?:Phase|P)\s*\d+[A-Za-z0-9.\-+]*"     # `Phase 1`, `P3.4`, `P1+P2`
            r"|[A-Za-z]*\d[A-Za-z0-9.\-+]*)"            # `AUTH2`, `3b.2`, `RS4` — has a digit
        )
        core = self.direct_ship_core(series_ph, phase_ph)
        if re.match(rf"^{core}", s, re.IGNORECASE):
            return True
        # The trailer probe (docs/289): a `(<PLAN> <PHASE>)` tail. The series
        # placeholder here is WIDER than the start-anchored one — a trailer
        # names the plan as registered, and plan ids are commonly digit-led
        # (`docs/286` → `286` after the dir prefix), a shape the start-anchored
        # placeholder deliberately rejects (it would over-match prose there).
        # Inside the parens + end anchor the digit-led form is safe. The phase
        # placeholder is unchanged: a digit is still what separates a phase
        # stamp from prose (`(docs/286 follow-up)` is a reference, not a ship).
        trailer_series_ph = r"(?:[A-Za-z0-9][\w .\-]*[A-Za-z0-9]|[A-Za-z0-9])"
        trailer = self.trailer_ship_core(trailer_series_ph, phase_ph)
        if trailer and re.search(trailer, s, re.IGNORECASE):
            return True
        if getattr(self, "issue_leaf_stamp", False):
            # A ship stamped as a `(#<issue>)` cite + a `(<ns> <leaf>)` named
            # trailer (fak #3132) — Conventional-Commits repos carrying the
            # unit of work split across two parens: the GH issue number is the
            # phase, the trailer's leaf token the plan (`… (#2911) (fak
            # tools)` ↔ `dos verify tools 2911`). Both operands are required:
            # the bare cite alone is a reference, the bare named trailer alone
            # already has its own recurrence-guarded surface
            # (`named_trailer_ship_subjects`).
            if re.search(r"\(\s*#\d+\b", s) and _NAMED_TRAILER_RE.search(s):
                return True
        return False


def ship_shaped_under_generic(subject: str) -> bool:
    """True iff `subject` looks like a ship under the most permissive (generic)
    grammar — used by the completeness rail to decide "this commit is a SHIP that
    SOME convention would recognize," independent of the active one.

    Deliberately broad: an optional path prefix, then `<SERIES><sep><PHASE>:` in
    either the spaced or glued form — OR a `(<PLAN> <PHASE>)` trailer at the end
    of the subject (docs/289: the probe runs with `trailer_stamp` ON, because
    this predicate's contract is "would SOME convention recognize it?", and the
    trailer convention exists to be declared — a Conventional-Commits repo whose
    stamps live in trailers should hear "reconcile [stamp]", not "none of your
    commits name a unit of work"). Excludes bulk snapshots (the universal
    bookkeeping guard) so a `working-dir snapshot:` is never counted. This is the
    "is this even a ship subject?" predicate; the active convention's
    `recognizes_direct_ship` is the "would MY grammar catch it?" predicate. A
    subject that is ship-shaped-generic but NOT recognized-by-active is the
    declared-grammar-misses-its-own-commits finding (SCV 3c).
    """
    return _GENERIC_TRAILER_PROBE.recognizes_direct_ship(subject)


def convention_coverage_finding(
    convention: StampConvention, subjects: list[str], *, declared: bool
) -> str | None:
    """The SCV 3c completeness finding, or None when the grammar looks fine.

    The rail (HACKING.md's `--check` invariant, stamp analogue of "a reason
    emitted but not in the registry"): if a workspace DECLARED a `[stamp]` table
    but its active convention recognizes NONE of the repo's own recent
    ship-shaped commits, the declared grammar almost certainly mismatches how the
    repo actually stamps ships — so `verify` will silently resolve `via none` for
    real ships. Surface that.

    Returns a one-line finding string when:
      * ``declared`` is True (an inherited default on a foreign repo is a
        different, expected situation — only a *declared* grammar is the host's
        own claim to check), AND
      * at least one `subject` is ship-shaped under the generic grammar, AND
      * the active ``convention`` recognizes NONE of those ship-shaped subjects.

    Returns None otherwise (no declaration, no ship-shaped commits to judge
    against, or the convention recognizes ≥1 — the healthy case). Pure: takes the
    subjects list so it is unit-testable without git.
    """
    if not declared:
        return None
    ship_shaped = [s for s in subjects if ship_shaped_under_generic(s)]
    if not ship_shaped:
        return None  # nothing ship-shaped to judge the grammar against
    if any(convention.recognizes_direct_ship(s) for s in ship_shaped):
        return None  # the declared grammar catches at least one real ship — fine
    sample = ship_shaped[0]
    dirs = ", ".join(convention.subject_dirs) or "(none — generic)"
    return (
        f"declared [stamp] (subject_dirs={dirs}) recognizes none of this repo's "
        f"{len(ship_shaped)} recent ship-shaped commit(s) — e.g. {sample!r}. "
        f"verify will resolve `via none` for real ships; reconcile [stamp] to how "
        f"this repo stamps (see `dos doctor` / HACKING.md)."
    )


# The Conventional-Commits header set (the Angular/CC canon). A CLOSED type set on
# purpose: a bare `tools: add x` is a generic `<series>:` ship candidate, NOT a
# Conventional-Commits subject — keeping the set closed is what stops the recipe
# below from misreading one as the other (the same discriminator `recognizes_
# direct_ship` uses: a ship names a NUMBERED phase, an ordinary `type: …` does not).
_CONVENTIONAL_COMMIT_TYPES = (
    "build", "chore", "ci", "docs", "feat", "fix",
    "perf", "refactor", "revert", "style", "test",
)
_CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(?:" + "|".join(_CONVENTIONAL_COMMIT_TYPES) + r")(?:\([^)]*\))?!?:\s",
    re.IGNORECASE,
)


def is_conventional_commit(subject: str) -> bool:
    """True iff `subject` is a Conventional-Commits header (`type(scope): …`,
    `type!: …`, `type: …`) for the canonical type set.

    Pure. A Conventional-Commits repo carries its ship-stamp (if any) in a TRAILER
    at the end of the subject — the one place a start-anchored ship grammar can
    never see (docs/289) — so this predicate gates the actionable trailer recipe in
    `conventional_commits_unstamped_finding`.
    """
    return bool(_CONVENTIONAL_COMMIT_RE.match((subject or "").strip()))


def conventional_commits_unstamped_finding(
    convention: StampConvention, subjects: list[str], *, declared: bool
) -> str | None:
    """An ACTIONABLE verifiability recipe for a declared Conventional-Commits repo
    whose commits carry no ship-stamp the active grammar can bind — else None.

    The complement to `convention_coverage_finding`. That one fires when ship-SHAPED
    commits miss a declared grammar; this one fires the OTHER dead-end — a declared
    workspace whose recent commits are Conventional-Commits-shaped (so the start-
    anchored grammar sees no ship at all) and carry no `(<plan> <phase>)` trailer
    yet. Instead of the bare "no referee can check agent claims yet" cul-de-sac,
    hand back the concrete fix: declare the trailer stamp and stamp the ships.

    Fires only when (mirrors `convention_coverage_finding`'s opt-in discipline so a
    foreign CC repo is never nagged — the "never cries wolf" contract):
      * ``declared`` — only a host's OWN [stamp] declaration is audited; an inherited
        default keeps the gentle 'no referee' line,
      * at least one subject is Conventional-Commits-shaped, AND
      * the active convention recognizes NONE of the subjects as a direct ship (if it
        already binds one, the affirmative 'can check' form wins upstream).

    The wording branches on whether the trailer rung is already declared: OFF →
    "set [stamp] trailer_stamp = true and stamp ships"; ON → "stamp ship commits"
    (the config is right, only the commits lag — the early-adoption state). Returns a
    concise one-line clause sized for the verifiability headline. Pure / unit-testable.
    """
    if not declared:
        return None
    cc = [s for s in subjects if is_conventional_commit(s)]
    if not cc:
        return None
    if any(convention.recognizes_direct_ship(s) for s in subjects):
        return None  # something already binds — the affirmative form wins
    head = f"{len(cc)} of your last {len(subjects)} commits are Conventional-Commits-shaped"
    if convention.trailer_stamp:
        return (
            f"{head} but none carry a `(<plan> <phase>)` ship trailer yet — stamp ship "
            f"commits so `dos verify` can bind them"
        )
    return (
        f"{head} but carry no ship-stamp — set [stamp] trailer_stamp = true and end ship "
        f"subjects with a `(<plan> <phase>)` trailer so `dos verify` can bind them"
    )


# A leaf-NAME `(<plan> <phase>)` trailer — the stamp a repo uses when it verifies
# per NAMED unit (a package / leaf), e.g. `fix(gateway): … (fak gateway)`, rather
# than a NUMBERED phase. `recognizes_direct_ship`'s trailer probe REQUIRES a digit
# in the phase token (so `(edge case)` prose never reads as a ship), so it
# structurally cannot see these — yet `dos verify fak gateway` binds them literally.
# The phase token here is letters-only (NO digit), keeping this set DISJOINT from the
# digit-phase forms `recognizes_direct_ship` already counts (a subject is in at most
# one), so a caller may union the two without double-counting.
_NAMED_TRAILER_RE = re.compile(
    r"\(\s*(?:refs\s+)?([A-Za-z0-9][\w./\-]*):?\s+"   # the <plan> token
    r"([A-Za-z][A-Za-z._\-]*)"                        # the <phase> NAME (no digit)
    r"(?:\s*[,;]?\s*(?:fixes|closes|refs)?\s*#\d+)*\s*\)\s*$",
    re.IGNORECASE,
)


def named_trailer_ship_subjects(
    convention: StampConvention, subjects: list[str]
) -> set[str]:
    """The subjects carrying a RECURRING leaf-name `(<plan> <phase>)` ship trailer.

    A non-numbered stamp the digit-requiring generic heuristic can't see, but
    `dos verify` binds literally (e.g. `fix(gateway): … (fak gateway)`). A trailer
    counts only when its <plan> token RECURS across ≥2 subjects — the discriminator
    that separates a real per-leaf stamping convention (`(fak gateway)`,
    `(fak tools)` — 'fak' recurs) from a one-off prose aside (`(edge case)`, whose
    first word does not). Bookkeeping subjects are excluded. Empty unless the
    convention opted into `trailer_stamp` (the same gate as the trailer rung), so a
    repo that declared no trailer stamp is never affected. Returns the SUBJECT SET
    (not a count) so a caller can union it with `recognizes_direct_ship` hits
    without double-counting. Pure / unit-testable.
    """
    if not convention.trailer_stamp:
        return set()
    from collections import Counter

    bookkeeping = convention.bookkeeping_subject_re()
    hits: list[tuple[str, str]] = []
    for s in subjects:
        t = (s or "").strip()
        if not t or bookkeeping.match(t):
            continue
        m = _NAMED_TRAILER_RE.search(t)
        if m:
            hits.append((m.group(1).lower(), t))
    plan_counts = Counter(p for p, _ in hits)
    return {t for p, t in hits if plan_counts[p] >= 2}


# ---------------------------------------------------------------------------
# The reference userland app's convention — the current hardcoded grammar, lifted
# VERBATIM from `phase_shipped.py`'s module constants so the existing
# kernel suite is byte-for-byte unchanged. This is a plain default the kernel
# falls back to (the `stamp` field on SubstrateConfig defaults to it), NOT an
# import from `drivers.job` — same pattern as the `main`/`global` lane default.
#
# Provenance of each tuple (the constants this replaces, all in phase_shipped):
#   subject_dirs            <- _DIRECT_PREFIX  = (docs|go|agents|job_search|scripts)
#   summary_bundle_prefixes <- _SUMMARY_BUNDLE_PREFIXES = ("docs/HYG:",)
#   bookkeeping_prefixes    <- _BOOKKEEPING_SUBJECT_PREFIXES
# ---------------------------------------------------------------------------
#   code_dirs               <- _REPO_PATH_RE allowlist
#                              (agents|job_search|go|scripts|templates|config|docs|tests)
#   infra_basenames         <- the reference app's OWN hub beyond the universal set
#                              (_SHARED_INFRA_BASENAMES minus the universal ones)
#   infra_doc_basenames     <- _SHARED_INFRA_DOC_BASENAMES (the named diagram/ref hubs)
JOB_STAMP_CONVENTION = StampConvention(
    subject_dirs=("docs", "go", "agents", "job_search", "scripts"),
    summary_bundle_prefixes=("docs/HYG:",),
    bookkeeping_prefixes=(
        "docs/_plans:",
        "docs/fanout:",
        "docs/dispatch:",
        "docs/dispatch-loop:",
        "docs/_soaks:",
    ),
    style="grep",
    # The file-path backstop allowlist (`_REPO_PATH_RE`), lifted verbatim so the
    # reference app's artefact rung is byte-for-byte unchanged.
    code_dirs=(
        "agents", "job_search", "go", "scripts",
        "templates", "config", "docs", "tests",
    ),
    # The reference app's OWN hub file beyond the universal set. `config.py` etc.
    # are now universal (`_UNIVERSAL_INFRA_BASENAMES`); `fanout_state.py` is the
    # one host-specific addition. The resolved set (`infra_basename_set()`) is the
    # original `_SHARED_INFRA_BASENAMES` exactly.
    infra_basenames=("fanout_state.py",),
    # The reference app's named bulk-regenerated doc hubs (`_SHARED_INFRA_DOC_BASENAMES`).
    infra_doc_basenames=(
        "00_subsystems-reference.md", "architecture.mmd", "data-flow.mmd",
        "pipeline-flow.mmd", "state-machine.mmd", "scoring-model.mmd",
        "model-tiering.mmd",
    ),
    # The reference app's soak/observation progress vocabulary, lifted verbatim
    # from `phase_shipped._PROGRESS_MARKER_WORDS` so the demotion is byte-for-byte
    # unchanged for the reference app. A `<PHASE> <marker>` subject is incremental
    # progress on a multi-step phase, not its ship.
    progress_markers=(
        "week-1", "week-2", "week-3", "week-4",
        "day-1", "day-2", "day-3", "day-4", "day-5", "day-6", "day-7",
        "audit", "re-audit", "baseline", "re-baseline", "rebaseline",
        "read", "reading", "snapshot", "obs", "observation", "measurement",
        "progress", "soak", "wip", "partial",
        "§why", "todo",
    ),
    # The reference app uses hyphen-suffixed sub-phase ids (`RS4-port`) and wants
    # the parent-phase fallback; a generic repo does not (it false-resolves a
    # fabricated `P2-CLI` against a real `P2`). Declared on, off-by-default.
    sub_phase_parent_fallback=True,
)


# ---------------------------------------------------------------------------
# The generic convention — what an EXTERNAL repo's ship subjects look like: a
# bare `<SERIES>: <PHASE>` / `<SERIES><PHASE>` with no dir prefix and no
# host-specific bundle/bookkeeping prefixes. Only the universal release anchor
# (`vX.Y.Z:`) and the universal bulk-snapshot guard apply. This is the value a
# foreign workspace gets by default once it has no `[stamp]` table of its own
# beyond `style="grep"` — and the value `test_verify_no_plan` exercises to prove
# `verify` is domain-free.
# ---------------------------------------------------------------------------
GENERIC_STAMP_CONVENTION = StampConvention(
    subject_dirs=(),
    summary_bundle_prefixes=(),
    bookkeeping_prefixes=(),
    style="grep",
)

# The breadth-probe convention behind `ship_shaped_under_generic` (docs/289):
# generic, with the trailer rung ON. NOT a default any workspace inherits —
# `verify` still recognizes trailers only where `[stamp] trailer_stamp = true`
# is declared. This probe only widens what the completeness rail / verifiability
# headline can SEE as ship-shaped, so a trailer-stamping repo is told to declare
# the flag instead of being told it has nothing checkable.
_GENERIC_TRAILER_PROBE = StampConvention(
    style="grep", trailer_stamp=True, issue_leaf_stamp=True
)


# ---------------------------------------------------------------------------
# The declarative on-ramp: read a `[stamp]` table out of a workspace's dos.toml.
#
# `dos init` already scaffolds `[stamp] style="grep"`; these turn that table into
# a `StampConvention`. Mirrors `reasons.specs_from_table` / `reasons.load_from_toml`
# exactly: a present table OVERRIDES the base (a host declaring `subject_dirs`
# means "these are MY dirs", not "these plus job's"); absent/empty degrades to the
# base; present-but-malformed raises (surfaced, not swallowed).
#
# TOML shape (every key optional; the omitted ones fall back to `base`'s value):
#
#     [stamp]
#     style        = "grep"
#     subject_dirs = ["src", "lib", "app"]      # this repo's top-level dirs
#     summary_bundle_prefixes = ["docs/HYG:"]   # extra standalone-summary prefixes
#     bookkeeping_prefixes    = ["docs/_plans:"]# subjects that NAME but don't ship
#     trailer_stamp = true                      # also ship via a `(<PLAN> <PHASE>)`
#                                               # end-of-subject trailer (docs/289)
# ---------------------------------------------------------------------------


def _str_tuple(value: object, key: str) -> tuple[str, ...]:
    """Coerce a TOML value to a tuple of strings, or raise naming the bad key.

    Accepts a single string (wrapped) or a list of strings. Anything else — a
    number, a nested table, a list with a non-string element — is a host mistake
    worth surfacing loudly at load (the same posture `reasons.specs_from_table`
    takes on a bad category).
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"[stamp].{key} must be a list of strings; got a "
                    f"{type(item).__name__} element ({item!r})"
                )
            out.append(item)
        return tuple(out)
    raise ValueError(
        f"[stamp].{key} must be a string or list of strings, "
        f"got {type(value).__name__}"
    )


def convention_from_table(
    table: dict, *, base: StampConvention = JOB_STAMP_CONVENTION
) -> StampConvention:
    """Build a `StampConvention` from a parsed `[stamp]` TOML table.

    Pure (no I/O). Each field the table names overrides ``base``; omitted fields
    inherit ``base``'s value. An unknown key raises (a typo'd field is a host
    mistake worth surfacing, mirroring `PathLayout.with_overrides`' posture in
    the sibling WCR plan). A malformed value (non-string-list) raises via
    `_str_tuple`.

    Note the override (not merge) semantics on the list fields: a host that
    declares ``subject_dirs = ["src"]`` gets exactly ``["src"]``, NOT
    ``["src"] + job's``. Declaring your dirs means declaring your dirs.
    """
    if not isinstance(table, dict):
        raise ValueError(f"[stamp] must be a table, got {type(table).__name__}")
    known = {
        "style", "subject_dirs", "summary_bundle_prefixes", "bookkeeping_prefixes",
        "code_dirs", "infra_basenames", "infra_doc_basenames",
        "progress_markers", "sub_phase_parent_fallback", "trailer_stamp",
        "issue_leaf_stamp",
    }
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[stamp] has unknown key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)}"
        )
    style = base.style
    if "style" in table:
        if not isinstance(table["style"], str):
            raise ValueError(
                f"[stamp].style must be a string, got {type(table['style']).__name__}"
            )
        style = table["style"]
    sub_phase = base.sub_phase_parent_fallback
    if "sub_phase_parent_fallback" in table:
        if not isinstance(table["sub_phase_parent_fallback"], bool):
            raise ValueError(
                "[stamp].sub_phase_parent_fallback must be a boolean, got "
                f"{type(table['sub_phase_parent_fallback']).__name__}"
            )
        sub_phase = table["sub_phase_parent_fallback"]
    trailer = base.trailer_stamp
    if "trailer_stamp" in table:
        if not isinstance(table["trailer_stamp"], bool):
            raise ValueError(
                "[stamp].trailer_stamp must be a boolean, got "
                f"{type(table['trailer_stamp']).__name__}"
            )
        trailer = table["trailer_stamp"]
    issue_leaf = base.issue_leaf_stamp
    if "issue_leaf_stamp" in table:
        if not isinstance(table["issue_leaf_stamp"], bool):
            raise ValueError(
                "[stamp].issue_leaf_stamp must be a boolean, got "
                f"{type(table['issue_leaf_stamp']).__name__}"
            )
        issue_leaf = table["issue_leaf_stamp"]
    return StampConvention(
        subject_dirs=(
            _str_tuple(table["subject_dirs"], "subject_dirs")
            if "subject_dirs" in table
            else base.subject_dirs
        ),
        summary_bundle_prefixes=(
            _str_tuple(table["summary_bundle_prefixes"], "summary_bundle_prefixes")
            if "summary_bundle_prefixes" in table
            else base.summary_bundle_prefixes
        ),
        bookkeeping_prefixes=(
            _str_tuple(table["bookkeeping_prefixes"], "bookkeeping_prefixes")
            if "bookkeeping_prefixes" in table
            else base.bookkeeping_prefixes
        ),
        style=style,
        code_dirs=(
            _str_tuple(table["code_dirs"], "code_dirs")
            if "code_dirs" in table
            else base.code_dirs
        ),
        infra_basenames=(
            _str_tuple(table["infra_basenames"], "infra_basenames")
            if "infra_basenames" in table
            else base.infra_basenames
        ),
        infra_doc_basenames=(
            _str_tuple(table["infra_doc_basenames"], "infra_doc_basenames")
            if "infra_doc_basenames" in table
            else base.infra_doc_basenames
        ),
        progress_markers=(
            _str_tuple(table["progress_markers"], "progress_markers")
            if "progress_markers" in table
            else base.progress_markers
        ),
        sub_phase_parent_fallback=sub_phase,
        trailer_stamp=trailer,
        issue_leaf_stamp=issue_leaf,
    )


def load_from_toml(
    path: Path | str, *, base: StampConvention = JOB_STAMP_CONVENTION
) -> StampConvention:
    """Build a `StampConvention` from a `dos.toml`'s `[stamp]` table.

    Returns ``base`` unchanged when the file is absent, has no `[stamp]` table, or
    `tomllib` is unavailable (Python < 3.11 with no `tomli`) — the declarative
    path is purely additive, so a missing/empty config degrades to the supplied
    base, never an error. A *present but malformed* `[stamp]` table raises
    (`convention_from_table`), because a host that declared its grammar wrong
    wants that surfaced, not swallowed. Mirrors `reasons.load_from_toml` exactly.
    """
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("stamp")
    if not isinstance(table, dict) or not table:
        return base
    return convention_from_table(table, base=base)
