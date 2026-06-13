"""Provider-limit category — the one canonical vocabulary the dispatch family
collapses every rate-limit / quota / overload signal into (the PI5 collapse
target promised in the job repo's ``agents/quota/base.py``).

Three independent taxonomies exist upstream, each correct for its own input:

  * ``rate_limit_classify.Kind`` (job) — string markers on a ``claude -p``
    terminal envelope ({RATE_LIMITED, OVERLOADED, CREDIT_LOW, NONE}).
  * ``agents.quota.QuotaErrorClass`` (job) — provider exceptions
    ({RPM_THROTTLED, DAILY_QUOTA_EXHAUSTED, SUBSCRIPTION_BLACKOUT, TRANSIENT_429}).
  * apply-next-loop outcome tokens (job) — exit-code + log regex
    ({LLM-QUOTA-EXHAUSTED, LLM-QUOTA-EXHAUSTED-DURABLE, CORRELATED-OUTAGE, …}).

They overlap but share no OUTPUT type, so every loop re-decided "transient vs
usage vs hard-quota" on its own and drifted. This module is **not** a fourth
classifier — it is the shared category + the canonical backoff policy that all
three map *into* via the thin pure ``from_*`` translators below.

⚓ Provider-invariance (job CLAUDE.md "Bulkhead"): provider distinctions stay
infrastructure inside the adapter. The mapper takes the upstream enum's VALUE
(a plain ``str``), never the upstream class — so ``dos`` imports nothing from
``agents.quota`` / ``rate_limit_classify``; the dependency arrow points the
right way (job → dos), never back.

The kernel decision logic that ACTS on a category already lives in
``dos.loop_decide.decide`` (``OutcomeKind.OVERLOADED`` → ``retry-same-iter``
with the same backoff ladder; ``RATE_LIMITED`` → stop). This module does not
change that — it standardizes the *word*, and ``policy_for`` makes the backoff
ladder a single source of truth both sides can read.

PURE — no I/O, no clock. py.typed.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class ProviderLimit(str, enum.Enum):
    """The canonical provider-limit category — what dispatch reasons about.

    ``str``-valued so it round-trips as a token (``ProviderLimit.USAGE_WINDOW
    == "usage_window"``), same convention as ``loop_decide.OutcomeKind`` and
    ``gate_classify.Verdict``.

      TRANSIENT_OVERLOAD — server-side 529 / ``overloaded_error`` / the harness
                           "Server is temporarily limiting requests (not your
                           usage limit)" surface. Clears in seconds-to-minutes.
                           Policy: retry the SAME unit of work with backoff;
                           escalate to stop only after K consecutive hits (an
                           outage, not a blip).
      USAGE_WINDOW       — a 429 / quota / 5-hour / 7-day / weekly cap. Every
                           retry fails identically until the window resets on a
                           TIMER. Policy: stop (or durable-defer past a measured
                           ``window_end``); re-invoke after reset.
      HARD_QUOTA         — a billing block ("credit balance too low") or an
                           opaque subscription blackout. No timer fixes it — an
                           OPERATOR must act. Policy: stop + surface.
      MODEL_UNAVAILABLE  — the NAMED model is down/retired/unknown ("Claude
                           Fable 5 is currently unavailable") — NOT a usage or
                           rate limit, and not the caller's account at all. A
                           child/grandchild launched on this model returns a
                           shaped non-result (``success`` + ``is_error`` + 1
                           turn + $0): the worker never ran. No timer clears it
                           and no operator credit fixes it, BUT — unlike
                           HARD_QUOTA — a sibling model fixes it INSTANTLY.
                           Policy: re-route the unit to an available model and
                           re-dispatch (auto-heal); retrying the SAME model is
                           futile. This is the model-roster axis the goal's
                           "model is down on a child/grandchild" case lands on.
      NONE               — no provider-limit signal.

    The load-bearing split is TRANSIENT_OVERLOAD (retry) vs everything else
    (stop/defer). A real overload and a real quota window can BOTH arrive as a
    ``rejected`` rate-limit event — the disambiguator is the error TYPE
    (529/overloaded vs 429/quota) and the "(not your usage limit)" prose, NOT
    the ``rejected`` status alone. MODEL_UNAVAILABLE is a SECOND split: it is
    the only category whose heal is to change WHICH model runs the unit rather
    than to wait, stop, or escalate to a human — so it carries its own
    ``reroute_model`` policy flag, orthogonal to ``retryable_same_iter``
    (re-running the same model would just re-hit the down model).
    """

    TRANSIENT_OVERLOAD = "transient_overload"
    USAGE_WINDOW = "usage_window"
    HARD_QUOTA = "hard_quota"
    MODEL_UNAVAILABLE = "model_unavailable"
    NONE = "none"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Canonical backoff ladder for a transient overload retry. Mirrors
# ``loop_decide._OVERLOADED_BACKOFF`` deliberately — this module is the shared
# source of truth, ``loop_decide`` keeps its own copy for the hot decide() path
# but the two MUST stay equal (asserted by a cross-module test in both repos).
_OVERLOAD_BACKOFF: tuple[int, ...] = (60, 270, 1200)
_OVERLOAD_ESCALATE_AFTER = 3  # consecutive TRANSIENT_OVERLOAD hits → stop


@dataclass(frozen=True)
class LimitPolicy:
    """The canonical handling policy for one :class:`ProviderLimit` category.

    A pure lookup (see :func:`policy_for`) — the single place the dispatch
    family reads "is this retryable, with what backoff, when do I escalate,
    does an operator have to act, will it clear on its own". Consumers must not
    re-derive these per-loop (that is the drift this module exists to kill).
    """

    category: ProviderLimit
    retryable_same_iter: bool
    """True only for TRANSIENT_OVERLOAD — retry the same unit of work."""

    backoff_seconds: tuple[int, ...]
    """Backoff ladder for the retry; ``()`` for non-retryable categories."""

    escalate_after: int
    """Consecutive hits of this category before escalating to a hard stop.

    ``_OVERLOAD_ESCALATE_AFTER`` (3) for TRANSIENT_OVERLOAD; ``1`` for the
    stop-now categories (the first hit already stops).
    """

    operator_action_required: bool
    """True for HARD_QUOTA — no backoff/wait resolves it; a human must act."""

    resets_on_timer: bool
    """True when the limit clears on its own (TRANSIENT_OVERLOAD, USAGE_WINDOW);
    False for HARD_QUOTA (operator-gated), MODEL_UNAVAILABLE (a sibling model,
    not a timer, fixes it) and NONE."""

    reroute_model: bool = False
    """True for MODEL_UNAVAILABLE only — the heal is to re-dispatch the unit on a
    DIFFERENT (available) model, not to wait, stop, or escalate to a human. This
    is the auto-routing flag the 'model down on a child/grandchild' case keys on:
    a consumer reading ``reroute_model`` knows the fix is a sibling model, and
    that retrying the SAME model (``retryable_same_iter``) would be futile. Which
    models exist and which are up is host/driver policy — this flag only says
    'route to another one', never names one (the provider-invariance Bulkhead)."""


_POLICIES: dict[ProviderLimit, LimitPolicy] = {
    ProviderLimit.TRANSIENT_OVERLOAD: LimitPolicy(
        category=ProviderLimit.TRANSIENT_OVERLOAD,
        retryable_same_iter=True,
        backoff_seconds=_OVERLOAD_BACKOFF,
        escalate_after=_OVERLOAD_ESCALATE_AFTER,
        operator_action_required=False,
        resets_on_timer=True,
    ),
    ProviderLimit.USAGE_WINDOW: LimitPolicy(
        category=ProviderLimit.USAGE_WINDOW,
        retryable_same_iter=False,
        backoff_seconds=(),
        escalate_after=1,
        operator_action_required=False,
        resets_on_timer=True,
    ),
    ProviderLimit.HARD_QUOTA: LimitPolicy(
        category=ProviderLimit.HARD_QUOTA,
        retryable_same_iter=False,
        backoff_seconds=(),
        escalate_after=1,
        operator_action_required=True,
        resets_on_timer=False,
    ),
    ProviderLimit.MODEL_UNAVAILABLE: LimitPolicy(
        category=ProviderLimit.MODEL_UNAVAILABLE,
        # The same model is down — retrying it (same-iter) is futile, so this is
        # NOT retryable_same_iter and carries no backoff ladder. The heal is the
        # orthogonal axis: reroute_model. escalate_after=1 — route on the first
        # hit, do not wait. No operator action: a sibling model heals it without
        # a human (the difference from HARD_QUOTA). No timer reset: a down model
        # is not a window that reopens on a clock — only a different model fixes
        # it (hence resets_on_timer=False, but operator_action_required=False).
        retryable_same_iter=False,
        backoff_seconds=(),
        escalate_after=1,
        operator_action_required=False,
        resets_on_timer=False,
        reroute_model=True,
    ),
    ProviderLimit.NONE: LimitPolicy(
        category=ProviderLimit.NONE,
        retryable_same_iter=False,
        backoff_seconds=(),
        escalate_after=1,
        operator_action_required=False,
        resets_on_timer=False,
    ),
}


def policy_for(category: ProviderLimit) -> LimitPolicy:
    """Return the canonical :class:`LimitPolicy` for ``category``.

    Total over the enum — every :class:`ProviderLimit` member has a policy (a
    test asserts exhaustiveness, so a new category cannot ship without one).
    """
    return _POLICIES[category]


# ---------------------------------------------------------------------------
# Mappers — pure translators FROM each upstream taxonomy INTO the canonical
# category. They do NOT classify (the upstream classifier already did); they
# translate. Each takes the upstream token's str VALUE, so this module never
# imports the upstream class (keeps the job→dos dependency arrow one-way).
# ---------------------------------------------------------------------------

# rate_limit_classify.Kind values (job/scripts/rate_limit_classify.py).
_RATE_LIMIT_KIND_TO_CATEGORY: dict[str, ProviderLimit] = {
    "OVERLOADED": ProviderLimit.TRANSIENT_OVERLOAD,
    "RATE_LIMITED": ProviderLimit.USAGE_WINDOW,
    "CREDIT_LOW": ProviderLimit.HARD_QUOTA,
    "NONE": ProviderLimit.NONE,
}


def from_rate_limit_kind(kind: str) -> ProviderLimit:
    """Map a ``rate_limit_classify.Kind`` value → canonical category.

    Accepts the enum member or its ``str`` value (the enum is ``str``-valued,
    so ``str(Kind.OVERLOADED) == "OVERLOADED"``). Unknown → NONE (defensive:
    an unrecognized token must not masquerade as a real limit).
    """
    return _RATE_LIMIT_KIND_TO_CATEGORY.get(str(kind), ProviderLimit.NONE)


# agents.quota.QuotaErrorClass values (job/agents/quota/base.py).
_QUOTA_ERROR_CLASS_TO_CATEGORY: dict[str, ProviderLimit] = {
    "rpm_throttled": ProviderLimit.TRANSIENT_OVERLOAD,
    "transient_429": ProviderLimit.TRANSIENT_OVERLOAD,
    "daily_quota_exhausted": ProviderLimit.USAGE_WINDOW,
    "subscription_blackout": ProviderLimit.USAGE_WINDOW,
}


def from_quota_error_class(qec: str) -> ProviderLimit:
    """Map an ``agents.quota.QuotaErrorClass`` value → canonical category.

    This is the Bulkhead seam: the apply adapter keeps ``QuotaErrorClass``
    internally for its own backoff; at the dispatch boundary it maps UP into
    the canonical category. ``rpm_throttled``/``transient_429`` are short-timer
    server-side throttles → TRANSIENT_OVERLOAD; the daily/subscription caps are
    timer-reset windows → USAGE_WINDOW. (A genuine billing block surfaces as a
    HARD_QUOTA via the rate_limit_classify CREDIT_LOW path, not here.) Unknown →
    NONE.
    """
    return _QUOTA_ERROR_CLASS_TO_CATEGORY.get(str(qec), ProviderLimit.NONE)


# apply-next-loop Step-3 outcome tokens (job/.claude/skills/apply-next-loop).
_APPLY_OUTCOME_TOKEN_TO_CATEGORY: dict[str, ProviderLimit] = {
    "LLM-QUOTA-EXHAUSTED": ProviderLimit.USAGE_WINDOW,
    "LLM-QUOTA-EXHAUSTED-DURABLE": ProviderLimit.USAGE_WINDOW,
    # A NAMED model is down/retired ("…is currently unavailable") — the worker
    # never ran. Distinct from a quota window (the account is fine) and from a
    # correlated outage (the whole provider is down): a SIBLING model heals it,
    # so it is a provider-limit category with a reroute_model policy, not NONE.
    "LLM-MODEL-UNAVAILABLE": ProviderLimit.MODEL_UNAVAILABLE,
    "MODEL-UNAVAILABLE": ProviderLimit.MODEL_UNAVAILABLE,
    # CORRELATED-OUTAGE / BROWSER-SERVICE-UNAVAILABLE are NOT provider limits —
    # they are infra outages with their own stop policy; they map to NONE so a
    # caller asking "is this a provider limit?" gets a truthful no. (A correlated
    # outage takes down EVERY model, so re-routing to a sibling cannot heal it —
    # which is exactly why it is NOT MODEL_UNAVAILABLE.)
    "CORRELATED-OUTAGE": ProviderLimit.NONE,
    "BROWSER-SERVICE-UNAVAILABLE": ProviderLimit.NONE,
}


def from_apply_outcome_token(token: str) -> ProviderLimit:
    """Map an apply-next-loop Step-3 outcome token → canonical category.

    Both the transient (``LLM-QUOTA-EXHAUSTED``, Q==3 stop) and the durable
    (``LLM-QUOTA-EXHAUSTED-DURABLE``, measured-window stop-on-first) quota
    tokens are USAGE_WINDOW — the durability difference is a policy nuance
    (``resets_on_timer`` + a measured ``window_end``), not a different category.
    Unknown / non-limit tokens → NONE.
    """
    return _APPLY_OUTCOME_TOKEN_TO_CATEGORY.get(str(token), ProviderLimit.NONE)


# dos.result_state.TerminalClass values — the fold-site death-witness. This is
# the in-kernel bridge: result_state CLASSIFIES why a child died from its
# transcript; this maps that class INTO the canonical heal category so a fold
# site can go straight from "the child died" to "and the heal is: reroute". As
# with every other mapper it takes the str VALUE, never imports the class, so
# the pure-stdlib floor of this module (and the no-cycle rule) holds — even
# though result_state is itself a sibling kernel module.
_TERMINAL_CLASS_TO_CATEGORY: dict[str, ProviderLimit] = {
    "RATE_LIMIT": ProviderLimit.USAGE_WINDOW,
    "USAGE_LIMIT": ProviderLimit.USAGE_WINDOW,
    "AUTH": ProviderLimit.HARD_QUOTA,        # an auth/credential block needs a human
    "SERVER": ProviderLimit.TRANSIENT_OVERLOAD,  # a 500 is a transient server-side blip
    "MODEL_UNAVAILABLE": ProviderLimit.MODEL_UNAVAILABLE,
    # OTHER / NONE carry no actionable heal category — a caller asking "what is
    # the heal?" gets NONE (don't fabricate a reroute/backoff from an unknown).
    "OTHER": ProviderLimit.NONE,
    "NONE": ProviderLimit.NONE,
}


def from_terminal_class(cls: str) -> ProviderLimit:
    """Map a ``result_state.TerminalClass`` value → canonical heal category.

    The bridge from the fold-site death-witness (``result_state`` classifies a
    dead child's transcript) to the heal policy (``policy_for`` says what to do).
    A ``MODEL_UNAVAILABLE`` terminal class → the ``MODEL_UNAVAILABLE`` category,
    whose policy's ``reroute_model`` is the auto-heal signal a dispatcher reads to
    re-launch the unit on a sibling model. Accepts the enum member or its ``str``
    value. Unknown / non-heal class → NONE.

    ``TerminalClass`` is ``str``-valued but does NOT override ``__str__`` (so
    ``str(member)`` is ``"TerminalClass.X"``, not the token) — so we read the
    member's ``.value`` when present, falling back to ``str`` for a bare token.
    This is the no-cycle floor: we touch only the duck-typed ``.value`` attribute,
    never import the upstream class.
    """
    token = getattr(cls, "value", cls)
    return _TERMINAL_CLASS_TO_CATEGORY.get(str(token), ProviderLimit.NONE)


__all__ = [
    "ProviderLimit",
    "LimitPolicy",
    "policy_for",
    "from_rate_limit_kind",
    "from_quota_error_class",
    "from_apply_outcome_token",
    "from_terminal_class",
]
