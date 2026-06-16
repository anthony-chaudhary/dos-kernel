# Which on-device AI agent models can a guardrail actually recover from a bad action?

> Recoverability rises with competence across the on-device band — a guardrail
> catches a small model's over-claim only once the model is capable enough to read
> before it writes. `pip install dos-kernel`, then `dos commit-audit` / `dos
> verify`. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

If you run a small tool-calling model on a phone or an edge device, you might
expect a guardrail to help *most* on the weakest model. It doesn't. A model too
weak to emit a valid tool call, or one that skips the reads and mints a garbage id
into an empty corpus, produces nothing a witness can catch — the failure is
uncatchable because there's no checkable effect. A guardrail starts to bite only
once the model is competent enough to read the real state and *then* mint a
plausible-but-wrong write — that's when the mint detector fires. So
DOS-recoverability is an inverted-U: it rises with competence across the on-device
band, peaks at the capable on-device tool-callers, and falls again at the frontier
(which rarely makes the catchable mistake). The lesson: a guardrail and a
read-before-write-capable model are complements, not substitutes.

## The evidence

The Qwen2.5-Instruct family driven on CPU over a multi-step ITSM task, native
tool-calling, committed trajectories (reproduces at $0):

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Recoverable fraction rises with model competence | **0% → 0% → 50% → 100%** across Qwen2.5 0.5B → 1.5B → 3B (and SmolLM2-135M 0%) | the env-authored state read-back (a minted foreign id) | [`benchmark/smartphone_tier/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/smartphone_tier/RESULTS.md) |
| The catch needs a read-before-write threshold the model must clear | the 3B reads first every run, mints `USR0010023` on the write → MINT fires every run | the stored corpus the model did not author | [`benchmark/smartphone_tier/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/smartphone_tier/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

The same witness that catches a 3B model's minted id catches an over-claim in any
runtime — what changes with model size is whether the *catchable* mistake gets
made at all.

## What this does — and does not — certify

It certifies where a guardrail's witness can **bite** as a function of model
competence — the recoverable fraction, measured. It does not claim a guardrail
makes a weak model capable; an uncatchable failure (no valid call, an empty
corpus) is still a failure. The point: pair the gate with a model that reads
before it writes.

## Sources / reproduce

- [`benchmark/smartphone_tier/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/smartphone_tier/RESULTS.md) — the on-device recoverability ladder.
- [`benchmark/iot_tier/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/iot_tier/RESULTS.md) — the IoT-tier collapse, the other edge of the inverted-U.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — the witness vs self-report axis.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- which on-device AI agent models can a guardrail actually recover from a bad action
- which on-device agent models can a guardrail recover from a bad action
- recoverable on-device AI agent models
- local agent models a checker can safely catch
- which small models does a guardrail work on
- on-device agent recoverability after a bad action
- edge agent models compatible with a recovery gate

> The kernel is the part that doesn't believe the agents.
