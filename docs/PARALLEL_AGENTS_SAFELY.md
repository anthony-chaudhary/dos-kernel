# Running parallel AI agents safely

You are about to run several AI agents at once — a fleet overnight, a fan-out of
subagents, a few loops against one repo. The architecture-review question fires
*before* you pay for an incident: **what goes wrong when agents run in parallel,
and what covers it?**

This page walks the **four places concurrent agents actually contend**. For each:
what breaks, whether worktree isolation already covers it, the one runnable
command if it doesn't, the output shape to expect, and the honest boundary.

> **The boundary, stated once up front.** Every command below is an *advisory
> decision point* — it tells the truth about contention; it does not physically
> hold a lock on the world. And it checks **presence, not correctness**: it
> confirms a thing *shipped* / a lease *is free* / a result *is real-model
> authored* — never that the code is *right*. Run your tests for correctness;
> run these to stop agents from invisibly clobbering each other.

---

## The one-line handshake (when *you* are the agent)

The four surfaces below are written for an operator dispatching a fleet from a
shell. But the most common case is smaller and closer: **you are an agent**, and
you are about to spawn sub-agents, kick off a second `/loop`, or fan out a
`parallel()`/`pipeline()` stage against this one repo. The universal move —
before you fan out, not after a collision — is a single call:

> *"another agent is also working here; deconflict with DOS"* → `arbitrate(my
> file-tree, the leases already held)`.

It returns `acquire` (your tree is disjoint from every live lease — go) or
`refuse` (it overlaps — and here are the `free_clusters` you could take
instead). Deconfliction is a **router, not a queue**: a colliding worker is sent
somewhere disjoint to work *now*. The verdict is the **same closed shape**
whether the "other agent" is your own sibling sub-agent in this very session,
another loop, or a different runtime entirely — that is what makes it a shared
*protocol over file-tree disjointness* rather than a per-host feature. Call it
deliberately from inside a session via the `dos_arbitrate` MCP tool (or
`dos arbitrate` on the CLI); the host's PreToolUse hook also enforces it
automatically at write time. The runnable, suite-pinned version is
[`examples/fleet_frameworks/in_session_deconflict.py`](../examples/fleet_frameworks/in_session_deconflict.py)
(Recipe 8).

---

## Why this page exists

Concurrency is cheap to ignore while tokens are cheap — "if two agents collide,
just re-run." It stops being cheap at the scale-up moment, which is usually the
same moment tokens get expensive: more agents, longer horizons, shared state, and
a re-run now costs real money. The four surfaces below are where the
**re-derivation tax** (re-establishing trust in work a sibling agent did) turns
into an actual incident. See [docs/324](324_the-token-cost-curve-and-the-re-derivation-tax.md)
for the economics; this page is the runnable checklist.

---

## 1. The working tree — two agents writing the same file *now*

**What breaks:** agent A and agent B both edit `src/auth.py` in the same checkout;
one silently overwrites the other, or the file ends up half-A half-B.

**This one is already covered — say so and move on.** Give each agent its own
**git worktree** (or sandbox / container). One isolated checkout per agent means
there is no shared file to clobber. Most harnesses that run parallel agents do
this for you; if yours does not, a worktree per agent is the fix.

**Boundary:** worktrees solve the *same-file-now* problem completely. They do
**not** solve the next three — you cannot worktree a merge target, a database
row, or a subagent's self-report. That is the rest of this page.

---

## 2. The merge target — two branches each fine alone, jointly broken

**What breaks:** agent A's branch is green. Agent B's branch is green. Merged,
they are inconsistent — A renamed a function B still calls, both edited the same
config from different assumptions. Worktrees made this *more* likely, not less:
isolation means neither agent ever saw the other's change.

**The command — declare disjoint trees, and verify each branch on its own merits:**

```bash
# Before dispatch: will these two agents even touch disjoint file trees?
dos arbitrate --workspace . --lane src
dos arbitrate --workspace . --lane docs
```

Two agents on **disjoint declared lanes** (here `src/**` and `docs/**`) are safe
to run concurrently; two that want the **same** lane are not, and the second gets
a refusal instead of a silent collision.

**Expected output shape** (an admitted lane):

```json
{"outcome": "acquire", "lane": "src", "lane_kind": "cluster",
 "tree": ["src/**"], "reason": "cluster lane 'src' free — admitted."}
```

A contended request returns `"outcome": "refuse"` with a `reason` and the
`free_clusters` you *could* take instead — a typed "no", not a crash.

Then, per branch, confirm the work actually shipped from **git ancestry**, not
the agent's say-so:

```bash
dos verify --workspace . AUTH AUTH2     # did (plan AUTH, phase AUTH2) ship?
```

