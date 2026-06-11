"""The `hook-observation` record family — the kernel-owned per-call telemetry contract (docs/297).

Every hook invocation can append ONE schema-tagged JSONL line to the workspace's
observation log (`.dos/metrics/observations.jsonl`): which verb fired, what it
decided, how long it took. That log is the only surface that knows the
**denominator** — how many tool calls the substrate adjudicated at all — so it is
what turns the absolute "DOS caught N things" count into the rate an operator
actually wants ("light touch or nanny?", issue #24).

The family was born on the plugin's Go binary (docs/276 Part 2). THIS module is
the docs/297 Option-B move: the kernel takes ownership of the contract — the
schema constants, the entry builder, the fail-soft writer, the tolerant reader,
and the pure rate fold all live HERE — and every hook runtime that wants its
calls counted (the Go binary, the Python hook verbs) is a *conforming writer*.
The kernel never knows WHO wrote a record; it reads its own contract. That keeps
the awareness arrow clean: nothing here names a plugin, a vendor, or a binary.

Design rules (the `lane_journal` / `posttool_sensor` postures):

* **Pure where it can be.** `observation_entry()` and `intervention_rate()` are
  data-in / data-out, no disk — the unit-test surface. Only `append()` and
  `read_observations()` touch the file, at the boundary.
* **FAIL-SOFT, ADVISORY (docs/99).** Telemetry about a decision is strictly
  downstream of the decision: `append()` never raises, so a write fault can
  never change an emitted dialect or an exit code. A torn line is "didn't
  happen," never a corruption that derails a read.
* **Byte-clean (docs/138).** Every counted field is env-authored — the hook
  wrote the record downstream of an already-decided verdict. No agent narration
  enters the numerator or the denominator.
* **Like-for-like only (the issue-#24 caveat, made structural).** The rate fold
  takes observation records and nothing else. The lane journal — a different
  log with a different window and scope — has no path into it.

The denominator rule (docs/297): a `delegate` record is a HANDOFF, not an
adjudication — the call's real verdict is (or will be) another record, written
by the runtime that actually decided it. So `adjudicated` excludes delegates,
which is the one rule that counts each call exactly once in all three writer
worlds (binary-only, Python-only, mixed).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from dos import durable_schema as _ds

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dos import config as _config


# ---------------------------------------------------------------------------
# The contract constants — one family, one version, one path.
# ---------------------------------------------------------------------------

# The schema family every writer tags and every reader gates on (durable_schema).
SCHEMA_FAMILY = "hook-observation"
SCHEMA_VERSION = 1

# Every record's `op` — the observation log records, it never decides.
OP_OBSERVE = "OBSERVE"

# The log location under the workspace's `.dos/` home: a sibling of `streams/`
# and `runs/` (`.dos/metrics/observations.jsonl`).
METRICS_DIRNAME = "metrics"
LOG_BASENAME = "observations.jsonl"

# The durable-append opt-out, shared with every conforming writer: unset or
# anything-but-"0" = on; "0" = off. A `--debug` run always logs (a trace run is
# asking to see everything).
_METRICS_ENV = "DOS_HOOK_METRICS"


def observations_path(cfg: "Optional[_config.SubstrateConfig]" = None) -> Path:
    """The workspace's observation log path. PURE path arithmetic.

    Rides `cfg.paths.dot_dos` (the per-project `.dos/` home), the
    `streams_dir_for` idiom. Never creates anything — `append` is the only
    creator (the read-only-path discipline)."""
    from dos import config as _config_mod

    cfg = _config_mod.ensure(cfg)
    return cfg.paths.dot_dos / METRICS_DIRNAME / LOG_BASENAME


def metrics_enabled(*, debug: bool = False) -> bool:
    """True iff the durable append should run — on by default, `DOS_HOOK_METRICS=0`
    opts out, `--debug` always logs (the same gate the other writers honor)."""
    if debug:
        return True
    return os.environ.get(_METRICS_ENV, "").strip() != "0"


def _now_iso() -> str:
    """Second-resolution UTC ISO-8601 with a `Z` — the journal `ts` grammar."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The write side — a PURE entry builder + a fail-soft boundary append.
# ---------------------------------------------------------------------------


def observation_entry(
    verb: str,
    outcome: str,
    *,
    exit_code: int = 0,
    latency_ms: float = 0.0,
    ts: str = "",
    run_id: str = "",
    rung: str = "",
    reason_class: str = "",
    dialect: str = "",
    tree_known: Optional[bool] = None,
    stream_state: str = "",
    marker_count: int = 0,
    max_markers: int = 0,
    claims_seen: int = 0,
    verify_source: str = "",
    blocked_plan: str = "",
    blocked_phase: str = "",
    panic_recovered: bool = False,
) -> dict:
    """One schema-tagged observation record — the PURE builder (the `_step_entry`
    posture).

    Only `verb` + `outcome` + `exit` + `latency_ms` are always present; every
    verb-specific field is written ONLY when set, so a bare record stays small
    and the schema version never bumps for an absent field (the additive
    contract every conforming writer shares). `outcome` is a short
    verb-specific tag: `deny`/`warn`/`passthrough`/`block`/`let`/`allow`/
    `refuse`/`delegate`/…. An empty `verb`/`outcome` raises — a writer that
    stamps an unnamed record is a kernel bug, not silent data (`durable_schema.tag`).
    `ts` may be left empty for `append` to stamp at write time.
    """
    if not verb:
        raise ValueError("an observation must name its verb")
    if not outcome:
        raise ValueError("an observation must name its outcome")
    e: dict = {
        **_ds.tag(SCHEMA_FAMILY, SCHEMA_VERSION),
        "op": OP_OBSERVE,
        "verb": verb,
        "outcome": outcome,
        "exit": int(exit_code),
        "latency_ms": float(latency_ms),
    }
    if ts:
        e["ts"] = ts
    if run_id:
        e["run_id"] = run_id
    if rung:
        e["rung"] = rung
    if reason_class:
        e["reason_class"] = reason_class
    if dialect:
        e["dialect"] = dialect
    if tree_known is not None:
        e["tree_known"] = bool(tree_known)
    if stream_state:
        e["stream_state"] = stream_state
    if marker_count or max_markers:
        e["marker_count"] = int(marker_count)
        e["max_markers"] = int(max_markers)
    if claims_seen:
        e["claims_seen"] = int(claims_seen)
    if verify_source:
        e["verify_source"] = verify_source
    if blocked_plan:
        e["blocked_plan"] = blocked_plan
        e["blocked_phase"] = blocked_phase
    if panic_recovered:
        e["panic_recovered"] = True
    return e


