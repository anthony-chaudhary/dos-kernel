"""`enumerate` — the phase-list producer (docs/168 Concept 1, the unbuilt third).

The kernel owns `oracle` ("did *this id* ship?") and `completion`
("residual = declared − verified"). Both need a `declared` set as INPUT — and
neither produces it. The host did, in its own code
(`job/scripts/plan_phases.py::derive_phase_universe`), and every bug in that
re-implementation was a fleet-wide wedge: the **picker-invisibility gap** — on
2026-06-05 the `job` registry held 62 ACTIVE plans but only 14 carried a
machine-readable `remaining:[…]` list; the other ~38, several with real work and
rich phase tables, were SILENTLY DROPPED by the auto-pick ladder because a plan
with no nameable next phase has no pick. That is the operator's "losing plans"
bug, and the `ladder read slot not priority` class is its sibling (the ladder
read an obsolete field with a prose-digit regex and ranked a done plan top).

This module is that missing producer, lifted to the kernel and made generic:

  > Given a plan-doc's BYTES and a declared GRAMMAR, enumerate the unit ids it
  > declares, in document order, with a typed DriftNote where the doc disagrees
  > with itself — never a silently-empty universe, never a raise.

It composes INTO `completion` (it is the producer of the `declared` set the
residual is measured against), it does not stand beside it (docs/168 §1).

Relocate, don't relax (Design Law 6, docs/207 §3)
=================================================

The `job` deriver is battle-scarred against a 38-invisible-plan corpus. Every
piece of its correctness moves here BYTE-FOR-BYTE; only the GRAMMAR (which
heading/table/bare shape declares a unit, which prefix anchors it) is lifted to
`[enumerate]` data (`EnumerateGrammar`), exactly as `[stamp]` lifted the ship
subject grammar:

  * **Series-anchored token regex** — the anti-brittleness core. A unit id is a
    declared `series` prefix then a digit / sub-phase / word-suffix tail. The
    series anchor is the ONE rule that rejects every data-table trap (`| Class |
    Count |`, `| (c) | 25 |`, sibling-plan rows, the literal `Phase`/`#`/`---`
    header/separator cells) — none start with the series, so none enumerate.
  * **Three id shapes** with range guards (`IFR4-IFR5` is a range, not a phase).
  * **Code-fence stripping** — a phase id inside a ``` sample never enumerates.
  * **Heading + table + bare-`Phase N` families**, UNION'd (the hybrid plan).
  * **Sibling-clause masking** — the `(CD8 shipped this slot)` row trap.
  * **Structural-stamp gate** — a prose "all-SHIPPED" must not read as a ship.
  * **Parent/child rollup** to a fixpoint, with the not-done guard.
  * **Degrade-never-crash** — a malformed body yields an empty `Enumeration` +
    a typed `DriftNote`, never a raise (the picker-invisibility cure: a typed
    refusal the picker can always produce, never the old silent `[]`).

The generic grammar default (a repo that declares nothing): markdown `### N.
NAME` / `### N — NAME` headings + `| Phase |` table first-cells + bare
`Phase N`. A repo with the reference series-anchored shape declares its grammar
in `dos.toml [enumerate]`.

⚓ Pure; host gathers state. `enumerate_units(source_bytes, *, grammar)` makes no
file/git/clock call — the CLI reads the file and hands in the bytes, the same
seam as `liveness.classify` reading a `git_delta`. So the byte-parity gate
(docs/207 Phase 2, `test_enumerate_byte_parity_job`) replays on the `job` repo's
committed plan docs offline, at $0.

⚓ The module is named ``enumerate`` so the CLI verb reads ``dos enumerate``, but
its public function is ``enumerate_units`` — NOT ``enumerate`` — and consumers
import it as ``from dos import enumerate as _enumerate`` / ``import
dos.enumerate``, NEVER the bare ``from dos import enumerate`` (which would shadow
the builtin in that scope; the kernel uses the builtin at 20+ call sites). See
docs/207-seam-ledger §4.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

# READ-ONLY reuse of the sibling ship-verdict kernel — the SAME internals the
# `job` deriver reused, but they live HERE, so the shipped-state decision is one
# implementation, not a second heuristic. Guarded so a refactor of
# `phase_shipped`'s privates degrades the deriver to its own scans rather than
# crashing the picker that imports us (the durability seam, byte-for-byte from the
# job deriver's own fallback).
try:  # pragma: no cover - the fallback IS the durability seam
    from dos.phase_shipped import (
        _phase_variants as _dos_phase_variants,
        _section_says_shipped as _dos_section_says_shipped,
    )
    _DOS_OK = True
except Exception:  # pragma: no cover - defensive
    _DOS_OK = False

    def _dos_phase_variants(phase: str, series: str = "") -> list[str]:  # type: ignore[misc]
        return [re.escape(phase)]

    def _dos_section_says_shipped(section: str) -> Optional[bool]:  # type: ignore[misc]
        return True if "SHIPPED" in (section or "") else False


# ---------------------------------------------------------------------------
# Where a unit id was discovered / how its shipped-state was decided.
# ---------------------------------------------------------------------------
UnitSource = Literal["header", "table-row", "header+table-row", "generic-header"]
ShippedBy = Literal["stamp", "child-rollup", "meta-shipped", "none"]


# ---------------------------------------------------------------------------
# The grammar — the per-workspace data the parser reads (the `[enumerate]` table).
# Modelled on `dos.stamp.StampConvention`: carries DATA, exposes the compiled
# patterns the scan interpolates. Declared in `dos.toml [enumerate]`, defaulting
# to a generic markdown grammar; the reference series-anchored shape is opt-in.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnumerateGrammar:
    """The closed set of shape parameters `enumerate_units` reads. PURE data.

    Fields (each is policy a host declares in `[enumerate]`):

      * ``series`` — the unit-id prefix that anchors a series-anchored scan
        (e.g. ``"AUTH"``, ``"TF"``). When set, a unit id must be ``<series>`` then
        a numeric / sub-phase / word-suffix tail (the anti-brittleness core). When
        EMPTY (the generic default), no series anchor is used and enumeration falls
        to the markdown-heading + bare-``Phase N`` families alone — so a generic
        repo that declares nothing still enumerates `### 1. NAME` headings.
      * ``heading_levels`` — which markdown heading depths declare a unit
        (``(2,3,4,5,6)`` = `##`..`######`). A heading whose text LEADS with a unit
        id (series-anchored) or with `### N. NAME` / `### Phase N` (generic) opens a
        section running to the next heading of equal-or-higher level.
      * ``scan_tables`` — whether a table data row whose FIRST cell is a unit id
        contributes (the `| Phase | … |` family). The series anchor guards the
        data-table trap; with no series, a numeric first-cell `| 1 | … |` is read
        as unit `1` only when ``generic_numeric_table`` is on.
      * ``generic_numeric_table`` — in the no-series generic mode, treat a leading
        `| N | … |` / `| N. NAME |` first cell as unit `N` (off by default — most
        generic docs use headings, not numbered tables, and a bare numeric column
        is the data-table trap this is conservative about).
      * ``bare_phase_fallback`` — whether bare `### Phase N` headings enumerate
        when the series/heading scan found nothing (the OBS/DLO family). When a
        ``series`` is set, the minted id is series-prefixed (`AB3`, not `Phase 3`)
        so it joins the series-keyed shipped/cooldown stores; with no series the id
        is `Phase N`.
      * ``rollup_parents`` — whether a parent unit (`### AFR1.1`) with no stamp
        whose every child sub-phase shipped is rolled up to shipped (with the
        not-done guard). Off in the generic default (a generic doc rarely nests).

    ``style`` is a human label echoed by `dos doctor` (``"series"`` vs
    ``"generic"``); it is not load-bearing for the scan.
    """

    series: str = ""
    heading_levels: tuple[int, ...] = (2, 3, 4, 5, 6)
    scan_tables: bool = True
    generic_numeric_table: bool = False
    bare_phase_fallback: bool = True
    rollup_parents: bool = False
    style: str = "generic"

    def to_dict(self) -> dict:
        return {
            "series": self.series,
            "heading_levels": list(self.heading_levels),
            "scan_tables": self.scan_tables,
            "generic_numeric_table": self.generic_numeric_table,
            "bare_phase_fallback": self.bare_phase_fallback,
            "rollup_parents": self.rollup_parents,
            "style": self.style,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EnumerateGrammar":
        d = dict(data or {})
        levels = d.get("heading_levels")
        return cls(
            series=str(d.get("series", "") or ""),
            heading_levels=tuple(int(x) for x in levels) if levels else (2, 3, 4, 5, 6),
            scan_tables=bool(d.get("scan_tables", True)),
            generic_numeric_table=bool(d.get("generic_numeric_table", False)),
            bare_phase_fallback=bool(d.get("bare_phase_fallback", True)),
            rollup_parents=bool(d.get("rollup_parents", False)),
            style=str(d.get("style", "generic") or "generic"),
        )


# The two named conventions that ship in the package — the `[stamp]` twin pattern.
GENERIC_GRAMMAR = EnumerateGrammar()
# The reference series-anchored grammar (the `job` deriver's shape), opt-in: a
# caller passes `series=<plan id>` to get it (so it stays per-plan, since the
# series differs per plan — unlike `[stamp]`'s repo-wide grammar).
JOB_GRAMMAR = EnumerateGrammar(
    scan_tables=True, bare_phase_fallback=True, rollup_parents=True, style="series"
)


# ---------------------------------------------------------------------------
# Compiled patterns (built from a grammar at scan time — none host-specific).
# ---------------------------------------------------------------------------

# A markdown heading at any level `## … / ###### …`.
_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.*)$")
# A markdown table data row; only the FIRST cell is inspected for a unit id.
_TABLE_ROW_RE = re.compile(r"(?m)^[ \t]*\|(?P<first>[^|]*)\|(?P<rest>.*)$")
# A table separator row `|---|:--:|` — never a unit.
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[\s:|.\-]+$")
# A fenced code block delimiter.
_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")
# Bare generic `### Phase N` header (the OBS/DLO fallback). Ordinal w/ letter/decimal.
_GENERIC_PHASE_RE = re.compile(r"(?i)^phase\s+(\d+[a-z]?(?:\.\d+)*)\b")
# A generic numbered heading `### 1. NAME` / `### 2 — NAME` (the no-series default).
_GENERIC_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?(?:\s|—|–|-|$)")
# Lowercase-tolerant shipped marker for table-row prose (the `(shipped 2026-…)` form).
# `done` is excluded (false-trips on "not done"); `shipped` is word-bounded + not
# preceded by `not `. ✅/✓/[x] are unambiguous completion marks.
_ROW_SHIPPED_RE = re.compile(r"(?i)(?:(?<!not )(?<!not yet )\bshipped\b|✅|✓|\[x\])")
# A *structural* uppercase-SHIPPED STAMP (the operator's `— SHIPPED 2026-…` mark),
# distinguished from the word buried in spec prose. `SHIPPED` preceded by ws/`*`/`(`
# or a separator glyph that itself follows whitespace.
_STRUCTURAL_STAMP_RE = re.compile(r"(?:[\s*(]|(?<=\s)[—·\-])SHIPPED\b")
# An explicit not-done marker that BLOCKS a parent/child roll-up (the MC2/MC2.1 guard).
_NOT_DONE_RE = re.compile(
    r"(?i)\b(pending|not[- ]shipped|not[- ]started|draft|tomb(?:ed|stone)?|"
    r"deferred|blocked|in[- ]progress|abort)\b"
)


def _phase_token_re(series: str) -> "re.Pattern[str]":
    """The series-anchored unit-id token regex (the anti-brittleness core).

    Byte-for-byte the `job` deriver's `_phase_token_re`. Three id shapes after the
    series, all requiring the series prefix:
      * numeric / sub-phase: `TF0`, `MAS2.5`, `AFR1.1.0`, `HS1a`, `SF1.2-port`,
        `PLA6.4-C`, `SVP-2` (leading `-?` lets the series hyphen-join a number).
      * word-suffix satellites: `AFR-FQ282`, `WD-CREATE-ACCT`, `SV-FQ57`.
    `(?<![A-Za-z0-9])` stops `XTF0`; the trailing boundary stops `MAS1`→`MAS10`. A
    sub-suffix that re-opens with `series+digit` is a RANGE (`IFR4-IFR5`), guarded out.
    """
    s = re.escape(series)
    num_arm = rf"-?\d+[a-z]?(?:\.\d+)*(?:-(?!{s}\d)[A-Za-z0-9.]+)?"
    word_arm = rf"-(?!{s}\d)[A-Za-z0-9][A-Za-z0-9.\-]*"
    return re.compile(
        rf"(?<![A-Za-z0-9]){s}(?:{num_arm}|{word_arm})(?![A-Za-z0-9.\-])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Observable result types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftNote:
    """A typed note that the doc disagrees with itself or could not be parsed.

    The kernel-typed replacement for the `job` `audit_plan_pickability --drift`
    surface and the picker-invisibility SILENT drop. ``kind``:

      * ``unparseable`` — a heading/region the grammar could not read (carries the
        ``span`` quote) — surfaced, never a raise, never a silently-empty universe.
      * ``list_table_mismatch`` — a plan-meta cached list names a unit the doc body
        does NOT declare, or vice-versa (the PPG "table is authority, cached list
        is cache" lesson). ``detail`` carries which ids diverged.
      * ``empty`` — the body declared no unit ids at all (an honestly-empty plan,
        or a malformed one). The caller decides; a picker reads it as "no pickable
        unit here," surfaced rather than dropped.
    """

    kind: str
    detail: str
    span: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "span": self.span}


@dataclass(frozen=True)
class UnitSpan:
    """One enumerated unit id — its source, shipped-state, and the deciding line.

    The observable record (the `job` `PhaseTrace`): a wrong enumeration is a query
    (`dos enumerate --json`), not a debugging session.
    """

    unit: str
    source: UnitSource
    shipped: bool
    shipped_by: ShippedBy
    evidence: str  # a short quote of the deciding line

    def to_dict(self) -> dict:
        return {
            "unit": self.unit,
            "source": self.source,
            "shipped": self.shipped,
            "shipped_by": self.shipped_by,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Enumeration:
    """The full observable result of enumerating one plan-doc body.

    ``units`` is the ordered list of ALL declared unit ids (shipped + remaining),
    in document order. ``remaining`` / ``shipped`` are the partition. ``by_unit``
    is the per-unit `UnitSpan` map. ``drift`` is the list of typed `DriftNote`s.
    ``series`` echoes the grammar's series label.
    """

    series: str
    units: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    shipped: tuple[str, ...] = ()
    by_unit: dict = field(default_factory=dict)
    drift: tuple[DriftNote, ...] = ()

    def to_dict(self) -> dict:
        return {
            "series": self.series,
            "units": list(self.units),
            "remaining": list(self.remaining),
            "shipped": list(self.shipped),
            "by_unit": {u: s.to_dict() for u, s in self.by_unit.items()},
            "drift": [d.to_dict() for d in self.drift],
        }


# ---------------------------------------------------------------------------
# Scan helpers (pure, no I/O) — relocated from the `job` deriver.
# ---------------------------------------------------------------------------


def _strip_code_fences(body: str) -> str:
    """Blank fenced code-block CONTENT (keeping line count + offsets stable)."""
    out: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _heading_id(title: str, tok_re: "re.Pattern[str]") -> Optional[str]:
    """The series unit id a heading LEADS with (within 2 chars), or None."""
    t = title.lstrip("*# ").strip()
    tm = tok_re.search(t)
    if tm and tm.start() <= 2:
        return tm.group(0)
    return None


def _iter_levels(body: str, heading_levels: tuple[int, ...]):
    """Yield `(level, match)` for every heading at a permitted level, in order."""
    for m in _HEADING_RE.finditer(body):
        level = len(m.group(1))
        if level in heading_levels:
            yield level, m


def _section_of(body: str, headings: list, idx: int, level: int) -> str:
    """The bounded section of heading `idx`: to the next heading of ≤ level."""
    end = len(body)
    for _lvl, nxt in headings[idx + 1:]:
        if nxt and len(nxt.group(1)) <= level:
            end = nxt.start()
            break
    return body[headings[idx][1].start():end]


def _mask_sibling_clauses(text: str, own_id: str, tok_re: "re.Pattern[str]") -> str:
    """Blank short clauses naming a DIFFERENT unit id (the CD9 `(CD8 shipped)` trap)."""
    own = own_id.lower()
    out = list(text)
    for m in tok_re.finditer(text):
        if m.group(0).lower() == own:
            continue
        end = m.end()
        while end < len(text) and text[end] not in ".;)|\n":
            end += 1
        for i in range(m.start(), end):
            out[i] = " "
    return "".join(out)


def _section_shipped(
    text: str, own_id: str = "", tok_re: "Optional[re.Pattern[str]]" = None
) -> Optional[bool]:
    """Shipped-verdict for a section / row line. Kernel detector + lowercase marker
    on a sibling-masked copy; the kernel uppercase True honored only on a structural
    stamp. Returns True if shipped, else None (so the unit stays remaining)."""
    try:
        v = _dos_section_says_shipped(text)
    except Exception:  # pragma: no cover - defensive
        v = None
    stamp_text = text
    if own_id and tok_re is not None:
        stamp_text = _mask_sibling_clauses(text, own_id, tok_re)
    if v is True and _STRUCTURAL_STAMP_RE.search(stamp_text):
        return True
    if _ROW_SHIPPED_RE.search(stamp_text):
        return True
    return None


def _norm_meta_shipped(shipped: Optional[Iterable[str]], series: str) -> set[str]:
    """The plan-meta `shipped:[]` set normalised to comparable unit ids."""
    out: set[str] = set()
    for entry in shipped or []:
        s = str(entry or "").strip()
        if not s:
            continue
        m = re.match(r"\s*([A-Za-z][A-Za-z0-9.+\-]*?)(?:\s|—|-{2,}|:|$)", s)
        tok = (m.group(1) if m else s).strip()
        pm = re.match(r"(?i)^phase\s*(\d+(?:\.\d+)?)$", tok)
        out.add(f"{series}{pm.group(1)}" if pm else tok)
    return out


def _meta_says_shipped(unit: str, series: str, meta_shipped: set[str]) -> bool:
    """True when `unit` (or a `_phase_variants` synonym) is in the meta shipped set."""
    if unit in meta_shipped:
        return True
    try:
        variants = {re.sub(r"\\(.)", r"\1", v) for v in _dos_phase_variants(unit, series)}
    except Exception:  # pragma: no cover - defensive
        variants = {unit}
    return bool(variants & meta_shipped)


def _parent_all_children_shipped(
    parent: str, shipped_flag: dict, section_of: dict
) -> bool:
    """True iff `parent` has ≥1 child, EVERY child shipped, no not-done marker."""
    children = [
        p for p in shipped_flag
        if p != parent and (p.startswith(parent + ".") or p.startswith(parent + "-"))
    ]
    if not children or not all(shipped_flag[c] for c in children):
        return False
    if _NOT_DONE_RE.search(section_of.get(parent, "")):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def enumerate_units(
    source_bytes: Optional[str],
    *,
    grammar: EnumerateGrammar = GENERIC_GRAMMAR,
    meta_shipped: Optional[Iterable[str]] = None,
) -> Enumeration:
    """Enumerate the unit ids a plan-doc body declares. PURE — no I/O, never raises.

    ``source_bytes`` is the full plan-doc body (the CLI reads the file). ``grammar``
    is the `EnumerateGrammar` (from `dos.toml [enumerate]` or `series=<id>` for the
    series-anchored shape). ``meta_shipped`` is the plan-meta `shipped:[]` cache, if
    any — an authoritative POSITIVE cache (ids here are forced shipped). The doc
    body is the AUTHORITY for the unit *universe*; the cached list is only a
    shipped-state hint and a drift signal (the PPG "table is authority" rule).

    Returns an `Enumeration`. A body that declares nothing yields an empty one with
    an `empty` `DriftNote` (the picker reads "no pickable unit," surfaced — never
    the old silent `[]`). A parse error yields an empty one with an `unparseable`
    `DriftNote`. The verdict is always a typed object, never an exception.
    """
    series = (grammar.series or "").strip().upper()
    meta_set = _norm_meta_shipped(meta_shipped, series)

    if not source_bytes:
        return Enumeration(
            series=series,
            drift=(DriftNote("empty", "empty body — no unit ids declared"),),
            shipped=tuple(sorted(meta_set)),
        )

    try:
        scan = _strip_code_fences(source_bytes)
        tok_re = _phase_token_re(series) if series else None

        order: list[str] = []
        seen: set[str] = set()
        source_set: dict[str, set[str]] = {}
        decide_texts: dict[str, list[str]] = {}
        section_of: dict[str, str] = {}
        evidence_line: dict[str, str] = {}

        def _record(unit: str, source: str, decide: str, evidence: str, section: str):
            if unit not in seen:
                seen.add(unit)
                order.append(unit)
            source_set.setdefault(unit, set()).add(source)
            decide_texts.setdefault(unit, []).append(decide)
            section_of.setdefault(unit, section)
            evidence_line.setdefault(unit, evidence)

        headings = list(_iter_levels(scan, grammar.heading_levels))

        # Headers — series-anchored OR generic numbered (`### 1. NAME`).
        for idx, (level, m) in enumerate(headings):
            title = m.group(2).lstrip("*# ").strip()
            section = _section_of(scan, headings, idx, level)
            unit: Optional[str] = None
            if tok_re is not None:
                unit = _heading_id(m.group(2), tok_re)
            if unit is None and not series:
                gm = _GENERIC_NUM_HEADING_RE.match(title)
                if gm:
                    unit = gm.group(1)
            if unit is not None:
                _record(unit, "header", section, m.group(0).strip(), section)

        # Table rows — first-cell is a unit id.
        if grammar.scan_tables:
            for m in _TABLE_ROW_RE.finditer(scan):
                line = m.group(0)
                if _TABLE_SEP_RE.match(line):
                    continue
                first = m.group("first").strip().strip("*").strip("`").strip()
                if tok_re is not None:
                    tm = tok_re.search(first)
                    if tm and (first == tm.group(0) or first.startswith(tm.group(0) + " ")):
                        _record(tm.group(0), "table-row", line, line.strip(), line)
                elif grammar.generic_numeric_table:
                    gm = _GENERIC_NUM_HEADING_RE.match(first)
                    if gm:
                        _record(gm.group(1), "table-row", line, line.strip(), line)

        # Fallback: bare `Phase N` headers only when nothing else matched.
        if not order and grammar.bare_phase_fallback:
            for idx, (level, m) in enumerate(headings):
                gm = _GENERIC_PHASE_RE.match(m.group(2).lstrip("*# ").strip())
                if not gm:
                    continue
                pid = f"{series}{gm.group(1)}" if series else f"Phase {gm.group(1)}"
                section = _section_of(scan, headings, idx, level)
                _record(pid, "generic-header", section, m.group(0).strip(), section)
    except Exception as exc:  # pragma: no cover - defensive
        return Enumeration(
            series=series,
            drift=(DriftNote("unparseable", f"parse error: {type(exc).__name__}"),),
            shipped=tuple(sorted(meta_set)),
        )

    # Pass 1: per-unit shipped from meta OR own stamp (OR'd across surfaces).
    shipped_flag: dict[str, bool] = {}
    shipped_by: dict[str, ShippedBy] = {}
    for unit in order:
        if _meta_says_shipped(unit, series, meta_set):
            shipped_flag[unit] = True
            shipped_by[unit] = "meta-shipped"
        elif any(
            _section_shipped(txt, unit, tok_re) is True
            for txt in decide_texts.get(unit, [])
        ):
            shipped_flag[unit] = True
            shipped_by[unit] = "stamp"
        else:
            shipped_flag[unit] = False
            shipped_by[unit] = "none"

    # Pass 2: parent/child rollup to a fixpoint.
    if grammar.rollup_parents:
        changed = True
        while changed:
            changed = False
            for unit in order:
                if not shipped_flag[unit] and _parent_all_children_shipped(
                    unit, shipped_flag, section_of
                ):
                    shipped_flag[unit] = True
                    shipped_by[unit] = "child-rollup"
                    changed = True

    by_unit: dict[str, UnitSpan] = {}
    remaining: list[str] = []
    shipped_out: list[str] = []
    for unit in order:
        srcs = source_set.get(unit, set())
        if {"header", "table-row"} <= srcs:
            src_label: UnitSource = "header+table-row"
        elif "generic-header" in srcs:
            src_label = "generic-header"
        elif "header" in srcs:
            src_label = "header"
        else:
            src_label = "table-row"
        ev = {
            "meta-shipped": "(plan-meta shipped:[])",
            "child-rollup": "all child sub-units shipped",
        }.get(shipped_by[unit], evidence_line.get(unit, ""))
        is_shipped = shipped_flag[unit]
        by_unit[unit] = UnitSpan(unit, src_label, is_shipped, shipped_by[unit], ev)
        (shipped_out if is_shipped else remaining).append(unit)

    # Drift: a cached meta-shipped id the body never declared (cache names a ghost),
    # and the empty-universe note. The body is authority; the cache is a hint, so a
    # cache id absent from the body is a list↔table mismatch worth surfacing.
    drift: list[DriftNote] = []
    if not order:
        drift.append(DriftNote("empty", "no unit ids found in body"))
    ghosts = sorted(meta_set - set(order))
    if ghosts and order:
        drift.append(DriftNote(
            "list_table_mismatch",
            f"plan-meta shipped:[] names {len(ghosts)} unit(s) the doc body does "
            f"not declare: {ghosts} — the cached list disagrees with the doc "
            f"(the doc table/headings are authority, the list is cache)",
        ))

    return Enumeration(
        series=series,
        units=tuple(order),
        remaining=tuple(remaining),
        shipped=tuple(shipped_out),
        by_unit=by_unit,
        drift=tuple(drift),
    )


# ---------------------------------------------------------------------------
# The `[enumerate]` config seam — the data-attachment, modelled on `dos.stamp`.
#
# The repo-wide `[enumerate]` table declares the STYLE knobs (heading levels,
# table scan, bare-Phase fallback, rollup). The `series` is supplied PER-PLAN at
# the call boundary (it differs per plan, unlike `[stamp]`'s repo-wide grammar),
# so it is deliberately NOT a TOML key — a caller layers it via `with_series`.
# ---------------------------------------------------------------------------


def grammar_from_table(
    table: dict, *, base: EnumerateGrammar = GENERIC_GRAMMAR
) -> EnumerateGrammar:
    """Build an `EnumerateGrammar` from a parsed `[enumerate]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted fields inherit. An
    unknown key raises (a typo'd field is a host mistake worth surfacing — the
    `stamp.convention_from_table` posture). ``series`` is deliberately NOT a known
    key: it is per-plan, layered at the call boundary, never repo-wide.
    """
    if not isinstance(table, dict):
        raise ValueError(f"[enumerate] must be a table, got {type(table).__name__}")
    known = {
        "heading_levels", "scan_tables", "generic_numeric_table",
        "bare_phase_fallback", "rollup_parents", "style",
    }
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[enumerate] has unknown key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)} (series is per-plan, not a table key)"
        )
    levels = base.heading_levels
    if "heading_levels" in table:
        raw = table["heading_levels"]
        if not isinstance(raw, (list, tuple)) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in raw
        ):
            raise ValueError("[enumerate].heading_levels must be a list of ints")
        levels = tuple(int(x) for x in raw)

    def _bool(key: str, current: bool) -> bool:
        if key not in table:
            return current
        v = table[key]
        if not isinstance(v, bool):
            raise ValueError(f"[enumerate].{key} must be a boolean, got {type(v).__name__}")
        return v

    style = base.style
    if "style" in table:
        if not isinstance(table["style"], str):
            raise ValueError(
                f"[enumerate].style must be a string, got {type(table['style']).__name__}"
            )
        style = table["style"]
    return EnumerateGrammar(
        series=base.series,  # per-plan; layered at the boundary, never from TOML
        heading_levels=levels,
        scan_tables=_bool("scan_tables", base.scan_tables),
        generic_numeric_table=_bool("generic_numeric_table", base.generic_numeric_table),
        bare_phase_fallback=_bool("bare_phase_fallback", base.bare_phase_fallback),
        rollup_parents=_bool("rollup_parents", base.rollup_parents),
        style=style,
    )


def load_from_toml(
    path, *, base: EnumerateGrammar = GENERIC_GRAMMAR
) -> EnumerateGrammar:
    """Build an `EnumerateGrammar` from a `dos.toml`'s `[enumerate]` table.

    Returns ``base`` unchanged when the file is absent, has no `[enumerate]` table,
    or `tomllib` is unavailable — the declarative path is purely additive. A present
    but malformed table raises (`grammar_from_table`). Mirrors
    `stamp.load_from_toml` exactly (incl. the `utf-8-sig` BOM strip).
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("enumerate")
    if not isinstance(table, dict) or not table:
        return base
    return grammar_from_table(table, base=base)


def with_series(grammar: EnumerateGrammar, series: str) -> EnumerateGrammar:
    """Layer a per-plan ``series`` onto a repo-wide grammar (the call-boundary seam).

    The CLI/host reads the plan's series id (plan-meta `id`/`phase_prefix`) and
    layers it here, so the repo declares STYLE in `[enumerate]` and the per-plan
    SERIES is supplied at the call. A non-empty series flips the default `style`
    label to ``"series"`` for legibility (a series-anchored scan), unless the host
    set a `style` explicitly.
    """
    import dataclasses
    s = (series or "").strip().upper()
    style = grammar.style
    if s and style == "generic":
        style = "series"
    return dataclasses.replace(grammar, series=s, style=style)


# ---------------------------------------------------------------------------
# Phase 2c — `enumerate` as the doc-side producer of the `declared` extent.
#
# CORRECTION (docs/207-seam-ledger §4.5): `completion.classify` reads `declared`
# from `state.declared_steps` (the INTENT LEDGER), NOT a host callback. There is
# no callback to remove. The honest Phase-2c task is to make `enumerate` an
# ALTERNATIVE producer of the `declared` extent for a workspace that declares its
# units in PLAN DOCS rather than minting intent-ledger steps — the two are
# different sources of "declared" (doc-enumeration vs ledger-fossils). This bridge
# is the doc-side producer; a host hands its output to whatever consumes a declared
# set (the picker's residual, or a `LedgerState` it mints from the doc universe).
# The kernel keeps `completion` pure and ledger-grounded; this just closes the
# closed concept (oracle → enumerate → completion) on the doc side.
# ---------------------------------------------------------------------------


def declared_extent(enumeration: Enumeration) -> tuple[str, ...]:
    """The ordered `declared` step ids a plan-doc declares — the doc-side producer
    of the extent `completion`/the picker measures the residual against.

    PURE accessor. Returns the full unit universe (shipped + remaining) in document
    order — the `declared` set, exactly the role `LedgerState.declared_steps` plays
    for the intent-ledger path. A host that declares its units in plan docs reads
    this; a host that mints intent-ledger steps reads `state.declared_steps`. The
    two paths converge on the same `declared` contract, so `completion`'s residual
    arithmetic is unchanged — `enumerate` just supplies the doc-side input.
    """
    return enumeration.units


def residual_from_enumeration(enumeration: Enumeration) -> tuple[str, ...]:
    """The doc-derived residual (declared − verified) from an `Enumeration` alone.

    PURE. The `enumerate`-side analogue of `completion`'s residual: the remaining
    (not-yet-shipped) unit ids in document order. A doc-declared workspace that has
    no intent ledger can still compute "what is left" end-to-end from the plan doc
    + the ship verdicts `enumerate` already folded — no host callback, the
    modularity payoff docs/207 §2 calls "close the closed concept." (The
    ledger-grounded `completion.classify` remains the authority where an intent
    ledger exists; this is the floor for the doc-only case.)
    """
    return enumeration.remaining
