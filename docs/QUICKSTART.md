# DOS in five minutes

> The [README](../README.md) tells you *what* DOS is and *why*. This tells you
> *what to type* — a runnable hello-world you can copy-paste, start to finish, in
> a throwaway directory. No agents, no fleet, no plan files. Just the truth
> syscall, working, against a plain git repo.

Every command below was run exactly as written; the output is real, not
illustrative.

## 0. The one-sentence model

DOS answers questions about work it **doesn't trust the worker to answer
honestly** — *did this actually ship? is this run still moving, or just spinning?
may this lane start without colliding with another?* Each answer comes from
ground truth (git history, file-tree math, a clock), never from what an agent
*says* it did. This walkthrough exercises the first and most important one:
`verify` — *did (plan, phase) actually ship?*

## 1. Install

```bash
pip install dos-kernel    # the distribution name is dos-kernel
# or, from a clone of this repo:  pip install -e .
```

> **The dist name is `dos-kernel`, not `dos`.** A bare `pip install dos` pulls an
> unrelated package (a Flask/OpenAPI helper) that squats the `dos` name. The
> *import* name is still `dos` (`import dos`, the `dos` command) — only the pip
> name differs. See SECURITY.md "Supply chain".

That puts a `dos` command on your PATH. Confirm it:

```bash
dos --help
```

The only runtime dependency is PyYAML — the kernel is deliberately near-stdlib.

## 2. Make a workspace

A *workspace* is any directory DOS serves. Scaffold one and look at it:

```bash
mkdir hello-dos
cd hello-dos
dos init .
```

```
wrote .../hello-dos/dos.toml
no source dirs detected — scaffolded a single-writer 'main' lane
DOS workspace initialised. Try:  dos doctor --workspace .
```

`dos init` writes a single `dos.toml` — the *only* file DOS ever asks of your
repo. It holds your **policy** (lane taxonomy, paths, ship-stamp grammar); the
package holds the **mechanism**. You can open it now, but the defaults are fine
for this tour.

> The lane taxonomy is **seeded from your top-level directories** — one
> concurrent lane per source dir, so a real repo gets usable parallel lanes for
> free (this empty `hello-dos` has none, so it scaffolds a single `main` lane).
> It's a one-time scaffold written as editable data, not a live filesystem
> binding — reshape it freely. See HACKING.md, "the folders→lanes convention."

> **Want the workflow skills too?** Add `--skills` and `dos init` ALSO copies the
> generic skill screenplays into `.claude/skills/` as **editable local files** —
> so the adoption path is one command, not a manual copy out of the wheel:
>
> ```bash
> dos init --skills .            # dos.toml + the core skills (next-up/dispatch/loop/replan)
> dos init --skill dos-promote . # dos.toml + just a named skill (repeatable)
> dos init --all .               # the full pack (the SKP five + the operator tier)
> ```
>
> The copies are ordinary files you edit and run (`/dos-dispatch`, `/dos-promote`,
> …); the package-data is the *seed*, not a runtime binding. Re-running is
> idempotent — a diverged local copy is never clobbered without `--force`.

> **Already running an agent? Bind the verdict to it in one command.** Add
> `--hooks <runtime>` and `dos init` ALSO wires the three DOS hooks into *that
> runtime's own config file* — so a refused tool-call is **denied before it runs**,
> a stalled stream is re-surfaced, and a stop on an unverified claim is refused. It
> works on whichever agent you already use, not just Claude Code:
>
> ```bash
> dos init --hooks claude-code .   # writes .claude/settings.json
> dos init --hooks cursor .        # writes .cursor/hooks.json
> dos init --hooks codex .         # writes .codex/config.toml
> dos init --hooks gemini .        # writes .gemini/settings.json
> ```
>
> The block is **merged** into any existing config (your other hooks survive), and
> re-running is idempotent. This is the **enforcement** path (the host *denies* on a
> DOS verdict). The **advisory** path — the agent *calling* `dos_verify` itself —
> is the MCP server, wired separately (`dos-mcp` in the host config; see
> [the MCP README](../src/dos_mcp/README.md)). Use both: hooks enforce, MCP advises.
> Full design: [docs/221](221_the-cross-vendor-hook-installer.md).

```bash
dos doctor --workspace .
```

