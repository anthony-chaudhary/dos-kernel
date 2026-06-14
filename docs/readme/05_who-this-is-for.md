<a id="who-this-is-for"></a>

## Who this is for

This README runs shallow to deep — try it, see the failure it fixes, audit the
evidence, wire it in, extend it. You don't have to read it in that order. Find
the question you arrived with and jump; the rows route by the question, not
your job title, and every section hands off to the one above it.

| You're asking… | Start at | Then |
|---|---|---|
| *"I vibe-code with an AI — did it actually do what it said?"* | ["Did my AI actually do it?" in 30 seconds](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/00b_did-my-ai-do-it.md) — one command on your own repo | the [plain-words version](#the-plain-words-version) of why it works |
| *"What is this, in plain words, and why should my team care?"* | [the plain-words version](#the-plain-words-version), just below — no code | hand the [60-second demo](#try-it-in-60-seconds) to whoever runs your agents |
| *"Show me it working, fast."* | [Try it in 60 seconds](#try-it-in-60-seconds) | [what goes wrong in a fleet](#what-goes-wrong-in-a-fleet) without it |
| *"I already run agents — how do I wire the verdict into **my** stack?"* | [How you plug it in](#how-you-plug-it-in) | [the MCP lie detector](#give-your-agent-a-lie-detector-mcp) · [Install](#install) |
| *"I run a fleet every day — how do I watch it, triage it, debug it?"* | [Operating a fleet](#operating-a-fleet) | [Three live projections](#three-live-projections-read-only-tuis) · [Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md) |
| *"How do I bend it to my org without forking it?"* | [Hacking it](#hacking-it) | [docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md) — the seven extension axes |
| *"What is actually proven here — and can I re-run it?"* | [For researchers](#for-researchers) — claims → invariants → reproduction | [What's proven and what's still a bet](#whats-proven-and-whats-still-a-bet) · [Citation](#citation) |

(The seventh reader — an AI agent orienting itself in this repo — already has
its own front door: **[AGENTS.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md)**, per the note above.)

### The plain-words version

A coding agent does some work, then tells you how it went. Usually the story is
true. Sometimes it isn't — the cheerful *"all work completed!"* from a worker
that actually shipped nothing is the single most common failure in agent
fleets. With one agent you catch that yourself, because you read its work
before trusting it — which is a real cost you're already paying, you just
haven't called it one: re-reading the output is the tax for taking the report
on faith.

Run twenty agents at once and that tax stops being payable — nobody reads
everything. Each worker grades its own homework, you believe the reports
because what else is there to go on, and
the unchecked problems pile up quietly — a false "done" here, two agents
overwriting the same file there, one worker spinning in circles burning money.
None of it is loud. The codebase ends up *sorta* working, and nobody can safely
change it.

DOS is the referee. It's a small, deterministic program that never reads the
agent's story; it reads what actually happened — the commit, the file, the
clock — and hands you a verdict. An agent says "done"? DOS checks whether the
work really landed in your repo's history. An agent says "making progress"?
DOS checks whether anything real has changed. Two agents head for the same
files? DOS admits one and refuses the other, with a reason a machine can act
on. Every verdict is computed from artifacts the agent didn't author, so no
amount of confident narration can move it.

Nothing about it is coding-specific, and it imposes no framework. Your repo
declares its own rules — which file regions each agent may touch, how a
finished unit of work signals "done" — as data in one small config file, and
DOS supplies only the machinery. You reach it through small, do-one-thing
commands, through the agent host you already run, or straight from Python. And
it stays in its lane: it tells you reliably *what happened*, never whether the
committed code is *good* — quality stays with your tests, your reviews, and
you.

Adopting it costs one engineer about an afternoon: one small Python package
(one runtime dependency), one optional config file — and it works on day one
against a plain git repository with neither. If your team is about to go from
one agent to many, the missing piece is usually not a smarter agent. It's a
referee that doesn't believe any of them.

Convinced enough to watch it work? [Try it in 60 seconds](#try-it-in-60-seconds)
is one command — or hand this page to whoever runs your agents.
