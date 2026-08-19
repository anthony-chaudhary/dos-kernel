# Shared-HEAD mutations — refuse in main, route integration to a worktree

> **Issue:** #222  
> **Status:** design decision. Raw checkout/merge/rebase/reset operations are
> refused in the shared main worktree while any lane is live; integration is
> routed to a dedicated DOS worktree.

## 1. The resource the lane arbiter does not model

A lane lease protects paths. A git worktree has two additional singleton
resources:

- `HEAD`, which names the checked-out branch/commit;
- the index and sequencer state (`index`, `MERGE_HEAD`, rebase/cherry-pick state).

Changing either is a **worktree-global mutation**. Two workers can own disjoint
file lanes and still collide when one runs `git switch`, `git checkout`, `git
merge`, `git rebase`, or a reset that rewrites the index/worktree. Prefix overlap
cannot describe this: the operation changes the interpretation and staging state
of every path at once.

The existing `global` lane has the correct admission semantics — it runs only
when no other lane is live — but raw git does not ask for that lane. The missing
piece is an observation/enforcement seam plus a safe routed alternative.

## 2. Decision

Use two complementary mechanisms:

1. **Refuse shared-main seizures.** At a shell/tool-call boundary, classify git
   argv before execution. If it can mutate HEAD/index/sequencer state and the
   current worktree is the configured shared main worktree, request the generic
   exclusive `global` admission. Any live lane yields typed
   `WORKTREE_GLOBAL_BUSY`; unknown/ambiguous git command shapes fail closed as
   `WORKTREE_MUTATION_UNKNOWN` when they contain a known mutating verb.
2. **Route integration.** Provide a `dos worktree-op` (name may change at build
   time) that creates or reuses `.dos/worktrees/<operation-id>` from an explicit
   base SHA, runs the requested checkout/merge operation there, and returns the
   worktree path plus resulting SHA/conflict state. It never changes HEAD or the
   index of the shared main worktree. Promotion back to trunk remains a separate
   merge-gate/commit operation.

The default user advice on refusal is therefore actionable:

```text
WORKTREE_GLOBAL_BUSY: shared worktree HEAD/index is in use by live lanes.
Run the integration in `dos worktree-op ...` or wait until `dos lease-lane live`
is empty and acquire `--lane global`.
```

A raw operation may proceed in main only when the exclusive global admission is
actually acquired and the tree is clean. This supports intentional maintenance
without teaching an unsafe bypass.

## 3. Pure classification seam

Host adapters gather facts; the kernel leaf classifies data:

```python
classify(
    evidence={
        "argv": ["git", "merge", "topic"],
        "cwd_worktree_id": "...",
        "shared_main_worktree_id": "...",
        "status": {"dirty": False, "sequencer": None},
        "live_leases": [...],
        "global_lease": None,
    },
    policy={"mutating_verbs": [...], "route_root": ".dos/worktrees"},
)
```

Typed outcomes:

| Outcome | Meaning |
|---|---|
| `ALLOW_NON_MUTATING` | `git status`, `log`, `diff`, `show`, etc.; no HEAD/index mutation |
| `ALLOW_ISOLATED_WORKTREE` | mutating argv, but cwd is a dedicated non-main worktree |
| `ALLOW_GLOBAL` | cwd is shared main, tree clean, no sequencer, and caller owns the exclusive global lease |
| `WORKTREE_GLOBAL_BUSY` | shared main plus any live non-caller lane / no exclusive ownership |
| `WORKTREE_DIRTY` | operation could overwrite or reinterpret local edits/index |
| `SEQUENCER_ACTIVE` | merge/rebase/cherry-pick already in flight; only observed owner may continue/abort |
| `WORKTREE_MUTATION_UNKNOWN` | parser sees a mutating family but cannot prove the exact effect |

The leaf performs no git or filesystem I/O. Resolving the common git dir,
worktree identity, status, and live journal remains at CLI/hook boundaries. This
preserves the kernel's host/vendor independence.

## 4. Command taxonomy

V1 must classify argv tokens, not substring-match a shell string. The parser
must account for global git options (`git -C X`, `--git-dir`, `--work-tree`) and
shell composition. If the host provides only an opaque shell program, its driver
splits commands conservatively and refuses an ambiguous segment containing a
mutating verb.

Global mutation families include at least:

