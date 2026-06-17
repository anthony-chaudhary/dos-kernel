# docs/379 — The VCS-read seam: git stops being *assumed*

> **Status:** SHIPPED (slices 1–7). The `dos.vcs` seam + `GitBackend`/`NullVcs`, the
> `dos.toml [vcs]` selector, every kernel evidence-gatherer rewired, and the
> `test_vcs_layering` litmus.
>
> *(Historical note: the implementation commits tag this work `docs/360`; that number
> was taken by an unrelated doc by the time this design doc was filed, so the canonical
> number is 379. The seam name in code is `dos.vcs`.)*

## The problem

DOS adjudicates ground truth from **non-forgeable evidence**, and that evidence was
git: commit ancestry, the "N commits since start" forward delta, the ship-stamp
subjects `verify` greps, the files a commit touched, the working-tree dirty set. Every
one of those reads was a `subprocess.run(["git", …])` hardcoded at a caller boundary.

That shape was already disciplined — I/O at the boundary, a pure
`classify(evidence, policy)` core — but it **fused the kernel to one VCS**. A workspace
on jj, Sapling, Mercurial, or no VCS at all could not supply evidence, and the graceful
no-git degrade was an *accident of git's exit codes* rather than a first-class, testable
thing.

## The wedge

Introduce a **`VcsBackend` seam** — a pluggable evidence-gatherer — mirroring the
`dos.judges` Judge seam exactly. Git stops being *assumed* and becomes the **default
backend** (`GitBackend`), with `NullVcs` the honest-empty fallback and an open set of
alternatives registerable under a `dos.vcs` entry-point group.

The key architectural read: **git is NOT a vendor the bulkhead forbids.** The litmus
that keeps `anthropic`/`openai`/network out of the kernel does *not* forbid git — git is
the kernel's *ground-truth substrate*, named throughout CLAUDE.md. So the default
`GitBackend` ships **in the kernel** beside the protocol, exactly as `AbstainJudge`
ships in `judges.py` while *ruling* judges live in `drivers/`. Alternative backends
(Mercurial, Sapling, a remote-API reader) are the open set and live in `drivers/vcs_*`,
resolved by name, never imported by a kernel module.

A second clean inheritance: **the ship-stamp grammar was already decoupled.**
`dos.stamp.StampConvention` owns 100% of the commit-subject parsing as pure, git-free
data. The seam abstracts the *read* (`log_subjects`/`log_lines` hand back opaque
subject strings); the *parse* (`convention.recognizes_direct_ship`) is untouched. The
backend MUST NOT parse a subject against the grammar — that would re-fuse the two.

## The protocol (`src/dos/vcs.py`)

`VcsBackend` is a `@runtime_checkable` Protocol. The **core** reads (every backend
answers, `NullVcs` with empties):

| Method | Returns |
|---|---|
| `commits_since(start, *, limit)` | `list[Commit]` |
| `commits_in_range(spec, *, limit, full_sha)` | `list[Commit]` (the general form) |
| `recent_commits(n)` | `list[Commit]` |
| `log_subjects(*, limit, paths, bodies)` | `list[Commit]` |
| `log_records(*, limit, paths, with_files, with_body)` | `list[Commit]` (typed, files+body) |
| `files_in_commit(sha)` | `list[str] \| None` |
| `diff_names(base, head)` | `list[str] \| None` |
| `is_ancestor(sha, of)` | `bool \| None` (three-valued) |
| `head_sha(*, short)` | `str \| None` |
| `commit_meta(ref)` | `Commit \| None` |
| `working_changes()` | `WorkingTree \| None` |

