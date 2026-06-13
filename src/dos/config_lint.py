"""CFL — the config-integrity linter: *dead policy as a verdict.*

docs/227 — **G1 from the docs/189 Claude Code audit.** Claude Code ships
`shadowedRuleDetection.ts` (`detectUnreachableRules`): a static check that finds a
permission *allow* rule made **unreachable** — dead code — by a more general
deny/ask rule that precedes it. This module is that idea aimed at *DOS's own*
registries: a workspace declares its policy as data (the lane taxonomy + the reason
vocabulary in `dos.toml`), and data can be **internally inconsistent** in ways that
are *structurally detectable* without running anything — a lane that can never be
arbitrated, a lane that is both "runs in parallel" and "runs alone," a reference to
a lane that was never declared, a lane whose region is wholly swallowed by another.
Each is **dead policy**: a declaration that looks active but can never fire.

This is a kernel verdict, not a CLI helper, for the same reason every other verdict
is — it is **byte-clean by construction**: its only input is the config the
operator authored (`LaneTaxonomy` + `ReasonRegistry`), never an agent's narration
and never the live world. It is a pure function — registry-in, findings-out — with
no I/O, no clock, no plan, the `liveness.classify` / `overlap_eval` shape:

    liveness.classify     (ProgressEvidence, policy)       -> LivenessVerdict
    productivity.classify  (WorkHistory, policy)            -> ProductivityVerdict
    config_lint.lint       (LaneTaxonomy, ReasonRegistry)  -> tuple[Finding, ...]
                           ^ THIS module

It also **consolidates logic that drifted into the CLI shell**. The treeless-lane
check lived inline in `cli.py::_treeless_lane_findings` (policy logic in a layer
that is supposed to carry none); the overlapping-concurrent-lane check is mirrored
in `supervise.overlapping_concurrent_lanes` (re-derived here from the same
`_tree.lane_trees_disjoint` definition, so the two cannot drift). The four checks
with no prior home — contradictory concurrent∩exclusive, dangling autopick/alias
targets, and the true strict-subset *shadow* — are new.

**Advisory** (the docs/99 floor): the linter REPORTS findings; it never rewrites
`dos.toml` and never refuses a lease. It is a `dos doctor --check` integrity rail
and a `dos lint` CI gate — the operator decides what a finding *means*.

**Shadow vs. overlap — the subtle distinction (docs/227 §3).** `_tree`'s existing
`lane_trees_disjoint` returns False for *any* prefix collision; it cannot tell
"A's region is wholly inside B's" (A is dead) from "A and B incidentally share a
file" (both live, order-sensitive). Those are different findings with different
fixes, so this module adds a **strict-subset** test (`_region_swallowed_by`): A is
swallowed by B iff every normalized prefix in A collides with some prefix in B and
the reverse is not also true (the symmetric case is identical regions — reported as
overlap, since neither is "the smaller one"). SHADOW means *remove the dead lane*;
OVERLAP means *disjoin the trees or mark one exclusive*. A pair is reported by
exactly one of the two, never both.

No host names (Law 1): the linter reads the generic taxonomy FIELDS
(`concurrent`/`exclusive`/`autopick`/`trees`/`aliases`) and never a lane NAME like
`apply` or `src`. Pure stdlib + the `_tree` leaf — a true leaf importing no layer
above it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Iterable, Sequence

from dos._tree import norm_tree_prefix as _norm_tree_prefix


class LintKind(str, enum.Enum):
    """The closed set of config-integrity findings — `str`-valued so a finding
    round-trips through JSON / a CLI line without a lookup table (the
    `liveness.Liveness` / `gate_classify.Verdict` discipline)."""

    # -- lane taxonomy --------------------------------------------------------
    LANE_WITHOUT_TREE = "LANE_WITHOUT_TREE"
    LANE_BOTH_CONCURRENT_AND_EXCLUSIVE = "LANE_BOTH_CONCURRENT_AND_EXCLUSIVE"
    AUTOPICK_LANE_UNDECLARED = "AUTOPICK_LANE_UNDECLARED"
    ALIAS_TARGET_UNDECLARED = "ALIAS_TARGET_UNDECLARED"
    LANE_REGION_SHADOWED = "LANE_REGION_SHADOWED"
    CONCURRENT_LANES_OVERLAP = "CONCURRENT_LANES_OVERLAP"
    # -- reason registry ------------------------------------------------------
    REASON_SEE_ALSO_DANGLES = "REASON_SEE_ALSO_DANGLES"
    # -- plan namespace (docs/317 P2) -----------------------------------------
    PLAN_NUMBER_DUPLICATE = "PLAN_NUMBER_DUPLICATE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Severity(str, enum.Enum):
    """How bad a finding is — ordered, so a consumer can gate on it.

      error — the config is BROKEN (a lane that cannot be arbitrated, a
              contradiction). A CI gate should fail.
      warn  — the config is SUSPECT (a dangling reference, a dead/shadowed lane,
              an order-sensitive roster). Surfaced; a host may treat as advisory.
      info  — a COSMETIC nit (a dead doc link). Surfaced, never gates.
    """

    ERROR = "error"
    WARN = "warn"
    INFO = "info"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# The sort rank for severities (error first — the most actionable on top). Kept as
# a module constant rather than relying on enum definition order so the ordering is
# explicit and a reorder of the enum can't silently change the report order.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.ERROR: 0,
    Severity.WARN: 1,
    Severity.INFO: 2,
}


@dataclass(frozen=True)
class Finding:
    """One config-integrity finding, as data — a typed record a consumer filters
    on, never prose it must re-parse (the `InterventionDecision` / `OverlapDecision`
    discipline).

    Fields:
      kind     — the closed `LintKind` this finding is.
      severity — error / warn / info (§4).
      subject  — the lane / reason / alias keyword the finding is ABOUT (so a
                 consumer can group/dedupe by subject).
      detail   — one line: what is wrong.
      fix      — one line: how to fix it (co-located with the finding, the
                 `ReasonSpec.fix` "remedy beside the symbol" rule).
    """

    kind: LintKind
    severity: Severity
    subject: str
    detail: str
    fix: str

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def line(self) -> str:
        """Render to one operator-facing line (the form `dos doctor --check`
        appends to its findings list). `[severity] subject: detail — fix`."""
        return f"[{self.severity}] {self.kind} {self.subject!r}: {self.detail} — {self.fix}"

    def to_dict(self) -> dict:
        """JSON-friendly projection (for `dos lint --json` / `doctor --json`)."""
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "subject": self.subject,
            "detail": self.detail,
            "fix": self.fix,
        }


# ---------------------------------------------------------------------------
# The prefix algebra for the shadow / overlap distinction. Reuses
# `_tree.norm_tree_prefix` (the ONE normalization — case-folded, glob-truncated)
# so this module collides identically to `lane_overlap` / the self-modify guard
# and cannot drift. We do NOT reuse `_tree.lane_trees_disjoint` for the subset
# test because that function answers only the boolean "do they collide at all?",
# which conflates strict-subset (shadow) with incidental-intersection (overlap).
# ---------------------------------------------------------------------------


def _norm_prefixes(tree: Sequence[str]) -> list[str]:
    """The normalized, non-empty-source prefixes of a lane tree. An entry that is
    the empty string is dropped (no region); a leading-glob entry (`**/*`) keeps
    its EMPTY prefix `""`, the universal prefix that collides with everything —
    `_tree`'s documented rule, preserved here."""
    return [_norm_tree_prefix(p) for p in tree if p]


