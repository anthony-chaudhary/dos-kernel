"""One-shot preflight bundler for `/fanout-true-headless-multi-agent`.

Replaces Steps 1.5 + 1.6 + 1.6.5 + 1.7 + 1.8 of the SKILL — four separate
Bash subcommands and ~5 paragraphs of prose decision logic — with one Bash
call returning a compact JSON blob the orchestrator branches on.

Audit motivating this (session 8ac5898a, 2026-05-19): the fanout SKILL.md
was 819 lines and the orchestrator was burning context re-deriving the same
preflight verdicts (packet staleness, in-flight collision, wave grouping,
register conflict gate) that already-shipped helpers
(`check_phase_shipped.py`, `fanout_state.py`, `fanout_archive_lock.py`)
produce mechanically. The skill described their behavior in prose rather
than just calling them.

Full audit + recipe: docs/_audits/skill-context-bundling-2026-05-11.md
docs/_audits/fanout-context-audit-2026-05-19.md (this audit).

Usage:
    python scripts/fanout_preflight_context.py <packet-path>
    python scripts/fanout_preflight_context.py <packet-path> --pretty

Output schema (top-level keys):
    schema_version          int   — bump on breaking change
    generated_at            str   — ISO-8601 UTC
    packet                  dict  — {path, last_sha, drift_commits, schema,
                                      packet_schema, expected_packet_schema,
                                      schema_drift, schema_drift_reason}
                                    schema_drift=True (OC4) means the packet's
                                    header schema token is absent/mismatched —
                                    the orchestrator must NOT silently launch.
    picks                   list  — [{n, plan, phase, phase_chain, gates_on,
                                      files (truncated), prompt_text_len,
                                      shipped, in_flight_collision, verdict,
                                      drop_reason}, ...]
                                    verdict ∈ {go, shipped, collision,
                                               unknown}; drop_reason set when
                                               verdict != go.
    waves                   list  — [[pick_n, ...], ...]  partition by gates_on
    drop_list               list  — verdict != go picks, with reason
    live_count              int   — count of go-verdict picks
    dirty_tree              dict  — {start_sha, modified_files, untracked_count,
                                     truncated_at}
    archive_lock            dict  — {state, prev_owner?, prev_age_s?}
    in_flight_overlap_phases list — phase-ids in_flight (filtered to picks)

The helper is read-only — never mutates files or registry. The SKILL still
calls `fanout_state.py register` (write) at Step 1.8 after consuming this.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dos import config as _config
from dos.packet_sidecar import SIDECAR_SCHEMA


def _workspace_root() -> Path:
    """The served workspace (where git, plan docs, and run-dirs live).

    Honors `DISPATCH_WORKSPACE` for a test/fixture redirect, then the active
    config. (The job preflight test repoints the workspace at a tmp dir; under
    the separation refactor it does so via this env var instead of monkeypatching
    a module constant that no longer exists.)
    """
    env = os.environ.get("DISPATCH_WORKSPACE")
    if env:
        return Path(env)
    return _config.active().paths.root


def _next_up_dir() -> Path:
    env = os.environ.get("DISPATCH_NEXT_UP_DIR")
    if env:
        return Path(env)
    return _config.active().paths.next_packets


SCHEMA_VERSION = 2  # +verdict_envelope / refuse (FQ-410)
DIRTY_TREE_CAP = 50  # don't bloat output with a 5k-file untracked list
FILES_CAP_PER_PICK = 20  # paths > 20 truncated; full list lives in sidecar

# The launchable-verdict set + the envelope-refusal judgement now live in ONE
# place, `wedge_reason` (`LAUNCHABLE_VERDICTS` / `envelope_is_refusal`), shared
# with `decisions`; `_envelope_refusal` below delegates there.

# OC4 (2026-05-19) — the packet-schema token the /next-up renderer
# (`scripts/next_up_render.py:PACKET_SCHEMA`) stamps in the packet's markdown
# header. This preflight reads the packet's marker and compares: a missing or
# mismatched token means the /next-up that wrote the packet is out of contract
# with this /fanout, so the orchestrator must NOT silently launch against a
# drifted packet. Keep this in lockstep with the renderer's constant — the two
# being equal IS the handoff contract. ⚓ feedback_mechanical_contract_over_prose.
EXPECTED_PACKET_SCHEMA = "next-up-packet-v1"


def _feature_flags_view() -> dict:
    """Surface the operator-mutable dispatch feature flags relevant to /fanout.

    Reads via the canonical accessors in `next_up_context` (the same read-path
    /next-up uses) so there is ONE loader for execution-state.yaml's
    `feature_flags:` block — no second parser here. Returns the resolved model
    for the `fanout.child` grandchild section (env+yaml honored) plus the raw
    overrides map, so the orchestrator can pass the right `--model` at the
    grandchild launch step without importing the registry itself. Defensive:
    any import/resolution failure degrades to an empty/quiet view rather than
    breaking the whole preflight bundle.
    """
    view: dict[str, object] = {}
    try:
        sys.path.insert(0, str(_workspace_root() / "scripts"))
        import next_up_context as _nuc  # noqa: E402
        view["lane_leasing"] = _nuc.lane_leasing_enabled()
        view["focus_auto"] = _nuc.focus_auto_enabled()
        view["model_overrides"] = _nuc.feature_flags().get("models") or {}
    except Exception:
        view.setdefault("model_overrides", {})
    try:
        import model_registry as _mr  # noqa: E402
        view["fanout_child_model"] = _mr.resolve_model("fanout.child")
    except Exception:
        pass
    return view


def _run(cmd: list[str], *, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess, return (exit, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(
            cmd,
            cwd=str(_workspace_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", f"FileNotFoundError: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:  # pragma: no cover — defensive
        return 1, "", f"{type(e).__name__}: {e}"


def _python() -> str:
    """Return the venv python interpreter, with PowerShell-style fallback."""
    root = _workspace_root()
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable  # fall back to the running interpreter


# The three states the `.prompts.json` prompt sidecar can be in, reported on
# the loader's `sidecar_status` field. FQ-420: the markdown fallback used to
# collapse `absent` and `corrupt` into a bare `source="markdown"`, hiding the
# fact that the renderer DROPPED the prompt bodies — the operator saw only the
# downstream symptom (`body_empty_picks`), never the root cause. Naming the
# status lets the refuse gate point straight at the dropped sidecar.
SIDECAR_PRESENT = "present"    # `.prompts.json` existed and parsed — prompts loaded
SIDECAR_ABSENT = "absent"      # `.prompts.json` did not exist — renderer never wrote it
SIDECAR_CORRUPT = "corrupt"    # `.prompts.json` existed but was unreadable / bad JSON


def load_packet_sidecar(packet_path: Path) -> dict:
    """Prefer the `.prompts.json` sidecar over markdown parsing.

    Returns {schema, picks, source, sidecar_path, sidecar_status}. `source` is
    'sidecar' (prompts loaded from the sidecar), 'markdown' (fell back to header
    parsing), or 'missing' (the packet itself was unreadable). `sidecar_status`
    is the FQ-420 distinguisher — one of SIDECAR_PRESENT / SIDECAR_ABSENT /
    SIDECAR_CORRUPT — so a caller can tell a dropped sidecar (the renderer never
    emitted the prompt bodies) apart from a corrupt one, and from a clean
    markdown packet that genuinely has no sidecar. The skill's Step 2 already
    prefers the sidecar; this preflight does the same.
    """
    sidecar = packet_path.with_name(packet_path.stem + ".prompts.json")
    sidecar_status = SIDECAR_ABSENT
    if sidecar.exists():
        try:
            with open(sidecar, encoding="utf-8") as f:
                d = json.load(f)
            # Repo-relative for display when under the workspace; fall back to
            # the absolute path otherwise (a sidecar outside the workspace — e.g.
            # a tmp_path fixture — must not crash the loader, the same guard
            # `packet_freshness` applies to the packet path).
            try:
                disp_sidecar = str(sidecar.relative_to(_workspace_root())).replace(os.sep, "/")
            except ValueError:
                disp_sidecar = str(sidecar).replace(os.sep, "/")
            return {
                "schema": d.get("schema", SIDECAR_SCHEMA),
                "picks": d.get("picks", []),
                "source": "sidecar",
                "sidecar_path": disp_sidecar,
                "sidecar_status": SIDECAR_PRESENT,
            }
        except (OSError, json.JSONDecodeError):
            # The sidecar is on disk but unreadable — a corrupt/half-written
            # drop, distinct from one that was never written. Record CORRUPT so
            # the refuse gate names it precisely, then fall through to markdown.
            sidecar_status = SIDECAR_CORRUPT
    # Markdown fallback: parse `### N. <PLAN> <PHASE> — <title>` headers.
    # Conservative — we do not extract prompt_text from markdown here; if the
    # sidecar is missing, the SKILL falls back to its existing markdown path.
    # The picks produced here have empty bodies (`prompt_text=""`, `files=[]`);
    # when the packet DID render picks, that empties them downstream — which is
    # exactly why `sidecar_status` is carried out, so the refuse gate can blame
    # the dropped sidecar rather than the (symptomatically) empty picks.
    picks: list[dict] = []
    try:
        text = packet_path.read_text(encoding="utf-8")
    except OSError:
        return {
            "schema": "unknown", "picks": [], "source": "missing",
            "sidecar_path": None, "sidecar_status": sidecar_status,
        }
    header_re = re.compile(r"^###\s+(\d+)\.\s+([A-Z][A-Za-z0-9]*)\s+(\S+)\s+—\s+(.+)$", re.MULTILINE)
    for m in header_re.finditer(text):
        n, plan, phase, title = m.groups()
        picks.append({
            "n": int(n),
            "plan_id": plan,
            "phase_id": phase,
            "phase_title": title.strip(),
            "phase_chain": [phase],
            "doc_path": None,
            "files": [],
            "reserve_paths": [],
            "gates_on": [],
            "prompt_text": "",
        })
    return {
        "schema": "markdown-fallback",
        "picks": picks,
        "source": "markdown",
        "sidecar_path": None,
        "sidecar_status": sidecar_status,
    }


def packet_freshness(packet_path: Path) -> dict:
    """Read 'Last commit: `<sha>`' + 'Packet schema: `<token>`' header lines.

    The `Last commit` sha drives the drift-count diff against HEAD. The OC4
    `Packet schema` token drives the handoff-contract check: a missing or
    mismatched token is reported as `schema_drift: true` so the orchestrator
    can refuse to launch on a packet whose /next-up is out of contract — the
    OC-P4 additive-silent failure mode made loud.
    """
    last_sha = None
    packet_schema: str | None = None
    try:
        with open(packet_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if last_sha is None:
                    m = re.search(r"Last commit:\s*`([0-9a-f]{7,40})`", line)
                    if m:
                        last_sha = m.group(1)
                if packet_schema is None:
                    sm = re.search(r"Packet schema:\s*`([^`]+)`", line)
                    if sm:
                        packet_schema = sm.group(1).strip()
                if last_sha is not None and packet_schema is not None:
                    break
                if i > 20:  # bounded header scan — don't read the whole packet
                    break
    except OSError:
        pass
    # OC4 handoff-contract check. A pre-OC4 packet carries no `Packet schema`
    # line (packet_schema is None) → drift with a "pre-v1" reason; a packet
    # whose token differs from EXPECTED_PACKET_SCHEMA → drift with a mismatch
    # reason. Both block a silent launch.
    if packet_schema is None:
        schema_drift = True
        schema_drift_reason = (
            "packet has no `Packet schema` marker — written by a pre-OC4 "
            "/next-up; re-run /next-up for a versioned packet"
        )
    elif packet_schema != EXPECTED_PACKET_SCHEMA:
        schema_drift = True
        schema_drift_reason = (
            f"packet schema {packet_schema!r} != expected "
            f"{EXPECTED_PACKET_SCHEMA!r} — the /next-up that wrote this packet "
            f"is out of contract with this /fanout"
        )
    else:
        schema_drift = False
        schema_drift_reason = None
    drift = 0
    drift_commits: list[str] = []
    if last_sha:
        rc, out, _ = _run(["git", "log", "--oneline", f"{last_sha}..HEAD"], timeout=15)
        if rc == 0:
            all_drift = [l for l in out.splitlines() if l.strip()]
            drift = len(all_drift)
            # Keep just enough for the SKILL to spot if a recent commit on
            # main shipped something the picks gate on. 10 is plenty.
            drift_commits = all_drift[:10]
    # Repo-relative path for display when the packet lives under the repo;
    # fall back to the absolute path otherwise (a packet outside REPO_ROOT —
    # e.g. a tmp_path fixture — must not crash the read).
    if packet_path.is_absolute():
        try:
            disp_path = str(packet_path.relative_to(_workspace_root())).replace(os.sep, "/")
        except ValueError:
            disp_path = str(packet_path).replace(os.sep, "/")
    else:
        disp_path = str(packet_path)
    return {
        "path": disp_path,
        "last_sha": last_sha,
        "drift_commits": drift_commits,
        "drift_count": drift,
        "packet_schema": packet_schema,
        "expected_packet_schema": EXPECTED_PACKET_SCHEMA,
        "schema_drift": schema_drift,
        "schema_drift_reason": schema_drift_reason,
    }


def packet_shipped_verdict(packet_path: Path) -> dict:
    """Run `check_phase_shipped.py --check-packet` and parse its table.

    Output we care about: per-pick verdict (KEEP / DROP) and cited sha if any.
    Exit codes (--check-packet): 0=any shipped, 1=all clean, 2=no coverage,
    3=parse error.
    """
    rc, out, err = _run(
        [_python(), "-m", "dos.phase_shipped", "--check-packet", str(packet_path)],
        timeout=60,
    )
    # Parse "  DROP <SERIES> <PHASE>          shipped in <sha>" / "  KEEP <SERIES> <PHASE>"
    by_phase: dict[str, dict] = {}
    line_re = re.compile(r"^\s*(KEEP|DROP)\s+([A-Z][A-Za-z0-9]*)\s+(\S+)\s*(?:shipped in\s+([0-9a-f]+))?\s*$")
    for line in out.splitlines():
        m = line_re.match(line)
        if m:
            verdict, plan, phase, sha = m.groups()
            by_phase[f"{plan}/{phase}"] = {
                "verdict": verdict,
                "shipped_sha": sha,
            }
    return {
        "exit_code": rc,
        "by_phase": by_phase,
        "raw_tail": "\n".join(out.splitlines()[-10:]) if out else "",
        "stderr_tail": "\n".join((err or "").splitlines()[-5:]),
    }


# claim_status values that mean the claim is no longer a live block on its
# (plan, phase) — mirrors next_up_context._DEAD_CLAIM_STATUSES on the job side.
# A row carrying one of these is terminal even if its legacy `status` field
# still says in_progress, so it must not count as an in-flight overlap.
_TERMINAL_CLAIM_STATUSES = frozenset({"done", "stale", "released", "expired"})


def list_active_filtered(
    pick_phases: set[str], own_packet_basename: str | None = None
) -> tuple[list[dict], list[str]]:
    """Call fanout_state list-active and filter to entries overlapping picks.

    Returns (filtered_rows, overlapping_phase_ids). Full output is ~50KB;
    filtered output is typically <2KB.

    own_packet_basename: when provided, soft-claim rows whose dispatched_by
    matches this packet are excluded — they are this packet's own freshly-
    written soft-claims, not in-flight work from another packet. Closes the
    self-collision recurrence (#4) where /next-up's pre-write of soft-claims
    wedges the very /fanout it hands off to.
    """
    rc, out, _ = _run(
        [_python(), "scripts/fanout_state.py", "list-active", "--json"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return [], []
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        return [], []
    overlap_phases: list[str] = []
    filtered: list[dict] = []
    for r in rows:
        if r.get("status") not in ("in_progress", "stalled", "open"):
            continue
        # FQ-336 (2026-06-05): a claim whose claim_status is terminal
        # (done/stale/released/expired) is NOT a live block on its phase, even
        # while its legacy `status` field still lags at in_progress (the writer
        # flipped claim_status but not status; the terminal-active-work sweep
        # only drains it after a 14-day grace window). Treating it as an in-flight
        # overlap would re-block a phase the /next-up picker already correctly
        # freed (next_up_context._trim_active_work drops the same rows for the
        # picker bundle) — the false-collision twin of the picker false-DRAIN.
        if str(r.get("claim_status") or "").strip().lower() in _TERMINAL_CLAIM_STATUSES:
            continue
        plan = r.get("plan") or ""
        phase = r.get("phase") or ""
        key = f"{plan}/{phase}"
        if key in pick_phases or phase in pick_phases:
            dispatched_by = r.get("dispatched_by") or ""
            if own_packet_basename and dispatched_by == own_packet_basename:
                continue
            overlap_phases.append(phase)
            filtered.append({
                "id": r.get("id"),
                "plan": plan,
                "phase": phase,
                "title": (r.get("title") or "")[:120],
                "dispatched_by": dispatched_by,
                "claim_kind": r.get("claim_kind"),
                "claim_status": r.get("claim_status"),
                "dispatched_at": r.get("dispatched_at"),
            })
    return filtered, overlap_phases


def dirty_tree_state() -> dict:
    """Snapshot current working tree state — start_sha + bounded mod/untracked list."""
    rc_sha, sha_out, _ = _run(["git", "rev-parse", "HEAD"], timeout=10)
    start_sha = sha_out.strip()[:12] if rc_sha == 0 else None
    rc_st, st_out, _ = _run(["git", "status", "--short"], timeout=10)
    modified: list[str] = []
    untracked: list[str] = []
    truncated = False
    if rc_st == 0:
        for line in st_out.splitlines():
            if not line.strip():
                continue
            tag = line[:2]
            path = line[3:].strip()
            if tag.startswith("??"):
                untracked.append(path)
            else:
                modified.append(f"{tag} {path}")
        total = len(modified) + len(untracked)
        if total > DIRTY_TREE_CAP:
            truncated = True
            keep = max(1, DIRTY_TREE_CAP // 2)
            modified = modified[:keep]
            untracked = untracked[:DIRTY_TREE_CAP - len(modified)]
    return {
        "start_sha": start_sha,
        "modified": modified,
        "untracked": untracked,
        "untracked_count_full": len([l for l in st_out.splitlines() if l.startswith("??")]) if rc_st == 0 else 0,
        "truncated_at": DIRTY_TREE_CAP if truncated else None,
    }


def archive_lock_state() -> dict:
    """Probe the Step 9.5 mutex's current state without acquiring it."""
    rc, out, _ = _run(
        [_python(), "-m", "dos.archive_lock", "status"],
        timeout=10,
    )
    state = (out or "").strip().splitlines()[0] if out.strip() else ""
    if state == "free":
        return {"state": "free"}
    # Held shapes: "held <owner> age=<s>s" / "held-stale <owner> age=<s>s"
    m = re.match(r"^(held|held-stale)\s+(\S+)(?:\s+age=(\d+)s)?", state)
    if m:
        return {
            "state": m.group(1),
            "prev_owner": m.group(2),
            "prev_age_s": int(m.group(3)) if m.group(3) else None,
        }
    return {"state": state or "unknown", "raw": out[:200] if out else ""}


def partition_waves(picks_with_verdict: list[dict]) -> list[list[int]]:
    """Partition live (go-verdict) picks into launch waves by gates_on.

    Returns [[pick_n, ...], ...] — wave 1 = roots (gates_on empty),
    wave 2 = picks whose gates_on ⊆ wave-1 phases, etc.
    """
    live = [p for p in picks_with_verdict if p.get("verdict") == "go"]
    if not live:
        return []
    placed: set[str] = set()  # phase ids already in a wave
    waves: list[list[int]] = []
    remaining = list(live)
    safety = 10  # cap on wave count — packets >10 waves are pathological
    by_n = {p["n"]: p for p in live}
    while remaining and safety > 0:
        safety -= 1
        wave_ns: list[int] = []
        next_remaining: list[dict] = []
        for p in remaining:
            gates = [g for g in (p.get("gates_on") or []) if g]
            if all(g in placed for g in gates):
                wave_ns.append(p["n"])
            else:
                next_remaining.append(p)
        if not wave_ns:
            # cycle or dangling gate — bail; orchestrator will see remainder
            # in drop_list (the SKILL's existing dangling-edge rule applies).
            break
        for n in wave_ns:
            placed.add(by_n[n]["phase"])
        waves.append(wave_ns)
        remaining = next_remaining
    return waves


def merge_picks_with_verdicts(
    sidecar_picks: list[dict],
    shipped: dict,
    in_flight_phases: list[str],
) -> tuple[list[dict], list[dict]]:
    """Produce the merged picks list + drop_list."""
    picks_out: list[dict] = []
    drops: list[dict] = []
    in_flight_set = set(in_flight_phases)
    shipped_by_phase = shipped.get("by_phase", {})
    for p in sidecar_picks:
        plan = p.get("plan_id") or ""
        phase = p.get("phase_id") or ""
        key = f"{plan}/{phase}"
        files = p.get("files") or []
        files_full_count = len(files)
        if files_full_count > FILES_CAP_PER_PICK:
            files = files[:FILES_CAP_PER_PICK] + [f"… ({files_full_count - FILES_CAP_PER_PICK} more — see sidecar)"]

        shipped_entry = shipped_by_phase.get(key)
        verdict = "go"
        drop_reason = None
        if shipped_entry and shipped_entry.get("verdict") == "DROP":
            verdict = "shipped"
            drop_reason = f"shipped in {shipped_entry.get('shipped_sha') or '?'}"
        elif phase in in_flight_set:
            verdict = "collision"
            drop_reason = "in-flight in registry (overlap detected)"

        # OC4 anchor #4 — carry the pick's kind so the fanout/dispatch overlap
        # consumers can branch the same way the renderer's _matrix does: a
        # synthetic findings pick's `files` are routing pointers, not a
        # code-touch footprint. Default `code`; infer `finding` from an explicit
        # field or the FQ- phase-id convention.
        pick_kind = str(p.get("pick_kind") or "").strip().lower()
        if pick_kind not in ("code", "finding"):
            pick_kind = "finding" if (phase.upper().startswith("FQ-") or p.get("is_synthetic")) else "code"

        row = {
            "n": p.get("n"),
            "plan": plan,
            "phase": phase,
            "phase_chain": p.get("phase_chain") or [phase],
            "phase_title": p.get("phase_title") or "",
            "gates_on": p.get("gates_on") or [],
            "files": files,
            "files_full_count": files_full_count,
            "doc_path": p.get("doc_path"),
            "subagent_type": p.get("subagent_type"),
            "mode": p.get("mode"),
            "pick_kind": pick_kind,
            "prompt_text_len": len(p.get("prompt_text") or ""),
            "verdict": verdict,
        }
        if drop_reason:
            row["drop_reason"] = drop_reason
            drops.append({"n": row["n"], "plan": plan, "phase": phase, "reason": drop_reason})
        picks_out.append(row)
    return picks_out, drops


def read_verdict_envelope(tag: str) -> dict | None:
    """Read `output/next-up/.verdict-<tag>.json` if present (FQ-410).

    The /next-up renderer / WEDGE-emitter writes this envelope for every run —
    LIVE-shaped on a real packet, or `verdict=WEDGE|DRAIN`/`do_not_render` when
    the lane was refused. The preflight was BLIND to it: a packet pre-routed
    `verdict=WEDGE do_not_render=true` still scored `live_count=1 verdict=go`
    for any non-shipped pick, so naively following the Step-1 outcome table
    launched an Opus subprocess against a WEDGEd (often body-empty) packet
    ([[feedback_fanout_preflight_blind_to_verdict_envelope]]). Returns the
    parsed dict, or None if the file is absent / unreadable / not an object.
    """
    path = _next_up_dir() / f".verdict-{tag}.json"
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _envelope_refusal(envelope: dict | None) -> tuple[bool, str | None]:
    """Decide whether a verdict envelope means REFUSE (do not launch).

    Thin wrapper over the canonical `wedge_reason.envelope_is_refusal` (the one
    place the envelope-refusal shape is defined, shared with `decisions`); kept as a
    named local so this module's callers read naturally. See that function for the
    rung order.
    """
    from dos import wedge_reason  # noqa: PLC0415
    return wedge_reason.envelope_is_refusal(envelope)


def _body_empty_picks(picks: list[dict]) -> list[int]:
    """Pick-numbers whose body is empty (prompt_text_len 0 AND no files).

    A body-empty pick (`prompt_text_len==0` and `files==[]`) is a refuse signal
    independent of the verdict envelope — it is a scope-substituted / mis-rendered
    pick with nothing for the subagent to do
    ([[feedback_fanout_preflight_blind_to_verdict_envelope]]). Reported so the
    SKILL can refuse rather than launch a no-op Opus subprocess.
    """
    out: list[int] = []
    for p in picks:
        text_len = p.get("prompt_text_len")
        files = p.get("files") or []
        if (text_len == 0 or text_len is None) and not files and p.get("verdict") == "go":
            n = p.get("n")
            if isinstance(n, int):
                out.append(n)
    return out


def _sidecar_dropped_refusal(
    sidecar_status: str, rendered_pick_count: int
) -> tuple[bool, str | None]:
    """Decide whether a missing/corrupt `.prompts.json` sidecar means REFUSE.

    FQ-420: when `/next-up` returns a packet that HAS picks but drops the prompt
    sidecar, the markdown fallback rehydrates the picks with empty bodies, so
    every `/fanout` refuses on `body_empty_picks` — but that names the symptom,
    not the cause. This is the ROOT signal: a packet that rendered >= 1 pick but
    whose sidecar is `absent` (renderer never wrote it) or `corrupt` (wrote a
    broken one) is a renderer defect that blocks the whole dispatch path. Refuse
    with a reason that points at the dropped sidecar so the operator (and the
    `/unstick` cue → `BlockedReason.BODY_EMPTY_PICKS`) routes the fix at the
    renderer, not the picks.

    Does NOT refuse when:
      * the sidecar was present (`SIDECAR_PRESENT`) — the normal path; or
      * the packet rendered NO picks (`rendered_pick_count == 0`) — a genuine
        empty DRAIN packet legitimately has no sidecar, and refusing it here
        would mislabel a true drain as a renderer drop.

    Returns `(refuse, reason)`; `reason` is a short machine-readable string in
    the same shape as `_envelope_refusal`'s.
    """
    if rendered_pick_count <= 0:
        return (False, None)
    if sidecar_status == SIDECAR_ABSENT:
        return (
            True,
            f"sidecar_dropped:absent rendered_picks={rendered_pick_count} "
            f"(/next-up returned picks but never wrote the .prompts.json prompt "
            f"sidecar — every pick body is empty)",
        )
    if sidecar_status == SIDECAR_CORRUPT:
        return (
            True,
            f"sidecar_dropped:corrupt rendered_picks={rendered_pick_count} "
            f"(.prompts.json exists but is unreadable/bad-JSON — prompt bodies lost)",
        )
    return (False, None)


def build_context(packet_path: Path) -> dict:
    sidecar = load_packet_sidecar(packet_path)
    pick_phase_keys = {
        f"{p.get('plan_id','')}/{p.get('phase_id','')}" for p in sidecar["picks"]
    }
    # Also build a phase-only set for the in-flight phase-id check (the
    # registry's phase ids are unambiguous within a plan).
    pick_phases = {p.get("phase_id", "") for p in sidecar["picks"]}
    freshness = packet_freshness(packet_path)
    shipped = packet_shipped_verdict(packet_path)
    own_packet_basename = packet_path.stem
    in_flight_rows, in_flight_overlap = list_active_filtered(
        pick_phase_keys | pick_phases, own_packet_basename=own_packet_basename
    )
    picks_out, drops = merge_picks_with_verdicts(
        sidecar["picks"], shipped, in_flight_overlap
    )
    waves = partition_waves(picks_out)

    # FQ-410: read the verdict envelope for this packet's tag (the packet stem)
    # and decide refusal. A WEDGE/DRAIN/do_not_render envelope means the lane was
    # already routed to /replan — the orchestrator must NOT launch regardless of
    # how many picks look live. A body-empty go-pick is a second, independent
    # refuse signal. `refuse` is the single load-bearing bool the SKILL branches
    # on; `refuse_reasons` lists each contributing cause for the operator log.
    tag = packet_path.stem
    verdict_envelope = read_verdict_envelope(tag)
    env_refuse, env_reason = _envelope_refusal(verdict_envelope)
    body_empty = _body_empty_picks(picks_out)
    # FQ-420: a dropped/corrupt prompt sidecar on a packet that rendered picks is
    # the ROOT refuse signal — listed BEFORE body_empty_picks so the operator
    # reads the cause (sidecar gone) above the symptom (empty bodies). Keyed on
    # the count of picks the packet RENDERED (len(sidecar["picks"])), not the
    # merged go-count, so a packet that claimed work but lost its bodies still
    # trips it even if every pick later reads as a drop.
    sidecar_refuse, sidecar_reason = _sidecar_dropped_refusal(
        sidecar.get("sidecar_status", SIDECAR_ABSENT), len(sidecar["picks"])
    )
    refuse_reasons: list[str] = []
    if sidecar_refuse and sidecar_reason:
        refuse_reasons.append(sidecar_reason)
    if env_refuse and env_reason:
        refuse_reasons.append(env_reason)
    if body_empty:
        refuse_reasons.append(
            "body_empty_picks=" + ",".join(str(n) for n in body_empty)
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packet": {
            "path": freshness["path"],
            "last_sha": freshness["last_sha"],
            "drift_count": freshness["drift_count"],
            "drift_commits": freshness["drift_commits"],
            "schema": sidecar["schema"],
            "source": sidecar["source"],
            "sidecar_path": sidecar["sidecar_path"],
            # FQ-420 — the prompt-sidecar state (present/absent/corrupt). A
            # non-`present` status on a packet with picks is the dropped-sidecar
            # root cause behind a body_empty_picks refuse.
            "sidecar_status": sidecar.get("sidecar_status", SIDECAR_ABSENT),
            # OC4 handoff-contract check (distinct from `schema`, which is the
            # *prompts sidecar* schema). `packet_schema` is the versioned token
            # in the packet markdown header; `schema_drift` is the load-bearing
            # gate the orchestrator branches on at Step 1. `.get` with a
            # conservative default so a patched/legacy `packet_freshness` (e.g.
            # an older test stub) still produces a valid bundle — absent keys
            # default to "treat as drift" (the safe, non-launching outcome).
            "packet_schema": freshness.get("packet_schema"),
            "expected_packet_schema": freshness.get(
                "expected_packet_schema", EXPECTED_PACKET_SCHEMA
            ),
            "schema_drift": freshness.get("schema_drift", True),
            "schema_drift_reason": freshness.get("schema_drift_reason"),
        },
        "picks": picks_out,
        "waves": waves,
        "drop_list": drops,
        "live_count": sum(1 for p in picks_out if p.get("verdict") == "go"),
        # FQ-410 — the verdict-envelope refusal gate. `refuse=True` means DO NOT
        # LAUNCH even if live_count>0: the lane was pre-routed WEDGE/DRAIN, or a
        # go-pick is body-empty. The orchestrator's Step-1 outcome table must
        # check `refuse` BEFORE acting on `live_count`.
        "refuse": bool(refuse_reasons),
        "refuse_reasons": refuse_reasons,
        "verdict_envelope": (
            {
                "present": verdict_envelope is not None,
                "verdict": (verdict_envelope or {}).get("verdict"),
                "reason_class": (verdict_envelope or {}).get("reason_class"),
                "do_not_render": (verdict_envelope or {}).get("do_not_render"),
                "blocked": (verdict_envelope or {}).get("blocked"),
                "all_clear": (verdict_envelope or {}).get("all_clear"),
            }
            if verdict_envelope is not None
            else {"present": False}
        ),
        "body_empty_picks": body_empty,
        "shipped_check_exit": shipped["exit_code"],
        "in_flight_overlap_phases": in_flight_overlap,
        "in_flight_rows": in_flight_rows,
        "dirty_tree": dirty_tree_state(),
        "archive_lock": archive_lock_state(),
        # Operator-mutable dispatch flags relevant to the grandchild launch
        # (model for `fanout.child`, lane-leasing/focus-auto state). The
        # SKILL.md grandchild-launch step reads `feature_flags.fanout_child_model`
        # for `--model` rather than hardcoding it.
        "feature_flags": _feature_flags_view(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("packet_path", help="Path to the /next-up packet markdown")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = ap.parse_args(argv)

    packet = Path(args.packet_path)
    if not packet.is_absolute():
        packet = (_workspace_root() / packet).resolve()
    if not packet.exists():
        print(json.dumps({
            "error": "packet-not-found",
            "path": str(packet),
        }), file=sys.stderr)
        return 2

    try:
        ctx = build_context(packet)
    except Exception as e:  # pragma: no cover
        print(json.dumps({
            "error": f"build-failed: {type(e).__name__}: {e}",
        }), file=sys.stderr)
        return 1

    # Ensure UTF-8 on Windows where stdout defaults to cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if args.pretty:
        print(json.dumps(ctx, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(ctx, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
