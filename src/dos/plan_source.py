"""The plan-source seam — pluggable discovery of the (plan, phase) rows a plan view audits.

Why this exists
===============

DOS deliberately holds **no plan schema** (CLAUDE.md: "Phased-plan concepts are NOT
in this package"). `verify()` takes ``(plan, phase)`` positionally and answers from
git alone when no plan exists. So a screen that wants to show "the shape of the work
and how far it has shipped" cannot read a plan registry the kernel doesn't believe
in — it must instead ask a *declared* source for a flat list of candidate rows, then
let the **oracle** rule on each one's ship status. The plan is a row source; the truth
is `oracle.is_shipped`. That inversion is the whole point: a plan-status view built on
the plan's own self-report would be a self-narrating worker; one built on the oracle's
verdict is the kernel doing its job at the plan altitude.

This module is that row source — the **seam**, not a schema. It is the exact analogue
of `dos.judges` (the JUDGE-rung seam): a domain-neutral Protocol + a frozen value type
+ a fail-safe runner + a by-name resolver over an entry-point group + a single built-in
that ships in the kernel. Every host-specific bit (where plans live, what a phase
heading looks like, what a ship stamp reads as) is either CONFIG DATA the built-in
reads (`config.paths.plans_glob`) or a host's own `dos.plan_sources` plugin — never a
hardcoded `docs/_plans` literal in a kernel module.

The unit a source yields is a `PlanRow` — a domain-neutral
``{plan, phase, doc_path, claimed_status}`` quadruple. ``claimed_status`` is the
**plan's self-report** (the stamp it carries / "open" / "blocked") — the part DOS does
NOT believe; it is shown only to contrast against the oracle's verdict (the
believed-vs-adjudicated divergence the plan view is built around). A source NEVER
returns a ship verdict — that is `oracle.is_shipped`'s job alone, attached downstream.

Purity & layering
==================

Pure kernel, exactly like `judges`: a Protocol, one frozen value type, a built-in that
harvests markdown, and resolver/runner helpers. The built-in's markdown read is
**boundary I/O gathered when the source runs** — there is no verdict here to keep pure
(a row list is data, not an adjudication), but the discipline that matters carries
over: the source names no host (it reads `config.paths.plans_glob`, declared per
workspace), and **fail-to-empty** — `run_plan_source` converts any raise / bad return
into ``[]``, never a partial or fabricated row, so a broken source degrades the plan
view to its no-plan floor rather than inventing work. Entry-point discovery (the one
bit of registry I/O) happens at the call boundary in `active_plan_sources`, exactly as
`active_judges` / `active_predicates` do.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# The row a source yields — domain-neutral, frozen, carries NO ship verdict.
# ---------------------------------------------------------------------------


# The closed self-report vocabulary. A source reports what the PLAN claims about a
# phase — never a verified fact. `str`-valued so it round-trips through `--json`
# without a lookup table (the `gate_classify.Verdict` idiom). The oracle's verdict
# is a SEPARATE axis attached downstream; these never name "shipped-as-fact".
CLAIMED_SHIPPED = "shipped"      # the plan stamps this phase as done (a `· SHIPPED` mark)
CLAIMED_BLOCKED = "blocked"      # the plan marks it gated / soaking / awaiting
CLAIMED_OPEN = "open"            # the plan lists it but claims no status
CLAIMED_UNKNOWN = ""             # the source could not read a status off the plan

_CLAIMED_VALUES = frozenset({CLAIMED_SHIPPED, CLAIMED_BLOCKED, CLAIMED_OPEN, CLAIMED_UNKNOWN})


@dataclass(frozen=True)
class PlanRow:
    """One candidate ``(plan, phase)`` the plan view will ask the oracle about.

    Deliberately NOT a plan-schema node — it is the *flat* shape a plan view needs:
    the positional ``(plan, phase)`` `oracle.is_shipped` takes, the ``doc_path`` the
    row was harvested from (for drill-in / the oracle's doc-aware cross-check), and the
    ``claimed_status`` — the plan's OWN self-report (`shipped`/`blocked`/`open`), the
    narration DOS distrusts and shows only to contrast against the oracle. ``lane`` is
    an OPTIONAL hint a source may carry when the plan names the phase's lane; the plan
    view uses it to join a row to a live lease / decision, and it is "" when unknown
    (the join then falls back to lane-name matching downstream).

    A `PlanRow` carries no ship verdict on purpose: the source's job is to enumerate
    candidates honestly, not to rule on them. The ruling is the oracle's, attached in
    `plan_board`.
    """

    plan: str
    phase: str
    doc_path: str = ""
    claimed_status: str = CLAIMED_UNKNOWN
    lane: str = ""

    def to_dict(self) -> dict:
        return {
            "plan": self.plan,
            "phase": self.phase,
            "doc_path": self.doc_path,
            "claimed_status": self.claimed_status,
            "lane": self.lane,
        }


@runtime_checkable
class PlanSource(Protocol):
    """The contract a host implements to tell a plan view where its phases live.

    ``name`` is the token a CLI flag selects and `dos doctor` could list. ``rows`` is
    handed the active ``config`` (read-only — it reads `config.paths.plans_glob` and
    the workspace root; the type gives it nothing to mutate) and returns the candidate
    `PlanRow`s, in a deterministic order (declaration / file order — the plan view
    renders them as given).

    A source MAY do I/O *inside* ``rows`` (glob the workspace, read markdown) — it is
    the boundary read that turns "where are the plans" into data. The discipline that
    keeps it honest is fail-to-empty (enforced by `run_plan_source`, not by trusting
    the source) and naming no host (the built-in reads the declared glob, never a
    literal), NOT purity — a row list is data, not an adjudication.
    """

    name: str

    def rows(self, config: object) -> list[PlanRow]:
        ...


# ---------------------------------------------------------------------------
# The built-in markdown source — harvests `### N. PLAN PHASE` headings + a
# `· SHIPPED` / `SHIPPED` stamp from the files config.paths.plans_glob names.
# ---------------------------------------------------------------------------

# A numbered packet/plan heading: `### 1. IF IF4.1 — title…` or `## 3. AUTH P2: …`.
# The first token after the section number is the plan id (starts with a letter), the
# second is the phase id (the exact positional string the oracle takes). This is the
# SAME unambiguous shape `timeline._parse_packet_picks` harvests — lifted here as the
# generic default so a plan view and a dispatch timeline read the same heading grammar.
#
# Two guards keep the generic harvest CONSERVATIVE — it must under-harvest a foreign
# convention (→ "(no plans)" + the git floor, the honest degrade) rather than mine prose
# for phantom phases (the live-repo failure mode: `### Why never-stall` →
# `(Why, never-stall)`):
#   * the trailing ``[—–\-:]`` separator is REQUIRED — a real plan heading titles its
#     phase (`### 1. IF IF4.1 — split`); a plain numbered section header
#     (`## 2. Next items`) has no separator after its second word.
#   * the phase token must contain a DIGIT **and** a LETTER (`_looks_like_phase_id`) — a
#     real phase id (`IF4.1`, `P2`, `1a`) carries both a series letter and an ordinal; a
#     prose word (`items`, `Built`, `TOML`) has no digit, and a bare ordinal (the `2` in a
#     prose `### 1. Phase 2 of 3 — done`) has no letter. Both are rejected — the single
#     most effective false-positive cut.
# A repo whose plan docs use another shape (DOS's OWN `### Phase N:` / `- **1a.**` design
# docs do) ships a `dos.plan_sources` plugin — the kernel default does not guess.
_HEADING_RE = re.compile(
    r"^#{2,4}\s+\d+\.\s+([A-Za-z][A-Za-z0-9_\-]*)\s+([A-Za-z0-9][A-Za-z0-9_\-./']*)\s*[—–\-:]",
)

# A bullet sub-phase row under a numbered heading: `- **IF4.2 — …`. The phase id is the
# bolded leading token; there is no plan id on the bullet, so it inherits the enclosing
# NUMBERED heading's plan (never a prose `###`). Like the heading, the token must look
# like a phase id (carry a digit) — without that guard a bolded design principle
# (`- **Rendering is downstream…`) harvests as a phantom phase (the live-repo failure).
_BULLET_RE = re.compile(
    r"^\s*-\s+\*\*([A-Za-z0-9][A-Za-z0-9_\-./']*)\s*(?:[—\-–:]|\*\*)",
)


def _looks_like_phase_id(token: str) -> bool:
    """True iff ``token`` has the generic phase-id shape: a LETTER and a DIGIT.

    Every real phase id carries both — a series letter and an ordinal: `IF4.1`, `P2`,
    `AUTH4`, `RS4`, `1a`, `MG3'-1`. Requiring both is the conservative false-positive cut
    that keeps the generic default honest against prose the loose heading/bullet regex
    would otherwise mine:

      * a prose WORD (`items`, `Built`, `TOML`, `release`, `downstream`) has a letter but
        no digit → rejected;
      * a BARE ORDINAL (`2`, `3`) — the second token of a prose `### 1. Phase 2 of 3 —
        done` heading, which would mis-harvest as the phantom phase `(Phase, 2)` — has a
        digit but no letter → rejected.

    The cost is the bare-ordinal `### Phase 6:` plan dialect (DOS's own design docs): its
    phase token `6` is digit-only, so it is NOT harvested by the default. That is the
    documented tradeoff — under-harvest a digit-less / bare-ordinal convention (ship a
    `dos.plan_sources` plugin) rather than mine prose for phantom work."""
    return any(c.isalpha() for c in token) and any(c.isdigit() for c in token)

# A heading section's own SHIPPED stamp lives in the lines under it until the next
# heading. We read the claimed status off the section text: a `SHIPPED` token (the
# universal stamp word `phase_shipped` keys on) ⇒ claimed shipped; a soak/gate/await
# word ⇒ claimed blocked; otherwise open. These are CLAIMED-only reads — the plan's
# narration, never a verified fact.
_STAMP_SHIPPED_RE = re.compile(r"\bSHIPPED\b")
_STAMP_BLOCKED_RE = re.compile(r"\b(?:SOAK|SOAKING|BLOCKED|AWAITING|GATED|DEFERRED)\b", re.IGNORECASE)


def _claimed_status_for(section_text: str) -> str:
    """Read the plan's self-reported status off a phase's section text.

    Pure. A `SHIPPED` token wins (the plan claims done); else a soak/blocked/await
    word ⇒ blocked; else open. This is the plan's NARRATION — the part the plan view
    distrusts and shows only to contrast against the oracle. An empty section ⇒ open
    (the plan lists the phase but claims nothing).
    """
    if _STAMP_SHIPPED_RE.search(section_text):
        return CLAIMED_SHIPPED
    if _STAMP_BLOCKED_RE.search(section_text):
        return CLAIMED_BLOCKED
    return CLAIMED_OPEN


def _harvest_markdown(text: str, doc_path: str) -> list[PlanRow]:
    """Parse one plan-doc's text into ordered PlanRows. Pure, no I/O.

    Two recognised shapes, both coded GENERICALLY (no host directory or series literal)
    and both gated on `_looks_like_phase_id` so prose is never mined for phantom phases:

      * a numbered ``### N. PLAN PHASE — …`` heading yields a row (plan = first token,
        phase = second), claimed status read from the lines under it (until the next
        heading). This sets the enclosing plan for any bullets that follow.
      * a bolded ``- **PHASE — …`` bullet INHERITS the most-recent numbered heading's
        plan, yielding a sub-phase row, its claimed status read from the bullet's line.

    A bullet with no preceding numbered heading is dropped (there is no honest plan id to
    give it). De-duped on ``(plan, phase)`` preserving first-seen order. A doc with no
    recognised heading yields ``[]`` — the conservative degrade (DOS's own `### Phase N:`
    design-doc dialect lands here, and that is correct: it wants a `dos.plan_sources`
    plugin, not a guess).
    """
    lines = text.splitlines()
    rows: list[PlanRow] = []
    seen: set[tuple[str, str]] = set()
    cur_plan = ""  # the plan id from the most-recent NUMBERED heading (bullets inherit it)

    # Pre-compute each heading line's index so a section's body is the slice up to the
    # next heading — used to read a `### N. PLAN PHASE` row's claimed status.
    heading_idx = [i for i, ln in enumerate(lines) if re.match(r"^#{2,4}\s+", ln)]
    next_heading_after = {}
    for pos, idx in enumerate(heading_idx):
        nxt = heading_idx[pos + 1] if pos + 1 < len(heading_idx) else len(lines)
        next_heading_after[idx] = nxt

    def _add(plan: str, phase: str, claimed: str) -> None:
        key = (plan, phase)
        if not plan or not phase or key in seen or not _looks_like_phase_id(phase):
            return
        seen.add(key)
        rows.append(PlanRow(plan=plan, phase=phase, doc_path=doc_path, claimed_status=claimed))

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            plan_tok, phase = m.group(1), m.group(2)
            # Adopt the heading's first token as the enclosing plan ONLY when the heading
            # itself harvested a real phase (its second token passed `_looks_like_phase_id`).
            # A PROSE numbered heading (`### 1. The rationale — why`, second token `why` →
            # no digit) must NOT scope the bullets below it: leaving `cur_plan` set to a
            # prose word ("The") would let a following digit-bearing bullet inherit it as a
            # phantom plan id (`(The, v2.0)`). Clearing it is the conservative degrade — the
            # bullet is dropped (no honest plan), same under-harvest posture as the phase
            # digit-guard. (Cost: a `### 1. IF overview` heading whose own second token is
            # prose no longer scopes its IF bullets — ship a `dos.plan_sources` plugin for
            # that dialect; the default does not guess a plan id off prose.)
            if _looks_like_phase_id(phase):
                cur_plan = plan_tok
                body = "\n".join(lines[i + 1 : next_heading_after.get(i, i + 1)])
                _add(cur_plan, phase, _claimed_status_for(line + "\n" + body))
            else:
                cur_plan = ""
            continue
        bm = _BULLET_RE.match(line)
        if bm and cur_plan:
            _add(cur_plan, bm.group(1), _claimed_status_for(line))
    return rows


class MarkdownPlanSource:
    """The built-in, always-available plan source: harvest the workspace's plan docs.

    Globs ``config.paths.plans_glob`` (the declared, per-workspace plan location —
    generic default ``docs/**/*-plan.md``, overridable in `dos.toml [paths]`) under the
    workspace root, parses each matched markdown file for ``### N. PLAN PHASE`` headings
    and ``- **PHASE`` bullet sub-phases, and reads each phase's CLAIMED status off its
    section. Names no host directory — the glob is data.

    The plan-source analogue of `judges.AbstainJudge` / the `text` renderer: a trusted
    fallback a plugin can never shadow (`resolve_plan_source` resolves built-ins first),
    and the honest zero of the seam — a workspace with no plugin still has a resolvable
    source. A repo with no plans (or a non-markdown plan convention with no plugin)
    yields ``[]``, which is the plan view's no-plan floor (the git-ships strip carries
    the screen, exactly as `dos top` degrades).
    """

    name = "markdown"

    def rows(self, config: object) -> list[PlanRow]:
        paths = getattr(config, "paths", None)
        if paths is None:
            return []
        root = Path(getattr(paths, "root", "."))
        glob = str(getattr(paths, "plans_glob", "") or "")
        if not glob:
            return []
        try:
            matched = sorted(root.glob(glob))
        except (OSError, ValueError):
            return []
        out: list[PlanRow] = []
        for p in matched:
            try:
                if not p.is_file():
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            out.extend(_harvest_markdown(text, rel))
        return out


def run_plan_source(source: PlanSource, config: object) -> list[PlanRow]:
    """Run one source, enforcing **fail-to-empty** + a clean, deduped row list.

    The wrapper EVERY consumer calls instead of `source.rows(...)` directly — it makes
    "a broken source degrades the plan view to its no-plan floor, never to fabricated
    or partial rows" a structural guarantee rather than a hope:

      * a source that **raises** (bad glob, unreadable tree, a bug) → ``[]``. Never
        propagates; the plan view falls to its git-ships floor.
      * a source that returns **anything that is not a list of `PlanRow`** → its
        non-`PlanRow` items are dropped (a duck-typed look-alike never reaches the
        oracle), and a non-iterable return → ``[]``.

    The asymmetry note vs `judges.run_judge`: a judge fails to ABSTAIN (punt up the
    ladder); a plan source fails to EMPTY (show no work). Both refuse to let a failure
    fabricate an outcome — a judge never auto-CLEARS, a source never auto-INVENTS a
    phase. Claimed-status values outside the closed set are normalised to UNKNOWN so a
    plugin can't smuggle a free-text status into the divergence logic downstream.
    """
    try:
        rows = source.rows(config)
    except Exception:  # fail-to-empty: a source that raises contributes nothing
        return []
    if not isinstance(rows, (list, tuple)):
        return []
    out: list[PlanRow] = []
    for r in rows:
        if not isinstance(r, PlanRow):
            continue
        if r.claimed_status not in _CLAIMED_VALUES:
            out.append(PlanRow(plan=r.plan, phase=r.phase, doc_path=r.doc_path,
                               claimed_status=CLAIMED_UNKNOWN, lane=r.lane))
        else:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Resolution — built-in first, then the `dos.plan_sources` entry-point group.
# ---------------------------------------------------------------------------

# The entry-point group a host/researcher registers a plan source under.
PLAN_SOURCE_ENTRY_POINT_GROUP = "dos.plan_sources"

# The built-in sources, resolvable by name and UNSHADOWABLE by a plugin (a plugin
# registering `markdown` cannot displace this one — built-ins resolve first). Only the
# generic markdown harvester ships in the kernel; a host's bespoke plan format is a
# plugin (the kernel has no host plan schema).
_BUILT_IN_PLAN_SOURCES: dict[str, type] = {
    MarkdownPlanSource.name: MarkdownPlanSource,
}


def _discover_entry_point_plan_sources(*, _stderr=None) -> list[tuple[str, PlanSource]]:
    """Find plan sources registered under the `dos.plan_sources` entry-point group.

    A plugin registers ``name = "pkg.module:SourceClass"`` in its
    ``[project.entry-points."dos.plan_sources"]``. We load each, instantiate it if it
    is a class, and return ``(entry_point_name, source)`` pairs sorted by name (stable,
    deterministic order). A plugin that fails to load is skipped with a one-line stderr
    note rather than crashing — the same posture `judges._discover_entry_point_judges`
    takes (a broken third-party plugin is the operator's to fix, not a kernel fault).
    """
    stderr = _stderr if _stderr is not None else sys.stderr
    out: list[tuple[str, PlanSource]] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=PLAN_SOURCE_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(PLAN_SOURCE_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
            source = obj() if isinstance(obj, type) else obj
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: plan source plugin {ep.name!r} failed to load ({e}); skipping",
                file=stderr,
            )
            continue
        out.append((ep.name, source))
    return out


def resolve_plan_source(name: str, *, _stderr=None) -> PlanSource:
    """Resolve a plan source by name: built-ins first, then `dos.plan_sources` plugins.

    Built-ins (`markdown`) resolve FIRST and cannot be shadowed by a plugin of the same
    name — the trusted-fallback guarantee, identical to `resolve_judge`. An unknown name
    fails LOUD with the known list (it never silently degrades to `markdown`, which would
    hide a typo'd selector): the caller asked for a specific source and getting a
    different one silently is exactly the unannounced substitution the kernel refuses.
    """
    if name in _BUILT_IN_PLAN_SOURCES:
        return _BUILT_IN_PLAN_SOURCES[name]()
    discovered = dict(_discover_entry_point_plan_sources(_stderr=_stderr))
    if name in discovered:
        return discovered[name]
    known = sorted(set(_BUILT_IN_PLAN_SOURCES) | set(discovered))
    raise ValueError(f"unknown plan source {name!r}; known: {', '.join(known)}")


def active_plan_sources(*, _stderr=None) -> list[tuple[str, PlanSource]]:
    """Every resolvable source as ``(name, source)`` — built-ins THEN discovered plugins.

    Does ENTRY-POINT DISCOVERY (I/O), so it is a call-boundary helper, never called
    inside a row harvest (the `active_judges` discipline)."""
    built = [(n, cls()) for n, cls in _BUILT_IN_PLAN_SOURCES.items()]
    discovered = _discover_entry_point_plan_sources(_stderr=_stderr)
    return built + discovered


def active_plan_source_names(*, _stderr=None) -> list[str]:
    """The names of every active source (built-in + discovered) — what a `dos doctor`
    listing or a `--plan-source` help text would show."""
    return [name for name, _src in active_plan_sources(_stderr=_stderr)]


# ---------------------------------------------------------------------------
# The `[plan]` data attachment (docs/293) — WHICH source a workspace reads by
# default, declared in dos.toml. The seam owns its table (the `stamp.py`
# precedent: `[stamp]` is read by stamp.load_from_toml, not scattered through
# consumers); the read happens at the plan_board.snapshot BOUNDARY, the same
# place the projection's other reads live — deliberately NOT a SubstrateConfig
# field (config.py is in the kernel's own T1 runtime set; see docs/293's
# status-note revision).
# ---------------------------------------------------------------------------


def load_plan_source_name_from_toml(
    path: "Path | str", *, base_name: str = MarkdownPlanSource.name,
) -> str:
    """Read a `dos.toml`'s `[plan]` table → the declared plan-source name.

    One key, mirroring `[overlap] policy`:

        [plan]
        source = "design-docs"   # "markdown" (built-in) or a dos.plan_sources name

    Returns ``base_name`` when the file is absent, has no `[plan]` table, names
    no ``source``, or `tomllib` is unavailable — the declarative path is purely
    additive, a workspace that declares nothing is byte-identical to today. A
    *present but malformed* table RAISES ``ValueError`` (an unknown key, a
    non-string / empty ``source``) — a host that declared its dialect wrong wants
    that surfaced, not a silent fall-through to a different harvester. Whether
    the NAME resolves is deliberately not checked here: resolution is entry-point
    I/O, done at the call boundary (`default_source`), where an unresolvable
    declared name fails to EMPTY.
    """
    p = Path(path)
    if not p.exists():
        return base_name
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("plan")
    if table is None:
        return base_name
    if not isinstance(table, dict):
        raise ValueError(f"[plan] must be a table, got {type(table).__name__}")
    allowed = {"source"}
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(
            f"unknown [plan] key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )
    if "source" not in table:
        return base_name
    raw = table["source"]
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"[plan] source must be a non-empty string, got {raw!r}")
    return raw.strip()


def declared_source_name(config: object, *, _stderr=None) -> str:
    """The plan-source name THIS workspace declares, warn-and-fall-back on a fault.

    Boundary I/O (reads ``<root>/dos.toml``), so it is called from `default_source`
    / a CLI surface, never inside a harvest. A malformed `[plan]` table (including
    an unparseable dos.toml — `TOMLDecodeError` is a `ValueError`) gets the
    config-layer posture: one stderr line, then the built-in default — a broken
    declaration must not crash a read-only board. An unreadable file degrades
    silently (the absent-file shape).
    """
    root = getattr(config, "root", None)
    if root is None:
        root = getattr(getattr(config, "paths", None), "root", None)
    if root is None:
        return MarkdownPlanSource.name
    try:
        return load_plan_source_name_from_toml(Path(root) / "dos.toml")
    except ValueError as e:
        stderr = _stderr if _stderr is not None else sys.stderr
        print(f"warning: ignoring malformed [plan] in dos.toml: {e}", file=stderr)
        return MarkdownPlanSource.name
    except OSError:
        return MarkdownPlanSource.name


def default_source(config: object, *, _stderr=None) -> "tuple[str, PlanSource | None]":
    """The ``(name, source)`` a plan view reads when nothing was asked explicitly.

    The "which source is the default" decision, kept with the seam: the declared
    `[plan] source` when the workspace names one, else the built-in `markdown`.
    A declared name that does not RESOLVE (plugin not installed, a typo) returns
    ``(name, None)`` with a one-line stderr note — the caller renders no rows
    (**fail-to-empty**), never a silently substituted harvester: the workspace
    asked for a specific dialect, and showing another source's rows under that
    label is exactly the unannounced substitution `resolve_plan_source` refuses.
    """
    name = declared_source_name(config, _stderr=_stderr)
    if name == MarkdownPlanSource.name:
        return name, MarkdownPlanSource()
    try:
        return name, resolve_plan_source(name, _stderr=_stderr)
    except ValueError as e:
        stderr = _stderr if _stderr is not None else sys.stderr
        print(
            f"warning: declared plan source {name!r} did not resolve ({e}); "
            f"the plan view shows no rows (fail-to-empty)",
            file=stderr,
        )
        return name, None


def default_rows(config: object, *, _stderr=None) -> list[PlanRow]:
    """The plan view's default row set: the DECLARED source (else the built-in
    markdown), run fail-safe.

    The one call `plan_board.snapshot`'s default branch makes when no explicit
    source/phase list was given — `default_source` picks, `run_plan_source`
    enforces fail-to-empty, and an unresolvable declared name contributes
    nothing. Kept here (not in `plan_board`) so the "which source is the
    default" decision lives with the seam, and so a future change to compose
    MULTIPLE active sources is a one-line edit here rather than in the
    projection.
    """
    _name, src = default_source(config, _stderr=_stderr)
    if src is None:
        return []
    return run_plan_source(src, config)
