# 391 — Salience: the true-but-PARKED verdict that prevents silent loss

> **A true thing must never be lost just because it is not useful today.** Truth
> and usefulness are different axes. A finding can be *perfectly true* and *not in
> the hotpath by default* — a real bug on a path nothing takes, a correct note
> about a feature behind a disabled flag, a lesson that still holds but no longer
> decides. The danger is dropping it *as if it were false*: it costs nothing today
> and bites the day the path goes live, and the drop leaves no record. This doc
> adds the verdict that converts that silent drop into a recorded, **recoverable
> PARK** — `salience.classify()` in `src/dos/salience.py`.

This is the operator goal (2026-06-24) stated as a kernel leaf: *"DOS can help
prevent silent 'loss' of things that might literally be true but not useful. e.g.
not in hotpath by default."* It is the **keep-but-park dual** of outcome-driven
retirement ([docs/350](350_outcome-driven-retirement-the-library-drift-fix.md)):
`retire` proposes *evicting* a library item from the active set once a witness has
measured it earns no place; `salience` *parks a true thing in place* — out of the
default hotpath under a typed reason, **with a defined cheap path back in**. Neither
silently drops; the difference is that `retire`'s terminal disposition is
evict-to-archive with no re-entry tier, while `salience` adds the retained-but-live
tier and the re-activation verdict `retire` lacks.

## The one idea: don't conflate "false" with "not useful"

A fleet's findings, claims, outputs, code paths, and remembered lessons are sorted
on two axes at once: **true / false**, and **useful / not useful**. They are
orthogonal. The kernel already guards the truth axis hard — `verify` reads ship
from git, `commit-audit` reads the diff, the whole point is to not believe a
self-report. But the *usefulness* axis is where things quietly vanish: a reviewer
or a picker, facing a true-but-low-value item, drops it. The drop is silent — no
record, no reason, no way back — so:

- a **true** thing is discarded as though it were **false**, and
- the day its path goes live, the bug nobody kept bites, and
- nobody can even tell it was ever known.

`answer_shape` ([docs/156 §4](156_grounded-rag-adoption-and-the-claim-ledger-seam.md))
already named one face of this — *"'Never shipped a wrong number' was literally true
and badly misleading."* That is the **output** face (true facts, useless shape).
This doc generalizes it to the **disposition** face: a true *item* that is not, by
default, on the hot path. The fix is symmetric — make the kernel carry a verdict
that says "this is true but parked, and here is the typed reason," so the act of
setting it aside is **recorded and reversible** instead of silent and final.

## The contract, in one line

    PARKED  ≠  dropped.   A parked thing is RETAINED and SURFACED, never silently lost.

No salience state ever means delete. `LIVE` keeps the thing in the hotpath;
`PARKED` keeps it out-but-surfaced under a typed reason; `INDETERMINATE` keeps it
pending a JUDGE/HUMAN. Deletion is a *different, stronger* verdict (`retire`, or a
lifecycle TOMB), gated on measured evidence — never a side effect of low salience.
`SalienceVerdict.is_retained` is `True` for every state; that property *is* the
guarantee, checkable in one line.

## Why a separate verdict (the neighbours, and the gap each leaves)

`salience` is a new member of the keep-only-what-a-witness-confirms family, and it
exists because each neighbour answers a *different* question:

| Verdict | Question | Action |
|---|---|---|
| `drivers.memory_recall.classify_recall` (docs/103) | is this still **TRUE**? | re-probe |
| `retire.classify` (docs/350) | does it still **EARN ITS PLACE**? | **DROP** (measured) |
| `answer_shape.classify` (docs/156) | is this output an **ANSWER**? | withhold (shape) |
| **`salience.classify` (this doc)** | is this true thing **LIVE or PARKED**? | **KEEP** (park, recoverable) |