def _prefix_collides(prefix: str, others: Sequence[str]) -> bool:
    """True iff `prefix` collides with at least one of `others` — SYMMETRIC (one is
    a prefix of the other, the `_tree.prefixes_collide` rule). This answers "do the
    regions intersect?", NOT "is one contained in the other" — see
    `_prefix_within` for the directional containment test."""
    for o in others:
        if prefix.startswith(o) or o.startswith(prefix):
            return True
    return False


def _prefix_within(prefix: str, outers: Sequence[str]) -> bool:
    """True iff `prefix` names a region AT OR BELOW some prefix in `outers` —
    DIRECTIONAL (``prefix.startswith(outer)`` only, never the reverse).

    This is the load-bearing asymmetry the shadow test needs (docs/227 §3). "Lane
    A's region is contained in lane B's" means every A-prefix sits *inside* a
    B-prefix — i.e. the A-prefix is the more-specific one (``src/api/`` is within
    ``src/``). The symmetric collision test (`_prefix_collides`) cannot tell that
    from the reverse (B inside A), which is exactly why the first draft mis-reported
    a strict subset as an incidental overlap. The empty/universal outer prefix
    ``""`` contains everything (``x.startswith("")`` is always True), so a lane is
    "within" a whole-repo lane — correct, it is swallowed by it.
    """
    for o in outers:
        if prefix.startswith(o):
            return True
    return False


