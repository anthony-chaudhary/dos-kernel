"""Concurrency-class budgets as declared data — the operator surface over the
already-shipped arbiter class-budget enforcement (docs/97 Phase 1-2, C13).

The arbiter ALREADY enforces "at most N of kind K may hold a lease at once":
`arbiter.arbitrate(..., class_budgets={"priority": 3})` counts live leases per
kind on the auto-pick walk, skips budget-exhausted candidates, and returns the
named `CLASS_BUDGET_EXHAUSTED` refuse (`arbiter.py:356,366,714`). What was missing
is the *operator surface* — the budgets were reachable only as a Python parameter.
This module is that surface's data half: a closed `ConcurrencyClass{name,
max_concurrent}` dataclass + a `from_table` reader for the `[[concurrency_class]]`
array-of-tables in `dos.toml`, projecting to the exact `{kind: N}` dict the arbiter
consumes.

This is mechanism-as-data, the `reasons`/`stamp`/`lanes` seam pattern: the kernel
ships the enforcement; the host declares the VALUES per workspace. It names no host
class — `"priority"`, `"apply"`, whatever — those are workspace data, so Law 1
(kernel imports no host) holds. It deliberately carries ONLY a max-concurrent
budget; it does NOT carry lane priority/value ordering — the arbiter refuses to
hard-code "whose work is valuable" (docs/90 §6), so that stays host policy and
never enters this registry.

    [[concurrency_class]]
    name = "priority"
    max_concurrent = 3

    [[concurrency_class]]
    name = "apply"
    max_concurrent = 1

Pure stdlib leaf — the closed-enum-as-data discipline, validated loud-on-malformed
(a host that mis-declared a budget wants it surfaced at load, not silently dropped
to "no budget" which would let the class run unbounded).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union


# ---------------------------------------------------------------------------
# RegionSource — how a free region of a concurrency class is PRODUCED (docs/97
# §model). Two halves, kept apart on purpose:
#
#   * the TOML-declared half (frozen, pure data, NO callables) — the discriminant
#     a `[[concurrency_class]]` row's `region_source = "..."` string parses to.
#     A frozen dataclass that a host declares in `dos.toml`, so it carries no
#     host pool and no callback.
#   * the call-boundary half (`TopPickablePlanBinding`) — carries the host's
#     actual pool list + `derive_tree` callback, handed to the projection at the
#     call boundary exactly as `auto_pick_order` / `pick_oracle` / `rank_key` are
#     handed to `arbiter.arbitrate` today. A callback structurally cannot live in
#     a frozen TOML-loadable dataclass, so the two halves are separate types.
#
# The kernel owns the TYPES and the disjointness+budget admission; the host owns
# the pool CONTENTS. None of these types names a host lane/plan/cluster — Law 1
# (kernel imports no host) holds: `name`/`pool`/`kind` are opaque strings and
# `derive_tree` an opaque callback.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixedTrees:
    """A closed menu of named regions — clusters / named lanes (docs/97 §model).

    `trees` is a tuple of `(label, tree)` pairs (tuple-of-tuples so the dataclass
    stays `frozen`-hashable; `as_dict()` projects back to `{label: [globs]}`). This
    is what `[lanes.trees]` already is — declared here only so the closed
    `RegionSource` set is complete; the arbiter resolves a `FixedTrees` region via
    the EXISTING cluster/named ladder, not a second resolver."""

    trees: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def as_dict(self) -> dict[str, list[str]]:
        """`{label: [globs]}` — the menu as the taxonomy's `[lanes.trees]` shape."""
        return {label: list(globs) for label, globs in self.trees}


@dataclass(frozen=True)
class WholeWorkspace:
    """The coarsest lock — the whole tree, one holder (docs/97 §model).

    The `exclusive` class's region source (`global`/`orchestration`). No fields:
    the region IS the workspace. Declared for completeness; the arbiter already
    enforces the run-alone semantics via the exclusive-lane path."""