**Optional capabilities** (base default returns `None`; a backend overrides only if it
can — a non-git backend abstains and the caller keeps its existing fallback):
`read_blob(sha, path)`, `commit_diffstat(sha)`, `log_lines(args)` (the grep rung's raw
passthrough), `history_search(**kwargs)` (git's pickaxe).

Value types: `Commit {sha, subject, body, files}`, `FileDelta {added, removed, path}`,
`WorkingTree {head, modified, untracked}`.

### The contract (what keeps a swappable evidence source honest)

1. **Thin & policy-free.** A backend returns raw facts and never interprets them — no
   grammar parsing, no "trust this anchor" judgement.
2. **Fail-to-EMPTY.** A read that cannot answer returns the empty shape
   (`[]`/`None`/`False`-on-`is_ancestor`→`None`), never raises out of the backend. The
   **caller keeps its own failsafe** on top: `git_delta` reads `[]` as "no delta",
   `resume_evidence` reads a `None` ancestry as its fail-closed `False`, `commit_audit`
   maps `None` to an unreadable commit. The backend never imposes one interpretation.
   This is the predicate-side direction — the INVERSE of `judges.py`'s fail-to-ABSTAIN.
3. **Three-valued where the truth is.** `is_ancestor` returns `bool | None`: `None`
   ("unresolvable") is distinct from a definite `False`. Collapsing `None`→`False` would
   regress `memory_recall`'s UNKNOWN abstention — the load-bearing reason it is
   three-valued.

### Resolution (mirrors `judges.py` byte-for-byte)

Built-ins (`git`, `null`) resolve FIRST and are unshadowable; an unknown name fails LOUD
with the known list; `active_vcs(root=…, cfg=…)` is the call-boundary resolver and the
only place entry-point discovery (I/O) happens — never inside a verdict.

## The config selector

`SubstrateConfig.vcs_backend: str = "git"` (default = byte-identical to before), loaded
from `dos.toml [vcs] backend`. The name is validated to RESOLVE at config-load
(fail-loud on an unknown backend); a malformed table is warned + base-kept like every
sibling. `cfg.vcs()` is the convenience resolver. `ENV_VCS_BACKEND` (sibling of
`ENV_STAMP_CONVENTION`) carries the backend name across the one process boundary that
needs it: `oracle` → `python -m dos.phase_shipped --batch`.

## What was rewired

- **Already-pure (no change — they prove the discipline):** `resume`, `liveness`,
  `scope`, `churn`, `arg_provenance`, `home`, `commit_audit.classify`.
- **Boundary readers (rewired to the seam):** `git_delta`, `phase_shipped._git_log`,
  `oracle._git_touched_files`, `commit_audit` (`read_commit` + `audit_range`),
  `resume_evidence` (`_is_ancestor`, `_touched_files`), `env_print._kernel_sha`,
  `health._git_log_subjects`, `preflight` (drift + `dirty_tree_state`),
  `verdict_cli._git_diff_names`, `drivers/memory_recall` (the pickaxe archaeology).
- **The subprocess hand-off:** `oracle._grep_batch_subprocess` carries `ENV_VCS_BACKEND`;
  `phase_shipped._bootstrap_active_config` reads it back. Proven end-to-end: with
  `[vcs] backend="null"` + the forced subprocess path the grep rung sees no history
  (`via none`); with git the same commit resolves SHIPPED `via grep-subject`.

## The fail-safe story

`NullVcs` is the honest-empty backend — every read returns `[]`/`None`/`False`. A
`dos.toml` with `[vcs] backend = "null"`, or a workspace where `GitBackend` finds no git
binary, yields exactly the evidence `git_delta` yielded in a non-git dir: liveness reads
"0 commits" (the honest floor), `verify` resolves `via none` (the evidence horizon, not
a lie). The seam does not *invent* graceful no-git degradation — it **names and
centralizes** the degradation the kernel already performed, and makes "this workspace
has no VCS" a first-class, testable backend rather than a returncode accident — the same
move `AbstainJudge` makes for "no adjudicator wired".

## The litmus (`tests/test_vcs_layering.py`)

AST-walk (the `test_home_layering` idiom): NO `src/dos/*.py` outside `vcs.py` /
`drivers/` / `cli.py` contains a `subprocess.run(["git", …])`. Plus: `vcs.py` IS the one
git home, `vcs.py` imports only `dos.config`/`dos`, no kernel module imports a driver
backend. `cli.py` is exempt as the layer-3 boundary shell where the architecture
sanctions direct I/O (the `dos demo` walkthrough echoes the literal `git` commands it
runs).

## Writing an alternative backend

Implement the `VcsBackend` protocol (the seven core methods; optional capabilities only
if you can), register it under `[project.entry-points."dos.vcs"]` as
`name = "pkg.module:Backend"`, and select it with `dos.toml [vcs] backend = "name"`.
A backend that cannot serve a capability returns `None`/`[]` and the kernel degrades
exactly as it does for a no-git workspace — no kernel edit, no fork.
