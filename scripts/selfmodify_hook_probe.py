#!/usr/bin/env python3
"""selfmodify_hook_probe.py — prove a host's SELF_MODIFY hook BLOCKS, SAFELY.

> **Why this exists.** The obvious way to prove the DOS pre-tool hook refuses a
> self-modifying write is to wire a live agent (Gemini, Cursor, …) to the REAL repo
> and tell it to `Write` to `src/dos/arbiter.py`. That is a TRAP: SELF_MODIFY only
> fires on a path that is *actually a kernel runtime file under the served
> workspace* (a decoy filename PASSES — verified), so the stimulus MUST name a real
> guarded path. If the hook then misses for ANY reason — empty stdin, a dialect the
> host ignores (the docs/268 Gemini fail-open), a crashed subprocess — the write
> LANDS and the kernel is destroyed (`arbiter.py` truncated to the agent's payload),
> breaking every session in the tree. A test must never be one fail-open away from
> deleting the thing it tests.
>
> **The fix is a sacrificial workspace.** This harness builds a THROWAWAY DOS
> workspace in a temp dir — a `dos.toml` plus DUMMY files at the kernel runtime
> paths (`src/dos/arbiter.py`, …) — and points the hook at THAT root
> (`--workspace <tmp>`). SELF_MODIFY fires there exactly as it would on the real
> repo (the guard is workspace-relative: `existing_runtime_files` matches the dummy
> files), but a fail-open can only clobber the disposable copy. The real
> `src/dos/arbiter.py` is never named as a write target on its own tree.

What it proves
==============

For each requested dialect, it feeds the host-shaped pre-tool event for a Write to
`src/dos/arbiter.py` (relative to the sacrificial root) through the REAL
`dos hook pretool` path and asserts the decision is a DENY rendered in that host's
grammar (the same bytes `dos.hook_dialect.resolve_dialect(name).render(...)` emits).
It then PROVES the safety property directly: it records the sha256 of the sacrificial
`arbiter.py`, and — to model a fail-open — writes the agent's payload to it anyway,
confirming only the THROWAWAY changed and the real kernel's sha is untouched.

Usage
=====

    python scripts/selfmodify_hook_probe.py                 # all built-in dialects
    python scripts/selfmodify_hook_probe.py --dialect gemini
    python scripts/selfmodify_hook_probe.py --json          # machine-readable

Exit 0 = every probed dialect produced a blocking deny AND the real kernel was
provably untouched. Exit 1 = a dialect failed to block (a latent fail-open) — the
finding this harness exists to surface, now caught against a dummy instead of live.

This is dev tooling that OPERATES ON the package (it shells `dos`/imports `dos`); it
is not part of the kernel (the "no scripts/ in the kernel" litmus).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import tempfile
from pathlib import Path


# The repo-relative kernel path used as the SELF_MODIFY stimulus. It is guarded ONLY
# because the sacrificial workspace below creates a real (dummy) file here — on a
# foreign root the same path passes, which is the whole point of the workspace-aware
# guard. We deliberately reuse the canonical name so the probe exercises the real
# prefix-collision the guard runs in production.
_STIMULUS_PATH = "src/dos/arbiter.py"

# A marker the dummy kernel files carry so an accidental real-tree write is obvious
# AND so a human grepping a clobbered file sees where it came from. If you ever find
# this string inside the REAL src/dos/arbiter.py, a probe was misconfigured to the
# live tree — restore from HEAD and check the --workspace argument.
_DUMMY_MARKER = "# SACRIFICIAL DUMMY — selfmodify_hook_probe.py throwaway kernel file.\n"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_sacrificial_workspace(root: Path) -> dict[str, str]:
    """Scaffold a throwaway DOS workspace under `root` that TRIPS SELF_MODIFY.

    Writes a minimal `dos.toml` and DUMMY files at every kernel runtime path the
    guard knows, so `existing_runtime_files(root)` returns the full set and a Write
    to `src/dos/arbiter.py` under this root is refused exactly as on the real repo.
    Returns {repo_relative_path: sha256} for the dummy files so a caller can later
    assert which (if any) changed. Nothing here touches the real tree.
    """
    from dos.self_modify import _DISPATCH_RUNTIME_FILES

    (root / "dos.toml").write_text(
        # A generic-enough config; the lane taxonomy is irrelevant to the SELF_MODIFY
        # predicate, which keys off the kernel runtime files existing on disk.
        "[paths]\nplans_glob = \"docs/**/*-plan.md\"\n",
        encoding="utf-8",
    )
    shas: dict[str, str] = {}
    for rel in _DISPATCH_RUNTIME_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # A dummy body — NOT real code. A fail-open overwrite of this is harmless.
        p.write_text(_DUMMY_MARKER + f"# stands in for {rel}\n", encoding="utf-8")
        shas[rel] = _sha256(p)
    return shas


def _kernel_repo_config(root: Path):
    """A SubstrateConfig for `root` with the kernel facts discovered from disk.

    Reuses the real discovery path (`existing_runtime_files`) so the probe's notion
    of "is this the kernel" is identical to production — no hand-faked facts."""
    from dos import config as _config
    from dos.self_modify import existing_runtime_files

    cfg = _config.default_config(root)
    facts = _config.WorkspaceFacts(
        root=root,
        kernel_runtime_files=tuple(existing_runtime_files(root)),
        is_kernel_repo=bool(existing_runtime_files(root)),
    )
    return dataclasses.replace(cfg, workspace=facts)


def probe_dialect(root: Path, dialect: str) -> dict:
    """Run the real pre-tool decision for a Write to the stimulus path, render it in
    `dialect`, and report whether it BLOCKS. Pure-ish: reads the sacrificial root,
    writes nothing to it here (the fail-open simulation is separate)."""
    import os

    from dos import hook_dialect as hd
    from dos import pretool_sensor as prt

    cfg = _kernel_repo_config(root)
    event = {
        "session_id": "selfmodify-probe",
        "tool_name": "Write",
        "tool_input": {"file_path": _STIMULUS_PATH, "content": "x"},
        "cwd": str(root),
    }
    # docs/355 — the probe demonstrates the HARD self-modify guard (the deny that
    # blocks a kernel write for every dialect). That hard deny is now the LOOP-session
    # case: an interactive operator editing the kernel between loop runs softens to a
    # WARN (docs/355 Change 1). Mark this probe a loop session so the guard fires as
    # the proof intends; restore the prior env after, so the probe leaves no trace.
    _saved = os.environ.get("DOS_LOOP")
    os.environ["DOS_LOOP"] = "1"
    try:
        cc_dict, outcome = prt.decide(event, cfg)
    finally:
        if _saved is None:
            os.environ.pop("DOS_LOOP", None)
        else:
            os.environ["DOS_LOOP"] = _saved
    decision = (outcome or {}).get("decision")
    reason_class = (outcome or {}).get("reason_class")

    verdict = hd.parse_cc(cc_dict, moment=hd.HookMoment.PRE)
    rendered = hd.resolve_dialect(dialect).render(verdict)

    # A blocking deny must carry a signal the host actually honors. We accept ANY of
    # the known blocking keys; the per-host CORRECTNESS of which key (e.g. Gemini's
    # `continue:false`, not `decision:deny`) is pinned by test_hook_dialect.py +
    # the Go parity test. Here we only assert "a deny that is not silently empty."
    blocks = (
        decision == "deny"
        and isinstance(rendered, dict)
        and bool(
            {"continue", "permissionDecision", "permission", "decision"}
            & set(rendered.keys())
            or "permissionDecision" in json.dumps(rendered)
        )
    )
    return {
        "dialect": dialect,
        "decision": decision,
        "reason_class": reason_class,
        "rendered": rendered,
        "blocks": bool(blocks),
    }


def prove_real_kernel_untouched(sacrificial_root: Path, dummy_shas: dict[str, str]) -> dict:
    """Model a fail-open and prove the blast radius is the THROWAWAY, not the kernel.

    Records the REAL repo's `src/dos/arbiter.py` sha, then writes the agent payload
    to the SACRIFICIAL arbiter.py (what a missed hook would do). Asserts (a) the
    sacrificial file changed and (b) the real kernel file's sha is identical before
    and after. Returns the evidence. The real-tree path is resolved from THIS file's
    location, never from the probe's workspace arg, so the two can never be confused.
    """
    real_repo_root = Path(__file__).resolve().parent.parent  # scripts/ -> repo root
    real_arbiter = real_repo_root / _STIMULUS_PATH
    real_before = _sha256(real_arbiter) if real_arbiter.exists() else ""

    sac_arbiter = sacrificial_root / _STIMULUS_PATH
    # The fail-open: overwrite the SACRIFICIAL kernel file with the agent's payload.
    sac_arbiter.write_text("x", encoding="utf-8")
    sac_changed = _sha256(sac_arbiter) != dummy_shas.get(_STIMULUS_PATH, "")

    real_after = _sha256(real_arbiter) if real_arbiter.exists() else ""
    return {
        "real_arbiter": str(real_arbiter),
        "real_sha_before": real_before,
        "real_sha_after": real_after,
        "real_untouched": real_before == real_after,
        "sacrificial_clobbered": bool(sac_changed),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dialect", default=None,
        help="probe one dialect (default: every built-in + discovered dialect)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    from dos import hook_dialect as hd

    dialects = [args.dialect] if args.dialect else list(hd.available_dialects())

    with tempfile.TemporaryDirectory(prefix="dos-selfmodify-probe-") as td:
        root = Path(td)
        dummy_shas = build_sacrificial_workspace(root)
        results = [probe_dialect(root, d) for d in dialects]
        safety = prove_real_kernel_untouched(root, dummy_shas)

    all_block = all(r["blocks"] for r in results)
    ok = all_block and safety["real_untouched"] and safety["sacrificial_clobbered"]

    if args.json:
        print(json.dumps(
            {"ok": ok, "results": results, "safety": safety}, sort_keys=True, indent=2))
    else:
        print("SELF_MODIFY hook probe — against a SACRIFICIAL workspace (real kernel safe)")
        for r in results:
            mark = "✓ blocks" if r["blocks"] else "✗ FAIL-OPEN"
            print(f"  {r['dialect']:14} {mark}  "
                  f"decision={r['decision']} reason_class={r['reason_class']}")
        print()
        s = safety
        print(f"  real kernel  {'✓ UNTOUCHED' if s['real_untouched'] else '✗ MUTATED!!'}  "
              f"({s['real_arbiter']})")
        print(f"  fail-open    {'✓ hit the throwaway only' if s['sacrificial_clobbered'] else '? no write'}")
        print()
        print("OK" if ok else "FAILED — a dialect did not block (latent fail-open)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
