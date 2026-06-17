"""FRS — the freshness verdict: *is this always-on job still firing on its cadence?*

The temporal completion of `pulse`, turned on the WATCHERS themselves.

`pulse` (docs/121 §4.1) is DOS's standing self-watch: a cron job that folds every
live RUN's `liveness` and asks "is anything wrong?". But `pulse` — and every other
always-on job DOS leans on (the supervisor cadence, `enforce-tune`, the docs/374
steward population) — has a failure mode `pulse` itself cannot see: **it can
silently stop firing.** A cron's deadliest failure is not a loud crash; it is a
quiet death. And by the silence rule (`pulse` SPEAKS only when something is wrong),
a dead pulse emits *exactly the same thing a healthy one does on a quiet cycle:*
nothing. The absence of an alarm reads, to anything downstream, as "all clear" —
when it may mean "the alarm itself is gone." That is the dead-man's-switch gap.

This module closes it. The shape is `liveness`'s, lifted one level up:

    liveness.classify  — is this RUN moving?         (per-lease, ground-truth delta)
    freshness.classify — is this JOB still beating?  (per-cron, beat-ledger age)
                         ^ THIS module

Where `liveness` reads a run's commit/heartbeat age against ADVANCING/SPINNING/
STALLED windows, `freshness` reads a recurring job's last-beat age against its
DECLARED cadence. A job that should beat every 6h and last beat 14h ago is the
same kind of finding a STALLED run is — surfaced, never silently absent.

The kernel/driver split — this is KERNEL, and PURE
==================================================

`classify` / `fold` are PURE transforms: already-gathered facts (a beat age, a
declared cadence) in, one typed `FreshnessVerdict` out. They mint no belief and
read no clock — `now_ms` is injected by the caller, exactly as `liveness.classify`
never reads a clock. The I/O that records a beat and reads the ledger lives at the
CLI boundary (`dos beat` / `cmd_pulse`'s freshness gather), the same "I/O at the
boundary, data to the pure core" rule `liveness` / `pulse` / `session_digest` rest
on. Delivery is the notifier seam's job (the `pulse` fold carries a freshness leg);
this module names no transport and no host.

What a beat is — and is NOT (the honest boundary)
=================================================

A beat is a **proof-of-fire**, not a proof-of-work. `dos beat <job>` records that
the scheduled thing *ran at all* at time T — the dead-man's-switch ping. It does
NOT claim the run did anything useful; that is what `liveness` (did the run move?)
and `verify` (did the claimed effect land?) are for. So the beat is forgeable in
the weak sense that a job could beat while doing nothing — but it is NOT forgeable
in the dimension freshness cares about: the *timing* is stamped by the boundary
clock at append, not by the job, so a job cannot make a missed window look on-time.
Freshness answers one narrow, high-value question — *did the cron fire when it
should have?* — and leaves correctness to the rungs built for it. Pair it with
`liveness`/`verify` for a job whose beats must also mean progress.

The verdict ladder, top to bottom (a reader holds it in their head):

  1. FRESH   — the newest beat is younger than `cadence × grace_factor`. The job
               fired within its expected window (plus a little jitter slack). No
               freshness problem.
  2. LATE    — the newest beat is older than the FRESH bound but younger than
               `cadence × dead_factor`: the job missed an expected window but has
               beaten recently enough that it is probably just slow/queued, not
               dead. The soft signal (WARN) — worth a glance, not an alarm.
  3. MISSING — the newest beat is older than `cadence × dead_factor`, OR there is
               no beat at all for a declared job. The dead-man's switch: the job is
               presumed gone. The loud signal (URGENT for a `critical` job) — the
               whole point of the module, the silent death made visible.

`FreshnessVerdict` is ADVISORY (the docs/99 floor): freshness REPORTS; it never
restarts a job or refuses anything. A `pulse` fold may surface a MISSING cron and
an operator (or a supervisor driver) may act — but the verdict and any actuation
stay different concerns.

No-config-no-noise discipline: freshness judges only jobs a workspace has
DECLARED it expects to beat (the `dos.toml [heartbeats]` table — closed config as
data, the `[lanes]`/`[liveness]` pattern). A workspace that declares nothing folds
to zero verdicts and zero noise; you opt in by naming what *should* be beating —
which is also the only way "never beat" can be a real finding rather than a guess.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence


class Freshness(str, enum.Enum):
    """The typed freshness verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `liveness.Liveness` and `gate_classify.Verdict`).
    """

    FRESH = "FRESH"      # last beat within the expected window — firing on cadence
    LATE = "LATE"        # missed a window but beaten recently — probably slow, not dead
    MISSING = "MISSING"  # no beat for a declared job, or far past cadence — dead-man's switch

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class CadencePolicy:
    """The slack factors that separate FRESH / LATE / MISSING — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as
    `liveness.LivenessPolicy`'s windows. A job's nominal `cadence_ms` is the
    expected gap between beats; real schedulers jitter (a queued cron, a slow
    runner), so the boundaries are MULTIPLES of the cadence, not the bare cadence:

      grace_factor — a beat younger than `cadence × grace_factor` is FRESH. The
                     slack that absorbs ordinary jitter so a job that fires a few
                     minutes late on a 6h cadence is not cried over. Default 1.5
                     (half a cadence of slack).
      dead_factor  — a beat older than `cadence × dead_factor` (or no beat at all)
                     is MISSING. Three missed windows is not jitter — it is a job
                     that has stopped. Default 3.0.

    Between the two it is LATE. The defaults are GENERIC; a workspace tunes them in
    `dos.toml [heartbeats]` (per-job or as a default), the closed-config-as-data
    pattern.
    """

    grace_factor: float = 1.5  # FRESH bound = cadence × this
    dead_factor: float = 3.0   # MISSING bound = cadence × this

    def __post_init__(self) -> None:
        if self.grace_factor < 1.0:
            raise ValueError("grace_factor must be >= 1.0 (a beat at the nominal cadence is FRESH)")
        if self.dead_factor < self.grace_factor:
            raise ValueError("dead_factor must be >= grace_factor (MISSING is past LATE)")


DEFAULT_POLICY = CadencePolicy()


@dataclass(frozen=True)
class JobCadence:
    """One declared always-on job and how often it is expected to beat.

    The unit of the `dos.toml [heartbeats]` table. `job_id` is the stable name a
    cron passes to `dos beat <job_id>` (e.g. "pulse", "supervise", "enforce-tune").
    `cadence_ms` is the expected gap between beats. `critical` decides how loud a
    MISSING is: a critical job gone silent is URGENT (the pulse must alarm); a
    non-critical one is a WARN. Most always-on jobs are critical by default —
    declare `critical = false` for a best-effort beat whose silence is informational.
    """

    job_id: str
    cadence_ms: int
    critical: bool = True

    def __post_init__(self) -> None:
        if self.cadence_ms <= 0:
            raise ValueError("cadence_ms must be positive (a job that never beats has no cadence)")


@dataclass(frozen=True)
class FreshnessVerdict:
    """The single verdict `classify()` returns, with the evidence echoed back.

    `verdict` is the typed `Freshness`. `reason` is a one-line operator-facing
    summary (the tally-row string). `last_beat_age_ms` (None = never beat),
    `cadence_ms`, and `critical` are carried so `dos beat --check --json` and the
    `pulse` fold emit the verdict AND the facts behind it (legible distrust: not
    just MISSING but *why* — last beat 14h ago vs a 6h cadence). `to_dict` is the
    json shape.
    """

    job_id: str
    verdict: Freshness
    reason: str
    last_beat_age_ms: Optional[int]
    cadence_ms: int
    critical: bool = True

    @property
    def is_problem(self) -> bool:
        """True for LATE or MISSING — anything a pulse/operator should see."""
        return self.verdict is not Freshness.FRESH

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "last_beat_age_ms": self.last_beat_age_ms,
            "cadence_ms": self.cadence_ms,
            "critical": self.critical,
        }


def classify(
    *,
    job_id: str,
    last_beat_age_ms: Optional[int],
    cadence_ms: int,
    critical: bool = True,
    policy: CadencePolicy = DEFAULT_POLICY,
) -> FreshnessVerdict:
    """Classify one job's freshness from its last-beat age. PURE — no I/O, no clock.

    Reads the ladder top to bottom (this function IS the answer to "is it still
    firing?"):

      1. FRESH   — `last_beat_age_ms` <= cadence × grace_factor.
      2. LATE    — FRESH bound < age <= cadence × dead_factor.
      3. MISSING — age > cadence × dead_factor, OR age is None (never beat for a
                   declared job — the dead-man's switch).

    `last_beat_age_ms` is `now_ms - newest_beat_ms`, computed by the CALLER (the
    boundary reads the ledger and the clock; the verdict reads neither). None means
    no beat has ever been recorded for this declared job — treated as MISSING,
    because a job a workspace declared it expects but has never heard from is
    exactly the silent-death the module exists to catch.
    """
    if cadence_ms <= 0:
        raise ValueError("cadence_ms must be positive")

    fresh_bound = cadence_ms * policy.grace_factor
    dead_bound = cadence_ms * policy.dead_factor

    if last_beat_age_ms is None:
        return FreshnessVerdict(
            job_id=job_id,
            verdict=Freshness.MISSING,
            reason=(
                f"no beat ever recorded for declared job '{job_id}' (expected every "
                f"{_human_ms(cadence_ms)}) — never fired, or never wired to `dos beat`"
            ),
            last_beat_age_ms=None,
            cadence_ms=cadence_ms,
            critical=critical,
        )

    if last_beat_age_ms < 0:
        # A beat stamped in the future (clock skew between the beating host and the
        # reader). It is trivially within any window — treat as FRESH, but say so:
        # a freshness verdict should never be MORE alarmed by a too-new beat.
        return FreshnessVerdict(
            job_id=job_id,
            verdict=Freshness.FRESH,
            reason=(
                f"'{job_id}' last beat is {_human_ms(-last_beat_age_ms)} in the future "
                f"(clock skew); treated as fresh"
            ),
            last_beat_age_ms=last_beat_age_ms,
            cadence_ms=cadence_ms,
            critical=critical,
        )

    if last_beat_age_ms <= fresh_bound:
        return FreshnessVerdict(
            job_id=job_id,
            verdict=Freshness.FRESH,
            reason=(
                f"'{job_id}' beat {_human_ms(last_beat_age_ms)} ago "
                f"(<= {_human_ms(int(fresh_bound))} grace on a {_human_ms(cadence_ms)} cadence) "
                f"— firing on schedule"
            ),
            last_beat_age_ms=last_beat_age_ms,
            cadence_ms=cadence_ms,
            critical=critical,
        )

    if last_beat_age_ms <= dead_bound:
        return FreshnessVerdict(
            job_id=job_id,
            verdict=Freshness.LATE,
            reason=(
                f"'{job_id}' last beat {_human_ms(last_beat_age_ms)} ago "
                f"(> {_human_ms(int(fresh_bound))} grace, <= {_human_ms(int(dead_bound))} dead "
                f"on a {_human_ms(cadence_ms)} cadence) — missed a window, probably slow"
            ),
            last_beat_age_ms=last_beat_age_ms,
            cadence_ms=cadence_ms,
            critical=critical,
        )

    return FreshnessVerdict(
        job_id=job_id,
        verdict=Freshness.MISSING,
        reason=(
            f"'{job_id}' last beat {_human_ms(last_beat_age_ms)} ago "
            f"(> {_human_ms(int(dead_bound))} dead on a {_human_ms(cadence_ms)} cadence) "
            f"— presumed dead (the cron stopped firing)"
        ),
        last_beat_age_ms=last_beat_age_ms,
        cadence_ms=cadence_ms,
        critical=critical,
    )


def latest_beats(records: Iterable[Mapping]) -> dict[str, int]:
    """Fold raw beat records into the newest beat-ms per job. PURE.

    Each record is a `{"job": str, "ts_ms": int, ...}` mapping (the JSONL shape the
    `dos beat` boundary appends). Returns `{job_id: newest_ts_ms}` keeping the
    MAX ts per job, so out-of-order or duplicate records fold to the freshest. A
    record missing a `job`/`ts_ms` (a torn line a tolerant reader kept) is skipped
    — a half-written beat is "didn't happen", the safe ledger reading.
    """
    newest: dict[str, int] = {}
    for rec in records:
        try:
            job = str(rec.get("job") or "").strip()
            ts = rec.get("ts_ms")
            if not job or ts is None:
                continue
            ts_i = int(ts)
        except (AttributeError, TypeError, ValueError):
            continue
        prev = newest.get(job)
        if prev is None or ts_i > prev:
            newest[job] = ts_i
    return newest


def fold(
    *,
    now_ms: int,
    cadences: Sequence[JobCadence],
    newest_beat_ms: Mapping[str, int],
    policy: CadencePolicy = DEFAULT_POLICY,
) -> tuple[FreshnessVerdict, ...]:
    """Classify every DECLARED job's freshness against the ledger. PURE — no I/O.

    One verdict per declared `JobCadence` (a job a workspace said it expects to
    beat). `newest_beat_ms` is `latest_beats(...)` over the ledger; a declared job
    absent from it has never beaten and folds to MISSING (the silent-death catch).
    A beat for an UN-declared job is ignored — freshness judges only the closed,
    declared set, the no-config-no-noise rule. `now_ms` is injected (the clock is
    the boundary's, never the verdict's).

    Returned in the declared order so the surface is stable across folds.
    """
    out: list[FreshnessVerdict] = []
    for jc in cadences:
        beat_ms = newest_beat_ms.get(jc.job_id)
        age = None if beat_ms is None else now_ms - beat_ms
        out.append(
            classify(
                job_id=jc.job_id,
                last_beat_age_ms=age,
                cadence_ms=jc.cadence_ms,
                critical=jc.critical,
                policy=policy,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Cadence-string parsing — the `[heartbeats]` table writes "6h", not 21600000.
# ---------------------------------------------------------------------------

# A duration token: a number (int or decimal) + an optional unit suffix. A bare
# number is SECONDS (the least-surprising default for a "cadence" field; ms would
# make "30" mean a 30ms cadence, which no cron has). Units: s/m/h/d/w.
_DURATION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([smhdw]?)\s*$", re.IGNORECASE)
_UNIT_MS = {
    "": 1000,             # bare number = seconds
    "s": 1000,
    "m": 60 * 1000,
    "h": 60 * 60 * 1000,
    "d": 24 * 60 * 60 * 1000,
    "w": 7 * 24 * 60 * 60 * 1000,
}


def parse_cadence(value) -> int:
    """Parse a cadence string/number into milliseconds. PURE.

    Accepts an int/float (seconds) or a string like "90s" / "30m" / "6h" / "1d" /
    "2w"; a bare numeric string is seconds. Raises `ValueError` on anything else so
    a typo in `dos.toml [heartbeats]` is a loud config error at load, not a silent
    zero-cadence that would make every job read FRESH forever.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject before the numeric path
        raise ValueError(f"cadence must be a duration, not a bool: {value!r}")
    if isinstance(value, (int, float)):
        ms = int(round(float(value) * 1000))
        if ms <= 0:
            raise ValueError(f"cadence must be positive: {value!r}")
        return ms
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(
            f"unparseable cadence {value!r} — use e.g. '90s', '30m', '6h', '1d', '2w', "
            f"or a bare number of seconds"
        )
    qty, unit = m.group(1), m.group(2).lower()
    ms = int(round(float(qty) * _UNIT_MS[unit]))
    if ms <= 0:
        raise ValueError(f"cadence must be positive: {value!r}")
    return ms


def _human_ms(ms: int) -> str:
    """A compact human duration for a verdict reason ('6h', '14h', '45m', '90s').

    Not for round-tripping (it rounds to the largest whole unit) — purely for the
    operator-facing reason string, so a MISSING line reads '14h ago' not
    '50400000 ms ago'. PURE.
    """
    if ms < 0:
        return "-" + _human_ms(-ms)
    sec = ms / 1000.0
    if sec < 90:
        return f"{int(round(sec))}s"
    minutes = sec / 60.0
    if minutes < 90:
        return f"{int(round(minutes))}m"
    hours = minutes / 60.0
    if hours < 48:
        return f"{int(round(hours))}h"
    days = hours / 24.0
    return f"{int(round(days))}d"
