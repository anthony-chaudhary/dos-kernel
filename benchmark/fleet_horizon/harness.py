"""The harness — run both arms over a (horizon × fleet) sweep, emit the A/B table.

This is the long-specified-but-never-built instrument: an open-loop-vs-closed-loop
A/B over a long-horizon task. It runs the open-loop and closed-loop arms on the
SAME workload + SAME failure model and prints:

  1. one A/B cell (the four metrics, both arms) for a given (efforts, phases);
  2. `--sweep` — the gap as a function of horizon and fleet size, showing the
     monotonicity claim (gap grows with horizon × fanout) AND its own falsifier
     (the gap → 0 as the fleet/horizon → 1, where DOS is pure overhead).
  3. `--host-sweep` — the HOST axis: an advisory host (RECORDs a collision after
     the fact) vs an enforcement host (PREVENTs it at contention). Emits the
     catch-vs-prevent curve (a per-host RECORD/PREVENT split) plus its falsifier
     (the gap → 0 on a disjoint workload, where nothing contends).

Honesty (`README.md` §honesty): identical seed → identical agent behavior in
both arms; the only difference is believe-vs-adjudicate. The gap is what the
open loop's own output contained and the closed loop revealed.

Run:
    PYTHONPATH=src python -m benchmark.fleet_horizon.harness --efforts 6 --phases 20
    PYTHONPATH=src python -m benchmark.fleet_horizon.harness --sweep
"""
from __future__ import annotations

import argparse
import json

from .agent import FailureModel
from .metrics import Metrics
from .workload import generate, generate_disjoint
from . import open_loop, closed_loop, harness_loop, metrics


def run_cell(
    *, efforts: int, phases: int, seed: int = 1729,
    shared_ratio: float = 0.25, lie_rate: float = 0.12,
    kappa: float = metrics.DEFAULT_KAPPA, review_mu: float = metrics.DEFAULT_REVIEW_MU,
) -> tuple[Metrics, Metrics]:
    """Run both arms on one (efforts, phases) workload; return (open, closed)."""
    workload = generate(seed=seed, efforts=efforts, phases=phases,
                        shared_ratio=shared_ratio)
    model = FailureModel(seed=seed, lie_rate=lie_rate)
    open_m, _ = open_loop.run(workload, model, run_seed=seed, kappa=kappa, review_mu=review_mu)
    closed_m, _ = closed_loop.run(workload, model, run_seed=seed, kappa=kappa, review_mu=review_mu)
    return open_m, closed_m


def run_quad(
    *, efforts: int, phases: int, seed: int = 1729,
    shared_ratio: float = 0.3, lie_rate: float = 0.12,
    kappa: float = metrics.DEFAULT_KAPPA, review_mu: float = metrics.DEFAULT_REVIEW_MU,
) -> dict[str, Metrics]:
    """The ORCHESTRATOR × TRUST cells (docs/98) over one workload.

    Returns the four cells of the 2×2 — but the believe column collapses to a
    single control: the orchestrator only has a seam to differ at when SOMEONE
    calls `arbitrate`, and the believe arm never does, so DOS-dispatch×believe and
    harness-flow×believe produce the same ledger (the `open_loop` arm is the shared
    baseline). The real experiment is the adjudicate column: `dos_adjudicate`
    (in-process leases) vs `harness_adjudicate_wb` (cross-process, disciplined
    write-back) vs `harness_adjudicate_nowb` (cross-process, naive — the gap).

    Honesty: ALL cells run the SAME seeded workload + failure model, so `real_ships`
    is identical and any delta is purely the orchestrator's / the trust column's.
    """
    workload = generate(seed=seed, efforts=efforts, phases=phases,
                        shared_ratio=shared_ratio)
    model = FailureModel(seed=seed, lie_rate=lie_rate)
    believe, _ = open_loop.run(workload, model, run_seed=seed, kappa=kappa, review_mu=review_mu)
    dos_adj, _ = closed_loop.run(workload, model, run_seed=seed, kappa=kappa, review_mu=review_mu)
    h_wb, _ = harness_loop.run(workload, model, run_seed=seed, kappa=kappa,
                               review_mu=review_mu, lease_writeback=True)
    h_nowb, _ = harness_loop.run(workload, model, run_seed=seed, kappa=kappa,
                                 review_mu=review_mu, lease_writeback=False)
    return {
        "believe": believe,               # the shared baseline (A≡C)
        "dos_adjudicate": dos_adj,         # cell B — DOS-native dispatch
        "harness_adjudicate_wb": h_wb,     # cell D, disciplined (write-back)
        "harness_adjudicate_nowb": h_nowb, # cell D, naive (the orchestrator gap)
    }


