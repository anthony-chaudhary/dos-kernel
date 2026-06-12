# 313 — the `.dos` surface: the repo-resident throughline behind platform agnosticism

> DOS runs under six hook hosts, two guardrail seats, any MCP client, two CI
> vendors, and bare Python — and every one of those adapters is thin. This
> plan names WHY, because the why is a load-bearing design fact we have never
> said out loud: **all shared state is repo-resident files with declared
> schemas.** Policy comes in as `dos.toml`. Every fossil the kernel writes
> lives under one self-gitignored `.dos/` home (docs/74). Evidence is git
> itself, and the public verdict is `verdict.json` (docs/312). A "host" holds
> zero state — it is anything that can invoke `dos` against a root. The
> substrate travels with the WORK, not the worker. That is the throughline
> (operator prompt, 2026-06-12: "think about the .dos file as the throughline
> for all of it") — and it is also the differentiator: the eval platforms and
> observability tools anchor agent state in *their* cloud; DOS anchors it in
> *your repo*, which is the only neutral ground every agent platform already
> shares.

*Status: P1 SHIPPED 2026-06-12 (the public page + indexes + the stale-pointer
fix — verify on master). P2–P4 open.*

## 0. What is already true (and where it is pinned)

| Fact | Where it lives | Pin |
|---|---|---|
| Every kernel emission resolves under `.dos/` in the generic layout (WAL, verdict journal, leases, runs, verdict envelopes, replan, soaks, picker audits) | `PathLayout.for_dos_dir` (docs/74) | `tests/test_state_home.py::test_dos_emissions_live_under_dot_dos` |
| `.dos/` is self-gitignoring, re-derivable, safe to delete | `home.py` `_DOT_DOS_GITIGNORE` | shipped behavior; bytes unpinned |
| The identity card is schema-versioned (`project.json`, `schema: 1`) | `home.py SCHEMA` | `tests/test_state_home.py` |
| What DOS *reads* stays repo-relative, outside `.dos/` (`dos.toml`, optional `dos.state.yaml`, git) | `for_dos_dir` field comments | `test_generic_registry_is_not_job_shaped` |
| The public verdict surface is schema-versioned (`verdict.json` v1, the badge) | docs/312 P1 | its suite |
| The hook decision is decided once, rendered per host dialect | docs/217 seam | `tests/test_hook_dialect.py`, `test_vendor_agnostic_kernel.py` |

So the throughline is TRUE and mostly pinned — what's missing is (a) a public
page that *names* it, (b) a behavioral pin that two different host surfaces
against one repo share one referee and one memory, and (c) discovery from a
subdirectory, so any agent landing anywhere in an adopted tree finds the
substrate the way git finds `.git`.

## Phase 1 — the public page, indexed, and the stale pointer fixed

`docs/DOT_DOS.md` — "the `.dos` surface": the arriving-question page (a user
who just saw `.dos/` appear in their repo asks "what is this, is it safe to
delete, why is it not in my history?") that grows into the contract statement:

1. The three surfaces — policy IN (`dos.toml`), state THROUGH (`.dos/`,
   every file named with one line each), verdicts OUT (git ancestry the
   oracle reads; `verdict.json` + badge the repo publishes).
2. The corollary, stated plainly: hosts are stateless adapters; that is why a
   new platform costs a driver, never a redesign — and why two platforms
   working the same repo get the same referee with the same memory.
3. The honest boundaries: `.dos/` is per-clone (not synced by git — a fresh
   clone starts with empty fossils; only git evidence travels), `~/.dos` is
   the per-machine home tier, and deleting `.dos/` loses observation history
   even though indices rebuild (`dos reindex`).

Wired in the same phase: a `llms.txt` row (non-Optional → inlined into
`llms-full.txt`), a Documentation bullet in `docs/readme/90_extending-and-docs.md`,
both generated files rebuilt. And the `.dos/.gitignore` template in `home.py`
stops pointing at the stale `dos/CLAUDE.md` and points at this page's GitHub
URL — that file is read by people in FOREIGN repos, so the pointer must be a
URL that resolves from anywhere.

**Done:** the page exists and is indexed; `python scripts/build_llms_full.py
--check` and the README assembly test pass; a freshly created `.dos/.gitignore`
names the page.

## Phase 2 — pin cross-host continuity (one repo, one referee, two hosts)

The agnosticism claim that matters operationally: state written via host A's
surface ADJUDICATES host B. A new test (`tests/test_cross_host_continuity.py`)
drives two different shipped surfaces against one temp workspace and pins:

1. a lane lease journaled via surface A (the hook path under
   `--dialect claude-code`) refuses the colliding request made via surface B
   (the CLI verb / a second dialect) — same WAL, one arbiter;
2. the observation log accumulates rows from both surfaces in one file;
3. the *decision* bytes are dialect-independent (already pinned by
   docs/217's suite — cite it, don't re-pin; this test pins the shared-STATE
   half those tests don't).

**Done:** the test exists and passes; it builds its own throwaway repo (the
`test_verify_no_plan.py` fixture discipline) and never touches this repo's
live `.dos/`.

## Phase 3 — upward discovery (the `.git`-walk analogue) — DESIGN, T1-gated

`resolve_workspace_root` today is explicit arg › `DISPATCH_WORKSPACE` › cwd.
An agent invoked in `src/foo/` of an adopted repo silently gets a cwd-rooted
config that misses the root `dos.toml` and `.dos/` — the one place the
"substrate travels with the repo" story breaks. Proposed: when neither
explicit arg nor env is set AND cwd carries no `dos.toml`/`.dos/`, walk
parents to the nearest directory that does (stopping at the filesystem root;
a git-toplevel cap is unnecessary — the marker IS the boundary), falling back
to cwd exactly as today when no ancestor has one. Safe direction: the walk
can only fire where today's behavior was a silent misconfiguration (a rootless
cwd inside a configured tree).

`config.py` is a T1 module (the SELF_MODIFY guard refuses live-loop edits),
so this phase ships via the operator playbook or the docs/296 arm-file once
that lands — it is designed here, deliberately not executed by the loop that
wrote this plan.

**Done:** from a subdir of a `dos.toml`-carrying repo, `dos doctor` reports
the repo root, not the subdir; explicit arg and env still win; a no-marker
tree behaves byte-identically to today. Pinned in `tests/test_workspace_config.py`.

## Phase 4 — `dos doctor` shows the surface

One `doctor` section reporting the throughline as facts: the project card
(id, schema, created), which fossils exist and their row counts/sizes
(lane journal, verdict journal, observations, streams), and the declared-policy
provenance (`dos.toml` present or generic default). The operator's "what does
my `.dos` know" view — and the natural place a support answer points.

**Done:** `dos doctor` (text + `--json`) carries the section; pinned in the
doctor suite.
