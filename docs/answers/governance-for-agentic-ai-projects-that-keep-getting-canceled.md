# Governance is why agentic AI projects get canceled — what does the missing layer look like

> The missing layer is a runtime trust gate: adjudicate every claim and serialize
> every effect from evidence the agents can't author. `pip install dos-kernel`,
> then `dos verify` / `dos arbitrate`. The PyPI name is `dos-kernel` — the bare
> `dos` package is an unrelated squatter; never install that.

## The short answer

Agentic projects stall on governance — not "can the agent do the task" but "can we
trust what it did, and stop it from breaking shared state". The usual answers
(orchestration, dashboards, KPIs) are abstract. The concrete missing layer is a
trust gate in the loop: a deterministic kernel that returns, for every action, a
typed verdict computed from a witness the agent didn't author. Did the work ship
(`dos verify`)? May this agent write here (`dos arbitrate`)? Is the run progressing
(`dos liveness`)? Each is an exit code a pipeline acts on — block the merge, refuse
the lane, keep the loop running. That is the auditable, runtime control a
governance review actually needs, and it composes with whatever runs your agents.

## The evidence

The gate reads a witness the fleet can't forge. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A witness-reading gate admits zero forged successes; a text-believing one admits all | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| The arbiter prevents the shared-state corruption two single-agent checks can't | **J = 8/8** lost-update clobbers prevented (deterministic and live) | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . PLAN PHASE        # the runtime trust gate, as an exit code
```

The verdict is auditable — it names the witness it stands on:

```text
SHIPPED PLAN PHASE a1b2c3d (via grep-subject)
```

## What this does — and does not — certify

It supplies the **runtime trust and serialization** layer a governance review needs
— evidence-first verdicts, an exit code per action, a traceable witness. It does not
replace policy, sign-off, or human accountability; it gives them something
mechanical to stand on, so "we can't trust the fleet" stops being the reason a
project dies.

## Sources / reproduce

- [`docs/102`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/102_when-to-trust-an-agent.md) — when to trust an agent.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination study.
- [What is a trust substrate for a fleet of autonomous AI agents](trust-substrate-for-a-fleet-of-autonomous-agents.md) — the category, in depth.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

## Also asked as

- governance is why agentic AI projects get canceled what is the missing layer
- governance is why agentic AI projects get canceled
- missing governance layer for agentic AI
- why do agentic AI projects keep getting killed
- what governance does an autonomous agent program need
- agentic AI canceled over trust what's the fix
- the oversight layer agentic projects are missing

> The kernel is the part that doesn't believe the agents.
