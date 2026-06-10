"""dispatch-timeline — one-page handoff view for a single /dispatch run.

Renders one ordered timeline for `docs/_chained_runs/<run-ts>/` by joining
the README breadcrumbs, both children's result envelopes, telemetry sidecars,
the packet header, fanout-state for those (plan,phase) pairs, the ship oracle
verdict, and `git log <start-sha>..HEAD`.

Why this exists: today reconstructing what one /dispatch did requires reading
the README + 2 envelopes + 2 telemetry files + the packet + the fanout run
dir + git log. That's a 7-file eyeball pass per run. This script collapses it
into one column-aligned timeline with a contract-handoff health footer that
flags stage gaps (e.g. child1 success but child2 never launched, or claimed
N picks vs oracle-confirmed M).

Read-only — never writes, never archives, never edits a plan.

Usage:
    python scripts/dispatch_timeline.py 20260521T005226Z          # one run
    python scripts/dispatch_timeline.py --latest                  # most recent
    python scripts/dispatch_timeline.py 20260521T005226Z --json   # machine
    python scripts/dispatch_timeline.py --batch                   # all runs, per-boundary GAP rate
    python scripts/dispatch_timeline.py --batch --since 20260520  # window
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
else:  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Contracts library lives next to this file (scripts/contracts.py). It's
# imported lazily — when contracts/ is empty or the import fails, we fall
# back to the legacy hardcoded _build_checks transparently.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # pragma: no cover - exercised by integration tests
    from contracts import (  # noqa: E402
        RunCtx,
        audit_contract,
        discover_contracts,
    )
    _CONTRACTS_IMPORTED = True
except ImportError:
    _CONTRACTS_IMPORTED = False

from dos import config as _config
from dos import git_delta as _git_delta


def _repo() -> Path:
    """The served workspace root (where run-dirs + git live) — not the package."""
    return _config.active().paths.root


def _chained_dir() -> Path:
    return _config.active().paths.chained_runs


def _contracts_dispatch_dir() -> Path:
    return _repo() / "docs" / "contracts" / "dispatch"

# README breadcrumb keys: split on the FIRST `: ` after the leading bullet.
# Keys can contain inner colons or parens (e.g. "Gate verdict (Step 5.6)") so
# we don't try to bound them with a character class — we just take everything
# up to the first ": " separator.
README_KEYED = re.compile(r"^\s*-\s+(.+?):\s+(.+?)\s*$")

# "Packet path: `output/next-up/next-up-2026-05-21-4.md`" — strip backticks.
BACKTICKED = re.compile(r"`([^`]+)`")

# Gate-verdict line from Step 5.6 lives free-text in the README; pull the
# tag (LIVE / DRAIN / STALE-STAMP / BLOCKED) when present. BLOCKED-OUTCOME and
# BLOCKED are the renamed WEDGED/WEDGE — both legacy + new spellings matched
# (longer BLOCKED-OUTCOME alternative first so it wins over bare BLOCKED).
GATE_VERDICT_RE = re.compile(
    r"\b(LIVE|DRAIN|STALE[-_ ]STAMP|BLOCKED-OUTCOME|BLOCKED|WEDGED|WEDGE|COLLISION|ERROR)\b"
)

# Picks-shipped line: "Picks shipped: 0/1" or "0/2 picks shipped".
PICKS_SHIPPED_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*(?:picks?\s*shipped|shipped)?", re.I)

# Free-text gate hint inside an Outcome bullet: "verdict=LIVE (1 live soft-claim)"
INLINE_VERDICT_RE = re.compile(
    r"verdict\s*=\s*(LIVE|DRAIN|STALE[-_ ]STAMP|BLOCKED-OUTCOME|BLOCKED|WEDGED|WEDGE|COLLISION|ERROR)(?:\s*\((\d+)\s*live)?",
    re.I,
)


@dataclass
class Stage:
    """One ordered row in the timeline."""

    order: int
    stage: str  # short label
    actor: str  # upper | child1 | child2 | git | oracle
    status: str  # ok | halt | miss | flag | info
    detail: str
    # Wall-clock the stage itself consumed, when the source gives us one. Today
    # only child1/child2 carry a real duration (their result envelope's
    # `duration_ms`); marker stages (invoke/packet/gate/commits/oracle/headline)
    # are instantaneous bookkeeping and stay None. This is the field that lets a
    # reader see *where the time went* — the "1.4 min iteration that actually
    # spent 8.5 min inside child1" confusion the content view alone can't answer.
    duration_s: float | None = None

    def as_row(self) -> tuple[str, str, str, str]:
        return (self.stage, self.actor, self.status, self.detail)


@dataclass
class HandoffCheck:
    """One contract-boundary check for the footer."""

    boundary: str
    expected: str
    observed: str
    verdict: str  # OK | GAP | UNKNOWN
    why: str = ""


@dataclass
class Timeline:
    run_ts: str
    run_dir: Path
    stages: list[Stage] = field(default_factory=list)
    checks: list[HandoffCheck] = field(default_factory=list)
    # raw inputs (handy for --json)
    readme: dict[str, str] = field(default_factory=dict)
    next_up_env: dict[str, Any] = field(default_factory=dict)
    fanout_env: dict[str, Any] = field(default_factory=dict)
    telemetry_next_up: dict[str, Any] = field(default_factory=dict)
    telemetry_fanout: dict[str, Any] = field(default_factory=dict)
    commits_since_start: list[dict[str, str]] = field(default_factory=list)
    packet_picks: list[dict[str, str]] = field(default_factory=list)
    # Per-stage orchestration timing the upper session wrote (the job-side
    # `scripts/step_trace.StepTrace.as_dict()` shape: {"spans":[{"step",
    # "elapsed_ms"},...]}). This is what lets the MARKER stages
    # (packet/gate/commits/oracle/headline) — the upper-session bookkeeping
    # between the two children — carry a real `duration_s` instead of the
    # always-None placeholder. Empty when the run predates the tracer or the
    # file is absent. See `_stage_duration_s`.
    orchestration_timings: dict[str, Any] = field(default_factory=dict)
    # mode flag — True means load checks from docs/contracts/dispatch/,
    # False forces the legacy hardcoded path (used by --legacy-checks).
    use_contracts: bool = True


def _load_json(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_readme(run_dir: Path) -> dict[str, str]:
    """Lift `- Key: value` and `- **Key:** value` lines into a flat dict.

    The README is operator-readable prose, but the keyed lines are stable
    enough across runs that we can trust them as a breadcrumb spine. When a
    field is missing we fall back to envelope-derived facts in the renderer.
    """

    readme_path = run_dir / "README.md"
    out: dict[str, str] = {}
    if not readme_path.exists():
        return out
    for raw_line in readme_path.read_text(encoding="utf-8").splitlines():
        m = README_KEYED.match(raw_line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        # Strip surrounding `**bold**` from headline-style keys.
        if key.startswith("**") and key.endswith("**"):
            key = key[2:-2].strip()
        val = m.group(2).strip()
        # Strip surrounding backticks (`path`) so downstream code gets a raw
        # path/string.
        bt = BACKTICKED.search(val)
        if bt and bt.group(0) == val:
            val = bt.group(1)
        out[key] = val
    return out


def _parse_packet_picks(packet_path: Path) -> list[dict[str, str]]:
    """Best-effort lift of (plan, phase) pairs from a /next-up packet.

    Real packets use numbered section headers under `## 2. Top N next major
    items` shaped like `### 1. DLC FQ-301 — title…`. The first token after the
    section number is the plan id; the second is the phase id (the exact
    string the ship oracle takes positionally — e.g. `FQ-301`, `IF4.1`, `P3`).
    """

    if not packet_path.exists():
        return []
    text = packet_path.read_text(encoding="utf-8", errors="replace")
    picks: list[dict[str, str]] = []
    # `### 1. <PLAN> <PHASE> — …`. Plan/phase are non-space tokens; we let
    # the oracle decide whether the phase is real.
    for m in re.finditer(
        r"^###\s+\d+\.\s+([A-Z][A-Za-z0-9_\-]*)\s+([A-Za-z0-9][A-Za-z0-9_\-./]*)\s+[—\-–]",
        text,
        re.M,
    ):
        picks.append({"plan_id": m.group(1).strip(), "phase": m.group(2).strip()})
    # De-dup while preserving order.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for p in picks:
        key = (p["plan_id"], p["phase"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def _git_log(start_sha: str) -> list[dict[str, str]]:
    """Commits since the run's start SHA, on the served workspace.

    Delegates to the shared `git_delta.commits_since` reader so this Stage-6
    rung and `dos.liveness`'s git rung can't drift (LVN-1b: LVN must not
    re-implement the commit-delta). Byte-identical behavior to the prior inline
    `git log start..HEAD` — same `_repo()` cwd, same 10s bound, same
    `[{sha, subject}, …]` shape, same empty-on-any-failure degrade.
    """
    return _git_delta.commits_since(start_sha, root=_repo())


def _ship_oracle_verdict(plan_id: str, phase: str) -> str:
    """Call scripts/ship_oracle.py for one (plan,phase). Returns short verdict.

    The oracle script is registry-first per the memory note. We call it via
    its positional CLI (`ship_oracle.py <plan> <phase>`) and treat exit code
    + stdout JSON as authoritative.
    """

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "dos.oracle", plan_id, phase],
            cwd=_repo(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return "ERROR"
    # Try JSON first; fall back to plain "SHIPPED" / "NOT" tokens.
    try:
        d = json.loads(proc.stdout)
        shipped = d.get("shipped")
        if shipped is True:
            return "SHIPPED"
        if shipped is False:
            return "NOT"
    except json.JSONDecodeError:
        out = proc.stdout.strip().upper()
        if "SHIPPED" in out:
            return "SHIPPED"
        if "NOT" in out or "MISSING" in out:
            return "NOT"
    return "ERROR"


def _add(
    t: Timeline,
    stage: str,
    actor: str,
    status: str,
    detail: str,
    duration_s: float | None = None,
) -> None:
    t.stages.append(
        Stage(
            order=len(t.stages),
            stage=stage,
            actor=actor,
            status=status,
            detail=detail,
            duration_s=duration_s,
        )
    )


def _stage_duration_s(t: Timeline, stage: str) -> float | None:
    """Look up a marker stage's wall-clock from the upper session's
    orchestration-timings, in seconds, or None when absent.

    The upper session records its per-stage timing as
    ``{"spans": [{"step": "<stage>", "elapsed_ms": <float>}, ...]}`` (the
    job-side ``StepTrace.as_dict()`` shape). We sum every span whose ``step``
    matches ``stage`` (a stage may be entered more than once) and convert ms→s.
    Returns None — not 0.0 — when there's no matching span, so the renderer
    keeps showing nothing rather than a misleading instantaneous reading.
    """
    spans = t.orchestration_timings.get("spans") if t.orchestration_timings else None
    if not isinstance(spans, list):
        return None
    total_ms = 0.0
    matched = False
    for s in spans:
        if isinstance(s, dict) and s.get("step") == stage:
            try:
                total_ms += float(s.get("elapsed_ms") or 0.0)
                matched = True
            except (TypeError, ValueError):
                continue
    return (total_ms / 1000.0) if matched else None


def build_timeline(run_ts: str, *, skip_oracle: bool = False, use_contracts: bool = True) -> Timeline:
    """Build a Timeline for one chained-run dir.

    skip_oracle: skip ship-oracle subprocess calls (Stage 7 + H6). Batch mode
    sets this to True so per-boundary coverage stats render in seconds, not
    minutes — the oracle is only needed for ship-reconciliation, not coverage.

    use_contracts: load handoff checks from docs/contracts/dispatch/ (default).
    Set False to fall back to the legacy hardcoded check builder.
    """

    run_dir = _chained_dir() / run_ts
    t = Timeline(run_ts=run_ts, run_dir=run_dir, use_contracts=use_contracts)
    if not run_dir.exists():
        _add(t, "run-dir", "upper", "miss", f"{run_dir} not found")
        return t

    t.readme = _parse_readme(run_dir)
    envelopes_dir = run_dir / "result_envelopes"
    t.next_up_env = _load_json(envelopes_dir / "next-up.json")
    t.fanout_env = _load_json(envelopes_dir / "fanout.json")
    t.telemetry_next_up = _load_json(envelopes_dir / "telemetry-next-up.json")
    t.telemetry_fanout = _load_json(envelopes_dir / "telemetry-fanout.json")
    # Upper-session per-stage orchestration timing (optional; absent on runs
    # that predate the step tracer). Same schema as the job-side StepTrace.
    t.orchestration_timings = _load_json(envelopes_dir / "orchestration-timings.json")

    # --- Stage 1: invocation ----------------------------------------------
    invoked = t.readme.get("invoked at", "?")
    args = t.readme.get("args", "?")
    start_sha = t.readme.get("start sha", "")
    _add(t, "invoke", "upper", "ok", f"{invoked}  args={args}  start={start_sha or '?'}")

    # --- Stage 2: child1 (/next-up) ---------------------------------------
    if t.next_up_env:
        sub = t.next_up_env.get("subtype") or "?"
        turns = t.next_up_env.get("num_turns")
        cost = t.next_up_env.get("total_cost_usd")
        dur_ms = t.next_up_env.get("duration_ms")
        dur_min = f"{dur_ms / 60000:.1f}m" if isinstance(dur_ms, (int, float)) else "?"
        cost_s = f"${cost:.3f}" if isinstance(cost, (int, float)) else "?"
        status = "ok" if sub == "success" else "halt"
        _add(
            t,
            "child1",
            "child1",
            status,
            f"subtype={sub} turns={turns} dur={dur_min} cost={cost_s} stop={t.next_up_env.get('stop_reason','?')}",
            duration_s=(dur_ms / 1000.0) if isinstance(dur_ms, (int, float)) else None,
        )
    else:
        _add(t, "child1", "child1", "miss", "no next-up.json envelope")

    # --- Stage 3: packet ---------------------------------------------------
    # The packet's numbered `### N. <PLAN> <PHASE>` headers are *candidates*
    # — the gate (Stage 4) reduces these to a live-pick count by applying
    # soft-claim + stale-stamp + WIP-collision filters. We surface candidates
    # here but never call them "picks" — that conflates two different things.
    packet_path_str = t.readme.get("packet path", "")
    packet_path = (_repo() / packet_path_str) if packet_path_str else None
    if packet_path and packet_path.exists():
        t.packet_picks = _parse_packet_picks(packet_path)
        _add(
            t,
            "packet",
            "child1",
            "ok",
            f"{packet_path_str}  candidates={len(t.packet_picks)}",
            duration_s=_stage_duration_s(t, "packet"),
        )
    elif packet_path_str:
        _add(t, "packet", "child1", "miss", f"declared {packet_path_str} but file not found")
    else:
        _add(t, "packet", "child1", "miss", "no packet path in README")

    # --- Stage 4: gate verdict (Step 5.6) ---------------------------------
    # Two write-styles in the wild: a dedicated "Gate verdict (Step 5.6):"
    # line (newer runs) or an inline "verdict=LIVE (N live …)" inside the
    # Outcome bullet (older runs). We accept both and record which.
    gate_verdict = ""
    gate_live_count: int | None = None
    gate_source = ""
    gate_line = t.readme.get("gate verdict (step 5.6)", "") or t.readme.get("gate verdict", "")
    if gate_line:
        m = GATE_VERDICT_RE.search(gate_line)
        if m:
            gate_verdict = m.group(1)
            gate_source = "dedicated key"
    if not gate_verdict:
        # Scan the README body for the inline verdict= form.
        readme_path = run_dir / "README.md"
        if readme_path.exists():
            body = readme_path.read_text(encoding="utf-8", errors="replace")
            m = INLINE_VERDICT_RE.search(body)
            if m:
                gate_verdict = m.group(1).upper()
                if m.group(2):
                    try:
                        gate_live_count = int(m.group(2))
                    except ValueError:
                        gate_live_count = None
                gate_source = "inline verdict= in Outcome"
    if gate_verdict:
        gate_status = "ok" if gate_verdict == "LIVE" else "flag"
        detail = f"{gate_verdict}  (source: {gate_source}"
        if gate_live_count is not None:
            detail += f", live={gate_live_count}"
        detail += ")"
        _add(t, "gate", "upper", gate_status, detail,
             duration_s=_stage_duration_s(t, "gate"))
        # Stash on the timeline so the H3/H4 checks can read it.
        t.readme["_resolved_gate_verdict"] = gate_verdict
        t.readme["_resolved_gate_source"] = gate_source
        if gate_live_count is not None:
            t.readme["_resolved_gate_live_count"] = str(gate_live_count)
    else:
        _add(t, "gate", "upper", "miss", "no gate verdict found (neither dedicated key nor inline verdict=)")

    # --- Stage 5: child2 (/fanout) ----------------------------------------
    if t.fanout_env:
        sub = t.fanout_env.get("subtype") or "?"
        turns = t.fanout_env.get("num_turns")
        cost = t.fanout_env.get("total_cost_usd")
        dur_ms = t.fanout_env.get("duration_ms")
        dur_min = f"{dur_ms / 60000:.1f}m" if isinstance(dur_ms, (int, float)) else "?"
        cost_s = f"${cost:.3f}" if isinstance(cost, (int, float)) else "?"
        # Heuristic: a `success` subtype but a "WIP collision" / "Awaiting"
        # body is really a soft halt — the README says so explicitly.
        result_txt = t.fanout_env.get("result") or ""
        is_soft_halt = isinstance(result_txt, str) and (
            "awaiting" in result_txt.lower()
            or "askuserquestion" in result_txt.lower()
            or "wip collision" in result_txt.lower()
        )
        if sub != "success":
            status = "halt"
        elif is_soft_halt:
            status = "halt"
        else:
            status = "ok"
        _add(
            t,
            "child2",
            "child2",
            status,
            f"subtype={sub} turns={turns} dur={dur_min} cost={cost_s} "
            f"stop={t.fanout_env.get('stop_reason','?')}",
            duration_s=(dur_ms / 1000.0) if isinstance(dur_ms, (int, float)) else None,
        )
    else:
        # Missing fanout envelope is the contract's intended state when child2
        # is skipped on an empty packet. We disambiguate in the footer check.
        _add(t, "child2", "child2", "info", "no fanout.json envelope (child2 not launched or crashed)")

    # --- Stage 6: commits since start -------------------------------------
    t.commits_since_start = _git_log(start_sha)
    _add(
        t,
        "commits",
        "git",
        "info" if t.commits_since_start else "flag",
        f"{len(t.commits_since_start)} commits since {start_sha or '?'}",
        duration_s=_stage_duration_s(t, "commits"),
    )

    # --- Stage 7: ship oracle per candidate -------------------------------
    # We can't tell *from the packet alone* which candidates were the gate's
    # live picks vs stalled/demoted ones — that info is in the gate stage.
    # So we report oracle per-candidate and tag shipped vs not; the live-pick
    # reconciliation happens in H6.
    if skip_oracle:
        _add(t, "oracle", "oracle", "info", "skipped (batch mode)")
    elif t.packet_picks:
        oracle_rows: list[str] = []
        shipped = 0
        for pick in t.packet_picks:
            v = _ship_oracle_verdict(pick["plan_id"], pick["phase"])
            oracle_rows.append(f"{pick['plan_id']}:{pick['phase']}={v}")
            if v == "SHIPPED":
                shipped += 1
        _add(
            t,
            "oracle",
            "oracle",
            "info",
            f"shipped={shipped}/{len(t.packet_picks)} candidates  " + "  ".join(oracle_rows),
            duration_s=_stage_duration_s(t, "oracle"),
        )
    else:
        _add(t, "oracle", "oracle", "info", "no candidates to verify")

    # --- Stage 8: README headline (operator-stated outcome) ----------------
    headline = t.readme.get("headline", "")
    if headline:
        _add(t, "headline", "upper", "info", headline[:140])

    # --- Contract-handoff checks (footer) ---------------------------------
    _build_checks(t, skip_oracle=skip_oracle)
    return t


def _build_checks(t: Timeline, *, skip_oracle: bool = False) -> None:
    """Populate t.checks with handoff-boundary verdicts.

    Default path: load contracts from docs/contracts/dispatch/*.json and run
    each contract's audit[] rules against the run via scripts/contracts.py.
    Fallback path: legacy hardcoded checks (kept for safety while the
    contracts spec stabilises). Toggle via build_timeline(use_contracts=...).
    """

    use_contracts = t.use_contracts and _CONTRACTS_IMPORTED and _contracts_dispatch_dir().exists()
    if use_contracts:
        _build_checks_from_contracts(t, skip_oracle=skip_oracle)
    else:
        _build_checks_legacy(t, skip_oracle=skip_oracle)


def _build_runctx(t: Timeline, *, skip_oracle: bool) -> RunCtx:
    """Lift the Timeline's already-loaded inputs into a RunCtx for the auditor.

    Two derived fields are filled here because they're computed during stage
    assembly (gate_verdict, gate_live_count, oracle_shipped_count) and aren't
    available from raw envelope reads alone.
    """

    readme_body = ""
    rp = t.run_dir / "README.md"
    if rp.exists():
        readme_body = rp.read_text(encoding="utf-8", errors="replace")
    ctx = RunCtx(
        run_ts=t.run_ts,
        run_dir=t.run_dir,
        readme=t.readme,
        readme_body=readme_body,
        next_up_env=t.next_up_env,
        fanout_env=t.fanout_env,
        resolved_gate_verdict=t.readme.get("_resolved_gate_verdict", ""),
        packet_candidates=t.packet_picks,
    )
    live_count_s = t.readme.get("_resolved_gate_live_count", "")
    if live_count_s.isdigit():
        ctx.gate_live_count = int(live_count_s)
    if not skip_oracle and t.packet_picks:
        shipped = sum(
            1
            for p in t.packet_picks
            if _ship_oracle_verdict(p["plan_id"], p["phase"]) == "SHIPPED"
        )
        ctx.oracle_shipped_count = shipped
    return ctx


def _build_checks_from_contracts(t: Timeline, *, skip_oracle: bool) -> None:
    """Run every contract under docs/contracts/dispatch/ against this run."""

    ctx = _build_runctx(t, skip_oracle=skip_oracle)
    contracts = discover_contracts(_contracts_dispatch_dir())
    # Map AuditResult verdicts to the HandoffCheck shape the renderer expects.
    # SKIP rows are quietly dropped — they correspond to rules whose precondition
    # didn't hold (e.g. envelope absent for a skip_when_envelope_absent rule).
    for c in contracts:
        results = audit_contract(c, ctx)
        for r in results:
            if r.verdict == "SKIP":
                continue
            verdict = r.verdict if r.verdict in ("OK", "GAP", "UNKNOWN") else "UNKNOWN"
            # Soft GAPs map to GAP but with a clarifying suffix in `why` so
            # they're visible without forcing a hard failure budget.
            t.checks.append(HandoffCheck(
                boundary=f"{c.title} :: {r.rule_id}",
                expected=c.expected.get("form", "") or c.title,
                observed=r.observed,
                verdict=verdict,
                why=(r.why or ("soft" if r.soft else "")),
            ))


def _build_checks_legacy(t: Timeline, *, skip_oracle: bool = False) -> None:
    """Original hardcoded check builder. Retained for fallback.

    Each check names a contract boundary, what the producer should have
    written, what we observed, and whether the boundary held.

    skip_oracle: skip H6 (ship-reconciliation) which calls ship_oracle.py.
    """

    # H1: upper → child1 — `result` envelope must exist with subtype.
    if t.next_up_env and t.next_up_env.get("subtype"):
        t.checks.append(HandoffCheck(
            boundary="upper → child1 (envelope)",
            expected="next-up.json with subtype",
            observed=f"subtype={t.next_up_env.get('subtype')}",
            verdict="OK",
        ))
    else:
        t.checks.append(HandoffCheck(
            boundary="upper → child1 (envelope)",
            expected="next-up.json with subtype",
            observed="missing or unparseable",
            verdict="GAP",
        ))

    # H2: child1 → packet — declared path must resolve to a file.
    packet_path_str = t.readme.get("packet path", "")
    packet_exists = bool(packet_path_str) and (_repo() / packet_path_str).exists()
    t.checks.append(HandoffCheck(
        boundary="child1 → packet (file)",
        expected="packet .md exists at declared path",
        observed=("exists" if packet_exists else f"missing ({packet_path_str or 'no path'})"),
        verdict="OK" if packet_exists else "GAP",
    ))

    # H3: packet → gate — README must carry a typed verdict tag (in either
    # the dedicated key style or the inline `verdict=…` Outcome bullet).
    gate_tag = t.readme.get("_resolved_gate_verdict", "")
    gate_source = t.readme.get("_resolved_gate_source", "")
    t.checks.append(HandoffCheck(
        boundary="packet → gate (typed verdict)",
        expected="one of LIVE/DRAIN/STALE-STAMP/WEDGE/COLLISION",
        observed=(f"{gate_tag} ({gate_source})" if gate_tag else "untyped or missing"),
        verdict="OK" if gate_tag else "GAP",
        why="without a typed tag, dispatch-loop falls back to grep heuristics (memory: typed-verdict-over-binary-gate)",
    ))
    # H3b: README write-style consistency — flag inline-only as a soft drift
    # signal (the newer dedicated-key style is what /dispatch-loop's Step 3
    # parser expects per the SKILL).
    if gate_tag and gate_source == "inline verdict= in Outcome":
        t.checks.append(HandoffCheck(
            boundary="README write-style (gate verdict)",
            expected="dedicated `Gate verdict:` line (machine-grepable)",
            observed="inline verdict= inside Outcome bullet",
            verdict="GAP",
            why="dispatch-loop and downstream tooling parse the dedicated key; inline form is parser-fragile",
        ))

    # H4: gate → child2 — LIVE verdict must coincide with a child2 envelope;
    # DRAIN/WEDGE/STALE-STAMP must NOT launch child2.
    has_fanout = bool(t.fanout_env)
    if gate_tag == "LIVE":
        t.checks.append(HandoffCheck(
            boundary="gate → child2 (launch)",
            expected="child2 launched (fanout.json present)",
            observed="present" if has_fanout else "absent",
            verdict="OK" if has_fanout else "GAP",
        ))
    elif gate_tag in ("DRAIN", "STALE-STAMP", "WEDGE"):
        t.checks.append(HandoffCheck(
            boundary="gate → child2 (suppression)",
            expected=f"{gate_tag} → child2 NOT launched",
            observed="suppressed" if not has_fanout else "launched anyway",
            verdict="OK" if not has_fanout else "GAP",
        ))
    else:
        t.checks.append(HandoffCheck(
            boundary="gate → child2",
            expected="verdict-driven launch decision",
            observed="cannot evaluate — gate verdict missing",
            verdict="UNKNOWN",
        ))

    # H5: child2 → grandchildren — fanout envelope `success` but with an
    # AskUserQuestion-shaped result body is a known headless soft-halt (the
    # 20260521T005226Z pattern). Flag it explicitly.
    if t.fanout_env:
        result_txt = t.fanout_env.get("result") or ""
        soft_halt = isinstance(result_txt, str) and (
            "awaiting" in result_txt.lower()
            or "askuserquestion" in result_txt.lower()
            or "wip collision" in result_txt.lower()
        )
        if soft_halt:
            t.checks.append(HandoffCheck(
                boundary="child2 → grandchildren (launch)",
                expected="grandchild agents launched, ship picks",
                observed="soft-halt: AskUserQuestion in headless",
                verdict="GAP",
                why="headless claude -p cannot answer; needs a non-interactive WIP-collision policy",
            ))
        else:
            t.checks.append(HandoffCheck(
                boundary="child2 → grandchildren (launch)",
                expected="grandchild agents launched",
                observed=f"subtype={t.fanout_env.get('subtype')}",
                verdict="OK" if t.fanout_env.get("subtype") == "success" else "GAP",
            ))

    if skip_oracle:
        return
    # H6: claimed picks vs oracle-shipped. We compare the README's stated
    # picks-shipped count against (a) the oracle-shipped subset of candidates
    # and (b) the resolved gate's live-pick count if available. This catches
    # both the over-claim and the under-claim case.
    if t.packet_picks:
        shipped = sum(
            1
            for p in t.packet_picks
            if _ship_oracle_verdict(p["plan_id"], p["phase"]) == "SHIPPED"
        )
        candidates = len(t.packet_picks)
        live_count_s = t.readme.get("_resolved_gate_live_count", "")
        live_count = int(live_count_s) if live_count_s.isdigit() else None
        readme_picks_line = t.readme.get("picks shipped", "")
        m = PICKS_SHIPPED_RE.search(readme_picks_line) if readme_picks_line else None
        readme_pair = (int(m.group(1)), int(m.group(2))) if m else None

        parts = [f"oracle-shipped={shipped}/{candidates} candidates"]
        if live_count is not None:
            parts.append(f"gate-live={live_count}")
        if readme_pair:
            parts.append(f"readme={readme_pair[0]}/{readme_pair[1]}")
        observed = "  ".join(parts)

        # Reconciliation: the README's "picks shipped numerator" should equal
        # the oracle-shipped count if both are known; the denominator should
        # match the gate-live count if known. We don't require strict equality
        # against candidate count — gate filtering legitimately reduces it.
        reconciles = True
        if readme_pair is not None:
            if readme_pair[0] != shipped:
                reconciles = False
            if live_count is not None and readme_pair[1] != live_count:
                reconciles = False
        t.checks.append(HandoffCheck(
            boundary="headline ↔ oracle (ship reconciliation)",
            expected="README picks-shipped matches oracle-shipped (numerator) and gate-live (denominator)",
            observed=observed,
            verdict="OK" if reconciles else "GAP",
            why="README is operator-stated; oracle is registry-first authoritative",
        ))


# ---- rendering -----------------------------------------------------------

STATUS_GLYPH = {
    "ok": "OK ",
    "halt": "HALT",
    "miss": "MISS",
    "flag": "FLAG",
    "info": "··· ",
}


def _fmt_dur(seconds: float | None) -> str:
    """Compact human duration for the time column. None → blank (marker stage)."""
    if seconds is None:
        return ""
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60.0:.1f}m"


def render_text(t: Timeline) -> str:
    out: list[str] = []
    out.append(f"# dispatch timeline · {t.run_ts}")
    out.append(f"  run-dir: {t.run_dir.relative_to(_repo())}")
    out.append("")
    # Identify the slowest timed stage so the reader's eye lands on where the
    # wall-clock actually went — the whole point of the time column.
    timed = [s for s in t.stages if s.duration_s is not None]
    slowest_order = max(timed, key=lambda s: s.duration_s).order if timed else None
    total_timed = sum(s.duration_s for s in timed) if timed else 0.0
    # Column widths kept small so the table fits in a terminal.
    header = f"  {'#':>2}  {'stage':<10} {'actor':<7} {'st':<4} {'time':>6}  detail"
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for s in t.stages:
        glyph = STATUS_GLYPH.get(s.status, s.status)
        dur = _fmt_dur(s.duration_s)
        slow = "  ◀ slowest" if s.order == slowest_order else ""
        out.append(f"  {s.order:>2}  {s.stage:<10} {s.actor:<7} {glyph:<4} {dur:>6}  {s.detail}{slow}")
    out.append("")
    if timed:
        out.append(
            f"  timed stages: {len(timed)} · sum {_fmt_dur(total_timed)} "
            f"(child wall-clock; marker stages are instantaneous)"
        )
        out.append("")
    out.append("## contract handoff checks")
    for c in t.checks:
        marker = {"OK": "OK  ", "GAP": "GAP ", "UNKNOWN": "??  "}.get(c.verdict, c.verdict)
        out.append(f"  {marker} {c.boundary}")
        out.append(f"        expected: {c.expected}")
        out.append(f"        observed: {c.observed}")
        if c.why:
            out.append(f"        why     : {c.why}")
    gaps = sum(1 for c in t.checks if c.verdict == "GAP")
    unknowns = sum(1 for c in t.checks if c.verdict == "UNKNOWN")
    out.append("")
    out.append(f"  → {gaps} GAP · {unknowns} UNKNOWN · {len(t.checks) - gaps - unknowns} OK")
    return "\n".join(out)


def render_json(t: Timeline) -> str:
    payload = {
        "run_ts": t.run_ts,
        "run_dir": str(t.run_dir.relative_to(_repo())),
        "stages": [
            {
                "order": s.order,
                "stage": s.stage,
                "actor": s.actor,
                "status": s.status,
                "detail": s.detail,
                "duration_s": s.duration_s,
            }
            for s in t.stages
        ],
        "checks": [
            {
                "boundary": c.boundary,
                "expected": c.expected,
                "observed": c.observed,
                "verdict": c.verdict,
                "why": c.why,
            }
            for c in t.checks
        ],
        "summary": {
            "gap": sum(1 for c in t.checks if c.verdict == "GAP"),
            "unknown": sum(1 for c in t.checks if c.verdict == "UNKNOWN"),
            "ok": sum(1 for c in t.checks if c.verdict == "OK"),
        },
        "packet_picks": t.packet_picks,
        "commits_since_start": t.commits_since_start,
    }
    return json.dumps(payload, indent=2)


def _latest_run() -> str | None:
    _chained = _chained_dir()
    if not _chained.exists():
        return None
    dirs = sorted([p.name for p in _chained.iterdir() if p.is_dir() and p.name[0:1].isdigit()])
    return dirs[-1] if dirs else None


def _all_runs(since: str = "") -> list[str]:
    _chained = _chained_dir()
    if not _chained.exists():
        return []
    dirs = sorted(p.name for p in _chained.iterdir() if p.is_dir() and p.name[0:1].isdigit())
    if since:
        dirs = [d for d in dirs if d >= since]
    return dirs


def render_batch(runs: list[str], *, since: str = "", use_contracts: bool = True) -> str:
    """Aggregate handoff-boundary verdicts across many runs.

    For each boundary that appears, report N runs that exercised it, count of
    GAP vs OK vs UNKNOWN, and the GAP rate. This is the "how well implemented
    are the contract handoff stages" view at the portfolio level — answers
    questions like 'is the dedicated Gate verdict: line consistently written?'
    or 'how often does child2 soft-halt vs launch cleanly?'.

    Skips H6 (ship reconciliation) so it stays fast across hundreds of runs.
    """

    # boundary -> {OK: int, GAP: int, UNKNOWN: int, examples: list[(run_ts, observed)]}
    rollup: dict[str, dict[str, Any]] = {}
    # stage -> list[(run_ts, duration_s)] for the per-stage timing rollup.
    timing: dict[str, list[tuple[str, float]]] = {}
    parsed = 0
    for run_ts in runs:
        t = build_timeline(run_ts, skip_oracle=True, use_contracts=use_contracts)
        # A run with no envelopes at all is usually a launch-error / partial
        # write. Count it but don't credit its absent boundaries as OK.
        if not t.next_up_env and not t.fanout_env and not t.readme:
            continue
        parsed += 1
        for c in t.checks:
            slot = rollup.setdefault(
                c.boundary,
                {"OK": 0, "GAP": 0, "UNKNOWN": 0, "examples": []},
            )
            slot[c.verdict] = slot.get(c.verdict, 0) + 1
            if c.verdict == "GAP" and len(slot["examples"]) < 3:
                slot["examples"].append((run_ts, c.observed))
        for s in t.stages:
            if s.duration_s is not None:
                timing.setdefault(s.stage, []).append((run_ts, s.duration_s))

    out: list[str] = []
    out.append("# dispatch handoff-coverage rollup")
    out.append(f"  runs scanned : {len(runs)}  (parsed: {parsed})")
    if since:
        out.append(f"  since        : {since}")
    out.append("")
    # Sort boundaries by GAP rate descending so the worst-implemented contract
    # stage floats to the top — that's the one observability question pays off.
    rows = []
    for boundary, slot in rollup.items():
        total = slot["OK"] + slot["GAP"] + slot["UNKNOWN"]
        gap_rate = slot["GAP"] / total if total else 0.0
        rows.append((boundary, slot, total, gap_rate))
    rows.sort(key=lambda r: (-r[3], -r[1]["GAP"], r[0]))

    header = f"  {'gap%':>5}  {'gap':>4}  {'ok':>4}  {'?':>3}  {'N':>4}  boundary"
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for boundary, slot, total, gap_rate in rows:
        out.append(
            f"  {gap_rate * 100:>4.0f}%  {slot['GAP']:>4}  {slot['OK']:>4}  "
            f"{slot['UNKNOWN']:>3}  {total:>4}  {boundary}"
        )

    # Show up to 3 example runs per GAP boundary so the operator has something
    # to drill into — the per-boundary GAP rate alone isn't actionable.
    out.append("")
    out.append("## sample GAPs (up to 3 per boundary)")
    for boundary, slot, _total, gap_rate in rows:
        if slot["GAP"] == 0:
            continue
        out.append(f"  {boundary}  ({slot['GAP']} runs, {gap_rate * 100:.0f}%)")
        for run_ts, observed in slot["examples"]:
            out.append(f"    · {run_ts}  {observed}")

    # Per-stage timing rollup — the "which step is systematically slow" view.
    # Sorted by median descending so the chronic time-sink floats to the top
    # (e.g. child1 /next-up consistently ~8min before a BLOCKED verdict).
    out.append("")
    out.append(_render_stage_timing(timing))
    return "\n".join(out)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0,100]); empty → 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def _render_stage_timing(timing: dict[str, list[tuple[str, float]]]) -> str:
    """Render the per-stage duration rollup table from {stage: [(run_ts, secs)]}.

    Split out so it is unit-testable on a synthetic dict without building real
    timelines. Median is the headline stat (robust to the one runaway run);
    p90 + max expose the tail; slowest-run gives a drill-in pointer.
    """
    out: list[str] = []
    out.append("## per-stage timing (where the wall-clock goes)")
    if not timing:
        out.append("  (no timed stages across the scanned runs)")
        return "\n".join(out)
    stat_rows = []
    for stage, pairs in timing.items():
        secs = [d for _ts, d in pairs]
        med = _percentile(secs, 50)
        p90 = _percentile(secs, 90)
        mx = max(secs)
        slow_run = max(pairs, key=lambda pr: pr[1])[0]
        stat_rows.append((stage, len(secs), med, p90, mx, slow_run))
    stat_rows.sort(key=lambda r: -r[2])  # median desc
    header = f"  {'stage':<10} {'N':>4}  {'med':>7}  {'p90':>7}  {'max':>7}  slowest-run"
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for stage, n, med, p90, mx, slow_run in stat_rows:
        out.append(
            f"  {stage:<10} {n:>4}  {_fmt_dur(med):>7}  {_fmt_dur(p90):>7}  "
            f"{_fmt_dur(mx):>7}  {slow_run}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_ts", nargs="?", help="Run timestamp dir name under docs/_chained_runs/")
    ap.add_argument("--latest", action="store_true", help="Use the most recent run dir")
    ap.add_argument("--json", action="store_true", help="Machine-readable output (single-run mode only)")
    ap.add_argument("--batch", action="store_true", help="Aggregate handoff-coverage across all runs")
    ap.add_argument("--since", default="", help="With --batch: only runs whose dir name sorts >= this (e.g. 20260520)")
    ap.add_argument("--legacy-checks", action="store_true", help="Use hardcoded check builder instead of docs/contracts/dispatch/")
    args = ap.parse_args(argv)
    use_contracts = not args.legacy_checks

    if args.batch:
        runs = _all_runs(since=args.since)
        if not runs:
            print("no chained runs found", file=sys.stderr)
            return 2
        print(render_batch(runs, since=args.since, use_contracts=use_contracts))
        return 0

    run_ts = args.run_ts
    if args.latest or not run_ts:
        run_ts = _latest_run()
        if not run_ts:
            print("no chained runs found", file=sys.stderr)
            return 2

    t = build_timeline(run_ts, use_contracts=use_contracts)
    if args.json:
        print(render_json(t))
    else:
        print(render_text(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
