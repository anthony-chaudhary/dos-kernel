"""The block-reason registry — the closed refusal vocabulary, *as data*.

This is the hackability seam for the kernel's single most-important syscall
(dispatch-os-vision §6 ranks structured refusal first): the closed
`reason_class` set a no-pick / blocked verdict may carry. Before this module the
set was a hardcoded enum in `dos.wedge_reason`; adding a reason meant editing the
package. That is the same mechanism/policy coupling the lane taxonomy already
broke — `LaneTaxonomy` lifted the job repo's hardcoded `_CLUSTERS` constants into
per-workspace `SubstrateConfig` data so "the arbiter never mentions a domain lane
name." This module does the same for reasons: the *mechanism* (emit / verify /
refuse / man, all keyed on a reason's fields) lives here; the *set of reasons* is
per-workspace data a host declares.

Why a registry and not a runtime-mutable enum
==============================================

The load-bearing invariant the kernel exists to protect is that every reason is
**simultaneously emittable, verifiable, and refusable** — the lockstep the
`wedge_reason` ↔ `picker_oracle` test pins, and the completeness rail DOM's
`man --check` wants ("no runtime name without a definition"). A monkeypatched
enum would silently re-open the `UNCLASSIFIED` prose-drift the kernel was built
to close. So hackability is NOT "mutate the enum at runtime"; it is "**declare
your closed set once, as data, and let every consumer derive from that single
declaration.**" A `ReasonRegistry` is exactly one such declaration: closed (you
can enumerate it), verifiable (every entry carries its category + refusal-ness +
fix), and projectable (the `man` renderer reads these fields directly).

The shape
=========

  * `ReasonSpec` — one reason as data: its token, the coarse `category` it rolls
    up to (the `picker_oracle.NoPickCause` value string), whether carrying it
    means *refuse* (route to /replan) vs *advisory*, and the curated `fix` /
    `see_also` text the man-page projects. Co-locating `fix`/`see_also` with the
    token (rather than in a separate doc) is DOM Design-rule 1: the one bit of
    curated prose lives beside the symbol so it cannot drift away from it.
  * `ReasonRegistry` — a closed, ordered set of `ReasonSpec`s with lookup +
    membership + category-map + refusal-set projections. Immutable once built
    (`extend()` returns a NEW registry — you compose, you don't mutate), so the
    "closed set" property holds: a process's active registry is a value, not a
    mutable global a plugin can scribble on mid-run.

`BASE_REASONS` is the built-in registry — the seven reasons the job spine shipped
as a closed enum, reproduced verbatim so `dos.wedge_reason` stays byte-compatible
and the existing lockstep test passes unchanged. A workspace that wants its own
reasons calls `BASE_REASONS.extend([...])` (or declares them in `dos.toml`, which
the loader turns into the same `extend` call) and installs the result on its
`SubstrateConfig`.

Pure stdlib — no third-party imports, no I/O — so `wedge_reason` / `picker_oracle`
/ the man renderer can all import it as a leaf, exactly as they import the old
`wedge_reason` enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# The coarse categories a reason rolls up to. These string values MUST be members
# of `picker_oracle.NoPickCause` (the oracle maps a reason onto its verification
# branch by this string) — the lockstep the refusal-plane test pins. Kept as bare
# strings here, not an import of `NoPickCause`, so this module stays a leaf with
# zero `dos`-internal deps (the same circular-import dodge `wedge_reason` used).
KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "TRUE_DRAIN",
    "OPERATOR_GATE",
    "STALE_CLAIM",
    "MISROUTE",
    "UNCLASSIFIED",
})

# The category an unknown / undeclared token classifies as. A token observed in
# the wild that is NOT in the active registry surfaces as this — the drift signal
# the `--check` rail turns into a CI failure (it is a bug to add, not tolerate).
UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True)
class ReasonSpec:
    """One block reason, as data. The unit a workspace declares to add a reason.

    Fields:
      token     — the `reason_class` string a no-pick / blocked verdict carries
                  (canonical UPPER_SNAKE; the registry normalizes case on lookup).
      category  — the coarse `picker_oracle.NoPickCause` value this rolls up to
                  (must be in `KNOWN_CATEGORIES`; the registry validates).
      refusal   — True ⇒ a verdict carrying this token must NOT be rendered
                  (route to /replan); False ⇒ advisory-only (deferred-but-valid).
                  Defaults True: a no-pick reason is a refusal unless declared
                  otherwise, matching today's "all reasons refuse" behavior.
      fix       — one-line operator-facing remedy sketch (the man-page TYPICAL FIX
                  line). Curated text, co-located with the token by design.
      see_also  — man-page SEE ALSO pointers (other reasons / lanes / oracles).
      summary   — one-line gloss of what the reason MEANS (the man-page NAME line
                  continuation). Optional; falls back to the token.
    """

    token: str
    category: str
    refusal: bool = True
    fix: str = ""
    see_also: tuple[str, ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise ValueError("ReasonSpec.token must be a non-empty string")
        if self.category not in KNOWN_CATEGORIES:
            raise ValueError(
                f"ReasonSpec {self.token!r} has category {self.category!r}, "
                f"which is not a known NoPickCause value {sorted(KNOWN_CATEGORIES)}. "
                f"A reason must roll up to a category the oracle can verify against."
            )

    @property
    def key(self) -> str:
        """The normalized lookup key (UPPER, stripped) — what `coerce` matches."""
        return self.token.strip().upper()


@dataclass(frozen=True)
class ReasonRegistry:
    """A closed, ordered set of `ReasonSpec`s — the active refusal vocabulary.

    Immutable: `extend()` returns a NEW registry. A process's active registry is
    therefore a value (installed on the `SubstrateConfig`), never a mutable global
    a plugin scribbles on — which is what keeps "closed set" a real property and
    not a hope. Lookup is case-insensitive and whitespace-tolerant (a hand-authored
    envelope written during a prose→data transition still classifies).
    """

    specs: tuple[ReasonSpec, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for s in self.specs:
            if s.key in seen:
                raise ValueError(
                    f"duplicate reason token {s.token!r} in registry — a reason "
                    f"is declared exactly once (later declarations would shadow "
                    f"silently, the drift this registry exists to forbid)"
                )
            seen.add(s.key)

    # -- lookup ------------------------------------------------------------
    def get(self, token: str | None) -> ReasonSpec | None:
        """The `ReasonSpec` for `token`, or None if not a member of this set."""
        if not token:
            return None
        k = token.strip().upper()
        for s in self.specs:
            if s.key == k:
                return s
        return None

    def is_known(self, token: str | None) -> bool:
        return self.get(token) is not None

    def tokens(self) -> tuple[str, ...]:
        """Every declared token, in declaration order."""
        return tuple(s.key for s in self.specs)

    # -- projections (what the consumers read) -----------------------------
    def category_for(self, token: str | None) -> str:
        """Map a token onto its category value; UNCLASSIFIED for an unknown one.

        Forward-compatible by construction: a brand-new label does not crash a
        consumer, it classifies as drift until declared (the `--check` rail is
        what turns that drift into a loud CI failure rather than a silent one).
        """
        spec = self.get(token)
        return spec.category if spec is not None else UNCLASSIFIED

    def is_refusal(self, token: str | None) -> bool:
        """True iff a verdict carrying `token` must NOT be rendered.

        An unknown token is refused conservatively — a no-pick envelope with an
        unrecognised reason_class is still a no-pick, and launching against it is
        the exact hazard. A *known* token honors its declared `refusal` flag, so a
        workspace can declare an advisory-only reason (refusal=False).
        """
        spec = self.get(token)
        if spec is None:
            return True
        return spec.refusal

    def category_map(self) -> dict[str, str]:
        """`{token: category}` for every declared reason. The dict `picker_oracle`
        derives its `REASON_CLASS_MAP` from, so a declared reason is verifiable the
        moment it is emittable (no second map to keep in sync)."""
        return {s.key: s.category for s in self.specs}

    def refusal_tokens(self) -> frozenset[str]:
        """The subset of tokens whose verdicts must route to /replan."""
        return frozenset(s.key for s in self.specs if s.refusal)

    # -- composition (the hackability verb) --------------------------------
    def extend(self, more: Iterable[ReasonSpec]) -> "ReasonRegistry":
        """Return a NEW registry with `more` appended. The one way to add reasons.

        Raises if any new token collides with an existing one (the same
        declared-exactly-once guard `__post_init__` enforces) — a workspace
        re-declaring a built-in is a mistake to surface, not to silently honor.
        To *change* a built-in (e.g. flip its refusal flag), build a fresh
        registry from the specs you want rather than extend-over-shadow.
        """
        return ReasonRegistry(specs=tuple(self.specs) + tuple(more))


# ---------------------------------------------------------------------------
# The built-in registry — the seven reasons the job spine shipped as a closed
# enum (`dos.wedge_reason.WedgeReason`), reproduced verbatim (token, category,
# refusal-ness) so `wedge_reason` stays byte-compatible and the lockstep test
# passes unchanged. The `summary`/`fix`/`see_also` text is lifted from the enum's
# own comment blocks — the man-page content the DOM plan notes "already exists as
# structured symbols," now co-located as fields. A foreign workspace ignores this
# and builds its own (or `extend`s it).
#
# Categories mirror `picker_oracle.NoPickCause` values exactly:
#   LANE_DRAINED                          -> TRUE_DRAIN
#   LANE_BLOCKED_ON_SOAK_GATED_PHASES     -> OPERATOR_GATE
#   LANE_LEASE_HELD_BY_LIVE_DISPATCH_LOOP -> OPERATOR_GATE  (LEASE_HELD alias)
#   LANE_ALL_INFLIGHT_OR_DEFERRED         -> STALE_CLAIM    (INFLIGHT alias)
#   LANE_ALL_SHIPPED_INFLIGHT_OR_STALE_STAMP -> STALE_CLAIM (INFLIGHT alias)
#   LANE_ALL_BLOCKED_OR_STALE_STAMP       -> OPERATOR_GATE
#   LANE_BLOCKED_ON_OPERATOR_DECISION     -> OPERATOR_GATE
# ---------------------------------------------------------------------------
BASE_REASONS = ReasonRegistry(specs=(
    ReasonSpec(
        token="LANE_DRAINED",
        category="TRUE_DRAIN",
        refusal=True,
        summary="0 plans + 0 findings — the lane is genuinely drained.",
        fix="Nothing to do — the backlog is empty. /replan to refill if you expect work.",
        see_also=("oracle picker_oracle",),
    ),
    ReasonSpec(
        token="LANE_BLOCKED_ON_SOAK_GATED_PHASES",
        category="OPERATOR_GATE",
        refusal=True,
        summary="Lane has pickable phases but all remaining gate on an open soak window.",
        fix="Wait for the soak window to close, or /replan to re-shape the lane.",
        see_also=("meta gates_on_soak", "meta soak_until", "oracle picker_oracle"),
    ),
    ReasonSpec(
        token="LANE_LEASE_HELD_BY_LIVE_DISPATCH_LOOP",
        category="OPERATOR_GATE",
        refusal=True,
        summary="A foreign, live /dispatch-loop holds this cluster's lane lease.",
        fix="Wait for the holding loop to release the lease (racing it is the "
            "collision the arbiter prevents). The reason string carries the holder.",
        see_also=("lane <holder>", "oracle picker_oracle"),
    ),
    ReasonSpec(
        token="LANE_ALL_INFLIGHT_OR_DEFERRED",
        category="STALE_CLAIM",
        refusal=True,
        summary="Remaining phases are all soft-claimed in-flight by a sibling, "
                "and/or deferred by the plan body's own gate.",
        fix="Wait for the sibling packet to ship/release, or /replan to re-rank.",
        see_also=("oracle picker_oracle",),
    ),
    ReasonSpec(
        token="LANE_ALL_SHIPPED_INFLIGHT_OR_STALE_STAMP",
        category="STALE_CLAIM",
        refusal=True,
        summary="Remaining phases are a mix of shipped-but-unstamped + in-flight "
                "+ stale-stamped (the apply/tailor 'already done or drifting' shape).",
        fix="/replan to reconcile the stale SHIPPED stamps, then re-dispatch.",
        see_also=("oracle ship_oracle",),
    ),
    ReasonSpec(
        token="LANE_ALL_BLOCKED_OR_STALE_STAMP",
        category="OPERATOR_GATE",
        refusal=True,
        summary="Every remaining phase is blocked, or its stamp is drifted "
                "(soak + stamp-drift co-occur).",
        fix="/replan to reconcile stamps and surface the blocks.",
        see_also=("oracle ship_oracle",),
    ),
    ReasonSpec(
        token="LANE_BLOCKED_ON_OPERATOR_DECISION",
        category="OPERATOR_GATE",
        refusal=True,
        summary="Lane is blocked on an unanswered operator decision; the routing "
                "finding is already soft-claimed by a sibling. No automation clears it.",
        fix="Answer the open decision (it surfaces once), then /replan.",
        see_also=("oracle picker_oracle",),
    ),
    # ADM Phase 2 — the typed refuse the SELF_MODIFY admission predicate emits
    # (`dos.self_modify.SelfModifyPredicate`). A lease whose tree includes the
    # orchestrator's own running code (`arbiter.py`, the classifiers, the reason
    # vocabulary, the config seam) is a misrouted lease — work aimed at the kernel
    # adjudicating it rather than at userland — so it rolls up to MISROUTE. Refusal
    # (route the operator to /replan or to --force if the kernel edit is deliberate).
    # Declared here so the arbiter-emitted reason is simultaneously emittable,
    # verifiable (`category_for`), refusable (`is_refusal`), and `dos man wedge
    # SELF_MODIFY`-documented — the Axis-1 completeness rail the predicate rides.
    ReasonSpec(
        token="SELF_MODIFY",
        category="MISROUTE",
        refusal=True,
        summary="Lease tree includes the orchestrator's own running code — a live "
                "loop must not rewrite the kernel that is adjudicating it.",
        fix="Edit kernel runtime files OUTSIDE a live dispatch loop, or pass "
            "--force to override (the operator's explicit 'I am deliberately "
            "editing the kernel between loop runs').",
        # The see_also points at the EXCLUSIVE-lane concept (the kernel's own
        # region runs alone) via the `dos man lane` verb rather than a specific
        # lane NAME — a generic registry must not name a lane a foreign workspace
        # may not declare (the `dos man lane` ref resolves on every workspace; a
        # bare `lane orchestration` dangled on any taxonomy without that lane,
        # which `config_lint.REASON_SEE_ALSO_DANGLES` correctly flagged).
        see_also=("dos man lane", "dos arbitrate"),
    ),
    # docs/115 primitive 4 — the typed refuse that surfaces `durable_schema`'s
    # refuse-don't-guess floor through the closed refusal vocabulary. When a reader
    # meets a durable record (intent ledger, WAL, env-print) tagged at a NON-additive
    # version this kernel predates (`durable_schema.classify` → UNREADABLE_NEWER), the
    # sound answer is to REFUSE the record, never best-effort-parse a shape it does
    # not know. A record this kernel cannot soundly read is work it must route
    # elsewhere (a newer kernel / a migration), not guess at — so it rolls up to
    # MISROUTE, the SELF_MODIFY sibling. Declared here so the floor is simultaneously
    # emittable, verifiable (`category_for`), refusable (`is_refusal`), and
    # `dos man wedge SCHEMA_UNREADABLE`-documented. The carried verdict
    # (`ReadabilityVerdict`: family + understood-ceiling + record-version) is the
    # remedy-with-the-refusal — the MCP `UnsupportedProtocolVersionError(-32004)`
    # `{supported, requested}` shape, which DOS's durable_schema predates.
    ReasonSpec(
        token="SCHEMA_UNREADABLE",
        category="MISROUTE",
        refusal=True,
        summary="A durable record is tagged at a schema version this kernel predates "
                "— refuse-don't-guess (never best-effort-parse an unknown shape).",
        fix="Upgrade the kernel to one that understands this record's schema "
            "version, or run a migration. The refusal carries the family + the "
            "version this kernel reads + the record's version (the supported set).",
        see_also=("durable_schema", "resume", "dos man wedge SELF_MODIFY"),
    ),
    # docs/104 §4 (control-flow arm) — the typed refuse the arbiter emits when an
    # EXPLICIT keyword request names a lane the workspace's taxonomy never heard of
    # and it resolves to no tree. Auto-pick's license is "the caller expressed NO
    # preference"; a named keyword is a preference the kernel cannot place, so
    # silently substituting a different free lane (the old degrade-to-bare) is the
    # refuse-don't-guess violation turned inward — the lease would describe the
    # wrong region and disjointness would guard the wrong tree. Work aimed at a lane
    # that does not exist here is misrouted (vs SELF_MODIFY = misrouted to the
    # kernel, SCHEMA_UNREADABLE = misrouted to a newer kernel) → MISROUTE. Declared
    # here so the arbiter-emitted reason is simultaneously emittable, verifiable
    # (`category_for`), refusable (`is_refusal`), and `dos man wedge UNKNOWN_LANE`-
    # documented — the same completeness rail SELF_MODIFY/SCHEMA_UNREADABLE ride.
    ReasonSpec(
        token="UNKNOWN_LANE",
        category="MISROUTE",
        refusal=True,
        summary="An explicit keyword request named a lane this workspace's taxonomy "
                "does not contain — the kernel refuses to guess a substitute "
                "(auto-pick only chooses when the caller expresses no preference).",
        fix="Pass a lane the workspace knows as --lane (see the refusal's "
            "known-lane list or `dos man lane`), run a bare invocation to auto-pick "
            "any free lane, or register the lane in dos.toml.",
        see_also=("lane", "dos arbitrate", "dos man wedge SELF_MODIFY"),
    ),
    # docs/97 §model — the typed refuse a `TopPickablePlan` region source emits when
    # an OPEN pool yields no admissible region: every candidate plan's derived tree
    # OVERLAPS a live lease (the pool is full of contended regions, not empty of
    # work). This is the open-pool sibling of the priority-ladder-exhausted refuse —
    # distinct from CLASS_BUDGET_EXHAUSTED (the class is at its budget; here the
    # budget has room but no disjoint region remains) and from a true DRAIN (the work
    # exists, the regions are simply all taken). It rolls up to TRUE_DRAIN: the right
    # lever is to WAIT for a holder to release a region, never to /replan (there is
    # nothing to refill — the pool is full, not empty). Declared here so the
    # arbiter/region-source-emitted reason is simultaneously emittable, verifiable
    # (`category_for`), refusable (`is_refusal`), and `dos man wedge NO_FREE_REGION`-
    # documented — the same completeness rail SELF_MODIFY/UNKNOWN_LANE ride. It lives
    # in BASE_REASONS, not dos.toml, because the kernel's own region-source admission
    # emits it: a dos.toml-only token would be undeclared on a foreign workspace whose
    # arbiter still emits it (the SELF_MODIFY/UNKNOWN_LANE rule).
    ReasonSpec(
        token="NO_FREE_REGION",
        category="TRUE_DRAIN",
        refusal=True,
        summary="An open-pool region source (TopPickablePlan) found no candidate "
                "whose derived tree is disjoint from every live lease — the pool is "
                "full of contended regions, not empty of work.",
        fix="Wait for a holder to release a region (the pool is FULL, not empty — do "
            "NOT /replan, there is nothing to refill), or widen the pool so a "
            "disjoint region exists. Distinct from CLASS_BUDGET_EXHAUSTED (the class "
            "has budget room here; no disjoint region remains).",
        see_also=("dos arbitrate", "dos man wedge CLASS_BUDGET_EXHAUSTED",
                  "dos man lane"),
    ),
))


# ---------------------------------------------------------------------------
# The declarative on-ramp: read reasons out of a workspace's `dos.toml`.
#
# `dos init` scaffolds a `dos.toml`; this is the function that turns its
# `[reasons.*]` table into a `ReasonRegistry` extending `BASE_REASONS`. It is the
# chosen "no-code" path (operator decision): a host adds a block reason by editing
# data, not by importing the package. The TOML shape mirrors the dataclass:
#
#     [reasons.LANE_PARKED_FOR_BUDGET]
#     category = "OPERATOR_GATE"      # required; must be a KNOWN_CATEGORIES value
#     refusal  = true                 # optional, default true
#     summary  = "lane parked: monthly token budget hit"
#     fix      = "raise the budget cap or /replan"
#     see_also = ["meta budget", "oracle picker_oracle"]
#
# Behavioral hooks (custom renderers / admission predicates) are NOT declarable in
# TOML — those load via Python packaging entry_points (see docs/HACKING.md). TOML
# is for the data axes (reasons, and later lanes/paths); entry_points for code.
# ---------------------------------------------------------------------------


def specs_from_table(table: dict) -> list[ReasonSpec]:
    """Turn a parsed `[reasons]` TOML table into a list of `ReasonSpec`.

    `table` is `{token: {category, refusal?, summary?, fix?, see_also?}}` — the
    shape `tomllib.load(...)["reasons"]` yields. Pure (no I/O); raises
    `ValueError` (via `ReasonSpec.__post_init__`) on a bad category or empty
    token, so a malformed declaration fails loudly at load instead of silently
    classifying as drift later.
    """
    specs: list[ReasonSpec] = []
    for token, body in (table or {}).items():
        if not isinstance(body, dict):
            raise ValueError(
                f"[reasons.{token}] must be a table, got {type(body).__name__}"
            )
        if "category" not in body:
            raise ValueError(f"[reasons.{token}] is missing required `category`")
        see = body.get("see_also") or ()
        if isinstance(see, str):
            see = (see,)
        specs.append(ReasonSpec(
            token=str(token),
            category=str(body["category"]),
            refusal=bool(body.get("refusal", True)),
            fix=str(body.get("fix", "")),
            see_also=tuple(str(s) for s in see),
            summary=str(body.get("summary", "")),
        ))
    return specs


def load_from_toml(path: Path | str, *, base: ReasonRegistry = BASE_REASONS) -> ReasonRegistry:
    """Build a `ReasonRegistry` from a `dos.toml`'s `[reasons]` table.

    Returns `base` unchanged when the file is absent, has no `[reasons]` table, or
    `tomllib` is unavailable (Python < 3.11 with no `tomli`) — the declarative
    path is purely additive, so a missing/empty config degrades to the built-in
    set, never an error. A *present but malformed* `[reasons]` table raises
    (`specs_from_table`), because a host that declared a reason wrong wants that
    surfaced, not swallowed.
    """
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
    # `utf-8-sig` transparently strips a UTF-8 BOM (PowerShell's default `utf8`
    # encoding writes one; raw `tomllib.load(rb)` chokes on it and would silently
    # drop a valid declared table — see the same fix in `config._load_toml_table`).
    data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
    table = data.get("reasons")
    if not isinstance(table, dict) or not table:
        return base
    return base.extend(specs_from_table(table))