def _region_within(a: Sequence[str], b: Sequence[str]) -> bool:
    """True iff EVERY normalized prefix in `a` sits at-or-below SOME prefix in `b`
    — "a's region is contained in b's region" (directional, via `_prefix_within`).

    An empty `a`/`b` → False (the caller guards treeless lanes upstream — a
    treeless concurrent lane is its own LANE_WITHOUT_TREE error, never a shadow —
    so this is only reached for lanes that have a tree).
    """
    na = _norm_prefixes(a)
    nb = _norm_prefixes(b)
    if not na or not nb:
        return False
    return all(_prefix_within(p, nb) for p in na)


def _region_swallowed_by(a: Sequence[str], b: Sequence[str]) -> bool:
    """True iff lane A's region is a STRICT subset of lane B's region (docs/227 §3).

    A ⊂ B iff A's region is contained in B AND B's is not also contained in A
    (the reverse containment failing is what makes it *strict* — equal regions, where
    both directions hold, are NOT a shadow; they are reported as overlap since neither
    lane is "the smaller one"). So `["src/api/**"]` is swallowed by `["src/**"]`, but
    two lanes with the identical tree are not (mutual containment → not strict).
    """
    a_in_b = _region_within(a, b)
    if not a_in_b:
        return False
    b_in_a = _region_within(b, a)
    return not b_in_a  # strict: A ⊆ B but B ⊄ A


def _regions_collide(a: Sequence[str], b: Sequence[str]) -> bool:
    """True iff any prefix of A collides with any prefix of B (the symmetric
    'do these regions intersect at all?' — same answer as
    `_tree.lane_trees_disjoint` negated, recomputed here over already-normalized
    prefixes so the overlap finding and the shadow finding share one prefix pass)."""
    na = _norm_prefixes(a)
    nb = _norm_prefixes(b)
    if not na or not nb:
        return False
    for pa in na:
        if _prefix_collides(pa, nb):
            return True
    return False


# ---------------------------------------------------------------------------
# The lane-taxonomy lint.
# ---------------------------------------------------------------------------