```
DOS v0.23.0
workspace root      .../hello-dos
execution-state     .../hello-dos/dos.state.yaml
plans glob          docs/**/*-plan.md
stamp convention    generic (any/no dir prefix)  [style=grep]
verifiability        no commits to read (not a git repo, or empty history)
concurrent lanes    (none)
exclusive lanes     main
autopick ladder     (none)
admission predicates disjointness, self-modify
judges (JUDGE rung)  abstain, llm, operator-decision, similarity
evidence sources    null, ci_status, citation_resolve, os_acceptance, paste_log  (verify: git-only)
enforce handlers     observe
overlap policy      prefix*  (ratio_max=0.333; prefix floor always on)
stall reader        REPEATING>=3, STALLED>=5  (ignore_tools: (none))
supervisor target   1  (count_spinning_as_alive=yes, reap_stalled=yes, spin_halt_after=off)
is git workspace    no
runtime hooks       none wired   (run `dos init --hooks <runtime>` to bind)
layout style        dos
environment print   <hash>  (kernel v0.23.0 @ <sha>; py 3.13.7; <os>)
  declared tools    (none declared)
dos home            .../dos  (0 project(s) indexed)
```

> The **`runtime hooks`** line shows which agent runtimes have the DOS hooks wired
> in *this* workspace — so after you run `dos init --hooks cursor .` it reads
> `runtime hooks  cursor (4)`, confirming the binding took (a mis-wired hook is
> otherwise a silent no-op). It's read-only — running `doctor` writes no config.

> Your output may show extra entries — e.g. `admission predicates … budget-guard`
> or `overlap policy prefix*, semantic-groups` — if you've pip-installed the
> `examples/dos_ext` skeleton; those are *your* registered plugins showing up live.
> A plain `pip install dos-kernel` shows the built-ins above.

`doctor` is your "what am I actually configured as?" command. A few lines to note.
`stamp convention    generic` is the grammar `verify` will use to recognize a ship
in your commit messages (we use it in a moment). `verifiability    no commits
to read` is DOS being honest up front: this is an empty directory, not a git repo
yet — so there's nothing for the oracle to read (that changes the moment you
commit). The `evidence sources … (verify: git-only)` line names the extra
witnesses `verify` *could* consult (a CI status, a pasted log) and confirms that
out of the box it reads **git only** — nothing else is trusted until you wire it
in. And the bottom block is DOS describing *itself*: which `enforce handlers`,
`overlap policy`, and `stall reader` are active, plus the `environment print` —
the kernel version, commit, Python, and OS a verdict would run *under*, so a
result is reproducible.

## 3. Do some work — and ship a "phase"

DOS has no opinion about *how* you work; it reads your **git history** as the
record of what happened. The unit it tracks is a **phase**: a named chunk of work
identified by an id like `AUTH1` (a series `AUTH`, phase `1`). You stamp a phase
as shipped by naming it at the start of a commit subject, `<PHASE-ID>: <message>`:

```bash
git init -q
git config user.email you@example.com    # if you haven't set a global identity
git config user.name "You"

# do the work...
echo "def login(): ..." > login.py

# ...then ship it with a phase-id at the front of the subject:
git add -A
git commit -m "AUTH1: ship the login endpoint"
```

That's the whole convention under the generic stamp grammar: **a phase id, a
colon, then your message.** The id needs a digit (it names a *numbered* phase),
which is what separates a ship (`AUTH1:`) from an ordinary `fix: typo` commit.

## 4. The payoff — `verify`

Now ask the truth syscall whether `AUTH1` shipped. You wrote no plan file, no
registry, nothing but the commit — and it still answers, from git history alone:

```bash
dos verify --workspace . AUTH AUTH1
```

```
SHIPPED AUTH AUTH1 e389e8b (via grep-subject)
```

That `via grep-subject` is DOS telling you *how it knows*: it found the phase
token in a commit **subject** in the git log — not in any registry, not from
anyone's say-so. (Reading the *rung* matters: a subject is the cheapest, most
forgeable place to claim a ship, so the verdict names it explicitly.) The exit
code is the verdict (`0` = shipped), so a script can branch on it.

Now ask about a phase you *haven't* shipped:

```bash
dos verify --workspace . AUTH AUTH2
```

```
NOT_SHIPPED AUTH AUTH2 (via none)
```

`via none` means DOS looked everywhere it knows — registry, then git history —
and found nothing. Exit code `1`. **This is the entire point of DOS in one
contrast:** an agent can *claim* `AUTH2` is done all it likes; `verify` reports
what the artifacts say, which is that it isn't.

