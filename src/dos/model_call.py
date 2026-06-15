"""The `model-call` record family — per-MODEL-CALL timing + spend telemetry.

The kernel already times its own hooks (`hook_observation` — `latency_ms` per
verb) and folds a run's whole token spend (`spend.SpendBreakdown`). Neither
answers the operator's plain question about the *model* itself: **how long did
each model (LLM API) call take, and what did each one cost — broken down by
model id?** `hook_observation` times the kernel's hook, not the provider's
response; `spend` is a per-run aggregate, not a per-call series. This leaf fills
that gap with one schema-tagged JSONL line per model call.

Every model call can append ONE record to the workspace's model-call log
(`.dos/metrics/model_calls.jsonl`): which model answered, the wall-clock
`duration_ms` of the call, an optional `ttft_ms` (time-to-first-token), and the
typed `SpendBreakdown` behind that one call. A PURE fold then rolls those
records into a per-model report — call counts, latency p50/p95/max, and the
summed spend (so per-model `total` tokens and `cache_hit_ratio` come for free
from the existing `SpendBreakdown` properties).

This is the `hook_observation` contract pattern applied to model calls — a PURE
builder + a fail-soft boundary writer + a tolerant boundary reader + a PURE
fold — so it slots in as a Layer-1 kernel leaf:

* **Pure where it can be.** `model_call_entry()` and `roll_up()` are data-in /
  data-out, no disk — the unit-test surface. Only `append()` and
  `read_model_calls()` touch the file, at the boundary.
* **FAIL-SOFT, ADVISORY (docs/99).** Telemetry about a call is strictly
  downstream of the call: `append()` never raises, so a write fault can never
  change a verdict or an exit code. A torn line is "didn't happen," never a
  corruption that derails a read. This leaf adjudicates nothing and takes no
  lease — it records and reports.
* **Byte-clean (docs/138).** Every counted field is env/provider-authored: the
  `duration_ms` the runtime measured, the model id the provider returned, the
  token counts the API billed. No agent narration enters the timing or the
  spend — a run can no more inflate its own cache-hit ratio than shrink its
  billed latency.
* **Vendor-agnostic.** The leaf names no model and no provider in code; the
  `model` field is whatever string the caller's boundary recorded. Which
  provider uses which usage-record shape is `spend`'s concern, normalized once
  before a `SpendBreakdown` reaches here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from dos import durable_schema as _ds
from dos.spend import SpendBreakdown

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dos import config as _config


# ---------------------------------------------------------------------------
# The contract constants — one family, one version, one path.
# ---------------------------------------------------------------------------

# The schema family every writer tags and every reader gates on (durable_schema).
SCHEMA_FAMILY = "model-call"
SCHEMA_VERSION = 1

# Every record's `op` — the log records a call, it never decides.
OP_MODELCALL = "MODELCALL"

# The log location under the workspace's `.dos/` home: a sibling of
# `observations.jsonl` (`.dos/metrics/model_calls.jsonl`).
METRICS_DIRNAME = "metrics"
LOG_BASENAME = "model_calls.jsonl"

# The durable-append opt-out, the `hook_observation` posture: unset or
# anything-but-"0" = on; "0" = off.
_METRICS_ENV = "DOS_MODEL_CALL_METRICS"

# The spend fields flattened onto a record (a sub-count `reasoning` rides along).
# One source of truth for the writer (flatten) and the reader (re-hydrate).
_SPEND_FIELDS = ("input", "output", "cache_read", "cache_creation", "reasoning")


def model_calls_path(cfg: "Optional[_config.SubstrateConfig]" = None) -> Path:
    """The workspace's model-call log path. PURE path arithmetic.

    Rides `cfg.paths.dot_dos` (the per-project `.dos/` home), the
    `observations_path` idiom. Never creates anything — `append` is the only
    creator (the read-only-path discipline)."""
    from dos import config as _config_mod

    cfg = _config_mod.ensure(cfg)
    return cfg.paths.dot_dos / METRICS_DIRNAME / LOG_BASENAME


def metrics_enabled(*, debug: bool = False) -> bool:
    """True iff the durable append should run — on by default,
    `DOS_MODEL_CALL_METRICS=0` opts out, `--debug` always logs (the same gate
    the other writers honor)."""
    if debug:
        return True
    return os.environ.get(_METRICS_ENV, "").strip() != "0"


def _now_iso() -> str:
    """Second-resolution UTC ISO-8601 with a `Z` — the journal `ts` grammar."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The write side — a PURE entry builder + a fail-soft boundary append.