- branch/HEAD: `checkout`, `switch`;
- history/index/worktree: `merge`, `rebase`, `cherry-pick`, `revert`, `am`,
  `reset` (except a proven read-only form, of which V1 assumes none);
- destructive restore/index forms: `restore`, `read-tree`;
- worktree administration targeting the shared worktree: `worktree remove`,
  `move`, `lock`, `unlock`, `prune` (route through lifecycle tooling).

`commit` mutates the current branch and index but does not switch HEAD. Existing
safe path-scoped commit tooling already serializes it; V1 records it as a
separate managed mutation rather than routing every commit through this gate.
`add`/`rm`/`mv` mutate the shared index and remain covered by commit/index
serialization follow-on work; #222's first hard floor targets branch/sequencer
seizures.

## 5. Routed worktree lifecycle

The route command builds on [docs/327](327_the-worktree-lifecycle-where-the-kernel-adds-value.md):

1. require explicit immutable base SHA and operation id;
2. create `.dos/worktrees/<id>` with `git worktree add --detach`;
3. persist a lifecycle record containing main worktree identity, base SHA,
   operation argv, PID/run identity, path, and timestamps;
4. execute only inside that path;
5. report `CLEAN`, `CONFLICT`, or `REFUSED` with resulting SHA and porcelain
   status facts;
6. run `dos merge-gate` on the produced commit before promotion;
7. reap only from lifecycle evidence (clean + landed/stale policy), never by
   guessing from directory age alone.

The routed worker may acquire ordinary path lanes for files it edits, but its
HEAD/index is physically distinct. A conflict cannot freeze the shared tree.

## 6. Enforcement reach and honest boundary

DOS cannot intercept a human or process that invokes `/usr/bin/git` outside a
wired host hook. Enforcement therefore has tiers:

- **Managed host hook:** hard refuse before the tool call.
- **DOS wrapper/skill:** always use the routed operation and witness its path.
- **Unmanaged shell:** advisory policy only; `dos doctor` reports that the
  worktree-mutation hook is not wired.

This is the same honest boundary as other runtime hooks. The command should not
claim universal OS mediation.

Leases remain filesystem-local as documented in
[docs/366](366_single-filesystem-lease-boundary.md). A remote machine sharing the
repository by network sync needs a shared lease backend before this can be a
cross-machine guarantee.

## 7. Recovery law

When sequencer state already exists in shared main, DOS does not auto-abort,
reset, checkout, or clean. Those actions can destroy the current owner's only
copy of conflict resolution. `SEQUENCER_ACTIVE` reports:

- operation type and start/mtime facts;
- observed owner/run identity when available;
- whether that owner is live;
- the operator decision required if ownership is dead.

Only the observed owner may continue/abort automatically. A dead or unknown
owner becomes a decision, not an eager cleanup.

## 8. Acceptance witness

A concrete implementation closes #222 when these checks pass:

1. Worker A holds a normal lane; worker B's `git switch topic` in shared main is
   refused `WORKTREE_GLOBAL_BUSY` before git runs.
2. The same argv in a dedicated DOS worktree is admitted and shared-main
   `HEAD`, index hash, and status remain byte-for-byte unchanged.
3. With no lanes live, a caller holding the exclusive global lease can perform
   intentional main-tree maintenance.
4. Dirty main refuses before execution.
5. Existing `MERGE_HEAD`/rebase state yields `SEQUENCER_ACTIVE`; no automatic
   abort/reset command runs.
6. `git status`, `log`, `show`, and `diff` stay read-only/admitted.
7. Ambiguous shell composition containing a mutating git family refuses rather
   than silently allowing.
8. Linux and Windows worktree identity/path fixtures agree.
9. Hook-off doctor output states the advisory-only boundary.

## 9. Work split

Ready follow-on **[#252 — refuse shared-HEAD seizures and route integration](https://github.com/anthony-chaudhary/dos-kernel/issues/252)** should land in slices:

- pure argv/evidence classifier and typed reasons;
- CLI fact gathering and routed worktree lifecycle;
- host pretool adapters and doctor reachability;
- end-to-end two-worker fixture proving shared-main HEAD/index invariance.

The classifier can ship before every host adapter, but #222 is not fully closed
until at least one enforcing host seam and the routed alternative are exercised
end to end.
