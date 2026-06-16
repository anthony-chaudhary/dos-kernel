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
