#!/usr/bin/env python3
"""Generate the GHF differential parity corpus (docs/125 GHF3).

The corpus is a JSONL where each line is a self-contained parity case:

    {
      "name": "...",                 # human label
      "event": {...},                # the CC PreToolUse event (stdin to the hook)
      "leases": [{"lane","tree"},…], # the live leases the decider sees (injected)
      "runtime_files": [...],        # the runtime files that "exist" (injected)
      "expected_stdout": "...",      # the EXACT bytes the Python decider emits
      "decision": "deny|warn|passthrough"   # the gated projection tag
    }

`expected_stdout` is produced by calling `pretool_sensor.decide` with the SAME
injected inputs the Go test will inject — NOT by reading a real WAL/FS — so the
case is hermetic and reproducible. This is the ORACLE side of the differential
gate: the Go `parity_test.go` replays each case through the native decider and
asserts byte-equality on `expected_stdout`, and the pytest asserts the Python
decider reproduces it (a regression tripwire on the Python side too).

The byte-exactness is over the EMITTED DIALECT (decision + structured reason);
docs/124 §2 keeps the reason PROSE carried-not-separately-gated, but because the
hook's reason is pure int/enum/path prose (no shortest-float — the only ratio is
`:.0%`, which agrees cross-engine, docs/124 §1.1), we can and do gate the whole
emitted line here. The one case that exercises the ratio float (`refuse_overlap`)
is included so the percentage formatting is pinned cross-engine.

Run: python gen_corpus.py > corpus.jsonl
"""
from __future__ import annotations

import json
import sys
from typing import Any

# Import the kernel decider + the injected-input shims. This script runs from a
# checkout with `dos` importable (pip install -e .).
from dos import pretool_sensor as prt
from dos import config as _config
from dos import admission as _admission
from dos.self_modify import SelfModifyPredicate, _DISPATCH_RUNTIME_FILES


def _render(dialect: dict | None) -> str:
    """The EXACT bytes cli.cmd_hook_pretool prints (or '' for passthrough)."""
    if dialect is None:
        return ""
    return json.dumps(dialect, sort_keys=True)


