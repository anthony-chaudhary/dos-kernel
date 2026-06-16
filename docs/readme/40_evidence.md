## What's proven and what's still a bet

We apply the same honesty to our own claims that the kernel applies to your
agents. It would be easy to lead with one big number; instead, here's the
split — what we actually measured, what we extrapolated from those
measurements, and what is still a bet. Draw the line yourself. (Every *proven*
number is from a live, re-runnable benchmark written up under
[`benchmark/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark) and the paper.)

**✅ Proven — measured in live runs, scored against a fact the agent can't fake**
(a test environment's database state, git history — bytes the agent wrote none of):

- **It catches the lie and blocks it.** Across 120 clean tasks on a standard
  agent benchmark, a DOS gate caught 10 genuine "I shipped it" lies and let
  every honest write through — at the same 8.3% catch rate on both a mid-size
  and a top-tier model. The signal doesn't fade when you upgrade the model.
  (Over the full benchmark: 15 lies caught in 258 tasks, two models, zero false
  alarms.) *(▶ the catch itself is the [gate figure below](#the-two-money-moments-rendered).)*
- **It prevents the collision.** The same referee put two live agents on one
  shared record and stopped 6 of 8 cases of one silently overwriting the other
  — 4 of 6 when the cases were drawn from the real task mix. This is the half a
  sandbox *can't* cover: an isolated workspace still shares the outside world.
  *(▶ the collision being prevented is the [coordination figure below](#the-two-money-moments-rendered).)*
- **Mid-run "fixes" don't help; quitting a doomed run does.** Every active fix
  we tried mid-run (warn it, rewind it, inject a hint) came out flat-to-negative
  — poking a run also disturbs the ones that would have passed. The one move
  that helps writes nothing: give up at the right moment — 0 runs wrongly
  killed out of 1,634 winners across 22 models, ~11% of fleet compute saved.
- **The training label can't be gamed.** For "may a fine-tune learn from this
  run?" (`dos reward`), the yes/no is computed from environment state the agent
  authored none of — so no amount of clever output text can flip a *no* to a
  *yes*. That's a proof, plus a measured 60% → 100% precision lift from
  filtering out the poison a naive self-graded collector would have kept.

<a id="the-two-money-moments-rendered"></a>

The two proven moments above, each rendered as a single figure from its own live
run (every number, hash, and ID is a verbatim read-off — never a hand-typed
dramatization):

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/benchmark/agentprocessbench/writeadmit/gate-moment.svg" alt="The DOS write-admission gate catching a real over-claim: a live agent reports a booking it never made, the environment DB-hash (gold vs. actual) shows the booking is not in the database, and the gate BLOCKS the confident-but-false write before it reaches the next agent. The numbers are in the bullets and caption." width="100%">
  <br>
  <sub><em><strong>It catches the lie and blocks it.</strong> A confident booking, refuted by the DB-hash the agent couldn't author, blocked before a downstream agent inherits the phantom. <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/agentprocessbench/writeadmit/gate_visual.html">Step through it locally</a> (an HTML walkthrough — clone and open in a browser; GitHub shows its source).</em></sub>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/benchmark/agentprocessbench/writeadmit/f2-moment.svg" alt="The DOS coordination payoff: two live agents act on one shared reservation, neither aware of the other. Under naive replay the second agent's stale write silently overwrites the first's cancellation — a real lost update. Under the arbiter, the region is leased to the first agent, the second's overlapping lease is refused, it re-plans against the true post-cancellation state, and no update is lost. The numbers are in the bullets and caption." width="100%">
  <br>
  <sub><em><strong>It prevents the collision.</strong> A stale add-bag clobbers a cancellation under naive replay; the arbiter serializes the two agents on the same region so neither overwrites the other. <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/agentprocessbench/writeadmit/f2_visual.html">Step through it locally</a> (an HTML walkthrough — clone and open in a browser).</em></sub>
</p>

**📈 Projected — real measurements, composed into a curve (and labelled as one).**
Here's the crux: catching a lie is only worth something to whoever can't catch
it themselves. Hand the verdict to one strong agent that re-checks its own
inputs and it buys you almost nothing — that agent recovers on its own. Hand it
to something that *can't* re-check — a non-LLM system, a weaker model, a long
multi-step chain, or a training loop — and it pays off (up to a full +1.0 in
our no-recovery upper bound). In short: DOS is worth more the less your
downstream can check itself. Our fleet-scale figure (≈173–505 corrupted results
prevented at a 32-agent fleet) projects these real per-run rates onto fleet
math — it's geometry on top of measured numbers, not a measured fleet run.

**🎲 A bet — stated as one.** Where this goes if the floor holds: a frozen,
cross-vendor trust standard (the "deny" message already renders from one
verdict across every wired host, and is byte-identical on the ones that share
Claude Code's envelope — Codex and Claude Cowork, pinned in
`tests/test_hook_dialect.py` — a de-facto standard waiting to be named),
a shared arbiter for real-world effects, the claim-vs-reality corpus only a
neutral party can hold, and a notary that proves what an agent did *to a
skeptic who wasn't in the room* (the mechanism already ships — `dos attest`
mints an HMAC-signed receipt over an effect-witness verdict and
`dos verify-receipt` checks it with the shared key alone;
[docs/246](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/246_dos-attest-the-portable-signed-receipt.md)). The seeds are
in the tree; we claim no results for any of it.

> **The one distinction that keeps this honest:** a **J** is a *count of failures
> blocked off ground truth* — never a downstream outcome delta. "Blocked 10 real
> over-claims" is proven; "made the fleet 10% better" is not the same sentence,
> and we don't write it.

## What DOS does *not* do

The proven/bet gradient above is about *evidence*; this is about *capability* —
the boundaries are part of the contract, and stating them is the same honesty
the kernel applies to your agents:

- **It adjudicates that a ship *happened*, not that the code is correct or good.**
  `verify` reads git ancestry, so it catches "no commit landed," not "the
  committed work is wrong." Judging *quality* is the JUDGE / HUMAN rung, not the
  deterministic oracle.
- **It computes verdicts and admission decisions; it never spawns or kills an OS
  process.** `liveness` is advisory — it *reports* SPINNING, it doesn't stop the
  run — and `dos loop` *emits* a spawn/reap/flag plan you act on. (`arbitrate` and
  `refuse` are decisions you enforce, not force the kernel applies.)
- **It is not a CI replacement or a test runner.** It sits *beside* them and lets a
  step branch on the exit-code verdict.
- **The pluggable verdict/JUDGE adjudicator *registry* is specced, not yet
  shipped** (see [docs/88](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/88_the-adjudicator-population.md) §5); the JUDGE
  *seam* and built-in judges are.
