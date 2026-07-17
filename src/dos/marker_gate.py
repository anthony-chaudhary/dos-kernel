"""marker-gate — the PURE arming decision for the wait-marker budget (docs/274).

> **`marker_sensor` is the boundary I/O (the per-session no-op tally) and
> `noop_streak.classify` / `loop_decide.wait_marker_budget` is the pure
> count-vs-cap BUDGET verdict. This module is the third piece they were missing:
> the pure ARMING decision — "should this `Stop` event be subject to the budget at
> ALL?" — extracted out of `cli.cmd_hook_marker` so the docs/274 fix is one named,
> unit-tested function instead of two inline `if` blocks, and so its inputs are
> declarable per-workspace in `dos.toml [marker]`.**

The problem this closes (docs/274, the inversion it fixed)
==========================================================

A Claude Code `Stop` hook fires when Claude finishes **any** turn — interactive
included — not only on a keep-alive *poll* turn, and a `{"decision":"block"}`
FORCES the agent to keep working. The wait-marker budget's polarity assumes a
`Stop` means "the loop chose not to stop, i.e. it is about to poll again"; on a
bare/global Stop binding that premise is FALSE, so an unscoped budget blocks every
ordinary turn and MANUFACTURES the very keep-alive cache-replay waste it exists to
cap (docs/274: 44 sessions, 35 walled at the 4/4 cap, 0 actual polls). The fix is
to ARM the budget only when there is positive evidence this `Stop` is a poll inside
a loop, and to honor Claude Code's own infinite-loop backstop (`stop_hook_active`).

This module is the *policy* half of that fix: the two guards, made a pure function
of an injected environment + a declared `MarkerPolicy`. The CLI (`cmd_hook_marker`)
gathers the impure inputs (the Stop event JSON, the process `os.environ`, the
`--loop` flag) and calls `decide()`; the budget arithmetic stays in `noop_streak`.

The arming signal IS the missing evidence (the load-bearing idea)
================================================================

The budget's flaw was that its trigger ("this `Stop` is a poll") was an *assumption
about the moment*, not *evidence read from it* — which is exactly why it was the one
DOS hook that inverted (`dos hook stop` blocks only on a claim-vs-git contradiction;
`dos hook pretool` denies only on a real lease collision). The arming signal
(`--loop`, or a loop-scoping env var the dispatch loop sets) is the evidence the bare
event lacks — supplied from OUTSIDE the event, it proves the assumption holds. So
`decide()` is the discipline "an intervention is a safe default only if its trigger is
evidence" turned into code: no arming evidence → not armed → allow the stop.

Why the env-var NAMES are config, not hardcoded
===============================================

A dispatch loop signals "I am a loop" by exporting a sentinel env var. The two
built-in defaults (`DOS_LOOP`, the correlation-spine `CID_RUN_ID` the marker record
already rides) cover the in-tree `/loop` and the reference userland app — but a
different host runs a different loop with a different sentinel. So the arming env-var
names are a declared `MarkerPolicy.arm_on_env` tuple, the same "closed-set-as-data"
pattern as `reasons`/`stamp`/`noop_streak`'s `max_streak`: mechanism (the arming
decision) is the kernel; policy (which signals arm it, what the cap is, whether to
honor `stop_hook_active`) is config a workspace declares in `dos.toml [marker]`.

Kernel discipline (the litmus)
==============================

A PURE policy leaf — imports only stdlib (+ the declarative-config `tomllib` at
load time). Names no host and no vendor (the `stop_hook_active` field is a
DIALECT-NEUTRAL concept the Stop event carries; this module never branches on which
runtime is acting). `decide()` makes NO I/O at all — the caller injects the
environment as a plain mapping, so the arming truth table is replay-testable away
from `os.environ`. Passes `test_vendor_agnostic_kernel.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


# The two built-in loop-sentinel env-var names (docs/274). `DOS_LOOP` is the generic
# opt-in a host exports on a keep-alive loop; `CID_RUN_ID` is the correlation-spine
# run-id the dispatch loop already sets (and the marker record already stamps), so a
# loop driven through the spine arms the budget with no extra wiring. A workspace
# REPLACES/extends this via `dos.toml [marker] arm_on_env` (a host with its own loop
# sentinel names it there).
DEFAULT_ARM_ON_ENV: tuple[str, ...] = ("DOS_LOOP", "CID_RUN_ID")


@dataclass(frozen=True)
class MarkerPolicy:
    """The declared knobs for the wait-marker budget — policy, not mechanism.

    The "mechanism is kernel, policy is config" split (the `noop_streak.NoOpStreakPolicy`
    / `tool_stream.StreamPolicy` posture). A workspace declares its own in
    `dos.toml [marker]`; the generic default is interactive-safe (armed only by an
    explicit loop signal, never on an ordinary turn).

      max_streak — the **no-op-turn budget** handed to `noop_streak.classify`: the most
                   consecutive keep-alive/poll turns a loop may take before the next is
                   refused. Default 4 (`wait_marker_budget`'s cap, one below the
                   `keepalive_poll` telemetry flag at >=5, so the runtime refusal lands
                   one turn before the post-hoc alarm). Must be non-negative.

      arm_on_env — the env-var NAMES whose presence (any one, non-empty) ARMS the budget.
                   The evidence that this `Stop` is a poll inside a loop. Default
                   `("DOS_LOOP", "CID_RUN_ID")`; a host names its own loop sentinel here.
                   Empty () means "no env arms it" — only the explicit `--loop` flag does.

      respect_stop_hook_active — honor Claude Code's own infinite-loop backstop
                   (docs/274 Case C): when the Stop event carries `stop_hook_active:true`
                   (this stop is ALREADY being continued by a prior hook block), do NOT
                   re-block it. Default True. A host that deliberately wants to keep
                   escalating an already-continued stop sets this False (rarely correct —
                   it is how a budget becomes a forced march).
    """

    max_streak: int = 4
    arm_on_env: tuple[str, ...] = DEFAULT_ARM_ON_ENV
    respect_stop_hook_active: bool = True

    def __post_init__(self) -> None:
        if self.max_streak < 0:
            raise ValueError("max_streak must be non-negative")
        # Normalize arm_on_env to a tuple of non-empty strings (a list from TOML, a
        # stray empty/blank name) so `decide`'s `env.get(name)` walk is well-defined.
        names = tuple(
            n.strip() for n in self.arm_on_env if isinstance(n, str) and n.strip()
        )
        # frozen dataclass — set through object.__setattr__ (the canonical idiom).
        object.__setattr__(self, "arm_on_env", names)


DEFAULT_POLICY = MarkerPolicy()


@dataclass(frozen=True)
class ArmDecision:
    """Whether the budget arms for this `Stop`, with the operator-facing why.

    `armed` is the load-bearing bit: True → the caller proceeds to the budget verdict
    (and may block the Stop); False → the caller emits nothing (allow the stop), the
    fail-safe direction. `reason` is for `--debug` — it names WHICH guard decided, so an
    operator can see "not armed: no loop signal" vs "not armed: stop_hook_active" vs
    "armed: DOS_LOOP set" without reading the code.
    """

    armed: bool
    reason: str


def decide(
    *,
    stop_hook_active: bool,
    loop_flag: bool,
    env: Mapping[str, str],
    policy: MarkerPolicy = DEFAULT_POLICY,
) -> ArmDecision:
    """Decide whether the wait-marker budget arms for this `Stop`. PURE — no I/O.

    The two docs/274 guards, in order, as a function of injected inputs:

      1. `respect_stop_hook_active and stop_hook_active` → NOT armed. Claude Code's own
         infinite-loop backstop: this stop is already being continued by a prior hook
         block, so escalating it with another block is how a budget becomes a forced
         march. Allow the stop. (Checked FIRST so an already-continued stop is never
         re-blocked even inside a loop.)

      2. else armed ⟺ `loop_flag OR any(env.get(name) for name in policy.arm_on_env)`.
         The TRIGGER guard: a `Stop` hook fires on every finished turn, so the budget
         arms only with positive evidence this one is a keep-alive poll inside a loop —
         an explicit `--loop`, or a loop-sentinel env var the dispatch loop set. No
         such evidence → an ordinary interactive turn → NOT armed → allow the stop.

    `env` is injected (a `Mapping`, typically `os.environ`) so the truth table is
    replay-testable without mutating the process environment. A name maps to "present"
    when `env.get(name)` is truthy (a set, non-empty value) — an empty string does NOT
    arm (a host that exports `DOS_LOOP=""` to UNSET it is honored).
    """
    if policy.respect_stop_hook_active and stop_hook_active:
        return ArmDecision(
            armed=False,
            reason=(
                "stop_hook_active — stop already hook-continued; do not re-block; "
                "allow stop"
            ),
        )
    if loop_flag:
        return ArmDecision(armed=True, reason="armed by --loop")
    for name in policy.arm_on_env:
        if env.get(name):
            return ArmDecision(armed=True, reason=f"armed by env {name}")
    arm_names = ", ".join(policy.arm_on_env) if policy.arm_on_env else "(none)"
    return ArmDecision(
        armed=False,
        reason=(
            f"no loop signal (--loop / env {arm_names}) — ordinary turn, not a "
            f"keep-alive poll; allow stop (wait-marker budget arms only inside a loop)"
        ),
    )


# ---------------------------------------------------------------------------
# The declarative on-ramp — read a policy out of dos.toml [marker]
# (mirror noop_streak/tool_stream/stamp: policy_from_table + load_from_toml).
# ---------------------------------------------------------------------------
def policy_from_table(table: dict, *, base: MarkerPolicy = DEFAULT_POLICY) -> MarkerPolicy:
    """Turn a parsed `[marker]` TOML table into a `MarkerPolicy`. PURE (no I/O).

    `table` is `{max_streak?, arm_on_env?, respect_stop_hook_active?}` — the shape
    `tomllib.load(...)["marker"]` yields. A missing key falls back to `base` (default the
    generic), so a partial table tunes only what it names. A malformed value (a negative
    `max_streak`, a non-list `arm_on_env`) raises at construction
    (`MarkerPolicy.__post_init__`), so a bad declaration fails loudly at load (the
    `noop_streak.policy_from_table` posture).
    """
    if not table:
        return base
    arm = table.get("arm_on_env", base.arm_on_env)
    # A scalar string is accepted as a single name (the common one-sentinel case);
    # a list is the general case. Anything else falls back to base (fail-soft on shape,
    # since the value is advisory config, not a verdict input).
    if isinstance(arm, str):
        arm_tuple: tuple[str, ...] = (arm,)
    elif isinstance(arm, (list, tuple)):
        arm_tuple = tuple(arm)
    else:
        arm_tuple = base.arm_on_env
    return MarkerPolicy(
        max_streak=int(table.get("max_streak", base.max_streak)),
        arm_on_env=arm_tuple,
        respect_stop_hook_active=bool(
            table.get("respect_stop_hook_active", base.respect_stop_hook_active)
        ),
    )


def load_from_toml(
    path: "Path | str", *, base: MarkerPolicy = DEFAULT_POLICY
) -> MarkerPolicy:
    """Build a `MarkerPolicy` from a `dos.toml`'s `[marker]` table.

    Returns `base` unchanged when the file is absent, has no `[marker]` table, or
    `tomllib` is unavailable — the declarative path is purely additive, so a
    missing/empty config degrades to the generic default, never an error (the
    `noop_streak.load_from_toml` contract). A *present but malformed* table raises
    (`MarkerPolicy.__post_init__`). Reads with `utf-8-sig` to strip a PowerShell-written
    BOM (the `reasons`/`tool_stream`/`noop_streak` `load_from_toml` fix).
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
    table = data.get("marker")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