def _decide_with(event: dict, leases: list[dict], runtime_files: tuple[str, ...],
                 override=None, now=None, operator_session=False):
    """Run `pretool_sensor.decide`'s two rungs with INJECTED leases + runtime files,
    bypassing the WAL/FS I/O so the corpus is hermetic.

    This mirrors decide() exactly but supplies the predicates with the injected
    runtime-file set and the injected live leases, so the Go test (which injects the
    same) is compared against the same logic the live hook runs.

    `override` (an `override_facts.OverrideFacts` or None) + `now` inject the operator's
    armed window hermetically — the same disposition `pretool_sensor.decide` runs at the
    enforcement boundary (docs/296), so the Go test (which injects the same facts+clock)
    is gated byte-exact on the override-admit path too. None ⇒ today's deny stands.
    """
    from dos import override_facts as _ovr
    cfg = _config.active()
    # Rung A with injected runtime files (the existence probe result) + injected leases.
    tree, tree_known = prt._tree_from_event(event)

    # The arm-path write PERIMETER (docs/296), before admission — byte-twinned with
    # `pretool_sensor.decide`: an agent write touching `.dos/override/` is denied
    # outright and never disposed.
    if tree and prt.is_mutating_tool(event) and _ovr.touches_arm_path(tree):
        reason = (
            f"this call would write the operator's SELF_MODIFY override arm file "
            f"({_ovr.ARM_RELPATH}) — only the operator arms a window, by hand "
            f"(docs/296). `dos override status` reports it; `dos override disarm` "
            f"is always allowed."
        )
        return prt.deny_payload(f"DOS PRE-admission: {reason}"), "deny"

    request = _admission.AdmissionRequest(
        lane=str(event.get("tool_name") or "tool"), kind="tool-call", tree=tree,
    )
    predicates = [
        _admission.DisjointnessPredicate(),
        SelfModifyPredicate(runtime_files=runtime_files),
    ]
    averdict = _admission.run_predicates(predicates, request, leases, cfg)
    if not averdict.admitted:
        reason = averdict.reason or "DOS admission refused this call (no lane available)."
        # MUST mirror `pretool_sensor.decide` (issue #14): the hook surface swaps
        # the SELF_MODIFY predicate's CLI-only `--force` tail for the remedies
        # that exist at this boundary. The Go decider applies the same swap
        # (`hookSurfaceReason`), so the corpus pins it cross-engine.
        reason = prt.hook_surface_reason(reason, averdict.reason_class or "")
        # MUST mirror `pretool_sensor.decide`'s gate exactly (FQ-532 Defect 3): a
        # refusal is provable (→ deny) only with a typed reason_class OR a real overlap
        # on a KNOWN **and non-empty** tree. A contention-only refusal — including a
        # read's known-but-EMPTY tree — stays ADVISORY regardless of tree_known.
        provable = bool(averdict.reason_class) or (tree_known and bool(tree))
        # docs/296 — the operator's armed override window, consulted at the ENFORCEMENT
        # boundary only (the SELF_MODIFY verdict is unchanged). Only a SELF_MODIFY
        # refusal is ever converted. Byte-twinned with `pretool_sensor.decide`'s
        # override-admit branch; the Go decider injects the same facts+now.
        # docs/355 — soften the interactive NO-LOOP case to an advisory WARN. MUST
        # mirror `pretool_sensor.decide`'s docs/355 branch and the Go decider's, and
        # runs BEFORE the override-window dispose (a softened no-loop human needs no
        # arm file). A LOOP session (operator_session=False, the corpus default)
        # falls through to the override/deny path below, unchanged.
        if provable and (averdict.reason_class or "") == "SELF_MODIFY" and operator_session:
            return (
                prt.warn_payload(
                    f"DOS PRE-admission (advisory, operator session): {reason} "
                    f"You are editing the live kernel, but NO dispatch loop is "
                    f"in flight (the mid-flight-rewrite hazard needs a live "
                    f"loop) — you own the blast radius of your own deliberate "
                    f"edit, so DOS warns instead of blocking. A dispatch loop "
                    f"carries the loop env and still gets the hard deny; arm a "
                    f"window (dos override status) to edit under a live loop."
                ),
                "warn",
            )
        expired_note = ""  # issue #159 — set when an armed window has LAPSED
        if provable and (averdict.reason_class or "") == "SELF_MODIFY" and override is not None:
            note = _ovr.dispose(
                averdict.reason_class or "", tuple(tree), override, now=now)
            if note is not None:
                return (
                    prt.warn_payload(
                        f"DOS PRE-admission (operator override): {note} "
                        f"[the refused verdict was: {reason}]"
                    ),
                    "override-admit",
                )
            # issue #159 — an EXPIRED window denies identically to NO window
            # unless we say so. Byte-twinned with `pretool_sensor.decide`'s and
            # the Go decider's expired-note branch (same minute math + phrasing).
            if now is not None and now > override.until:
                _mins = int((now - override.until).total_seconds() // 60)
                _ago = f"{_mins} min ago" if _mins >= 1 else "less than a min ago"
                expired_note = (
                    f" An operator override window WAS armed but EXPIRED at "
                    f"{override.until.isoformat()} ({_ago}) — it lapsed, it was "
                    f"never absent. Re-arm to edit: dos override status."
                )
        if provable:
            return prt.deny_payload(f"DOS PRE-admission: {reason}{expired_note}"), "deny"
        # PROVEN no-footprint (issue #46): a KNOWN-and-EMPTY tree with no reason_class
        # is a read — it cannot collide, so it passes CLEAN (no advisory). MUST mirror
        # `pretool_sensor.decide`; the Go decider applies the identical branch.
        if tree_known and not tree:
            return None, "passthrough"
        return (
            prt.warn_payload(
                f"DOS PRE-admission (advisory): {reason} This call's footprint does not prove a "
                f"collision (an unresolved write footprint is unknown), "
                f"so DOS cannot prove it collides — proceeding, but "
                f"if this call mutates shared state, scope it to a declared path/lane."
            ),
            "warn",
        )
    # Rung B with the default observe handler: always passthrough (PDP-only floor).
    call = prt.toolcall_from_event(event)
    if call is None:
        return None, "passthrough"
    # Default observe handler -> observe -> passthrough (no behavioral deny).
    return None, "passthrough"


# The runtime-file set the corpus injects for "DOS-repo" cases (all present), vs
# "foreign-repo" cases (none present). A test injects whichever the case names.
ALL_RUNTIME = tuple(_DISPATCH_RUNTIME_FILES)
NO_RUNTIME: tuple[str, ...] = ()

CWD = "/work/workspace"  # neutral fixture workspace path (no real machine path)


def case(name: str, event: dict, leases: list[dict], runtime_files: tuple[str, ...],
         override=None, now=None, operator_session=False) -> dict:
    dialect, tag = _decide_with(event, leases, runtime_files, override=override,
                                now=now, operator_session=operator_session)
    out = {
        "name": name,
        "event": event,
        "leases": leases,
        "runtime_files": list(runtime_files),
        "expected_stdout": _render(dialect),
        "decision": tag,
    }
    # docs/355 — the session class the Go test must inject as `in.OperatorSession`.
    # Emitted ONLY when True (an interactive no-loop session that softens a
    # SELF_MODIFY deny to a WARN); absent ⇒ False (a loop session, the default that
    # keeps every pre-355 case byte-identical). The Go side reads this into Inputs.
    if operator_session:
        out["operator_session"] = True
    # The override window the Go test must inject to reproduce this case. Serialized as
    # the arm-file fields (until/reason/scope) + the injected clock — the same data the
    # boundary `ReadOverride` would parse — so the Go side rebuilds OverrideFacts and
    # the exact `now`. Absent ⇒ no window (the common case, byte-identical to before).
    if override is not None:
        out["override"] = {
            "until": override.until.isoformat(),
            "reason": override.reason,
            "scope": list(override.scope),
        }
        out["now"] = now.isoformat()
    return out


def _ev(tool: str, tool_input: dict[str, Any] | None = None, **extra) -> dict:
    e = {"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": CWD, "tool_name": tool}
    if tool_input is not None:
        e["tool_input"] = tool_input
    e.update(extra)
    return e


SRC_LEASE = [{"lane": "src", "tree": ["src/**"], "lane_kind": "cluster",
              "loop_ts": "2026-06-08T00:00", "holder": "other"}]
EXACT_LEASE = [{"lane": "edit", "tree": ["src/dos/cli.py"], "lane_kind": "plan",
                "loop_ts": "2026-06-08T00:00", "holder": "other"}]
EMPTY_TREE_LEASE = [{"lane": "ghost", "tree": [], "lane_kind": "plan",
                     "loop_ts": "2026-06-08T00:00", "holder": "other"}]


def build_cases() -> list[dict]:
    cases: list[dict] = []
    # --- self-modify (request-absolute, no leases) ---
    cases.append(case("selfmodify-edit-arbiter",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME))
    cases.append(case("selfmodify-bash-rm-tree",
                      _ev("Bash", {"command": "rm src/dos/_tree.py"}), [], ALL_RUNTIME))
    cases.append(case("selfmodify-multi-hit-glob",
                      _ev("Bash", {"command": "sed -i s/x/y/ src/dos/*.py"}), [], ALL_RUNTIME))
    cases.append(case("selfmodify-foreign-repo-admits",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], NO_RUNTIME))
    # --- reads never gated ---
    cases.append(case("read-runtime-file-passthrough",
                      _ev("Read", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME))
    # A proven no-footprint read against a CONTENDED lease passes CLEAN (issue #46):
    # a read touches nothing, so it cannot collide — no advisory, no noise.
    cases.append(case("grep-passthrough",
                      _ev("Grep", {"pattern": "x"}), SRC_LEASE, ALL_RUNTIME))
    cases.append(case("read-contended-lease-passes-clean",
                      _ev("Read", {"file_path": "src/dos/arbiter.py"}), SRC_LEASE, ALL_RUNTIME))
    # --- disjoint edits pass through ---
    cases.append(case("edit-disjoint-doc",
                      _ev("Edit", {"file_path": "docs/notes.md"}), [], ALL_RUNTIME))
    cases.append(case("bash-non-runtime-file",
                      _ev("Bash", {"command": "echo hi > src/dos/cli.py"}), [], ALL_RUNTIME))
    # --- disjointness collisions (need a live lease) ---
    cases.append(case("collision-src-lease-ratio-100",
                      _ev("Edit", {"file_path": "src/dos/cli.py"}), SRC_LEASE, ALL_RUNTIME))
    cases.append(case("collision-exact-glob",
                      _ev("Edit", {"file_path": "src/dos/cli.py"}), EXACT_LEASE, ALL_RUNTIME))
    # --- soft-overlap admit (ratio <= 1/3) ---
    cases.append(case("soft-overlap-admit",
                      _ev("Bash", {"command": "cp src/a.py docs/b.md docs/c.md docs/d.md"}),
                      SRC_LEASE, ALL_RUNTIME))
    # --- WARN-and-pass (unknown tree, refused by a colliding lease) ---
    cases.append(case("warn-unknown-tree-contended",
                      _ev("Bash", {"command": "make build"}), SRC_LEASE, ALL_RUNTIME))
    cases.append(case("warn-write-no-path-contended",
                      _ev("Write", {}), SRC_LEASE, ALL_RUNTIME))
    # --- a mention is not a mutation (issue #12): a no-write-footprint command gets the
    #     read-only posture — a kernel path inside an ARGUMENT is prose, never a deny;
    #     a shell write metacharacter defeats the allowance and still denies. ---
    cases.append(case("mention-gh-issue-body-passthrough",
                      _ev("Bash", {"command": 'gh issue create --body "see src/dos/arbiter.py"'}),
                      [], ALL_RUNTIME))
    cases.append(case("mention-grep-kernel-path-passthrough",
                      _ev("Bash", {"command": "grep -n foo src/dos/arbiter.py"}), [], ALL_RUNTIME))
    cases.append(case("mention-git-log-kernel-path-passthrough",
                      _ev("Bash", {"command": "git log --oneline -- src/dos/arbiter.py"}),
                      [], ALL_RUNTIME))
    cases.append(case("redirect-defeats-mention-allowance",
                      _ev("Bash", {"command": "git log > src/dos/arbiter.py"}), [], ALL_RUNTIME))
    # A no-write-footprint Bash (git status) against a contended lease is a proven
    # no-footprint call too — it passes CLEAN, the same as a Read/Grep (issue #46).
    cases.append(case("read-only-bash-contended-passes",
                      _ev("Bash", {"command": "git status"}), SRC_LEASE, ALL_RUNTIME))
    # --- empty-tree lease never blocks ---
    cases.append(case("empty-tree-lease-admits",
                      _ev("Edit", {"file_path": "src/dos/cli.py"}), EMPTY_TREE_LEASE, ALL_RUNTIME))
    # --- structural PRE guard / malformed ---
    cases.append(case("posttool-event-declined",
                      {"hook_event_name": "PostToolUse", "tool_name": "Read", "cwd": CWD,
                       "tool_response": "data"}, [], ALL_RUNTIME))
    cases.append(case("no-tool-name",
                      {"hook_event_name": "PreToolUse", "cwd": CWD}, [], ALL_RUNTIME))
    cases.append(case("write-no-path-no-lease",
                      _ev("Write", {}), [], ALL_RUNTIME))
    cases.append(case("mcp-unknown-tool-no-lease",
                      _ev("mcp__x__y", {"id": "abc"}), [], ALL_RUNTIME))
    # --- path relativization (absolute path under cwd) ---
    cases.append(case("abs-path-under-cwd-selfmodify",
                      _ev("Edit", {"file_path": "/work/workspace/src/dos/config.py"}), [], ALL_RUNTIME))
    # --- non-ascii in a path-shaped reason (lane name with unicode is unrealistic,
    #     but the em-dash in every self-modify reason exercises ensure_ascii) ---

    # --- docs/296 operator-armed SELF_MODIFY override (the Py↔Go parity fix, #186) ---
    # Hermetic facts + clock (no live arm file is read): a fixed UTC window and a `now`
    # inside / past / scoped, so the Go decider injecting the same reproduces each.
    import datetime as _dt
    from dos import override_facts as _ovr
    until = _dt.datetime(2026, 6, 15, 18, 0, 0, tzinfo=_dt.timezone.utc)
    now_in = _dt.datetime(2026, 6, 15, 17, 30, 0, tzinfo=_dt.timezone.utc)   # inside
    now_past = _dt.datetime(2026, 6, 15, 18, 30, 0, tzinfo=_dt.timezone.utc)  # expired
    armed_unscoped = _ovr.OverrideFacts(until=until, reason="parity fix #186", scope=())
    armed_scoped = _ovr.OverrideFacts(
        until=until, reason="scoped to _tree", scope=("src/dos/_tree.py",))
    # (a) armed, in window, unscoped → a SELF_MODIFY edit is DISPOSED to override-admit.
    cases.append(case("selfmodify-override-armed-in-window",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME,
                      override=armed_unscoped, now=now_in))
    # (b) armed but EXPIRED (now > until) → the deny stands.
    cases.append(case("selfmodify-override-expired",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME,
                      override=armed_unscoped, now=now_past))
    # (c) armed + in window but SCOPED to a different file than the edit → deny stands.
    cases.append(case("selfmodify-override-scope-miss",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME,
                      override=armed_scoped, now=now_in))
    # (d) armed + in window + SCOPED to the edited file → override-admit.
    cases.append(case("selfmodify-override-scope-hit",
                      _ev("Edit", {"file_path": "src/dos/_tree.py"}), [], ALL_RUNTIME,
                      override=armed_scoped, now=now_in))
    # (e) the arm-path write PERIMETER: an agent write to the arm file is denied even
    #     WITH a window armed (a window must not extend itself). Disposition never runs.
    cases.append(case("override-arm-path-write-denied",
                      _ev("Edit", {"file_path": ".dos/override/self-modify.toml"}), [], ALL_RUNTIME,
                      override=armed_unscoped, now=now_in))

    # --- docs/355 the SELF_MODIFY middle ground: soften the interactive no-loop case ---
    # An interactive operator (no loop env => operator_session=True) editing a T1 file
    # gets an advisory WARN, not a hard deny — the mid-flight-rewrite hazard needs a
    # live loop. The loop-session twins of these (operator_session default False) are
    # the existing `selfmodify-edit-arbiter` / `-bash-rm-tree` cases above (still deny).
    cases.append(case("selfmodify-operator-session-edit-warns",
                      _ev("Edit", {"file_path": "src/dos/arbiter.py"}), [], ALL_RUNTIME,
                      operator_session=True))
    cases.append(case("selfmodify-operator-session-bash-warns",
                      _ev("Bash", {"command": "rm src/dos/_tree.py"}), [], ALL_RUNTIME,
                      operator_session=True))
    # An operator-session edit to a DROPPED (post-355-trim) file is not even T1: it
    # passes clean (no warn), proving the trim and the soften compose. `cli.py` was
    # never T1; this pins that a non-runtime edit by an operator is a plain passthrough.
    cases.append(case("operator-session-non-runtime-passthrough",
                      _ev("Edit", {"file_path": "src/dos/cli.py"}), [], ALL_RUNTIME,
                      operator_session=True))
    return cases


def main() -> int:
    for c in build_cases():
        sys.stdout.write(json.dumps(c, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
