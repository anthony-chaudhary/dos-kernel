# AGENTS.md — orientation for an AI agent working in this repo

> You are an AI agent reading this repo to understand it or change it. This file
> is your front door: what DOS is in three lines, how to build and check your
> work, the short list of files actually worth reading, and the rules the kernel
> enforces on its own contributors. It is deliberately short and navigational —
> the detail lives behind the links.
>
> **Fitting, given what DOS is:** the kernel exists to *not believe an agent's
> self-report*. So don't take this file on faith either — every claim here is one
> you can check with a `dos` command or a `git` read, and where that's the point,
> the command is shown. (Human-oriented? Read [README.md](README.md) instead; it
> is the same story written for a person browsing GitHub.)

## What DOS is (the 30-second version)

DOS is a small, deterministic **kernel** that referees a fleet of AI agents
working on a shared git repo. Every agent *narrates* — "I shipped it," "tests
pass," "still making progress." DOS treats all of that as a **claim, not a fact**,
and hands back a verdict read from ground truth the agent could not have
authored: git history, the file tree, a clock, an environment's own state.

- **`verify`** — did `(plan, phase)` actually ship? (from git ancestry, never the agent's word)
- **`arbitrate`** — may this agent take this lane, or does it collide with one already running?
- **`liveness`** — is the run ADVANCING, or just SPINNING / STALLED?
- **`refuse`** — say no with a reason a machine can act on.

Nothing here is coding-specific: a repo declares its own rules (lanes, paths,
ship-stamp grammar) as data in `dos.toml`; the kernel supplies only the
machinery. Reach it from the `dos` CLI, an MCP server, or `import dos`.

## Run it yourself first — the 60-second proof

Before reading anything, watch the core idea happen:

```bash
pip install -e .       # editable install from this clone (PyYAML is the only runtime dep)
dos quickstart         # scaffolds a throwaway repo, commits, then:
                       #   SHIPPED AUTH AUTH1 … (via grep-subject)   exit 0
                       #   NOT_SHIPPED AUTH AUTH2 … (via none)        exit 1
```

One `SHIPPED`, one `NOT_SHIPPED`, from git alone — that contrast *is* DOS. The
hand-typed version of the same thing is [docs/QUICKSTART.md](docs/QUICKSTART.md)
(5 minutes, every line is real output).

## When the user asks you ABOUT DOS (the consumer moves)

The person who cloned this is often not a kernel contributor — they want to know
what they fetched, see it work, install it, or wire it into their agent host.
These are the verified moves; answer from this table instead of re-deriving them
from the long README:

| The user asks | The move |
|---|---|
| "What is this? Show me." | `dos quickstart` — the 60-second caught-lie demo above. The hand-typed version is [docs/QUICKSTART.md](docs/QUICKSTART.md). |
| "Install it" (to *use*) | **`dos-kernel` is on PyPI (since 2026-06-10)** — `pip install dos-kernel` (runtime, PyYAML-only) or `pip install "dos-kernel[mcp]"` (adds the MCP server); tracking unreleased `master` is `pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"`, and inside this clone `pip install -e .` works the same. Never `pip install dos` — that bare name is an unrelated squatter package. The full matrix (uv, pipx, wrappers, WSL) is [docs/INSTALL.md](docs/INSTALL.md). |
| "Install it" (to *work on it*) | `pip install -e ".[dev,mcp]"` — exactly what CI installs; brings pytest/ruff/mypy. |
| "Wire it into Claude Code" (or Cursor / Codex / Gemini / Antigravity) | Enforcement (hooks): `dos init --hooks claude-code <their repo>`. Advisory (MCP): register `dos-mcp` in the host config — or install the bundled plugin, [claude-plugin/README.md](claude-plugin/README.md) (prerequisite: the `[mcp]` install above). Hooks enforce, MCP advises; the repo recommends both. (Trae is the advisory-only exception: it has no hook seam, so it gets MCP + rules + skills and deliberately no `--hooks trae` — [docs/294](docs/294_trae-advisory-only-the-host-with-no-hook-seam.md).) |
| "Use it on MY repo" | `cd <their repo> && dos init . && dos doctor` — then `dos verify PLAN PHASE` answers from their git history. Works on a plain git repo; the one `dos.toml` is all the config. |
| "Wire it into LangGraph / CrewAI / AutoGen / the OpenAI or Claude Agents SDK" | [examples/playbooks/cookbook-fleet-frameworks.md](examples/playbooks/cookbook-fleet-frameworks.md) — one function at that framework's believe-the-agent seam (a referee node, a termination condition, an output guardrail); every recipe's seam was executed against the real framework, versions + verbatim output in the file. |
| "Run the tests" | The `[dev]` install in the next section, then `python -m pytest -q` — and read that section's foreground note before you start. |

## Build, test, and check your work

```bash
pip install -e ".[dev,mcp]"       # editable + the test/lint toolchain (exactly what CI installs)
python -m pytest -q               # the full kernel suite — must stay green (~4,100 tests, ~3–4 min)
dos doctor --workspace .          # what IS this workspace? (the config seam, made visible)
ruff check src/dos src/dos_mcp    # lint exactly as CI does (the wider tree is NOT lint-clean — don't "fix" it)
```

**Two traps that bite an agent here.** (1) A bare `pip install -e .` deliberately
installs only PyYAML — `pytest` comes from the `[dev]` extra, so the suite command
above fails without it. (2) **Run the suite in the foreground and wait for its
verdict.** It takes a few minutes; in a one-shot/headless session do NOT launch it
in the background and end your turn — your session ends before the suite does, and
the user receives a promise instead of a verdict.

**This repo is itself a DOS workspace** (`dos doctor` reports
`is_kernel_repo: true`), so adjudicate your own work with the kernel — don't trust
your own narration any more than the kernel trusts an agent's:

```bash
dos verify --workspace . docs/82_liveness-oracle-plan liveness   # did a phase actually ship? (asks git)
dos commit-audit --workspace . HEAD                              # does a commit's SUBJECT match its own diff?
dos arbitrate --workspace . --lane src                          # may I take this lane right now?
```

The full working ritual (`doctor → arbitrate → edit → verify → commit-audit`) is
the **"DOS on DOS"** section of [CLAUDE.md](CLAUDE.md). Use it for real, not just
as a demo: before you claim a `docs/NN_*.md` phase is done, `dos verify` it; after
you commit, `dos commit-audit` it. The oracle answers from git, so let the oracle
close the phase, not your prose.

## Read ONLY these first (the repo is large; most of it is a build journal)

The tree has ~213 files under `docs/` and ~265 under `benchmark/`. **Almost none
of that is required to understand or use DOS** — it is a dated build journal and
research record. Do not try to read it all. Start with exactly these, in order:

| Read this | To learn |
|---|---|
| [README.md](README.md) | What DOS is, the syscall ABI, the full CLI, how to adopt it. The front door for humans. |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | The runnable 5-minute hello-world. |
| [CLAUDE.md](CLAUDE.md) | **The architecture contract** — the 4 layers, the one-way import rule, where code is allowed to live. Read this before editing any `src/dos/` file. |
| [docs/HACKING.md](docs/HACKING.md) | Extend DOS *without forking it* — reasons, lanes, judges, renderers as workspace data (7 extension axes). |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to send a change: the layering rule and the CI-enforced litmus tests. |

Need to go deeper into the *why* or the research?

- **The design notes** (`docs/79`, `102`, `108`, `138`, `182`, `204` …) are essays
  explaining the thinking the code rests on. The curated index — guides vs. design
  notes vs. the dated journal — is [docs/README.md](docs/README.md). The numbers
  are **chronology, not a reading order**, and a few collide (there are two
  `docs/191`), so prefer the index over guessing a number.
- **The benchmarks** are six independent research programs that *measure* DOS
  claims; they are consumers of the kernel, never part of it. Start at
  [benchmark/README.md](benchmark/README.md) / [benchmark/BENCHMARKS.md](benchmark/BENCHMARKS.md),
  not by listing the directory.
- **The per-module map** (every kernel leaf, its `docs/NN` lineage) is the cold
  tier, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Read it before touching a
  specific leaf.

## The layout in one screen

| Path | What it is |
|---|---|
| `src/dos/` | **The kernel** — pure verdict modules (`oracle`, `arbiter`, `liveness`, …). The thing you are mostly here to understand. |
| `src/dos/drivers/` | **Drivers** — the only place provider/host/IO policy lives (a host's lanes, an LLM judge). Outside the kernel boundary. |
| `src/dos_mcp/` | The MCP server (a *separate* top-level package on purpose; the kernel never imports it). |
| `src/dos/skills/` | The generic skill pack — package **data**, not code (nothing imports it; the files shell `dos` verbs). |
| `tests/` | The kernel suite. Many tests are litmus tests that pin the architecture rules below. |
| `examples/` | Runnable playbooks, copy-me extension skeletons (`dos_ext/`, `drivers/`), example workspaces. The fastest way to see real usage. |
| `docs/` | Guides (`QUICKSTART`, `HACKING`, `ARCHITECTURE`) + the numbered design-note / build journal. |
| `benchmark/` | Six research programs measuring DOS claims. Consumers, not kernel. |
| `paper/`, `scripts/`, `claude-plugin/`, `.github/` | The paper (generated — never hand-edit the `.tex`), release/dev tooling, the bundled Claude Code plugin, CI. All operate *on* the package; none is imported by it. |

## The rules the kernel holds itself to (so your change lands)

DOS has a strict **4-layer architecture** with a one-directional import rule, and
several of these are enforced by tests in `tests/` (a violation turns the suite
red, so you'll find out fast). The ones most likely to bite an edit:

- **The kernel imports no host and no vendor.** No module under `src/dos/` (except
  `drivers/`) may name a host (`job`, `apply`, …) or a vendor (`claude`, `gemini`,
  `cursor`, …) as a code identifier. Host/vendor specifics live in a **driver** or
  come from `dos.toml`. (Pinned by `test_vendor_agnostic_kernel.py`,
  and the host litmus in `CLAUDE.md`.)
- **`verify` needs no plan.** The truth syscall must answer against a plain git
  repo with no plan and no registry. (Pinned by `test_verify_no_plan.py`.)
- **Every verdict is a pure `classify(evidence, policy)`.** I/O is gathered at the
  CLI boundary and passed in as data; a verdict function does no disk/network I/O.
  This is what makes the kernel testable.
- **The package never assumes it lives in the repo it serves.** Every path resolves
  against `SubstrateConfig.root` (`--workspace` › `$DISPATCH_WORKSPACE` › cwd),
  never `__file__`.
- **A policy/scorer/judge can only refuse MORE, never admit a collision.**
  Extensions are conjunctive under a deterministic floor — a buggy or hostile one
  degrades to the safe default, it cannot loosen safety.

The canonical statement of all of this, with the full layer table and the litmus
list, is [CLAUDE.md](CLAUDE.md). If a doc ever seems to contradict it, the doc is
the stale one — CLAUDE.md is the contract.

## Committing (when you're working in here)

A commit **is** the ship-stamp `dos verify` reads, so a finished, green change
that isn't committed is a phase the kernel will call `NOT_SHIPPED`. When a unit of
work is complete and `pytest -q` is green, commit it — this trunk is `master` and
the preference is to land promptly, not defer. A few specifics:

- **Commit only the lane you worked.** Stage the specific files you touched
  (`git add src/dos/… docs/…`); never a blanket `git add -A`. The working tree here
  is often shared with another agent's in-flight edits — sweeping them into your
  commit is the exact `SELF_MODIFY` / disjoint-lane hazard the kernel refuses.
- **Match the existing commit-subject grammar** (`git log` shows it). Do **not**
  add a `Co-Authored-By` or other agent-attribution trailer — commits here carry
  no agent co-author, even if your harness appends one by default.
- **Out of scope? File an issue, don't widen the commit.** A finding that isn't
  your current task goes to `gh issue create` — with a checkable done-condition,
  a lane guess, and where you found it. Issue text is public and the leak gate
  never scans it: no private paths or hostnames. Never close an issue off your
  own narration — put `Fixes #N` in the commit body and let the landing close
  it (or use the `issue-verify` skill for an evidenced manual close). The full
  rule is the "Out-of-scope findings" section of [CLAUDE.md](CLAUDE.md).
- **Ask first only for the hard-to-reverse / outward-facing** — pushing, tagging, a
  release, history rewrites. A local commit on `master` is none of those.

---

*This file is for any agent (Claude Code, Cursor, Codex, Gemini CLI, Aider, an
Agent-SDK app). [CLAUDE.md](CLAUDE.md) holds the Claude-Code-specific working
notes and the full architecture contract; it is the deeper read once this
orientation has landed.*
