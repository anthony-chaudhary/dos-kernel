# 363 — Deconfliction beyond the file tree: the resource periodic table

> **Status:** survey + design map, with a first slice SHIPPED. The arbiter
> deconflicts agents on ONE resource today — the file tree. This doc takes
> inventory of the *other* things a real fleet collides on, sorts each by the
> [docs/89](89_the-lane-is-a-region-lock.md) region-lock litmus, and ships the one
> axis that the file algebra genuinely cannot express today: the `lock://NAME`
> named-mutex region (`src/dos/named_lock.py`, `tests/test_named_lock.py`).
>
> **One line.** A lane is a *leased predicate-lock over a region*, and the region
> has always been a set of file-path globs. But a fleet of agents collides on far
> more than files — a git branch, a database range, a publish step, an API budget,
> a deploy order. This doc is the periodic table of those collisions: which are the
> *same primitive over a richer predicate algebra* (a new `prefixes_collide`, not a
> new arbiter — docs/89 §4.2), which are the *cardinality/order* siblings the
> kernel already proved on other axes (docs/125 trifecta, docs/247 clearance), which
> are really a *budget* the kernel already ships (docs/97 `max_concurrent`), and
> which are seductive **category errors** that look like deconfliction but are not.
>
> **Lineage.** The forward path docs/89 §4.2 named ("design the lattice as a new
> predicate algebra plugged into the existing disjointness gate, not a new
> arbiter") and docs/97's `RegionSource` seam ("how a free region of a class is
> produced") both point here. The single-axis deep dives — docs/125 (capability
> colors), docs/247 (sensitivity lattice), docs/131 (capability source) — are the
> CARDINALITY/ORDER columns of this table already built or planned; this doc is the
> *breadth* map they are depth cuts of, and it adds the missing REGION_LOCK column.

---

## 0. The question, stated precisely

`arbitrate()` admits a new worker iff its requested **file tree** is provably
disjoint from every live lease's tree. That is the whole deconfliction surface
today. The operator's question — *what are the top things to deconflict beyond
file paths?* — is well-posed because docs/89 already proved the file tree is just
**one instance** of a general primitive:

> A lane is a named, leased predicate-lock over a region of the workspace, admitted
> by predicate-disjointness. `arbitrate` is a lock manager whose granularity is a
> glob-set. The capability-lattice generalization is *the same primitive over a
> richer predicate algebra than path-prefixes — a new `prefixes_collide`, not a new
> arbiter.*

So "deconflict more things" = "give the arbiter more region types." The work is to
**name the region types worth having**, and to be honest about which candidates are
region types at all.

---

## 1. The five shapes (the classification rule)

A fleet-collision survey (3 independent lenses — classical concurrency control, a
production agent-fleet SRE, a security architect — 52 raw candidates) sorts cleanly
into five shapes. The first two are real admission predicates; the rest are not
deconfliction even when they look like it.