def run_host_pair(
    *, efforts: int, phases: int, seed: int = 1729,
    shared_ratio: float = 0.3, lie_rate: float = 0.12,
    kappa: float = metrics.DEFAULT_KAPPA, review_mu: float = metrics.DEFAULT_REVIEW_MU,
) -> dict[str, Metrics]:
    """The HOST axis (advisory vs enforcement) over one workload.

    The orchestrator axis (`run_quad`) varies HOW the fleet shares leases; the host
    axis varies what the host DOES with the arbiter's verdict on a collision:

      * an **enforcement** host treats a refusal as BINDING — the colliding write is
        PREVENTED at contention (deferred, drained later on a split footprint). No
        data is lost; the collision never lands. This is the in-process DOS-native
        loop (`closed_loop.run`): its `refused_writes` are prevented-at-contention.
      * an **advisory** host runs the SAME arbiter but the refusal is only ADVISORY —
        its lease visibility lags (separate units, no write-back), so two siblings
        both ADMIT a colliding tree and the collision is merely RECORDED after both
        writes land (a `detected_collision`, plus a surviving `silent_overwrite`
        verify cannot undo). This is `harness_loop.run(lease_writeback=False)`.

    So the host axis reduces to the catch-vs-prevent split: enforcement PREVENTS
    (refused_writes, recorded_after=0); advisory only RECORDS (detected_collisions,
    prevented=0). Both run the SAME seeded workload + failure model + real git repo,
    so `real_ships` is identical and any delta is purely the host's enforcement
    posture — the honesty invariant, lifted to this axis.
    """
    workload = generate(seed=seed, efforts=efforts, phases=phases,
                        shared_ratio=shared_ratio)
    model = FailureModel(seed=seed, lie_rate=lie_rate)
    enforcement, _ = closed_loop.run(workload, model, run_seed=seed, kappa=kappa,
                                     review_mu=review_mu)
    advisory, _ = harness_loop.run(workload, model, run_seed=seed, kappa=kappa,
                                   review_mu=review_mu, lease_writeback=False)
    return {"enforcement": enforcement, "advisory": advisory}


def _host_split(m: Metrics) -> dict:
    """The RECORD/PREVENT split for one host — the host-axis discriminator.

    `prevented` = collisions stopped at contention (the arbiter's refusal was
    binding); `recorded_after` = collisions only detected after both writes landed
    (the refusal was advisory and its lease lagged); `silent_overwrites` = the
    subset of recorded-after that clobbered a real concurrent write and survive (the
    irreducible cost of RECORD-without-PREVENT). `posture` is RECORD vs PREVENT: a
    host that prevents anything is an enforcement host; one that only records is
    advisory. `collisions_total` is a workload property (same in both hosts on one
    seed); only the split differs."""
    posture = "PREVENT" if m.refused_writes > 0 else (
        "RECORD" if m.detected_collisions > 0 else "NONE")
    return {
        "arm": m.arm,
        "collisions_total": m.collisions_total,
        "prevented": m.refused_writes,
        "recorded_after": m.detected_collisions,
        "silent_overwrites": m.silent_overwrites,
        "prevention_rate": round(m.prevention_rate, 4),
        "posture": posture,
        "real_ships": m.real_ships,
    }


def break_even_kappa(*, efforts: int, phases: int, seed: int = 1729,
                     shared_ratio: float = 0.3, lo: float = 0.0, hi: float = 50.0,
                     tol: float = 0.05) -> float | None:
    """The κ at which closed-loop verified-velocity/$ overtakes open-loop's.

    Bisection on κ. Below break-even the open loop is cheaper (DOS is overhead);
    above it DOS pays for itself on conflict cost alone. Reported as the headline
    (docs/81 §2.2) — compared to the published merge-cost literature, never picked.
    Returns None if the arms never cross in [lo, hi] (e.g. fleet=1, no conflicts).
    """
    def closed_ahead(k: float) -> bool:
        o, c = run_cell(efforts=efforts, phases=phases, seed=seed,
                        shared_ratio=shared_ratio, kappa=k)
        return c.verified_velocity_per_dollar >= o.verified_velocity_per_dollar
    if closed_ahead(lo):
        return lo            # already ahead at κ=0 (review-fraction alone wins)
    if not closed_ahead(hi):
        return None          # never crosses in range
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if closed_ahead(mid):
            hi = mid
        else:
            lo = mid
    return round(hi, 2)


