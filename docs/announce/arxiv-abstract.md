# arXiv paper — abstract & post copy

> For the "the paper is up" announcement, once arXiv assigns an ID. The abstract
> below mirrors `paper/sections/01_abstract.html` (the single source of truth — if
> they diverge, the `.html` wins; regenerate with `python paper/assemble_arxiv.py`).
> Fill `arXiv:XXXX.XXXXX` once assigned.

*Refreshed 2026-06-14 against v0.26.0. This item still waits on an arXiv ID — do
not post until `arXiv:XXXX.XXXXX` is filled; it goes up as its own separate
submission, after the repo/package launch.*

---

## Title

**Verification Is All You Need — But Not Where You Think**

## Abstract (as submitted)

Checking a single agent is easy and not the problem. Anyone can wrap one agent in
"if the check fails, run it again" — a few lines of glue. That trick quietly
assumes the world holds still between the check and the act. In a fleet of
concurrent agents it does not. The verdict itself moves: a check can read *true*
the instant agent A looks and *false* by the time A writes, because agent B changed
the shared state in between — a time-of-check-to-time-of-use race on world state.
Agents act through high-level tool APIs that offer no transaction and no
conditional write, so no re-run wrapper around A can close that race. It takes a
**referee between the agents**: one that serializes
their effects on shared state and reads only bytes the agents *cannot author*. That
is the result of this paper, and we earn it live.

One out-of-loop referee pays on both ways a fleet fails. The naive flow lets two
agents racing one reservation clobber each other; our arbiter serializes them and
**prevents 6 of 8 lost-update clobbers** (we write this payoff **J**: real
corruptions the referee stops off ground truth), witnessed by the environment's own
database hash — a byte no agent wrote. The *same* referee, aimed at the fleet's
other concurrency failure (one agent narrates a write that never landed and a peer
is about to inherit it as truth), is a write-admission gate that blocks **10
genuine over-claims across 120 clean tasks** off that same hash while admitting
every correct write — at an *identical* 8.3% rate on a mid and a strong model, so
the signal does not vanish at the stronger tier. A third consumer is not an agent
at all: the loop that *trains* the next model. Each blocked over-claim is a *poison
reward label* — a wrong answer a self-judged RL loop banks as a *win*, teaching the
model to over-claim more — and scoring those same rows against the byte the model
cannot author purges every one (label purity 60% → 100% on the live rows): a reward
signal non-forgeable by construction, so training against it cannot launder it
away.

*Where* the verdict is spent is the whole result. Spent as an *active fix back
inside* the loop — warning the agent, rewinding it, injecting a correction — the
very same verdict is flat-to-*harmful* at the easy hop: a capable agent re-checks
and fixes its own phantom on its own, so intervening only disturbs a run that was
going to recover. (The verdict still pays a lone agent in two quieter ways — as an
*unforgeable* check the agent can't fake its way past, and as the one in-loop
action that survives below; what does *not* pay is feeding a fix back to the
producer.) On an offline,
third-party-scored replay of Toolathlon (22 models, 76.2% base failure) our
byte-clean detectors fire with 88–98% precision but catch only ~2–7% of failures,
and a live bake-off shows every *in-loop* active fix is flat-to-negative. The one
in-loop action that survives is the negative one — **give up correctly**, halting a
doomed run after the environment itself reports the same error repeatedly: zero
false-halts across 1,634 winners, ~11% compute saved. So the boundary *is* the
result: a verdict the agent cannot forge is harmful handed back to that agent and
valuable handed to anyone else — the rest of the fleet, and the training loop behind
it.

**Categories:** cs.SE (primary), cs.AI; optionally cs.DC.

---

## The announcement post (LinkedIn / X / Mastodon, once the ID is live)

📄 **New paper: "Verification Is All You Need — But Not Where You Think"**

The title is a bit of a troll. The point isn't more verification. It's that a
verdict an agent can't forge is worthless handed back to that agent and valuable
handed to the rest of the fleet.

We hold one thing fixed, a check the agent cannot fake (it reads the environment's
own database hash, never the agent's word), and ask only where to spend it. Live
results:

• In-loop (warn, rewind, or inject a fix back to the agent): flat to harmful. A
capable agent already self-heals, so you're just disturbing a run that was going to
pass.

• Out-of-loop (to the fleet around the agent): prevents **6 of 8** write-collisions
between racing agents, and blocks **10 real over-claims** before a downstream peer
inherits them, at the same 8.3% rate across two model tiers.

The failure that makes this matter is a check that's true when agent A looks and
false when A writes, because agent B moved the state in between. The tool APIs
agents act through offer no transaction and no conditional write, so no wrapper
around A can close that race. You need a referee between agents.

We're honest about the floor: detectors are precise but low-recall (~2-7%), and the
downstream outcome delta at the easy hop is about zero. The headline is the blocked
count J, off ground truth, not a downstream delta we can't stand behind.

📄 arXiv:XXXX.XXXXX
🔗 Code + every recomputable number: github.com/anthony-chaudhary/dos-kernel

#AIAgents #MultiAgent #LLM #AIalignment #MLpaper

---

## Tweet-length variants

**(a) the hook**
New paper: "Verification Is All You Need — But Not Where You Think." A verdict an
agent can't forge is harmful handed back to that agent (it self-heals, you just
disturb it) and valuable handed to the rest of the fleet. Measured live.
arXiv:XXXX.XXXXX

**(b) the mechanism**
A "done" check can read true when agent A looks and false when A writes, because
agent B changed the shared state in between. That's a TOCTOU race on world state,
and the tool APIs agents act through give A no transaction to close it. No wrapper
around A fixes that. You need a referee between agents. New paper + code:
arXiv:XXXX.XXXXX

**(c) the number**
Live: an out-of-loop referee prevented 6/8 write-collisions between racing agents
and blocked 10 real over-claims before a peer inherited them, off the env's own DB
hash, never the agent's word. Hand the same verdict back to the agent? Flat to
harmful. arXiv:XXXX.XXXXX

---

## Notes

- The abstract here is a verbatim mirror of the paper's. If you edit the framing,
  edit `paper/sections/01_abstract.html` and regenerate — don't let the
  announcement and the paper drift.
- The "troll title" framing is fine for social but **not** for the arXiv comments
  field — keep that factual.
- For Hacker News: the full "Show HN" draft (title + author's first comment) is in
  [`hackernews.md`](hackernews.md) — post the *code* there as its own Show HN, and
  the *paper* as a separate item once it has an arXiv ID. The honest-floor paragraph
  (low recall, ΔB≈0) is an *asset* on HN, not a liability — that crowd rewards
  stating your own weaknesses, and it pre-empts the top comment.