> **What you just proved.** Nobody *told* DOS that `AUTH1` shipped — you wrote no
> plan, no registry, no status file. The only input was a git commit you made, and
> the verdict was re-derived from git history:
>
> ```text
>   you committed:   AUTH1: ship the login endpoint   ──┐  (git — not self-report)
>   dos verify AUTH AUTH1  ──────────────────────────────┴─►  SHIPPED      (via grep-subject)
>   dos verify AUTH AUTH2  ───────────────────────────────►  NOT_SHIPPED  (via none)
> ```
>
> An agent can *narrate* "AUTH2 done" all day; `verify` reads the artifacts, not
> the narration. That gap — claim vs. ground truth — is the entire kernel.

> **The grammar matters.** `verify` recognizes the **glued phase-id** form
> (`AUTH1: …`, verified as `dos verify AUTH AUTH1`) out of the box. If your repo
> stamps ships differently — under a directory (`docs/AUTH1: …`), or with its own
> prefixes — declare that once in `dos.toml`'s `[stamp]` table and every surface
> picks it up. See [HACKING.md](HACKING.md) §"the four data tables". Run
> `dos doctor --check` to be told if your declared grammar doesn't match your own
> commits.

## 5. One more syscall — `arbitrate`

`verify` distrusts a *finished* claim. `arbitrate` is the *admission* kernel: may
a new unit of work ("lane") start right now without colliding with work already
in flight? It's a pure function — you hand it the request and the live leases, it
hands back a decision. With nothing else running:

```bash
dos arbitrate --workspace . --lane main --leases '[]'
```

```json
{"auto_picked": false, "free_clusters": [], "lane": "main", "lane_kind": "global",
 "outcome": "acquire", "pick_count": null,
 "reason": "exclusive lane 'main' — no other loop live, admitted.", "tree": ["**/*"]}
```

`outcome: acquire` — green light. Note `lane_kind: global`: the scaffolded `main`
lane is **exclusive** (it owns `**/*`, the whole tree), so the rule here is "it
must run alone" — hand `arbitrate` a *live* lease and it would refuse the second
request instead. On a **concurrent** lane (one per source dir in a real repo), the
refusal trigger is finer-grained: an overlapping live lease, where the file-tree
disjointness rule is what stops two agents editing the same files at once. Either
way the refusal is *structured* — a named reason you can look up: `dos man wedge`
lists the whole refusal vocabulary, and
`dos man wedge <NAME>` prints a generated man page for any one of them.

## Where to go next

You've now used the two load-bearing syscalls. The rest of the surface:

| You want to… | Command |
|---|---|
| See your active config & taxonomy | `dos doctor [--json]` |
| Check a finished claim | `dos verify PLAN PHASE` |
| Check an in-flight run is moving, not spinning | `dos liveness --run-id … --start-sha …` |
| Decide if a lane may start | `dos arbitrate --lane … --kind … --leases …` |
| Watch what's **running** (lanes/leases/verdicts/commits) | `dos top` (read-only; `--once` for one frame) |
| See what's **waiting on you** (refusals to resolve) | `dos decisions` |
| Check the plan's **claim** vs. the **ground truth** | `dos plan [--once]` |
| Read the refusal vocabulary | `dos man wedge [REASON]` |
| Gate an empty work-packet | `dos gate PACKET` |

Those last three are the **read-only live projections** — each mutates nothing and
works without extra dependencies (`--once` / `--json` on a bare install; the live
redraw is the optional `[tui]` extra). The when-to-use-each map is
[Three live projections](../README.md#three-live-projections-read-only-tuis).

- **The full CLI** is in the [README](../README.md#cli).
- **Already running a fleet through LangGraph, CrewAI, AutoGen, or an Agents
  SDK?** Bolt the referee onto the framework you have — one function at its
  believe-the-agent seam, every recipe executed against the real framework:
  [the fleet-framework cookbook](../examples/playbooks/cookbook-fleet-frameworks.md).
- **To extend DOS** — add your own refusal reasons, lanes, renderers, or safety
  predicates *without forking the package* — read [HACKING.md](HACKING.md) and
  copy [`examples/dos_ext/`](../examples/dos_ext/).
- **Why it's shaped this way** (and the evidence it pays off across a fleet):
  [the docs index](README.md) maps the design notes.