def _fmt_ab(open_m: Metrics, closed_m: Metrics) -> str:
    o, c = open_m.to_row(), closed_m.to_row()
    cols = [
        ("metric", "open-loop", "closed-loop"),
        ("phases", o["phases"], c["phases"]),
        ("banked as shipped", o["banked_shipped"], c["banked_shipped"]),
        ("  └ of those, LIES banked", o["banked_lies"], c["banked_lies"]),
        ("lies CAUGHT (refused)", o["caught_lies"], c["caught_lies"]),
        ("real ships (ground truth)", o["real_ships"], c["real_ships"]),
        ("silent overwrites", o["silent_overwrites"], c["silent_overwrites"]),
        ("collisions refused", o["refused_writes"], c["refused_writes"]),
        ("rework", o["rework"], c["rework"]),
        ("thrash", o["thrash"], c["thrash"]),
        ("raw spend (actions)", o["cost"], c["cost"]),
        ("downstream defect debt", o["defect_debt"], c["defect_debt"]),
        ("TRUE cost (spend+debt)", o["true_cost"], c["true_cost"]),
        ("LIE RATE (banked)", f"{o['lie_rate']:.1%}", f"{c['lie_rate']:.1%}"),
        ("banked INTEGRITY", f"{o['banked_integrity']:.1%}", f"{c['banked_integrity']:.1%}"),
        ("verified/$ (raw)", o["verified_per_$"], c["verified_per_$"]),
        ("verified/$ (defect-adj)", o["defect_adj_verified_per_$"], c["defect_adj_verified_per_$"]),
        ("── VELOCITY (docs/81) ──", "", ""),
        ("human reviews queued", o["human_reviews"], c["human_reviews"]),
        ("human-review FRACTION", f"{o['human_review_fraction']:.1%}", f"{c['human_review_fraction']:.1%}"),
        ("conflict detonations", o["conflict_detonations"], c["conflict_detonations"]),
        (f"loaded cost (κ={open_m.kappa:g})", o["loaded_cost"], c["loaded_cost"]),
        ("review-queue wait", o["review_wait"], c["review_wait"]),
        ("VERIFIED-VELOCITY / $", o["verified_velocity_per_$"], c["verified_velocity_per_$"]),
    ]
    w0 = max(len(str(r[0])) for r in cols)
    w1 = max(len(str(r[1])) for r in cols)
    w2 = max(len(str(r[2])) for r in cols)
    lines = []
    for i, (a, b, d) in enumerate(cols):
        lines.append(f"  {str(a):<{w0}}  {str(b):>{w1}}  {str(d):>{w2}}")
        if i == 0:
            lines.append(f"  {'-'*w0}  {'-'*w1}  {'-'*w2}")
    return "\n".join(lines)


