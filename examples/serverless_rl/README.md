# Serverless RL — `dos reward` as the reward function

> **The kernel is the part that doesn't believe the agents.** In an RL loop,
> the reward function is the part that *pays* the agents — so it had better
> not believe them either.

This directory holds [`dos_reward_scorer.py`](dos_reward_scorer.py): the
witness-gated reward verdict (`dos.reward.admit`, docs/230/234) dropped into
the W&B Serverless-RL signal path, in the two shapes that path consumes.

## The believe-the-agent point

In Serverless RL (W&B Training / OpenPipe ART), the reward is arbitrary user
Python that runs on your machine: your rollout finishes, your code assigns
`trajectory.reward`, and the trainer optimizes the policy toward that number.
The laziest reward reads the trajectory's own text ("Successfully cancelled"
→ 1.0); an LLM judge reads the same narration with better prose taste. Both
are forgeable — the policy can learn to *say* the magic words, so it is
trained to over-claim more.

`dos reward` is the deterministic fix. A "done" claim earns reward only if a
witness the agent authors zero bytes of (the provider's ledger, an OS exit
code) confirms the effect:

| trajectory | naive reward | dos reward | verdict |
|---|---|---|---|
| did the work, said so | 1.0 | **1.0** | ACCEPT |
| said so, didn't do it | 1.0 | **0.0** | REJECT_POISON |
| pasted its own "receipt" | 1.0 | **0.0** | ABSTAIN |
| made no claim | 0.0 | **0.0** | NO_CLAIM |

The label is **non-distillable**: no output text can flip it. The accept bit
is a pure function of the witness, inherited from the kernel's
`believe_under_floor` — an `AGENT_AUTHORED` read-back is structurally ignored,
however convincing its bytes.

## The two bindings

* **`make_weave_scorer(...)`** — a real `weave.Scorer` whose `score(output=…,
  task=…)` returns the flat verdict row (`reward`, `passed`, `verdict`,
  witness fields). Needs `pip install weave`; no W&B account to construct or
  call.
* **`make_art_reward(...)`** — a plain `(task, final_output) -> float` you
  assign to `art.Trajectory.reward`. Needs nothing beyond `dos-kernel`.

Both delegate to the same `DosRewardScorer` core, so the bindings cannot
drift from the tested behavior. You supply the two host jobs as callables,
and the adapter parses nothing itself:

* `claim_extractor(output) -> bool` — *precision*: did this row make a
  checkable "done" claim? Reads forgeable text, so the worst it can do is
  route a row to NO_CLAIM — never to a false accept.
* `witness(task) -> readbacks` — *soundness*: re-read the world at the place
  the task names (never where the agent's text points), returning
  `dos.evidence.EvidenceFacts` on an honest accountability rung.

## Run it

```bash
pip install dos-kernel
python examples/serverless_rl/dos_reward_scorer.py   # the four-row demo above
```

Test it (no W&B account, no `weave`/`art` install, no network):

```bash
python -m pytest tests/test_serverless_rl_example.py
```

The demo workers are scripted liars — no LLM behind them — because the signal
path is what's being demonstrated: swap in your real rollout; the scorer does
not care who is lying to it.
