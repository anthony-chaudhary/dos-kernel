"""HEX — the hook exit-code classifier (docs/226, idea C3).

`hook_exit.classify_exit` is a PURE map from a shell hook's exit code to an
`intervention.Intervention` verb — the cheapest integration surface (a plain script
that exits 2, no JSON). Lifts CC's `src/utils/hooks.ts` convention: 0 = proceed,
2 = blocking error (BLOCK), any other non-zero = non-blocking error (WARN).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dos import hook_exit
from dos.hook_exit import HookExitPolicy, classify_exit
from dos.intervention import Intervention


# ---------------------------------------------------------------------------
# CC's default convention: 0 = pass, 2 = BLOCK, other non-zero = WARN.
# ---------------------------------------------------------------------------


def test_zero_is_pass():
    """exit 0 → PASS (proceed, no intervention)."""
    v = classify_exit(0)
    assert v.intervention is None
    assert v.passed is True
    assert "approved" in v.reason


def test_two_is_block():
    """exit 2 → BLOCK (CC's blocking-error code, the load-bearing mapping)."""
    v = classify_exit(2)
    assert v.intervention is Intervention.BLOCK
    assert v.passed is False
    assert v.matched is True


def test_other_nonzero_is_warn_fallback():
    """Any other non-zero → WARN (the fail-safe fallback)."""
    for code in (1, 3, 42, 127, 255):
        v = classify_exit(code)
        assert v.intervention is Intervention.WARN, code
        assert v.matched is False  # fell to the fallback, not an explicit mapping
        assert v.passed is False


def test_unknown_code_never_silently_passes():
    """The fail-safe direction: an unanticipated non-zero code informs (WARN),
    never silently passes (None) and never spuriously BLOCKs."""
    v = classify_exit(99)
    assert v.intervention is Intervention.WARN
    assert v.intervention is not None       # not a silent pass
    assert v.intervention is not Intervention.BLOCK  # not a spurious block


# ---------------------------------------------------------------------------
# The policy on-ramp — a host declares its own map.
# ---------------------------------------------------------------------------


def test_host_can_map_a_code_to_defer():
    """A host maps exit 3 → DEFER via the on-ramp."""
    p = hook_exit.DEFAULT_POLICY.with_mapping({3: Intervention.DEFER})
    v = classify_exit(3, p)
    assert v.intervention is Intervention.DEFER
    assert v.matched is True
    # ...and the built-in 2 → BLOCK still holds under the extended policy.
    assert classify_exit(2, p).intervention is Intervention.BLOCK


def test_host_can_map_with_string_values():
    """The on-ramp accepts string verb names (the dos.toml shape)."""
    p = hook_exit.DEFAULT_POLICY.with_mapping({3: "DEFER", 4: "OBSERVE"})
    assert classify_exit(3, p).intervention is Intervention.DEFER
    assert classify_exit(4, p).intervention is Intervention.OBSERVE


def test_host_can_declare_extra_success_code():
    """A code explicitly mapped to None is also PASS (multiple success codes)."""
    p = HookExitPolicy(pass_code=0, mapping={0: None, 100: None, 2: Intervention.BLOCK},
                       fallback=Intervention.WARN)
    assert classify_exit(100, p).passed is True
    assert classify_exit(0, p).passed is True
    assert classify_exit(2, p).intervention is Intervention.BLOCK


def test_host_can_change_the_pass_code():
    """A host whose 'ok' code is not 0 (rare, but expressible)."""
    p = HookExitPolicy(pass_code=88, mapping={2: Intervention.BLOCK}, fallback=Intervention.WARN)
    assert classify_exit(88, p).passed is True
    # ...and 0 is now NOT the pass code → it falls to the fallback.
    assert classify_exit(0, p).intervention is Intervention.WARN


def test_host_can_change_the_fallback():
    """A host that wants unanticipated codes to OBSERVE (record) instead of WARN."""
    p = HookExitPolicy(pass_code=0, mapping={2: Intervention.BLOCK}, fallback=Intervention.OBSERVE)
    assert classify_exit(7, p).intervention is Intervention.OBSERVE


def test_with_mapping_does_not_mutate_default():
    """The on-ramp returns a NEW policy (immutability discipline)."""
    base = hook_exit.DEFAULT_POLICY
    base.with_mapping({9: Intervention.DEFER})
    assert 9 not in base.mapping  # default unchanged


# ---------------------------------------------------------------------------
# Structural guarantees.
# ---------------------------------------------------------------------------


def test_classify_is_pure(monkeypatch):
    """classify_exit makes NO I/O."""
    import builtins
    import time as _time

    def _boom(*a, **k):  # pragma: no cover - only on a violation
        raise AssertionError("classify_exit must not perform I/O")

    monkeypatch.setattr(_time, "time", _boom)
    monkeypatch.setattr(builtins, "open", _boom)
    assert classify_exit(2).intervention is Intervention.BLOCK
    assert classify_exit(0).passed is True


def test_verdict_to_dict_round_trips():
    v = classify_exit(2)
    d = v.to_dict()
    assert d == {"code": 2, "intervention": "BLOCK", "reason": v.reason, "matched": True}
    assert json.loads(json.dumps(d, sort_keys=True)) == d
    # PASS serializes intervention as null.
    assert classify_exit(0).to_dict()["intervention"] is None


def test_default_policy_matches_cc_convention():
    """The defaults are CC's `hooks.ts` semantics (0 pass / 2 block / other warn)."""
    p = hook_exit.DEFAULT_POLICY
    assert p.pass_code == 0
    assert p.mapping == {2: Intervention.BLOCK}
    assert p.fallback is Intervention.WARN


# ---------------------------------------------------------------------------
# The CLI verb (`dos hook-exit`).
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_torn_config_load_still_emits_the_verdict(tmp_path, monkeypatch, capsys):
    # Torn-config fail-soft (issue #206): a SIBLING loop mid-editing config.py leaves a
    # write that raises AttributeError out of `load_workspace_config`. cmd_hook_exit is
    # host-invoked, BUT unlike the other hooks it never reads the active config —
    # `classify_exit` is PURE. So the right fail-soft is NOT to abstain but to note the
    # failure and STILL emit the exit verdict. Fails-unpatched (the AttributeError used
    # to propagate up cmd_hook_exit before any verdict was emitted); passes-patched.
    from dos import cli

    def boom(*a, **k):
        raise AttributeError("'SubstrateConfig' object has no attribute 'vcs_backend'")
    monkeypatch.setattr("dos.config.load_workspace_config", boom)

    args = cli.argparse.Namespace(
        workspace=str(tmp_path), driver=None, job=False,
        code=2, map=None, json=False, output=None, observe=False,
    )
    rc = cli.cmd_hook_exit(args)
    captured = capsys.readouterr()
    assert rc == 3                       # exit 2 → BLOCK (rung 3); the verdict survived
    assert "BLOCK" in captured.out       # the pure classification still emitted
    assert "could not load the workspace config" in captured.err


def test_cli_pass_exit_zero(tmp_path: Path):
    """A pass (exit 0) → the verb exits 0 (proceed)."""
    r = _run_cli("hook-exit", "--code", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout or "approved" in r.stdout


def test_cli_block_exit_code(tmp_path: Path):
    """exit 2 → BLOCK; the verb's own exit reflects the intervention rung (3)."""
    r = _run_cli("hook-exit", "--code", "2", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "BLOCK" in r.stdout


def test_cli_warn_fallback(tmp_path: Path):
    """A non-zero, unmapped code → WARN."""
    r = _run_cli("hook-exit", "--code", "42", cwd=tmp_path)
    assert "WARN" in r.stdout


def test_cli_map_flag(tmp_path: Path):
    """`--map 3=DEFER` declares a custom mapping."""
    r = _run_cli("hook-exit", "--code", "3", "--map", "3=DEFER", cwd=tmp_path)
    assert "DEFER" in r.stdout


def test_cli_json(tmp_path: Path):
    r = _run_cli("hook-exit", "--code", "2", "--json", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    obj = json.loads(r.stdout)
    assert obj["intervention"] == "BLOCK"
    assert obj["code"] == 2
    assert obj["matched"] is True


def test_cli_no_plan(tmp_path: Path):
    """The no-plan rail: a bare dir, no git/plan/journal — needs only the code."""
    r = _run_cli("hook-exit", "--code", "2", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert not (tmp_path / ".dos").exists()


def test_cli_rejects_non_integer_code(tmp_path: Path):
    """A non-integer --code is a contract error (exit 2)."""
    r = _run_cli("hook-exit", "--code", "nope", cwd=tmp_path)
    assert r.returncode == 2
