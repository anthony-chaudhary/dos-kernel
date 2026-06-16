"""enforce_outcomes — fold the enforcement journal into LABELLED outcomes (docs/365).

The PEP-feedback leaf. DOS is "a sound PDP with no PEP" (docs/189 §A1): the kernel
*decides* an intervention verdict, a host consumer *acts* on it, and the act is
journaled as an `OP_ENFORCE` record on the lane WAL (`lane_journal.enforce_entry`).
What was missing is the loop that closes back the other way — *did acting on the
verdict turn out to be right?* — so the policy that drove the act can be tuned from
what actually happened, not from a hand-labelled corpus alone.

This module is that fold. It reads the SAME `OP_ENFORCE` records the decisions queue
already mines (`decisions._from_enforce_storms`), but where that fold answers "is this
deny-storm a pending HUMAN decision?", this one answers a different, tuning-shaped
question: **for each TARGET the enforcement seam acted on, was the act a FALSE-DENY
or a HELD catch?** The signal is non-forgeable and already on disk:

  * a `deny` for a target LATER followed by an `override-admit` for the same target =
    a **FALSE-DENY**. The operator armed the override window and the very edit the
    seam refused went through — so the refusal was, in hindsight, too aggressive. The
    override is the environment's correction, authored by the operator's armed
    window, never by the loop. (The docs/296 override-admit is the same SUCCESS
    signal `_from_enforce_storms` reads to RESET its breaker.)
  * a `deny` with NO subsequent override-admit = a **HELD catch**. The seam refused
    and the operator did not overturn it — the refusal stood.
  * a run of consecutive denies of the same target = a **storm** (the false-
    disruption the agent kept hitting; the docs/223 breaker shape, counted as a cost).

These three counts are exactly the axes a self-tuning enforcement loop optimizes:
drive FALSE-DENIES down, HOLD the catches, shrink the storms. They are the live-
evidence augmenter to the frozen `intervention_eval` corpus — the metric the
`drivers.enforce_tune` loop measures (docs/365).

⚓ Pure kernel leaf — stdlib + `lane_journal` only, no clock, no config, no host
names. `fold_enforce_outcomes`/`outcome_metric` take ENTRIES (a list of journal
dicts) and return frozen value types, so the suite folds synthetic journals without
touching disk (the `lane_journal.replay` / `decisions._superseded_refuse_seqs`
discipline). The one boundary reader (`read_outcomes`) is a thin
`lane_journal.read_all` shell, kept here so a caller has a one-liner; it is the only
I/O and is never called inside the fold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dos import lane_journal

# ---------------------------------------------------------------------------
# The three tiny readers — re-implemented HERE (not imported from `decisions`)
# so this stays a kernel leaf whose only DOS import is `lane_journal`. `decisions`
# imports `breaker`/`config`/`wedge_reason`; a kernel-clean fold must not pull that
# chain. The regexes/shapes are deliberately byte-identical to
# `decisions._enforce_target` / `_enforce_reason_class` / `_enforce_decision_tag`
# (the one home for the OP_ENFORCE record grammar is `lane_journal.enforce_entry`;
# both readers parse the same fields it writes). A test pins the two in lockstep.
# ---------------------------------------------------------------------------

# The runtime files a SELF_MODIFY reason names, e.g. "… own running code
# (src/dos/arbiter.py) — refusing …". The parenthetical is the TARGET key the fold
# groups on; a reason without it falls back to the whole reason text so identical
# retries still group together. Mirrors `decisions._ENFORCE_TARGET_RE`.
_ENFORCE_TARGET_RE = re.compile(r"own running\s+code \(([^)]*)\)")


def _enforce_reason_class(entry: dict) -> str:
    """The closed reason token on an ENFORCE entry, top-level or nested. Pure.

    `lane_journal.enforce_entry` lifts `reason_class` to the top level; an older
    Go fast-path writer left it only inside the nested `proposal` body. Read both
    so a Go-written deny is never invisible to the fold (mirrors
    `decisions._enforce_reason_class`).
    """
    rc = entry.get("reason_class")
    if not rc:
        proposal = entry.get("proposal")
        if isinstance(proposal, dict):
            rc = proposal.get("reason_class")
    return str(rc or "").strip().upper()


def _enforce_target(entry: dict) -> str:
    """The edit target an ENFORCE deny refused — the fold's grouping key. Pure.

    Mirrors `decisions._enforce_target`: prefer the SELF_MODIFY reason's
    parenthetical (`own running code (<path>)`); else the whole reason text so
    identical retries of a non-SELF_MODIFY deny still group together.
    """
    reason = str(entry.get("reason") or "")
    m = _ENFORCE_TARGET_RE.search(reason)
    if m:
        return m.group(1).strip()
    return reason


def _enforce_decision_tag(entry: dict) -> str:
    """deny / override-admit / "" for an ENFORCE entry, from its recorded shape. Pure.

    Prefers the nested `proposal.decision` (both writers record it); falls back to
    the lifted `intervention` (BLOCK = a deny) for a minimal/foreign record. Mirrors
    `decisions._enforce_decision_tag` exactly.
    """
    proposal = entry.get("proposal")
    if isinstance(proposal, dict) and proposal.get("decision"):
        return str(proposal.get("decision"))
    if str(entry.get("intervention") or "").strip().upper() == "BLOCK":
        return "deny"
    return ""


# ---------------------------------------------------------------------------
# The labelled outcome — one TARGET the enforcement seam acted on.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EnforceOutcome:
    """The folded enforcement outcome for ONE target the seam acted on.

    A frozen value the tuning loop reads — the live-evidence label `intervention_eval`
    cannot get from a frozen corpus. Every field is authored by the environment
    (the WAL the seam + the operator's override window wrote), never by the loop:

      holder        — the FIRST agent that hit this wall (for legibility — "who was
                      denied first"). The false-DENY correlation is target-scoped, not
                      holder-scoped (the operator who overrides is usually a different
                      holder than the denied agent), so this is descriptive only.
      target        — the edit the deny refused (the grouping key — a SELF_MODIFY
                      path, or the whole reason text for a non-SELF_MODIFY deny).
      reason_class  — the typed refusal token the deny carried (SELF_MODIFY, …).
      n_denies      — how many denies the seam recorded for this target (storm size).
      was_overridden— True iff a `deny` of this target was LATER followed by an
                      `override-admit` (by ANY holder — usually the operator): the
                      FALSE-DENY label. The operator armed the window and the refused
                      edit went through — the refusal was overturned.
      intervention  — the last rung the seam acted at on this target (BLOCK/DEFER/…),
                      lifted for legibility (the eval reads the policy, not this).

    `is_false_deny` and `is_held_catch` are the two mutually-exclusive labels the
    metric counts; a target with `n_denies == 0` (an override-admit with no prior
    deny — the seam never refused it) is NEITHER and is dropped by the fold.
    """

    holder: str
    target: str
    reason_class: str
    n_denies: int
    was_overridden: bool
    intervention: str = ""

    @property
    def is_false_deny(self) -> bool:
        """A deny the operator later overturned — the cell to drive DOWN."""
        return self.n_denies > 0 and self.was_overridden

    @property
    def is_held_catch(self) -> bool:
        """A deny that stood (never overridden) — the cell to HOLD."""
        return self.n_denies > 0 and not self.was_overridden

    def to_dict(self) -> dict:
        return {
            "holder": self.holder,
            "target": self.target,
            "reason_class": self.reason_class,
            "n_denies": self.n_denies,
            "was_overridden": self.was_overridden,
            "intervention": self.intervention,
            "is_false_deny": self.is_false_deny,
            "is_held_catch": self.is_held_catch,
        }


@dataclass(frozen=True)
class EnforceMetric:
    """The env-authored counts the self-tuning loop's metric rides — frozen.

    The fold's headline: three integers the tuning loop drives. `false_denies` is the
    cell to MINIMIZE (denies the operator overturned), `held_catches` the cell to HOLD
    (denies that stood), `storms` a cost (extra consecutive denies beyond the first on
    a target — the false-disruption the agent kept hitting). `work` is a single
    non-negative integer, HIGHER = better, so it drops straight into
    `improve.CandidateEvidence.work` for a loop that tunes against the LIVE journal
    (the corpus `net_task_delta` is the primary metric; this is the live augmenter).

    The work formula is deliberately simple and monotone in the right direction: a
    held catch is worth `+W`, a false-deny costs `-W`, each storm-excess deny costs
    `-1`. Offset by a constant floor so the result is non-negative (the kernel
    keep-gate compares magnitudes; a negative `work` is a contract error there). The
    weight `W` is a parameter so a host can reweight catches-vs-false-denies, but the
    default makes a false-deny exactly as costly as a held catch is valuable — the
    honest "do not buy a catch by paying a false-deny" stance.
    """

    n_pairs: int
    false_denies: int
    held_catches: int
    storms: int
    storm_denies: int  # total denies beyond the first across all targets (the cost base)
    work: int

    def to_dict(self) -> dict:
        return {
            "n_pairs": self.n_pairs,
            "false_denies": self.false_denies,
            "held_catches": self.held_catches,
            "storms": self.storms,
            "storm_denies": self.storm_denies,
            "work": self.work,
        }


# The default per-outcome weight: a held catch is worth +W, a false-deny costs -W.
_DEFAULT_WEIGHT = 1000
# The non-negative floor so `work` never goes below zero (the keep-gate contract).
# Sized so even an all-false-deny / all-storm journal stays >= 0 for realistic counts;
# a pathological journal that would underflow is clamped at 0 (the safe direction —
# a maximally-bad policy reads as zero work, never a negative the keep-gate rejects).
_WORK_FLOOR = 1_000_000


def fold_enforce_outcomes(entries: list[dict]) -> list[EnforceOutcome]:
    """Fold journal entries into one `EnforceOutcome` per acted-on TARGET.

    PURE — entries in, a list of frozen outcomes out, no disk, no clock. Walks the
    `OP_ENFORCE` records IN JOURNAL ORDER (the order the seam acted), grouping by
    **target** (the edit the seam refused). For each target it tracks the deny count
    and whether a `deny` was ever LATER followed by an `override-admit` — the
    FALSE-DENY label.

    Why TARGET, not (holder, target) — the load-bearing key choice (docs/365). The
    decisions queue (`_from_enforce_storms`) keys on `(holder, target)` because it
    tracks ONE agent's storm — the same agent retrying the same edit. But the
    false-DENY OUTCOME is a fact about the TARGET's legitimacy, and the operator who
    overrides is almost always a DIFFERENT holder than the denied agent: the agent
    gets denied, then the OPERATOR (or an armed operator session) admits the same
    edit (the live journal shows this directly — a deny by an agent uuid, the
    override by holder `S1` / an operator session / empty). Keying on
    `(holder, target)` would split the deny and its override into separate groups and
    MISS the correction entirely. The override establishes "this target was
    legitimately editable," so it labels every prior deny of that target a false-DENY
    — which is exactly the signal the tuner must drive down.

    The journal-order walk still matters: an override only labels a false-deny if at
    least one deny of that target PRECEDED it (an override with no prior deny is the
    seam never having refused — it labels nothing). Reads only fields
    `lane_journal.enforce_entry` writes, via the local `_enforce_*` readers (kept
    kernel-clean — no `decisions` import).

    Returns targets in first-seen order (stable, comparable), each with
    `n_denies > 0` (a target that only ever saw an override-admit and never a deny is
    not an enforcement outcome — the seam never acted to refuse it). The reported
    `holder` is the FIRST denied agent (for legibility — "who hit this wall first").
    """
    # target -> mutable accumulator: deny count, override-seen, first denied holder,
    # last rung, reason_class.
    acc: dict[str, dict] = {}
    order: list[str] = []
    for e in entries:
        if e.get("op") != lane_journal.OP_ENFORCE:
            continue
        tag = _enforce_decision_tag(e)
        if tag not in ("deny", "override-admit"):
            continue
        holder = str(e.get("holder") or "")
        target = _enforce_target(e)
        state = acc.get(target)
        if state is None:
            state = {
                "n_denies": 0,
                "overridden": False,
                "holder": "",
                "reason_class": _enforce_reason_class(e),
                "intervention": "",
            }
            acc[target] = state
            order.append(target)
        if tag == "deny":
            state["n_denies"] += 1
            if not state["holder"] and holder:
                state["holder"] = holder  # the FIRST agent that hit this wall
            rung = str(e.get("intervention") or "")
            if rung:
                state["intervention"] = rung
            # Keep the first non-empty reason_class seen (a deny carries it; an
            # override-admit may not).
            if not state["reason_class"]:
                state["reason_class"] = _enforce_reason_class(e)
        elif tag == "override-admit":
            # An override (by ANY holder — usually the operator, not the denied
            # agent) labels a FALSE-DENY iff a deny of this target already preceded
            # it in journal order. An override with no prior deny labels nothing.
            if state["n_denies"] > 0:
                state["overridden"] = True
    out: list[EnforceOutcome] = []
    for target in order:
        state = acc[target]
        if state["n_denies"] <= 0:
            continue  # only ever an override-admit, no deny — not an enforcement act
        out.append(EnforceOutcome(
            holder=str(state["holder"]),
            target=target,
            reason_class=str(state["reason_class"]),
            n_denies=int(state["n_denies"]),
            was_overridden=bool(state["overridden"]),
            intervention=str(state["intervention"]),
        ))
    return out


def outcome_metric(
    outcomes: list[EnforceOutcome], *, weight: int = _DEFAULT_WEIGHT
) -> EnforceMetric:
    """Tabulate the env-authored enforcement metric over folded outcomes. PURE.

    Counts the two labels + the storm cost and rolls them into a single non-negative
    `work` integer (higher = better) the kernel keep-gate compares. A held catch is
    worth `+weight`, a false-deny costs `-weight`, each deny beyond the first on a
    target costs `-1` (the storm tax). Offset by `_WORK_FLOOR` and clamped at 0 so the
    result is always non-negative (the `improve.CandidateEvidence.work` contract).

    `storms` is the number of targets with more than one deny (a deny-storm happened);
    `storm_denies` is the total excess denies across all targets (the cost base).
    """
    false_denies = sum(1 for o in outcomes if o.is_false_deny)
    held_catches = sum(1 for o in outcomes if o.is_held_catch)
    storms = sum(1 for o in outcomes if o.n_denies > 1)
    storm_denies = sum(max(0, o.n_denies - 1) for o in outcomes)
    raw = (held_catches * weight) - (false_denies * weight) - storm_denies
    work = max(0, _WORK_FLOOR + raw)
    return EnforceMetric(
        n_pairs=len(outcomes),
        false_denies=false_denies,
        held_catches=held_catches,
        storms=storms,
        storm_denies=storm_denies,
        work=work,
    )


def read_outcomes(config) -> list[EnforceOutcome]:
    """Boundary reader: fold the live lane journal into enforcement outcomes.

    The ONE I/O of this module — a thin `lane_journal.read_all` shell over the
    workspace's WAL, kept here so a caller (the `dos enforce-outcomes` verb, the
    `enforce_tune` driver's live-case gather) has a one-liner. Never called inside
    the fold. Degrades to `[]` on a missing/torn journal (the read-only-projection
    posture every `decisions` reader takes — a fold never crashes on a torn WAL).

    Path resolution honors the `DISPATCH_LANE_JOURNAL_PATH` env override first (the
    same precedence `lane_journal._journal_path` uses for every journal reader),
    falling back to the passed config's `paths.lane_journal` — so a redirected WAL
    (a test fixture, a non-default workspace) is read consistently with the rest of
    the kernel.
    """
    import os
    from pathlib import Path
    override = os.environ.get("DISPATCH_LANE_JOURNAL_PATH") or os.environ.get(
        "JOB_LANE_JOURNAL_PATH")
    path = Path(override) if override else config.paths.lane_journal
    try:
        entries = lane_journal.read_all(path)
    except Exception:  # noqa: BLE001 — a read fault must not crash a projection
        return []
    return fold_enforce_outcomes(entries)