def _sweep() -> None:
    """The monotonicity demonstration: gap vs horizon and vs fleet size."""
    print("=" * 78)
    print("FleetHorizon — the adjudication gap as a function of horizon × fanout")
    print("=" * 78)
    print("\nThe claim: DOS's value (lies caught, overwrites prevented, integrity")
    print("preserved) is monotonically increasing in horizon × fleet size — and")
    print("→ 0 when the fleet/horizon → 1 (where a non-believing kernel is pure")
    print("overhead). Both columns prove themselves below.\n")

    def _gap(o: Metrics, c: Metrics) -> str:
        """closed÷open on defect-adjusted verified/$ — >1 means DOS is ahead."""
        ov = o.defect_adjusted_verified_per_dollar
        cv = c.defect_adjusted_verified_per_dollar
        if ov == 0:
            return "  —"
        return f"{cv/ov:.2f}x"

    # --- sweep horizon at a fixed fleet ---
    print("\n[A] FIXED fleet = 6 efforts; horizon (phases/effort) grows:")
    print("    The lie RATE is ~flat (per-phase) — what compounds is the COUNT of")
    print("    caught defects and the defect DEBT (= count × remaining horizon).")
    print(f"    {'horizon':>8} {'lies caught':>12} {'overwrites prev':>16} "
          f"{'open debt':>10} {'DOS verified/$ edge':>20}")
    for phases in (1, 3, 8, 20, 40):
        o, c = run_cell(efforts=6, phases=phases)
        print(f"    {phases:>8} {c.caught_lies:>12} {c.refused_writes:>16} "
              f"{o.defect_debt:>10.0f} {_gap(o, c):>20}")

    # --- sweep fleet at a fixed horizon ---
    print("\n[B] FIXED horizon = 20 phases; fleet (concurrent efforts) grows:")
    print("    At fleet=1 there is no one to collide with — overwrites-prevented is")
    print("    0 and DOS's collision value is nil (its own falsifier). It climbs")
    print("    with the fleet — collision-prevention is strictly a fleet phenomenon.")
    print(f"    {'fleet':>8} {'lies caught':>12} {'overwrites prev':>16} "
          f"{'open debt':>10} {'DOS verified/$ edge':>20}")
    for efforts in (1, 2, 4, 8, 12):
        o, c = run_cell(efforts=efforts, phases=20)
        print(f"    {efforts:>8} {c.caught_lies:>12} {c.refused_writes:>16} "
              f"{o.defect_debt:>10.0f} {_gap(o, c):>20}")

    # --- the headline single cell at a realistic fleet ---
    print("\n[C] Headline cell — a realistic long-horizon fleet (8 efforts × 30 phases):")
    o, c = run_cell(efforts=8, phases=30)
    print()
    print(_fmt_ab(o, c))
    print()
    # the one-line takeaways
    overwrites = c.refused_writes
    print(f"  → The open loop banked {o.banked_lies} false 'shipped' claims "
          f"({o.lie_rate:.1%} of its ledger) it could not detect.")
    print(f"  → The closed loop caught all {c.caught_lies} and prevented "
          f"{overwrites} silent overwrites on shared state — same agents, same seed.")
    print(f"  → It cost {c.total_cost - o.total_cost:+.0f} extra actions "
          f"({(c.total_cost/o.total_cost - 1):+.1%}) to keep the fleet honest.")


def _velocity_sweep() -> None:
    """The velocity axis (docs/81): human-review fraction + break-even κ.

    Two velocity headlines, both honest:
      * human-review FRACTION — model-free (no assumed μ). The Faros-paradox lever.
      * break-even κ — the conflict multiplier at which DOS pays for itself on merge
        cost alone, to compare against the published literature (Ghiotto 10-20%,
        AgenticFlict 27.67%, super-linear-in-divergence).
    """
    print("=" * 78)
    print("FleetHorizon — the VELOCITY axis (docs/81): review-queue + conflict cost")
    print("=" * 78)
    print("\n'Catching the lie is integrity. Not paying for it downstream is velocity.'")
    print("The open loop must human-review EVERYTHING (nothing adjudicated")
    print("completeness); the closed loop routes only kernel-surfaced exceptions to")
    print("a human — the lever against the Faros productivity paradox (+98% PRs")
    print("merged, +91% review time, flat throughput).\n")

    print("[V-A] human-review FRACTION (model-free) as the fleet grows (horizon=20):")
    print(f"    {'fleet':>8} {'open review frac':>18} {'closed review frac':>20} "
          f"{'human-queue shrink':>20}")
    for efforts in (1, 2, 4, 8, 12):
        o, c = run_cell(efforts=efforts, phases=20)
        shrink = (1 - c.human_review_fraction / o.human_review_fraction) if o.human_review_fraction else 0.0
        print(f"    {efforts:>8} {o.human_review_fraction:>17.1%} "
              f"{c.human_review_fraction:>19.1%} {shrink:>19.1%}")

    print("\n[V-B] BREAK-EVEN κ (conflict multiplier where DOS overtakes), horizon=20:")
    print("    Below it DOS is overhead; above it DOS pays for itself on merge cost")
    print("    alone. Literature puts real κ (hand-merge ÷ normal action) well into")
    print("    double digits — far above these break-evens.")
    print(f"    {'fleet':>8} {'break-even κ':>14}  interpretation")
    for efforts in (2, 4, 8, 12):
        be = break_even_kappa(efforts=efforts, phases=20)
        if be is None:
            be_s, note = "—", "arms never cross in [0,50] (no contention to price)"
        elif be <= 0.0:
            be_s, note = "0.00", "DOS ahead even at κ=0 (review-fraction alone wins)"
        else:
            be_s, note = f"{be:g}", f"DOS wins once a hand-merge costs ≥{be:g} actions"
        print(f"    {efforts:>8} {be_s:>14}  {note}")

    print("\n[V-C] Velocity headline cell (8 efforts × 30 phases, κ=5, μ=0.33):")
    o, c = run_cell(efforts=8, phases=30)
    print()
    print(_fmt_ab(o, c))
    print()
    print(f"  → Open loop sent {o.human_reviews} 'done's to a human "
          f"({o.human_review_fraction:.0%} of output); closed loop sent "
          f"{c.human_reviews} ({c.human_review_fraction:.0%}) — only the exceptions.")
    if o.verified_velocity_per_dollar:
        print(f"  → Verified-velocity/$ (fully loaded, κ=5): open "
              f"{o.verified_velocity_per_dollar:.3f} vs closed "
              f"{c.verified_velocity_per_dollar:.3f} "
              f"({c.verified_velocity_per_dollar/o.verified_velocity_per_dollar:.2f}×).")


