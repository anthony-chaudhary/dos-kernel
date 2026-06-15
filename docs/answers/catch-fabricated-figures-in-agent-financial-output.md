# How to catch fabricated figures in an AI agent's financial model output

> Grade the recomputed value, not the asserted one: `pip install dos-kernel`,
> then the `formula_recompute` witness re-evaluates every cell. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that. (Run `dos doctor` to list the witnesses your install exposes.)

## The short answer

An agent building a financial model can paste a hand-typed number where a formula
belongs, balance a sheet with a fabricated plug, or hide the workaround in
white-font rows — a model that "appears complete but cannot be updated". The agent
authors the stored value of every cell, so the stored value is the forgeable
floor. The defense is a deterministic engine that re-evaluates every formula from
its precedents and compares: a stored value that disagrees with its recompute is
a forgery, flagged off a quantity the agent did not author. DOS ships this as the
`formula_recompute` derived witness on its `effect_witness` join — grade the
recomputed quantity, never the asserted one.

## The evidence

The forgeries are injected (so recall and false-refute are exact). Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated figures (static-value masquerade, fabricated-balance, plug) are flagged | detect recall **100.0% (24/24 forged blocked)** | the recomputed value (`OS_RECORDED`), re-evaluated from precedents | [`benchmark/finmodel/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/finmodel/RESULTS.md) |
| Clean, auditable models are not wrongly flagged | false-refute **0.0% (0/8 clean blocked)** | the same recompute over an honest model | [`benchmark/finmodel/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/finmodel/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta — here, the 24 synthesized forgeries a recompute witness refused to
vouch for.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json             # the witnesses your install exposes
```

The recompute rung refutes a cell whose stored value its formula doesn't produce:

```text
REFUTED — stored value 1,240 != recompute 0 (the cell is a hand-typed plug)
```

## What this does — and does not — certify

It certifies **mechanical soundness**: every stored value is the value its own
formula produces from its precedents. It does **not** certify the model's
assumptions are reasonable or its forecast is right — a model can be internally
consistent and still wrong about the world. The narrow guarantee: a fabricated
number that breaks the recompute cannot pass as a real one.

## Sources / reproduce

- [`benchmark/finmodel/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/finmodel/RESULTS.md) — the `formula_recompute` derived-witness study.
- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the forgeable floor vs the recomputed rung.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Do AI coding agents lie about what they shipped?](do-ai-coding-agents-lie-about-what-they-shipped.md) — the same forgeability rule on git.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
