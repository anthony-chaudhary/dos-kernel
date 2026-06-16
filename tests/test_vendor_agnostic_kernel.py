"""The kernel does not read who you are — vendor-agnosticism, pinned.

DOS's premise (CLAUDE.md) is a kernel that adjudicates ground truth "without
believing what they say they did." A corollary the marketing rests on: it does
not matter whether the self-narrating worker is Claude, Gemini, Codex, or a shell
script. The benchmark proves this *behaviorally* over a whole fleet
(`benchmark/fleet_horizon/test_vendors.py`); these tests prove it *structurally*
at the syscall boundary — the stronger statement, because it shows there is no
CHANNEL through which a vendor identity could even enter a decision:

  1. **No identity parameter.** `arbiter.arbitrate` and its `AdmissionRequest`
     datum, and `oracle.is_shipped`, take a footprint / a claim — never an
     "agent", "vendor", "model", or "who". A coupling cannot exist where there is
     no parameter to carry it.
  2. **Free-text identity is ignored.** Where an identity CAN ride along — a
     lease's free-text `holder` / `effort` fields — swapping it between vendors
     does not move the arbiter's verdict. The kernel reasons over lane + tree, not
     the label.
  3. **No vendor literal in kernel code.** No module under `src/dos/` (except the
     `drivers/` layer, whose whole job is to reach an external adjudicator) names
     a vendor as CODE (an identifier or attribute). Vendor names appear only in
     comments/docstrings as illustrative examples (`claude -p`), never in a branch.

Together: the kernel is vendor-blind by construction, not by configuration.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import dos
from dos import arbiter, oracle
from dos.admission import AdmissionRequest
from dos.config import LaneTaxonomy, default_config


# Concepts that would represent "which agent/vendor/model is acting." If any of
# these is a parameter or dataclass field of a decision syscall, identity could
# leak into a verdict. The point is that NONE of them is.
_IDENTITY_CONCEPTS = {
    "agent", "vendor", "model", "actor", "who", "identity", "author",
    "claude", "gemini", "codex", "openai", "anthropic", "gpt", "provider",
}

# Vendor tokens that must never appear as CODE in a non-driver kernel module.
_VENDOR_TOKENS = {"claude", "gemini", "codex", "openai", "anthropic", "gpt"}


# --------------------------------------------------------------------------- #
# 1. the decision syscalls carry no identity parameter
# --------------------------------------------------------------------------- #

def test_arbitrate_signature_has_no_identity_parameter():
    """`arbitrate` decides admission from (requested lane/kind/tree, live leases,
    config). It has NO parameter naming an agent/vendor/model — so a caller cannot
    even pass "this is Gemini" into the decision. The agnosticism is enforced by
    the absence of a channel, which is stronger than 'we choose to ignore it.'"""
    params = set(inspect.signature(arbiter.arbitrate).parameters)
    leaked = {p for p in params if any(c in p.lower() for c in _IDENTITY_CONCEPTS)}
    assert not leaked, f"arbitrate grew an identity parameter: {leaked}"


def test_admission_request_has_no_identity_field():
    """The pure datum a predicate sees is the FOOTPRINT — where the work touches
    (`lane`/`kind`/`tree`) and what the call DOES (`command`/`arg_values`, the
    agent-authored argument bytes the docs/364 call-shape predicate matches) —
    never the footprint's AUTHOR. A workspace predicate therefore cannot branch on
    vendor; it sees only what the work touches and what it would do, never WHO is
    doing it. The load-bearing guard is the identity-leak check below; the positive
    set just pins the known footprint fields so a NEW field is a deliberate review."""
    fields = {f for f in getattr(AdmissionRequest, "__dataclass_fields__", {})}
    leaked = {f for f in fields if any(c in f.lower() for c in _IDENTITY_CONCEPTS)}
    assert not leaked, f"AdmissionRequest grew an identity field: {leaked}"
    # and positively: it is exactly the footprint fields — the where-it-touches
    # triple plus the what-it-does pair (docs/364). `command`/`arg_values` are the
    # CALL's own bytes (content), not identity: a predicate reads what the call
    # would do, still never who proposed it.
    assert fields == {"lane", "kind", "tree", "command", "arg_values"}


def test_is_shipped_signature_has_no_identity_parameter():
    """The truth syscall verifies a (plan, phase) against ground truth. WHO claimed
    it ships is irrelevant — there is no agent/vendor parameter, so the verdict is
    a function of git + registry, never of the claimant's identity."""
    params = set(inspect.signature(oracle.is_shipped).parameters)
    leaked = {p for p in params if any(c in p.lower() for c in _IDENTITY_CONCEPTS)}
    assert not leaked, f"is_shipped grew an identity parameter: {leaked}"


