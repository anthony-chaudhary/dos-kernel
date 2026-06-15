# 351 — The outer ratchet: RSI first-class across all the loops

> **Every unattended loop should ask, each iteration: did this produce a witnessed
> net gain, or am I spinning while narrating progress?** DOS already answers that
> for the one self-modifying loop (the `improve` keep-gate, docs/280). This doc
> makes the same verdict a first-class rung of the GENERIC loop decider, so the
> dispatch / replan / supervise loops gain the same honest stop — without being
> turned into source-rewriting loops.

This is the loop-iteration half of the doctrine whose library half is
[docs/350](350_outcome-driven-retirement-the-library-drift-fix.md): keep only what
a witness the author did not write confirms still earns its place. Docs/280 built
it for one loop; this generalizes it across the loop roster.

## The one idea: a loop degrades with steps unless every kept step is witnessed

The failure is measured, not feared. "Reward Hacking in Self-Improving Code Agents"
(ICLR 2026 RSI workshop, openreview ikrQWGgxYg) found reward-hacking in a
self-improving loop **worsens with optimization steps**: prevalence rose **+31.4
percentage points** going from 10 to 100 steps. And the hack is **invisible on the
agent's own tests** — SpecBench (arXiv 2605.21384) found validation scores nearly
identical between honest and hacked solutions while the held-out scores diverge
sharply. The theory closes it: an external verifier prevents a self-consuming loop
from collapsing, "but gains plateau and may even reverse unless the verifier is
perfectly reliable" (arXiv 2510.16657).

Three consequences, each load-bearing for this design:

1. The witness must gate **every kept iteration**, not run once at the end — because
   the hacking grows with steps.
2. The witness cannot be the agent's own self-critique — because the hack does not
   show up on the agent's own tests.
3. The guarantee is only as strong as the witness — so anchor on the strongest
   objective one available (here: git-ancestry-confirmed shipped work), and state
   the ceiling honestly.

## What "first-class across loops" means here (and what it does NOT)

The dispatch / replan / supervise loops do **not** rewrite source — making them do
so would be wrong and unsafe. So they do **not** get the `/dos-self-improve`
worktree / SELF_MODIFY machinery. What they share with the self-improving loop is
the *pattern*: a per-iteration verdict on whether the loop is producing a witnessed
net gain, with an honest stop when it is "running but not improving" (the spinning,
narrating-progress failure). That pattern becomes a first-class rung of the loop
decider — composing the verdict that already exists, never duplicating it.

## Why there is no new "ratchet leaf"

The outer-ratchet verdict ALREADY exists: `improve.classify` (docs/280) decides
KEEP / REVERT / ESCALATE over four env-authored facts (suite, truth, work,
baseline-work), with a ratcheting baseline and a breaker that ESCALATEs to a human.
That IS the ratchet — it was just first-class in only one place. And the place
loops compose per-tier verdicts ALSO already exists: `loop_decide.decide` already
carries `liveness`, `completion`, `convergence`, `pickability`, `cooldown` as
optional in-flight-evidence verdicts, each cleared up-front, each byte-identical to
the pre-field loop when `None`.

So making RSI first-class across the loops is the FOURTH instance of that exact
pattern, not a new mechanism:

- `LoopState.ratchet: Optional[improve.CandidateVerdict] = None` — the in-flight
  ratchet verdict the caller re-gathers each iteration (via `dos improve`), cleared
  up-front like its siblings; `None` ⇒ byte-identical to today.
- `StopReason.NOT_RATCHETING` — the loop is running but producing no witnessed net
  gain. An `improve` **ESCALATE** maps to it (the breaker fired inside `improve`: N
  iterations in a row with no witnessed gain). A single **REVERT** does not stop on
  its own — it is one breaker tick, like a single UNCLEAR.
- The rung reads the verdict **verbatim** — the loop adds no second judgement, which
  is what carries the docs/138 non-forgeability end-to-end: the keep/stop bit is a
  pure function of facts the loop did not author.

### Precedence (explicit, pinned by a test)

`COMPLETE` beats `NOT_RATCHETING` — a provably-finished run is not "not ratcheting"
(the docs/280 COMPLETE-before-SPINNING precedent). When `SPINNING` and the ratchet
both fire, `SPINNING` wins the reason — ground-truth git delta beats the composed
self-report of the same disease. The ratchet rung sits with SPINNING in the
ground-truth tier, before the pick rungs and the outcome block.

## The per-loop ratchet table

| Loop | Its ratchet | Status |
|---|---|---|
| `dos-dispatch-loop` | `NOT_RATCHETING` — the cross-run reconcile-VERIFIED ship-count must rise; N iterations of "SHIPPED" that all reconcile QUIET_INCOMPLETE stop the loop | **new** (this doc) |
| `dos-replan-loop` | `REPLAN_STALLED` — K consecutive UNPRODUCTIVE replans (a replan's net gain is gardening/refill, a different metric than ship-count) | existing (#506 / docs/258) |
| `dos-supervise-loop` | the per-worker `liveness` SPINNING → FLAG / PROPOSE_HALT ladder (the supervisor surfaces a spinning worker, never kills it) | existing (docs/82) |

The dispatch loop is the one that genuinely lacked the rung: it had anti-churn
(pickable / cooldown) and a per-pick cross-run KEEP (`dos reconcile`), but no
per-iteration measured-net-gain ratchet — so it could run N "successful" iterations
while net-shipping nothing real. The replan and supervise loops already had their
shaped ratchet; this doc NAMES it so the doctrine is legible across the roster
rather than forcing one metric onto three differently-shaped loops.

## The witness ceiling, stated honestly

The dispatch ratchet is only as good as its work-metric. The metric is the count of
picks `dos reconcile` confirms VERIFIED against git ancestry — a non-forgeable
witness (the loop cannot write the git history that decides). That is the strong
end of the ceiling. A loop whose host wired a weaker metric (a self-reported
ship-count) would get a weaker guarantee; the kernel only compares the magnitudes
it is handed. Cross-link [docs/318](318_keep-gate-ablation-plan.md) (what the
keep-gate buys — the ratchet curve) and
[docs/329](329_witness-tamper-floor-the-keep-gate-cannot-see-a-harness-edit-plan.md)
(the witness cannot be edited by the thing it judges) as the honesty backstops.

## What this is NOT

- **Not making the loops rewrite source.** The shared thing is the *pattern* (a
  witnessed per-iteration net-gain verdict), not the SELF_MODIFY worktree machinery.
- **Not a new leaf or a new CLI verb.** `improve.classify` is the verdict;
  `dos improve` is the evidence-gather the loop calls; `loop_decide` is the
  composer. This doc adds one opt-in field and one stop reason.
- **Not unbounded autonomy.** The ratchet's stop is the breaker ESCALATE — it hands
  "what matters next" back to a human exactly when the loop runs dry of witnessed
  gain, the RSI research's irreducible bottleneck made a kernel rule.

## Provenance

The outer ratchet is `improve.classify` (docs/280) read as a first-class rung of
`loop_decide.decide` (docs/258), the same compose-don't-duplicate move every other
in-flight-evidence verdict in that decider already makes. Its library sibling is
docs/350; its keep-gate ancestor is docs/280; its non-forgeability is the docs/138
invariant and the docs/234 theorem at loop scale.
