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


def _decide_with(event: dict, leases: list[dict], runtime_files: tuple[str, ...]):
    """Run `pretool_sensor.decide`'s two rungs with INJECTED leases + runtime files,
    bypassing the WAL/FS I/O so the corpus is hermetic.

    This mirrors decide() exactly but supplies the predicates with the injected
    runtime-file set and the injected live leases, so the Go test (which injects the
    same) is compared against the same logic the live hook runs.
    """
    cfg = _config.active()
    # Rung A with injected runtime files (the existence probe result) + injected leases.
    tree, tree_known = prt._tree_from_event(event)
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
        if provable:
            return prt.deny_payload(f"DOS PRE-admission: {reason}"), "deny"
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


def case(name: str, event: dict, leases: list[dict], runtime_files: tuple[str, ...]) -> dict:
    dialect, tag = _decide_with(event, leases, runtime_files)
    return {
        "name": name,
        "event": event,
        "leases": leases,
        "runtime_files": list(runtime_files),
        "expected_stdout": _render(dialect),
        "decision": tag,
    }


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
    return cases


def main() -> int:
    for c in build_cases():
        sys.stdout.write(json.dumps(c, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
