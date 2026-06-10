# DOS — the Dispatch Operating System

> ### Catch your AI agents when they lie about what they shipped.

<!-- PyPI / Python-version badges land with the PyPI release — until dos-kernel is
     on PyPI they'd render broken (404 / unknown version). -->
[![CI](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml)
[![verified by DOS](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

<p align="center">
  <img src="docs/assets/loop-hero.svg" alt="Two agent fleets side by side. Left, no referee: agents all report 'done!', every report is believed, and silent corruption (lies, collisions, spin) piles up into a codebase that 'sorta works' and can't be changed. Right, DOS adjudicates: dos verify reads git and the run branches to SHIPPED (exit 0, land it) or NOT_SHIPPED (exit 1, re-dispatch — caught), and that verdict steers the next step." width="100%">
  <br>
  <em>Run a fleet of agents on one repo. The left loop just feels like progress; the right one you can steer.
  The only difference is a verdict DOS reads from the real world — here, git — never the agent's word.</em>
</p>

An AI agent will tell you it finished. DOS checks the real world instead of
taking its word — and the nearest piece of the real world is your git history.

An agent says it shipped the login endpoint. Did it? Run one command,
`dos verify`, and it answers from the artifacts the work actually left behind,
not from what the agent typed. If a commit backs the claim, you get `SHIPPED`
and exit code `0`. If nothing landed, you get `NOT_SHIPPED` and exit code `1`.
The agent's story never enters into it. (Git is just the first witness DOS
reads; the file tree, the clock, a CI status, a test environment's own state
are others — anything the agent didn't author.)

```bash
dos verify AUTH AUTH1   # → SHIPPED      AUTH AUTH1 e62f74d   (exit 0)
dos verify AUTH AUTH2   # → NOT_SHIPPED  AUTH AUTH2           (exit 1)
```

That's the smallest version. It scales up, too: point a dozen agents at one
repo — in CI, in a fleet, racing on the same files — and DOS also tells you
which ones are stepping on each other, which one is spinning in circles, and
which claim of "done" is real. Every answer comes from the artifacts (git, the
file tree, the clock), never the narration. It works on a plain `git` repo with
zero config, and the only thing you ever install is one small Python package.

> ⏱️ **Want to try it right now?** Jump to **[Try it in 60 seconds](#try-it-in-60-seconds)**
> — one command, real output, then come back for the why.

<sub>**v0.22.0** · 3900+ tests · CI: Python 3.11–3.13 on Linux + a Windows 3.13
smoke run · the only runtime dependency is **PyYAML** · **MIT**.</sub>

<details>
<summary><strong>The 30-second mental model</strong> (one paragraph, plain words) — click to expand</summary>

> Coding agents narrate everything: what they shipped, which files they touched,
> whether they're still making progress. DOS treats all of that as a claim, not
> a fact, and hands you a verdict built from what actually happened. Under the
> hood it's a small, deterministic kernel — the part that decides ground truth
> across a crowd of unreliable workers and keeps their edits from colliding.
> Nothing about it is coding-specific: your repo declares its own rules (which
> file regions each agent may touch, how a commit signals "done") as data in one
> `dos.toml`, and the kernel supplies only the machinery. You reach that
> machinery through small, do-one-thing commands — `verify`, `arbitrate`,
> `liveness`, `refuse` — from the `dos` CLI, an MCP server wired into the agent
> host you already run, or straight from Python.

</details>

> **Reading this as an AI agent?** Start with **[AGENTS.md](AGENTS.md)** — a short
> orientation written for you: what DOS is in three lines, how to build/test/check
> your work, the ~5 files actually worth reading, and the architecture rules a
> change must satisfy.
