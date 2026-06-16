<!-- GENERATED FILE — do not edit README.md directly.
     The source of truth is docs/readme/ (one file per section, assembled
     in filename order). Edit the part, then run:
         python scripts/build_readme.py
     tests/test_readme_assembly.py pins this file to the parts. -->

# DOS — the Dispatch Operating System

> ### Catch your AI agents when they lie about what they shipped.

[![PyPI](https://img.shields.io/pypi/v/dos-kernel)](https://pypi.org/project/dos-kernel/)
[![Python versions](https://img.shields.io/pypi/pyversions/dos-kernel)](https://pypi.org/project/dos-kernel/)
[![CI](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml)
[![verified by DOS](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml)
[![commit-claims](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fanthony-chaudhary%2Fdos-kernel%2Fmaster%2Fdocs%2Fscoreboard%2Fanthony-chaudhary%2Fdos-kernel%2Fbadge.json)](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/scoreboard/methodology.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/anthony-chaudhary/dos-kernel/blob/master/LICENSE)

> 📊 **See it run on real repos:** the **[scoreboard](https://anthony-chaudhary.github.io/dos-kernel/scoreboard/)**
> scores 15 popular AI-built repos (roborev, open-interpreter, crewAI, autogen, …)
> — how much agents wrote, which ones, and whether each commit's claim is backed
> by its own diff. Score yours: `dos commit-audit --sweep --workspace . BASE..HEAD`.

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/docs/assets/caught-lie-cast.svg" alt="A terminal recording of the caught lie. The agent reports: Done! Shipped the login endpoint (AUTH1) and the password reset (AUTH2). git log shows one commit — AUTH1: ship the login endpoint. dos verify AUTH AUTH1 answers SHIPPED (exit 0); dos verify AUTH AUTH2 answers NOT_SHIPPED via none (exit 1) — caught. The exit code is the verdict: gate the agent's done on it and a false claim cannot land." width="100%">
  <br>
  <em>The whole pitch in one recording: the agent claims two features shipped; git backs one.
  <code>dos verify</code> answers from the commits, the lie exits <code>1</code>, and a gate on that
  exit code refuses the false "done". Every line is the real CLI's verbatim output —
  <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/scripts/build_caught_lie_cast.py"><code>scripts/build_caught_lie_cast.py</code></a> re-records it whenever the output changes.</em>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/docs/assets/loop-hero.svg" alt="Two agent fleets side by side. Left, no referee: agents all report 'done!', every report is believed, and silent corruption (lies, collisions, spin) piles up into a codebase that 'sorta works' and can't be changed. Right, DOS adjudicates: dos verify reads git and the run branches to SHIPPED (exit 0, land it) or NOT_SHIPPED (exit 1, re-dispatch — caught), and that verdict steers the next step." width="100%">
  <br>
  <em>Run a fleet of agents on one repo. The left loop just feels like progress; the right one you can steer.
  The only difference is a verdict DOS reads from the real world — here, git — never the agent's word.</em>
</p>

An AI agent will tell you it finished. DOS checks the real world instead of
taking its word — and the nearest piece of the real world is your git history.
An agent says it shipped the login endpoint; did it? Run one command,
`dos verify`, and it answers from the artifacts the work left behind, not from
what the agent typed: a commit backs the claim → `SHIPPED`, exit `0`; nothing
landed → `NOT_SHIPPED`, exit `1`. The agent's story never enters into it. (Git
is just the first witness DOS reads; the file tree, the clock, a CI status, a
test environment's own state are others — anything the agent didn't author.)

```bash
dos verify AUTH AUTH1   # → SHIPPED      AUTH AUTH1 e62f74d   (exit 0)
dos verify AUTH AUTH2   # → NOT_SHIPPED  AUTH AUTH2           (exit 1)
```

That's the smallest version. It scales up, too: point a dozen agents at one
repo — in CI, in a fleet, racing on the same files — and DOS also tells you
which ones are stepping on each other, which one is spinning in circles, and
which claim of "done" is real. Every answer comes from the artifacts (git, the
file tree, the clock), never the narration. It works on a plain `git` repo with
zero config and gets smarter the more you tell it, and the only thing you ever
install is one small Python package.

> ⚡ **Just add it — two commands, zero decisions.** From the repo where your
> agent works:
>
> ```bash
> pip install dos-kernel
> dos init --hooks auto   # finds the agent runtime(s) you already use, wires in the checks
> ```
>
> From then on: your agent can't tell you **"done"** unless the work actually
> landed, two agents can't silently overwrite each other's files, and a run
> that stalls gets flagged instead of quietly spinning. Nothing about your
> workflow changes, and you don't need to learn any of the vocabulary below to
> be covered. It prints the one config file it wrote; deleting the `dos hook`
> entries there undoes it. (No runtime detected? It says so and lists the
> names to pick from — it never guesses.)

<sub>**v0.27.1** · 5,600+ tests · CI: Python 3.11–3.13 on Linux + a Windows 3.13
smoke run · the only runtime dependency is **PyYAML** · **MIT**.</sub>

> 🧭 **Where to go next:** the [why & evidence](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/why-a-referee.md) (plain-words story, the 20-lines-of-bash answer, what's proven),
> [wire it into your stack](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md) (MCP · hooks · install), the
> [syscall + CLI reference](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/cli-reference.md), or, **reading this as an AI agent?**, [AGENTS.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — build/test/check in three lines. The full map is the router just below.

> 🔤 **Five words the rest of this page leans on.** A **plan** is a named goal
> (`AUTH`); a **phase** is one shippable step of it (`AUTH1`); a **lane** is the
> slice of the file tree one agent may touch; the **oracle** is the part of DOS
> that reads the evidence and rules; a **stamp** is the mark a shipped phase
> leaves in a commit subject (`AUTH1: …`) — the thing the oracle greps for.
> That's the whole vocabulary.

<a id="who-this-is-for"></a>
<a id="the-plain-words-version"></a>

## In plain words

A coding agent does work, then tells you how it went. Usually the story is true;
sometimes it's the cheerful *"all work completed!"* from a worker that shipped
nothing. With one agent you catch that yourself by re-reading its output — a real
tax you already pay. Run twenty at once and that tax stops being payable: nobody
reads everything, each worker grades its own homework, and the unchecked problems
pile up quietly until the codebase *sorta* works and nobody can safely change it.
DOS is the referee that never reads the story — it reads what happened (the
commit, the file, the clock) and hands you a verdict no narration can move. It
costs about an afternoon, has one runtime dependency, and stays in its lane: it
tells you *what happened*, never whether the code is *good* — quality stays with
your tests and reviews. ([The full plain-words version](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/why-a-referee.md#the-plain-words-version).)

## Measured, not asserted

Every number here is scored against a fact the agent can't fake (a test
environment's DB state, git history). A DOS gate caught **15 "I shipped it" lies
in 258 tasks across two models with zero false alarms**; the same referee stopped
**6 of 8** silent collisions on one shared record; quitting doomed runs at the
right moment saved **~11% of fleet compute with 0 of 1,634 winners wrongly
killed**; and the reward-set admission label lifted acceptance precision **60% →
100%** by purging poison a self-graded collector keeps. The methodology, the two
money-moment figures, and the projected-vs-bet honesty gradient are in
**[what's proven and what's still a bet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/why-a-referee.md#whats-proven-and-whats-still-a-bet)**.

## Where the rest of the docs are

This page keeps the hook, the demo, and the failure it fixes. Everything deeper
lives on a focused page — find the question you arrived with and jump:

| You're asking… | Go to |
|---|---|
| *"What is this in plain words, and why should my team care? Is it real?"* | [Why a referee](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/why-a-referee.md) — the plain-words story, the 20-lines-of-bash / Temporal answers, and the full proven/bet evidence |
| *"Show me it working, fast."* | [Try it in 60 seconds](#try-it-in-60-seconds), just below — one command |
| *"I already run agents — how do I wire the verdict into **my** stack?"* | [Wire it in](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md) — MCP, runtime hooks, the exit-code tier, fleet frameworks, and the install matrix |
| *"What's the full command / syscall surface?"* | [The syscall ABI & CLI reference](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/cli-reference.md) — every verb, the three live screens, the verdict journal |
| *"I run a fleet every day — how do I watch it, triage it, debug it?"* | [Operating a fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/operating-a-fleet.md) + [Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md) |
| *"How do I bend it to my org without forking it?"* | [Extending it](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/extending.md) — the seven axes, the docs index, the playbooks |
| *"What is actually proven, and can I re-run it?"* | [For researchers](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/for-researchers.md) — claims → invariants → reproduction |
| *"I'm an AI agent orienting in this repo."* | **[AGENTS.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md)** — what DOS is in three lines, build/test/check, the ~5 files worth reading |

## Try it in 60 seconds

Got a terminal? This runs the whole thing in a throwaway repo — one command
scaffolds it, makes a real commit, verifies it, and cleans up after itself:

```bash
pip install dos-kernel      # PyYAML is the only runtime dep
dos quickstart              # → SHIPPED AUTH AUTH1 … then NOT_SHIPPED AUTH AUTH2
```

One `SHIPPED`, one `NOT_SHIPPED`: the first is a claim git can back, the second
is a claim nothing landed for. That contrast is the product. The demo closes
with a router to wherever you already run agents — a Claude Code / Cursor tab
(`dos init --hooks`), an MCP host, a CI step, or a fleet — so your next move is
one line, not a docs dig. (Add `--keep ./demo` to keep the repo and poke at it.
Don't even want the install? `uvx --from dos-kernel dos quickstart` runs the
same demo ephemerally — nothing left behind.) The same thing by hand, in five
lines, is **[docs/QUICKSTART.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md)**.

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/examples/demo/verify-moment.svg" alt="The dos verify money-moment. Two equally-confident agent claims, checked against git. Left, what the agent claims (forgeable): 'Shipped AUTH1 — the login endpoint is done' and 'AUTH2 is done too — all work completed!'. Right, what git actually records: one real commit e389e8b 'AUTH1: ship the login endpoint', and no commit anywhere mentions AUTH2. The two verdicts: dos verify AUTH AUTH1 finds the token in a real commit subject → SHIPPED, exit 0, via grep-subject; dos verify AUTH AUTH2 finds it nowhere → NOT_SHIPPED, exit 1, via none. The confident AUTH2 claim collapses the instant no commit backs it." width="100%">
  <br>
  <sub><em>Two equally confident claims, one verdict each — <code>SHIPPED</code> for the one git can back, <code>NOT_SHIPPED</code> for the one nothing landed for. Every string is verbatim output of <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/demo/verify_demo.sh"><code>examples/demo/verify_demo.sh</code></a>. <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/demo/verify_visual.html">Step through it locally</a> for the click-through version (it's an HTML file — clone the repo and open it in a browser; GitHub shows its source, not the running page).</em></sub>
</p>

The smallest real win: in a CI step or dispatch loop, replace the line that
trusts an agent's "done" with `dos verify PLAN PHASE` and branch on its exit
code (`0` shipped / `1` not). No parsing, no plan, no config — the
[CI integration cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-ci-integration.md) walks it
end-to-end. To run it on a repo shaped like yours, start with
[Onboard a repo in 10 minutes](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/01_onboard-a-repo.md).

Point the same witness at a **review queue** when commits pile up faster than
anyone can read them. [Residual review](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/residual_review/)
folds `commit-audit`'s per-commit verdict into three bands — **CLEARED** (the
diff witnessed the claim, so spend ~0 attention re-asking "did it do what it
said"), **RESIDUAL** (a claim git couldn't back — the human's 100%), and the
no-claim rest. On this repo's own last 200 commits it cleared 170 of 171
checkable claims: that's the re-review you skip, proven by git rather than a
model's confidence score. (CLEARED means the change's *shape* matched its
claim — **not** that the code is correct; correctness review still applies to
every commit. The band can only ever ask for *more* eyes, never fewer.)

*Next level up — wire the verdict into your own stack: [Wire it in](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md).*

## What goes wrong in a fleet

Run a pile of agents at once with nobody refereeing, and here's how it goes:
each worker reports its own success, and you believe the reports, because what
else is there to go on? The unchecked problems pile up quietly — a lie here,
two agents clobbering the same file there, a little scope creep, one worker
spinning in circles — until the codebase *sorta* works and nobody can safely
change it.

The trouble is you launched the agents and then let them grade their own
homework. DOS gives you the missing signal — a verdict from ground truth — so
the loop closes (the loop-hero figure above is exactly this: believe the
narration on the left, steer on a verdict on the right).

Here are the failures a fleet actually produces, each next to the ground truth
that quietly contradicts the worker's story — and the verdict DOS hands back:

| A worker… | …but the ground truth is | DOS verdict |
|---|---|---|
| says it shipped a unit of work | no commit ever landed | `verify` → **caught lie** |
| tried, but the commit silently failed | no commit ever landed | `verify` (the flake — indistinguishable from a lie *without* git) |
| edits files another worker owns | two agents, one shared file | `arbitrate` → **refuse** the second |
| overruns the file region it claimed | footprint reaches beyond the declared tree | `scope-gate` → **REFUSE** (before the write lands) |
| reports "making progress" | 0 commits, only a fresh heartbeat | `liveness` → **SPINNING** |

The first row is the most common one. The classic tell is a cheerful one-liner,
*"all work completed!"*, from a worker that did little or nothing. DOS never
reads that line; it reads the ground truth, so the claim collapses the instant
no artifact backs it (more in
[docs/108](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/108_the-cheap-lie-and-the-narration-taxonomy.md)). That's also
what makes it cheap to adopt: `verify` needs no plan, no registry, no config,
and the exit code *is* the verdict — any shell or CI step can branch on it
without parsing a word.

You adopt it through whichever surface matches how you already work — an MCP
host, your agent's runtime hooks, a bare exit-code check, Python, or your fleet
framework — and it works on a plain `git init` with zero config, getting smarter
the more you tell it. Both axes — *how deep your config goes* and *how you call
the referee* — are laid out, surface by surface, in
**[Wire it in](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md)**. The verbs the table above names
(`arbitrate`, `scope-gate`, `liveness`) are each in the
[syscall + CLI reference](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/cli-reference.md).

*Next level up — wire the verdict into your own stack: [Wire it in](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md).*

## Citation

The ideas here are written up in a paper — *"Verification Is All You Need — But
Not Where You Think"* — on the out-of-loop referee for agent fleets. A built PDF
lives at [`paper/releases/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/paper/releases); the arXiv preprint is in
preparation. Until the arXiv ID lands, cite the repository:

```bibtex
@misc{dos_kernel,
  title        = {Verification Is All You Need --- But Not Where You Think},
  author       = {Chaudhary, Anthony},
  howpublished = {\url{https://github.com/anthony-chaudhary/dos-kernel}},
  note         = {DOS --- the Dispatch Operating System; arXiv preprint in preparation},
  year         = {2026}
}
```

## License

MIT — see [LICENSE](https://github.com/anthony-chaudhary/dos-kernel/blob/master/LICENSE).

<!-- The marker below is the official MCP Registry's PyPI ownership proof: the
     registry only accepts a server.json naming the `dos-kernel` PyPI package if
     this exact token appears in the published package README. Keep it intact. -->
<!-- mcp-name: io.github.anthony-chaudhary/dos-kernel -->