def lint_lanes(taxonomy) -> tuple[Finding, ...]:
    """Every lane-taxonomy integrity finding, unsorted (the caller sorts).

    `taxonomy` is duck-typed on the `config.LaneTaxonomy` surface
    (`.concurrent`/`.exclusive`/`.autopick` tuples, `.trees` dict, `.aliases`
    dict, `.tree_for(lane)`) so this leaf imports no layer above it — the
    dependency-arrow rule (it never imports `config`, which imports the seam-data
    modules; it reads the shape it is handed). Pure: no I/O.
    """
    findings: list[Finding] = []

    concurrent = tuple(taxonomy.concurrent)
    exclusive = tuple(taxonomy.exclusive)
    autopick = tuple(taxonomy.autopick)
    aliases = dict(taxonomy.aliases)

    declared = set(concurrent) | set(exclusive)

    # 1. LANE_WITHOUT_TREE — a concurrent/autopick lane with no tree. Exclusive
    #    lanes are EXEMPT (the arbiter admits them on liveness alone, never a tree —
    #    the bug `_treeless_lane_findings` already learned: a treeless `global` is
    #    correct). One finding per offending lane, naming which role(s) referenced it.
    treeless_roles: dict[str, list[str]] = {}
    for role, members in (("concurrent", concurrent), ("autopick", autopick)):
        for lane in members:
            if not taxonomy.tree_for(lane):
                treeless_roles.setdefault(lane, []).append(role)
    for lane in sorted(treeless_roles):
        where = "/".join(treeless_roles[lane])
        findings.append(Finding(
            kind=LintKind.LANE_WITHOUT_TREE,
            severity=Severity.ERROR,
            subject=lane,
            detail=(f"declared in [lanes].{where} but has no tree in [lanes.trees] "
                    f"— it can't be arbitrated (the disjointness algebra has nothing "
                    f"to compare)"),
            fix="declare its tree in [lanes.trees], or drop the lane",
        ))

    # 2. LANE_BOTH_CONCURRENT_AND_EXCLUSIVE — a contradiction.
    both = sorted(set(concurrent) & set(exclusive))
    for lane in both:
        findings.append(Finding(
            kind=LintKind.LANE_BOTH_CONCURRENT_AND_EXCLUSIVE,
            severity=Severity.ERROR,
            subject=lane,
            detail=("declared in BOTH [lanes].concurrent and [lanes].exclusive — "
                    "contradictory (concurrent runs in parallel iff disjoint; "
                    "exclusive runs alone)"),
            fix="keep the lane in exactly one of concurrent / exclusive",
        ))

    # 3. AUTOPICK_LANE_UNDECLARED — an autopick walk entry that is neither
    #    concurrent nor exclusive (a dangling reference — usually a typo).
    for lane in autopick:
        if lane not in declared:
            findings.append(Finding(
                kind=LintKind.AUTOPICK_LANE_UNDECLARED,
                severity=Severity.WARN,
                subject=lane,
                detail=("is in the [lanes].autopick walk order but is declared in "
                        "neither [lanes].concurrent nor [lanes].exclusive — the walk "
                        "silently skips it"),
                fix=("add it to [lanes].concurrent (or [lanes].exclusive), or remove "
                     "it from autopick"),
            ))

    # 4. ALIAS_TARGET_UNDECLARED — an alias pointing at an undeclared lane.
    #    Subject is the keyword (what the operator typed); detail names the target.
    for keyword in sorted(aliases):
        target = aliases[keyword]
        if target not in declared:
            findings.append(Finding(
                kind=LintKind.ALIAS_TARGET_UNDECLARED,
                severity=Severity.WARN,
                subject=keyword,
                detail=(f"[lanes.aliases].{keyword} routes to lane {target!r}, which "
                        f"is declared in neither concurrent nor exclusive — the alias "
                        f"resolves to nothing (or an UNKNOWN_LANE refuse at request "
                        f"time)"),
                fix=f"declare lane {target!r}, or fix the alias target",
            ))

    # 5 + 6. Shadow (strict subset → dead) vs. overlap (incidental intersection →
    #        order-sensitive), over the CONCURRENT lanes that HAVE a tree (a
    #        treeless lane is already a LANE_WITHOUT_TREE error; we don't double-
    #        report it as shadowed/overlapping). A pair is reported by exactly one
    #        of the two findings.
    sized = [(lane, tuple(taxonomy.tree_for(lane)))
             for lane in concurrent if taxonomy.tree_for(lane)]
    # Stable order: name-sorted, so the report and fixtures are deterministic.
    sized.sort(key=lambda t: t[0])
    for i in range(len(sized)):
        for j in range(i + 1, len(sized)):
            a_name, a_tree = sized[i]
            b_name, b_tree = sized[j]
            # Shadow first — it is the more specific (and more actionable) verdict.
            # Either direction can be the swallowed one; name the dead lane as the
            # subject and the swallowing lane in the detail.
            if _region_swallowed_by(a_tree, b_tree):
                dead, broad = a_name, b_name
                findings.append(_shadow_finding(dead, broad))
            elif _region_swallowed_by(b_tree, a_tree):
                dead, broad = b_name, a_name
                findings.append(_shadow_finding(dead, broad))
            elif _regions_collide(a_tree, b_tree):
                # Incidental intersection (neither swallows the other) → overlap.
                findings.append(Finding(
                    kind=LintKind.CONCURRENT_LANES_OVERLAP,
                    severity=Severity.WARN,
                    subject=f"{a_name}+{b_name}",
                    detail=(f"concurrent lanes {a_name!r} and {b_name!r} have "
                            f"OVERLAPPING regions — only one can hold a worker at a "
                            f"time, so the supervisor's spawn order decides which"),
                    fix=("declare disjoint trees in [lanes.trees], or mark one "
                         "[lanes].exclusive, to make the roster order-insensitive"),
                ))

    return tuple(findings)


