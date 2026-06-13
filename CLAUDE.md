# DOS — the Dispatch Operating System

> **The kernel is the part that doesn't believe the agents.**

DOS is the domain-free **trust substrate** for fleets of autonomous agents: a
small, deterministic kernel that adjudicates ground truth across many
unreliable, self-narrating workers — and serializes their effects on shared
state — *without believing what they say they did*.

This file is the **architecture contract**: the rules every edit must satisfy.
Detail lives in two cold-tier docs — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
(per-module map, full syscall table, full litmus arguments, glossary) and
[docs/DOGFOOD.md](docs/DOGFOOD.md) (the worked DOS-on-DOS ritual). Read the
relevant cold section before editing a kernel leaf.

> **Write plainly (operator directive, 2026-06-10).** Plain Feynman English in
> this file and agent memory: short common words, short sentences, one idea at
> a time. Simplify the wording, never the facts.

> **Asking ABOUT DOS rather than editing it?** Don't answer from this file —
> use the "When the user asks you ABOUT DOS" table in [AGENTS.md](AGENTS.md).
> Lead with `dos quickstart`. Install is `pip install dos-kernel` (the bare
> `dos` name on PyPI is an unrelated squatter — never install or pin it); host
> wiring is `dos init --hooks <runtime>` or
> [claude-plugin/](claude-plugin/README.md).

> **Tracked here = ships — route privacy at AUTHORING time.** This IS the
> public repo; every tracked file is public on the next push, no scrub step
> between. Strategy essays, operator notes, and spikes are born in the private
> sibling `../dos-private`, never here; engineering design plans (the numbered
> `docs/NN_*.md`) stay here. Never write a dev-machine absolute path, hostname,
> or personal identifier into a tracked file — including JSON-escaped forms in
> logs/fixtures; synthetic fixtures use neutral roots. Cross-link by filename,
> don't duplicate; nothing under `src/dos/` depends on `dos-private`.

## The layering — keep these apart

One rule: **mechanism is the kernel; policy is a driver; the phased-plan
workflow is host concern.** Four layers, one-directional imports (each may
import the layer above it, never below). Long form: ARCHITECTURE.md.

- **1. Kernel** (`src/dos/*.py` not claimed below; imports stdlib + config +
  seam data + kernel siblings) — the syscalls, pure: every verdict is
  `classify(evidence, policy)`, I/O at the CLI boundary only. No host names,
  no plan schema, no I/O policy. Roster = the directory listing (hand-kept
  lists rot).
- **2a. Seam** (`src/dos/config.py`; stdlib + 2b) — `SubstrateConfig`:
  workspace root, lane taxonomy, refusal vocabulary, stamp grammar, discovered
  `WorkspaceFacts`; generic `main`/`global` default.
- **2b. Seam data** (`src/dos/{reasons,stamp}.py`; stdlib only) — closed sets
  as data: `ReasonRegistry`, `StampConvention`, declared in `dos.toml`
  ([docs/HACKING.md](docs/HACKING.md)).
- **3. Helpers** (`src/dos/{cli,_tree,timeline}.py` + projection pairs;
  imports 1–2) — policy-free shells: CLI, tree algebra, timeline, read-only
  projections + TUIs, `dos notify`; no lease, no launch, no mutation.
- **4. Drivers** (`src/dos/drivers/*.py`; imports 1–2) — host policy packs,
  the JUDGE rung (advisory, fail-to-abstain), transports; the only home for
  provider/network/non-determinism/vendor names. New host, judge, or transport
  = a module or plugin here, never a kernel edit.

Four things live OUTSIDE the layers, operating *on* the package (they `import
dos`; nothing under `src/dos/` imports them): release/dev tooling (`scripts/`,
`.claude/skills/`), the MCP server (`src/dos_mcp/`, separate top-level package,
`mcp` dep only in the `[mcp]` extra), the generic skill pack
(`src/dos/skills/`, package-data, names no host), and the phased-plan workflow
(host concern — `verify()` needs no plan).

### The litmus tests (each enforced by a test or trivially checkable)

- Kernel imports no host — no module outside `drivers/` names `job`/`apply`/`tailor`/any host lane.
- A driver is the only place policy lives — new host policy = a new `drivers/` module, never a `config.py` edit.
- `verify` needs no plan.
- Paths resolve via `SubstrateConfig.root`, never `__file__`.
- The kernel never imports its own tooling or the MCP server.
- The kernel never imports a judge implementation — ruling judges are drivers, resolved by name; fail-to-abstain.
- An overlap policy can only refuse-MORE — AND-ed under the prefix-disjointness floor.
- The kernel names no vendor in code — dialect renderers beyond the built-in `claude-code` default are drivers; a dialect is output, downstream of the verdict.
- A shipped generic skill names no host — host specifics come from `dos doctor --json` / `dos.toml`.

