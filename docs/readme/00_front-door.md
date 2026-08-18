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

## Setup — use DOS in your repo

DOS is a Python package you install **into the existing git repo where your
agents work**. You do not need to clone or fork the DOS source unless you plan
to develop DOS itself.

```bash
cd path/to/your-repo
pip install dos-kernel
dos init --hooks auto   # detects the agent runtime(s) already in this repo
dos doctor              # shows exactly what this workspace is using
```

From then on, your agent cannot tell you **"done"** unless the work actually
landed, two agents cannot silently overwrite each other's files, and a run
that stalls gets flagged instead of quietly spinning. `dos init` prints the
config and hook entries it wrote; removing those generated entries undoes the
setup. If no runtime is detected, it says so and lists the explicit choices —
it never guesses.

| What you want | Start here |
|---|---|
| **Use DOS in my repo** | Install the package, then run `dos init --hooks auto` in that repo (the setup above). |
| **See the 60-second demo** | Run `uvx --from dos-kernel dos quickstart`; it creates and removes a throwaway repo. |
| **Read or change DOS itself** | Clone this repository and use the contributor install; most users do not need the source clone. |

<sub>**v0.30.0** · 5,600+ tests · CI: Python 3.11–3.13 on Linux + a Windows 3.13
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
