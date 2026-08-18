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
| *"Show me it working, fast."* | [Optional demo](#optional-demo--try-it-in-60-seconds), just below — one command in a throwaway repo |
| *"I already run agents — how do I wire the verdict into **my** stack?"* | [Wire it in](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/wire-it-in.md) — MCP, runtime hooks, the exit-code tier, fleet frameworks, and the install matrix |
| *"What's the full command / syscall surface?"* | [The syscall ABI & CLI reference](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/cli-reference.md) — every verb, the three live screens, the verdict journal |
| *"I run a fleet every day — how do I watch it, triage it, debug it?"* | [Operating a fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/operating-a-fleet.md) + [Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md) |
| *"How do I bend it to my org without forking it?"* | [Extending it](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/extending.md) — the seven axes, the docs index, the playbooks |
| *"What is actually proven, and can I re-run it?"* | [For researchers](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/for-researchers.md) — claims → invariants → reproduction |
| *"I'm an AI agent orienting in this repo."* | **[AGENTS.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md)** — what DOS is in three lines, build/test/check, the ~5 files worth reading |
| *"What surfaces are stable and what's the deprecation window?"* | **[docs/STABILITY.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/STABILITY.md)** — the compatibility promise, what the version number means, and what will never break |