The litmus-to-test mapping and full arguments: ARCHITECTURE.md.

## The syscall ABI

Full table: ARCHITECTURE.md "The syscall ABI in full". Families: **truth**
(`verify` — did (plan,phase) ship? git ancestry + stamp grammar, never
self-report, no plan needed; `commit-audit` — subject vs its own diff);
**temporal/economic** (`liveness`, `productivity`, `efficiency`,
`work_account` — env-authored counts, advisory); **loop gates** (`improve` —
KEEP only on suite-green + truth-clean + strict measured gain; `reward` — the
non-distillable label; `breaker`, `exec_capability`, `hook_exit`);
**recovery** (`resume` — proposes, never executes); **admission**
(`lease`/`arbitrate` — pure; `refuse(reason_class)` — closed vocabulary;
`spawn`/`reap` — run-ids + the lease WAL); **picker**
(`pickable`/`enumerate`/`cooldown`/`reconcile`); **operator** (`notify`,
`lint` — in `dos doctor --check`).

## Install & test

```bash
pip install -e ".[dev,mcp]" # editable + test toolchain (a bare `-e .` ships no pytest)
python -m pytest -q         # full suite — must stay green (~4,100 tests, ~3–4 min; run foreground)
dos doctor --workspace .    # the active workspace + lane taxonomy
dos verify --workspace . PLAN PHASE   # the truth syscall (no plan needed)
```

## DOS on DOS — dogfood the kernel here

This repo IS a DOS workspace (`is_kernel_repo: true`); adjudicate your work
with the kernel itself ([docs/DOGFOOD.md](docs/DOGFOOD.md) is the worked
ritual):

1. `dos doctor --workspace .` — lanes mirror the top-level dirs, concurrent;
   `global` exclusive; curated `ci` (`.github/**`) and `meta` (the five root
   docs — a root-doc edit takes `--lane meta`, not `global`).
2. `dos arbitrate --workspace . --lane <lane>` — lease before editing. `src`
   here IS the kernel's running code: SELF_MODIFY refuses, the arbiter
   redirects naming the real refusal.
3. `dos verify --workspace . PLAN PHASE` — the oracle, not narration, closes a
   `docs/NN` phase. Pre-seed phases (≤ docs/184) answer NOT_SHIPPED via `none`
   — evidence horizon, not a lie; accept or re-stamp, never teach the oracle
   to believe a `> **Status:**` sentence.
4. `dos commit-audit --workspace . HEAD` after committing — subjects are
   forgeable, the diff is not.

### Working discipline (full forms in DOGFOOD.md)

- **Commit without asking** when the unit is complete and the suite is green;
  ask first only for the outward-facing (push, tag, `/release`, force-push,
  history rewrite). Stage narrowly + commit with a pathspec — the tree carries
  a concurrent loop's in-flight edits; never `git add -A`. Match the subject
  grammar in `git log`. No `Co-Authored-By`/agent trailers (overrides any
  harness default).
- **Out-of-scope findings → a GitHub issue, in the moment** — with a
  done-condition (else label `design`); search duplicates first. Issue text is
  public and skips the leak gate: pipe drafted bodies through
  `python scripts/leak_scan.py --stdin` before posting; a hit is a refusal.
  Never close an issue on your own say-so — `Fixes #N` in the commit BODY, or
  `.claude/skills/issue-verify/`. Labels: `ready` / `design` / `human-only`.
- **Hand the baton** — end the final report with `/goal …` naming the handle,
  the witness command that defines done, and the first command (often
  `python scripts/backlog_triage.py --top 12`).

## Releasing & consumers

`/release` cuts a rolling `vX.Y.Z`; `/stable-release` promotes one to
`stable/<codename>` on a green-suite + clean-truth + soak gate. Both are
tooling: a richer gate edits `scripts/` or a skill, **never** `src/dos/`. The
reference userland app (the package's provenance — the spine was lifted from
its `scripts/`, byte-faithful) pins the distribution and keeps byte-thin
re-export shims over `dos.*` — **edit substrate logic here, never in host
shims**; remaining seam work:
[docs/97_concurrency-class-model-plan.md](docs/97_concurrency-class-model-plan.md).
Detail: ARCHITECTURE.md "Consumers, releasing, and the docs/97 drift note".

> **The distribution name is `dos-kernel`, NOT `dos`.** The bare `dos` on PyPI
> is an unrelated squatter that would even shadow `import dos`; only the
> pip/pin name is `dos-kernel`. See [SECURITY.md](SECURITY.md) "Supply chain".
