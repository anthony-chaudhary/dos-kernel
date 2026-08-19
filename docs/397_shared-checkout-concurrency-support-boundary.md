# Shared-checkout concurrency — cooperative file intents, isolation for durability

> **Issue:** #205  
> **Decision:** DOS should offer a short-lived, per-path working-tree write intent
> at enforcing host seams. It is distinct from lane leases and cannot make an
> unmanaged shared checkout safe. Dedicated git worktrees remain the required
> boundary for long-lived, high-contention, or unhooked work.

## 1. The three resources hidden behind “the working tree”

The incidents grouped under #205 are related but not identical:

| Resource | Failure | Existing/selected control |
|---|---|---|
| file bytes | two editors read and rewrite one path; stale Edit loses or overwrites | atomic block helper (#209) plus the per-path intent selected here |
| git index | path-scoped `git add` captures a sibling's already-present hunks | session snapshot/stage comparison and serialized safe commit tooling |
| HEAD/sequencer | checkout/merge/rebase changes the meaning/state of every path | worktree-global refusal and routed integration (#222 / #252) |

A lane lease is deliberately coarser and semantic: “this run may work in
`src/**`.” It does not prove which process last read `src/dos/cli.py`, which
bytes that process intends to replace, or whose hunks are already in the shared
index. Stretching the lane WAL to answer those questions would mix scheduling
with mutable-editor state and still miss unmanaged writers.

## 2. Decision

Support same-checkout concurrency only as a **cooperative, hook-enforced fast
path**:

- retain lane admission for broad tree ownership;
- add a separate short-TTL `WRITE_INTENT` keyed by canonical worktree identity
  and canonical repo-relative path;
- acquire/refresh it from PreToolUse immediately before a known mutating editor
  call and release/expire it after PostToolUse;
- refuse a second incompatible write intent with typed `PATH_WRITE_BUSY` before
  either edit executes;
- bind the intent to run/session/tool-call identity so a worker cannot release a
  sibling's intent;
- keep index and HEAD mutations on their separate managed paths.

This is **not** a durable lock held for the whole thinking interval. Agents can
read concurrently. The intent covers the effect window between host admission
and the completed write, where a hook can actually enforce. A long TTL would
turn crashed editors into wedges; a zero-TTL advisory would not close the race.

The intent is also not inferred from “the agent says it will edit X.” The host
tool envelope supplies the path, the hook canonicalizes it against workspace
root, and PostToolUse/read-back observes the result.

## 3. Why not declare same-tree concurrency wholly out of scope

DOS already advertises and operates shared-tree fleets. Declaring every shared
checkout unsupported would contradict the existing path arbiter, pretool hooks,
safe-stage discipline, and dogfooding model. Many short, disjoint edits are
cheap and safe enough when the host exposes a before/after tool seam.

But a per-path intent cannot make the entire checkout transactional. Editors,
shell scripts, antivirus/indexers, and raw git commands can bypass hooks; a
process may crash after writing but before PostToolUse; and the git index remains
one shared file. Therefore the support statement must be narrower than “same
working tree is serialized.”

## 4. Support boundary

### Supported fast path

Same checkout is supported when all are true:

1. every mutating agent runs through an enforcing DOS host hook;
2. the adapter can extract concrete target paths before execution;
3. each run holds a compatible lane lease;
4. per-path write intent is admitted for the effect window;
5. commits use explicit paths plus the stage-snapshot guard/safe commit path;
6. no branch/sequencer operation runs in shared main without exclusive global
   ownership.

### Required isolation

Use a dedicated git worktree when any is true:

- work lasts beyond a short edit/commit interval;
- files are hot or repeatedly rewritten/generated;
- the tool envelope has unknown or dynamic output paths;
- a host has no enforcing pre/post hook;
- branch switching, merging, rebasing, or conflict resolution is needed;
- meaningful uncommitted/untracked state must survive sibling activity;
- a remote machine participates without a shared lease backend.

This cross-links [docs/327](327_the-worktree-lifecycle-where-the-kernel-adds-value.md)
and [docs/396](396_shared-head-mutations-route-integration-to-a-worktree.md):
isolation is not failure recovery after a collision; it is the supported answer
when the cooperative seam cannot bound the effect.

## 5. WRITE_INTENT evidence and verdict

The I/O boundary gathers:

```text
workspace/worktree id
canonical target paths
operation identity + mutation/read classification
caller run/session/tool-call identity
live unexpired intents
lane admission receipt
clock
```

A pure classifier returns:

```text
ALLOW_PATH_WRITE
PATH_WRITE_BUSY
PATH_OUTSIDE_LANE
PATH_UNKNOWN
WORKTREE_IDENTITY_UNKNOWN
```

Policy laws:

- canonicalize case/separators/symlinks according to the workspace filesystem
  before classification;
- multiple readers need no write intent;
- one writer conflicts with another writer on the same canonical path;
- directory/rename/delete operations expand to the conservative affected path
  set or refuse `PATH_UNKNOWN`;
- a plugin/scorer may refuse more but never admit a deterministic conflict;
- expiry uses injected time; PID liveness may reap more precisely but never
  extends a dead claim based on narration;
- intents are scoped to one filesystem, matching
  [docs/366](366_single-filesystem-lease-boundary.md).

The WAL record is distinct from `ACQUIRE` lane records, for example:

```json
{
  "op": "WRITE_INTENT",
  "worktree": "<identity>",
  "path": "src/dos/cli.py",
  "run_id": "...",
  "tool_call_id": "...",
  "expires_ms": 123456789
}
```

The exact storage can reuse the append-only journal machinery, but consumers
must not treat a write intent as lane ownership or vice versa.

## 6. Relationship to the shipped mitigations

- **#209 / `scripts/atomic_block_edit.py`:** collapses one cooperating process's
  read/prepare/publish window and prevents torn writes. It does not serialize a
  non-cooperating sibling; use it after write-intent admission.
- **Safe-stage snapshot:** proves staged hunks were authored after the session
  snapshot. It catches index contamination before commit; it is not an editor
  lock.
- **#222 / docs/396:** classifies HEAD/index/sequencer seizures as worktree-global
  and routes integration away from main.
- **Lane lease:** remains the broad scheduling/overlap contract. A path intent
  cannot grant a path outside the caller's admitted lane.

The controls are conjunctive. Passing one never overrides another's refusal.

## 7. Failure and recovery

- If PreToolUse cannot identify paths, fail closed for known mutating tools or
  route the call to an isolated worktree.
- If PostToolUse is missing, the short TTL expires; a heartbeat may not renew a
  path intent because thinking is not an active filesystem effect.
- If a process dies, PID/session evidence may reap its intent early.
- If bytes changed despite a refusal, report an unmanaged-writer observation;
  do not pretend the intent prevented an out-of-band effect.
- Never restore/checkout/stash a contended file automatically. The current bytes
  may be the sibling's only copy.

Ready implementation follow-on: **[#253 — per-path write intents for shared checkouts](https://github.com/anthony-chaudhary/dos-kernel/issues/253)**.

## 8. Acceptance witness

The per-path feature is complete when a two-worker fixture proves:

1. both workers hold disjoint/compatible broad lane state but target one path;
2. worker A's admitted write intent causes worker B to receive
   `PATH_WRITE_BUSY` before its editor runs;
3. distinct canonical paths are admitted concurrently;
4. case/symlink aliases collide on platforms where they identify one file;
5. read-only calls do not create low-signal intents;
6. missing PostToolUse expires safely and PID-dead evidence reaps early;
7. a target outside the lane refuses even when no path intent exists;
8. unknown dynamic target sets refuse or route to isolation;
9. an unmanaged direct write is detected/read back and honestly reported as
   outside enforcement reach.

## 9. Final answer to #205

DOS **does** offer a working-tree coordination primitive distinct from the lane
lease, but only as a short-lived per-path write intent at a cooperative host
seam. It does **not** claim that one shared checkout becomes generally safe.
Dedicated worktrees are mandatory whenever the effect window or target set
cannot be bounded and enforced.

That split matches the kernel thesis: serialize effects where there is an
admission seam; expose the boundary where there is not.
