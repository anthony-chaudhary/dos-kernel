# DOS — the Dispatch Operating System

> ### Catch your AI agents when they lie about what they shipped.

[![PyPI](https://img.shields.io/pypi/v/dos-kernel)](https://pypi.org/project/dos-kernel/)
[![Python versions](https://img.shields.io/pypi/pyversions/dos-kernel)](https://pypi.org/project/dos-kernel/)
[![CI](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/ci.yml)
[![verified by DOS](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml/badge.svg)](https://github.com/anthony-chaudhary/dos-kernel/actions/workflows/dos-gate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/anthony-chaudhary/dos-kernel/blob/master/LICENSE)

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

> ⚡ **Or just add it — two commands, zero decisions.** From the repo where your
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

<sub>**v0.25.0** · 3900+ tests · CI: Python 3.11–3.13 on Linux + a Windows 3.13
smoke run · the only runtime dependency is **PyYAML** · **MIT**.</sub>

> 🧭 **Want it in plain words first?** What DOS is, what it catches, and what
> adopting it costs — no code: **[the plain-words version](#the-plain-words-version)**, just below.

> 🧭 **Or route yourself:** the page runs shallow → deep, and
> **[Who this is for](#who-this-is-for)** matches the question you brought to the
> section that answers it.

> **Reading this as an AI agent?** Start with **[AGENTS.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md)** — a short
> orientation written for you: what DOS is in three lines, how to build/test/check
> your work, the ~5 files actually worth reading, and the architecture rules a
> change must satisfy.

> 🔤 **Five words the rest of this page leans on.** A **plan** is a named goal
> (`AUTH`); a **phase** is one shippable step of it (`AUTH1`); a **lane** is the
> slice of the file tree one agent may touch; the **oracle** is the part of DOS
> that reads the evidence and rules; a **stamp** is the mark a shipped phase
> leaves in a commit subject (`AUTH1: …`) — the thing the oracle greps for.
> That's the whole vocabulary.