def _shadow_finding(dead: str, broad: str) -> Finding:
    """The LANE_REGION_SHADOWED finding for `dead` ⊂ `broad` (factored so both
    direction branches build it identically)."""
    return Finding(
        kind=LintKind.LANE_REGION_SHADOWED,
        severity=Severity.WARN,
        subject=dead,
        detail=(f"concurrent lane {dead!r}'s region is wholly inside concurrent lane "
                f"{broad!r}'s — {dead!r} can never be picked independently (any "
                f"request for it collides with {broad!r}), so it is dead"),
        fix=(f"remove lane {dead!r}, or carve {broad!r} so it no longer contains "
             f"{dead!r}"),
    )


# ---------------------------------------------------------------------------
# The reason-registry lint. (Most reason integrity is enforced at construction —
# `ReasonRegistry.__post_init__` rejects duplicate tokens, `ReasonSpec.__post_init__`
# rejects an unknown category — so the linter adds only the cross-reference check
# the constructor can't do without knowing the lane set.)
# ---------------------------------------------------------------------------


def lint_reasons(registry, *, known_lanes: Iterable[str]) -> tuple[Finding, ...]:
    """The reason-registry integrity findings, unsorted (the caller sorts).

    `registry` is duck-typed on the `reasons.ReasonRegistry` surface (`.specs`
    iterable of objects with `.token` + `.see_also`). `known_lanes` is the set of
    declared lane names a `see_also` of the form `lane <name>` is checked against.
    Pure: no I/O.

    The only check: REASON_SEE_ALSO_DANGLES — a `see_also` pointer `lane <name>`
    whose `<name>` is not a declared lane (a dead man-page cross-ref). Conservative
    — only the `lane <name>` shape is modeled; other `see_also` targets (oracles,
    meta keys) are free-form prose the linter does not judge.
    """
    findings: list[Finding] = []
    lanes = set(known_lanes)
    for spec in getattr(registry, "specs", ()):  # tolerate an empty/absent registry
        for ref in getattr(spec, "see_also", ()):
            ref = str(ref).strip()
            if not ref.lower().startswith("lane "):
                continue
            target = ref[len("lane "):].strip()
            # A `lane <name>` ref may itself be a placeholder like `<holder>` (the
            # LANE_LEASE_HELD reason's templated pointer); skip angle-bracketed
            # placeholders — they are documentation, not a concrete lane name.
            if not target or (target.startswith("<") and target.endswith(">")):
                continue
            if target not in lanes:
                findings.append(Finding(
                    kind=LintKind.REASON_SEE_ALSO_DANGLES,
                    severity=Severity.INFO,
                    subject=str(spec.token),
                    detail=(f"see_also points at {ref!r} but lane {target!r} is not "
                            f"declared — the man-page cross-ref dead-ends"),
                    fix=f"declare lane {target!r}, or fix the see_also pointer",
                ))
    return tuple(findings)


