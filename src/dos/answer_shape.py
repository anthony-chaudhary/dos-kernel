"""`answer_shape` — the "is this output an ANSWER, or a non-answer?" verdict (docs/156 §4).

The picker/grounding-boundary closure of the **grounded-but-not-an-answer** gap that
the first real third-party DOS adoption surfaced (docs/156, a third-party grounded-RAG
app; the `project-dos-grounded-rag-adoption` recall). There, the numeric grounding gate
worked perfectly — every shipped *number* was witnessed — and the app still shipped, as
its "answer" to question q_025, a **5,780-char leaked chain-of-thought log** with
`refused=False`. The gate guarded the *facts*; nothing guarded that the output was an
*answer*. "Never shipped a wrong number" was literally true and badly misleading.

This is the missing leaf primitive docs/156 §4 named (build order Phase 2): a pure,
domain-free verdict an assembly policy can require *before* it ships an output —

    ship  ⟺  grounded  AND  answer_shape ≠ NON_ANSWER

so a structurally-disqualified output (an empty stub, a process/CoT log, a bare
refusal pasted as content) is caught even when every claim inside it grounds.

⚠ THE HONESTY BOUNDARY — read this before extending. This verdict judges **shape**,
never **correctness or relevance.** It answers the *mechanically-checkable* question
"is this output the KIND of thing that could be an answer, or is it structurally a
non-answer?" — NOT the semantic question "is this a GOOD / CORRECT / RELEVANT answer to
the question?". That second question is the Tier-3 gestalt the kernel deliberately
ABSTAINS on (docs/212/213/215, the `project-dos-non-coding-domains-world-witness-axis`
arc; the `project-dos-wall-presence-not-goal` W2/W3 gap): it has no independent witness,
so it belongs to a JUDGE (advisory, fail-to-abstain) or a HUMAN, never to a deterministic
oracle. `ANSWER_SHAPED` therefore means *"shaped like an answer,"* and explicitly NOT
*"a right answer"* — confusing the two would be the consistency-is-not-grounding trap
(`feedback-consistency-is-not-grounding`) one level up. On anything it cannot decide from
shape alone, this verdict returns INDETERMINATE — the abstain floor — never a false
`ANSWER_SHAPED`.

So where does it sit on the witness ladder (docs/192)? It is a **W2-presence-class**
check on the OUTPUT itself: "an answer-shaped artifact is present," the same altitude as
`verify()`'s file-path rung ("a real commit touched the path") — useful and sound for
what it claims, and pointedly NOT a W3 goal-witness. It is also *advisory*: it REPORTS a
shape; the consumer (an assembly policy) decides whether to withhold. PDP, not PEP.

The three states (mutually exclusive):

  * ``ANSWER_SHAPED`` — passes the structural floor: non-empty, at or above the
                        viability length, and matches no disqualifying marker. Shaped
                        like an answer. (NOT a claim of correctness — see the boundary.)
  * ``NON_ANSWER``    — structurally disqualified: empty/whitespace-only, below the
                        viability floor, OVER the verbosity ceiling, degenerated into a
                        repeating loop, OR matches a declared non-answer marker (a
                        process/CoT-log signature, a bare-refusal signature, a stub).
                        The q_025 catch (the stub/CoT end) PLUS the opposite failures the
                        same shape guard should see — the runaway/padded dump and the
                        decode-degeneration loop (the verbosity/drift end). The dangerous
                        case a grounding gate misses.
  * ``INDETERMINATE`` — no policy supplied, or the text is non-trivial but the policy
                        cannot disqualify it on shape — the abstain floor. The semantic
                        "is it a good answer?" residue goes here, to a JUDGE / HUMAN.

The markers are **policy, not hardcode.** docs/156 §5 specifically criticised the host's
finance-shaped `_TOOL_LEAK` / `strip_cot` regex pile as the wrong thing to lift into the
kernel. So this module ships a *generic* default policy (the obvious cross-domain
signatures — a fenced reasoning block, "let me think", a tool-call dump, a bare "I
cannot") and lets a host DECLARE its own `AnswerShapePolicy` (the closed-enum-as-data /
policy-injection pattern used across the kernel: `dos.reasons`, `dos.stamp`,
`overlap_policy`). The kernel carries the *fold + the floor*; the host carries the
*signatures*.

⚓ Pure; the candidate text + the policy are handed in at the caller boundary (the
drafted answer, the declared markers). No I/O, no model call, no regex compilation at
import. Returns a verdict; NEVER raises (a bad pattern degrades to "not matched", never
an exception — the fail-safe direction is to NOT over-disqualify, the dual of
`run_judge`'s fail-to-abstain).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class AnswerShape(str, enum.Enum):
    """The typed answer-shape verdict (docs/156 §4).

    `str`-valued so it round-trips a `--json` token / exit code without a lookup table
    (the `Reconciliation` / `Completion` / `gate_classify.Verdict` idiom). The
    load-bearing asymmetry: only `NON_ANSWER` is a positive disqualification;
    `ANSWER_SHAPED` is "no structural disqualifier found" (NOT "correct"), and
    `INDETERMINATE` is the abstain floor.
    """

    ANSWER_SHAPED = "ANSWER_SHAPED"    # shaped like an answer — no disqualifier (NOT "correct")
    NON_ANSWER = "NON_ANSWER"          # structurally disqualified — empty / too-short / marker hit
    INDETERMINATE = "INDETERMINATE"    # cannot decide on shape alone — abstain to JUDGE/HUMAN

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def is_shippable(self) -> bool:
        """True iff an assembly policy MAY ship this on shape grounds (ANSWER_SHAPED only).

        Note the asymmetry with `is_disqualified`: INDETERMINATE is neither shippable
        nor disqualified — it means "shape can't decide; ask a JUDGE/HUMAN". A consumer
        that treats INDETERMINATE as shippable has skipped the residual question, not
        answered it.
        """
        return self is AnswerShape.ANSWER_SHAPED

    @property
    def is_disqualified(self) -> bool:
        """True iff this output was positively ruled out as a non-answer."""
        return self is AnswerShape.NON_ANSWER


@dataclass(frozen=True)
class AnswerShapePolicy:
    """The declared, swappable shape rules — markers as DATA, not hardcode (docs/156 §5).

    ``min_viable_chars`` is the length floor below which a non-empty output is too small
    to be an answer (a bare "0", an ack token, a truncated stub). Default 1 disables the
    floor *as a length test* (only empty/whitespace disqualifies) — set it per host
    (the RAG app's q_025 leaked-CoT was 5,780 chars, so length alone never catches that;
    the *markers* do — length catches the opposite failure, the empty/stub end).

    ``max_viable_chars`` is the SYMMETRIC ceiling — the verbosity/bloat end the floor
    can't see. Above it, a non-empty output is a runaway/padded generation, structurally
    not a focused answer (the q_025 leaked-CoT was *long*; gross length is itself a
    structural tell once a host declares its budget). Default ``0`` DISABLES the ceiling
    (no upper bound) — exactly like the floor's default-1 only-empty behavior, the host
    declares the threshold; the kernel never guesses a length budget (the "ceiling fixed
    by the source, never inferred from content" rule, docs/156 §3.2). Still SHAPE, not
    quality: a host setting a ceiling is declaring "in my domain an answer over N chars
    is structurally a dump", the same kind of host call as setting ``min_viable_chars``.

    ``max_repeat_ratio`` is the DEGENERATION guard — the drift-into-a-loop failure where
    a generation collapses into repeating the same line/sentence (the classic decode
    degeneration, and the most common *mechanism* of padded verbosity). It is the
    fraction of repeated (non-distinct) units the output may carry before it is
    structurally a non-answer: ``repeat_ratio = (n_units - n_distinct) / n_units`` over
    the segmented units (see ``_repeat_ratio``). Above the threshold ⇒ NON_ANSWER.
    Default ``0.0`` DISABLES it (the fail-safe UNDER-disqualify direction — the kernel
    ships the *mechanism*, the host opts in with a threshold like ``0.5``). Mechanically
    checkable and domain-free, so it stays inside the SHAPE boundary: a text that is
    mostly the same sentence over and over is not an answer regardless of the topic.

    ⚠ SCOPE IT TO PROSE. The metric is the fraction of EXACT (stripped+casefolded)
    duplicate units — content-blind, not semantic. So a list/table/CSV/log channel whose
    rows legitimately repeat a short literal (``- N/A`` ten times, a ``| --- |`` rule)
    will read as high-ratio and be disqualified. That is WHY it defaults off and the host
    opts in: point ``max_repeat_ratio`` at free-prose answer channels, not at structured
    output, where exact-row repetition is a feature, not degeneration.

    ``non_answer_patterns`` is the host's closed set of disqualifying regexes — a
    process/CoT-log signature, a bare-refusal signature, a tool-call dump. Matched
    case-insensitively, in a `search` (anywhere in the text). The kernel ships a generic
    cross-domain default (`GENERIC_ANSWER_SHAPE_POLICY`); a host declares its own. An
    invalid pattern is skipped at match time (never raises — the fail-safe is to
    UNDER-disqualify, so a broken host rule degrades to "ANSWER_SHAPED", not a crash).

    ``answer_markers`` (optional) is the dual — a closed set of positive answer
    signatures (e.g. a host's structured "Answer:" prefix or a required citation token).
    When non-empty, a non-trivial text that matches NONE of them is INDETERMINATE (not
    ANSWER_SHAPED) — the policy is saying "I only call something answer-shaped if it
    carries one of my positive markers; otherwise I abstain." When empty (the default),
    absence-of-disqualifier is enough for ANSWER_SHAPED. This is how a strict host opts
    into positive-evidence-required without the kernel guessing.
    """

    min_viable_chars: int = 1
    max_viable_chars: int = 0
    max_repeat_ratio: float = 0.0
    non_answer_patterns: tuple[str, ...] = ()
    answer_markers: tuple[str, ...] = ()


# The generic, domain-free default. The cross-domain non-answer signatures — NOT a
# finance-shaped pile (docs/156 §5's lesson). Each is a structural tell that the output
# is a process artifact / a refusal / a stub rather than a delivered answer. A host with
# domain-specific leaks (the RAG app's tool-leak markers) declares its own policy ON TOP.
GENERIC_NON_ANSWER_PATTERNS: tuple[str, ...] = (
    r"<thinking>",                       # a leaked reasoning block (open tag is enough)
    r"</thinking>",
    r"<scratchpad>",
    r"\blet me (?:think|reason)\b",       # narrated chain-of-thought
    r"\bstep 1:\s",                       # an enumerated process log presented as the answer
    r"\bi cannot\b.*\b(?:answer|help|comply|provide)\b",  # a bare refusal pasted as content
    r"\bi'?m (?:sorry|unable)\b.*\b(?:cannot|can't|unable)\b",
    r"^\s*(?:tool_call|function_call|tool_use)\b",  # a raw tool-call dump
    r"\btraceback \(most recent call last\)",        # a stack trace shipped as the answer
)

GENERIC_ANSWER_SHAPE_POLICY = AnswerShapePolicy(
    min_viable_chars=1,
    non_answer_patterns=GENERIC_NON_ANSWER_PATTERNS,
)


@dataclass(frozen=True)
class AnswerShapeVerdict:
    """The single verdict `classify` returns, with the inputs echoed back for legibility.

    ``state`` is the typed `AnswerShape`. ``length`` is the candidate's char count.
    ``matched`` is the disqualifying pattern that fired (empty when none did). ``reason``
    is the operator-facing one-liner. The echoed fields make a surfaced verdict
    self-explaining (the `ReconciliationVerdict` idiom).
    """

    state: AnswerShape
    length: int
    matched: str
    reason: str

    @property
    def is_shippable(self) -> bool:
        return self.state.is_shippable

    @property
    def is_disqualified(self) -> bool:
        return self.state.is_disqualified

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "length": self.length,
            "matched": self.matched,
            "is_shippable": self.is_shippable,
            "is_disqualified": self.is_disqualified,
            "reason": self.reason,
        }


# The minimum number of segmented units before a repeat ratio is meaningful. Below it
# there is too little signal to call degeneration, so the guard ABSTAINS (under-
# disqualify — the same fail-safe direction as `_safe_search`). A short Q&A like
# "Yes. No. Yes." must never trip the loop guard; a real degeneration repeats many times.
_MIN_REPEAT_UNITS = 4

# Segment on sentence terminators OR newlines, so a degeneration is caught whether it
# repeats one-unit-per-line or many-sentences-on-one-line.
_UNIT_SPLIT = re.compile(r"[.!?\n]+")


def _repeat_ratio(text: str) -> tuple[float, int]:
    """The fraction of REPEATED (non-distinct) units in ``text``, and the unit count.

    Splits ``text`` into units on sentence terminators / newlines, normalizes each
    (stripped + casefolded), drops empties, and returns
    ``((n_units - n_distinct) / n_units, n_units)``. A clean answer with all-distinct
    units scores ~0; a generation that has collapsed into repeating one line/sentence
    approaches 1. PURE; returns ``(0.0, n)`` when there are too few units to judge (the
    caller's `_MIN_REPEAT_UNITS` floor makes that an abstain, never a disqualification).
    """
    units = [u.strip().casefold() for u in _UNIT_SPLIT.split(text)]
    units = [u for u in units if u]
    n = len(units)
    if n < 2:
        return 0.0, n
    return (n - len(set(units))) / n, n


def _safe_search(pattern: str, text: str) -> bool:
    """Case-insensitive `re.search`, fail-safe to False on a bad pattern.

    A host-declared regex that does not compile must NOT crash the verdict — the
    fail-safe direction is to UNDER-disqualify (treat it as "not matched"), the dual of
    `run_judge`'s fail-to-abstain. A broken disqualifier therefore degrades a possible
    `NON_ANSWER` toward `ANSWER_SHAPED`/`INDETERMINATE`, never toward an exception.
    """
    try:
        return re.search(pattern, text, re.IGNORECASE | re.MULTILINE) is not None
    except re.error:
        return False


def classify(
    text: "str | None",
    *,
    policy: "AnswerShapePolicy | None" = GENERIC_ANSWER_SHAPE_POLICY,
) -> AnswerShapeVerdict:
    """Classify an output's SHAPE: ANSWER_SHAPED / NON_ANSWER / INDETERMINATE. PURE.

    ``text`` is the candidate output (a drafted answer), gathered at the boundary.
    ``policy`` is the declared shape rules; the generic default if omitted, or `None`
    to force INDETERMINATE (no rules → cannot judge shape → abstain).

    The decision order (first match wins):

      1. ``policy is None``                → INDETERMINATE (no rules; abstain — the floor).
      2. ``text`` empty / whitespace-only  → NON_ANSWER (nothing was delivered).
      3. ``len(text) < min_viable_chars``  → NON_ANSWER (below the viability floor — a
                                             stub / ack token, not an answer).
      4. ``max_viable_chars`` set AND
         ``len(text) > max_viable_chars``  → NON_ANSWER (over the verbosity/bloat ceiling
                                             — a runaway/padded dump, not a focused answer).
      5. a ``non_answer_patterns`` hit     → NON_ANSWER (a process/CoT-log / bare-refusal
                                             / tool-dump signature — the q_025 catch).
      6. ``max_repeat_ratio`` set AND the
         repeat ratio exceeds it           → NON_ANSWER (degenerated into a loop — the
                                             same line/sentence over and over; drift).
      7. ``answer_markers`` non-empty AND
         none matched                      → INDETERMINATE (the strict host required a
                                             positive answer marker and found none; abstain).
      8. otherwise                         → ANSWER_SHAPED (no disqualifier; shaped like
                                             an answer — NOT a claim of correctness).

    Returns an `AnswerShapeVerdict`; NEVER raises. Remember the boundary: a `NON_ANSWER`
    is a sound structural disqualification; an `ANSWER_SHAPED` is only "shape is fine,"
    and the semantic correctness/relevance question is for a JUDGE/HUMAN (INDETERMINATE
    is where shape honestly cannot decide).
    """
    if policy is None:
        return AnswerShapeVerdict(
            state=AnswerShape.INDETERMINATE,
            length=len(text or ""),
            matched="",
            reason="no answer-shape policy supplied — cannot judge shape; abstain "
                   "(the semantic 'is it an answer?' question goes to a JUDGE/HUMAN)",
        )

    raw = text or ""
    stripped = raw.strip()
    n = len(raw)

    if not stripped:
        return AnswerShapeVerdict(
            state=AnswerShape.NON_ANSWER,
            length=n,
            matched="",
            reason="empty / whitespace-only output — nothing was delivered (NON_ANSWER)",
        )

    if len(stripped) < max(1, int(policy.min_viable_chars)):
        return AnswerShapeVerdict(
            state=AnswerShape.NON_ANSWER,
            length=n,
            matched="",
            reason=(f"output is {len(stripped)} non-space chars, below the viability "
                    f"floor of {policy.min_viable_chars} — a stub/ack token, not an "
                    f"answer (NON_ANSWER)"),
        )

    ceiling = int(policy.max_viable_chars)
    if ceiling > 0 and len(stripped) > ceiling:
        return AnswerShapeVerdict(
            state=AnswerShape.NON_ANSWER,
            length=n,
            matched="",
            reason=(f"output is {len(stripped)} non-space chars, over the verbosity "
                    f"ceiling of {ceiling} — a runaway/padded dump, not a focused "
                    f"answer (NON_ANSWER; a SHAPE call on the host's length budget, "
                    f"not a correctness judgment)"),
        )

    for pat in policy.non_answer_patterns:
        if _safe_search(pat, raw):
            return AnswerShapeVerdict(
                state=AnswerShape.NON_ANSWER,
                length=n,
                matched=pat,
                reason=(f"output matched the non-answer signature {pat!r} — a "
                        f"process/CoT-log, bare refusal, or tool dump pasted as the "
                        f"answer (the grounded-but-not-an-answer catch, docs/156 §4)"),
            )

    repeat_cap = float(policy.max_repeat_ratio)
    if repeat_cap > 0.0:
        ratio, units = _repeat_ratio(raw)
        if units >= _MIN_REPEAT_UNITS and ratio > repeat_cap:
            return AnswerShapeVerdict(
                state=AnswerShape.NON_ANSWER,
                length=n,
                matched="",
                reason=(f"output repeat ratio {ratio:.2f} over {units} units exceeds "
                        f"the degeneration cap of {repeat_cap:.2f} — collapsed into "
                        f"repeating the same line/sentence (NON_ANSWER; a structural "
                        f"loop/drift tell, not a correctness judgment)"),
            )

    if policy.answer_markers:
        if not any(_safe_search(m, raw) for m in policy.answer_markers):
            return AnswerShapeVerdict(
                state=AnswerShape.INDETERMINATE,
                length=n,
                matched="",
                reason=("no disqualifier fired, but the policy requires a positive "
                        "answer marker and none matched — abstain on shape (route the "
                        "semantic question to a JUDGE/HUMAN)"),
            )

    return AnswerShapeVerdict(
        state=AnswerShape.ANSWER_SHAPED,
        length=n,
        matched="",
        reason=("no structural disqualifier — shaped like an answer (NOT a claim of "
                "correctness or relevance; that is a JUDGE/HUMAN question)"),
    )
