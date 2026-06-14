# DOS: the kernel that doesn't believe the agents

> A longer launch narrative — for a blog, Substack, dev.to, or the repo's own
> announcement page. Lead is the user-approved fleet wording; every number traces
> to the paper (`paper/sections/*.html`). Trim to taste for the venue.

*Refreshed 2026-06-14 against v0.26.0. Repo + PyPI links resolve now; fill the
arXiv link once an ID is assigned.*

---

## The lie your agents tell, and why it gets worse with more of them

Give a coding agent a real task and it works for many turns, then reports success.
Often the report is a lie the agent believes. It ran twenty tool calls, wrote a
tidy "All done, the login endpoint is live!", and nothing landed. The run looks
finished, the world is still broken, and nothing in the agent's own words tells you
which is true.

For one agent there's a clean answer, and a skeptic is right to reach for it: don't
trust the "done," check the work, re-run if the check fails. The shape of that loop
is right. I'll grant it.

What I won't grant is what people usually mean by it, that single-agent reliability
is therefore trivial, a weekend of glue. Two parts of that loop are real work.

First, the check has to be one the agent can't fake. Letting it re-read its own
result just grades the story against itself (the mirror-verifier trap). DOS's check
is a git-ancestry ship oracle under a verdict vocabulary the agent didn't author,
which isn't something you'd hand-build for a one-off loop. Second, the loop has to
be cheap enough to run every turn. In DOS's own kernel the in-flight check had to
be rewritten from a ~250ms Python hook to a ~10ms native one before it was fast
enough to always fire. So even for a lone agent, an unforgeable check and a loop
you can afford to run every turn are worth real money. The thing I don't rest the
headline on is handing that verdict back to the agent to act on. That part is
zero-to-harmful, and it's the next section.

The single-agent loop works for that easy hop because a lone agent owns its world.
Nothing changes the shared state between the moment you check and the moment you
act. The instant you have a fleet, many agents working at once on the same files,
the same database, the same tickets, that assumption breaks.

Here's exactly how it breaks:

> Checking a single agent is easy and not the problem. Anyone can wrap one agent
> in "if the check fails, run it again" in a few lines. But that assumes the world
> holds still between the check and the act. In a fleet of concurrent agents it
> doesn't. The verdict itself moves: a check reads true the instant agent A looks
> and false by the time A writes, because agent B changed the shared state in
> between.

Systems people will know it on sight. It's a time-of-check-to-time-of-use race,
moved off files and onto world state. They'll also know the textbook cure: fuse
the check and the act into one atomic step at the store — a transaction, or a
compare-and-swap that writes only if the value is still the one you read. That
cure is real. Where the shared state offers it, a wrapper around even one agent
closes the race, and the store itself is the referee. But agents in the wild
don't get that primitive. They act through high-level tool APIs — cancel a
reservation, file a ticket, edit a git tree — that expose only unconditional
writes. The check and the act stay two separate calls with a gap between them,
and no number of re-runs makes two calls one. So nothing bolted onto A fixes it.
It takes a referee between the agents, one that supplies the ordering the store
doesn't and trusts none of their reports.

That referee is **DOS**.

## What DOS is

DOS (the Dispatch Operating System) is a small deterministic kernel. It's the part
of a multi-agent system that decides ground truth across a crowd of unreliable,
self-reporting workers and orders their writes to shared state without believing
what they say they did.

The smallest version fits on one line. An agent claims it shipped the login
endpoint. Did it?

```bash
dos verify AUTH AUTH1   # → SHIPPED      AUTH AUTH1 e62f74d   (exit 0)
dos verify AUTH AUTH2   # → NOT_SHIPPED  AUTH AUTH2           (exit 1)
```

The answer comes from git history, not from what the agent typed. A commit backs
the claim, so `SHIPPED`, exit 0. Nothing landed, so `NOT_SHIPPED`, exit 1. In a CI
step or a dispatch loop you replace the line that trusts an agent's "done" with
`dos verify PLAN PHASE` and branch on the exit code. No parsing, no plan, no config.

It scales from there. Point a dozen agents at one repo and DOS also tells you which
ones are stepping on each other, which one is spinning in circles, and which "done"
is real. Every answer comes from the artifacts (git, the file tree, the clock),
never the report.

There's a name for the discipline, and it's the whole idea. A check is byte-clean
when whoever wrote its bytes is not the agent being judged. The environment's tool
result qualifies, because the gateway wrote it. A separate grader qualifies. The
agent's own prose does not. Reading the agent's own bytes is the mirror-verifier
trap: you're asking the model to confirm itself, which only measures whether its
story is internally consistent and collapses the moment the model stops emitting
the tell you trained on.