# ---------------------------------------------------------------------------


def model_call_entry(
    model: str,
    duration_ms: float,
    *,
    ttft_ms: float = 0.0,
    spend: Optional[SpendBreakdown] = None,
    ts: str = "",
    run_id: str = "",
) -> dict:
    """One schema-tagged model-call record — the PURE builder (`observation_entry`
    posture).

    Only `model` + `duration_ms` are always present; every other field is
    written ONLY when set, so a bare record stays small and the schema version
    never bumps for an absent field (the additive contract). When a `spend`
    breakdown is given its five disjoint counts are flattened onto the record
    (so the reader can re-hydrate a `SpendBreakdown` without a nested object).
    An empty `model` raises — a writer that stamps an unnamed call is a kernel
    bug, not silent data (the `observation_entry` rule). `ts` may be left empty
    for `append` to stamp at write time.
    """
    if not model:
        raise ValueError("a model call must name its model")
    if duration_ms < 0:
        raise ValueError("duration_ms must be non-negative")
    if ttft_ms < 0:
        raise ValueError("ttft_ms must be non-negative")
    e: dict = {
        **_ds.tag(SCHEMA_FAMILY, SCHEMA_VERSION),
        "op": OP_MODELCALL,
        "model": model,
        "duration_ms": float(duration_ms),
    }
    if ttft_ms:
        e["ttft_ms"] = float(ttft_ms)
    if ts:
        e["ts"] = ts
    if run_id:
        e["run_id"] = run_id
    if spend is not None:
        for name in _SPEND_FIELDS:
            value = getattr(spend, name)
            if value:
                e[name] = int(value)
    return e


def append(
    entry: dict,
    *,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    path: Optional[Path] = None,
    debug: bool = False,
) -> bool:
    """Append one model-call record to the workspace log — best-effort, FAIL-SOFT.

    Stamps `ts` if absent, writes one canonical-JSON line (the
    `hook_observation.append` grammar: sorted keys, `ensure_ascii=False`),
    creates the directory on first use, and `fsync`s so a recorded call
    outlives the process that wrote it. Gated by `metrics_enabled` (the
    `DOS_MODEL_CALL_METRICS` opt-out; `--debug` always logs).

    NEVER raises: the call has already happened, and telemetry about it must not
    be able to alter anything downstream (docs/99). Returns True iff a line was
    durably written — a test affordance, not a contract a hot path reads.
    """
    try:
        if not metrics_enabled(debug=debug):
            return False
        p = path or model_calls_path(cfg)
        e = dict(entry)
        e.setdefault("ts", _now_iso())
        line = json.dumps(e, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 — a telemetry write fault never alters anything
        return False


# ---------------------------------------------------------------------------
# The read side — a tolerant boundary read.
# ---------------------------------------------------------------------------


def read_model_calls(path: Optional[Path] = None,
                     cfg: "Optional[_config.SubstrateConfig]" = None) -> tuple[dict, ...]:
    """Every soundly-readable model-call record in the log, in file order.

    Tolerant the way every JSONL fold here is (via `_decode_model_call`): a
    blank/torn/corrupt line is skipped ("didn't happen"); a record tagged for a
    different family, tagged at a version this kernel predates (refuse-don't-
    guess, `durable_schema`), untagged, or whose `op` is not MODELCALL is
    skipped. A missing or unreadable file degrades to () — a read-only surface
    shows what it has, never an error.
    """
    p = path or model_calls_path(cfg)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    out: list[dict] = []
    for line in text.splitlines():
        rec = _decode_model_call(line)
        if rec is not None:
            out.append(rec)
    return tuple(out)


def _decode_model_call(line: str) -> Optional[dict]:
    """One log line → a soundly-readable MODELCALL record, or None. PURE.

    The single home of the tolerance contract: a blank or torn line, a record
    tagged for a different family / a version this kernel predates (refuse-don't-
    guess, `durable_schema`), an untagged record, a non-dict, or one whose `op`
    is not MODELCALL all return None ("didn't happen").
    """
    s = line.strip()
    if not s:
        return None
    try:
        rec = json.loads(s)
    except (ValueError, TypeError):
        return None
    if not isinstance(rec, dict):
        return None
    verdict = _ds.classify(rec, family=SCHEMA_FAMILY, understands=SCHEMA_VERSION)
    if not verdict.readability.is_soundly_readable:
        return None
    if rec.get("op") != OP_MODELCALL:
        return None
    return rec


# ---------------------------------------------------------------------------
# The PURE roll-up fold — records in, a per-model report out, no disk.
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile over an already-sorted list. PURE.

    `q` in [0, 1]. Empty list → 0.0. The same nearest-rank method `dos-hook
    stats` uses for hook latency, kept local (a few lines, not worth a shared
    util). The rank is `ceil(q * n)` clamped to [1, n], 1-indexed — so p50 of
    an even-length list takes the upper-middle element (a conservative,
    deterministic choice; no interpolation).
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    import math

    rank = math.ceil(q * n)
    idx = min(max(rank, 1), n) - 1
    return sorted_values[idx]


@dataclass(frozen=True)
class LatencyStat:
    """The folded latency distribution for one series (duration or ttft). PURE value.

    `n` is how many records contributed a value (ttft is optional, so its `n`
    can be below the model's call count). `mean`/`p50`/`p95`/`max` are
    milliseconds over those values. All zero on an empty series — a model with
    no recorded ttft renders a zero row, never a missing one (honest zeros, the
    `HeadlineSummary` posture).
    """

    n: int = 0
    mean: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    max: float = 0.0

    @classmethod
    def of(cls, values: Iterable[float]) -> "LatencyStat":
        vals = sorted(float(v) for v in values)
        n = len(vals)
        if n == 0:
            return cls()
        return cls(
            n=n,
            mean=sum(vals) / n,
            p50=_percentile(vals, 0.50),
            p95=_percentile(vals, 0.95),
            max=vals[-1],
        )

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": round(self.mean, 3),
            "p50": round(self.p50, 3),
            "p95": round(self.p95, 3),
            "max": round(self.max, 3),
        }


