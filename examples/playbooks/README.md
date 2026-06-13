# DOS playbooks — usage walked end-to-end

> **The kernel is the part that doesn't believe the agents.** These playbooks
> show what that buys you on real-shaped codebases.

This directory is the **task-oriented** companion to the reference docs. Where
[`docs/HACKING.md`](../../docs/HACKING.md) is "how to *extend* DOS" and the
[`README`](../../README.md) is "what the syscalls *are*," these are **"here is a
repo that looks like yours, here is the exact sequence of `dos` commands, here is
what comes back."**

> **Install:** the package is `dos-kernel`, not `dos` (the bare `dos` name on PyPI
> is an unrelated squatter). The import + CLI stay `dos`; only the pip name differs.

Almost every command in every playbook was run against the shipped `dos` CLI and
its output pasted back verbatim — exit codes included. The handful of exceptions
are the few `SHIPPED ... (via grep-subject)` examples that need a commit in the repo's
*own* history: the example workspaces under [`../workspaces/`](../workspaces/) are
`dos.toml` fixtures, not standalone git repos, so those specific commit-SHA lines
are **labeled illustrative** where they appear (see
[playbook 03](03_oss-library-release.md)). Every `via none` negative, every
`arbitrate`/`gate`/`man` verdict, and every exit code reproduces against the
fixtures as shown.

## The four archetypes

Each playbook is anchored to an **anonymized real-world repo shape** — invented
names, realistic layout. Find the one whose directory tree looks most like yours:

