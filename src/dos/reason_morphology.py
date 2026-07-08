"""The reason-class *morphology* — rung 2 of the recognizer ladder, as data.

This is the hackability seam that closes the deeper half of the picker oracle's
blind spot (`docs/105`). `picker_oracle.resolve_cause` maps an emitted
`reason_class` token onto a closed `NoPickCause` so the oracle can grade a
NO-PICK. Its first two rungs are **exact string match** — the frozen
`REASON_CLASS_MAP` (the closed `wedge_reason` enum) and the workspace
`ReasonRegistry` (`dos.toml [reasons]`). But `reason_class` tokens are
*LLM-authored compounds* — an open, effectively-infinite set
(`PLAN_ID_COLLISION_FALSE_SHIPPED`,
`DL_LANE_STALE_STAMP_BODY_VS_META_DRIFT_PLUS_SHIP_ORACLE_SERIES_COLLISION`, …).
Exact equality over an infinite generator is brittle by construction: every novel
compound falls to `UNCLASSIFIED`, even when its category is obvious.

The tell measured on `job`'s corpus (`docs/105` §1): each token's **category is
legible in its morphology** — `*FALSE_SHIP*` is a stale-claim shape, `*OPERATOR*`
is an operator-gate, `*INFLIGHT*` is a stale-claim (the inflight alias), `*SOAK*`
/`*GATE*` is a gate. So this module is the **rung-2 recognizer**: an ordered set
of `(substring → category)` rules, declared as DATA, that classifies the legible
tail the exact rungs miss. It is the direct analogue of `stamp.StampConvention` —
the host declares *what a stale-claim token looks like in its dialect*; the kernel
keeps the closed `NoPickCause` set and every cross-check downstream of it
(`docs/76` §3's line, applied to the reason-class recognizer instead of the
ship-subject grammar).

The category strings a rule may emit are exactly `reasons.KNOWN_CATEGORIES`
(`TRUE_DRAIN` / `OPERATOR_GATE` / `STALE_CLAIM` / `MISROUTE` / `UNCLASSIFIED`),
which equal the `NoPickCause` member values by construction. A rule emitting
anything else is a host mistake surfaced loudly at load.

Pure stdlib — no third-party imports, no I/O — so `picker_oracle` imports it as a
leaf, the same way it imports `stamp` / `wedge_reason`. The aliases the GENERIC
ruleset encodes mirror the ones already documented in `reasons.py`'s built-in
`BASE_REASONS` table (`INFLIGHT → STALE_CLAIM`, `SOAK/LEASE_HELD → OPERATOR_GATE`),
so the morphological rung and the exact rung agree on category by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# The category vocabulary a morphology rule may emit. Kept as a module constant
# (not imported from `reasons`/`picker_oracle`) so this leaf stays pure-stdlib and
# dependency-free; it is pinned EQUAL to `reasons.KNOWN_CATEGORIES` by
# `tests/test_reason_morphology.py` so the two can never drift. `UNCLASSIFIED` is
# included so a rule may *deliberately* map a recognized-but-undecidable shape to
# the honest floor (rare, but legal).
KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "TRUE_DRAIN", "OPERATOR_GATE", "STALE_CLAIM", "MISROUTE", "UNCLASSIFIED",
})


@dataclass(frozen=True)
class MorphologyRule:
    """One ``(substring → category)`` rule of the rung-2 recognizer.

    Fields:
      substring
          A case-insensitive substring of the reason-class token. Matched with a
          plain ``in`` (uppercased on both sides) — NOT a regex — so a host
          declares a token *shape* (``FALSE_SHIP``), never a pattern. Empty is a
          host mistake (it would match everything); rejected at construction of
          the ruleset.
      category
          The `NoPickCause` value-string this shape maps to. Must be one of
          ``KNOWN_CATEGORIES``; anything else raises at ruleset construction (the
          closed-enum discipline — a typo'd category is surfaced, not silently
          carried as an unknown that resolves to UNCLASSIFIED anyway).
    """

    substring: str
    category: str

    def matches(self, token_upper: str) -> bool:
        """True iff this rule's (uppercased) substring occurs in ``token_upper``.

        The caller passes the already-uppercased token so a ruleset scan does not
        re-uppercase per rule. Pure."""
        return self.substring.upper() in token_upper


@dataclass(frozen=True)
class MorphologyRuleset:
    """An ORDERED set of `MorphologyRule`s — the rung-2 recognizer, as data.

    Order is load-bearing: `classify` returns the FIRST matching rule's category
    (first-match-wins), so a more-specific shape must precede a more-general one
    (e.g. ``STALE_STAMP`` before a bare ``STAMP``, or ``INFLIGHT`` before ``GATE``
    for a token like ``LANE_ALL_SHIPPED_INFLIGHT_OR_SOAK_GATED`` where the author's
    intent is the in-flight state, not the gate). Because the rung *reports itself*
    (`picker_oracle` records ``cause_source="morphological"`` and the matched
    rule), the precedence a given order encodes is auditable, never buried.

    Construction validates every rule (non-empty substring, known category) so a
    malformed ruleset fails at load — the same loud-on-malformed posture
    `stamp.convention_from_table` takes.
    """

    rules: tuple[MorphologyRule, ...] = ()

    def __post_init__(self) -> None:
        for r in self.rules:
            if not r.substring or not r.substring.strip():
                raise ValueError(
                    "MorphologyRule.substring must be a non-empty string "
                    "(an empty substring would match every token)"
                )
            if r.category not in KNOWN_CATEGORIES:
                raise ValueError(
                    f"MorphologyRule.category {r.category!r} is not a known "
                    f"category; must be one of {sorted(KNOWN_CATEGORIES)}"
                )

    def classify(self, token: str | None) -> tuple[str, str] | None:
        """Return ``(category, matched_substring)`` for the first matching rule.

        Returns ``None`` when no rule matches (the caller then falls through to the
        ``UNCLASSIFIED`` floor — rung 3). The matched substring is returned so the
        caller can record *which* rule fired (the self-reporting `docs/105` §3.1
        requires). Pure; ``None``/empty token → ``None``.
        """
        if not token:
            return None
        token_upper = token.upper().strip()
        if not token_upper:
            return None
        for r in self.rules:
            if r.matches(token_upper):
                return (r.category, r.substring.upper())
        return None

    # -- serialization (parity with StampConvention; crosses no subprocess today
    #    but kept symmetric so a future grep-rung-style boundary is free) --------
    def to_list(self) -> list[dict]:
        """Plain-data form — a JSON-serializable ordered list of rule dicts."""
        return [{"substring": r.substring, "category": r.category} for r in self.rules]

    @classmethod
    def from_list(cls, data: object) -> "MorphologyRuleset":
        """Rebuild from `to_list` form. Tolerant of a missing/empty value (→ empty
        ruleset); a malformed entry raises via `__post_init__` / `_rule_from`."""
        if not data:
            return cls(())
        if not isinstance(data, (list, tuple)):
            raise ValueError(
                f"[reasons.morphology] must be a list of rules, got "
                f"{type(data).__name__}"
            )
        return cls(tuple(_rule_from(item) for item in data))


def _rule_from(item: object) -> MorphologyRule:
    """Coerce one parsed TOML/JSON entry into a `MorphologyRule`, raising on shape.

    Accepts a ``{substring, category}`` table (the canonical form) or a
    two-element ``[substring, category]`` pair (a terse TOML form). Anything else
    is a host mistake surfaced loudly, mirroring `stamp._str_tuple`'s posture.
    """
    if isinstance(item, dict):
        try:
            sub = item["substring"]
            cat = item["category"]
        except KeyError as e:
            raise ValueError(
                f"[reasons.morphology] rule {item!r} is missing required key {e}"
            ) from None
    elif isinstance(item, (list, tuple)) and len(item) == 2:
        sub, cat = item[0], item[1]
    else:
        raise ValueError(
            f"[reasons.morphology] rule must be a {{substring, category}} table or "
            f"a [substring, category] pair, got {item!r}"
        )
    if not isinstance(sub, str) or not isinstance(cat, str):
        raise ValueError(
            f"[reasons.morphology] rule {item!r}: substring and category must both "
            f"be strings"
        )
    # `MorphologyRule` is validated for emptiness/known-category by the ruleset's
    # __post_init__; build it here and let that fire.
    return MorphologyRule(substring=sub, category=cat)


# ---------------------------------------------------------------------------
# The GENERIC, domain-free default ruleset — the kernel ships this so EVERY host
# gets the legible-tail recovery out of the box, with NO host lanes baked in
# (`APPLY_LANE_*` / `TAILOR_LANE_*` are a host's; they are NOT here — a host
# declares those via `dos.toml [reasons.morphology]`). Every substring below is a
# DOMAIN-FREE distrust shape, and every category matches the alias intuitions
# already encoded in `reasons.BASE_REASONS` (so rung-2 agrees with rung-1).
#
# Order is most-specific-first. The category for each shape:
#   *FALSE_SHIP* / *FALSE_SHIPPED*  -> STALE_CLAIM   (claims-shipped-but-not: the
#                                      diagnostic picker-bug shape — a stamp the
#                                      ship oracle would refuse)
#   *STALE_STAMP* / *STAMP_DRIFT*   -> STALE_CLAIM   (a stamp present but stale)
#   *OPERATOR*                      -> OPERATOR_GATE (operator-attended / decision-pending)
#   *SOAK*                          -> OPERATOR_GATE (soak window open)
#   *INFLIGHT* / *IN_FLIGHT*        -> STALE_CLAIM   (the inflight alias — matches
#                                      reasons.py: LANE_ALL_INFLIGHT_* -> STALE_CLAIM)
#   *LEASE_HELD*                    -> OPERATOR_GATE (held by a live loop — the LEASE_HELD alias)
#   *GATE*                          -> OPERATOR_GATE (a generic gate; AFTER soak/inflight
#                                      so a *...INFLIGHT...GATE* token reads as inflight)
#   *DRAIN* / *DRAINED*             -> TRUE_DRAIN    (the lane is drained — genuinely nothing)
#   *NO_DISPATCHABLE* / *NOTHING*   -> TRUE_DRAIN    (no dispatchable phase)
#   *MISROUTE* / *MIS_ROUT*         -> MISROUTE      (finding routed to the wrong lane)
#
# Note `STALE_STAMP` precedes nothing it would shadow, but `DRAIN` is placed AFTER
# the stamp/inflight shapes so `STALE_STAMP_LANE_DRAINED` reads as STALE_CLAIM (the
# stamp is the salient defect), not TRUE_DRAIN. This precedence is the auditable
# judgment `docs/105` §3.2 calls out; it is DATA precisely so a host can reorder it.
# ---------------------------------------------------------------------------
GENERIC_REASON_MORPHOLOGY = MorphologyRuleset((
    MorphologyRule("FALSE_SHIPPED", "STALE_CLAIM"),
    MorphologyRule("FALSE_SHIP", "STALE_CLAIM"),
    MorphologyRule("STALE_STAMP", "STALE_CLAIM"),
    MorphologyRule("STAMP_DRIFT", "STALE_CLAIM"),
    MorphologyRule("STAMPING_DRIFT", "STALE_CLAIM"),
    MorphologyRule("OPERATOR", "OPERATOR_GATE"),
    MorphologyRule("SOAK", "OPERATOR_GATE"),
    MorphologyRule("INFLIGHT", "STALE_CLAIM"),
    MorphologyRule("IN_FLIGHT", "STALE_CLAIM"),
    MorphologyRule("LEASE_HELD", "OPERATOR_GATE"),
    MorphologyRule("GATE", "OPERATOR_GATE"),
    MorphologyRule("DRAINED", "TRUE_DRAIN"),
    MorphologyRule("DRAIN", "TRUE_DRAIN"),
    MorphologyRule("NO_DISPATCHABLE", "TRUE_DRAIN"),
    MorphologyRule("MISROUTE", "MISROUTE"),
    MorphologyRule("MIS_ROUT", "MISROUTE"),
))


# The EMPTY ruleset — an explicit "no morphological rung" value. A workspace that
# wants only the two exact rungs (the pre-`docs/105` behavior) can set this, and a
# `[reasons.morphology] = []` table degrades to it. Distinct from
# GENERIC_REASON_MORPHOLOGY: empty means "rung 2 is off", generic means "rung 2 with
# the kernel's domain-free defaults".
NO_REASON_MORPHOLOGY = MorphologyRuleset(())


# ---------------------------------------------------------------------------
# The declarative on-ramp: read a `[reasons.morphology]` list out of dos.toml.
# Mirrors `stamp.load_from_toml` / `reasons.load_from_toml`: a present list
# OVERRIDES the base (declaring your morphology means declaring it, not appending
# to the kernel's); absent/empty degrades to the base; present-but-malformed
# raises. The TOML shape (an array of tables, the natural TOML form for an ordered
# list of rules):
#
#     [[reasons.morphology]]
#     substring = "APPLY_LANE_BLOCKED_MESH"   # a host's own shape
#     category  = "OPERATOR_GATE"
#
#     [[reasons.morphology]]
#     substring = "RESPAWN"
#     category  = "TRUE_DRAIN"
# ---------------------------------------------------------------------------


def load_from_toml(
    path: Path | str, *, base: MorphologyRuleset = GENERIC_REASON_MORPHOLOGY
) -> MorphologyRuleset:
    """Build a `MorphologyRuleset` from a `dos.toml`'s `[reasons.morphology]` list.

    Returns ``base`` unchanged when the file is absent, has no
    `[reasons.morphology]` key, or `tomllib` is unavailable — the declarative path
    is additive, so a missing config degrades to the supplied base (the kernel's
    generic ruleset), never an error. A *present but malformed* list raises
    (`from_list` / `_rule_from`), because a host that declared its morphology wrong
    wants it surfaced. Mirrors `stamp.load_from_toml` exactly.

    Override (not merge) semantics: a host that declares a `[reasons.morphology]`
    gets EXACTLY its rules, not its rules plus the kernel's generic ones. To keep
    the generic shapes a host re-lists the ones it wants — declaring your
    morphology means declaring it. (A future `extend = true` key could add merge
    semantics; out of scope for `docs/105`.)
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
    reasons_tbl = data.get("reasons")
    if not isinstance(reasons_tbl, dict):
        return base
    morph = reasons_tbl.get("morphology")
    if morph is None:
        return base
    # An explicit empty list means "turn rung 2 off" → NO_REASON_MORPHOLOGY, not base.
    if isinstance(morph, (list, tuple)) and len(morph) == 0:
        return NO_REASON_MORPHOLOGY
    return MorphologyRuleset.from_list(morph)