# ---------------------------------------------------------------------------
# The plan-namespace lint (docs/317 P2). Two declared plans sharing a docs/NN
# number share one STAMP HANDLE: until the slug-or-nothing oracle rule
# (docs/317 P1) refuses it, a bare `(docs/NN Pk)` stamp witnesses either plan —
# and under the rule, it witnesses neither, so the duplicate is dead-policy
# shaped either way: a number that can no longer do its job. The fold that
# DETECTS the duplicates lives with its consumer (`phase_shipped.
# duplicate_plan_heads`, the one implementation both the oracle and this lint
# read); this leaf stays pure by taking the already-folded map as data —
# gathered at the CLI boundary like every other workspace fact.
# ---------------------------------------------------------------------------


def lint_plans(duplicate_plans) -> tuple[Finding, ...]:
    """The plan-namespace integrity findings, unsorted (the caller sorts).

    `duplicate_plans` is the `{number-head: (basenames…)}` map of heads shared
    by ≥ 2 declared plan docs (the `phase_shipped.duplicate_plan_heads` fold,
    fed from the workspace's own `plans_glob` at the boundary). One WARN per
    shared head, naming EVERY colliding basename — the disjoin move is a
    rename, so the finding must say which files collide. Severity warn, not
    error: under the docs/317 P1 oracle rule the verdicts stay truthful per
    plan (bare-head stamps refuse); the duplicate is suspect policy, not
    corruption. Pure: no I/O; `None`/empty → no findings.
    """
    findings: list[Finding] = []
    for head in sorted(duplicate_plans or {}):
        names = tuple((duplicate_plans or {})[head])
        findings.append(Finding(
            kind=LintKind.PLAN_NUMBER_DUPLICATE,
            severity=Severity.WARN,
            subject=str(head),
            detail=(f"{len(names)} declared plan docs share number {head!r} "
                    f"({', '.join(names)}) — a bare number-head ship-stamp on "
                    f"that number is ambiguous, so the oracle refuses it for "
                    f"every one of them (docs/317 slug-or-nothing)"),
            fix=("renumber all but the first-committed plan (git mv + re-stamp, "
                 "the docs/317 playbook), or stamp with the full plan slug"),
        ))
    return tuple(findings)


# ---------------------------------------------------------------------------
# The top-level lint — both registries, sorted.
# ---------------------------------------------------------------------------


def _sort_key(f: Finding) -> tuple[int, str, str]:
    """error → warn → info, then by kind, then by subject — a stable, deterministic
    report order (the `overlap_eval` / `lane_overlap` ordering discipline)."""
    return (_SEVERITY_RANK[f.severity], f.kind.value, f.subject)


def lint(taxonomy, registry=None, *, duplicate_plans=None) -> tuple[Finding, ...]:
    """Every config-integrity finding across the lane taxonomy and (optionally) the
    reason registry, sorted error → warn → info.

    `registry` is optional: a caller that only has a taxonomy (or wants only the
    lane rail) passes `None` and gets the lane findings alone. When a registry is
    given, its `see_also` pointers are checked against the taxonomy's declared lanes
    (so the cross-reference check sees both halves). `duplicate_plans` is optional
    (docs/317 P2): the `{number-head: (basenames…)}` map of shared plan numbers the
    boundary gathered from the workspace's plans glob — omitted/empty, the plan
    rail emits nothing and the call is byte-identical to before. Pure: no I/O.
    """
    findings: list[Finding] = list(lint_lanes(taxonomy))
    if registry is not None:
        known = set(taxonomy.concurrent) | set(taxonomy.exclusive)
        findings.extend(lint_reasons(registry, known_lanes=known))
    findings.extend(lint_plans(duplicate_plans))
    return tuple(sorted(findings, key=_sort_key))


def has_error(findings: Iterable[Finding]) -> bool:
    """True iff any finding is an ERROR — the gate a strict CI check keys on (a
    `dos lint --strict` could fail on this alone, while the default fails on any
    finding, matching today's `doctor --check`)."""
    return any(f.severity is Severity.ERROR for f in findings)