| Archetype | Looks like | The DOS feature it exercises | Playbook |
|---|---|---|---|
| **Polyglot web service** | a `api/` + `web/` + `infra/` SaaS monorepo | **concurrent disjoint lanes** — a fleet edits the API and the frontend in parallel without colliding | [`02_polyglot-web-service.md`](02_polyglot-web-service.md) |
| **OSS library + docs** | a published library: `src/` + `docs/` + `tests/`, a strict release convention | **`verify()` + the stamp grammar** — "did this actually ship?" from git history, no plan doc needed | [`03_oss-library-release.md`](03_oss-library-release.md) |
| **Data / ML pipeline** | `ingest/` + `train/` + `serve/`, long jobs, one shared GPU box | **`liveness()`** — is that 40-minute training run *advancing*, or wedged in a retry loop? | [`04_data-ml-pipeline.md`](04_data-ml-pipeline.md) |
| **Infra / platform monorepo** | Terraform + k8s + pipelines, blast-radius is real | **exclusive lanes, the self-modify guard, operator-gated `BLOCKED`** — the refusals that keep a fleet from detonating shared state | [`05_infra-monorepo.md`](05_infra-monorepo.md) |
| **Driver / ring-0 bring-up** | a PCI-driver repo + one emulated rig (QEMU's `edu` device — no physical hardware) | **equipment lanes + the effect-witness join + ring-0 exec capability** — "the interrupt fires" adjudicated from `/proc/interrupts`, never from narration | [`08_driver-bringup-qemu-edu.md`](08_driver-bringup-qemu-edu.md) |

Start with the onboarding quickstart, then jump to your archetype:

0. **[`00_non-coder-verdict-in-15-minutes.md`](00_non-coder-verdict-in-15-minutes.md)** —
   the smallest adoption move: turn on `--output plain` so a non-coder gets an
   honest "Probably yes" / "Not yet" verdict on what an agent claimed it built.
1. **[`01_onboard-a-repo.md`](01_onboard-a-repo.md)** — 10 minutes from `pip install`
   to your first verified ship, on *any* repo. Read this first.
2. Your archetype playbook (table above).
3. **[`06_debug-a-stuck-fleet.md`](06_debug-a-stuck-fleet.md)** — the
   cross-cutting troubleshooting + FAQ: `verify` says `via none`, an agent is
   `SPINNING`, the decisions queue, the common foot-guns.
4. **[`07_verify-subagent-results.md`](07_verify-subagent-results.md)** — running a
   *fan-out*? `dos verify-result` catches the ~32% of subagent returns that are a
   harness-synthesized death folded as a finding (exit 3 = DEAD).
5. **[`08_driver-bringup-qemu-edu.md`](08_driver-bringup-qemu-edu.md)** — agent
   fleets on *ring-0 code*: the rig as an equipment lane, a false "the interrupt
   fires" claim REFUTED from the `/proc/interrupts` delta, and `insmod` declared
   as an arbitrary-exec entry point. Runs entirely against QEMU's `edu` device
   and static fixtures — no physical hardware.

## Three cookbooks (recipes, not walkthroughs)

- **[`cookbook-fleet-frameworks.md`](cookbook-fleet-frameworks.md)** — already
  running a fleet through **LangGraph, CrewAI, AutoGen, or the OpenAI / Claude
  Agents SDKs**? Each framework has a believe-the-agent point (a conditional
  edge, a termination condition, an output guardrail); these recipes route it
  through a kernel verdict instead — one function at one seam, no rewrite. Every
  seam was executed against the real framework (versions + verbatim output in
  the file).
- **[`cookbook-python-api.md`](cookbook-python-api.md)** — drive the kernel from
  *code* instead of the CLI: `oracle.is_shipped(...)`, `arbiter.arbitrate(...)`,
  building a `SubstrateConfig`, the reason/stamp registries. For when you're
  embedding DOS in your own tool rather than shelling out.
- **[`cookbook-ci-integration.md`](cookbook-ci-integration.md)** — wire DOS into
  a pipeline: a GitHub Actions ship-gate, a pre-commit hook, and the MCP-host
  config so an agent can call the referee directly.

## Runnable example workspaces

The playbooks reference workspaces under
[`../workspaces/`](../workspaces/) you can `cd` into and run live — each is a
`dos.toml` plus a tiny file tree modeling that archetype:

| Workspace | Archetype |
|---|---|
| [`acme-store/`](../workspaces/acme-store/) | polyglot web service |
| [`libkv/`](../workspaces/libkv/) | OSS library + docs |
| [`riverflow/`](../workspaces/riverflow/) | data / ML pipeline |
| [`gravel/`](../workspaces/gravel/) | infra / platform monorepo |
| [`edu-rig/`](../workspaces/edu-rig/) | driver / ring-0 bring-up (QEMU `edu`) |

```bash
cd examples/workspaces/acme-store
dos doctor --workspace .          # see this workspace's lanes + stamp grammar
```

> **Note on `examples/dos_ext/` vs here.** [`../dos_ext/`](../dos_ext/) is the
> *extension* skeleton — copy it to **add** a reason or a renderer to the kernel.
> These playbooks are the *usage* layer — how to **run** the kernel you have. The
> two compose: extend with `dos_ext`, operate with these.

## The verbs, by the question they answer

The kernel exposes ~40 verbs; these are the load-bearing ones. **For a
verdict-bearing verb, the exit code *is* the verdict** — branch on it directly.

| Verb | The question it answers | Exit codes |
|---|---|---|
| `dos verify` | did (plan, phase) actually ship? | `0`=yes `1`=no |
| `dos verify-result` | did a subagent's terminal record DIE (a harness 429 / quota), or is it a real result? | `0`=HEALTHY/UNREADABLE `2`=contract `3`=DEAD |
| `dos liveness` | is the run advancing, or just spinning? | advisory verdict (ADVANCING/SPINNING/STALLED) |
| `dos complete` | is the **whole** declared job verifiably done? (`residual = declared − verified`) | `0`=COMPLETE `3`=INCOMPLETE `4`=INDETERMINATE |
| `dos arbitrate` | may a loop start on lane L without a collision? | `0`=acquire `1`=refuse |
| `dos scope-gate` | does this proposed write overrun its lane? | `0`=ALLOW `5`/`6`=REFUSE |
| `dos rewind` | which dead-end transcript turns should be excised? (proposes, never truncates) | advisory verdict |
| `dos gate` | the typed empty-packet verdict | LIVE=`0` DRAIN=`3` STALE-STAMP=`4` BLOCKED=`5` RACE=`6` |

## The one-line cheat sheet

```bash
dos init [DIR]                       # scaffold a dos.toml workspace config
dos doctor --workspace . [--json]    # what lanes / stamp grammar / predicates are active?
dos verify --workspace . PLAN PHASE  # did (plan,phase) actually ship?            exit 0=yes 1=no
dos verify-result --transcript T     # did a subagent's terminal record DIE?      exit 0=healthy 3=DEAD
dos liveness --run-id R --start-sha S  # is the run advancing or just spinning?
dos complete --run-id R              # is the WHOLE declared job verifiably done?  exit 0=complete 3=incomplete
dos arbitrate --lane L --kind cluster --leases '[...]'   # may a loop run on L?   exit 0=acquire 1=refuse
dos gate PACKET                      # typed empty-packet verdict   LIVE=0 DRAIN=3 STALE-STAMP=4 BLOCKED=5 RACE=6
dos man wedge [REASON]               # the self-describing refusal manual
dos decisions [--all]                # the operator-decision queue
dos trace RUN_ID                     # walk one run across spine + ledger + WAL + git
```
