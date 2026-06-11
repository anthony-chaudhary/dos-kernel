"""SPEND — the token-spend breakdown: *what kind of tokens did this run spend?*

docs/300 — the evidence vocabulary under the efficiency family. `efficiency`
(docs/263) reads one scalar token count. This leaf gives that scalar a typed
inside: the five-way split the field standardized in early 2026 (the
OpenTelemetry GenAI usage attributes, and every major provider's billing record):

    input          — input tokens processed fresh (NOT served from a cache)
    output         — tokens the model generated (the decode side)
    cache_read     — input tokens served from the provider's prompt cache
    cache_creation — input tokens written INTO the provider's prompt cache
    reasoning      — the sub-count of `output` spent thinking (0 = unreported)

**Canonically disjoint.** The four count fields do not overlap: `input` excludes
the two cache fields, so `total` is a plain sum. The one exception is
`reasoning`, which is a SUB-count of `output` (every provider bills thinking as
output), carried separately because "how much of the output was deliberation"
is the overthinking diagnostic an operator wants — validated `reasoning <=
output`, never added twice.

**Why the constructors are named by wire semantics, not by vendor.** Providers
ship usage records in two shapes, and confusing them is the industry's
double-count bug class:

  * the **additive** shape — the input count EXCLUDES cached tokens; the cache
    fields are siblings, and total input is the sum of the three.
  * the **inclusive** shape — the input count already INCLUDES the cached
    tokens; the cache field is a sub-count to subtract.

`from_additive_usage` / `from_inclusive_usage` normalize each shape ONCE, at
the boundary, into the disjoint canonical form — so the ambiguity cannot enter
the kernel (the docs/217 dialect-seam move, applied to usage records). The
kernel names no vendor in code (`tests/test_vendor_agnostic_kernel.py`); which
provider uses which shape is prose for the caller's docs, not a branch here.

**Byte-clean (docs/138).** Every count comes from the provider's billing
record — bytes the judged run did not author. A run can no more inflate its own
cache-hit ratio than it could shrink its own billed total. Like the rest of the
family this leaf makes **no I/O at all**: the caller's boundary (a CLI flag, a
loop reading its usage record) freezes the mapping into the dataclass; every
derived number is pure arithmetic.

**A breakdown explains; it never adjudicates.** The verdict ladders in
`efficiency` / `improve` ride the scalar total. The breakdown adds the
diagnostics the field treats as headline KPIs — `cache_hit_ratio` (cache reads
are ~10× cheaper than fresh input), `output_share` (decode tokens are the
expensive, slow side), `reasoning_share` (how much of the output was
deliberation) — so a WASTEFUL verdict can say *what kind* of spend bought
nothing. Richer evidence can never flip a verdict toward EFFICIENT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


def _count(mapping: Mapping, key: str) -> int:
    """Read one non-negative token count from a usage mapping, tolerantly.

    A missing key or an explicit `None` is 0 (providers omit fields they did not
    meter). Anything else must be a non-negative `int` — a string, a float, or a
    negative count is a malformed record, refused LOUDLY at the boundary rather
    than coerced (a silently-mended usage record is how double-counts ship).
    """
    raw = mapping.get(key)
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"usage field {key!r} must be a non-negative integer token count, "
            f"got {raw!r}"
        )
    if raw < 0:
        raise ValueError(
            f"usage field {key!r} must be non-negative, got {raw!r}"
        )
    return raw


@dataclass(frozen=True)
class SpendBreakdown:
    """The five-way token-spend split — disjoint counts, frozen at the boundary.

    All five fields are non-negative ints. `input`, `output`, `cache_read`, and
    `cache_creation` are mutually disjoint (the canonical, additive form);
    `reasoning` is a sub-count of `output` (thinking is billed as output
    everywhere), so it never enters `total` on its own.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    reasoning: int = 0

    def __post_init__(self) -> None:
        for name in ("input", "output", "cache_read", "cache_creation", "reasoning"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer token count, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value!r}")
        if self.reasoning > self.output:
            raise ValueError(
                f"reasoning ({self.reasoning}) cannot exceed output "
                f"({self.output}) — reasoning is a sub-count of the output tokens"
            )

    # -- totals ---------------------------------------------------------------

    @property
    def total(self) -> int:
        """Every token the provider billed, whatever its rate — the scalar the
        efficiency ladder reads (`EfficiencyEvidence.tokens`). `reasoning` is
        already inside `output`, so it is not added again."""
        return self.input + self.output + self.cache_read + self.cache_creation

    @property
    def prefill(self) -> int:
        """The context side — every input token processed, cached or not."""
        return self.input + self.cache_read + self.cache_creation

    @property
    def decode(self) -> int:
        """The generation side — the sequential, per-token-priced half."""
        return self.output

    # -- diagnostics (each division-safe: 0.0 on an empty denominator) ---------

    @property
    def cache_hit_ratio(self) -> float:
        """What fraction of the context was served from the provider cache —
        the headline efficiency KPI (cache reads bill at ~0.1× fresh input)."""
        if self.prefill <= 0:
            return 0.0
        return self.cache_read / self.prefill

    @property
    def output_share(self) -> float:
        """What fraction of the total spend was decode — the expensive side."""
        if self.total <= 0:
            return 0.0
        return self.output / self.total

    @property
    def reasoning_share(self) -> float:
        """What fraction of the output was deliberation — the overthinking
        diagnostic. 0.0 when the record did not report a reasoning sub-count."""
        if self.output <= 0:
            return 0.0
        return self.reasoning / self.output

    # -- construction ----------------------------------------------------------

    @classmethod
    def of(
        cls,
        *,
        input: int = 0,
        output: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
        reasoning: int = 0,
    ) -> "SpendBreakdown":
        """Build a breakdown from already-disjoint counts."""
        return cls(
            input=input,
            output=output,
            cache_read=cache_read,
            cache_creation=cache_creation,
            reasoning=reasoning,
        )

    def to_dict(self) -> dict:
        """The counts plus the derived diagnostics — the legible-distrust JSON
        shape (`dos efficiency --json` shows not just the verdict but what kind
        of spend was behind it)."""
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_creation": self.cache_creation,
            "reasoning": self.reasoning,
            "total": self.total,
            "prefill": self.prefill,
            "decode": self.decode,
            "cache_hit_ratio": self.cache_hit_ratio,
            "output_share": self.output_share,
            "reasoning_share": self.reasoning_share,
        }