def _orchestrator_sweep() -> None:
    """The ORCHESTRATOR axis (docs/98): is the harness/ultracode flow as safe as
    DOS-native dispatch? Holds TRUST fixed at adjudicate and varies the orchestrator.

    The headline is the prevented-vs-detected split: a DOS-native loop (in-process
    leases) and a disciplined harness (writes leases back via `dos lease-lane`) both
    PREVENT every collision at contention; a naive harness (no write-back) lets the
    same collisions slip past the arbiter to be DETECTED after the fact — some
    surviving as silent overwrites verify cannot undo. The falsifier: on a genuinely
    disjoint workload the gap vanishes (orchestrator is moot when nothing contends).
    """
    print("=" * 78)
    print("FleetHorizon — the ORCHESTRATOR axis (docs/98): is ultracode as safe as")
    print("DOS-native dispatch? Trust fixed at ADJUDICATE; the orchestrator varies.")
    print("=" * 78)
    print("\n'The harness owns the fanout; DOS owns the lock. The question is whether")
    print("the fanout feeds the lock in time.' DOS-native threads leases in-process;")
    print("a harness flow shares them only through the durable WAL (dos lease-lane) —")
    print("safe IFF it writes the grant back BEFORE a sibling arbitrates.\n")

    print("[O-A] Collisions PREVENTED-at-contention vs DETECTED-after, as the fleet")
    print("      grows (horizon=20, shared_ratio=0.3). DOS-native & disciplined-")
    print("      harness prevent all; the naive harness detects-after (and worse).")
    print(f"    {'fleet':>6} | {'DOS-native':>22} | {'harness +writeback':>22} | "
          f"{'harness NO writeback':>26}")
    print(f"    {'':>6} | {'prev/det/silent':>22} | {'prev/det/silent':>22} | "
          f"{'prev/det/silent':>26}")
    print("    " + "-" * 86)
    for efforts in (1, 2, 4, 8, 12):
        q = run_quad(efforts=efforts, phases=20)
        def cell(m: Metrics) -> str:
            return f"{m.refused_writes}/{m.detected_collisions}/{m.silent_overwrites}"
        print(f"    {efforts:>6} | {cell(q['dos_adjudicate']):>22} | "
              f"{cell(q['harness_adjudicate_wb']):>22} | "
              f"{cell(q['harness_adjudicate_nowb']):>26}")

    print("\n[O-B] The orchestrator GAP — collisions the naive harness let through")
    print("      (detected-after + surviving silent overwrites) that DOS-native")
    print("      prevented. This is what leaning on ultracode WITHOUT the lease")
    print("      write-back discipline costs. Grows with fleet × contention.")
    print(f"    {'fleet':>6} {'naive detected':>16} {'naive silent':>14} "
          f"{'naive prevention':>18} {'DOS prevention':>16}")
    for efforts in (2, 4, 8, 12):
        q = run_quad(efforts=efforts, phases=20)
        nv = q["harness_adjudicate_nowb"]
        dn = q["dos_adjudicate"]
        print(f"    {efforts:>6} {nv.detected_collisions:>16} {nv.silent_overwrites:>14} "
              f"{nv.prevention_rate:>17.0%} {dn.prevention_rate:>15.0%}")

    print("\n[O-C] The FALSIFIER — a genuinely disjoint workload (no shared footprints).")
    print("      The arbiter never refuses, lease visibility is irrelevant, and EVERY")
    print("      orchestrator ties: the gap → 0 where nothing contends. The benchmark")
    print("      proves its own boundary (the docs/98 analogue of gap→0 at horizon→1).")
    seed = 1729
    model = FailureModel(seed=seed, lie_rate=0.12)
    wl = generate_disjoint(seed=seed, efforts=6, phases=20)
    dn, _ = closed_loop.run(wl, model, run_seed=seed)
    nv, _ = harness_loop.run(wl, model, run_seed=seed, lease_writeback=False)
    wb, _ = harness_loop.run(wl, model, run_seed=seed, lease_writeback=True)
    print(f"    DOS-native:        detected={dn.detected_collisions} silent={dn.silent_overwrites} real_ships={dn.real_ships}")
    print(f"    harness +wb:       detected={wb.detected_collisions} silent={wb.silent_overwrites} real_ships={wb.real_ships}")
    print(f"    harness no-wb:     detected={nv.detected_collisions} silent={nv.silent_overwrites} real_ships={nv.real_ships}")
    tie = (dn.detected_collisions == nv.detected_collisions == wb.detected_collisions == 0
           and dn.silent_overwrites == nv.silent_overwrites == wb.silent_overwrites == 0
           and dn.real_ships == nv.real_ships == wb.real_ships)
    print(f"    → boundary holds (orchestrator moot when disjoint): {tie}")

    print("\n[O-D] Orchestrator headline cell (8 efforts × 30 phases, shared_ratio=0.3):")
    q = run_quad(efforts=8, phases=30)
    dn, wb, nv = q["dos_adjudicate"], q["harness_adjudicate_wb"], q["harness_adjudicate_nowb"]
    print(f"    same real ships across all three (honesty): "
          f"{dn.real_ships}/{wb.real_ships}/{nv.real_ships}  "
          f"(equal={dn.real_ships == wb.real_ships == nv.real_ships})")
    print(f"    same banked_lies=0 (verify catches lies regardless of orchestrator): "
          f"{dn.banked_lies}/{wb.banked_lies}/{nv.banked_lies}")
    print(f"    DOS-native       — prevention {dn.prevention_rate:.0%}, "
          f"detected {dn.detected_collisions}, surviving silent {dn.silent_overwrites}")
    print(f"    harness +wb      — prevention {wb.prevention_rate:.0%}, "
          f"detected {wb.detected_collisions}, surviving silent {wb.silent_overwrites}")
    print(f"    harness no-wb    — prevention {nv.prevention_rate:.0%}, "
          f"detected {nv.detected_collisions}, surviving silent {nv.silent_overwrites}")
    print("\n  → Leaning on ultracode for the fanout is SAFE iff the flow writes its")
    print("    leases back (dos lease-lane). Without it, the same fleet's collisions")
    print("    regress from PREVENTED to DETECTED-after — invisible in a demo, paid")
    print("    downstream as the hand-merge the silent overwrite became.")


