"""pulse — DOS's standing self-watch heartbeat, folded for a cron cadence.

> **DOS has rich folded-verdict machinery but nothing that RUNS it on its own.**
> `liveness.classify` (is this run moving?), the HUMAN `decisions` queue (what needs
> a person?), the `breaker` (has a failure class tripped?) are all REQUEST-RESPONSE
> — pulled by an interactive verb or a loop's Step 0. Nothing periodically asks
> *"what is the state of everything right now, and is anything wrong that nobody has
> looked at?"* That is exactly what a cron job is for, and `dos pulse` is the verb a
> cron/Actions job runs on a cadence to give DOS that standing pulse. Its first job
> is to be the READER for the queue [docs/121](121_first-class-on-devices-and-unattended.md)
> §4.1 names: unattended, *"a refusal routed to an absent operator blocks forever —
> and 'blocks forever' silently reads, to anything downstream, as 'didn't
> happen.'"* The pulse is the absent operator's proxy.

The kernel/driver split — this is KERNEL, and PURE
==================================================

`fold_pulse` is a PURE transform: already-gathered facts (per-run `liveness`
verdicts + the ranked HUMAN `decisions` rows + a `breaker` verdict) in, one typed
`PulseDigest` out. It mints NO new belief and adjudicates nothing new — every signal
it folds was decided by a kernel verdict the boundary already ran. The I/O that
gathers those facts (`lane_lease.live_leases` → per-run `liveness.ProgressEvidence` →
`liveness.classify`; `decisions.collect_decisions`; `breaker.classify`) lives at the
CLI boundary (`cli.py:cmd_pulse`) — the same "I/O at the boundary, data to the pure
core" rule `liveness.classify` / `session_digest.build_digest` / `help_summary`
already rest on. Delivery is the notifier seam's job (`notify.notification_for_pulse`
→ `send_safely`); this module names no transport.

The silence rule
================

A pulse SPEAKS only when something is wrong. An all-clear workspace — no STALLED
run, an empty HUMAN queue, a CLOSED breaker — folds to an EMPTY digest, and the verb
pushes nothing, exactly as `session_digest.build_digest` returns `None`. A heartbeat
that fires a notification every quiet cycle is noise the operator learns to mute; the
silence is what keeps an URGENT pulse worth reading.

The advisory floor (docs/99)
============================

The pulse REPORTS; it never acts on the fleet. `fold_pulse` is pure; the verb takes
no lease, stops no run, mutates no state — it is a read-of-projections → fold → push.
A LIVENESS-stalled pulse *describes* a hung run; enacting any stop stays the
operator's call. This is `decisions.py`'s and `notify.py`'s locked read-only-router
model, now on a cadence nothing currently provides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

# The closed severity vocabulary — string-valued, matching `notify.Severity` so the
# digest's worst-signal level maps straight onto a `Notification` without a lookup.
INFO = "INFO"      # nothing wrong (an empty digest carries this implicitly)
WARN = "WARN"      # a pending HUMAN queue / an OPEN breaker — needs a person, not NOW
URGENT = "URGENT"  # a STALLED run — something is hung right now

# The liveness verdicts a pulse treats as "wrong" and pushes. ADVANCING is healthy
# and never surfaced; SPINNING (alive but not moving) is a softer signal carried at
# WARN; STALLED (dead/hung) is the URGENT one. Matched case-insensitively against the
# verdict's string value so a `liveness.Liveness` enum or a plain string both fold.
_STALLED = "STALLED"
_SPINNING = "SPINNING"


@dataclass(frozen=True)
class PulseDigest:
    """The one folded fact a cron pulse pushes — pure data, no transport shape.

    `empty` is the silence rule made explicit: True ⇒ nothing worth pushing (the verb
    emits nothing). `severity` is the worst signal folded (URGENT › WARN › INFO), so a
    transport routes a hung run louder than a pending queue — the
    `notification_for_decisions`/`_for_top` posture. `lines` are the one-per-finding
    operator-facing strings (the plain-text body a notifier with no rich surface still
    says in full). `stalled` / `pending_human` / `breaker_open` are the folded counts,
    carried for the `--json` consumer and the `(label, value)` glance fields.
    """

    empty: bool
    severity: str
    lines: tuple[str, ...] = ()
    stalled: int = 0
    pending_human: int = 0
    breaker_open: bool = False
    enforce_false_denies: int = 0  # docs/365 — denies the operator later overrode

    def to_dict(self) -> dict:
        return {
            "empty": self.empty,
            "severity": self.severity,
            "lines": list(self.lines),
            "stalled": self.stalled,
            "pending_human": self.pending_human,
            "breaker_open": self.breaker_open,
            "enforce_false_denies": self.enforce_false_denies,
        }


def _verdict_word(v: Any) -> str:
    """The uppercase verdict string from a `LivenessVerdict` stand-in or enum/str.

    Reads `.verdict.value` (a `liveness.LivenessVerdict` carries a `Liveness` enum),
    then `.value` (a bare enum), then `str()` — so the fold is robust to exactly what
    the boundary hands it without importing `liveness` (the dependency-arrow rule).
    """
    inner = getattr(v, "verdict", v)
    word = getattr(inner, "value", inner)
    return str(word or "").upper()


def _run_label(v: Any) -> str:
    """`RID-3f2 (src)` — the run id + its lane, for naming a stalled run in a line.

    The boundary attaches `run_id`/`lane` to each verdict it gathers (a
    `LivenessVerdict` itself carries neither — they come from the live lease the
    evidence was gathered for). Both are optional; a bare run id (or `?`) degrades
    gracefully.
    """
    run_id = str(getattr(v, "run_id", "") or "").strip() or "?"
    lane = str(getattr(v, "lane", "") or "").strip()
    return f"{run_id} ({lane})" if lane else run_id


def _decision_phrase(d: Any) -> str:
    """One queued HUMAN decision → a short phrase: `SELF_MODIFY @ src ×2`.

    Duck-typed over `.reason_token`/`.reason_text`/`.lane`/`.dup_count` — the same
    fields `notify._decision_field` reads — so this module needs no `decisions`
    import. The token (or prose) names WHY; the lane names WHERE; a dup count rides
    when one row stands in for several identical denies.
    """
    token = str(getattr(d, "reason_token", "") or "").strip()
    text = str(getattr(d, "reason_text", "") or "").strip()
    lane = str(getattr(d, "lane", "") or "").strip()
    what = token or text or "?"
    phrase = f"{what} @ {lane}" if lane else what
    dup = getattr(d, "dup_count", 1) or 1
    if dup > 1:
        phrase = f"{phrase} ×{dup}"
    return phrase


def _breaker_is_open(breaker_verdict: Any) -> bool:
    """True iff the folded breaker verdict is OPEN. Mirrors `session_digest`.

    Reads the `.is_open` property a `breaker.BreakerVerdict` exposes, falling back to
    a `.state` value of "OPEN" — so a stand-in or the real verdict both fold. None /
    absent ⇒ not open (a missing breaker is the normal, silent state).
    """
    if breaker_verdict is None:
        return False
    is_open = getattr(breaker_verdict, "is_open", None)
    if is_open is None:
        state = getattr(breaker_verdict, "state", None)
        is_open = str(getattr(state, "value", state) or "").upper() == "OPEN"
    return bool(is_open)


# The false-DENY count at/above which the pulse surfaces the enforcement-tuning
# signal (docs/365). One overturned deny is noise (an operator made a one-off
# exception); a standing count means the enforcement policy is over-blocking a target
# the workspace genuinely edits, and a tuning run is warranted. Default 3 — the same
# "3 strikes is a pattern" threshold the `_from_enforce_storms` breaker uses. A host
# tunes it via the boundary (the verb passes the gathered count + threshold).
_ENFORCE_FALSE_DENY_THRESHOLD = 3


def _enforce_false_denies(enforce_metric: Any) -> int:
    """The false-DENY count from a folded `enforce_outcomes.EnforceMetric` stand-in.

    Duck-typed (`.false_denies`) so this module needs no `enforce_outcomes` import —
    the same robustness `_breaker_is_open` has. None / absent ⇒ 0 (no enforcement
    signal was gathered — the silent default)."""
    if enforce_metric is None:
        return 0
    n = getattr(enforce_metric, "false_denies", 0)
    try:
        return max(0, int(n))
    except (TypeError, ValueError):
        return 0


def fold_pulse(
    *,
    liveness: Sequence[Any] = (),
    decisions: Sequence[Any] = (),
    breaker_verdict: Any = None,
    enforce_metric: Any = None,
    enforce_threshold: int = _ENFORCE_FALSE_DENY_THRESHOLD,
) -> PulseDigest:
    """Fold the already-gathered fleet facts into one `PulseDigest`. PURE — no I/O.

    Returns an EMPTY digest (the silence rule) when nothing is wrong: no STALLED run,
    an empty HUMAN decisions queue, a CLOSED/absent breaker, an enforcement false-DENY
    count below threshold. Otherwise the digest names each finding in `lines` and
    carries the worst-signal `severity`.

    `liveness`        — per-run `liveness.LivenessVerdict`s the boundary classified,
                        each carrying the run id + lane it was gathered for. Only
                        STALLED (URGENT) and SPINNING (WARN) surface; ADVANCING is
                        healthy and silent.
    `decisions`       — the ranked HUMAN rows from `decisions.collect_decisions(
                        resolver="HUMAN")` — the queue that goes unread when nobody
                        is watching (docs/121 §4.1).
    `breaker_verdict` — a folded `breaker.BreakerVerdict` (or None); only OPEN speaks.
    `enforce_metric`  — a folded `enforce_outcomes.EnforceMetric` (or None): the
                        false-DENY / held-catch ledger of the OP_ENFORCE journal
                        (docs/365). A standing false-DENY count (>= `enforce_threshold`)
                        surfaces a WARN tuning signal — the continuous-OBSERVE half of
                        the autonomous enforcement-tuning loop (pulse names the
                        over-blocking; the autonomous `enforce-tune` cadence acts on it).
    """
    lines: list[str] = []
    severity = INFO

    # 1. STALLED / SPINNING runs — the loudest signal (a run is hung NOW → URGENT).
    stalled_runs = [v for v in liveness if _verdict_word(v) == _STALLED]
    spinning_runs = [v for v in liveness if _verdict_word(v) == _SPINNING]
    if stalled_runs:
        severity = URGENT
        for v in stalled_runs:
            reason = str(getattr(v, "reason", "") or "").strip()
            tail = f" — {reason}" if reason else ""
            lines.append(f"STALLED: run {_run_label(v)} not moving{tail}")
    for v in spinning_runs:
        # SPINNING is a softer "alive but not progressing" — WARN, never demotes URGENT.
        if severity == INFO:
            severity = WARN
        lines.append(f"SPINNING: run {_run_label(v)} alive but not progressing")

    # 2. Unread HUMAN decisions — the absent-operator's-proxy. A pending queue is WARN
    #    (it needs a person, but not the NOW urgency of a hung run).
    human = list(decisions)
    if human:
        if severity == INFO:
            severity = WARN
        n = len(human)
        head = f"UNREAD: {n} decision{'s' if n != 1 else ''} need a human"
        phrases = "; ".join(_decision_phrase(d) for d in human[:5])
        more = "" if n <= 5 else f"; +{n - 5} more"
        lines.append(f"{head} ({phrases}{more})")

    # 3. An OPEN breaker — a failure class has tripped to an escalation. WARN.
    breaker_open = _breaker_is_open(breaker_verdict)
    if breaker_open:
        if severity == INFO:
            severity = WARN
        esc = str(getattr(getattr(breaker_verdict, "escalation", None), "value", "") or "").upper()
        tail = f" → {esc}" if esc else ""
        lines.append(f"breaker OPEN{tail}")

    # 4. ENFORCEMENT OVER-BLOCKING — the docs/365 PEP-feedback observe leg. The fold
    #    of the OP_ENFORCE journal (`enforce_outcomes.outcome_metric`) counts denies
    #    the operator LATER OVERRODE — the ground-truth false-DENYs. A standing count
    #    (>= threshold) means the enforcement policy is refusing edits the workspace
    #    genuinely makes, and a `dos enforce-tune` run is warranted. WARN (it is a
    #    standing-pattern signal a person should look at, not the NOW urgency of a hung
    #    run) — the continuous-OBSERVE half of the autonomous tuning loop: pulse
    #    SURFACES the over-blocking; the autonomous `enforce-tune` cadence ACTS on it.
    false_denies = _enforce_false_denies(enforce_metric)
    if false_denies >= max(1, enforce_threshold):
        if severity == INFO:
            severity = WARN
        lines.append(
            f"ENFORCEMENT over-blocking: {false_denies} deny(ies) the operator later "
            f"overrode — the policy is refusing legitimate edits; run `dos enforce-tune` "
            f"(docs/365)")

    empty = not lines
    return PulseDigest(
        empty=empty,
        severity=INFO if empty else severity,
        lines=tuple(lines),
        stalled=len(stalled_runs),
        pending_human=len(human),
        breaker_open=breaker_open,
        enforce_false_denies=false_denies,
    )


def render_pulse_text(digest: PulseDigest) -> str:
    """The plain-text body a notifier with no rich surface shows in full. PURE.

    Mirrors `decisions.render_list_plain` / `dispatch_top.render_frame_text`: the
    caller passes this into `notification_for_pulse` as the prebuilt `summary` so the
    notify adapter needs no rendering of its own. An empty digest renders the
    all-clear line (used only by the verb's non-silent surfaces; the cron path emits
    nothing on empty).
    """
    if digest.empty:
        return "fleet clear — nothing the pulse needs to surface"
    return "\n".join(digest.lines)
