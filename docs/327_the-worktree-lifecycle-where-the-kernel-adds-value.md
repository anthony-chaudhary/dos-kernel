# 327 — the worktree lifecycle: where the kernel adds value past "isolation"

> **Status:** Phase 1 (`dos merge-gate`, the §5 build #1) SHIPPED. The rest is
> design plan. This doc is the position, the four-moment map, the
> mechanism-vs-metaphor line per moment (re-grounded against `src/dos/` at HEAD,
> 2026-06-13), the buildables ranked, and the one cheap spike that settles
> real-vs-metaphor in ~30 lines with no I/O. It proposes one real new verb (`dos
> merge-gate`) and two re-aims of shipped machinery; the verb is now built (the
> pure leaf `dos.mergegate`, the driver `dos.drivers.merge_gate`, the `dos
> merge-gate` CLI verb, and 24 tests). Builds #2–#3 (worktree-lease GC, lease the
> future merge target at spawn) remain open.
>
> **One line:** A worktree is not a *point* that closes one contention surface —
> it is a *transaction* (BEGIN at spawn, COMMIT at merge), and the kernel already
> owns a mechanism at all four of its lifecycle moments. The unclaimed value is
> the COMMIT half: the worktree is the cleanest evidence substrate the kernel
> ever gets, because every moment of its life leaves a fossil to read instead of
> a claim to believe.

---

## 0. The reframe — isolation is half a transaction

The shipped design-review page ([PARALLEL_AGENTS_SAFELY.md](PARALLEL_AGENTS_SAFELY.md))
treats a worktree as a *point*: it closes surface 1 (two agents, same file, now)
and DOS closes surfaces 2–4 (merge target, external blast radius, the fold).
That table is correct. It stops one move too early.

> **A worktree is a transaction. Isolation is BEGIN; the merge is COMMIT.**

Isolation without reconciliation is a *deferred* collision — the STORM/DeLM
result already in the [FAQ](FAQ.md): worktree-per-agent "defers conflict
resolution to a post-hoc merge step." Git gives you the BEGIN (an isolated
checkout) and throws the rest away. The kernel's whole job is to be the thing
that does not believe the agent **at the COMMIT point** — and a worktree is the
ideal place for that job, because each of its four lifecycle moments produces an
env-authored fossil:

| Moment | What git gives | The fossil the kernel reads | What the kernel adds |
|---|---|---|---|
| **Birth** (spawn) | an isolated checkout | the requested region (a `list[str]`) | lease the *future merge target* → refuse the surface-2 collision **before any tokens spend** |
| **Life** (work) | a private branch | `commits_since(start_sha)` on that branch | liveness over a branch with **no sibling commits** polluting the delta |
| **Death** (crash/abandon) | an orphaned dir git won't GC | the lease heartbeat age | reclaim the worktree **soundly**: `DEAD` past the TTL, `ORPHANED_WORKING` (spare it) only *inside* the stall band with activity |
| **Judgment** (merge) | a branch to merge | the runner outcome on **both** trees | merge as an admission gate: `test-witness` + `commit-audit` + `verify` → merge only on a clean verdict |

The rest of this doc walks the four moments, marks the mechanism-vs-metaphor
line per moment (the [docs/211](211_human-isolation-the-dual-of-the-virtual-machine.md)
discipline), and ranks what to build.

---

## 1. BIRTH — the worktree path *is* a lease region *(re-aim, ~0 kernel code)*

[docs/211 §0](211_human-isolation-the-dual-of-the-virtual-machine.md) established
the load-bearing fact: the arbiter is **glob-blind**. A region is an
uninterpreted `list[str]`, and the default admission path is **pure — no I/O**
(`arbiter.py:4-5,219-220`, re-confirmed at HEAD). The shipped story leases over
*logical* lanes (`src/**`). A worktree adds a second thing worth leasing: the
**future merge target** declared at spawn time.

The shared-tree lease answers "can you edit here *now*." Aim the same pure call
at the subtree a worktree's branch will eventually land on, and it answers the
more valuable question — **"can you *merge* here later."** A second agent
spawning a worktree whose branch will land on the same subtree gets the typed
refuse *before it spends a single token*, instead of discovering the collision
at merge where recovery is most expensive (the docs/324 re-derivation tax, paid
at the cheapest possible moment).

**Where it is honest:** this is a re-aim, not a new mechanism — the region is
just a different `list[str]`. **Where it stops:** it refuses *declared* overlap
(arbiter.py is admission over declared trees); a branch that wanders outside its
declared target at merge is surface-2 again, caught by §4, not here. And it is
advisory (PDP-no-PEP) — it coordinates agents that ask first.

## 2. LIFE — liveness on an isolated branch has a clean delta *(shipped; one wording demotion)*

`liveness.classify` decides ADVANCING by the **forward delta** —
`len(git_delta.commits_since(start_sha)) ≥ 1` (`liveness.py:150-151,251`),
never clock-dependent. The value the worktree adds is in the *denominator*: in a
shared checkout a sibling loop's commits land in the same history, so
`commits_since` is polluted by work this agent did not do. **On an isolated
worktree branch the commit stream since `start_sha` is exactly this run's
work** — a clean "is it spinning?" with no cross-talk.