@dataclass(frozen=True)
class TopPickablePlanKind:
    """The DISCRIMINANT for an open-pool priority region source (docs/97 §model).

    Carries NO host data — it is only the marker a `[[concurrency_class]]` row's
    `region_source = "top_pickable_plan"` string parses to. The actual open pool +
    `derive_tree` callback are supplied at the CALL boundary as a
    `TopPickablePlanBinding`, never declared in TOML (a callback cannot be TOML
    data). This separation is what keeps the kernel free of host names: the class
    declares THAT its region comes from an open pool; the host supplies WHICH plans
    and HOW to derive their trees."""


# The closed `RegionSource` discriminant set — the TOML-declarable kinds.
RegionSourceKind = Union[FixedTrees, WholeWorkspace, TopPickablePlanKind]

# The string a `[[concurrency_class]]` row uses to name an open-pool source. The
# fixed/exclusive kinds are resolved by the existing ladder, so only the open-pool
# kind needs a declarable discriminant string today.
_REGION_SOURCE_STRINGS: dict[str, Callable[[], RegionSourceKind]] = {
    "top_pickable_plan": TopPickablePlanKind,
    "fixed_trees": FixedTrees,
    "whole_workspace": WholeWorkspace,
}


def region_source_from_string(value: str | None) -> RegionSourceKind | None:
    """Parse a `region_source` discriminant string to a `RegionSourceKind`.

    `None`/empty → `None` (the class has no declared region source — its kind is
    already resolved by the existing ladder, the back-compat default). An unknown
    string RAISES (a typo'd discriminant is a host mistake worth surfacing, the
    loud-on-malformed sibling-seam discipline). The fixed/exclusive kinds parse to
    their empty form; the host fills a `FixedTrees`' menu separately if it uses one."""
    if not value:
        return None
    key = str(value).strip().lower()
    ctor = _REGION_SOURCE_STRINGS.get(key)
    if ctor is None:
        raise ValueError(
            f"concurrency_class.region_source {value!r} is not a known kind; "
            f"known kinds are {sorted(_REGION_SOURCE_STRINGS)}"
        )
    return ctor()


@dataclass(frozen=True)
class TopPickablePlanBinding:
    """The call-boundary inputs for an open-pool priority region source.

    Carries the HOST data a `TopPickablePlanKind` class needs at admission time:
    `pool` — the opaque plan ids the host offers (a tuple of strings, never
    inspected by the kernel beyond identity), and `derive_tree` — a callback the
    host supplies that maps one plan id to its repo-relative file tree (globs).
    Both are handed to `project_to_ladder` (below) at the call boundary, exactly as
    `auto_pick_order` is handed to `arbiter.arbitrate` today. NOT frozen-required
    and NOT TOML-loadable — it holds a callable. The kernel names no host: `pool`
    members and the derived globs are opaque strings."""

    pool: tuple[str, ...]
    derive_tree: Callable[[str], list[str]]


@dataclass(frozen=True)
class ConcurrencyClass:
    """One declared budget: at most `max_concurrent` leases of kind `name` at once.

    `name` is the lane-KIND the arbiter keys budgets on (`lease["lane_kind"]`),
    opaque workspace data. `max_concurrent` is a non-negative int — 0 means "admit
    none of this kind" (a valid, if drastic, throttle); a negative value is a
    declaration error.

    `rank` (docs/97 §model) is the auto-pick ladder order — lower walks first; it
    lets a host order classes (e.g. a `priority` class ahead of `maintenance`)
    when it projects them into the ladder. `region_source` (docs/97 §model) is the
    declared discriminant for HOW a free region of this class is produced — `None`
    (the back-compat default) means "this kind is already on the existing ladder";
    a `TopPickablePlanKind` means "an open pool supplies the region" (the host
    hands the pool + derive_tree at the call boundary). Both default so every
    existing `ConcurrencyClass(name, max_concurrent)` construction is byte-identical.
    """

    name: str
    max_concurrent: int
    rank: int = 0
    region_source: RegionSourceKind | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("concurrency_class.name is required (the lane kind)")
        if not isinstance(self.max_concurrent, int) or isinstance(self.max_concurrent, bool):
            raise ValueError(
                f"concurrency_class[{self.name!r}].max_concurrent must be an int, "
                f"got {type(self.max_concurrent).__name__}"
            )
        if self.max_concurrent < 0:
            raise ValueError(
                f"concurrency_class[{self.name!r}].max_concurrent must be ≥ 0, "
                f"got {self.max_concurrent}"
            )
        if not isinstance(self.rank, int) or isinstance(self.rank, bool):
            raise ValueError(
                f"concurrency_class[{self.name!r}].rank must be an int, "
                f"got {type(self.rank).__name__}"
            )


