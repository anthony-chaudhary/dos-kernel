"""SUP — the population verdict: *is the worker roster at its target, and what
should change to get it there?*

The supervisor is DOS's **init / PID-1** for a fleet of dispatch-loops. A
workspace declares a lane roster (its `[lanes]` taxonomy); each worker holds one
lane lease and runs a dispatch loop inside it. The supervisor counts the held
leases against a target population and emits a per-tick PLAN to close the gap —
SPAWN the lanes that are free, REAP the lanes whose worker is dead, FLAG the ones
that are spinning, HOLD the ones that are advancing. It keeps N workers alive the
way `init` keeps the system's services alive: declaratively, by reconciling the
observed population toward the desired one, never by trusting a worker's own
report that it is "still working."

This module is `liveness`'s sibling — a **pure** verdict function, the
`arbitrate()` / `classify()` shape:

    liveness.classify          (ProgressEvidence, policy)        -> LivenessVerdict
    supervise.supervise        (SuperviseEvidence, policy)       -> SuperviseVerdict
                               ^ THIS module

All I/O — replaying the lane journal into the live-lease set, classifying each
run's liveness, reading the clock, and (the output) actually spawning a
subprocess or writing a journal entry — happens in the CALLER (the `dos loop`
CLI's evidence-gather and the supervisor driver), never inside `supervise()`.
`supervise()` makes no subprocess, file, or clock call: the per-lane `Liveness`
is gathered at the boundary (one `liveness.classify` per run) and frozen onto the
evidence; the verdict only *reads* it. That is what lets the whole population
verdict be replay-tested on frozen fixtures, away from anything that needs a live
multi-minute fleet to reproduce (the `liveness` design value, restated for the
population axis).

`SuperviseVerdict` is a `verdict.py` **COUSIN, not a member** (verdict.py:41-47).
It shares the `classify` *shape* — closed-enum verdict + one-line `reason` +
echoed evidence + `to_dict()` — but its output is an **EFFECT decision** (spawn /
reap / hold / flag), not an epistemic belief about ground-truth state. Like
`arbitrate()` and `spawn/reap`, it is therefore deliberately NOT registered as a
`TypedVerdict`: forcing an effect-emitter under the epistemic Protocol would make
that type a god-type that means nothing. We match the value shape so the JSON /
MCP / renderer seam is uniform; we do not claim it answers "is this claim true?"

The disposition ladder, per lane (the five things a worker can be told):

  * HOLD  — the lane's worker is ADVANCING (≥1 commit / lease event since start).
            Leave it alone. Counts toward the alive population.
  * FLAG  — the lane's worker is SPINNING: alive (fresh heartbeat) but landing no
            forward delta. **Advisory only.** The supervisor FLAGS a spinning
            worker, it never auto-reaps it — a SPINNING run still counts as alive
            and keeps its lease. This mirrors `liveness`: SPINNING is the verdict
            with no enforcement home. A FLAG is ALWAYS emitted for a spinner.
  * PROPOSE_HALT — the lane's worker is SPINNING *and has been for longer than the
            policy's `spin_halt_after_ms` threshold*. This is the escalation of the
            FLAG that closes the "acting on a spin" gap (docs/82 LVN-3a, docs/90
            §5): the supervisor PROPOSES the operator/driver halt the stuck worker.
            It is **advisory, never autonomous** — the kernel emits a typed proposal
            and stops there (the docs/99 actuation boundary: the supervisor has no
            standing to halt a peer's control flow and no domain knowledge to kill
            its process). Critically it is **NOT a REAP**: the spinner KEEPS its
            lease, its region stays held, it is never a spawn candidate — so a
            proposal never frees a region or triggers a replacement spawn. The
            proposal rides its OWN verdict tuple (`proposed_halt`), never the
            `reap` channel, so `reap_stalled` semantics and the spawn-refill
            coupling are byte-identical. Off by default (`spin_halt_after_ms=None`).
  * REAP  — the lane's worker is STALLED: no fresh heartbeat, no commits — dead or
            hung. Its lease should be released/scavenged so the lane is free
            again. A reaped lane immediately re-enters the spawn-candidate pool in
            the SAME verdict: a STALLED worker yields BOTH a REAP plan AND, if the
            roster is still under target, a replacement SPAWN. (One tick:
            kill-and-refill, the supervisor's whole job.) The line between REAP and
            PROPOSE_HALT is *dead vs alive*: REAP frees a confirmed-dead lease (and
            is safe to enact because a second SIGTERM to a dead pid is a no-op);
            PROPOSE_HALT only *proposes* stopping a worker that is still alive, so
            the kernel must not act on it — stopping a live foreign process is the
            domain knowledge the kernel deliberately lacks (docs/99 §3).
  * SPAWN — the lane is FREE (no live lease) and the population is under its
            admissible target. Emit a spawn LanePlan; the CALLER turns it into a
            shell command line. The kernel never `Popen`s.

The population verdict (the roster-level outcome):

  * AT_TARGET         — alive == min(target, admissible) and nothing to spawn.
  * FILLING           — alive < target and there is admissible headroom; the
                        verdict carries the SPAWN plans that close the gap.
  * TARGET_UNREACHABLE— target > admissible: the roster cannot hold this many
                        concurrent disjoint workers no matter what. The reason
                        names the fix (declare more pairwise-disjoint concurrent
                        lanes in `dos.toml [lanes]`), it is not a transient state.
                        It STILL carries a fill-to-admissible SPAWN plan (a roster
                        that can hold one worker but was asked for three should
                        still run that one worker) — a caller acts on `.spawn`
                        regardless of outcome; the outcome only frames WHY the
                        population is below target. Also the **no-plan floor**: an
                        empty roster has admissible 0, so any positive target is
                        UNREACHABLE with no spawns — the verdict still returns, it
                        never crashes.
  * OVER_TARGET       — alive > target. The supervisor FLAGS the excess (advisory)
                        but **never reaps a healthy worker** to shrink the
                        population: reaping is reserved for STALLED runs. Choosing
                        which healthy worker to retire is an operator/driver call,
                        not a mechanical kernel one (the distrust-state /
                        distrust-judgment line again).

The **no-plan floor** (`test_verify_no_plan` sibling): `supervise()` must return
a verdict for an empty roster — `SuperviseEvidence(lanes=(), target=N)` yields
`TARGET_UNREACHABLE` with `admissible=0`, never an exception.

The **double-spawn race guard** (the `pending` field): between the tick that
emits a SPAWN and the tick where that worker's ACQUIRE lands in the journal, the
lane has no live lease but a spawn is already in flight. If the supervisor
re-spawned on it every tick in that window it would launch an UNBOUNDED stampede
of duplicate workers for one lane. The caller marks such a lane `pending=True`
(it has an in-flight spawn the journal hasn't reflected yet); a pending lane
COUNTS toward `alive` ("alive-or-coming"), occupies its region for the spawn
disjointness walk, but is NOT a held lease and is NOT a spawn candidate. The race
is thus BOUNDED to at most one extra worker per lane per in-flight window, not an
unbounded stampede — the supervisor analogue of an idempotent reconcile.

The **spawn soundness floor**: the SPAWN plan `supervise()` emits is disjoint by
construction. The spawn walk is a region-aware greedy seeded with the regions of
every already-alive worker (ADVANCING / counted-SPINNING / pending): a FREE lane
is emitted only when its tree is `_tree.lane_trees_disjoint` from every alive
region AND every spawn already chosen this tick. So the plan never proposes two
workers on overlapping lanes, and never proposes a worker onto a region a live
worker already holds — even though the worker's own `arbitrate` at Step 0 is the
authoritative gate (the supervisor's pick is an advisory hint, but an *honest*
one). This may emit FEWER than the headroom count when candidates collide with
held regions — that is correct: the headroom was illusory.

Admissible is computed PURE from the per-lane trees (docs/89 — a lane is a
region-lock over a glob-set). It imports the kernel sibling `dos._tree`
(`scope.py` does the same; the layering litmus is "no host, no I/O", not "no
sibling import"): two concurrent lanes may hold workers simultaneously only when
their trees are pairwise disjoint (`_tree.lane_trees_disjoint`, which treats an
empty or universal/leading-glob tree as never-disjoint — conservative, exactly
right). The generic default (`main` and `global`, both `**/*`) computes to
admissible 1: `main`'s universal tree is disjoint from nothing, so no second
concurrent worker can join. Correct.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from dos import _tree
from dos.liveness import Liveness


class Disposition(str, enum.Enum):
    """What the supervisor tells one lane to do — a closed, five-valued set.

    `str`-valued so it round-trips through a CLI stdout token / JSON field
    without a lookup table (mirrors `liveness.Liveness`).
    """

    SPAWN = "SPAWN"  # lane is FREE and under target — start a worker
    REAP = "REAP"    # lane's worker is STALLED (dead/hung) — release its lease
    HOLD = "HOLD"    # lane's worker is ADVANCING — leave it alone
    FLAG = "FLAG"    # lane's worker is SPINNING, or it is excess — advise, don't act
    # lane's worker is SPINNING *past the policy threshold* — the supervisor
    # PROPOSES the operator/driver halt it. NOT a reap (the lease is NOT released)
    # and NOT executed by the kernel — a typed proposal carried in its own verdict
    # tuple, on the docs/99 advisory / PDP-not-PEP floor. The escalation of the
    # FLAG `acting-on-spin` (docs/90 §5) the SPINNING branch's comment named unbuilt.
    PROPOSE_HALT = "PROPOSE_HALT"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class SuperviseOutcome(str, enum.Enum):
    """The roster-level population verdict — a closed, four-valued set.

    AT_TARGET / FILLING are the healthy steady-state and the converging-toward-it
    states; TARGET_UNREACHABLE / OVER_TARGET are the two off-target ends (too
    many lanes asked for vs the disjointness ceiling, and too many workers alive).
    """

    AT_TARGET = "AT_TARGET"                    # alive == target (within admissible)
    FILLING = "FILLING"                        # under target, headroom exists — spawning
    TARGET_UNREACHABLE = "TARGET_UNREACHABLE"  # target > admissible — declare more lanes
    OVER_TARGET = "OVER_TARGET"                # alive > target — excess flagged, not reaped

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class SupervisePolicy:
    """The knobs that shape the population verdict — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as
    `liveness.LivenessPolicy`. The defaults are GENERIC (target one worker, count
    spinners as alive, reap the dead); a workspace declares its own in
    `dos.toml [supervise]` read back through `SubstrateConfig`, the
    closed-config-as-data pattern (`[lanes]` / `[liveness]` / `[stamp]`).

      target                 — the desired number of live workers across the
                               roster. The supervisor fills up to it (bounded by
                               admissible) and flags above it.
      count_spinning_as_alive— whether a SPINNING worker counts toward the alive
                               population. Default True: a spinning run still holds
                               its lease and is up, so re-spawning its lane would
                               just duplicate the worker. (SPINNING is advisory —
                               we flag it, we don't pretend the lease is free.)
      reap_stalled           — whether a STALLED worker yields a REAP plan. Default
                               True: a dead/hung worker's lease must be released so
                               the lane is free to refill. Set False to make the
                               supervisor report-only (no reaps emitted).
      spin_halt_after_ms     — the acting-on-spin threshold (docs/90 §5): how long
                               a worker may be SPINNING before the supervisor
                               escalates its FLAG to a PROPOSE_HALT (a *proposed*,
                               never autonomous, stop). Default **None = off** — the
                               advisory-only default that reproduces today's
                               pure-FLAG behaviour byte-for-byte (the same opt-in
                               posture as `reap_stalled=False`). When set, a spinner
                               whose `spinning_age_ms` (gathered at the boundary)
                               meets or exceeds this many milliseconds ALSO yields a
                               PROPOSE_HALT plan. It is NEVER a reap: the spinner
                               keeps its lease either way. Keep it generous: too low
                               proposes halts on legitimate eventual-consistency
                               polling (the `tool_stream` false-resurface hazard).
      worker_launch_template — the operator-facing command the `dos loop` SPAWN plan
                               prints for each free lane (`{lane}` is substituted).
                               Default is the **vendor-neutral** bare skill
                               invocation `/dos-dispatch-loop --lane {lane}` — the
                               kernel names no agent-runtime BINARY (the `claude -p
                               "…"` wrapper is a vendor specific that belongs in a
                               driver / the host's `dos.toml [supervise]`, not the
                               kernel CLI). A host that wants the full launcher line
                               declares it here.
      max_concurrency        — the **derived-claim concurrency cap** (docs/283): the
                               number of workers the supervisor may keep alive on a
                               REPEATABLE auto-pick lane WITHOUT that many disjoint
                               trees being pre-declared in `dos.toml [lanes]`. Default
                               **None = off** — admissible stays the pre-declared
                               pairwise-disjoint static-lane count (today's behaviour,
                               byte-for-byte). When set, a roster that has at least one
                               `repeatable` lane (a fungible auto-pick handle whose
                               disjointness is enforced PER-PICK by each worker's own
                               `arbitrate` at Step 0, not by a fixed tree) may admit up
                               to this many concurrent workers — the supervisor synthesises
                               the spawn SLOTS onto the repeatable handle, and the arbiter
                               remains the authoritative per-pick gate (the supervisor's
                               pick was always an advisory hint, docstring ¶ "spawn
                               soundness floor"). This is the answer to "a concurrency
                               limit I don't have to declare lane-by-lane in advance":
                               declare ONE budget number, not N disjoint trees. It NEVER
                               lifts the cap above its own value, NEVER overrides a live
                               EXCLUSIVE lane (an exclusive worker still caps the
                               population at 1), and NEVER weakens the disjointness the
                               arbiter enforces — it only stops `_admissible` from
                               refusing a target the operator has explicitly budgeted for.
    """

    target: int = 1
    count_spinning_as_alive: bool = True
    reap_stalled: bool = True
    spin_halt_after_ms: Optional[int] = None
    worker_launch_template: str = "/dos-dispatch-loop --lane {lane}"
    max_concurrency: Optional[int] = None

    def __post_init__(self) -> None:
        if self.target < 0:
            raise ValueError("supervise target must be non-negative")
        if self.spin_halt_after_ms is not None and self.spin_halt_after_ms < 0:
            raise ValueError("supervise spin_halt_after_ms must be non-negative or None")
        if self.max_concurrency is not None and self.max_concurrency < 1:
            raise ValueError("supervise max_concurrency must be >= 1 or None")
        if "{lane}" not in self.worker_launch_template:
            raise ValueError(
                "supervise worker_launch_template must contain the '{lane}' "
                "placeholder (the per-lane substitution point)"
            )

    def to_dict(self) -> dict:
        """The JSON shape `dos doctor --json` publishes (the `cooldown`/`stamp`
        seam-report convention) — the knobs that shape the population verdict, so
        an operator/skill reads the active target + reap + spin-halt posture
        without re-parsing `dos.toml`."""
        return {
            "target": self.target,
            "count_spinning_as_alive": self.count_spinning_as_alive,
            "reap_stalled": self.reap_stalled,
            "spin_halt_after_ms": self.spin_halt_after_ms,
            "worker_launch_template": self.worker_launch_template,
            "max_concurrency": self.max_concurrency,
        }


DEFAULT_POLICY = SupervisePolicy()


@dataclass(frozen=True)
class LaneLiveness:
    """One lane's observed state, gathered by the CALLER before the call.

    No journal read, no `liveness.classify`, no clock inside the verdict — the
    arbiter rule. The CLI's evidence-gather (the boundary) replays the lane
    journal into the live-lease set (`lane_journal.replay`), runs one
    `liveness.classify` per held lease, reads each lane's tree from
    `cfg.lanes.tree_for(lane)`, sets `is_exclusive` from the METHOD
    `cfg.lanes.is_exclusive(lane)` (note: that is a method on `LaneTaxonomy`,
    while this is a bool field — fill the field FROM the method), then freezes
    the result here.

      lane          — the lane name (a key in `cfg.lanes.trees`).
      liveness      — the per-run `Liveness` verdict for the worker holding this
                      lane, or **None when the lane is FREE** (no live lease). A
                      None liveness on a non-pending lane is a spawn candidate.
      tree          — the lane's glob-set (`cfg.lanes.tree_for(lane)`), used for
                      the pairwise-disjointness / admissible computation. A lane
                      with no declared tree (`()`) is treated as universal-greedy
                      by `_tree.lane_trees_disjoint` (never disjoint).
      is_exclusive  — True if the lane is in `cfg.lanes.exclusive`: it never runs
                      alongside any other worker (it caps the whole population at
                      1 when it is the only thing the roster can admit).
      pending       — True if a spawn for this lane is in flight but its ACQUIRE
                      has not yet landed in the journal (the double-spawn race
                      window). A pending lane COUNTS toward alive but is NOT a held
                      lease and is NOT re-spawned. See the module docstring.
      spinning_age_ms— for a SPINNING lane, how long (ms) the worker has been
                      spinning — the staleness that MADE it SPINNING. Gathered at
                      the boundary from the SAME journal newest-heartbeat-age
                      `liveness.classify` already consumed (zero new I/O); the
                      verdict only READS it (the arbiter purity rule). `None` when
                      not gathered or not applicable (a FREE / ADVANCING lane); a
                      `None` here can never produce a PROPOSE_HALT — the kernel
                      never proposes a halt on absent evidence (fail-quiet).
      repeatable    — True if this lane is a FUNGIBLE auto-pick handle: its
                      disjointness is enforced PER-PICK by each worker's own
                      `arbitrate` at Step 0, not by a fixed pre-declared tree, so
                      MORE THAN ONE worker may hold it at once (each resolves the
                      handle to a distinct narrow per-pick claim). The CALLER sets
                      it from `cfg.lanes.autopick` membership AND non-exclusivity (an
                      exclusive lane is never repeatable). It is the seam the
                      `max_concurrency` cap rides: when the policy budgets a
                      concurrency higher than the static disjoint-tree count, the
                      supervisor may synthesise extra spawn slots ONTO a repeatable
                      lane (the derived-claim model, docs/283) instead of demanding
                      N pre-declared disjoint trees. Default False = the lane admits
                      at most one worker (the pre-declared static-tree behaviour).
    """

    lane: str
    liveness: Optional[Liveness] = None
    tree: tuple[str, ...] = ()
    is_exclusive: bool = False
    pending: bool = False
    spinning_age_ms: Optional[int] = None
    repeatable: bool = False

    def __post_init__(self) -> None:
        if not self.lane:
            raise ValueError("LaneLiveness.lane must be a non-empty lane name")
        if self.repeatable and self.is_exclusive:
            raise ValueError(
                "LaneLiveness cannot be both repeatable and exclusive — an "
                "exclusive lane runs alone, so it can never be a fungible "
                "multi-holder auto-pick handle"
            )


@dataclass(frozen=True)
class SuperviseEvidence:
    """Everything `supervise()` needs, gathered by the CALLER before the call.

    `lanes` is the full roster — one `LaneLiveness` per declared lane, in roster
    order (concurrent lanes then exclusive lanes, declaration-order, de-duped) so
    the SPAWN walk is deterministic. `target` is the desired live population
    (defaulted from policy but pinned here so the verdict echoes the exact target
    it judged against, the `liveness` evidence-echo discipline).
    """

    lanes: tuple[LaneLiveness, ...] = ()
    target: int = 1

    def __post_init__(self) -> None:
        if self.target < 0:
            raise ValueError("supervise evidence target must be non-negative")


@dataclass(frozen=True)
class LanePlan:
    """One per-lane instruction in the verdict — lane + disposition + reason.

    Pure data; the CALLER turns a SPAWN/REAP into a shell command line / journal
    write. A FLAG/HOLD is informational (the operator-facing tally row).
    """

    lane: str
    disposition: Disposition
    reason: str

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "disposition": self.disposition.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SuperviseVerdict:
    """The single verdict `supervise()` returns, with the evidence echoed back.

    `verdict` is the typed `SuperviseOutcome`. `reason` is a one-line
    operator-facing summary (the tally-row string). `spawn` / `reap` / `flag` /
    `proposed_halt` are the per-lane plans, split by what the caller acts on (spawn
    a process / reap a dead lease / surface advisory / surface a *proposed* halt of
    a live-but-stuck worker). `proposed_halt` is a SEPARATE channel from `reap` on
    purpose: a reap frees a confirmed-dead lease (the driver enacts it), a proposed
    halt only PROPOSES stopping a still-alive spinner (the driver surfaces it, never
    enacts it) — folding them would let `reap_stalled` policy or a driver's reap
    code accidentally act on a mere proposal, breaking the docs/99 advisory floor.
    `alive` and `admissible` are the two population counts the verdict turned on,
    carried so `dos loop --output json` emits the verdict *and the facts behind it*
    in one object (the renderer seam) — legible distrust: the operator sees not just
    FILLING but *why* (alive 1 < target 2, admissible 2). `to_dict` is the JSON shape.
    """

    verdict: SuperviseOutcome
    reason: str
    evidence: SuperviseEvidence
    spawn: tuple[LanePlan, ...] = ()
    reap: tuple[LanePlan, ...] = ()
    flag: tuple[LanePlan, ...] = ()
    proposed_halt: tuple[LanePlan, ...] = ()
    alive: int = 0
    admissible: int = 0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "alive": self.alive,
            "admissible": self.admissible,
            "target": self.evidence.target,
            "spawn": [p.to_dict() for p in self.spawn],
            "reap": [p.to_dict() for p in self.reap],
            "flag": [p.to_dict() for p in self.flag],
            "proposed_halt": [p.to_dict() for p in self.proposed_halt],
            "evidence": {
                "target": self.evidence.target,
                "lanes": [
                    {
                        "lane": ln.lane,
                        "liveness": ln.liveness.value if ln.liveness is not None else None,
                        "tree": list(ln.tree),
                        "is_exclusive": ln.is_exclusive,
                        "pending": ln.pending,
                        "spinning_age_ms": ln.spinning_age_ms,
                        "repeatable": ln.repeatable,
                    }
                    for ln in self.evidence.lanes
                ],
            },
        }


def _admissible(
    lanes: tuple[LaneLiveness, ...], max_concurrency: Optional[int] = None
) -> int:
    """The largest number of workers the roster could hold simultaneously. PURE.

    A lane is a region-lock over its `tree` (docs/89); two workers may run at once
    only when their trees are pairwise disjoint. So the admissible CONCURRENT
    population is the size of the largest set of concurrent lanes that are pairwise
    tree-disjoint (`_tree.lane_trees_disjoint`, which is conservative: an empty or
    universal/leading-glob tree is disjoint from nothing).

    Static computation (greedy, deterministic in roster order):

      * Consider only CONCURRENT lanes (is_exclusive=False). Walk them in roster
        order; admit a lane into the accumulating set S only if its tree is
        disjoint from every lane already in S. |S| is the admissible concurrent
        population. (A universal-tree lane like `main` `**/*` admits first and then
        blocks every later concurrent lane, so the count is 1 — correct: only one
        worker can safely own the whole tree.)
      * If there are NO concurrent lanes but ≥1 EXCLUSIVE lane in the roster, one
        exclusive worker can run alone → admissible = 1.
      * Empty roster → admissible = 0 (the no-plan floor).

    Exclusive lanes never *add* to a concurrent count (an exclusive worker runs
    alone by definition); they only matter when they are the roster's only option.

    The DERIVED-CLAIM ceiling (docs/283 — `max_concurrency`): the static count is
    blind to the fungible auto-pick model, where a lane is a HANDLE whose
    disjointness is enforced PER-PICK at acquire time (each worker's own
    `arbitrate` resolves the handle to a distinct narrow claim) rather than by a
    fixed pre-declared tree. A workspace that runs that model can hold many more
    than `static` workers without enumerating that many disjoint trees. So when
    `max_concurrency` is set AND the roster carries ≥1 REPEATABLE lane (a
    non-exclusive auto-pick handle), the admissible ceiling is lifted to
    `max(static, max_concurrency)` — BUT a live exclusive lane still caps the
    population at 1 (an exclusive worker runs alone, full stop), and the cap is
    never raised above its own declared value. With `max_concurrency` unset, or a
    roster with no repeatable lane, the ceiling is exactly the static count — the
    behaviour is byte-for-byte today's.
    """
    concurrent = [ln for ln in lanes if not ln.is_exclusive]
    admitted: list[LaneLiveness] = []
    for ln in concurrent:
        if all(
            _tree.lane_trees_disjoint(list(ln.tree), list(other.tree))
            for other in admitted
        ):
            admitted.append(ln)
    count = len(admitted)
    if count == 0:
        # No concurrent lane could be admitted. If the roster has any exclusive
        # lane at all, one exclusive worker can run alone.
        if any(ln.is_exclusive for ln in lanes):
            # An exclusive-only roster caps at 1 even under a concurrency budget —
            # the budget rides REPEATABLE auto-pick lanes, and an exclusive lane is
            # never repeatable (enforced in LaneLiveness.__post_init__).
            return 1
        return 0
    # A declared concurrency budget lifts the static ceiling, but ONLY when the
    # roster actually carries a fungible repeatable lane to ride it (else the budget
    # is meaningless — there is no handle a second worker could disjointly take).
    if max_concurrency is not None and any(ln.repeatable for ln in lanes):
        return max(count, max_concurrency)
    return count


def supervise(
    ev: SuperviseEvidence, policy: SupervisePolicy = DEFAULT_POLICY
) -> SuperviseVerdict:
    """Reconcile the observed worker population toward `target`. PURE — no I/O.

    Walks the roster once to classify each lane into a disposition, counts the
    alive population and the regions those workers hold, computes the admissible
    ceiling, then selects the spawn plan that closes the gap WITHOUT proposing an
    overlapping region. The whole thing is a frozen-evidence → typed-verdict
    function: every input (the per-lane `Liveness`, the clock that produced it,
    the journal it was folded from) was gathered at the caller boundary, exactly
    like `liveness.classify`.

    Per-lane ladder (step 1):
      * pending=True   -> HOLD-equivalent: counts toward alive ("alive-or-coming")
                          and occupies its region for the spawn-disjointness walk,
                          but emits NO plan and is NEVER a spawn candidate (the
                          double-spawn race guard).
      * liveness None  -> the lane is FREE: a spawn candidate (no plan yet; the
                          spawn walk in step 4 decides whether — and whether it can
                          disjointly — fill it).
      * ADVANCING      -> HOLD; counts toward alive; its region is held.
      * SPINNING       -> FLAG always (advisory — we never auto-reap a spinner).
                          ADDITIONALLY, if policy.spin_halt_after_ms is set and the
                          lane's spinning_age_ms meets it, emit a PROPOSE_HALT into
                          the SEPARATE `proposed_halt` channel (acting-on-spin,
                          docs/90 §5) — a *proposed* stop, never a reap: the spinner
                          keeps its lease and is never a spawn candidate.
                          If count_spinning_as_alive: counts toward alive and its
                          region is held. If NOT: report-only — it is NOT counted
                          alive and NOT refilled (a spinner still holds its lease;
                          the supervisor will not displace a live worker).
      * STALLED        -> if reap_stalled: emit a REAP and make the lane a spawn
                          candidate again (kill-and-refill in one tick). If not
                          reap_stalled: report-only, lane is not refilled. A dead
                          worker never counts as alive and never holds a region.
    """
    lanes = ev.lanes
    target = ev.target

    spawn_candidates: list[LaneLiveness] = []  # FREE / reaped lanes
    held_trees: list[tuple[str, ...]] = []     # regions occupied by alive workers
    reaps: list[LanePlan] = []
    flags: list[LanePlan] = []
    proposed_halts: list[LanePlan] = []        # spinners past the spin_halt threshold
    alive = 0

    for ln in lanes:
        if ln.pending:
            # A spawn is in flight; the ACQUIRE hasn't landed. Count it as
            # alive-or-coming, hold its region, emit nothing, never re-spawn.
            alive += 1
            held_trees.append(ln.tree)
            continue

        lv = ln.liveness
        if lv is None:
            # FREE lane — a spawn candidate, no plan emitted yet.
            spawn_candidates.append(ln)
            continue

        if lv == Liveness.ADVANCING:
            alive += 1
            # A repeatable (fungible auto-pick) lane holds NO fixed region — its
            # claim is narrowed per-pick by the arbiter — so a held repeatable lane
            # must NOT seed `held_trees` (it would block nothing real) and, crucially,
            # may still accept MORE workers up to the concurrency budget. We record
            # it as a re-spawnable handle below rather than locking its tree here.
            if ln.repeatable:
                spawn_candidates.append(ln)
            else:
                held_trees.append(ln.tree)
            continue

        if lv == Liveness.SPINNING:
            # Advisory FLAG always; whether it counts as alive is policy. A
            # spinner ALWAYS holds its lease, so it is NEVER a spawn candidate —
            # re-spawning its lane would try to displace a live worker. When the
            # policy does not count it as alive it is simply report-only (the
            # `reap_stalled=False` shape).
            flags.append(
                LanePlan(
                    lane=ln.lane,
                    disposition=Disposition.FLAG,
                    reason=(
                        "worker is SPINNING (alive, no forward delta) — advisory; "
                        "the supervisor flags a spinner, it never auto-reaps it"
                    ),
                )
            )
            # Acting-on-spin (docs/90 §5): escalate the FLAG to a *proposed* halt
            # when the spin has lasted past the policy threshold. This is the ONLY
            # net-new effect of the SPINNING branch — and it is purely additive:
            #   * it appends to `proposed_halts`, NOT to `spawn_candidates`, so the
            #     spinner is still never re-spawned;
            #   * it does NOT touch `alive` / `held_trees` (those stay governed
            #     solely by `count_spinning_as_alive` below), so the population /
            #     admissible math — and therefore the disjoint-by-construction spawn
            #     plan — is byte-identical to today's;
            #   * it NEVER releases the lease (unlike REAP), so the kernel proposes
            #     stopping a *live* worker without acting on it (the docs/99 floor).
            # Fail-quiet: a None `spinning_age_ms` (evidence not gathered) can never
            # produce a proposal — the kernel does not propose a halt on absent
            # evidence. Threshold is a `>=` so an exactly-at-threshold spin escalates.
            if (
                policy.spin_halt_after_ms is not None
                and ln.spinning_age_ms is not None
                and ln.spinning_age_ms >= policy.spin_halt_after_ms
            ):
                proposed_halts.append(
                    LanePlan(
                        lane=ln.lane,
                        disposition=Disposition.PROPOSE_HALT,
                        reason=(
                            f"worker has been SPINNING for {ln.spinning_age_ms}ms "
                            f"(>= spin_halt_after_ms {policy.spin_halt_after_ms}) — "
                            f"PROPOSING a halt (advisory; the operator/driver enacts "
                            f"it, the kernel never kills a live worker)"
                        ),
                    )
                )
            if policy.count_spinning_as_alive:
                alive += 1
                held_trees.append(ln.tree)
            continue

        if lv == Liveness.STALLED:
            if policy.reap_stalled:
                reaps.append(
                    LanePlan(
                        lane=ln.lane,
                        disposition=Disposition.REAP,
                        reason=(
                            "worker is STALLED (no fresh heartbeat, no commits) — "
                            "release the lease; the lane refills if under target"
                        ),
                    )
                )
                # A reaped lane is FREE again this very tick — re-enter the pool.
                spawn_candidates.append(ln)
            # reap_stalled=False: report-only, lane is neither reaped nor refilled
            # (and a dead worker does NOT count as alive — it holds no real lease).
            continue

    admissible = _admissible(lanes, policy.max_concurrency)

    # How many we still want to start: never exceed admissible, never go negative.
    spawn_needed = max(0, min(target, admissible) - alive)

    # The spawn soundness floor: walk candidates in roster order, concurrent lanes
    # first (an exclusive lane only fills if nothing concurrent can — it would cap
    # the population at 1), and admit a candidate ONLY when its region is disjoint
    # from every already-alive worker's region AND every spawn already chosen this
    # tick. The emitted plan is therefore disjoint by construction — it can never
    # propose two workers on overlapping lanes, nor a worker onto a held region.
    # (May emit fewer than spawn_needed when candidates collide — correct: the
    # headroom was illusory.) `sorted` is stable, so roster order holds within each
    # exclusivity group.
    #
    # The derived-claim exception (docs/283 `max_concurrency`): a REPEATABLE free
    # lane is a fungible auto-pick HANDLE, not a fixed-tree region-lock — each
    # worker that takes it resolves it (via its own `arbitrate` at Step 0) to a
    # DISTINCT narrow per-pick claim. So a repeatable lane may be emitted MORE THAN
    # ONCE (one SPAWN per synthesised slot, up to spawn_needed) and its tree is NOT
    # added to `chosen_trees` (it holds no fixed region to collide with). The
    # per-pick disjointness is the arbiter's job, not the supervisor's — the
    # supervisor only budgets the SLOT COUNT (the operator's declared
    # max_concurrency), exactly the "a concurrency limit not declared lane-by-lane"
    # ask. A non-repeatable (fixed-tree) lane keeps the old single-emit, region-locking
    # behaviour byte-for-byte.
    # Order: fixed-tree concurrent lanes FIRST (they fill exactly as before, region
    # by region), THEN repeatable handles (first one slot per handle, then any
    # derived-budget overflow without a region lock), THEN exclusive lanes (only if
    # nothing else can fill). The sort key buckets free/reaped repeatable handles
    # before held repeatable handles so a standing autopick roster fills other free
    # concrete lanes before duplicating a lane that is already ADVANCING. `sorted`
    # is stable so roster order holds within each bucket. This keeps the no-
    # `max_concurrency` fixed-lane path byte-for-byte (no lane is repeatable there,
    # so the key degenerates to the old `is_exclusive` sort).
    def _spawn_order_key(ln: LaneLiveness) -> tuple[int, int]:
        if ln.is_exclusive:
            return (2, 0)
        if ln.repeatable:
            held_handle = ln.liveness == Liveness.ADVANCING
            return (1, 1 if held_handle else 0)
        return (0, 0)

    ordered = sorted(spawn_candidates, key=_spawn_order_key)
    spawns: list[LanePlan] = []
    chosen_trees: list[tuple[str, ...]] = []
    repeatable_candidates = [ln for ln in ordered if ln.repeatable]

    def _append_repeatable_spawn(ln: LaneLiveness) -> None:
        spawns.append(
            LanePlan(
                lane=ln.lane,
                disposition=Disposition.SPAWN,
                reason=(
                    f"repeatable auto-pick lane under target "
                    f"(alive {alive} < target {target}, admissible "
                    f"{admissible}, max_concurrency {policy.max_concurrency}) "
                    f"— spawn a worker; the arbiter narrows its per-pick claim"
                ),
            )
        )

    for ln in ordered:
        if len(spawns) >= spawn_needed:
            break
        if ln.repeatable:
            # A fungible handle gets ONE first-pass slot here. This preserves the
            # concrete-lane roster case where every autopick lane is repeatable:
            # fill distinct lane names before using a handle twice. Any remaining
            # derived-budget slots are synthesized in the overflow pass below,
            # before exclusive lanes are considered.
            _append_repeatable_spawn(ln)
            continue
        if ln.is_exclusive:
            # Exclusive lanes are the last resort. Repeatable overflow is handled
            # before this pass, matching the old "repeatables soak before exclusive"
            # ordering while still giving distinct repeatable handles a first slot.
            continue
        disjoint = all(
            _tree.lane_trees_disjoint(list(ln.tree), list(t))
            for t in held_trees + chosen_trees
        )
        if not disjoint:
            continue
        spawns.append(
            LanePlan(
                lane=ln.lane,
                disposition=Disposition.SPAWN,
                reason=(
                    f"lane is free and the roster is under target "
                    f"(alive {alive} < target {target}, admissible {admissible}) "
                    f"— spawn a worker"
                ),
            )
        )
        chosen_trees.append(ln.tree)

    repeatable_i = 0
    while len(spawns) < spawn_needed and repeatable_candidates:
        ln = repeatable_candidates[repeatable_i % len(repeatable_candidates)]
        _append_repeatable_spawn(ln)
        repeatable_i += 1

    for ln in ordered:
        if len(spawns) >= spawn_needed:
            break
        if not ln.is_exclusive:
            continue
        disjoint = all(
            _tree.lane_trees_disjoint(list(ln.tree), list(t))
            for t in held_trees + chosen_trees
        )
        if not disjoint:
            continue
        spawns.append(
            LanePlan(
                lane=ln.lane,
                disposition=Disposition.SPAWN,
                reason=(
                    f"lane is free and the roster is under target "
                    f"(alive {alive} < target {target}, admissible {admissible}) "
                    f"— spawn a worker"
                ),
            )
        )
        chosen_trees.append(ln.tree)

    # Step 5 — the population verdict. Precedence note: TARGET_UNREACHABLE
    # deliberately dominates OVER_TARGET — a roster whose disjointness ceiling is
    # below target is the operator's FIRST lever (raise the ceiling), so it is the
    # more actionable verdict even when the population also happens to be over the
    # (unreachable) target. TARGET_UNREACHABLE still carries the fill-to-admissible
    # spawns computed above (run the workers the roster CAN hold).
    if target > admissible:
        outcome = SuperviseOutcome.TARGET_UNREACHABLE
        reason = (
            f"target {target} exceeds admissible {admissible}: the roster cannot "
            f"hold that many pairwise-disjoint concurrent workers. Declare more "
            f"disjoint concurrent lanes in dos.toml [lanes] to raise the ceiling."
        )
    elif alive > target:
        outcome = SuperviseOutcome.OVER_TARGET
        reason = (
            f"alive {alive} exceeds target {target}: the excess is FLAGGED, not "
            f"reaped — the supervisor never retires a healthy worker (that is an "
            f"operator/driver call). Reaping is reserved for STALLED runs."
        )
        # Flag the healthy excess (advisory). Do NOT emit a reap. Pick the excess
        # from currently-held ADVANCING lanes in reverse roster order so the FLAG
        # is deterministic. NOTE: spinners contributing to the over-count are
        # already FLAGged in the per-lane pass above, and a transient `pending`
        # excess is intentionally not re-flagged (it self-resolves when the ACQUIRE
        # lands), so this best-effort advisory re-flags only the ADVANCING excess.
        excess = alive - target
        held_advancing = [
            ln
            for ln in lanes
            if not ln.pending and ln.liveness == Liveness.ADVANCING
        ]
        for ln in reversed(held_advancing):
            if excess <= 0:
                break
            flags.append(
                LanePlan(
                    lane=ln.lane,
                    disposition=Disposition.FLAG,
                    reason=(
                        f"healthy worker beyond target {target} (alive {alive}) — "
                        f"flagged as excess; retiring it is an operator decision"
                    ),
                )
            )
            excess -= 1
    elif spawns:
        outcome = SuperviseOutcome.FILLING
        reason = (
            f"alive {alive} < target {target} (admissible {admissible}); spawning "
            f"{len(spawns)} worker(s) to fill the roster"
        )
    else:
        outcome = SuperviseOutcome.AT_TARGET
        reason = (
            f"alive {alive} at target {min(target, admissible)} "
            f"(admissible {admissible}) — roster is full, nothing to spawn"
        )

    return SuperviseVerdict(
        verdict=outcome,
        reason=reason,
        evidence=ev,
        spawn=tuple(spawns),
        reap=tuple(reaps),
        flag=tuple(flags),
        proposed_halt=tuple(proposed_halts),
        alive=alive,
        admissible=admissible,
    )


# ---------------------------------------------------------------------------
# The `[supervise]` config seam — modelled on `dos.cooldown` / `dos.stamp`.
# ---------------------------------------------------------------------------
# The supervisor is DOS's always-on "separate program"; its POLICY (how many
# workers to keep alive, whether a spinner counts as up, whether to reap the
# dead) is the same mechanism/policy split every other seam draws — the kernel
# owns the population verdict, the workspace owns the numbers. Before this seam,
# `dos loop --target N` was the ONLY way to set the target and the two booleans
# were unreachable from the operator surface (the CLI hardcoded the policy). Now
# a workspace declares them ONCE in `dos.toml [supervise]` and BOTH the `dos loop`
# emitter and the long-lived watchdog driver read the same declaration; an
# explicit `--target` still overrides the config value at the call boundary (a
# one-off run wanting a different population than the standing default).


def policy_from_table(
    table: dict, *, base: SupervisePolicy = DEFAULT_POLICY
) -> SupervisePolicy:
    """Build a `SupervisePolicy` from a parsed `[supervise]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted fields inherit. An
    unknown key raises (the `cooldown.policy_from_table` / `stamp.convention_from_table`
    posture — a typo'd knob is a loud error, not a silent no-op). The validated
    shape: ``target`` must be a non-negative int (delegated to
    `SupervisePolicy.__post_init__`); the two booleans must be real bools;
    ``spin_halt_after_ms`` (or the ergonomic ``spin_halt_after_minutes``) must be a
    non-negative number, and an explicit ``spin_halt_after_ms = 0`` / absent leaves
    it at the base (None = the acting-on-spin escalation stays off).
    """
    if not isinstance(table, dict):
        raise ValueError(f"[supervise] must be a table, got {type(table).__name__}")
    known = {
        "target", "count_spinning_as_alive", "reap_stalled",
        "spin_halt_after_ms", "spin_halt_after_minutes",
        "worker_launch_template", "max_concurrency",
    }
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[supervise] has unknown key(s) {sorted(unknown)}; known keys are {sorted(known)}"
        )
    if "spin_halt_after_ms" in table and "spin_halt_after_minutes" in table:
        raise ValueError(
            "[supervise] declares both spin_halt_after_ms and spin_halt_after_minutes; "
            "pick one"
        )

    target = base.target
    if "target" in table:
        v = table["target"]
        # A TOML bool is an int subclass; reject it so `target = true` is a clear
        # error rather than silently meaning 1 (the `cooldown._int` guard).
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"[supervise].target must be a non-negative integer, got {type(v).__name__}"
            )
        target = v

    def _bool(key: str, current: bool) -> bool:
        if key not in table:
            return current
        v = table[key]
        if not isinstance(v, bool):
            raise ValueError(f"[supervise].{key} must be a boolean, got {type(v).__name__}")
        return v

    spin_halt_after_ms = base.spin_halt_after_ms
    if "spin_halt_after_ms" in table:
        v = table["spin_halt_after_ms"]
        # A TOML bool is an int subclass; reject it (the `target`/`cooldown` guard).
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"[supervise].spin_halt_after_ms must be a non-negative number, "
                f"got {type(v).__name__}"
            )
        spin_halt_after_ms = int(v)
    elif "spin_halt_after_minutes" in table:
        v = table["spin_halt_after_minutes"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"[supervise].spin_halt_after_minutes must be a non-negative number, "
                f"got {type(v).__name__}"
            )
        spin_halt_after_ms = int(v * 60 * 1000)

    worker_launch_template = base.worker_launch_template
    if "worker_launch_template" in table:
        v = table["worker_launch_template"]
        if not isinstance(v, str):
            raise ValueError(
                f"[supervise].worker_launch_template must be a string, got {type(v).__name__}"
            )
        # `{lane}` presence is validated in __post_init__ (loud on a missing
        # placeholder, the same posture as the other knobs).
        worker_launch_template = v

    max_concurrency = base.max_concurrency
    if "max_concurrency" in table:
        v = table["max_concurrency"]
        # A TOML bool is an int subclass; reject it (the `target` guard). >= 1 is
        # validated in __post_init__ (a budget below 1 is a clear error, not 0-cap).
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"[supervise].max_concurrency must be a positive integer or absent, "
                f"got {type(v).__name__}"
            )
        max_concurrency = v

    return SupervisePolicy(
        target=target,
        count_spinning_as_alive=_bool("count_spinning_as_alive", base.count_spinning_as_alive),
        reap_stalled=_bool("reap_stalled", base.reap_stalled),
        spin_halt_after_ms=spin_halt_after_ms,
        worker_launch_template=worker_launch_template,
        max_concurrency=max_concurrency,
    )


def load_from_toml(
    path, *, base: SupervisePolicy = DEFAULT_POLICY
) -> SupervisePolicy:
    """Build a `SupervisePolicy` from a `dos.toml`'s `[supervise]` table.

    Returns ``base`` unchanged when the file is absent, has no `[supervise]`
    table, or `tomllib` is unavailable. A present-but-malformed table raises.
    Mirrors `cooldown.load_from_toml` (incl. the `utf-8-sig` BOM strip)."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return base
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return base
    data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
    table = data.get("supervise")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)


# ---------------------------------------------------------------------------
# The roster-order-sensitivity lint — the spawn-ranking "descope" (docs/210 §pivot).
# ---------------------------------------------------------------------------
# Value-aware spawn RANKING (fill the highest-value free lane first) was
# investigated and DECLINED: the spawn walk is a greedy disjointness walk whose
# ORDER changes the outcome only under a triple-rare condition — two CONCURRENT
# lanes whose regions OVERLAP, while the population is capacity-limited so the walk
# can't fill them both. In the designed disjoint-concurrent norm (docs/89) every
# free lane spawns regardless of order, so ranking is a measured no-op; and the one
# legitimate value-ordering seam already exists (the arbiter's `rank_key`, docs/91),
# so a second supervisor ranker would be debt. The honest fix for the rare
# order-sensitive case is therefore a CONFIG-TIME lint, not a runtime ranker: name
# the overlapping-concurrent-lane pairs so the operator declares disjoint lanes (or
# marks one exclusive), removing the order-sensitivity at its source. Pure + read-only.


def overlapping_concurrent_lanes(
    lanes: tuple[tuple[str, tuple[str, ...], bool], ...],
) -> tuple[tuple[str, str], ...]:
    """The CONCURRENT lane PAIRS whose regions overlap — the only roster shape in
    which the spawn walk's ORDER changes which lanes get filled. PURE.

    ``lanes`` is ``(lane_name, tree, is_exclusive)`` per declared lane (the caller
    gathers it from `cfg.lanes`). Two lanes are an overlap finding iff BOTH are
    concurrent (an exclusive lane runs alone — it never co-spawns, so its overlap
    is moot) AND their trees are NOT disjoint by `_tree.lane_trees_disjoint` (the
    same predicate the admissible computation uses; a treeless/universal lane is
    never disjoint, so it overlaps every concurrent peer — correct, that lane caps
    concurrency at 1 and makes the rest of the roster order-sensitive). Pairs are
    returned in a stable (name-sorted) order, each pair name-sorted, so the lint is
    deterministic. An empty tuple means the roster is order-INSENSITIVE: every free
    lane that can be admitted is disjoint from every other, so spawn order is
    irrelevant and value-aware ranking would be a no-op (the investigated finding).
    """
    concurrent = [(name, tree) for (name, tree, exclusive) in lanes if not exclusive]
    pairs: list[tuple[str, str]] = []
    for i in range(len(concurrent)):
        for j in range(i + 1, len(concurrent)):
            a_name, a_tree = concurrent[i]
            b_name, b_tree = concurrent[j]
            if not _tree.lane_trees_disjoint(list(a_tree), list(b_tree)):
                pairs.append(tuple(sorted((a_name, b_name))))  # type: ignore[arg-type]
    return tuple(sorted(set(pairs)))
