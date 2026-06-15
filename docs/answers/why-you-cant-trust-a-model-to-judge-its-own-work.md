# Why you can't trust an AI model to judge its own work

> The contestant can't be the referee — judge the work with an oracle that reads
> an effect the model didn't author. `pip install dos-kernel`, then `dos verify` /
> `dos improve`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

A model asked to grade its own output has an incentive its grading can't escape:
the same process that wants credit decides whether credit is due. Worse, a fluent
post-hoc judge — even a *different* model reading the trajectory — is swayed by
plausible prose, so it waves through confident-but-false "I did it" narration and
over-flags nervous-but-honest runs. The fix is not a better judge; it's a
deterministic floor *beneath* the judge that reads the stored effect. Where the
prose and the world disagree, the read-back is right by construction. DOS makes
the keep/ship decision a function of that floor — `dos verify` (git ancestry),
`dos improve` (a held-out measurement) — never of the model's self-assessment.

## The evidence

A safety benchmark re-instrumented so the violation is checked by a world-state
read-back instead of the LLM judge — and the two are measured where they
disagree:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Where the judge and the world-state oracle disagree, the oracle is right | disagreement rate 62.5% (5/8); oracle right on the disagreement slice **5 / 5 (100%)**, judge accuracy 37.5% | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| A self-judged keep loop banks over-claims a witness-gated one refuses | over-claims kept = **12** (self-judged, seed 0) vs **0** (witness-gated) | a held-out measurement the proposer did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. The two judge failure modes both appear: gamed-clean (a real
violation behind confident prose) and alarmed-clean (a clean run with nervous
prose) — the oracle corrects both.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . FEAT FEAT1        # the artifact decides, not the model
```

The verdict reads git ancestry; the model's "I'm confident this is done" is parsed
for nothing:

```text
NOT_SHIPPED FEAT FEAT1 (via none)
```

## What this does — and does not — certify

It moves the judging authority from the model's self-assessment to an **un-authored
witness** — closing the self-preference and self-attribution gap. It does not
claim a deterministic oracle answers *every* question (a genuine taste call has no
witness and abstains to a human). The load-bearing point: a model's verdict on its
own work is consistency, not grounding — and grounding is what you wanted.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`docs/87`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/87_the-adjudicator-trust-ladder.md) — the adjudicator trust-ladder (oracle / judge / human).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to make an agent prove it did the work](make-an-agent-prove-the-work-not-self-certify.md) — the keep-gate in practice.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
