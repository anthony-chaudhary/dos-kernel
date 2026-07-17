"""PG — the precursor-presence verdict: *did the mandated lookup FIRE before this mutation?*

docs/147 (the precursor-presence gate; greenlit by docs/146's slice survey). `arg_provenance`
(docs/143) asks *"did the model MINT this id, or RESOLVE it?"* — a check on
**provenance-of-a-string** that vanishes on a strong model (a model that reads-before-it-writes
mints nothing). docs/146 found one more byte-clean question of the same shape:

  > does a tool whose name is on a config-declared mandated-precursor set produce ANY result in
  > env-authored bytes before this mutating call's stream index?

That is **provenance-of-a-precursor-PRESENCE** — a pure byte question about *env-authored* bytes
(the gym MCP server authored the result, recording that the precursor tool fired; the judged
agent did not author the *existence* of that result). So it sidesteps the **mirror-verifier
trap** (docs/141, docs/143 §5a) for the same reason `arg_provenance` does: it needs no answer
key, no held-out state, and **no self-authored satisfaction predicate** ("did the precursor
result *authorize* this action on this resource?" — the forgeable-in-the-agent's-favor question
this module must never ask). It attacks the named "Missing Prerequisite Lookup" /
"Cascading State Propagation" failure modes that feed the Policy/Permission verifier slice.

The dead-line — firing, NEVER adjudication (docs/147 §3)
=======================================================

This module checks *only* that a mandated-precursor-named tool produced **some** result earlier
in the call stream. It deliberately does **not**, and structurally **cannot**, ask:

  * **resource-identity** — "was the precursor about the SAME record the mutation touches?"
  * **clause-satisfaction** — "did the precursor result return a value that AUTHORIZES the act?"
  * **ordering beyond stream-index** — "does the precursor LOGICALLY precede this in the policy?"

Each of those binds the precursor *to this action* — a **provenance-of-a-RELATION** the agent
narrates from agent-visible prose, forgeable in its favor. The verdict's `REFUTED` therefore
means ONLY "a mandated precursor for this tool produced no result in the stream"
(presence-absence, a byte fact), never the OS-witnessed disconfirmation `evidence`'s `REFUTED`
carries for the `os_acceptance` driver. The moment a consumer lets this REFUTED drive an
actuating rung harder than WARN, it has crossed into the mirror-verifier — which is why the
intervention map below emits **only WARN** and has no harder rung to reach.

Why this is NOT `arg_provenance`'s `_build_env`/`_component_found` fold
======================================================================

Those fold over a prior result's *text tokens* — they answer "does this id-component trace to
env bytes?" A precursor tool's NAME is generally absent from its own result payload, so a
token-trace would systematically MISS a fired precursor (a false REFUTED). The firing question
is instead a **structural membership scan** over the call stream's `tool_name` fields:
`any(_canon(c.tool_name) in precursor_name_set for c in stream[:idx])`. This module lifts only
the *pattern* `arg_provenance`/`tool_stream` share — casefold + alias normalization + a pure
scan over an already-accumulated env corpus — not the id-matcher.

The two errors, and which one is safe
=====================================

  * **false-NO_SIGNAL** (a mutating tool absent from the grammar, so never gated) → the call
    dispatches as baseline. SAFE — no worse than not having the gate; the side-effect-suppression
    edge is simply bounded by grammar coverage (what the eval's `missed_precursor_recall`
    measures).
  * **false-REFUTED** (the precursor DID fire, but under an alias the grammar did not list) → an
    unnecessary WARN. Bounded by the `alias_map`, and even when it misses the cost is a redundant
    reminder, not a withheld call (the intervention is WARN-only, §4) — a fire on a feasible task
    is a *correct* "you have not called the mandated check" nudge, not a false-block.

So like `arg_provenance`, the whole module is tuned to **under-fire**, and its one actuation is
the least-disruptive informing rung.

⚓ Pure kernel, I/O on the edge (the dos idiom — mirrors `arg_provenance.classify_call`,
`liveness.classify`, `tool_stream.classify_stream`): `classify_call(MutatingCall, CallStream,
PrecursorGrammar, policy) -> PrecursorVerdict` is a frozen datum in, a frozen verdict out. The
caller flattens each prior tool RESULT to a `(tool_name, result_text)` pair at the boundary; the
kernel parses no JSON, reads no clock, no disk — replay-testable on frozen fixtures with zero
benchmark/LLM/MCP access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# The verdict vocabulary has ONE home — docs/121's `evidence`. A precursor check is a
# presence-of-evidence question (did a witness — the precursor result — appear?), so it
# reuses `EvidenceStance` (ATTESTED / REFUTED / NO_SIGNAL) verbatim rather than fork a
# parallel three-valued enum. NB the SEMANTICS differ: here REFUTED is presence-absence,
# NOT the OS-witnessed disconfirmation `os_acceptance` mints it for (see the module doc).
from dos.evidence import EvidenceStance

# The actuation vocabulary — the shipped intervention ladder (docs/144). This gate maps its
# stance to a rung DIRECTLY (the `tool_stream` precedent), never via `choose_intervention`,
# which is typed to a `ProvenanceVerdict` this gate does not produce.
from dos.intervention import Intervention, InterventionDecision

__all__ = [
    "PrecursorStance",
    "PrecursorPolicy",
    "DEFAULT_POLICY",
    "PriorCall",
    "CallStream",
    "MutatingCall",
    "PrecursorGrammar",
    "PrecursorVerdict",
    "classify_call",
    "precursor_intervention",
    "grammar_from_table",
    "load_from_toml",
]


# The stance is `EvidenceStance` (one home for the presence-of-evidence vocabulary).
PrecursorStance = EvidenceStance


def _canon(name: str) -> str:
    """Normalize a tool name for matching: casefold + `-`/`.`→`_` (the `dos_react`
    `_normalize_tool_name` convention, so `check-access` / `check.access` / `Check_Access`
    all canonicalize to one key)."""
    return (name or "").strip().casefold().replace("-", "_").replace(".", "_")


# ---------------------------------------------------------------------------
# Frozen inputs — the pure datum a caller gathers at the boundary and hands in.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PriorCall:
    """One prior call in the stream, as the pure datum the verdict sees.

    `tool_name` is the tool the env executed (it returned a result, recording the firing).
    `result_text` is the flattened result bytes — carried ONLY for the legibility note (a
    consumer may quote it); the firing decision keys on `tool_name` alone (a structural
    membership test, not a substring trace over `result_text` — see the module doc).
    """

    tool_name: str
    result_text: str = ""


@dataclass(frozen=True)
class CallStream:
    """The env-authored call stream accumulated before the call under scrutiny.

    `calls` is a tuple of `PriorCall` in call order — every prior tool RESULT the agent has
    seen (the same `prior_tool_results` the `arg_provenance` consult already accumulates).
    Empty (`()`) on the first call of an episode, which `classify_call` reads as "nothing
    could have fired yet → NO_SIGNAL" (the load-bearing first-call safe direction, the
    `arg_provenance` empty-corpus / `tool_stream` too-short floor).
    """

    calls: tuple[PriorCall, ...] = ()


@dataclass(frozen=True)
class MutatingCall:
    """The call under scrutiny — the `ToolCall` / `AdmissionRequest` analogue.

    `is_mutating` is set by the consumer's fail-open write-verb classifier (the same
    `dos_react.is_mutating_tool`). A read / non-mutating call is never gated — reads are how a
    precursor result ENTERS the stream — so `is_mutating=False` short-circuits to NO_SIGNAL.
    The classifier is deliberately fail-open (when unsure, treat as a read): under-gating
    degrades to baseline (safe), over-gating risks a feasible-task regression.
    """

    tool_name: str
    is_mutating: bool = True


@dataclass(frozen=True)
class PrecursorPolicy:
    """The knobs — mechanism is kernel, knobs are config (the `LivenessPolicy` /
    `ProvenancePolicy` / `StreamPolicy` seam). Defaults GENERIC; a host declares its own in
    `dos.toml [precursor]` read back through `SubstrateConfig` (the closed-config-as-data
    pattern, like `[tool_stream]` / `[intervention]`).

      case_sensitive — match tool names case-sensitively. Default False (casefold both sides):
                       a precursor declared `Check_Access` still matches a called `check_access`
                       (the fewest-false-fires bias, the `arg_provenance` casefold default). NB
                       names are ALSO `-`/`.`→`_` normalized regardless (`_canon`), so this knob
                       only toggles the casefold step.

    There is deliberately NO knob for resource-binding, clause-satisfaction, or ordering beyond
    stream-index — those are the off-limits provenance-of-a-RELATION questions (the module doc's
    dead-line, made structural by the absence of the field).
    """

    case_sensitive: bool = False


DEFAULT_POLICY = PrecursorPolicy()


# ---------------------------------------------------------------------------
# The grammar — config-as-data: which mutating tool requires which precursor(s).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrecursorGrammar:
    """The mandated-precursor map, as data — the unit a workspace declares.

    `requires` maps a mutating tool name → the tuple of precursor tool name(s) that satisfy
    its mandate (ANY one present in the stream → ATTESTED — the floor is "at least one mandated
    lookup fired," never "all of them"). `aliases` maps a precursor name → other tool names
    that count as the SAME precursor (the synonym allow-list — the §3 false-REFUTED safety
    valve). Both are **Appendix-C / system-prompt-derived, NEVER inferred** — a host writes the
    map by reading the policy prose once, the way it writes its lane taxonomy from the dir tree.
    Inferring the map from policy text *is* parsing policy = planner-adjacent = off-limits.

    The map is keyed on canonical names (`_canon`) at construction, so a lookup is a single
    canonical-name membership test with no per-call normalization of the grammar.
    """

    requires: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)

    def required_set(self, mutating_tool: str) -> frozenset:
        """The canonical set of tool names whose presence satisfies `mutating_tool`'s mandate —
        the declared precursor(s) UNION every alias of each. Empty frozenset iff the tool has no
        declared precursor (→ the caller NO_SIGNALs it). PURE."""
        key = _canon(mutating_tool)
        declared = self.requires.get(key)
        if not declared:
            return frozenset()
        out: set[str] = set()
        for p in declared:
            cp = _canon(p)
            out.add(cp)
            for alias in self.aliases.get(cp, ()):  # aliases keyed canonical (built below)
                out.add(_canon(alias))
        return frozenset(out)


EMPTY_GRAMMAR = PrecursorGrammar()


# ---------------------------------------------------------------------------
# Frozen verdict — the folded answer, advisory only (the EvidenceFacts shape).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrecursorVerdict:
    """The folded answer over a mutating call — the `ProvenanceVerdict` / `StreamVerdict` analogue.

    `stance` is the typed `PrecursorStance` (= `EvidenceStance`):
      ATTESTED  — a mandated precursor for this tool produced a result earlier in the stream.
      REFUTED   — the call is mutating, HAS a declared mandated precursor, and NONE fired. The
                  one actionable rung (→ a WARN re-surfacing the requirement).
      NO_SIGNAL — a read/non-mutating call, OR a mutating tool with no declared precursor, OR an
                  empty stream (first call). The fail-safe zero; never an intervention.

    `mutating_tool` echoes the call. `required` is the canonical precursor set that would have
    satisfied the mandate (the requirement the WARN names — never a fabricated DB row).
    `present` is the subset of `required` actually found in the stream (empty ⟺ REFUTED among
    mutating-with-grammar calls). `reason` is the one-line operator summary. Advisory: never
    raises, never dispatches — the consumer reads `stance` and decides whether to re-surface.
    """

    stance: PrecursorStance
    mutating_tool: str
    required: tuple[str, ...]
    present: tuple[str, ...]
    reason: str

    @property
    def fired(self) -> bool:
        """True iff this is the actionable REFUTED rung (the only stance that drives a WARN)."""
        return self.stance is EvidenceStance.REFUTED

    def to_dict(self) -> dict:
        return {
            "stance": self.stance.value,
            "mutating_tool": self.mutating_tool,
            "required": list(self.required),
            "present": list(self.present),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# The pure verdict — a structural tool_name-membership scan over the stream.
# ---------------------------------------------------------------------------
def classify_call(
    call: MutatingCall,
    stream: CallStream,
    grammar: PrecursorGrammar = EMPTY_GRAMMAR,
    policy: PrecursorPolicy = DEFAULT_POLICY,
) -> PrecursorVerdict:
    """Classify whether `call`'s mandated precursor(s) fired earlier in `stream`. PURE — no I/O.

    Reads the ladder top to bottom:

      1. NO_SIGNAL — a read / non-mutating call (reads are how a precursor ENTERS, never gated).
      2. NO_SIGNAL — a mutating call whose tool has NO declared precursor in the grammar (the
         absent-key safe direction — the gate only speaks where a host declared a mandate; an
         undeclared mutating tool dispatches as baseline). This is the grammar-coverage bound.
      3. NO_SIGNAL — an empty stream (the first call): nothing could have fired yet, so we never
         accuse (the `arg_provenance` empty-corpus floor).
      4. ATTESTED — ≥1 of the mandated precursor names appears among the prior calls' tool names.
      5. REFUTED  — none did: the agent is about to mutate before the mandated lookup fired.

    The match is a structural `tool_name`-membership test (canonicalized; casefolded unless
    `policy.case_sensitive`), NOT a substring trace over result bytes — a precursor's name is
    generally absent from its own result payload, so a token-trace would false-REFUTED.
    """
    required = grammar.required_set(call.tool_name)

    if not call.is_mutating:
        return PrecursorVerdict(
            stance=EvidenceStance.NO_SIGNAL,
            mutating_tool=call.tool_name,
            required=tuple(sorted(required)),
            present=(),
            reason="read / non-mutating call — precursor not gated (reads source the stream)",
        )
    if not required:
        return PrecursorVerdict(
            stance=EvidenceStance.NO_SIGNAL,
            mutating_tool=call.tool_name,
            required=(),
            present=(),
            reason=(
                f"{call.tool_name!r} has no declared mandated precursor in the grammar — "
                f"not gated (the side-effect-suppression edge is bounded by grammar coverage)"
            ),
        )
    if not stream.calls:
        return PrecursorVerdict(
            stance=EvidenceStance.NO_SIGNAL,
            mutating_tool=call.tool_name,
            required=tuple(sorted(required)),
            present=(),
            reason="empty call stream — first call of the episode, nothing could have fired yet",
        )

    # The structural membership scan: which mandated precursor names produced a prior result?
    # `required` is canonical (delimiter-normalized + casefolded, via the grammar). When
    # `case_sensitive` is OFF (the default), a prior call's name is matched the same way, so the
    # comparison is like-for-like. When ON, the host has opted into exact-case matching: we
    # compare delimiter-normalized-but-NOT-casefolded names on BOTH sides (re-deriving the
    # required set without casefold) so a `Check_Access` precursor no longer matches a called
    # `check_access`. The common path is the casefold default.
    if policy.case_sensitive:
        def _name(n: str) -> str:
            return (n or "").strip().replace("-", "_").replace(".", "_")
        req_match = frozenset(
            _name(p)
            for p in grammar.requires.get(_canon(call.tool_name), ())
        ) | frozenset(
            _name(a)
            for p in grammar.requires.get(_canon(call.tool_name), ())
            for a in grammar.aliases.get(p, ())
        )
    else:
        _name = _canon
        req_match = required
    present = sorted({
        _name(c.tool_name) for c in stream.calls if _name(c.tool_name) in req_match
    })

    if present:
        return PrecursorVerdict(
            stance=EvidenceStance.ATTESTED,
            mutating_tool=call.tool_name,
            required=tuple(sorted(required)),
            present=tuple(present),
            reason=(
                f"mandated precursor(s) {present} fired before {call.tool_name!r} — "
                f"the required lookup is present in the stream"
            ),
        )
    return PrecursorVerdict(
        stance=EvidenceStance.REFUTED,
        mutating_tool=call.tool_name,
        required=tuple(sorted(required)),
        present=(),
        reason=(
            f"{call.tool_name!r} is about to mutate, but none of its mandated precursor(s) "
            f"{sorted(required)} produced a result in the stream — the required lookup was "
            f"skipped (Missing Prerequisite Lookup); re-surface the requirement"
        ),
    )


# ---------------------------------------------------------------------------
# The intervention map — a DIRECT stance→rung map (the tool_stream precedent).
# NOT choose_intervention: that is ProvenanceVerdict-typed and reads fields a
# PrecursorVerdict does not carry. The only fired output is WARN — there is no
# harder rung in this map, no ceiling knob, no InterventionPolicy clamp, so BLOCK
# is unreachable BY CONSTRUCTION (the docs/147 §4 WARN-only-by-output-type guarantee).
# ---------------------------------------------------------------------------
def precursor_intervention(verdict: PrecursorVerdict) -> Optional[InterventionDecision]:
    """Map a `PrecursorVerdict` directly to an intervention rung. PURE + ADVISORY.

    The mapping is a two-line literal — the `tool_stream` precedent (that leaf's consumer maps
    its `StreamState` to a WARN without calling `choose_intervention`):

      REFUTED            -> Intervention.WARN   (re-surface the mandated-precursor requirement)
      ATTESTED/NO_SIGNAL -> None                (no intervention; dispatch unchanged)

    Returns `None` (not OBSERVE) on the non-fired stances so a consumer can `if decision:`
    cheaply. The fired rung is **always WARN** — there is no rung above it in this map, no
    confidence to assess (a `PrecursorVerdict` carries none), and no policy object to re-tune,
    so a BLOCK/DEFER is unreachable for this signal by construction. "Mandated precursor absent"
    cannot honestly carry a BLOCK's confidence (you cannot prove a check was *required for this
    specific action* without the resource/clause relation the dead-line cut), and the wiring
    reflects that: a fixed WARN, full stop. The kernel RECOMMENDS this; the consumer ACTS on it.
    """
    if verdict.stance is not EvidenceStance.REFUTED:
        return None
    miss = ", ".join(verdict.required) or "a mandated lookup"
    return InterventionDecision(
        intervention=Intervention.WARN,
        # The fields below are echoed for the InterventionDecision shape; a PrecursorVerdict has
        # no Confidence, so we carry NONE (the honest "no confidence rung for this verdict type")
        # and the precursor tool as the single "unsupported" subject the WARN names.
        confidence=_no_confidence(),
        rung=_warn_spec(),
        disruption_cost=0.0,
        unsupported=(verdict.mutating_tool,),
        reason=(
            f"mandated precursor absent for {verdict.mutating_tool!r} (required: {miss}) "
            f"-> WARN: re-surface the requirement, dispatch preserved (turn not lost)"
        ),
    )


def _no_confidence():
    """The `Confidence.NONE` literal — a `PrecursorVerdict` has no mint-confidence rung, so the
    decision carries NONE honestly (never a fabricated HIGH that a downstream reader might
    escalate on). Imported lazily to keep the module's import surface minimal."""
    from dos.intervention import Confidence
    return Confidence.NONE