# --------------------------------------------------------------------------- #
# 2. free-text identity, where it CAN ride along, does not move the verdict
# --------------------------------------------------------------------------- #

def _arbitrate_with_holder(vendor: str):
    """Arbitrate a colliding keyword request against a live lease whose free-text
    `holder`/`effort` is tagged with `vendor`. The collision is real (overlapping
    `shared/` tree), so the verdict should be 'refuse' REGARDLESS of the vendor
    tag — the arbiter reasons over the tree overlap, not the holder string."""
    cfg = default_config(workspace=Path("."))
    cfg = dataclasses.replace(cfg, lanes=LaneTaxonomy(
        concurrent=("lane-a", "lane-b"), autopick=("lane-a", "lane-b"),
        exclusive=(), trees={"lane-a": ("a/", "shared/"),
                             "lane-b": ("b/", "shared/")},
    ))
    live = [{
        "lane": "lane-a", "lane_kind": "keyword",
        "tree": ["a/x.txt", "shared/r.txt"],
        # identity rides in free-text fields the arbiter does NOT consult:
        "holder": f"{vendor}-worker", "effort": f"{vendor}-effort",
    }]
    return arbiter.arbitrate(
        requested_lane="lane-b", requested_kind="keyword",
        requested_tree=["b/y.txt", "shared/r.txt"],   # collides on shared/r.txt
        live_leases=live, config=cfg,
    )


def test_arbiter_verdict_identical_across_vendor_holders():
    """The arbiter's decision is byte-identical whether the live lease is held by a
    claude-, gemini-, or codex-tagged worker. Identity rides in free-text fields it
    never reads; the verdict is a pure function of the tree overlap."""
    decisions = {v: _arbitrate_with_holder(v).to_dict()
                 for v in ("claude", "gemini", "codex")}
    # every vendor refuses (the collision is real) ...
    assert all(d["outcome"] == "refuse" for d in decisions.values()), decisions
    # ... and the decisions are identical to one another except for nothing: the
    # arbiter never echoes the holder, so the dicts match exactly.
    assert decisions["claude"] == decisions["gemini"] == decisions["codex"]


def test_oracle_verdict_identical_across_vendor_claimants():
    """The same (plan, phase) claim verifies to the same verdict no matter which
    vendor 'made' the claim — because the registry row carries no claimant. We
    stamp a vendor onto the SURROUNDING bookkeeping and confirm `is_shipped` is
    unmoved: it reads `status: done`, not `claimant`."""
    verdicts = {}
    for vendor in ("claude", "gemini", "codex"):
        registry = {"recently_completed": [
            # a vendor-tagged row — the extra key is bookkeeping the oracle ignores.
            {"plan": "P", "phase": "P.01", "status": "done",
             "commit_sha": "abc1234", "claimed_by": vendor},
        ]}
        v = oracle.is_shipped("P", "P.01", state=registry,
                              grep_fallback=lambda p, ph: oracle.ShipVerdict(
                                  plan=p, phase=ph, shipped=False, source="none"))
        verdicts[vendor] = (v.shipped, v.source)
    # identical verdict for every vendor — the claimant tag changed nothing.
    assert verdicts["claude"] == verdicts["gemini"] == verdicts["codex"]
    assert verdicts["claude"] == (True, "registry")


# --------------------------------------------------------------------------- #
# 3. no vendor literal as CODE in a non-driver kernel module
# --------------------------------------------------------------------------- #

def _kernel_modules() -> list[Path]:
    """Top-level kernel modules — `src/dos/*.py`, EXCLUDING the `drivers/` layer.

    A driver's job is to reach an external adjudicator, so `drivers/llm_judge.py`
    legitimately names `claude -p` in a docstring example; the kernel proper must
    not name a vendor in CODE at all."""
    core_dir = Path(dos.__file__).parent
    return sorted(core_dir.glob("*.py"))   # glob (not rglob) → excludes drivers/


def _code_identifiers(tree: ast.AST) -> set[str]:
    """Every identifier USED AS CODE in the module: Name ids and Attribute attrs.

    Deliberately NOT string literals or docstrings — vendor names appear there as
    illustrative prose (`claude -p`, "an LLM adjudicator"), which is documentation,
    not a branch. A real coupling would be `if vendor == 'claude'` (a Name/compare)
    or `client.gemini` (an Attribute), which this DOES catch."""
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr.lower())
    return ids


