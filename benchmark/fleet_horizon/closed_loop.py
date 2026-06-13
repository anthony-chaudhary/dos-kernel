"""Closed-loop arm — the SAME workload under the REAL DOS kernel.

This is the centerpiece: it drives the actual kernel, no mocks.

  * **arbitrate** — before a worker writes, the arm asks `dos.arbiter.arbitrate`
    whether its file-tree footprint collides with a live lease. A colliding
    write into the shared area is REFUSED/deferred (the worker retries later) —
    so the silent overwrites the open loop banked never happen.
  * **verify** — when a worker CLAIMS a phase shipped, the arm does NOT believe
    it. It checks `dos.oracle.is_shipped` against GROUND TRUTH derived from a
    real git repo: a phase counts as shipped iff a real commit closing it exists.
    A lie (claim, no commit) → `shipped=False` → refused, never banked.
  * **spine** — every effort gets a `run_id` lineage and every lease decision is
    journaled (`dos.lane_journal`), so "what did this fleet actually do" is a
    replay, not a guess.

Ground truth is a REAL git repo (`tempfile` + `git init`): a worker that "really
commits" makes an actual commit; a worker that lies makes none. The registry the
oracle reads is reconstructed FROM that git log — so the kernel believes git, not
the worker. The "lie = no commit" property is therefore literally checkable by
hand with `git log` in the temp repo, which is the strongest honesty guarantee
(`README.md` §honesty).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import dataclasses

from dos import arbiter, oracle, run_id, lane_journal, scope, liveness
from dos.config import SubstrateConfig, LaneTaxonomy, default_config

from . import metrics
from .agent import FailureModel
from .metrics import Event, score, Metrics
from .trajectory import TrajectoryStep, step_from_claim
from .workload import Workload, interleave, Phase


def _git(repo: Path, *args: str) -> str:
    """Run a git command in `repo`, return stdout. Raises on failure."""
    res = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fleet@bench.local")
    _git(repo, "config", "user.name", "FleetBench")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("fleet benchmark repo\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "root: init")


def _real_commit(repo: Path, phase: Phase, files: tuple[str, ...]) -> str:
    """Write the phase's files for real and commit. Returns the short sha.

    The commit subject carries the (effort, phase_id) so a git-log grep can find
    it — making the oracle's ground-truth check shellable and hand-checkable.
    """
    for rel in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # append (don't truncate) so a later real writer to a shared file is a
        # real change git will record, not a no-op.
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{phase.effort} {phase.phase_id}\n")
    _git(repo, "add", "-A")
    subject = f"{phase.effort}: {phase.phase_id} — ship"
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "--short", "HEAD")


def _git_grep_fallback(repo: Path):
    """A REAL grep fallback: shell `git log --grep` for the phase's ship subject.

    This is the belt-and-suspenders rung. We key the registry off real commits
    too, but wiring a genuine git-log grep here means the oracle's verdict rests
    on `git` output we did not synthesize — the honesty property the README
    promises ("checkable by hand"). Returns a ShipVerdict.
    """
    def fallback(plan: str, phase: str) -> oracle.ShipVerdict:
        # Search for a commit whose subject names this (effort, phase_id).
        token = f"{phase} — ship"
        try:
            out = subprocess.run(
                ["git", "log", "--all", "--grep", token, "--format=%h %s", "-1"],
                cwd=str(repo), capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            return oracle.ShipVerdict(plan=plan, phase=phase, shipped=False, source="grep")
        line = out.stdout.strip()
        if out.returncode == 0 and line and token in line:
            sha = line.split(" ", 1)[0]
            return oracle.ShipVerdict(plan=plan, phase=phase, shipped=True,
                                      sha=sha, source="grep")
        return oracle.ShipVerdict(plan=plan, phase=phase, shipped=False, source="grep")
    return fallback


def _bench_config(repo: Path, workload: Workload) -> SubstrateConfig:
    """A SubstrateConfig whose lane taxonomy = the fleet's efforts.

    Each effort is a concurrent cluster lane whose tree is its private subtree;
    the shared area is NOT in any lane's private tree, so two efforts both
    reaching into `shared/` produce overlapping footprints the arbiter refuses.
    The kernel never names these lanes — they are pure config data, proving the
    arbiter arbitrates a foreign domain's lanes unchanged (CLAUDE.md litmus).
    """
    lane_trees = {e.lane: (f"{e.name}/", "shared/") for e in workload.efforts}
    lanes = tuple(e.lane for e in workload.efforts)
    taxonomy = LaneTaxonomy(
        concurrent=lanes,
        autopick=lanes,
        exclusive=(),
        trees=lane_trees,
    )
    # Start from the generic default (gives a full PathLayout.for_root(repo)) and
    # swap in the fleet's lane taxonomy — the kernel never names these lanes;
    # they are pure per-workspace data (CLAUDE.md litmus).
    base = default_config(workspace=repo)
    return dataclasses.replace(base, lanes=taxonomy)


# Constants for the per-step liveness reading: a step that committed advanced
# (commits_since_start=1 → ADVANCING); a step that did not, while the worker is
# alive (fresh heartbeat) and past the grace age, is SPINNING. Reuses the REAL
# `liveness.classify` on bench data — not a re-implementation (docs/86 §3).
_LIVENESS_NOW = liveness.DEFAULT_POLICY.grace_ms + 1   # past grace → old enough to judge
_LIVENESS_HEARTBEAT_FRESH = 0                          # alive


def _step_verdicts(claim, lane: str, cfg: SubstrateConfig) -> tuple[str, str]:
    """The (scope, advancing) verdict pair for a step, from data already present.

    A PURE projection over `claim`/`cfg` — same discipline as the trajectory sink
    (it observes the run, never drives it). `scope.classify` reads the claim's
    ACTUAL footprint (`wrote_files`) against the lane's declared tree
    (`cfg.lanes.trees[lane]`) — catching the cross-lane spill `verify` cannot see.
    `liveness.classify` reads the per-step forward delta (did it commit?).
    """
    scope_v = scope.classify(scope.ScopeEvidence(
        touched_files=frozenset(claim.wrote_files),
        lane_tree=tuple(cfg.lanes.trees.get(lane, ("**/*",))),
        lane=lane,
    ))
    live_v = liveness.classify(liveness.ProgressEvidence(
        run_started_ms=0,
        now_ms=_LIVENESS_NOW,
        commits_since_start=1 if claim.really_committed else 0,
        last_heartbeat_age_ms=_LIVENESS_HEARTBEAT_FRESH,
    ))
    return scope_v.verdict.value, live_v.verdict.value


def run(workload: Workload, model: FailureModel, *, run_seed: int,
        kappa: float = metrics.DEFAULT_KAPPA,
        review_mu: float = metrics.DEFAULT_REVIEW_MU,
        sink: Callable[[TrajectoryStep], None] | None = None,
        bare_pick: bool = False,
        rank_key: Callable[[str, str, list[str]], float | None] | None = None,
        rank_key_factory: Callable[..., Callable[[str, str, list[str]], float | None]] | None = None,
        budget: float | None = None,
        max_steps: int | None = None,
        window_override: int | None = None,
        ) -> tuple[Metrics, list[Event]]:
    """Run the closed-loop arm.

    `sink` (optional) is called once per adjudicated phase with the
    `TrajectoryStep` for that step — the per-step (features ⟂ label ⟂ verdict)
    record (`docs/84`). It is a pure projection of values the arm already
    computes: passing a sink changes NOTHING about scoring or kernel calls, it
    only observes them. The A/B return shape is unchanged so existing callers
    (harness, tests) are unaffected.

    Value-aware picker (docs/91 Phases 2-3), all opt-in and default-off so the
    fixed-lane A/B is byte-identical to today:

    * `bare_pick` — drive the loop from per-effort cursors and let the ARBITER
      pick which ready effort to advance next (via `auto_pick_order` + `rank_key`)
      instead of the fixed `interleave` order. The unit of pick is the EFFORT
      (each keeps its own canonical lane), so the picker chooses ORDER among ready
      efforts and never re-homes a footprint — the soundness floor, held here too.
    * `rank_key` — `(name, kind, tree) -> float | None`, the value estimator the
      arbiter ranks ready candidates by (descending). `None` ⇒ ladder order ⇒
      first-fit (the regression baseline). Only consulted when `bare_pick=True`.
    * `rank_key_factory` — `(workload, cursors) -> rank_key`, an alternative to a
      bare `rank_key` for estimators that must close over the LIVE cursors (the
      reference yield estimator does — it scores by remaining horizon). When given
      it is built once per run against the bare-pick loop's own cursors and wins
      over `rank_key`. (docs/91 Phase 4 — the estimator lives in the benchmark,
      never the kernel.)
    * `budget` / `max_steps` — a hard cost ceiling / step deadline (docs/91 §3).
      With neither set the loop drains the whole workload (today's behavior) and
      `real_ships` is fixed across arms. Under a cap the loop stops early, so
      `real_ships` becomes a dependent variable and a smarter pick ORDER can bank
      more verified work before the cap — the only regime where the picker's
      throughput win is visible. The cap is arm-independent: both arms share it.
    * `window_override` — override the in-flight lease window (default scales with
      the fleet). `window_override=0` means no lease is ever live, so nothing
      contends and bare-pick has nothing to schedule around — the picker-axis
      falsifier where the infra win vanishes (docs/91 §3).
    """
    events: list[Event] = []
    emit = sink if sink is not None else (lambda _s: None)
    workers = {e.name: model.worker(e.name) for e in workload.efforts}

    tmp = Path(tempfile.mkdtemp(prefix="fleet_bench_"))
    repo = tmp / "repo"
    repo.mkdir()

    try:
        _init_repo(repo)
        cfg = _bench_config(repo, workload)
        # Journal in the temp tree so each run is isolated. The path is threaded
        # EXPLICITLY into every `lane_journal.append(..., path=...)` below — NOT via
        # `config.active()` (which this bench never installs) and NOT via an env var.
        # An earlier version set `DISPATCH_JOURNAL_PATH`, but the module reads
        # `DISPATCH_LANE_JOURNAL_PATH`, so the override silently no-op'd and every
        # ACQUIRE landed in the *dogfood* `.dos/lane-journal.jsonl` (17 MB of
        # benchmark garbage before this was caught). `cfg.paths.lane_journal` is
        # already under `repo` (it's rooted at `tmp`), so reuse it.
        bench_journal = cfg.paths.lane_journal
        grep_fb = _git_grep_fallback(repo)

        # the git-derived registry the oracle reads — we add a row ONLY when a real
        # commit lands, so the kernel's "shipped" set == git ground truth, never
        # the worker's claim.
        registry: dict = {"recently_completed": []}

        # live leases the arbiter reasons over; a lease names its lane + footprint.
        live_leases: list[dict] = []
        # per-effort run-id lineage (the correlation spine): a root run, then one
        # child per effort carrying the root's id — so "everything this fleet did"
        # is a WHERE root_id=? join, not a timestamp grep.
        root_rid = run_id.mint("fleet-bench")
        effort_rids = {e.name: run_id.mint(e.name, parent=root_rid,
                                           root_id=root_rid.run_id)
                       for e in workload.efforts}

        # phases an effort must retry because their write was refused (collision).
        # Stores (phase, original_claim) so the retry preserves ground truth.
        deferred: dict[str, list] = {e.name: [] for e in workload.efforts}
        really_shipped: set[tuple[str, str]] = set()

        # Concurrency window: a lease stays live for `window` subsequent steps
        # (the phase is "in flight" — taking wall-clock while OTHER efforts act),
        # then expires. This is what creates real overlap windows: effort-B's
        # shared-touching phase arrives while effort-A's is still in flight. Scaled
        # to the fleet so a wider fleet has more simultaneously-in-flight efforts —
        # the regime where contention bites (the monotonicity-in-fanout claim).
        # `window=0` (an override) means NO lease is ever in flight, so nothing
        # contends — the picker-axis falsifier: with no contention to schedule
        # around, bare-pick and fixed-lane tie (docs/91 §3).
        window = (window_override if window_override is not None
                  else max(1, workload.n_efforts - 1))
        lane_of = {e.name: e.lane for e in workload.efforts}

        def _expire(now_step: int) -> None:
            nonlocal live_leases
            live_leases = [l for l in live_leases if l["_expires_at"] > now_step]

        # Phase-3 budget/deadline cap (docs/91 §3). Both are arm-INDEPENDENT and
        # default-off; with neither set the loop drains, identical to today. The
        # check is at the TOP of each step so the cap is a hard ceiling never
        # overspent. `_actions_spent` counts the SAME costed `action` events the
        # cost model prices (metrics.COST_PER_ACTION each), so "budget" is in the
        # same unit as `total_cost`.
        def _actions_spent() -> int:
            return sum(1 for e in events if e.kind == "action")

        def _cap_hit(next_step: int) -> bool:
            if max_steps is not None and next_step >= max_steps:
                return True
            if budget is not None and (
                    (_actions_spent() + 1) * metrics.COST_PER_ACTION > budget):
                return True
            return False

        def _commit_verify_emit(step: int, phase, claim, lane: str) -> None:
            """Steps 2-3 + the trajectory record for an ADMITTED phase.

            Factored out so the fixed-lane `interleave` path and the bare-pick
            path share one body — the ONLY thing that differs between them is how
            a phase is selected and arbitrated (Step 1). Keeping this identical is
            what makes the default path byte-for-byte unchanged.
            """
            key = (phase.effort, phase.phase_id)
            # ---- STEP 2: do the work; commit FOR REAL only if it really shipped ----
            if claim.really_committed:
                sha = _real_commit(repo, phase, claim.wrote_files)
                events.append(Event("real-ship", phase.effort, phase.phase_id))
                really_shipped.add(key)
                registry["recently_completed"].insert(0, {
                    "plan": phase.effort, "phase": phase.phase_id,
                    "status": "done", "commit_sha": sha,
                })
            if claim.is_rework:
                events.append(Event("rework", phase.effort, phase.phase_id))

            # ---- STEP 3: VERIFY the claim against ground truth (don't believe) ----
            verdict = oracle.is_shipped(
                phase.effort, phase.phase_id,
                state=registry, grep_fallback=grep_fb,
            )
            if verdict.shipped:
                events.append(Event("banked-shipped", phase.effort, phase.phase_id))
            elif claim.claimed_shipped:
                events.append(Event("caught-lie", phase.effort, phase.phase_id))
                events.append(Event("human-review", phase.effort, phase.phase_id))

            # ---- trajectory record for this ADMITTED, adjudicated phase ----
            sc_v, live_v = _step_verdicts(claim, lane, cfg)
            emit(step_from_claim(
                step=step, claim=claim,
                run_id=effort_rids[phase.effort].run_id,
                root_id=root_rid.run_id,
                verdict_shipped=verdict.shipped, verdict_source=verdict.source,
                arbiter_outcome="acquire",
                verdict_in_scope=sc_v, verdict_advancing=live_v,
            ))

        # ── BARE-PICK PATH (docs/91 Phase 2) ───────────────────────────────────
        # Drive the loop from per-effort cursors and let the ARBITER pick which
        # ready effort to advance next (ranked by `rank_key`), instead of the fixed
        # `interleave` order. The unit of pick is the EFFORT, not the lane: each
        # effort keeps its own canonical lane (its private subtree), so every
        # candidate's tree IS the phase's real footprint — the picker only chooses
        # the ORDER among ready efforts, never re-homes a footprint (the soundness
        # floor, held in the harness too). `rank_key=None` ⇒ ladder order ⇒ first-fit.
        if bare_pick:
            cursors = {e.name: 0 for e in workload.efforts}
            # A factory (docs/91 Phase 4) lets a yield estimator close over THESE
            # live cursors so it always scores against the current remaining
            # horizon; it wins over a bare `rank_key` when both are passed.
            active_rank_key = (rank_key_factory(workload, cursors)
                               if rank_key_factory is not None else rank_key)
            step = 0
            while True:
                if _cap_hit(step):
                    break
                # ready candidates: each effort whose horizon isn't exhausted, on
                # its OWN lane, footprinted by its NEXT phase.
                order: list[tuple[str, str, list[str]]] = []
                lookup: dict[str, tuple] = {}
                for e in workload.efforts:
                    idx = cursors[e.name]
                    if idx >= len(e.phases):
                        continue
                    ph = e.phases[idx]
                    ln = lane_of[e.name]
                    order.append((ln, "keyword", list(ph.touches)))
                    lookup[ln] = (e.name, ph)
                if not order:
                    break  # every effort drained

                _expire(step)
                decision = arbiter.arbitrate(
                    requested_lane="", requested_kind="", requested_tree=[],
                    live_leases=live_leases, config=cfg,
                    auto_pick_order=order, rank_key=active_rank_key,
                )

                if decision.outcome != "acquire" or decision.lane not in lookup:
                    # Every ready effort's next phase collides with a live in-flight
                    # lease (same-lane window serialization) — nothing admissible
                    # this tick. Advance the clock so a lease window expires, and
                    # retry. Guard against a no-progress spin: if no lease will ever
                    # expire (none live), stop.
                    if not live_leases:
                        break
                    step += 1
                    continue

                eff, phase = lookup[decision.lane]
                lane = decision.lane
                w = workers[eff]
                key = (eff, phase.phase_id)
                cursors[eff] += 1

                claim = w.attempt(phase, already_shipped=(key in really_shipped))
                events.append(Event("action", eff, phase.phase_id))
                if w.will_thrash():
                    events.append(Event("action", eff, phase.phase_id))
                    events.append(Event("thrash", eff, phase.phase_id))

                candidate_lease = {
                    "lane": lane, "lane_kind": "keyword",
                    "tree": list(phase.touches), "effort": eff,
                    "run_id": effort_rids[eff].run_id,
                    "_expires_at": step + window,
                }
                lane_journal.append(
                    lane_journal.acquire_entry(candidate_lease, reason=decision.reason),
                    path=bench_journal)
                live_leases.append(candidate_lease)
                _commit_verify_emit(step, phase, claim, lane)
                step += 1

        # ── FIXED-LANE PATH (default) ──────────────────────────────────────────
        # The original interleave-driven loop, unchanged in behavior: each phase is
        # arbitrated on its own effort's lane in the seeded interleave order. Guarded
        # by `not bare_pick` so the two paths are mutually exclusive.
        for step, phase in enumerate(interleave(workload, seed=run_seed)):
            if bare_pick:
                break  # the bare-pick walk above already drove the loop
            if _cap_hit(step):
                break
            w = workers[phase.effort]
            key = (phase.effort, phase.phase_id)
            lane = lane_of[phase.effort]

            # expire any in-flight leases whose window has elapsed
            _expire(step)

            claim = w.attempt(phase, already_shipped=(key in really_shipped))

            # every attempt costs an action
            events.append(Event("action", phase.effort, phase.phase_id))
            if w.will_thrash():
                # the closed loop ALSO thrashes — DOS does not make the worker
                # better. But a thrash that tries to re-do an already-verified
                # phase is caught below as rework-refused, capping the waste.
                events.append(Event("action", phase.effort, phase.phase_id))
                events.append(Event("thrash", phase.effort, phase.phase_id))

            # ---- STEP 1: ARBITRATE the write (collision control) ----
            # We arbitrate on the phase's ACTUAL file footprint, not just its lane
            # name. DOS's `cluster` path trusts that a cluster lane owns a disjoint
            # subtree and admits without a footprint check (the by-construction
            # disjointness the job repo bets on); the `keyword` path runs the full
            # admission conjunction (disjointness + self-modify + workspace
            # predicates) against every live lease's footprint. A fleet contending
            # on a SHARED resource that crosses lane boundaries is exactly the
            # keyword case — so we request `keyword` to force the real overlap
            # check. (Private-only phases still admit; they're disjoint.)
            decision = arbiter.arbitrate(
                requested_lane=lane,
                requested_kind="keyword",
                requested_tree=list(phase.touches),
                live_leases=live_leases,
                config=cfg,
            )
            candidate_lease = {
                "lane": decision.lane or lane, "lane_kind": "keyword",
                "tree": list(phase.touches), "effort": phase.effort,
                "run_id": effort_rids[phase.effort].run_id,
                "_expires_at": step + window,   # in-flight until the window elapses
            }
            # WAL the decision (the LJ write-ahead log): ACQUIRE on admit, REFUSE
            # otherwise — so a replay reconstructs who-was-refused-and-why.
            if decision.outcome == "acquire":
                lane_journal.append(lane_journal.acquire_entry(candidate_lease),
                                    path=bench_journal)
            else:
                lane_journal.append(lane_journal.acquire_entry(
                    candidate_lease, reason=f"REFUSED: {decision.reason}"),
                    path=bench_journal)

            if decision.outcome == "refuse":
                # collision: the arbiter refused this footprint. The open loop
                # would have written anyway (silent overwrite); the closed loop
                # DEFERS — no data loss. Count the prevented overwrite. We carry the
                # ALREADY-ROLLED claim so the retry preserves ground truth (a phase
                # that would really ship still ships; a lie stays a lie) — DOS does
                # not turn a lie into a ship, it just reschedules the write.
                events.append(Event("refused-write", phase.effort, phase.phase_id,
                                    detail=decision.reason))
                deferred[phase.effort].append((phase, claim, decision.reason))
                # a refused write that the worker had really intended to commit is
                # rescheduled, not lost; it costs a retry action later. We model
                # the retry as immediately re-attempted on its own lane footprint
                # MINUS the shared file (the real-world "split the change" move).
                continue

            # admitted — take the lease, then run Steps 2-3 + the trajectory record
            # (shared with the bare-pick path so the adjudication body is identical).
            live_leases.append(candidate_lease)
            _commit_verify_emit(step, phase, claim, lane)

        # drain deferred (collision-rescheduled) phases on a split footprint that
        # no longer touches shared/ — they now admit and, if they really shipped,
        # bank. This is the cost of safety: extra actions, but no lost work. By the
        # time we drain, the main loop is done and all in-flight leases have expired.
        live_leases = []
        _drained = False
        for effort, items in deferred.items():
            if _drained:
                break
            for phase, claim, refusal_reason in items:
                # The Phase-3 BUDGET cap binds the drain too, or it would not be a
                # real ceiling: a phase whose retry action would exceed the budget is
                # left stranded (a dependent outcome — a smarter picker strands fewer
                # high-value phases). With no budget this never trips and the drain is
                # exactly today's. (A `max_steps` deadline has no step counter in the
                # drain; it already bounded the main loop, so the drain runs to
                # completion under a pure deadline — the drain is the cost of safety
                # the deadline arm still pays.)
                if budget is not None and (
                        (_actions_spent() + 1) * metrics.COST_PER_ACTION > budget):
                    _drained = True
                    break
                events.append(Event("action", effort, phase.phase_id))  # retry costs
                # Reuse the ORIGINAL claim (no re-roll) — same ground truth as the
                # open loop saw. Footprint is split to private-only, so it admits
                # now. The only difference vs open-loop is WHEN the write lands and
                # that the shared file is written serially, not clobbered.
                key = (effort, phase.phase_id)
                private = tuple(f for f in phase.touches if not f.startswith("shared/"))
                if claim.really_committed:
                    sha = _real_commit(repo, phase, private)
                    events.append(Event("real-ship", effort, phase.phase_id))
                    really_shipped.add(key)
                    registry["recently_completed"].insert(0, {
                        "plan": effort, "phase": phase.phase_id,
                        "status": "done", "commit_sha": sha,
                    })
                verdict = oracle.is_shipped(effort, phase.phase_id,
                                            state=registry, grep_fallback=grep_fb)
                if verdict.shipped:
                    events.append(Event("banked-shipped", effort, phase.phase_id))
                elif claim.claimed_shipped:
                    events.append(Event("caught-lie", effort, phase.phase_id))
                    events.append(Event("human-review", effort, phase.phase_id))

                # ---- trajectory record for this DEFERRED, now-adjudicated phase ----
                # It WAS refused on its shared footprint (recorded), then admitted on
                # the split footprint and verified. arbiter_outcome reflects the
                # refusal that the kernel actually made — the legible negative example.
                sc_v, live_v = _step_verdicts(claim, lane_of[effort], cfg)
                emit(step_from_claim(
                    step=-1, claim=claim,
                    run_id=effort_rids[effort].run_id,
                    root_id=root_rid.run_id,
                    verdict_shipped=verdict.shipped, verdict_source=verdict.source,
                    arbiter_outcome="refuse", refusal_reason=refusal_reason,
                    verdict_in_scope=sc_v, verdict_advancing=live_v,
                ))

        return score("closed-loop", events, total_phases=workload.total_phases,
                     horizon=workload.n_phases_each, kappa=kappa, review_mu=review_mu), events
    finally:
        # best-effort cleanup of the temp repo
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