@dataclass(frozen=True)
class ModelStat:
    """One model's roll — call count, latency distributions, summed spend. PURE value.

    `calls` is how many records named this model. `duration`/`ttft` are the two
    `LatencyStat` distributions. `spend` is the summed `SpendBreakdown` across
    every call (so `spend.total`, `spend.cache_hit_ratio`, `spend.output_share`
    come for free from the existing properties — no new arithmetic). Every count
    is env/provider-authored; none is narration.
    """

    model: str
    calls: int = 0
    duration: LatencyStat = field(default_factory=LatencyStat)
    ttft: LatencyStat = field(default_factory=LatencyStat)
    spend: SpendBreakdown = field(default_factory=SpendBreakdown)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "duration_ms": self.duration.to_dict(),
            "ttft_ms": self.ttft.to_dict(),
            "spend": self.spend.to_dict(),
        }


@dataclass(frozen=True)
class ModelCallRoll:
    """The whole model-call log folded into per-model stats + a fleet total. PURE value.

    `models` is the per-model roll, sorted by call count descending then model
    name (a stable, operator-useful order — the busiest model first). `total` is
    the roll across ALL models (one `ModelStat` keyed `"(all)"`). `since` echoes
    the window. `to_dict` is the `--json` shape.
    """

    models: tuple[ModelStat, ...] = ()
    total: ModelStat = field(default_factory=lambda: ModelStat(model="(all)"))
    since: str = ""

    @property
    def call_count(self) -> int:
        return self.total.calls

    def to_dict(self) -> dict:
        return {
            "since": self.since,
            "call_count": self.call_count,
            "total": self.total.to_dict(),
            "models": [m.to_dict() for m in self.models],
        }


def _spend_of(rec: dict) -> SpendBreakdown:
    """Re-hydrate the `SpendBreakdown` flattened onto one record. PURE.

    The mirror of the `model_call_entry` flatten: an absent field is 0 (the
    builder omits zero counts). A malformed (non-int / negative) count is the
    `SpendBreakdown` constructor's loud error, not silently mended — a tolerant
    READ of a record's READABILITY is one thing; reconstructing a spend from
    garbage counts is the double-count bug class `spend` exists to refuse. The
    reader already dropped unreadable lines, so a record reaching here is
    well-formed JSON; a count outside the contract is a real fault.
    """
    return SpendBreakdown(
        input=int(rec.get("input") or 0),
        output=int(rec.get("output") or 0),
        cache_read=int(rec.get("cache_read") or 0),
        cache_creation=int(rec.get("cache_creation") or 0),
        reasoning=int(rec.get("reasoning") or 0),
    )