```json
{"plan": "AUTH", "phase": "AUTH2", "shipped": true, "source": "grep", "sha": "…"}
```

`source` names *which authority* answered (`registry` / `grep` / `none`) — a thin
answer can never masquerade as a strong one.

**Boundary:** `arbitrate` is a pure admission decision over *declared* trees — if
two lanes' globs actually overlap, the kernel AND-s them under a disjointness
floor and refuses the overlap; it cannot know about contention you never declared.

---

## 3. The external blast radius — the same DB / API / queue / deploy

**What breaks:** you **cannot worktree a database row.** Two agents both write the
same table, both hit the same rate-limited API, both kick the same deploy. File
isolation does nothing here — the shared state is *outside* the repo.

**The command — take an effect lease so only one agent holds the shared resource:**

```bash
dos lease acquire --workspace .      # the cross-process mutex over a shared effect
# … the agent does its externally-visible work …
dos lease release --workspace .
dos lease status  --workspace .      # who holds it right now?
```

The lease is a **cross-process** lock with write-back, so a crashed agent cannot
leave a phantom hold — the next agent reclaims a dead lease instead of waiting
forever. For *named* shared resources, `dos arbitrate` over an effect lane gives
you the same disjointness check as §2, applied to "the prod database" or "the
deploy pipeline" instead of a file tree.

**Boundary:** this is **advisory** — it coordinates agents that all agree to ask
first. It is not a database lock or an API quota; it stops *cooperating* agents
from colliding, and it is only as good as every agent routing its effect through
the lease. Wire it at the one place each effect is issued.

---

## 4. The fold — the lead agent believing N subagents' "done"

**What breaks:** the orchestrator fans out N subagents, each returns `"✅ done"`,
and the lead **folds those strings in as fact**. This is the single unverified
boundary in every fan-out: the subagent's self-authored return becomes
authoritative input with no ground-truth check. A subagent that crashed, hit a
rate limit, or hallucinated success looks identical to one that succeeded.

**The command — witness the result instead of believing it:**

```bash
# Did a subagent's terminal record come from a real model turn,
# or is it a synthetic/forged "done"?
dos verify-result --workspace . --transcript path/to/subagent.jsonl

# For a CHECKABLE effect (a shipped phase), verify from git, not the string:
dos verify --workspace . PLAN PHASE
```

`verify-result` reads a **different byte-author** than the worker — the harness's
own transcript record — so an agent's `"done"` text can never, by itself, move it
to belief. For effects that land in git, `dos verify` reads ancestry. Fold **only
the confirmed** results.

**Expected output shape:**

```json
{"plan": "RS", "phase": "RS1", "shipped": false, "source": "none"}
```

`shipped: false, source: none` is the honest answer when there is **no positive
evidence** — it is the safe default, not a failure. Treat "no evidence" as "not
done", never as "probably fine".

**Boundary:** this witnesses *that the work happened* (the effect is present,
the record is real-model authored) — not that it is *correct*. Pair it with the
subagent's own tests for correctness.

---

## The four surfaces at a glance

| # | Surface | Covered by worktrees? | The command |
|---|---|---|---|
| 1 | Same file, now | **Yes** — one checkout per agent | (worktree / sandbox) |
| 2 | The merge target | No | `dos arbitrate` + per-branch `dos verify` |
| 3 | External blast radius | No (can't worktree a DB row) | `dos lease` / `dos arbitrate` over an effect lane |
| 4 | The fold (believing "done") | No | `dos verify-result` / `dos verify` |

Worktrees close surface 1 outright. The other three are where a fleet quietly
loses money at scale — and where the one runnable command per surface turns a
re-derivation (or an incident) into a read.

---

## Where to go next

- *You are the agent* and want the deliberate in-session handshake (sub-agents,
  parallel loops, a `parallel()` stage)? Recipe 8 —
  [`examples/fleet_frameworks/in_session_deconflict.py`](../examples/fleet_frameworks/in_session_deconflict.py)
  — is the one-call version, and "The one-line handshake" section above is the why.
- Arriving from a specific framework (LangGraph, CrewAI, AutoGen, the Agents
  SDKs)? The runnable recipes live in
  [`examples/fleet_frameworks/`](../examples/fleet_frameworks/README.md).
- The short-answer versions of these questions are in the
  [FAQ](FAQ.md) — including "don't git worktrees already solve this?" (the
  long answer is surfaces 2–4 above).
- The post-failure searches — "my agent claimed it fixed X but didn't" — are the
  incident pages; this page is the *pre*-failure, design-review companion.
- The economics of why this matters more as tokens get expensive:
  [docs/324 — the re-derivation tax](324_the-token-cost-curve-and-the-re-derivation-tax.md).
