"""tool_trace — the per-call observability spine (docs/361).

DOS observes tool calls through four byte-clean logs, each folding to *counts*:

  * `hook_observation`  — what the kernel DECIDED per call (pass / warn / deny),
                          the docs/297 denominator. Keys: `run_id` + `ts`.
  * `posttool_sensor`   — the ordered tool-result STREAM (ADVANCING/REPEATING/
                          STALLED). Keys: `run_id` + `step_index` (the spine).
  * `model_call`        — per-MODEL-CALL latency + spend. Keys: `run_id` + `ts`.
  * `verdict_journal`   — the kernel's own verdict stream (read by `observe`).

The gap docs/361 names: nothing joins these at the TOOL-CALL grain. Five count
projections over four disjoint logs, none cross-referencing another. The one log
that captures the ORDERED tool stream — the posttool accumulator — has a writer
but no operator-facing reader. The operator question with no verb today:

    "What tool calls happened in run R, in order, and what did DOS do about each
     one — pass / warn / deny, stalled or advancing, and what did it cost?"

This module answers it. It is a **read-only projection** (the `observe` /
`decisions` / `trace` posture): it folds logs other syscalls already wrote, mints
no new belief, takes no lease, adjudicates nothing. Delete it and you lose the
reader, not the data.

Why a projection, not a verdict (the docs/361 §2 decision)
==========================================================

The tempting move — a `commit_audit` analogue that adjudicates "what the agent
NARRATED about a tool result vs the env-authored result bytes" — was refuted on
three independent lenses (docs/361 §2): `effect_witness`/`attest` already own the
claim-vs-witness join; the narration operand is the §5a satisfaction predicate
the kernel forbids; and the two operands never co-exist at one hook event. The
scattered surfaces were never missing a verdict — they were missing a SPINE.

So the typed states here are **join-completeness**, never a satisfaction verdict:

  JOINED  — the call appears in ≥2 logs that reconcile on the shared key.
  PARTIAL — present in one log only (an observation with no matching stream step).
  ORPHAN  — a record whose key matches no call in any other log. SURFACED, never
            dropped: the operator needs to see the seam, not have it hidden.

There is deliberately NO CORROBORATED/CONTRADICTED. A row may *display* an
`effect_verdict` when `effect_witness` already produced one; this module never
DERIVES it (consume `witness_effect`, never re-derive it).

Why it is byte-clean (docs/138, inherited)
==========================================

The projection adjudicates nothing; it folds env/runtime-authored records each
written downstream of an already-decided verdict:

  * `outcome`/`exit`/`latency_ms`/`rung`/`reason_class`/`stream_state` — hook-
    authored (the sensor wrote them AFTER the verdict was decided).
  * `result_digest` — env-authored (the gym/MCP server produced the bytes).
  * `model`/tokens/`duration_ms` — provider-authored (the API billed them).

The two AGENT-authored fields a row carries — `tool_name`, `args_digest` — are
the docs/145 provenance split made visible: DISPLAYED as proposals, never
adjudicated on. No agent prose enters any count.

The like-for-like rule (docs/297), honored
===========================================

It must NOT compute a single blended rate across logs (their windows and scopes
differ). When two records can't be joined, the row is `PARTIAL`/`ORPHAN` with
per-log provenance, never a silent merge. The join is a *correlation* explicitly
labelled by `join_grain`, not a unification that papers over the four scopes.

The join key (the persistence nuance docs/361 verified)
=======================================================

The spine is the posttool stream RECORD, which carries `run_id` + `step_index`
(the docs/179 firing-join fields, written by `posttool_sensor._step_entry`). NOTE
the verified nuance: those fields are on the persisted RECORD, not on the
in-memory `StreamStep` dataclass, and `posttool_sensor.read_stream` reconstructs
bare `StreamStep`s that DROP them. So this fold reads RAW records (the CLI hands
in `read_observations()` / raw stream records / `read_model_calls()`); it never
relies on `read_stream`'s lossy reconstruction. No new write-side field: the join
fields are already on disk.

⚓ Kernel discipline (the litmus): PURE Layer-1 fold — it imports only stdlib (no
sibling kernel module is needed; it folds plain dicts the boundary read). It
names no host, no driver, no vendor; reads no clock, no disk, no network inside
the fold. The CLI boundary (`cmd_tool_trace`) does the four `read_*` calls and
hands the records in — the `intervention_rate(records) -> value` posture, one
rung up to a per-call join.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# The typed join-completeness state — three values, mutually exclusive.
# ---------------------------------------------------------------------------
class JoinState(str, enum.Enum):
    """How completely a tool call's record was joined across the logs.

    `str`-valued so it round-trips through a CLI token / JSON without a lookup
    table (the `StreamState` / `Liveness` posture). NOT a satisfaction verdict —
    it reports join completeness, never whether the agent succeeded (docs/361 §2).

      JOINED  — the spine step reconciled with ≥1 other log (an observation
                and/or a model call on the same `run_id` within the `ts` window).
                The full per-call view: decision + stall + spend on one row.
      PARTIAL — a spine step with NO other-log match (the stream recorded the
                call but no observation/model-call reconciled to it), OR an
                observation/model-call that matched a spine step but the spine
                step itself carried no cross-log siblings. The honest "we have
                some of this call, not all of it".
      ORPHAN  — a record in one log whose key matches NO call in any other log
                AND which is not a spine step (a stray observation or model call
                with an unmatched `run_id`). Surfaced as its own row so the seam
                is visible — never silently dropped.
    """

    JOINED = "JOINED"
    PARTIAL = "PARTIAL"
    ORPHAN = "ORPHAN"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ---------------------------------------------------------------------------
# The policy — the only knob is the cross-log ts reconciliation window.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TracePolicy:
    """The reconciliation policy — mechanism is the fold, this is the one knob.

    `ts_window_s` is how far apart (seconds) an observation / model-call `ts` may
    be from a spine step's `ts` and still reconcile to it, WHEN `step_index`
    cannot disambiguate (the logs share `run_id` but the observation log carries
    no `step_index` today). Default 0 means "reconcile on `run_id` only, nearest
    `ts` wins" — the conservative join that never reaches across runs. A positive
    window is advisory tightening, not loosening: it can only REJECT a far-apart
    match, never invent one across `run_id`s.

    The join NEVER crosses `run_id`: two records with different `run_id`s are
    never the same call, whatever their `ts`. That is the hard floor; the window
    only refines within one `run_id`.
    """

    ts_window_s: float = 0.0

    def __post_init__(self) -> None:
        if self.ts_window_s < 0:
            raise ValueError("ts_window_s must be non-negative")


DEFAULT_POLICY = TracePolicy()


# ---------------------------------------------------------------------------
# Frozen outputs — one row per tool call + the join report.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CallRow:
    """One tool call, joined across the logs — the per-call observability row.

    The spine fields (`step_index`/`tool_name`/`args_digest`/`result_digest`)
    come from the posttool stream record; `outcome`/`exit`/`latency_ms`/`rung`/
    `reason_class` from the matched hook-observation; `stall_state` from the
    stream record's `verdict_state` (the firing) or the observation's
    `stream_state`; `model`/`model_spend_tokens`/`model_duration_ms` from the
    matched model call; `effect_verdict` only when a host persisted one (DISPLAYED,
    never derived). `state` is the typed join-completeness. Every field is
    env/runtime-authored except `tool_name`/`args_digest` (agent proposals,
    surfaced not scored — docs/145).
    """

    state: JoinState
    run_id: str = ""
    step_index: Optional[int] = None
    ts: str = ""
    # spine (posttool stream record)
    tool_name: str = ""
    args_digest: str = ""
    result_digest: Optional[str] = None
    stall_state: str = ""
    # decision (hook_observation)
    outcome: str = ""
    exit: Optional[int] = None
    latency_ms: Optional[float] = None
    rung: str = ""
    reason_class: str = ""
    # spend (model_call)
    model: str = ""
    model_duration_ms: Optional[float] = None
    model_spend_tokens: Optional[int] = None
    # consumed verdict (effect_witness), never derived here
    effect_verdict: str = ""
    # which logs contributed to this row (provenance, the honesty floor)
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = {
            "state": self.state.value,
            "run_id": self.run_id,
            "step_index": self.step_index,
            "ts": self.ts,
            "tool_name": self.tool_name,
            "args_digest": self.args_digest,
            "result_digest": self.result_digest,
            "stall_state": self.stall_state,
            "outcome": self.outcome,
            "exit": self.exit,
            "latency_ms": self.latency_ms,
            "rung": self.rung,
            "reason_class": self.reason_class,
            "model": self.model,
            "model_duration_ms": self.model_duration_ms,
            "model_spend_tokens": self.model_spend_tokens,
            "effect_verdict": self.effect_verdict,
            "sources": list(self.sources),
        }
        return d


@dataclass(frozen=True)
class JoinReport:
    """The fold's own honesty receipt — how the join went, never hidden.

    `joined`/`partial`/`orphan` count the rows by state; `by_log_unmatched` names
    how many records of each log never reconciled to a spine step (the seam made
    a number). `join_grain` states the key the correlation used. A reader prints
    this so the operator knows the join is a correlation, not a unification.
    """

    joined: int = 0
    partial: int = 0
    orphan: int = 0
    by_log_unmatched: dict = field(default_factory=dict)
    join_grain: str = "run_id+step_index (spine); run_id+ts for observations/model-calls"

    def to_dict(self) -> dict:
        return {
            "joined": self.joined,
            "partial": self.partial,
            "orphan": self.orphan,
            "by_log_unmatched": dict(self.by_log_unmatched),
            "join_grain": self.join_grain,
        }


@dataclass(frozen=True)
class ToolTrace:
    """The ordered per-call replay + the join report. The render surface.

    `rows` is ordered by (`run_id`, `step_index`, `ts`) — the live "what happened
    in order" question. `report` is the join honesty receipt. `run`/`session`
    echo the active filters. `to_dict` is the `--json` shape.
    """

    rows: tuple[CallRow, ...] = ()
    report: JoinReport = field(default_factory=JoinReport)
    run: str = ""
    session: str = ""

    def to_dict(self) -> dict:
        return {
            "run": self.run,
            "session": self.session,
            "report": self.report.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
        }


# ---------------------------------------------------------------------------
# Small pure helpers — provenance reads off plain dicts (the boundary read).
# ---------------------------------------------------------------------------
def _s(rec: dict, key: str) -> str:
    """A record field as a stripped string ("" when absent/None). PURE."""
    v = rec.get(key)
    return str(v).strip() if v is not None else ""


def _spend_tokens(rec: dict) -> Optional[int]:
    """Total billed tokens flattened onto a model-call record, or None. PURE.

    Sums the disjoint `model_call` spend fields (input/output/cache_read/
    cache_creation) the writer flattened. `reasoning` is a SUB-count of `output`
    (the `SpendBreakdown` canonical form), so it is NOT added — adding it would
    double-count. Returns None when no spend field is present (an honest "not
    recorded"), 0 only when a field is present and zero.
    """
    fields = ("input", "output", "cache_read", "cache_creation")
    if not any(f in rec for f in fields):
        return None
    total = 0
    for f in fields:
        try:
            total += int(rec.get(f) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _ts_delta_s(a: str, b: str) -> Optional[float]:
    """Absolute seconds between two ISO-8601 `Z` stamps, or None if unparseable.

    PURE — parses the journal `%Y-%m-%dT%H:%M:%SZ` grammar with no clock read.
    Returns None when either stamp is empty/malformed (the fail-safe: an
    unparseable ts can't tighten a window, so the `run_id`-only join stands).
    """
    import datetime as _dt

    def _parse(s: str):
        if not s:
            return None
        try:
            return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.timezone.utc
            )
        except (ValueError, TypeError):
            return None

    pa, pb = _parse(a), _parse(b)
    if pa is None or pb is None:
        return None
    return abs((pa - pb).total_seconds())


# ---------------------------------------------------------------------------
# The pure fold — records in, a ToolTrace out, no disk.
# ---------------------------------------------------------------------------
def correlate_calls(
    observations: Iterable[dict],
    stream_records: Iterable[dict],
    model_calls: Iterable[dict],
    effect_verdicts: Iterable[dict] = (),
    *,
    run_id: str = "",
    session_id: str = "",
    policy: TracePolicy = DEFAULT_POLICY,
) -> ToolTrace:
    """Join the four tool logs into one ordered per-call replay. PURE — no disk.

    The spine is `stream_records` (the posttool stream RECORDS, which carry
    `run_id` + `step_index`; NOT the lossy `read_stream` reconstruction). Each
    spine step becomes one `CallRow`; the fold reconciles a hook-observation and
    a model-call to it on `run_id` (+ nearest `ts` within `policy.ts_window_s`
    when set). A spine step with siblings is JOINED; without any is PARTIAL. An
    observation or model-call that reconciled to NO spine step is surfaced as its
    own ORPHAN row (never dropped). `effect_verdicts` (optional) are matched on
    `run_id` + `step_index` and DISPLAYED, never derived.

    `run_id` filters every log to one run before folding (the `observe --run`
    join key); `session_id` echoes through to the result for the title. The join
    NEVER crosses `run_id` (docs/297 like-for-like; two run_ids are never one
    call). Records in, value out — the unit-test surface.
    """
    obs = [r for r in observations if isinstance(r, dict)]
    steps = [r for r in stream_records if isinstance(r, dict)]
    mcs = [r for r in model_calls if isinstance(r, dict)]
    evs = [r for r in effect_verdicts if isinstance(r, dict)]

    if run_id:
        obs = [r for r in obs if _s(r, "run_id") == run_id]
        steps = [r for r in steps if _s(r, "run_id") == run_id]
        mcs = [r for r in mcs if _s(r, "run_id") == run_id]
        evs = [r for r in evs if _s(r, "run_id") == run_id]

    # Only pretool/posttool observations are about a tool CALL; a stop/marker/
    # session-start observation is not a per-call admission (the
    # `intervention_rate` denominator discipline).
    obs = [r for r in obs if _s(r, "verb") in ("pretool", "posttool")]

    # effect verdicts indexed by (run_id, step_index) for O(1) display lookup.
    ev_by_key: dict[tuple[str, Optional[int]], str] = {}
    for r in evs:
        si = r.get("step_index")
        key = (_s(r, "run_id"), int(si) if isinstance(si, int) else None)
        verdict = _s(r, "verdict") or _s(r, "state") or _s(r, "effect_verdict")
        if verdict:
            ev_by_key[key] = verdict

    used_obs: set[int] = set()
    used_mc: set[int] = set()
    rows: list[CallRow] = []

    def _best_match(step: dict, pool: list[dict], used: set[int]) -> Optional[int]:
        """Index of the nearest unused same-run record in `pool`, or None. PURE.

        Same `run_id` is mandatory (the hard floor). Among same-run unused
        candidates, the nearest `ts` wins; when `policy.ts_window_s` is set, a
        candidate farther than the window is rejected (can only tighten). When no
        `ts` is parseable, the first same-run unused candidate matches (the
        `run_id`-only conservative join). A pool with no `run_id` on its records
        can still match a spine step that ALSO has no `run_id` only if both are
        empty — but an empty-run spine never reconciles across the window, so the
        run scoping above (when `run_id` is set) is what makes this safe.
        """
        srun = _s(step, "run_id")
        sts = _s(step, "ts")
        best_i: Optional[int] = None
        best_delta: Optional[float] = None
        for i, cand in enumerate(pool):
            if i in used:
                continue
            if _s(cand, "run_id") != srun:
                continue
            delta = _ts_delta_s(sts, _s(cand, "ts"))
            if policy.ts_window_s and delta is not None and delta > policy.ts_window_s:
                continue
            if delta is None:
                # No comparable ts — take it (run_id matched) unless a better
                # ts-comparable candidate already won.
                if best_i is None:
                    best_i = i
                continue
            if best_delta is None or delta < best_delta:
                best_delta, best_i = delta, i
        return best_i

    for step in steps:
        srun = _s(step, "run_id")
        si_raw = step.get("step_index")
        si = int(si_raw) if isinstance(si_raw, int) else None

        oi = _best_match(step, obs, used_obs)
        mi = _best_match(step, mcs, used_mc)
        o = obs[oi] if oi is not None else {}
        m = mcs[mi] if mi is not None else {}
        if oi is not None:
            used_obs.add(oi)
        if mi is not None:
            used_mc.add(mi)

        sources = ["stream"]
        if o:
            sources.append("observation")
        if m:
            sources.append("model_call")

        stall = _s(step, "verdict_state") or _s(o, "stream_state")
        state = JoinState.JOINED if (o or m) else JoinState.PARTIAL
        rows.append(
            CallRow(
                state=state,
                run_id=srun,
                step_index=si,
                ts=_s(step, "ts") or _s(o, "ts") or _s(m, "ts"),
                tool_name=_s(step, "tool_name"),
                args_digest=_s(step, "args_digest"),
                result_digest=(step.get("result_digest")
                               if step.get("result_digest") is not None else None),
                stall_state=stall,
                outcome=_s(o, "outcome"),
                exit=(int(o["exit"]) if isinstance(o.get("exit"), int) else None),
                latency_ms=(float(o["latency_ms"])
                            if isinstance(o.get("latency_ms"), (int, float)) else None),
                rung=_s(o, "rung"),
                reason_class=_s(o, "reason_class"),
                model=_s(m, "model"),
                model_duration_ms=(float(m["duration_ms"])
                                   if isinstance(m.get("duration_ms"), (int, float))
                                   else None),
                model_spend_tokens=_spend_tokens(m) if m else None,
                effect_verdict=ev_by_key.get((srun, si), ""),
                sources=tuple(sources),
            )
        )

    # Surface the unmatched observations + model calls as ORPHAN rows — the seam,
    # never dropped (docs/361 §4).
    for i, o in enumerate(obs):
        if i in used_obs:
            continue
        rows.append(
            CallRow(
                state=JoinState.ORPHAN,
                run_id=_s(o, "run_id"),
                ts=_s(o, "ts"),
                tool_name=_s(o, "verb"),  # an orphan observation has no tool name
                stall_state=_s(o, "stream_state"),
                outcome=_s(o, "outcome"),
                exit=(int(o["exit"]) if isinstance(o.get("exit"), int) else None),
                latency_ms=(float(o["latency_ms"])
                            if isinstance(o.get("latency_ms"), (int, float)) else None),
                rung=_s(o, "rung"),
                reason_class=_s(o, "reason_class"),
                sources=("observation",),
            )
        )
    for i, m in enumerate(mcs):
        if i in used_mc:
            continue
        rows.append(
            CallRow(
                state=JoinState.ORPHAN,
                run_id=_s(m, "run_id"),
                ts=_s(m, "ts"),
                model=_s(m, "model"),
                model_duration_ms=(float(m["duration_ms"])
                                   if isinstance(m.get("duration_ms"), (int, float))
                                   else None),
                model_spend_tokens=_spend_tokens(m),
                sources=("model_call",),
            )
        )

    # Stable order: by run, then step_index (None last), then ts.
    def _order(r: CallRow):
        return (
            r.run_id,
            r.step_index if r.step_index is not None else 1 << 30,
            r.ts,
        )

    rows.sort(key=_order)

    joined = sum(1 for r in rows if r.state is JoinState.JOINED)
    partial = sum(1 for r in rows if r.state is JoinState.PARTIAL)
    orphan = sum(1 for r in rows if r.state is JoinState.ORPHAN)
    report = JoinReport(
        joined=joined,
        partial=partial,
        orphan=orphan,
        by_log_unmatched={
            "observations": sum(1 for i in range(len(obs)) if i not in used_obs),
            "model_calls": sum(1 for i in range(len(mcs)) if i not in used_mc),
        },
    )
    return ToolTrace(
        rows=tuple(rows), report=report, run=run_id, session=session_id
    )


# ---------------------------------------------------------------------------
# The renderer — the ordered per-call replay. PURE (text in / text out).
# ---------------------------------------------------------------------------
def render_text(trace: ToolTrace) -> str:
    """The ordered one-row-per-call replay + the join report. PURE.

    Leads with the title + the honest join report (joined/partial/orphan +
    unmatched-per-log), then one line per call: step, tool, the decision
    (outcome/rung), the stall state, and the model spend — the per-call view that
    has no verb today. An empty trace renders an honest "(no tool calls …)" line.
    """
    out: list[str] = []
    title = "# tool-trace"
    if trace.run:
        title += f" · run {trace.run}"
    if trace.session:
        title += f" · session {trace.session}"
    out.append(title)
    rep = trace.report
    out.append(
        f"  {rep.joined} joined · {rep.partial} partial · {rep.orphan} orphan "
        f"(grain: {rep.join_grain})"
    )
    if not trace.rows:
        out.append("  (no tool calls recorded yet — the stream/observation logs "
                   "fill as the hooks fire)")
        return "\n".join(out)
    unmatched = rep.by_log_unmatched
    if any(unmatched.values()):
        bits = ", ".join(f"{k}={v}" for k, v in unmatched.items() if v)
        out.append(f"  · unmatched (surfaced as orphans): {bits}")
    out.append("")
    header = (f"  {'#':>3} {'tool':<18} {'decision':<14} {'stall':<10} "
              f"{'lat(ms)':>8} {'tokens':>8}  state")
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for r in trace.rows:
        si = str(r.step_index) if r.step_index is not None else "-"
        decision = (f"{r.outcome}/{r.rung}" if r.rung else r.outcome) or "-"
        lat = f"{r.latency_ms:.0f}" if r.latency_ms is not None else "-"
        tok = str(r.model_spend_tokens) if r.model_spend_tokens is not None else "-"
        tool = (r.tool_name or "-")[:18]
        ev = f"  [{r.effect_verdict}]" if r.effect_verdict else ""
        out.append(
            f"  {si:>3} {tool:<18} {decision:<14} {(r.stall_state or '-'):<10} "
            f"{lat:>8} {tok:>8}  {r.state.value}{ev}"
        )
    return "\n".join(out)