## The part I didn't expect: where the verdict pays

I held one thing fixed, a check the agent cannot forge, and asked only where to
spend it. The answer was sharp enough to become the paper.

Handed back to the agent that earned it (spending it in-loop), it's worthless, and
at the easy hop even harmful. I ran a live bake-off of every in-loop fix: warn the
agent, rewind its bad turns, inject the environment's own correction. All flat to
negative. The reason is simple. A capable agent already re-checks and fixes its own
phantom, so injecting any turn disturbs a run that was going to pass. The one
in-loop action that survives is the negative one: give up on a doomed run after the
environment reports the same error over and over. That earned zero false-halts
across 1,634 winners and saved ~11% of compute.

Handed to the rest of the fleet (spending it out-of-loop), the same verdict pays,
on both ways a fleet fails:

- **A race.** Two agents touch the same shared state and one silently overwrites
  the other. I ran two live agents at one shared reservation through a
  customer-service benchmark, and the naive flow let the second clobber the first.
  The DOS arbiter ordered them and prevented **6 of 8 lost-update clobbers**,
  checked against the environment's own database hash, a byte no agent wrote. (A
  later live run reproduced this at 9 of 10 on fresh conflict pairs.)

- **A claim.** One agent reports a write that never landed, and a downstream peer
  is about to inherit it as truth. The same referee, aimed here, is a
  write-admission gate. It blocked **10 genuine over-claims across 120 clean tasks**
  off that same hash while admitting every correct write, at the same 8.3% rate on
  a mid and a strong model, so the signal doesn't fade at the top tier.

There's a third consumer that isn't an agent at all: the loop that trains the next
model. When a model is fine-tuned on its own runs scored right-or-wrong (RLVR),
each blocked over-claim is a poison reward label, a wrong answer a self-judged loop
banks as a win, which teaches the model to over-claim more. Scoring those rows
against the byte the model can't fabricate purges every one (label purity rose from
60% to 100% on the live rows). And the label is non-distillable by construction: no
amount of training against a check that reads only bytes the model didn't author
can teach the model to move it.

So the boundary is the result. A verdict the agent cannot forge is harmful handed
back to that agent and valuable handed to anyone else, and "anyone else" is the
rest of the fleet and the training loop behind it. The concurrency case — agents
racing each other through tool APIs that give them no transaction — is the one a
wrapper can't reach at all.

## Being honest about the weak spots

The rule I held myself to: state the strength of every claim in the same breath as
the claim. So:

- The detectors are precise (88-98%) but low-recall. On an offline,
  third-party-scored replay of 22 models they catch only ~2-7% of failures, and
  that share shrinks on the strongest models. That low ceiling is exactly what
  sends the verdict out-of-loop in the first place.
- The downstream outcome delta from blocking one phantom (ΔB) is measured at the
  single hop where it's smallest, and there it's about zero, because a capable
  consumer recovers on its own. I report it as a fragile floor, not a headline. The
  headline is J, a blocked count of real corruptions a check the agent can't forge
  stopped, never a downstream outcome delta.
- The RLVR result is a measured label purge. The trained-behavior delta (does a
  model fine-tuned on the cleaned set over-claim less?) is set up but not yet
  measured to a weight update, and I say so.

## Where it sits

The method spans the stack on purpose. An operating-system framing (a minimal
trusted kernel that orders effects and believes no worker). Distributed-systems
hazards moved off files onto world state (TOCTOU, lost-update, write-ahead
recovery). Classical sequential statistics used honestly (the SPRT/CUSUM lineage of
the give-up gate, with a measured Wilson bound). A frontier-ML payoff (the RLVR
label filter). Underneath all of it is one object, a verdict on an agent's work
that the agent itself cannot forge or train away, and that object is the
load-bearing primitive under three problems a frontier lab already cares about:
scalable oversight, reward hacking, and multi-agent reliability.

## Try it

```bash
pip install dos-kernel      # the package name is dos-kernel, NOT dos
dos quickstart              # scaffolds a repo, commits, verifies, cleans up
```

One `SHIPPED`, one `NOT_SHIPPED`. The first is a claim git can back, the second is
a claim nothing landed for. That contrast is the whole product in one command.

- **Repo:** github.com/anthony-chaudhary/dos-kernel (MIT)
- **Paper:** [arXiv link once it's up]
- **MCP server** included, so you can wire it into the agent host you already run.

If you run more than one agent against the same codebase, I'd really like to know
where it breaks for you.