| Shape | The test | Math | Home in DOS |
|---|---|---|---|
| **① REGION_LOCK** | admit iff the requested claim is DISJOINT from every held claim | a `prefixes_collide` over some namespace | `DisjointnessPredicate` + a region normalizer |
| **② CARDINALITY / ORDER** | refuse when a SET-UNION or LATTICE-ORDER over what ONE agent holds reaches a forbidden value | set cardinality / partial order | a request-relative `AdmissionPredicate` (docs/125, docs/247) |
| **③ BUDGET** | refuse when the live COUNT of a class reaches `max_concurrent` | a counting semaphore | already shipped: `arbitrate(class_budgets=…)`, docs/97 |
| **④ DIFFERENT SYSCALL** | it is a thing you VERIFY / a liveness signal / a recovery proposal — not an admission | n/a | `verify`, `liveness`, `resume`, `effect_witness` |
| **⑤ CATEGORY ERROR** | a fixed-slot pool, a CONTAINMENT (can't-escape) boundary, or a lock-ordering protocol — the swim-lane trap | n/a | nothing — docs/89 §4.4 says *stop* |

The litmus that separates ① from ⑤ is docs/89 §4.4 verbatim: *is the property a
property of a leased predicate-lock over a region?* If admission is "admit iff
disjoint," it is ①. If it models a fixed lane count, or a containment boundary you
cannot escape (a cgroup, a per-agent sandbox), it is ⑤ — that is *isolation*, not
*arbitration*, and the cure is namespacing, not a lease.

---

## 2. The periodic table — the top 10 axes

Ranked by (how often it bites a real 2026 fleet × deconfliction-fit). The
**predicate algebra** column is load-bearing: it is the thing that plays the
`prefixes_collide` role, and naming it is what proves the axis is "the same
primitive, richer region."

| # | Axis | What two agents collide on | Shape | Predicate algebra (the `prefixes_collide` role) | Status |
|---|---|---|---|---|---|
| 1 | **File tree** | the same path / glob | ① | path-prefix collision (`_tree.prefixes_collide`) | **shipped** |
| 2 | **Named mutex** (`lock://NAME`) | an abstract critical section with no file backing — `gh-pages-publish`, a PyPI upload, a tag mint | ① | string-equality over a one-element namespace (a point = degenerate range) | **shipped here** (`named_lock.py`) |
| 3 | **VCS branch / ref** | the same branch or `HEAD` — non-fast-forward race, clobbering rebase | ① | ref-name equality + a fast-forward (ancestry) compatibility test | unbuilt — see §4 caveat |
| 4 | **DB row / key-range** | the same record, or a RANGE predicate (`all rows id<100`, all `ready`-labeled issues) — lost update, phantom | ① | key-range intersection / next-key locking (System R range locks) | unbuilt |
| 5 | **Declarative cloud object** (k8s deploy, Terraform state, Helm release) | the same live spec — read-modify-write on one reconciled object | ① | resource-path equality + a `resourceVersion`/state-lock CAS | unbuilt |
| 6 | **Capability conjunction** (lethal trifecta) | one agent holding private-data + untrusted-content + exfiltration at once | ② | set-union reaching cardinality 3 over capability *colors* | planned — [docs/125](125_the-trifecta-color-and-the-capability-conjunction.md) |
| 7 | **Sensitivity lattice / compartment** (clearance, PII egress, data residency) | a write that moves data DOWN a level, or an actor straddling two compartments | ② | lattice order-compare (`write_level ≥ max(read_levels)`) + compartment compatibility | planned — [docs/247](247_the-clearance-lattice-a-sensitivity-class-as-an-arbiter-color.md) |
| 8 | **Same-actor exclusion** (separation of duties, irreversibility class, cumulative spend) | one principal holding two roles policy forbids together — author+approver, reversible+irreversible, spend summing past a cap | ② | a forbidden PAIR / a sum-over-a-principal's-subtree crossing a ceiling | unbuilt — the under-covered ② family (§5) |
| 9 | **Queue partition / log tail** | a consumer-group partition, or the single-writer tail of an append-only log (the WAL, a ledger) | ① | partition-id equality + a single-writer-per-region rule | unbuilt |
| 10 | **Idempotency key** for an outward effect | two agents independently sending the same email / charge / webhook | ① (CAS) | dedup over a logical-operation id — claim-the-key-or-refuse | partially: the [docs/343](343_the-exactly-once-envelope-and-the-re-drive-contract.md) envelope |

Two whole families collapse into shapes the kernel ALREADY has, and naming them
keeps the table honest:

- **Quota / rate / cost / seat-pool / concurrency-cap** (API token budget,
  model-provider concurrency cap, cloud quota, CI runner pool, account seats) — this
  is shape ③, a **counting semaphore**, and the arbiter already ships it as
  `class_budgets={kind: max_concurrent}` (docs/97 Phase 1, the `_budget_exhausted`
  gate). It is NOT a region lock: "at most 3 writers to the orders table regardless
  of which rows" is a budget; "disjoint row ranges coexist" is the lock. Keep them
  apart — conflating them is the docs/89 §4.4 swim-lane error.
- **Singleton role / leader election / distributed lease+fencing** — this is the
  `exclusive` lane the kernel already has: `max_concurrent = 1` and refuse against
  every other class. Leader election is the coarsest region lock (the whole
  workspace), with a TTL + fencing token on the lease envelope (the lease spine
  already carries `pid`/`ttl`/`heartbeat`, docs/89 §5).

---

## 3. The slice shipped here — `lock://NAME`

The named mutex (axis 2) is the one axis from the survey the file algebra genuinely
**cannot express today**, and it ships with almost no new mechanism — the strongest
possible demonstration of the docs/89 §4.2 thesis.

**Why it's the right first slice.** Every other unbuilt REGION_LOCK axis (branch,
DB range, k8s object) needs its OWN range-intersection predicate AND a cross-scheme
aliasing analysis (§4). The named mutex needs neither: a pure critical section is a
*point*, so "disjoint" is just "different name," and a point rides the EXISTING
`prefixes_collide` unchanged. And it closes a real gap — two `/release` loops can
race the `gh-pages` publish today because there is no path to lease.

