"""`dos observe --loops` — the self-improving-loop TRAJECTORY projection (docs/383).

`dos observe` folds the verdict journal into a *census* — "47 improve verdicts: 30
KEEP, 17 REVERT." That is the wrong shape for the one thing an operator most needs to
watch: a long-running recursive-self-improvement loop (`dos-self-improve`, docs/280;
`dos-enforce-tune`, docs/365) iterating autonomously for hours. For that, the question
is not "how many KEEPs in total" but **"where is THIS loop on its curve right now"** —
which iteration, is the metric still climbing, how close is the breaker to handing back
to a human, and is the loop even still alive.

This module is that projection: it folds the verdict journal's `improve`-syscall
events, **grouped by `run_id`**, into one `LoopTrajectory` per loop — the same way
`dispatch_top` folds the lease WAL into a live lane screen, restated for the loop axis.

Why this is honest observability (the docs/138 invariant, restated for the loop axis).
A self-improving loop's history is *already a non-forgeable witness stream*: every
KEEP/REVERT/ESCALATE is a kernel verdict over env-authored facts (the suite exit, the
truth syscall, the measured metric — docs/280). We do not ask the loop how it is doing;
we **fold the verdicts it already emitted**. The candidate's `narrated` self-assessment
rides along as *context only* (so an operator can read why a reverted candidate was
proposed) and enters **no band and no count** — exactly the channel `improve.classify`
parses for nothing.

It is a **read-only projection** (the `observe` / `dispatch_top` row-3 discipline): it
reads the verdict journal only, takes no lease, mutates nothing, mints no belief. The
fold (`trajectories_from_events`) is pure over already-read events + an injected `now`
— no disk, no clock read inside — so the suite folds it without a file; the single
`verdict_journal.read_all` is the CLI boundary's I/O. Delete this module and you lose
the screen, not any verdict.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from typing import Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover
        pass
elif not isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dos import verdict_journal as _vj

# The syscall dimension both RSI loops record under. `dos-self-improve` and
# `dos-enforce-tune` BOTH ride `improve.classify`, so both land here under
# `syscall == "improve"` (the `_ENFORCE_TUNE_EXITS` map sets `syscall="improve"`
# deliberately, docs/365). One fold therefore covers "RSI and similar" — the two
# loops are separated only by their distinct `run_id`s, never by the syscall.
SYSCALL_IMPROVE = "improve"

# The default ESCALATE threshold — `improve.ImprovePolicy.max_consecutive_reverts`'s
# default. Kept as a literal here (not an import of the policy) so this projection
# stays a leaf with no dependency on the keep-gate's internals; the CLI passes the
# workspace's actual `[improve] max_consecutive_reverts` so the distance-to-escalate
# is honest to the policy the loop ran under. 3 is the kernel default (docs/280).
DEFAULT_MAX_REVERTS = 3

# How long a loop may go without a fresh iteration before it reads STALLED rather
# than ACTIVE. An RSI cycle is EXPENSIVE — a candidate proposal plus the FULL suite
# on an isolated worktree plus the metric re-measure — so a single cycle routinely
# runs many minutes; a too-tight threshold would flag a healthy mid-cycle loop as
# stalled. 30 minutes is generous for any real cycle while still catching a loop that
# has genuinely wedged (no verdict in half an hour). Injected/overridable, never read
# from a clock inside the fold (the arbiter discipline).
STALL_AFTER_MS = 30 * 60 * 1000

# The three at-a-glance bands a loop reads as. ACTIVE = a fresh iteration landed
# recently; STALLED = no verdict within the stall window (wedged or finished without
# a clean stop record); ESCALATED = the breaker tripped and handed the "what matters
# next" judgment back to a human (docs/223) — terminal and ATTENTION-WORTHY, the
# whole point of the bell glyph.
STATE_ACTIVE = "ACTIVE"
STATE_STALLED = "STALLED"
STATE_ESCALATED = "ESCALATED"

_STATE_CHIP = {
    STATE_ACTIVE: "🟢 ACTIVE",
    STATE_STALLED: "🔴 STALLED",
    STATE_ESCALATED: "🔔 ESCALATED",
}

# The unattributed bucket label (a verdict recorded with no run_id — a loop that
# predates the docs/383 Phase-2 correlation wiring, or a one-off `dos improve` call).
# Surfaced honestly under one bucket rather than guessed onto a run by time (the
# docs/118 fail-toward-no-match rule the verdict journal's `for_run` keeps).
UNATTRIBUTED = "(unattributed)"


# ---------------------------------------------------------------------------
# Small helpers — the tolerant ISO parse + compact age, mirroring dispatch_top.
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None):
    import datetime as dt
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_ms(ts: str | None, *, now) -> int | None:
    """Age of an ISO stamp in milliseconds as of ``now`` (None if unparseable)."""
    import datetime as dt
    t = _parse_iso(ts)
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now - t).total_seconds() * 1000))


def _fmt_age(age_ms: int | None) -> str:
    """Compact age from milliseconds: 45s / 18m / 2h / 3d / '—' when unknown."""
    if age_ms is None:
        return "—"
    s = age_ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _int_or_none(d: Mapping, key: str) -> int | None:
    v = d.get(key)
    if isinstance(v, bool):  # a bool is an int subclass — never a metric count
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def _bool_or_none(d: Mapping, key: str):
    v = d.get(key)
    return bool(v) if isinstance(v, bool) else None


# ---------------------------------------------------------------------------
# One iteration — a single improve verdict, normalized from a journal event.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopIteration:
    """One cycle of a self-improving loop — a single `improve` verdict, normalized.

    Built from a `VerdictEvent` whose `syscall == "improve"`. Every field is read
    from the event's `verdict` token or its env-authored `detail` counts (the
    flattened `CandidateVerdict.to_dict()`); `narrated` is the candidate's own
    description, carried for the operator surface and folded into NOTHING — the
    docs/234 forgeable channel, kept honest by never entering a band or count.
    """

    ts: str
    seq: int
    verdict: str                       # KEEP | REVERT | ESCALATE
    work: int | None = None            # evidence.work — metric AFTER this candidate
    baseline_work: int | None = None   # evidence.baseline_work — metric BEFORE
    delta: int | None = None           # evidence.delta — signed change
    consecutive_reverts: int | None = None       # breaker count INTO this iteration
    next_consecutive_reverts: int | None = None   # breaker count carried OUT
    revert_cause: str = ""             # regressed | no-improvement | wasteful (REVERT only)
    escalation: str = ""               # "" | none | human
    suite_passed: bool | None = None
    truth_clean: bool | None = None
    tokens: int | None = None
    narrated: str = ""                 # context ONLY — never folded into a verdict
    subject: str = ""                  # the iteration tag, when the loop passed one

    @classmethod
    def from_event(cls, ev: _vj.VerdictEvent) -> "LoopIteration":
        d = ev.detail or {}
        return cls(
            ts=ev.ts or "",
            seq=int(ev.seq or 0),
            verdict=ev.verdict or "",
            work=_int_or_none(d, "evidence.work"),
            baseline_work=_int_or_none(d, "evidence.baseline_work"),
            delta=_int_or_none(d, "evidence.delta"),
            consecutive_reverts=_int_or_none(d, "evidence.consecutive_reverts"),
            next_consecutive_reverts=_int_or_none(d, "next_consecutive_reverts"),
            revert_cause=str(d.get("revert_cause") or ""),
            escalation=str(d.get("escalation") or ""),
            suite_passed=_bool_or_none(d, "evidence.suite_passed"),
            truth_clean=_bool_or_none(d, "evidence.truth_clean"),
            tokens=_int_or_none(d, "evidence.tokens"),
            narrated=str(d.get("evidence.narrated") or ""),
            subject=ev.subject or "",
        )

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "seq": self.seq,
            "verdict": self.verdict,
            "work": self.work,
            "baseline_work": self.baseline_work,
            "delta": self.delta,
            "consecutive_reverts": self.consecutive_reverts,
            "next_consecutive_reverts": self.next_consecutive_reverts,
            "revert_cause": self.revert_cause,
            "escalation": self.escalation,
            "suite_passed": self.suite_passed,
            "truth_clean": self.truth_clean,
            "tokens": self.tokens,
            "narrated": self.narrated,
            "subject": self.subject,
        }


# ---------------------------------------------------------------------------
# One loop — the folded trajectory of all its iterations.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopTrajectory:
    """One self-improving loop's whole trajectory, folded from its iterations.

    Pure data, no rich objects. The metric curve (`start_baseline`/`high_water`/
    `latest_work`/`net_gain`) answers "is it still improving?"; the breaker fields
    (`current_reverts`/`max_reverts`/`distance_to_escalate`/`escalated`) answer "how
    close to handing back to a human?"; the liveness fields (`last_age_ms`/`state`)
    answer "is it alive?". All from the kernel's own verdict stream.
    """

    run_id: str
    lane: str
    iterations: tuple[LoopIteration, ...]
    # tallies over the verdict tokens
    keeps: int
    reverts: int
    escalates: int
    # the revert-cause split (why the non-keeps)
    regressions: int
    no_improvements: int
    wastefuls: int
    # the metric curve
    start_baseline: int | None
    latest_work: int | None
    high_water: int | None
    net_gain: int | None
    # the breaker
    current_reverts: int
    max_reverts: int
    distance_to_escalate: int
    escalated: bool
    # liveness
    last_ts: str
    last_age_ms: int | None
    state: str

    @property
    def n(self) -> int:
        return len(self.iterations)

    @property
    def chip(self) -> str:
        return _STATE_CHIP.get(self.state, self.state)

    @property
    def display_run(self) -> str:
        return self.run_id or UNATTRIBUTED

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "display_run": self.display_run,
            "lane": self.lane,
            "n": self.n,
            "keeps": self.keeps,
            "reverts": self.reverts,
            "escalates": self.escalates,
            "regressions": self.regressions,
            "no_improvements": self.no_improvements,
            "wastefuls": self.wastefuls,
            "start_baseline": self.start_baseline,
            "latest_work": self.latest_work,
            "high_water": self.high_water,
            "net_gain": self.net_gain,
            # the per-iteration metric series — the shape a dashboard renders its own
            # sparkline from (the text screen renders `_sparkline(metric_curve)`).
            "work_curve": [it.work for it in self.iterations if it.work is not None],
            "current_reverts": self.current_reverts,
            "max_reverts": self.max_reverts,
            "distance_to_escalate": self.distance_to_escalate,
            "escalated": self.escalated,
            "last_ts": self.last_ts,
            "last_age_ms": self.last_age_ms,
            "state": self.state,
            "iterations": [it.to_dict() for it in self.iterations],
        }


def _fold_one(
    run_id: str,
    iterations: list[LoopIteration],
    *,
    now,
    max_reverts: int,
    stall_after_ms: int,
) -> LoopTrajectory:
    """Fold one run's ordered iterations into a `LoopTrajectory`. PURE.

    ``iterations`` MUST already be in chronological (ts, seq) order — `_order` does
    that once for the whole stream. The lane is the last non-empty lane seen (a loop
    can in principle move lanes; the current one is what the operator wants).
    """
    keeps = sum(1 for it in iterations if it.verdict == "KEEP")
    reverts = sum(1 for it in iterations if it.verdict == "REVERT")
    escalates = sum(1 for it in iterations if it.verdict == "ESCALATE")
    regressions = sum(1 for it in iterations if it.revert_cause == "regressed")
    no_improvements = sum(1 for it in iterations if it.revert_cause == "no-improvement")
    wastefuls = sum(1 for it in iterations if it.revert_cause == "wasteful")

    # The metric curve. start_baseline is the FIRST iteration's baseline (the tree the
    # loop started from); latest_work the LAST iteration's measured metric; high_water
    # the best the loop ever reached; net_gain how far the high-water beat the floor —
    # the cumulative improvement the loop has banked, a single number for "did this
    # run move the needle." All None-tolerant: a loop with no metric detail (pre-#381
    # fossils) folds to a None curve, never a crash.
    baselines = [it.baseline_work for it in iterations if it.baseline_work is not None]
    works = [it.work for it in iterations if it.work is not None]
    start_baseline = baselines[0] if baselines else None
    latest_work = works[-1] if works else None
    high_water = max(works) if works else None
    floor = baselines[0] if baselines else (min(works) if works else None)
    net_gain = (high_water - floor) if (high_water is not None and floor is not None) else None

    # The breaker. current_reverts is the count carried OUT of the last iteration
    # (next_consecutive_reverts) — 0 after a KEEP, the bumped value after a REVERT,
    # held at the tripping value on an ESCALATE. distance_to_escalate is how many more
    # non-keeps until the breaker hands back to a human; 0 once it already escalated.
    escalated = escalates > 0
    last = iterations[-1] if iterations else None
    current_reverts = 0
    if last is not None and last.next_consecutive_reverts is not None:
        current_reverts = last.next_consecutive_reverts
    distance = 0 if escalated else max(0, max_reverts - current_reverts)

    # Liveness. The age of the most-recent iteration → an ACTIVE/STALLED band, unless
    # the loop already ESCALATED (terminal — it is waiting on a human, not stalled).
    last_ts = last.ts if last is not None else ""
    last_age = _age_ms(last_ts, now=now) if last_ts else None
    if escalated:
        state = STATE_ESCALATED
    elif last_age is None or last_age > stall_after_ms:
        state = STATE_STALLED
    else:
        state = STATE_ACTIVE

    # The lane is grafted on by `trajectories_from_events` (which holds the per-run
    # lane map); _fold_one only sees the iterations, so it leaves lane empty here.
    return LoopTrajectory(
        run_id=run_id,
        lane="",
        iterations=tuple(iterations),
        keeps=keeps,
        reverts=reverts,
        escalates=escalates,
        regressions=regressions,
        no_improvements=no_improvements,
        wastefuls=wastefuls,
        start_baseline=start_baseline,
        latest_work=latest_work,
        high_water=high_water,
        net_gain=net_gain,
        current_reverts=current_reverts,
        max_reverts=max_reverts,
        distance_to_escalate=distance,
        escalated=escalated,
        last_ts=last_ts,
        last_age_ms=last_age,
        state=state,
    )


def trajectories_from_events(
    events: Iterable[_vj.VerdictEvent],
    *,
    now,
    max_reverts: int = DEFAULT_MAX_REVERTS,
    stall_after_ms: int = STALL_AFTER_MS,
) -> list[LoopTrajectory]:
    """Fold a verdict-event stream → one `LoopTrajectory` per RSI loop. PURE — no disk.

    Keeps only `syscall == "improve"` events (the RSI keep-gate dimension — every
    self-improve / enforce-tune cycle), groups them by `run_id`, orders each group
    chronologically, and folds. Returns the trajectories newest-activity-first (by the
    last iteration's stamp), so the loop that moved most recently is on top — the
    `dispatch_top` reading order. The clock is injected (`now`), never read inside.
    """
    by_run: dict[str, list[LoopIteration]] = {}
    lane_by_run: dict[str, str] = {}
    # Group by run_id (the "" bucket is the unattributed one). `read_all` yields each
    # journal line exactly once, so every event is a distinct iteration — no dedupe
    # (which could only drop a real cycle on a pathological seq collision, never
    # protect against a real double-count).
    for ev in events:
        if getattr(ev, "syscall", "") != SYSCALL_IMPROVE:
            continue
        rid = ev.run_id or ""
        by_run.setdefault(rid, []).append(LoopIteration.from_event(ev))
        if ev.lane:
            lane_by_run[rid] = ev.lane

    out: list[LoopTrajectory] = []
    for rid, items in by_run.items():
        ordered = sorted(items, key=lambda it: (it.ts or "", it.seq))
        traj = _fold_one(
            rid, ordered, now=now, max_reverts=max_reverts, stall_after_ms=stall_after_ms
        )
        # _fold_one cannot see the per-run lane map; graft the lane on here.
        if lane_by_run.get(rid):
            traj = _with_lane(traj, lane_by_run[rid])
        out.append(traj)
    # Newest activity first; an empty/unparseable last_ts sorts last (oldest).
    out.sort(key=lambda t: (t.last_ts or ""), reverse=True)
    return out


def _with_lane(traj: LoopTrajectory, lane: str) -> LoopTrajectory:
    """Return a copy of ``traj`` with ``lane`` set (frozen-dataclass replace)."""
    import dataclasses as _dc
    return _dc.replace(traj, lane=lane)


# ---------------------------------------------------------------------------
# Rendering — the plain-text floor (always available). Pure over the data.
# ---------------------------------------------------------------------------

_WIDTH = 78

# The eight block glyphs for the inline metric sparkline — the trajectory's SHAPE at
# a glance. A steady climb, a sawtooth (KEEP/REVERT churn), and a long plateau all
# read identically in a "40→58 (hi 58, +18)" endpoint string; the sparkline is what
# tells them apart. The plan's headline question — "is it still climbing, or flat?" —
# is a question about the curve's shape, not its endpoints (docs/383). Pure ASCII-art
# over the env-measured `work` values; carries no narration.
_SPARK_GLYPHS = "▁▂▃▄▅▆▇█"
_SPARK_WIDTH = 32  # cap the inline sparkline; a longer run is bucket-averaged to fit


def metric_curve(t: LoopTrajectory) -> list[int]:
    """The per-iteration measured `work` values in order — the trajectory's shape.

    The bytes the sparkline renders and the `--json` `work_curve` exposes: each
    iteration's env-measured metric, skipping the None-metric fossils (pre-#381 events
    with no `evidence.work`). PURE — the kernel's own counts, never the narration."""
    return [it.work for it in t.iterations if it.work is not None]


