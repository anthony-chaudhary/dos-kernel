"""dos.drivers — the layer where the kernel admits what it cannot itself contain.

The kernel (`dos.oracle`, `dos.arbiter`, `dos.wedge_reason`, …) is pure
*mechanism*: deterministic, I/O-policy-free, host-name-free. A **driver** is the
layer outside that boundary. There are two kinds, and conflating them hides the
interesting one:

  1. **Host policy packs** — the dull kind. The *policy* a particular host repo
     supplies on top of the mechanism: which lanes exist, how they admit
     concurrency, where its plans and ship-state live. Data + thin factory
     functions over `dos.config.SubstrateConfig`. `dos.drivers.job` (the
     reference userland app) is the reference one; its symbols are
     re-exported from `dos.config` for backward compatibility.

  2. **Out-of-kernel adjudicators** — the interesting kind. The kernel adjudicates
     a claim deterministically (`verify`/`picker_oracle`) and ABSTAINS on what it
     cannot mechanically prove. The driver layer is where a *non-deterministic*
     adjudicator — a model judge, a heuristic, a debate — rules on that residue.
     This is the **JUDGE rung** of the trust ladder (ORACLE → JUDGE → HUMAN, see
     `docs/87_the-adjudicator-trust-ladder.md`). `dos.drivers.llm_judge` is the
     reference one. A judge has the surface the kernel forbids (a provider, I/O,
     non-determinism — *a model verifying a model*), which is exactly *why* it lives
     here, hedged by four disciplines: deterministic-first, advisory-only,
     fail-to-abstain, and abstention-first (`dos.judges`).

Both kinds obey the one-way arrow: **they import the kernel; the kernel never
imports them.** Adding a host, or a new adjudicator, means adding a module here OR —
and this is the part that no longer requires touching this package at all — shipping
an entry-point plugin from your OWN pip package. Every seam resolves the same way:
built-ins (the modules here) first and unshadowable, then a `dos.<seam>` entry-point
group discovered by name at the call boundary. The host-policy pack itself is the
`dos.drivers` group (`dos.drivers_seam`, resolved by `dos --driver <name>`); the
adjudicators are `dos.judges`; and the other axes (`dos.predicates`, `dos.renderers`,
`dos.exporters`, `dos.mcp_tools`, …) follow the identical shape. `dos plugins` lists
every seam and what's installed; `dos plugin new <seam>` scaffolds one. The reference
third-party pack is `examples/dos_ext` (its `acme` driver + MCP tool).
"""

from __future__ import annotations

from dos.drivers.job import JOB_LANE_TAXONOMY, job_config
from dos.drivers.workshop import WORKSHOP_LANE_TAXONOMY, workshop_config

__all__ = [
    "JOB_LANE_TAXONOMY", "job_config",
    "WORKSHOP_LANE_TAXONOMY", "workshop_config",
]