def _warn_spec():
    """The shipped WARN `InterventionSpec` from the base ladder — reused, never re-declared, so
    the rung's `dispatches`/`rank`/`actuation` data stays single-sourced (the consumer reads
    `rung.dispatches` to know the call still fires)."""
    from dos.intervention import BASE_INTERVENTIONS
    spec = BASE_INTERVENTIONS.get("WARN")
    if spec is None:  # pragma: no cover - WARN is a base rung
        raise KeyError("WARN is not in BASE_INTERVENTIONS")
    return spec


# ---------------------------------------------------------------------------
# The declarative on-ramp — read a grammar out of dos.toml (mirror tool_stream/intervention).
# ---------------------------------------------------------------------------
def grammar_from_table(table: dict) -> PrecursorGrammar:
    """Turn a parsed `[precursor]` TOML table into a `PrecursorGrammar`. PURE (no I/O).

    `table` is `{requires: {tool: [precursor, ...] | precursor}, aliases: {precursor:
    [alias, ...] | alias}, case_sensitive?}` — the shape `tomllib.load(...)["precursor"]`
    yields (`[precursor.requires]` / `[precursor.aliases]` sub-tables). Names are canonicalized
    at load so lookups need no per-call normalization. A scalar value is accepted in place of a
    one-element list (the `see_also` / `ignore_tools` single-string convenience). Missing →
    EMPTY_GRAMMAR (the gate NO_SIGNALs everything = today's behavior). PURE.
    """
    if not table:
        return EMPTY_GRAMMAR

    def _as_tuple(v) -> tuple:
        if v is None:
            return ()
        if isinstance(v, str):
            return (v,)
        return tuple(v)

    requires_raw = table.get("requires", {}) or {}
    aliases_raw = table.get("aliases", {}) or {}
    requires = {
        _canon(tool): tuple(_canon(p) for p in _as_tuple(precs))
        for tool, precs in requires_raw.items()
    }
    aliases = {
        _canon(prec): tuple(_canon(a) for a in _as_tuple(als))
        for prec, als in aliases_raw.items()
    }
    return PrecursorGrammar(requires=requires, aliases=aliases)


def load_from_toml(
    path: "Path | str", *, base: PrecursorGrammar = EMPTY_GRAMMAR
) -> PrecursorGrammar:
    """Build a `PrecursorGrammar` from a `dos.toml`'s `[precursor]` table.

    Returns `base` unchanged when the file is absent, has no `[precursor]` table, or `tomllib`
    is unavailable — the declarative path is purely additive, so a missing/empty config degrades
    to the empty grammar (the gate NO_SIGNALs everything), never an error. A *present* table
    REPLACES the grammar wholesale (the `[stamp]`/`[tool_stream]` override shape). Reads with
    `utf-8-sig` to strip a PowerShell-written BOM (the `intervention.load_from_toml` fix).
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
    table = data.get("precursor")
    if not isinstance(table, dict) or not table:
        return base
    return grammar_from_table(table)