def _downsample(values: list[int], width: int) -> list[int]:
    """Compress a series to at most ``width`` points by averaging contiguous buckets.

    Preserves the WHOLE shape (a 200-cycle sawtooth still reads as a sawtooth), unlike
    a tail crop that would hide the early run. PURE; integer-averages each bucket."""
    n = len(values)
    if n <= width or width <= 0:
        return values
    out: list[int] = []
    for i in range(width):
        lo = i * n // width
        hi = (i + 1) * n // width
        chunk = values[lo:hi] or [values[min(lo, n - 1)]]
        out.append(sum(chunk) // len(chunk))
    return out


def _sparkline(values: list[int], *, width: int = _SPARK_WIDTH) -> str:
    """Render an int series as block glyphs normalized across its own min..max.

    A flat series (all equal, or a single point) renders as one mid-height glyph per
    point — never a fake ramp. An empty series → "" (no curve to show). A series longer
    than ``width`` is bucket-averaged first, so the shape survives the width cap. PURE —
    no I/O, no clock; the `observe`/`dispatch_top` pure-render discipline."""
    nums = [v for v in values if v is not None]
    if not nums:
        return ""
    nums = _downsample(nums, width)
    lo, hi = min(nums), max(nums)
    if hi == lo:  # a flat run: every point the same height, never a manufactured slope
        return _SPARK_GLYPHS[len(_SPARK_GLYPHS) // 2] * len(nums)
    span = hi - lo
    last = len(_SPARK_GLYPHS) - 1
    return "".join(_SPARK_GLYPHS[(v - lo) * last // span] for v in nums)


def _metric_str(t: LoopTrajectory) -> str:
    """`40→58 (hi 58, +18)` — the metric curve, or "" when no metric was recorded."""
    if t.latest_work is None and t.start_baseline is None:
        return ""
    a = t.start_baseline if t.start_baseline is not None else "?"
    b = t.latest_work if t.latest_work is not None else "?"
    extra = ""
    if t.high_water is not None and t.net_gain is not None:
        sign = "+" if t.net_gain >= 0 else ""
        extra = f" (hi {t.high_water}, {sign}{t.net_gain})"
    return f"{a}→{b}{extra}"


def _breaker_str(t: LoopTrajectory) -> str:
    s = f"breaker {t.current_reverts}/{t.max_reverts}"
    if t.escalated:
        s += " → human"
    return s


def render_loops_text(
    trajectories: tuple[LoopTrajectory, ...] | list[LoopTrajectory],
) -> str:
    """The summary screen — one block per RSI loop, newest-activity-first.

    Each loop gets a headline (run-id, state chip, iteration count, the KEEP/REVERT/
    ESCALATE tally, the metric curve, the breaker distance, the last-iteration age)
    plus an indented context line (lane + the latest cycle's situation). `(none)`
    when there are no RSI loops in the journal — the steady-state of a workspace that
    has not run one, and the byte-stable floor.
    """
    trajectories = list(trajectories)
    out = ["# self-improving loops    [improve verdicts folded by run · read-only]"]
    if not trajectories:
        out.append("  (no self-improving loops recorded — run dos-self-improve / "
                   "dos-enforce-tune with --observe, then watch here)")
        return "\n".join(out)
    active = sum(1 for t in trajectories if t.state == STATE_ACTIVE)
    stalled = sum(1 for t in trajectories if t.state == STATE_STALLED)
    escalated = sum(1 for t in trajectories if t.state == STATE_ESCALATED)
    out.append(
        f"  {len(trajectories)} loop(s) · {active} active · {stalled} stalled · "
        f"{escalated} escalated"
    )
    out.append("")
    for t in trajectories:
        metric = _metric_str(t)
        metric_seg = f"   metric {metric}" if metric else ""
        tally = f"KEEP·{t.keeps} REVERT·{t.reverts} ESC·{t.escalates}"
        out.append(
            f"  {t.display_run:<24} {t.chip:<14} iter {t.n:<3} {tally}"
            f"{metric_seg}   {_breaker_str(t)}   last {_fmt_age(t.last_age_ms)}"
        )
        # The context line: lane + the latest iteration's one-line situation, clipped
        # to the screen width (a narrated context can be long; only the trailing note
        # is cut, the dispatch_top discipline).
        ctx_bits: list[str] = []
        if t.lane:
            ctx_bits.append(f"lane={t.lane}")
        spark = _sparkline(metric_curve(t))
        if spark:
            ctx_bits.append(spark)
        last = t.iterations[-1] if t.iterations else None
        if last is not None:
            ctx_bits.append(_iteration_note(last))
        if ctx_bits:
            out.append(("    " + "  ".join(ctx_bits))[: _WIDTH + 2])
    return "\n".join(out)


def _iteration_note(it: LoopIteration) -> str:
    """A one-line, byte-clean situation for one iteration — verdict + the env facts.

    Built from the kernel verdict + the env-authored counts only. The `narrated`
    string is NEVER used here (it is the forgeable channel); the operator reads it via
    `--json` / the full per-iteration view, clearly labelled as context.
    """
    if it.verdict == "KEEP":
        if it.delta is not None and it.baseline_work is not None and it.work is not None:
            return f"last KEEP: metric {it.baseline_work} -> {it.work} (+{it.delta})"
        return "last KEEP"
    if it.verdict == "ESCALATE":
        cause = f" ({it.revert_cause})" if it.revert_cause else ""
        return f"last ESCALATE{cause}: handed to a human"
    if it.verdict == "REVERT":
        cause = it.revert_cause or "reverted"
        return f"last REVERT: {cause}"
    return f"last {it.verdict}" if it.verdict else "no verdict"


def render_trajectory_text(t: LoopTrajectory) -> str:
    """One loop's FULL per-iteration curve — the `--loops --run <RID>` view.

    A header line (the same headline as the summary), then one row per iteration in
    order: the cycle's verdict, its metric move, the breaker count, and the env
    witnesses. The `narrated` context rides a dimmed trailing note so the operator can
    read *why* a reverted candidate was proposed — clearly downstream of the verdict,
    never part of it.
    """
    out = [f"# loop {t.display_run}    [{t.chip}]"]
    metric = _metric_str(t)
    if metric:
        out.append(f"  metric {metric}   {_breaker_str(t)}   "
                   f"last {_fmt_age(t.last_age_ms)}")
    else:
        out.append(f"  {_breaker_str(t)}   last {_fmt_age(t.last_age_ms)}")
    out.append(
        f"  {t.n} iteration(s) · KEEP·{t.keeps} REVERT·{t.reverts} ESC·{t.escalates}"
        + (f"  (reverts: {t.regressions} regressed, {t.no_improvements} no-improvement, "
           f"{t.wastefuls} wasteful)" if t.reverts or t.escalates else "")
    )
    if t.lane:
        out.append(f"  lane={t.lane}")
    curve = metric_curve(t)
    spark = _sparkline(curve)
    if spark:
        out.append(f"  curve {spark}  ({len(curve)} pt)")
    out.append("")
    if not t.iterations:
        out.append("  (no iterations)")
        return "\n".join(out)
    header = f"  {'#':>3} {'ts':<20} {'verdict':<9} {'metric':<16} {'breaker':<8} witnesses"
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for i, it in enumerate(t.iterations, start=1):
        metric_cell = "—"
        if it.baseline_work is not None and it.work is not None:
            d = it.delta if it.delta is not None else (it.work - it.baseline_work)
            sign = "+" if d >= 0 else ""
            metric_cell = f"{it.baseline_work}->{it.work} ({sign}{d})"
        brk = "—"
        if it.next_consecutive_reverts is not None:
            brk = f"{it.next_consecutive_reverts}/{t.max_reverts}"
        witnesses = _witness_str(it)
        note = ""
        if it.revert_cause:
            note = f"  [{it.revert_cause}]"
        out.append(
            f"  {i:>3} {(it.ts or '-'):<20} {(it.verdict or '-'):<9} "
            f"{metric_cell:<16} {brk:<8} {witnesses}{note}"[: _WIDTH + 24]
        )
    return "\n".join(out)


def _witness_str(it: LoopIteration) -> str:
    """The env-authored witnesses for one iteration — `suite✓ truth✓ 1234t`."""
    bits: list[str] = []
    if it.suite_passed is not None:
        bits.append("suite✓" if it.suite_passed else "suite✗")
    if it.truth_clean is not None:
        bits.append("truth✓" if it.truth_clean else "truth✗")
    if it.tokens:
        bits.append(f"{it.tokens}t")
    return " ".join(bits) if bits else "—"