def _sum_spend(breakdowns: Iterable[SpendBreakdown]) -> SpendBreakdown:
    """Sum N disjoint `SpendBreakdown`s field-wise. PURE (the canonical form is
    additive, so the sum stays disjoint and `reasoning <= output` is preserved)."""
    inp = out = cr = cc = rsn = 0
    for b in breakdowns:
        inp += b.input
        out += b.output
        cr += b.cache_read
        cc += b.cache_creation
        rsn += b.reasoning
    return SpendBreakdown(
        input=inp, output=out, cache_read=cr, cache_creation=cc, reasoning=rsn
    )


def _stat_for(model: str, recs: list[dict]) -> ModelStat:
    """Fold one model's records into a `ModelStat`. PURE."""
    durations = [float(r.get("duration_ms") or 0.0) for r in recs]
    ttfts = [float(r["ttft_ms"]) for r in recs if "ttft_ms" in r]
    spend = _sum_spend(_spend_of(r) for r in recs)
    return ModelStat(
        model=model,
        calls=len(recs),
        duration=LatencyStat.of(durations),
        ttft=LatencyStat.of(ttfts),
        spend=spend,
    )


def roll_up(records: Iterable[dict], *, since: str = "") -> ModelCallRoll:
    """Fold model-call records into the per-model roll-up report. PURE — no disk.

    Groups by `model` and, per model, folds call count + latency distributions
    (`duration_ms`, and `ttft_ms` where present) + the summed `SpendBreakdown`.
    `since` keeps only records with `ts >= since` (ISO-8601 sorts lexically);
    when a window is set, a record with no `ts` is skipped — a windowed fold
    must not count an undatable record (the `intervention_rate` conservative
    direction). The result's `models` are sorted busiest-first.

    Records in, value out, no disk — the unit-test surface.
    """
    by_model: dict[str, list[dict]] = {}
    kept: list[dict] = []
    for rec in records:
        ts = str(rec.get("ts") or "")
        if since and (not ts or ts < since):
            continue
        model = str(rec.get("model") or "")
        if not model:
            continue
        by_model.setdefault(model, []).append(rec)
        kept.append(rec)

    stats = [_stat_for(model, recs) for model, recs in by_model.items()]
    # Busiest model first, ties broken by name for a stable order.
    stats.sort(key=lambda s: (-s.calls, s.model))

    total = _stat_for("(all)", kept)
    return ModelCallRoll(models=tuple(stats), total=total, since=since)


# ---------------------------------------------------------------------------
# The renderer — the operator-facing table. PURE (text in / text out).
# ---------------------------------------------------------------------------


def render_roll_text(roll: ModelCallRoll) -> str:
    """The per-model latency + spend table, the `dos-hook stats` posture. PURE.

    An empty roll renders the honest "(no model calls recorded yet …)" line — a
    read-only surface shows what it has, never an error. Otherwise: a header
    line with the windowed call count, then one row per model (busiest first)
    with its p50/p95/max duration, total tokens, and cache-hit share, then the
    `(all)` total row.
    """
    window = f" since {roll.since}" if roll.since else ""
    if roll.call_count == 0:
        return (f"dos model-calls{window}\n"
                f"  (no model calls recorded yet — the log fills as the runtime "
                f"records each call)")
    out: list[str] = [f"dos model-calls{window} — {roll.call_count} call(s)"]
    out.append(
        f"  {'model':<28} {'calls':>6} {'p50ms':>9} {'p95ms':>9} "
        f"{'maxms':>9} {'tokens':>10} {'cache%':>7}"
    )

    def _row(s: ModelStat) -> str:
        cache_pct = s.spend.cache_hit_ratio * 100.0
        return (
            f"  {s.model[:28]:<28} {s.calls:>6} {s.duration.p50:>9.1f} "
            f"{s.duration.p95:>9.1f} {s.duration.max:>9.1f} "
            f"{s.spend.total:>10} {cache_pct:>6.1f}%"
        )

    for s in roll.models:
        out.append(_row(s))
    out.append("  " + "-" * 80)
    out.append(_row(roll.total))
    return "\n".join(out)