@dataclass(frozen=True)
class ClassBudgets:
    """The declared concurrency-class registry — an ordered set of `ConcurrencyClass`.

    Carries the budgets as data and projects them to the `{kind: max_concurrent}`
    dict `arbiter.arbitrate(class_budgets=...)` already consumes. Empty by default
    (no file / no `[[concurrency_class]]` table → no budgets → today's unbounded-
    per-kind behavior, the additive-degradation floor)."""

    classes: tuple[ConcurrencyClass, ...] = ()

    def as_arbiter_budgets(self) -> dict[str, int]:
        """The `{kind: max_concurrent}` dict the arbiter takes. A duplicate name is a
        last-wins override (the host declared the same class twice — honor the last,
        the toml array's natural order)."""
        out: dict[str, int] = {}
        for c in self.classes:
            out[c.name] = c.max_concurrent
        return out

    @classmethod
    def from_table(cls, table: object) -> "ClassBudgets":
        """Build from a parsed `[[concurrency_class]]` array-of-tables.

        TOML's `[[concurrency_class]]` parses to a LIST of dicts. Tolerant of an
        absent/empty list (→ no budgets). Rejects, with a `ValueError` naming the
        offending entry, anything that is not a `{name, max_concurrent}` table —
        loud-on-malformed, the sibling-seam discipline. Mirrors
        `reason_morphology.MorphologyRuleset.from_table` in shape (the array-of-
        tables reader)."""
        if table is None:
            return cls(())
        if not isinstance(table, (list, tuple)):
            raise ValueError(
                f"[[concurrency_class]] must be an array of tables, "
                f"got {type(table).__name__}"
            )
        out: list[ConcurrencyClass] = []
        for i, item in enumerate(table):
            if not isinstance(item, dict):
                raise ValueError(
                    f"[[concurrency_class]] entry {i} must be a table "
                    f"({{name, max_concurrent}}), got {type(item).__name__}"
                )
            if "name" not in item or "max_concurrent" not in item:
                raise ValueError(
                    f"[[concurrency_class]] entry {i} needs both `name` and "
                    f"`max_concurrent` (got keys {sorted(item)})"
                )
            # Optional docs/97 §model fields: `rank` (ladder order, default 0) and
            # `region_source` (the discriminant string, default None = "already on
            # the ladder"). `region_source_from_string` raises on an unknown kind
            # (loud-on-malformed); ConcurrencyClass.__post_init__ validates name /
            # max_concurrent / rank shapes.
            out.append(ConcurrencyClass(
                name=str(item["name"]),
                max_concurrent=item["max_concurrent"],
                rank=item.get("rank", 0),
                region_source=region_source_from_string(item.get("region_source")),
            ))
        return cls(tuple(out))


# An empty registry — the kernel default (no per-kind budget, today's behavior).
NO_CLASS_BUDGETS = ClassBudgets(())


def parse_cli_budgets(pairs: list[str] | None) -> dict[str, int]:
    """Parse repeatable `--class-budget KIND=N` operator flags into `{kind: N}`.

    Each `pairs` item is a `"KIND=N"` string. Raises `ValueError` (operator error,
    the CLI maps it to a clean contract-error exit, never a traceback) on a malformed
    pair: no `=`, an empty kind, or a non-int / negative N. An empty/None list → {}.
    These OVERLAY the config-declared budgets at the call boundary (a `--class-budget`
    wins over a `[[concurrency_class]]` of the same name — the explicit operator flag
    beats the declared default)."""
    out: dict[str, int] = {}
    for raw in pairs or ():
        if "=" not in raw:
            raise ValueError(
                f"--class-budget must be KIND=N, got {raw!r} (no '=')")
        kind, _, val = raw.partition("=")
        kind = kind.strip()
        if not kind:
            raise ValueError(f"--class-budget {raw!r} has an empty KIND")
        try:
            n = int(val.strip())
        except ValueError:
            raise ValueError(
                f"--class-budget {raw!r}: N must be an integer, got {val.strip()!r}"
            ) from None
        if n < 0:
            raise ValueError(f"--class-budget {raw!r}: N must be ≥ 0, got {n}")
        out[kind] = n
    return out


