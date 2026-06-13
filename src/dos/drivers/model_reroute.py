"""dos.drivers.model_reroute — the auto-HEALING actuator for a down model (propose-not-enact).

`model_health` (the kernel projection) answers "WHICH model is down across the
fleet's children and grandchildren, and what should I route AWAY from?" — but it
deliberately stops at the verdict: it names no model, holds no roster, launches
nothing (the roster of live models is host policy, and a domain-free kernel must
not carry it). This driver is the other half: it consumes that verdict, picks a
SIBLING model from a host-supplied roster, and PROPOSES the re-dispatch — closing
the goal's "auto-healing" word for a model that is down on a child or grandchild.

Why this is a DRIVER, not a kernel leaf
=======================================

It names the one thing the kernel may not: the model ROSTER (which models exist,
in what preference order). That is provider/vendor policy — the `provider_limit`
"Bulkhead" line — so it lives in `drivers/`, the only home for vendor names. The
kernel said "route away from <down models>"; this driver knows the roster and so
can say "route to <this sibling>".

The actuation boundary holds (the `watchdog.py` discipline)
===========================================================

"Auto-healing" here means PROPOSE the heal, exactly where `watchdog` proposes an
`OP_HALT`: it emits a `RerouteProposal` carrying the re-dispatch command (one paste
away), and NEVER launches a worker. *Delivering* the re-dispatch requires knowing
what a "unit of work" IS in the host (a lane? a prompt? a run-dir?) and how to
spawn it — domain knowledge a further host driver or the operator's paste supplies.
This driver stops at the propose line. It calls no `subprocess`, no `os`, no spawn
verb (pinned by `test_model_reroute_proposes_does_not_launch`).

Fail-closed (the honest floor)
==============================

If EVERY model in the roster is down (no sibling to route to), the proposal is an
ESCALATE, not a silent drop — an operator must act (add capacity / wait for a
model to come back). A down model with no parseable name (`<unnamed>`) still earns
a proposal: the operator is told "an unnamed model is down on <depth>:<id>; reroute
manually" rather than having the death swallowed.

⚓ It imports the kernel projection `model_health` (+ stdlib) and names a roster the
CALLER passes in — it hardcodes NO model. Pure core (`propose_reroutes`) + a thin
verdict-in/proposals-out shape; the only "I/O" is the caller handing it a roster.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Sequence

from dos.model_health import ModelHealth


class RerouteAction(str, enum.Enum):
    """What to do with one down model's stranded units.

      * REROUTE  — a sibling model is available; re-dispatch the unit(s) on it.
      * ESCALATE — no sibling is available (every roster model is down) OR the
                   down model is unnamed and cannot be excluded safely — an
                   operator must act. NEVER a silent drop.
    """

    REROUTE = "REROUTE"
    ESCALATE = "ESCALATE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class RerouteProposal:
    """One proposed heal for one down model. PROPOSE-ONLY — carries the command, never runs it.

      * action     — REROUTE or ESCALATE.
      * down_model  — the unavailable model the units died on.
      * sibling     — the chosen replacement model (REROUTE only; "" for ESCALATE).
      * units       — the source labels of the stranded units (e.g.
                      "grandchild:<id>") — what to re-dispatch.
      * command     — the host-supplied re-dispatch command with the sibling
                      substituted, one paste away. "" when no command template was
                      given (the operator supplies the spawn).
      * reason      — a short, log-greppable explanation.
    """

    action: RerouteAction
    down_model: str
    sibling: str
    units: tuple[str, ...]
    command: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "down_model": self.down_model,
            "sibling": self.sibling,
            "units": list(self.units),
            "command": self.command,
            "reason": self.reason,
        }


def _pick_sibling(down_models: frozenset[str], roster: Sequence[str]) -> Optional[str]:
    """The first roster model that is NOT down. PURE. None when every model is down.

    Roster order IS preference order — the host lists its models best-first, and the
    first non-down one wins. A case-insensitive compare so "Claude Fable 5" matches
    "claude fable 5" between the death text and the roster spelling.
    """
    down_lower = {m.strip().lower() for m in down_models}
    for model in roster:
        if model.strip().lower() not in down_lower:
            return model
    return None


def _command_for(template: str, sibling: str) -> str:
    """Substitute the sibling model into a host re-dispatch command template. PURE.

    The template uses `{model}` for the replacement (e.g. "claude -p --model {model}
    …"). No template → "" (the operator supplies the spawn). A template without the
    placeholder is returned verbatim (the host chose a fixed command)."""
    if not template:
        return ""
    return template.replace("{model}", sibling)


def propose_reroutes(
    health: ModelHealth,
    roster: Sequence[str],
    *,
    command_template: str = "",
) -> tuple[RerouteProposal, ...]:
    """Propose a heal per down model from a `model_health` verdict + a host roster. PURE.

    For each down model in `health.tallies` (most-deaths-first order preserved):

      * pick the first roster model that is not itself down (preference = roster
        order). Found → a REROUTE proposal naming the sibling + the stranded units +
        the substituted command. None (every model down) → an ESCALATE proposal.
      * an UNNAMED down model → ESCALATE (we cannot prove a sibling differs from an
        unnamed model, so we never auto-route past it — the operator reroutes by
        hand). The death is surfaced, never swallowed (the honest floor).

    Returns () when no model is down (nothing to heal). PROPOSE-ONLY: the proposals
    carry commands; this function launches nothing.
    """
    from dos.model_health import UNNAMED_MODEL

    if not health.any_model_down:
        return ()

    down = frozenset(t.model for t in health.tallies)
    proposals: list[RerouteProposal] = []
    for tally in health.tallies:
        units = tally.sources
        # A SUSPENDED model escalates BEFORE any sibling pick (issue #140): a
        # policy pull must surface to the operator, never silently reroute — a
        # sibling in the same capability class may also be pulled, draining budget
        # rerouting to another down model. This is the load-bearing #140 case.
        if getattr(tally, "suspended", False):
            proposals.append(
                RerouteProposal(
                    action=RerouteAction.ESCALATE,
                    down_model=tally.model,
                    sibling="",
                    units=units,
                    reason=(
                        f"model {tally.model!r} was SUSPENDED by policy on {tally.deaths} "
                        f"unit(s) — escalate, do NOT silently reroute: a sibling may also "
                        f"be pulled, and the operator must see the suspension. Wait for "
                        f"the policy to lift or re-dispatch by hand on a model you have "
                        f"confirmed is available"
                    ),
                )
            )
            continue
        if tally.model == UNNAMED_MODEL:
            proposals.append(
                RerouteProposal(
                    action=RerouteAction.ESCALATE,
                    down_model=tally.model,
                    sibling="",
                    units=units,
                    reason=(
                        f"an UNNAMED model is down on {tally.deaths} unit(s) — its name "
                        f"could not be parsed from the death text, so a sibling cannot be "
                        f"chosen safely; an operator must reroute these by hand"
                    ),
                )
            )
            continue
        sibling = _pick_sibling(down, roster)
        if sibling is None:
            proposals.append(
                RerouteProposal(
                    action=RerouteAction.ESCALATE,
                    down_model=tally.model,
                    sibling="",
                    units=units,
                    reason=(
                        f"model {tally.model!r} is down on {tally.deaths} unit(s) and "
                        f"EVERY roster model is also down — no sibling to route to; an "
                        f"operator must add capacity or wait for a model to recover"
                    ),
                )
            )
            continue
        proposals.append(
            RerouteProposal(
                action=RerouteAction.REROUTE,
                down_model=tally.model,
                sibling=sibling,
                units=units,
                command=_command_for(command_template, sibling),
                reason=(
                    f"model {tally.model!r} is down on {tally.deaths} unit(s) — "
                    f"re-dispatch them on the available sibling {sibling!r}"
                ),
            )
        )
    return tuple(proposals)


def render_text(proposals: Sequence[RerouteProposal]) -> str:
    """A compact, paste-ready heal plan. Mirrors the `watchdog`/`decisions` posture."""
    if not proposals:
        return "no down model — nothing to reroute"
    out: list[str] = ["# model-reroute heal plan (PROPOSED — nothing launched)"]
    for p in proposals:
        if p.action is RerouteAction.REROUTE:
            out.append(
                f"  REROUTE  {p.down_model} → {p.sibling}  "
                f"({len(p.units)} unit(s): {', '.join(p.units[:4])}"
                f"{'…' if len(p.units) > 4 else ''})"
            )
            if p.command:
                out.append(f"    $ {p.command}")
        else:
            out.append(
                f"  ESCALATE {p.down_model}  ({len(p.units)} unit(s)) — {p.reason}"
            )
    return "\n".join(out)


__all__ = [
    "RerouteAction",
    "RerouteProposal",
    "propose_reroutes",
    "render_text",
]