> **The demotion the adversarial pass forced (do not overclaim):** the kernel
> does **not** attribute the delta by author or by lane. It counts commits since
> a start SHA. The cleanliness is a property of the **isolated branch**, not of
> any kernel author-tracking. Write "the worktree gives liveness a clean
> start-SHA boundary," never "the kernel attributes the delta to one author."

This is the supervisor pattern (`drivers/supervisor.py`) pointed at a worktree:
a `STALLED` worktree-lease is scavenged; a `SPINNING` worktree-agent is
*surfaced, never killed*. **Boundary:** liveness reads change-*presence*, not
change-*usefulness* (Wall 3) — a branch churning useless commits reads as alive.

## 3. DEATH — the abandoned worktree is a reclaimable lease, not a leak *(shipped machinery, new noun)*

The moment the shipped docs omit entirely. Worktrees **leak**: an agent crashes,
the harness dies, the user Ctrl-Cs — and `.claude/worktrees/` fills with
orphaned checkouts and branches nobody will merge. Git has no notion of "this
worktree's owner is dead."

The kernel does, and it is **already four-valued**.
`lease_health.classify_lease_health` (`lease_health.py:94-130`) folds heartbeat
age + activity into `LIVE / STALLED / ORPHANED_WORKING / DEAD` — a **pure**
classifier, unit-testable with no filesystem I/O. The verdict is two-banded on
age, and the worktree-GC question maps straight onto it:

- **`age > TTL` → `DEAD`** regardless of activity (the TTL is a *hard backstop
  that wins over activity*, re-confirmed by the spike). Reclaim:
  `git worktree remove` is safe. A worktree that churns useless commits forever
  is still reclaimed once it passes the TTL — the activity grace is **bounded**.
- **`stall_threshold < age ≤ TTL` + activity → `ORPHANED_WORKING`** → **do not
  nuke yet** — the exact case where a naive "old dir → delete" GC eats live work.
  The kernel names this hazard *only inside the stall band*; a wall-clock cron
  does not name it at all. (`activity_state="UNKNOWN"` also yields
  `ORPHANED_WORKING` — never reclaim on missing evidence.)
- **same band + `QUIET` → `STALLED`** → genuinely dead, reclaim.
- **`age ≤ stall_threshold` → `LIVE`** → leave it.

"Which worktrees are abandoned and safe to remove?" becomes a kernel **read**
over the lease set, not a human guessing which `feat-*` dirs are dead — and the
reclaim is *sound* because it fires on a dead heartbeat (a fossil), reclaimed via
the existing WAL + TTL + `OP_HEARTBEAT`/`OP_SCAVENGE` path, not on a guess.

**Boundary:** the owner string is unauthenticated and `--force` bypasses (the
docs/211 Cut 2 ceiling) — this reclaims *cooperating* agents' dead worktrees, not
a determined adversary's.

## 4. JUDGMENT — the merge is an admission gate, and only a worktree gives the kernel *both trees* *(engine shipped; the gate is now SHIPPED — Phase 1)*

The payoff, and the hardest-to-refute claim in the doc. At merge time a worktree
hands the kernel something a shared checkout structurally cannot: **the
before-tree and the after-tree, both materialized, both addressable.** That is
not a nice-to-have — it is the *precondition* for the kernel's sharpest rung.

`test-witness` (TWV, [docs/288](288_twv-the-test-witness-verdict-reverse-classical-testing-as-a-kernel-rung.md))
already says so in its own source: the verdict "joins two bits the agent authors
zero bytes of — **the runner's outcome on the baseline tree and the runner's
outcome on the candidate tree**" (`testwitness.py:25-27`), and names the
mechanism outright: "a worktree checkout without the candidate diff, then with
it" (`testwitness.py:56`). DISCRIMINATES — proof a candidate's new test actually
fails on the tree it didn't touch — is **only reachable when both trees exist.**
A shared checkout has thrown the baseline away; the worktree is what keeps it.

So the merge generalizes to: *gather env-authored witnesses on the worktree →
classify with the pure kernel → merge only on a clean verdict.* That is exactly
`drivers/self_improve.py:run_cycle` — apply-in-worktree → witness (suite +
truth syscall + measured metric) from a clean process → **merge only on KEEP,
discard otherwise** — with the "metric" generalized from "a self-improvement
score" to "suite-green + truth-clean + commit-audit + (optional) test-witness."

> **The load-bearing realization: `run_cycle` is not a self-improvement-only
> pattern. It is the general worktree-merge admission protocol, already shipped.**
> The self-improve loop is the proof-of-concept; the general version is a host-
> agnostic `dos merge-gate` any fleet could wire at its merge step.

**Boundary:** every rung here is *presence*, not *correctness* (Wall 3) —
commit-audit checks the diff did the *kind* of thing claimed; test-witness checks
the test discriminates the *trees*, not that it asserts the intended *behavior*.
Pair with the branch's own correctness review. And it is advisory: the kernel
proposes "clean / refuse," the host actuates the merge.