def append(
    entry: dict,
    *,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    path: Optional[Path] = None,
    debug: bool = False,
) -> bool:
    """Append one observation to the workspace log — best-effort, FAIL-SOFT.

    Stamps `ts` if absent, writes one canonical-JSON line (the
    `lane_journal.append` grammar: sorted keys, `ensure_ascii=False`), creates
    the directory on first use, and `fsync`s so a recorded observation outlives
    the one-shot process that wrote it. Gated by `metrics_enabled` (the
    `DOS_HOOK_METRICS` opt-out; `--debug` always logs).

    NEVER raises: the caller has already decided + emitted, and telemetry about
    a decision must not be able to alter it (docs/99). Returns True iff a line
    was durably written — a test affordance, not a contract the hook verbs read.
    """
    try:
        if not metrics_enabled(debug=debug):
            return False
        p = path or observations_path(cfg)
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
    except Exception:  # noqa: BLE001 — a telemetry write fault never alters a verdict
        return False


# ---------------------------------------------------------------------------
# The read side — a tolerant boundary read + the PURE rate fold.
# ---------------------------------------------------------------------------


def read_observations(path: Optional[Path] = None,
                      cfg: "Optional[_config.SubstrateConfig]" = None) -> tuple[dict, ...]:
    """Every soundly-readable observation in the log, in file order. Boundary I/O.

    Tolerant the way every JSONL fold here is: a blank/torn/corrupt line is
    skipped ("didn't happen"); a record tagged for a different family, tagged
    at a version this kernel predates (refuse-don't-guess, `durable_schema`),
    untagged, or whose `op` is not OBSERVE is skipped. A missing or unreadable
    file degrades to () — a read-only surface shows what it has, never an error.
    """
    p = path or observations_path(cfg)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    out: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        verdict = _ds.classify(rec, family=SCHEMA_FAMILY, understands=SCHEMA_VERSION)
        if not verdict.readability.is_soundly_readable:
            continue
        if rec.get("op") != OP_OBSERVE:
            continue
        out.append(rec)
    return tuple(out)


@dataclass(frozen=True)
class InterventionRate:
    """The folded "what share of tool calls did the kernel touch?" value.

    All five counts come from ONE observation log — never the lane journal (the
    like-for-like rule). `pretool_records` is every pretool record seen;
    `adjudicated` excludes the `delegate` handoffs (the docs/297 denominator
    rule); `passed` + `intervened` partition `adjudicated`. The percents are
    properties so a renderer never recomputes the arithmetic differently.
    """

    pretool_records: int = 0
    adjudicated: int = 0
    passed: int = 0
    intervened: int = 0
    delegated: int = 0

    @property
    def passed_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.passed * 100.0 / self.adjudicated

    @property
    def intervened_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.intervened * 100.0 / self.adjudicated

    def to_dict(self) -> dict:
        return {
            "adjudicated": self.adjudicated,
            "passed": self.passed,
            "intervened": self.intervened,
            "delegated": self.delegated,
            "passed_pct": round(self.passed_pct, 1),
            "intervened_pct": round(self.intervened_pct, 1),
        }


def intervention_rate(records: Iterable[dict], *, since: str = "") -> InterventionRate:
    """Fold observation records into the intervention rate. PURE.

    One pretool record = one tool call adjudicated, so pretool is the honest
    denominator (posttool/stop/marker firings are not tool-call admissions). A
    `delegate` outcome leaves the denominator: it is a handoff whose real
    verdict is another record (docs/297) — counting both would count the call
    twice. Everything adjudicated that did not pass through untouched was
    intervened on (deny / warn — the rungs that touched the call).

    `since` keeps only records with `ts >= since` (ISO-8601 sorts lexically);
    when a window is set, a record with no `ts` is skipped — a windowed fold
    must not count an undatable record (the conservative direction).

    Records in, value out, no disk — the unit-test surface. The signature takes
    observation records ONLY; there is deliberately no parameter through which
    a lane-journal count could enter either side of the ratio.
    """
    pretool_records = adjudicated = passed = delegated = 0
    for rec in records:
        if rec.get("verb") != "pretool":
            continue
        ts = str(rec.get("ts") or "")
        if since and (not ts or ts < since):
            continue
        pretool_records += 1
        outcome = str(rec.get("outcome") or "")
        if outcome == "delegate":
            delegated += 1
            continue
        adjudicated += 1
        if outcome == "passthrough":
            passed += 1
    return InterventionRate(
        pretool_records=pretool_records,
        adjudicated=adjudicated,
        passed=passed,
        intervened=adjudicated - passed,
        delegated=delegated,
    )