**How it works (mechanism).** A lane-tree entry may be `lock://NAME`. The single new
piece is `named_lock.normalize_entry`, the lock-aware front door to
`_tree.norm_tree_prefix`:

- a **file** entry (no scheme) passes through to `_tree.norm_tree_prefix` *verbatim*
  — so the file algebra is **byte-identical** and every existing verdict is
  preserved (the load-bearing regression: the full arbiter/overlap suite stays green
  through this path, `tests/test_named_lock.py::TestFilePathByteIdentical` + the 193
  pre-existing arbiter/overlap tests);
- a present, glob-free lock name → a reserved, file-disjoint prefix that collides
  only with the identical lock (`lock://gh-pages-publish` on both sides →
  `REFUSE_EXACT_GLOB`, a hard collision, not a diluted ratio);
- a **bare** (`lock://`) or **wildcarded** (`lock://release-*`) name → the universal
  prefix `""` (collides with everything) — the named-mutex twin of `_tree`'s
  empty-tree rule and the `**/*`→universal corner: an *unknown* critical section is
  never waved through as "touches nothing."

`lane_overlap.py` routes its two normalization call sites through this front door, so
a lock region flows through the UNCHANGED ratio/exact-glob logic. The arbiter, the
`DisjointnessPredicate`, the overlap floor, and the lease/journal spine are all
untouched. A richer region rode the unchanged gate — which *is* the thesis.