def _host_sweep_data(fleets: tuple[int, ...] = (1, 2, 4, 8, 12),
                     phases: int = 20, shared_ratio: float = 0.3) -> dict:
    """The host-axis data the sweep renders — the catch-vs-prevent curve as JSON.

    For each fleet size: the per-host RECORD/PREVENT split (`_host_split`) for the
    enforcement and advisory hosts. Plus the FALSIFIER cell on a genuinely disjoint
    workload, where neither host has anything to prevent OR record (the gap → 0
    where nothing contends — the host-axis analogue of gap→0 at horizon→1). A pure
    data function so the text renderer and `--json` share one computation."""
    curve = []
    for efforts in fleets:
        hosts = run_host_pair(efforts=efforts, phases=phases, shared_ratio=shared_ratio)
        curve.append({
            "fleet": efforts,
            "enforcement": _host_split(hosts["enforcement"]),
            "advisory": _host_split(hosts["advisory"]),
        })

    # the falsifier: a truly disjoint workload — no shared footprints to contend on.
    seed = 1729
    model = FailureModel(seed=seed, lie_rate=0.12)
    wl = generate_disjoint(seed=seed, efforts=6, phases=phases)
    enf, _ = closed_loop.run(wl, model, run_seed=seed)
    adv, _ = harness_loop.run(wl, model, run_seed=seed, lease_writeback=False)
    enf_s, adv_s = _host_split(enf), _host_split(adv)
    # The boundary predicate is about SHARED-STATE contention, not lane serialization.
    # On the disjoint workload no two phases share a file, so NOTHING is RECORDED-after
    # and NO silent overwrite survives in either host — and both ship the same ground
    # truth. The enforcement host may still log same-LANE serialization refusals
    # (effort-k's phase arrives while its own prior phase's lease window is open — a
    # LANE_HELD defer, not a cross-effort data collision); those are an artifact of the
    # in-flight lease window, not contention on shared state, so they do NOT break the
    # boundary. The honest claim — identical to the orchestrator falsifier — is that
    # when footprints are truly disjoint the host choice is moot: neither host detects
    # a collision after the fact, neither suffers a surviving overwrite, both ship the
    # same real work.
    falsifier = {
        "enforcement": enf_s,
        "advisory": adv_s,
        "boundary_holds": (
            enf_s["recorded_after"] == enf_s["silent_overwrites"] == 0
            and adv_s["recorded_after"] == adv_s["silent_overwrites"] == 0
            and enf_s["real_ships"] == adv_s["real_ships"]),
    }
    return {
        "axis": "host",
        "phases": phases,
        "shared_ratio": shared_ratio,
        "curve": curve,
        "falsifier": falsifier,
    }