# The ONE principled exception (docs/217): the hook-dialect SEAM may name the
# `claude-code` DEFAULT renderer as code — and ONLY that token, ONLY in that module.
# Rationale: DOS's own sensors emit a Claude-Code-shaped dict as the dialect-NEUTRAL
# lingua franca, so `ClaudeCodeDialect` is the kernel's unshadowable baseline (the
# `AbstainJudge` analogue), not an adjudication branch on "who is acting." Every
# OTHER vendor renderer (codex/gemini/cursor) lives in `drivers/hook_dialects.py` and
# is discovered by name — so the kernel still names no vendor it could *decide* on.
_VENDOR_CODE_EXCEPTIONS = {
    "hook_dialect.py": {"claude"},  # the claude-code default ONLY (see above)
    # The install-side sibling (docs/221): `hook_install.py` names the `claude-code`
    # DEFAULT install-spec (`claude_code_spec`, `DEFAULT_HOST`) for the identical
    # reason — it is the unshadowable baseline DOS's own sensors emit, not an
    # adjudication branch. Every OTHER host's install-spec (cursor/codex/gemini)
    # lives in `drivers/hook_dialects.py` and is discovered by name through the
    # `dos.hook_installs` entry-point group, so the kernel still names no vendor it
    # could *decide* on. Same `claude`-only allowance as the dialect seam.
    "hook_install.py": {"claude"},
}


def test_no_kernel_module_names_a_vendor_in_code():
    """No non-driver kernel module uses a vendor name as an identifier or attribute.

    The grep-style litmus from CLAUDE.md ("kernel imports no host"), at the vendor
    level: vendor names may appear in comments/docstrings (examples) but never as
    code — so no kernel decision can branch on which vendor is acting. The sole
    allowance is the `claude-code` DEFAULT renderer in the hook-dialect seam (see
    `_VENDOR_CODE_EXCEPTIONS`); the swappable per-vendor renderers are a driver."""
    offenders: list[str] = []
    for py in _kernel_modules():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        used = _code_identifiers(tree)
        hit = {tok for tok in _VENDOR_TOKENS
               if any(tok == ident or f"_{tok}" in ident or f"{tok}_" in ident
                      for ident in used)}
        hit -= _VENDOR_CODE_EXCEPTIONS.get(py.name, set())
        if hit:
            offenders.append(f"{py.name}: {sorted(hit)}")
    assert offenders == [], f"kernel modules name a vendor in code: {offenders}"


def test_no_kernel_module_imports_a_driver():
    """No non-driver kernel module may IMPORT `dos.drivers` (the CLAUDE.md litmus:
    "the kernel never imports a judge implementation").

    The JUDGE rung's adjudicators (and host policy packs) live under `drivers/`; the
    kernel's `dos.judges` seam holds only the pure protocol + resolver, and discovers
    a ruling judge by NAME at the call boundary — it never imports one. This walks the
    import statements (not docstrings, so a comment mentioning `drivers/__init__` does
    not trip it) and asserts no kernel module pulls a driver in. The one-way arrow,
    AST-checked, the same shape as the no-vendor-in-code litmus above."""
    offenders: list[str] = []
    for py in _kernel_modules():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "dos.drivers" or alias.name.startswith("dos.drivers."):
                        offenders.append(f"{py.name}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "dos.drivers" or mod.startswith("dos.drivers."):
                    offenders.append(f"{py.name}:{node.lineno}: from {mod} import …")
    assert offenders == [], f"kernel modules import a driver: {offenders}"


def test_vendor_names_only_appear_in_prose_not_branches():
    """Belt-and-suspenders: confirm the vendor tokens that DO appear in kernel
    modules (e.g. `claude -p` in run_id.py's docstring) are confined to string
    literals / docstrings / comments — never a comparison operand.

    We walk every `Compare` node and assert no constant operand is a vendor token,
    so a future `if model == 'gemini':` is caught even though the string lives in
    code (a Compare), while a docstring mentioning gemini stays fine."""
    offenders: list[str] = []
    for py in _kernel_modules():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for op in operands:
                if isinstance(op, ast.Constant) and isinstance(op.value, str):
                    low = op.value.lower()
                    if any(tok in low for tok in _VENDOR_TOKENS):
                        offenders.append(f"{py.name}:{node.lineno}: compares to {op.value!r}")
    assert offenders == [], f"kernel compares against a vendor literal: {offenders}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