# ---------------------------------------------------------------------------
# The call-boundary projection — turn an OPEN pool into `auto_pick_order` rows
# (docs/97 / docs/110 Phase 3). This is the "modular dynamic queue" primitive:
# the host hands a `TopPickablePlanBinding` (its pool of plan ids + a `derive_tree`
# callback), this expands it into the exact `[(plan_id, kind, tree)]` ladder the
# arbiter already walks, and `arbiter.arbitrate(auto_pick_order=<rows>,
# class_budgets={kind: N})` does the disjointness + budget admission unchanged.
#
# The arbiter signature does NOT change — `auto_pick_order` is already an open,
# host-supplied list (docs/110 §model). So a class whose region comes from an open
# pool is N anonymous holders, each bound to a DISJOINT region at grab time, gated
# by the existing budget — not a fixed batch. The kernel names no host: `kind` and
# every `pool` member are opaque strings, `derive_tree` an opaque callback.
# ---------------------------------------------------------------------------


def project_to_ladder(
    binding: TopPickablePlanBinding,
    kind: str,
    *,
    skip: "frozenset[str] | set[str] | None" = None,
) -> list[tuple[str, str, list[str]]]:
    """Expand an open pool into `auto_pick_order` rows — one per pool member.

    For each plan id in ``binding.pool`` (in pool order — the host's ranking), call
    ``binding.derive_tree(pid)`` and emit a ``(pid, kind, tree)`` row. BEST-EFFORT,
    mirroring the arbiter's own `_picks`/`_safe_rank` posture: a member is SKIPPED
    when ``derive_tree`` raises, returns an empty tree, or its id is in ``skip``
    (e.g. ids already leased). PURE apart from calling the host's ``derive_tree`` —
    it does no disjointness check itself; the arbiter does that over the rows. ``kind``
    is the lane-kind the rows lease under (the same key ``class_budgets`` is keyed
    on), so the budget gate bites on this pool. Returns the rows in pool order; the
    arbiter (optionally via a ``rank_key``) chooses among the disjoint ones."""
    skip_set = set(skip or ())
    rows: list[tuple[str, str, list[str]]] = []
    for pid in binding.pool:
        if pid in skip_set:
            continue
        try:
            tree = list(binding.derive_tree(pid) or [])
        except Exception:
            # A host deriver that throws on one plan must not sink the whole pool —
            # drop that member and keep the others reachable (the durability seam).
            continue
        if not tree:
            continue
        rows.append((pid, kind, tree))
    return rows


# The marker an open-pool caller substitutes for the arbiter's generic
# ladder-exhausted refuse when the pool produced rows but every one overlapped a
# live lease. Kept here (next to `project_to_ladder`) so the caller-side
# re-labeling and the projection stay one concept; the arbiter is untouched.
NO_FREE_REGION_TOKEN = "NO_FREE_REGION"


def is_no_free_region_refuse(
    decision: object, *, projected_rows: int, budget_exhausted: bool
) -> bool:
    """True iff an open-pool arbitrate refused because no DISJOINT region remained.

    The arbiter, handed a non-empty `auto_pick_order` whose every row collides with a
    live lease, returns its generic ladder-exhausted refuse — correct, but an open
    pool wants the more specific `NO_FREE_REGION` (the pool is full of contended
    regions, not empty of work). This classifier lets the CALLER re-label without
    touching the arbiter: a refuse, where the projection produced ≥1 row AND the
    cause was NOT the class budget (that has its own `CLASS_BUDGET_EXHAUSTED`
    token). PURE — reads only the decision's outcome + the two counts the caller
    already holds."""
    outcome = getattr(decision, "outcome", None)
    if outcome is None and isinstance(decision, dict):
        outcome = decision.get("outcome")
    if outcome != "refuse":
        return False
    if budget_exhausted:
        return False  # CLASS_BUDGET_EXHAUSTED owns that case
    return projected_rows > 0