def _host_sweep() -> None:
    """The HOST axis (advisory vs enforcement): does a RECORD-only host catch what
    an enforcement host PREVENTS? Holds trust + orchestrator intent fixed and varies
    the host's enforcement posture.

    The headline is the catch-vs-prevent curve: an enforcement host PREVENTS every
    collision at contention (refused_writes); an advisory host runs the same arbiter
    but only RECORDS the collision after the fact (detected_collisions + surviving
    silent_overwrites). The falsifier: on a genuinely disjoint workload both hosts
    have nothing to prevent OR record — the gap vanishes where nothing contends.
    """
    data = _host_sweep_data()
    print("=" * 78)
    print("FleetHorizon — the HOST axis: advisory (RECORD) vs enforcement (PREVENT)")
    print("=" * 78)
    print("\n'The arbiter renders the same verdict in both hosts. The host decides")
    print("whether that verdict BINDS.' An enforcement host treats a refusal as")
    print("binding — the colliding write is PREVENTED at contention. An advisory host")
    print("lets the write proceed and only RECORDS the collision after it lands —")
    print("catching it too late to undo the silent overwrite it became.\n")

    print(f"[H-A] The catch-vs-prevent CURVE as the fleet grows "
          f"(horizon={data['phases']}, shared_ratio={data['shared_ratio']}):")
    print("      Enforcement PREVENTS; advisory only RECORDS-after. The split is the")
    print("      whole host axis — same collisions, caught at a different time.")
    print(f"    {'fleet':>6} | {'enforcement (PREVENT)':>26} | {'advisory (RECORD)':>26}")
    print(f"    {'':>6} | {'prevented/rec/silent':>26} | {'prevented/rec/silent':>26}")
    print("    " + "-" * 64)
    for row in data["curve"]:
        e, a = row["enforcement"], row["advisory"]
        def cell(s: dict) -> str:
            return f"{s['prevented']}/{s['recorded_after']}/{s['silent_overwrites']}  [{s['posture']}]"
        print(f"    {row['fleet']:>6} | {cell(e):>26} | {cell(a):>26}")

    print("\n[H-B] The host GAP — collisions the advisory host only RECORDED (and the")
    print("      silent overwrites that survived) where the enforcement host PREVENTED")
    print("      them. This is what an advisory-only posture costs, paid downstream as")
    print("      the hand-merge each surviving overwrite became.")
    print(f"    {'fleet':>6} {'enf prevented':>14} {'adv recorded':>14} "
          f"{'adv silent':>12} {'enf prevention':>16} {'adv prevention':>16}")
    for row in data["curve"]:
        e, a = row["enforcement"], row["advisory"]
        print(f"    {row['fleet']:>6} {e['prevented']:>14} {a['recorded_after']:>14} "
              f"{a['silent_overwrites']:>12} {e['prevention_rate']:>15.0%} "
              f"{a['prevention_rate']:>15.0%}")

    f = data["falsifier"]
    print("\n[H-C] The FALSIFIER — a genuinely disjoint workload (no shared footprints).")
    print("      No collision is RECORDED-after and no silent overwrite survives in")
    print("      either host; both ship the same ground truth — the gap → 0 where")
    print("      nothing CONTENDS on shared state (the host-axis analogue of gap→0 at")
    print("      horizon→1). The enforcement host may still log same-LANE serialization")
    print("      defers (a phase arriving inside its OWN prior lease window) — those are")
    print("      a lease-window artifact, not contention, and do not break the boundary.")
    print(f"    enforcement:  prevented={f['enforcement']['prevented']} "
          f"recorded={f['enforcement']['recorded_after']} "
          f"silent={f['enforcement']['silent_overwrites']} "
          f"real_ships={f['enforcement']['real_ships']}")
    print(f"    advisory:     prevented={f['advisory']['prevented']} "
          f"recorded={f['advisory']['recorded_after']} "
          f"silent={f['advisory']['silent_overwrites']} "
          f"real_ships={f['advisory']['real_ships']}")
    print(f"    → boundary holds (host moot when disjoint): {f['boundary_holds']}")
    print("\n  → An enforcement host PREVENTS the collision; an advisory host only")
    print("    RECORDS it — same arbiter, same verdict, but RECORD lands too late to")
    print("    undo the overwrite. Catching ≠ preventing once the write has landed.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FleetHorizon open-loop vs closed-loop A/B")
    ap.add_argument("--efforts", type=int, default=6, help="fleet size (concurrent efforts)")
    ap.add_argument("--phases", type=int, default=20, help="horizon (phases per effort)")
    ap.add_argument("--seed", type=int, default=1729)
    ap.add_argument("--shared-ratio", type=float, default=0.25,
                    help="fraction of phases reaching into shared state (collision surface)")
    ap.add_argument("--lie-rate", type=float, default=0.12,
                    help="P(worker claims shipped without a real commit)")
    ap.add_argument("--kappa", type=float, default=metrics.DEFAULT_KAPPA,
                    help="conflict-detonation multiplier (hand-merge ÷ normal action)")
    ap.add_argument("--review-mu", type=float, default=metrics.DEFAULT_REVIEW_MU,
                    help="human review-queue service rate (M/M/1 wait estimate)")
    ap.add_argument("--sweep", action="store_true", help="run the horizon×fleet integrity sweep")
    ap.add_argument("--velocity-sweep", action="store_true",
                    help="run the velocity sweep (review-fraction + break-even κ)")
    ap.add_argument("--orchestrator-sweep", action="store_true",
                    help="run the orchestrator sweep (DOS-dispatch vs ultracode/harness; docs/98)")
    ap.add_argument("--host-sweep", action="store_true",
                    help="run the host sweep (advisory/RECORD vs enforcement/PREVENT; the catch-vs-prevent curve)")
    ap.add_argument("--json", action="store_true", help="emit the cell as JSON")
    args = ap.parse_args(argv)

    if args.sweep:
        _sweep()
        return 0
    if args.velocity_sweep:
        _velocity_sweep()
        return 0
    if args.orchestrator_sweep:
        _orchestrator_sweep()
        return 0
    if args.host_sweep:
        if args.json:
            print(json.dumps(_host_sweep_data(), indent=2))
        else:
            _host_sweep()
        return 0

    o, c = run_cell(efforts=args.efforts, phases=args.phases, seed=args.seed,
                    shared_ratio=args.shared_ratio, lie_rate=args.lie_rate,
                    kappa=args.kappa, review_mu=args.review_mu)
    if args.json:
        print(json.dumps({"open_loop": o.to_row(), "closed_loop": c.to_row()}, indent=2))
        return 0
    print(f"\nFleetHorizon A/B — fleet={args.efforts} efforts × horizon={args.phases} "
          f"phases (seed={args.seed}, lie_rate={args.lie_rate}, "
          f"shared_ratio={args.shared_ratio})\n")
    print(_fmt_ab(o, c))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