**What an operator does.** Put `lock://gh-pages-publish` in a lease tree (a
`--scope`, a release skill's lane). The second `/release` loop that asks for the
same lock is refused at Step 0, exactly as a file collision is, and waits for the
holder to release. No new reason token: a named-mutex collision IS a region
collision, so it carries the existing `REFUSE_EXACT_GLOB` / `REFUSE_OVERLAP` verdict
and rolls up to the same "wait for release" cause — reusing the vocabulary keeps the
taxonomy honest.

---

## 4. The honest scope limit — why ONLY `lock://`, and why "different schemes never
collide" is rejected

The seductive next step is a full resource-URI algebra: `branch://`, `db://`,
`k8s://`, `queue://` all in the lane tree, with a rule "two URIs collide iff same
scheme AND their scheme-specific predicates intersect; different schemes never
collide." **That rule is unsound in the false-ADMIT direction — the one direction a
region lock must never fail in — and it is not shipped.**

The violating case is mundane, not exotic: `branch://master` and `file://src/**`
name the *same working-tree bytes*. A worker holding `branch://master` to rebase or
merge **rewrites the working tree**, stomping `src/**` out from under a file-lease
holder mid-edit. Two locks the naive rule calls disjoint are contending for the same
physical bytes — the silent concurrent-clobber the arbiter exists to prevent. A
second case: `lock://pypi-upload` and `file://dist/**` are the same operation's two
faces.

So the law is **not** "different schemes never collide." It is: *cross-scheme
disjointness is sound only for schemes over physically independent substrates; where
two schemes can name the same underlying bytes — notably `branch://`/`worktree://`
vs `file://`, and any `lock://` guarding a file region — the kernel cannot prove
disjointness and a lease must declare BOTH regions.* `lock://` is sound today
*precisely because* a pure named mutex is over a substrate independent of every file:
holding `lock://gh-pages-publish` claims the abstract publish step, not any path.
This module invents **no** mapping from a lock name to files; a lock that guards a
file region must also declare that file region in the same lease.

Each future scheme (branch, DB range, k8s) is therefore its own build, gated on its
own cross-scheme aliasing analysis and its own range-intersection predicate — never
smuggled in on the named mutex's coattails.

---

## 5. The under-covered column worth a future doc — same-actor exclusion (②)

The CARDINALITY/ORDER shape is well-covered on two axes (docs/125 colors, docs/247
levels). But a *third* ② family from the survey is unbuilt and high-leverage:
**same-actor exclusions** — a refusal keyed not on disjointness between two agents,
nor on a lattice, but on **one principal holding two things policy forbids
together**:

- **Separation of duties** — the agent that authored a change must not be the one
  that approves/merges it (`actor(author) == actor(approver)` ⇒ refuse). The classic
  audit control, and exactly an arbiter shape (a forbidden pair over a `(subject,
  action)` relation held by one identity).
- **Irreversibility class** — an agent batching a reversible action and an
  irreversible one in the same uncommitted unit, so a rollback of the cheap part
  cannot undo the expensive part (a saga/savepoint boundary).
- **Cumulative spend authority** — a fan-out tree under one principal whose
  spend-authorizing actions SUM past a budget ceiling; no single call looks large,
  the union does (the docs/125 §3 held-set discipline, with a sum instead of a
  cardinality).

All three are the docs/125 §3 "held-set" mechanism (union over a principal's live
leases, read at the boundary) with a different aggregate. They are flagged here as
the next ② payload, not built — the same honesty discipline docs/247 used to flag
read-up-against-agent-clearance as Phase 3.

---

## 6. The one-paragraph version

The file tree is one resource a fleet collides on; it is not the only one, and the
arbiter was always a general region-lock, not a file-specific one. The other
collisions sort into five shapes: REGION_LOCKs (a richer `prefixes_collide` — branch,
DB range, cloud object, named mutex, queue partition, idempotency key),
CARDINALITY/ORDER siblings (the trifecta and clearance lattice the kernel already
proved, plus the unbuilt same-actor-exclusion family), BUDGETs (the
`max_concurrent` counting semaphore the kernel already ships), things that are really
a DIFFERENT SYSCALL (verify/liveness/resume), and CATEGORY ERRORS (fixed-slot pools
and containment boundaries the swim-lane metaphor smuggles in — stop). The cheapest
honest win is the **named mutex**: a critical section with no file backing, shipped
here as `lock://NAME` riding the unchanged path algebra, byte-green on file://, and
deliberately the *only* new scheme — because "different schemes never collide" is
unsound the moment a `branch://` and a `file://` name the same bytes.

---

## See also

- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) — the
  primitive this doc generalizes; §4.2 is the "richer predicate algebra, not a new
  arbiter" thesis the `lock://` slice makes concrete, and §4.4 is the category-error
  litmus this doc's §1/§2 apply.
- [`97_concurrency-class-model-plan.md`](97_concurrency-class-model-plan.md) — the
  `RegionSource` seam ("how a free region of a class is produced") and the
  `max_concurrent` budget that is shape ③ in §2.
- [`125_the-trifecta-color-and-the-capability-conjunction.md`](125_the-trifecta-color-and-the-capability-conjunction.md),
  [`247_the-clearance-lattice-a-sensitivity-class-as-an-arbiter-color.md`](247_the-clearance-lattice-a-sensitivity-class-as-an-arbiter-color.md),
  [`131_the-capability-source-the-authority-plane-dual-of-the-witness.md`](131_the-capability-source-the-authority-plane-dual-of-the-witness.md)
  — the CARDINALITY/ORDER columns of the table (§2 axes 6–7), each a depth cut this
  breadth map sits over.
- `src/dos/named_lock.py`, `tests/test_named_lock.py` — the shipped `lock://NAME`
  slice (§3); `src/dos/lane_overlap.py` is the one call site that turns it on.
- [`343_the-exactly-once-envelope-and-the-re-drive-contract.md`](343_the-exactly-once-envelope-and-the-re-drive-contract.md)
  — the idempotency-key axis (§2 axis 10) seen from the exactly-once side.
