# What replaced tokens-burned as the metric for AI agents?

> Verified outcomes — and a loop is only "progress" if a witness the agent didn't
> author says so: `pip install dos-kernel`, then `dos verify` / `dos efficiency` /
> `dos reward`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

"Token-maxxing" — treating tokens consumed as the proxy for AI productivity —
lost its legitimacy in 2026: leaderboards that ranked people by token usage came
down, and the press that had celebrated the metric called it "a flawed way to
measure ROI." (Those are others' reports, cited below — not DOS results.) The
replacement everyone is converging on is **verified outcomes**: did the task
actually pass, witnessed, and how many plan→edit→verify **loops** did it take to
get to green. Tokens are a cost now, not a trophy.

That shift is the same move DOS is built on: stop letting the agent's own count
be the score, and read the result from something the agent could not author.
`dos verify` says whether a claimed phase actually shipped (git ancestry, not the
"done" line); `dos efficiency` / `dos liveness` read loops and motion from the
artifacts, not the status line; `dos reward` emits a per-step label that is a
pure function of the environment's state. The unit of value is a *witnessed
completion*, and these are the syscalls that produce one.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Over-claims are caught before the write lands — "I shipped it" stops being the score | J = 10/120 over-claims blocked, **0 honest writes refused** (8.3% over-claim rate, 15/258 over the full benchmark) | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| Loops are graded by witnessed progress, not "making progress" | **0 false-abandons / 1,634 winners across 22 models** (error-gated, K≥3); the same test falsifies the naive raw-repeat gate | each task's own oracle over a frozen replay corpus | [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) |
| The outcome label can't be gamed by spending more tokens | acceptance precision **60% → 100%**, J = 5 poison labels purged (**ΔP +40 pp**) from a self-judged collector's bank | the gold database hash, keyed on `db_match` | [`docs/230`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta — "blocked 10 real over-claims against the environment's own
database hash" is a proven sentence; "made the fleet 10% better" is a different
sentence these pages do not write. The token-maxxing reporting is cited as
*others'* findings about the metric's legitimacy, not as a DOS measurement.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . AUTH AUTH2        # did the claimed phase actually ship?
dos efficiency --workspace .               # is the loop producing output per unit of work, or just burning turns?
```

`dos verify` on a claim nothing backs:

```text
NOT_SHIPPED AUTH AUTH2 (via none)
```

Exit code `1` — `via none` means DOS checked everywhere it trusts and found
nothing behind the claim. `dos efficiency` and `dos liveness` read loops and
motion from env-authored counts (commits, touches, elapsed), so a run that
narrates progress while the git log stays flat shows up as stalled. The metric is
the witnessed result, not the token meter.

## Why this is the durable claim

The careful version of "token-maxxing is over" is about *legitimacy*, not
behavior: the metric lost its authority — plenty of teams are still mid-binge.
That distinction is the point. A score the agent can run up by talking more
(tokens, a convincing "done") is a proxy; a score read from a witness the agent
didn't author (git ancestry, a database hash, a task's oracle) is the thing
itself. As models get better at producing convincing narration, a witness-derived
metric *appreciates* with capability while a self-reported one erodes — which is
exactly why the market moved off the proxy.

## What this does — and does not — certify

These verdicts certify **presence, progress, and outcome against the artifacts** —
the claim has a real effect behind it, the loop is landing real changes, the step
matched the world. They do not certify **correctness**: a verified, audited,
admitted commit can still be wrong code. The claim is narrower and load-bearing —
the agent's word, and its token count, stop being the evidence.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the over-claim gate study.
- [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) — the loop / give-up study (progress read from artifacts).
- [`docs/230`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md) · [`docs/234`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/234_the-non-distillable-reward-channel-lab-facing-proof.md) — the non-distillable outcome label.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- Related answers: [How do I verify an AI agent actually did the work?](how-to-verify-an-ai-agent-actually-did-the-work.md) · [How do I detect an agent loop spinning without progress?](how-to-detect-an-agent-loop-spinning-without-progress.md) · [Where do I get process-reward training data that can't be gamed?](process-reward-model-training-data-that-cant-be-gamed.md)
- External context (others' reporting on the metric's legitimacy, not DOS results): the 2026 "token-maxxing is over / a flawed way to measure ROI" coverage and the move to loop-count / per-task cost in AI-coding pricing analyses.

> The kernel is the part that doesn't believe the agents.