- **vs `retire`** — the deliberate opposite on the same orthogonality, and the
  closest neighbour, so the line must be exact. `retire` does **not** silently drop
  either: `retire.classify` only *proposes*, a human disposes (docs/350 "retirement
  proposes; it never deletes"). The real distinction is narrower and load-bearing:
  `retire`'s terminal disposition is **evict-from-active** (a proposal to archive),
  and it has **no parked-but-live tier and no re-entry verdict** — its KEEP/RETIRE is
  in-active vs out-of-active. `salience` *is* that missing middle tier: a true thing
  **retained in place but out of the default hotpath**, carrying a typed reason **and
  a `reactivation` line — the cheap, defined path back in**. Its `LOW_CONTRIBUTION`
  rung is the explicit bridge (the same measured signal), but it PARKS-in-place where
  `retire` would propose evict-to-archive. Use `retire` when enough trials prove a
  thing earns no place at all; use `salience` to keep a true thing reachable and
  recoverable when it is merely not, today, on the hot path.
- **vs `answer_shape`** — the same three-state asymmetry (`PARKED`/`LIVE`/
  `INDETERMINATE` ↔ `NON_ANSWER`/`ANSWER_SHAPED`/`INDETERMINATE`) and the same
  honesty boundary, but a different axis: shape of an *output* vs disposition of an
  *item*.
- **vs `lifecycle` PARK (docs/207)** — `lifecycle` already has a plan-CLASS `PARK`
  (ACTIVE / MAINTENANCE / PARK / TOMB / DRAFT), but it is *semantic*, *advisory*,
  *whole-plan*, and *judge-approved*. `salience` is the *mechanical, per-item* floor
  underneath it: a deterministic W2 verdict on one finding/path/lesson that can
  FEED a lifecycle PARK transition but never needs a judge to fire. Same word, one
  altitude down — and `TOMB` (the terminal drop) is exactly what `salience` is *not*.
- **vs `retention` (docs/106)** — `retention` keeps the fail-safe asymmetry this
  leaf inherits: *"a misconfigured policy may keep too much, but it must never cause
  a False-collect of state the kernel still needs."* `salience` carries that up to
  the item level — a False-PARK is tolerable (still there, surfaced, recoverable); a
  silent loss is not. The fail-safe always points at RETAIN.

## The mechanism — `salience.classify()`

A pure leaf: `classify(evidence, policy) -> SalienceVerdict`. The three states:

- **`PARKED`** — true-but-not-useful: out of the hotpath under a *typed*
  `reason_class`, RETAINED and SURFACED. The only positive classification.
- **`LIVE`** — no park-reason fired; kept in the default hotpath. Like
  `ANSWER_SHAPED`, this is *"no disqualifier,"* **NOT** a claim of importance.
- **`INDETERMINATE`** — cannot decide on the evidence; abstain → RETAIN + surface.
  The floor.

The park reasons are **policy, not hardcode** (the `dos.reasons` closed-set-as-data
discipline): the kernel ships the cross-domain classes — `NOT_IN_HOTPATH`,
`UNREACHABLE`, `SUPERSEDED`, `LOW_CONTRIBUTION` — and a host declares its own via
`SalienceEvidence.declared_reason`. The evidence is **env-authored** (a static-
analysis reachability bit, a flag state, a supersession event, a MEASURED
contribution count); the item's own self-description is read for *nothing* — the
same non-forgeable discipline as `retire`. The kernel carries the **fold + the
floor**; the host carries the **signals**. The kernel does not (and must not)
compute reachability — that is a host's static analysis, handed in at the boundary.

Decision order (first match wins; the more-deliberate / harder signal first):
declared reason → superseded → unreachable → not-in-hotpath → the measured rung →
LIVE (if any evidence was present) → INDETERMINATE (none). The measured rung is
OFF unless the host sets `min_trials > 0`, and it ABSTAINS below that floor — never
park on thin evidence (the `retire` witness-ceiling rule, docs/350 §3).

Every `PARKED` verdict also carries a **`reactivation`** line — the operator-facing
"how to pull it back into the hotpath" string, resolved per reason class (mirroring
`pickable.HoldReason.next_action`, not a heavier registry). This is the re-entry
affordance that makes "recoverable" concrete and is the load-bearing line between
`salience` and `retire`: a parked thing that cannot be recovered is just a slow
drop. A host's own `declared_reason` resolves to a generic recovery line, so a
parked item is *always* recoverable, never a dead end.

`partition(items, policy)` is the fold that makes the guarantee operational: every
item lands in exactly one of `{live, parked, indeterminate}` and **none is
dropped** — `total == input count`, checkable. A caller routes the hotpath to
`live`, SURFACES `parked` (with each row's typed reason and `reactivation` line) for
recovery, and escalates `indeterminate`. The silent drop is no longer reachable: a
parked truth is sitting in `parked` with its reason and its path back, waiting.

## The honesty boundary (do not let this drift into "is it worth it?")

This verdict judges **mechanical / measured** salience, never **semantic
importance.** `LIVE` means "no park-reason fired," not "this matters." The question
*"is this finding actually worth acting on?"* is the Tier-3 gestalt the kernel
ABSTAINS on (the docs/212 world-witness arc; the `answer_shape` boundary one axis
over): it has no independent witness, so it belongs to a JUDGE (advisory, fail-to-
abstain) or a HUMAN. On anything the mechanical/measured evidence cannot decide,
`salience` returns `INDETERMINATE` — and `INDETERMINATE` means RETAIN, never a
confident `LIVE` and, above all, never a silent drop. On the witness ladder
(docs/192) a `PARKED` is a **W2-presence-class** call (a bit the environment
authored), the same altitude as `verify`'s file-path rung. It is *advisory* (PDP,
not PEP): it reports a park; the consumer routes the hotpath; it never executes a
move and never deletes.

## The fail-safe direction — always RETAIN

Everything points one way: a null policy → INDETERMINATE (retain); a null/empty
evidence → INDETERMINATE (retain); an unknown signal (`reachable is None`) → never
parks; thin contribution evidence → never parks; a would-be-parked item under a
`None` policy → retained, not parked-away. `classify` NEVER raises. The dual of
`run_judge`'s fail-to-abstain, pointed at "do not lose anything." A False-PARK is
recoverable; a silent loss is not — so the verdict prefers LIVE/INDETERMINATE to a
wrong PARK, and prefers a recorded PARK to a silent drop.

## Go-parity obligation (docs/385 §3 / §8)

Per the strongly-typed mandate, a new pure decider is *"authored in Go, or ships
with a Go parity port queued before it is relied on"* — a Python-only core with no
recorded reason is the regression. Like `answer_shape`, `salience` is born in
Python so it is reachable through the still-Python seams (`dos salience` CLI, Tier
4; `import dos`, TP5). It is pure `classify(evidence, policy)` with no I/O and no
regex (enum / bool / float only) — i.e. PORT-READY for the docs/124 differential
gate, in the **TP2 RE2-clean pure set**. The Go parity port is **QUEUED** there;
this paragraph is the recorded obligation that keeps it a queued port, not a
backward step.

## Acceptance / litmus

- **`PARKED` ≠ delete.** `SalienceVerdict.is_retained` (and `Salience.*.is_retained`)
  is `True` for every state; `partition` drops nothing (`total == input count`).
  (Pinned: `tests/test_salience.py::test_every_state_is_retained`,
  `::test_partition_drops_nothing`.)
- **The fail-safe is RETAIN.** A null policy / null evidence / unknown signal /
  thin evidence never parks and never raises.
  (`::test_none_policy_with_parkable_evidence_retains`, `::test_thin_evidence_never_parks`.)
- **Every parked thing is recoverable.** A `PARKED` verdict always carries a
  non-empty `reactivation` line (the re-entry path `retire`'s evict-to-archive
  lacks); even a host-declared reason gets the generic recovery line.
  (`::test_parked_carries_a_reactivation_line`, `::test_host_declared_reason_falls_to_generic_line`.)
- **The honesty boundary holds.** `LIVE` is "no park-reason fired," not importance;
  the semantic question abstains to INDETERMINATE.
  (`::test_live_is_not_a_claim_of_importance`.)
- **Kernel-clean.** Pure stdlib; names no host lane and no vendor; the verdict IS
  the exit code, published in `dos doctor --json exit_codes` under `salience`.
  (`::test_cli_exit_codes_published_in_doctor`.)
- **Go port queued, not skipped** (docs/385 §8) — recorded above.

## References

- [`350_outcome-driven-retirement-the-library-drift-fix.md`](350_outcome-driven-retirement-the-library-drift-fix.md) — the DROP dual; the keep-only-what-a-witness-confirms family; the thin-evidence ceiling.
- [`156_grounded-rag-adoption-and-the-claim-ledger-seam.md`](156_grounded-rag-adoption-and-the-claim-ledger-seam.md) — `answer_shape`, the output face of "literally true and badly misleading"; the three-state asymmetry and honesty boundary this leaf mirrors.
- [`207`](207_dispatch-workflow-extraction-and-the-pickable-substrate-completion.md) / `src/dos/lifecycle.py` — the plan-class `PARK`/`TOMB` taxonomy (docs/207 §5c) `salience` sits one altitude below.
- [`106_garbage-collection-and-the-reachability-verdict.md`](106_garbage-collection-and-the-reachability-verdict.md) / `src/dos/retention.py` — the keep-too-much-is-tolerable / never-False-collect fail-safe asymmetry.
- [`192_the-world-state-witness-ladder-and-the-w2-w3-gap.md`](192_the-world-state-witness-ladder-and-the-w2-w3-gap.md) — the W2-presence altitude a `PARKED` call sits at.
- [`385_the-strongly-typed-mandate-and-the-python-retreat-plan.md`](385_the-strongly-typed-mandate-and-the-python-retreat-plan.md) — the Go-parity obligation and the TP2 pure-set queue.