def from_additive_usage(mapping: Mapping) -> SpendBreakdown:
    """Normalize an ADDITIVE-shape usage record: input EXCLUDES cached tokens.

    Wire keys: `input_tokens` (fresh input only), `output_tokens`,
    `cache_read_input_tokens`, `cache_creation_input_tokens`, and the optional
    `reasoning_output_tokens` sub-count. Missing/None fields are 0. This shape
    is already the canonical disjoint form, so the read is a straight copy.
    """
    return SpendBreakdown(
        input=_count(mapping, "input_tokens"),
        output=_count(mapping, "output_tokens"),
        cache_read=_count(mapping, "cache_read_input_tokens"),
        cache_creation=_count(mapping, "cache_creation_input_tokens"),
        reasoning=_count(mapping, "reasoning_output_tokens"),
    )


def from_inclusive_usage(mapping: Mapping) -> SpendBreakdown:
    """Normalize an INCLUSIVE-shape usage record: input INCLUDES cached tokens.

    Wire keys: `prompt_tokens` (cached tokens INCLUDED), `completion_tokens`,
    with the sub-counts nested under `prompt_tokens_details.cached_tokens` and
    `completion_tokens_details.reasoning_tokens` (flat `cached_tokens` /
    `reasoning_tokens` accepted as fallbacks). The cached sub-count is
    SUBTRACTED out of the prompt count to reach the canonical disjoint form —
    the one move that kills the double-count bug class. A record whose cached
    sub-count exceeds its prompt count is malformed and refused loudly, never
    clamped. This shape does not report cache writes; `cache_creation` is 0.
    """
    prompt = _count(mapping, "prompt_tokens")
    completion = _count(mapping, "completion_tokens")

    prompt_details = mapping.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping):
        cached = _count(prompt_details, "cached_tokens")
    else:
        cached = _count(mapping, "cached_tokens")

    completion_details = mapping.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning = _count(completion_details, "reasoning_tokens")
    else:
        reasoning = _count(mapping, "reasoning_tokens")

    if cached > prompt:
        raise ValueError(
            f"inclusive usage record is inconsistent: cached_tokens ({cached}) "
            f"exceeds prompt_tokens ({prompt}) — the cached sub-count must be "
            f"inside the prompt count it is subtracted from"
        )
    return SpendBreakdown(
        input=prompt - cached,
        output=completion,
        cache_read=cached,
        cache_creation=0,
        reasoning=reasoning,
    )


def parse_usage(mapping: Mapping) -> SpendBreakdown:
    """Detect a usage record's wire shape by its key names and normalize it.

    `input_tokens` present → the additive shape; `prompt_tokens` present → the
    inclusive shape. Both present is ambiguous and refused (a record carrying
    both vocabularies is not a record this parser can attribute safely);
    neither present is unrecognized and refused. A caller holding counts in
    neither wire shape builds `SpendBreakdown.of(...)` directly.
    """
    has_additive = "input_tokens" in mapping
    has_inclusive = "prompt_tokens" in mapping
    if has_additive and has_inclusive:
        raise ValueError(
            "usage record carries BOTH 'input_tokens' and 'prompt_tokens' — "
            "ambiguous wire shape; normalize it before handing it to the kernel"
        )
    if has_additive:
        return from_additive_usage(mapping)
    if has_inclusive:
        return from_inclusive_usage(mapping)
    raise ValueError(
        "unrecognized usage record: expected an additive shape ('input_tokens' …) "
        "or an inclusive shape ('prompt_tokens' …)"
    )