---

## 5. What to build (ranked by honesty-of-payoff)

1. **`dos merge-gate` — the COMMIT half the docs never wired. SHIPPED (Phase 1).**
   Generalize `run_cycle`'s witness→classify→keep from "self-improvement metric"
   to "any worktree branch": suite-green + truth-clean + `commit-audit` +
   (optional) `test-witness` against the baseline tree → emit a clean/refuse
   verdict; the host merges only on clean. The engine is shipped
   (`self_improve.py`); this is the host-agnostic exposure + a CLI verb. **Highest
   payoff.** *Built as: the pure floor leaf `dos.mergegate` (`classify` →
   CLEAN/REFUSE, admits a correct no-op where `improve` would revert it — the
   one decisive divergence), the driver `dos.drivers.merge_gate` (gather → classify
   → actuate on injected callbacks, deterministic on fakes), the `dos merge-gate`
   CLI verb (exit 0 CLEAN / 3 REFUSE, `--json` receipt, `--narrated` parsed for
   nothing), and 24 tests pinning the floor conjunction, the no-op divergence, and
   non-forgeability.*
2. **Worktree-lease GC (§3).** `dos` scavenge over worktree-path leases →
   `{worktree, lease_health}` so "which are safe to `git worktree remove`?" is a
   read. Rides shipped `lease_health` + WAL/TTL/scavenge. `ORPHANED_WORKING` is
   the load-bearing distinction (don't nuke a dead-heartbeat-but-still-churning
   tree).
3. **Lease the future merge target at spawn (§1).** Aim `arbitrate` at the
   worktree's eventual target subtree so surface-2 collisions refuse at birth.
   Re-aim, ~0 kernel code.

Everything past these three (e.g. attributing a delta to an author, an
authenticated worktree owner) is **out of scope / metaphor** — the kernel keys
on SHAs and unauthenticated strings, and saying otherwise is the overclaim to
cut.

---

## 6. The spike that settles real-vs-metaphor (~30 lines, against HEAD, no I/O)

A single test exercising only the *unmodified* pure kernel, the
[docs/211 §3](211_human-isolation-the-dual-of-the-virtual-machine.md) form —
prove the three buildables rest on shipped purity, not on new mechanism:

1. **Birth is a thin re-aim.** `arbitrate(requested_lane="worktree:src/auth",
   requested_kind="keyword", requested_tree=["src/auth/**"],
   live_leases=[{lane:"…", lane_kind:"cluster", tree:["src/auth/**"]}], config)`
   → assert `outcome=="refuse"` (overlapping merge target caught at spawn). Same
   call, disjoint tree, empty `live_leases` → `acquire`. *Proves §1 needs zero
   kernel edit.*
2. **Death is a shipped four-valued read, two-banded on age.**
   `classify_lease_health` with a heartbeat age **past `ttl_minutes`** → `DEAD`
   *regardless of activity* (the hard backstop); age **in the stall band**
   (`stall_threshold < age ≤ ttl`) **with activity** → `ORPHANED_WORKING`, the
   same band **`QUIET`** → `STALLED`. *Proves §3's GC distinction already ships,
   is pure, and that the activity grace is bounded by the TTL — not unbounded.*
   (Spike trap, recorded so the next author skips it: `parse_iso` accepts only a
   `…Z`-suffixed heartbeat — a `+00:00` offset parses as `None` → age `inf` →
   `DEAD`, so the fixture must stamp `%Y-%m-%dT%H:%M:%SZ`.)
3. **Judgment needs both trees, and only joins env-authored bits.**
   `testwitness.classify` with `baseline=fail, candidate=pass` → `DISCRIMINATES`;
   the same with `--forgeable` (an agent-authored outcome) → `ABSTAIN`, NOT
   DISCRIMINATES. *Proves §4's rung is structural (two real tree-outcomes), not
   narratable — the worktree is what supplies the two outcomes.*

If 1–3 pass: §1 and §3 are re-aims of shipped purity, §4's rung is real and
two-tree-bound, and `dos merge-gate` (build #1) is `run_cycle` with a
generalized metric — a driver + CLI verb, not a kernel edit. The doc's only new
*kernel-adjacent* surface is the merge-gate driver; the arbiter, `lease_health`,
and `testwitness` are used unchanged.

---

*Provenance: developed 2026-06-13 from the operator goal "where can DOS add value
in worktree context more — isolation + arbitration still somehow." Every
`[shipped]` claim re-grounded against `src/dos/` at HEAD **and the §6 spike was
run** before being written here (`tests/test_worktree_lifecycle_spike.py`). The
pass forced one demotion (§2 — liveness keys on a start SHA, not an author) and
two sharpenings made honest by running the code: §3 — `ORPHANED_WORKING` is real
but the activity grace is **bounded by a hard TTL** (the spike first picked an
age past TTL and got `DEAD`, correcting the doc); §4 — `testwitness` already
names the worktree as its two-tree mechanism and ABSTAINs on a narrated outcome.
This is a kernel design plan (how kernel modules compose), so it lives in
`dos/docs/`.*
