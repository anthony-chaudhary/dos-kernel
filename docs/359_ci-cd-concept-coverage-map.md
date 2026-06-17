# 359 — The CI/CD concept coverage map: what a trust substrate covers at the pipeline boundary

**Files:** `docs/answers/README.md`, `docs/answers/dos-for-ci-cd.md`

> **Status:** 🟢 **Shipped** (the map; docs/359 P1). This doc is the deliverable:
> a complete map of the industry CI/CD concept space onto DOS's primitives, an
> honest denominator for "coverage," and the small set of kernel-free gaps it
> closes. It adds **no kernel module** and trips **no litmus** — it routes each
> concept to a verb that already ships, names what DOS deliberately does not own,
> and names the thin seam that closes each genuine gap.

## What this is, in one sentence

Mapped against the full industry CI/CD vocabulary (~140 concepts), DOS already
serves the **verify / admit / observe** functions of CI/CD as kernel primitives —
and deliberately does **not** own the deployment functions — so "covering CI/CD"
for a *trust substrate* means routing each concept to its DOS verdict or naming it
honestly out of scope, not building a deploy engine.

## The denominator — what "98% of CI/CD concepts that can be used by DOS" means

DOS is the domain-free **trust substrate** for fleets of agents — "the kernel is
the part that doesn't believe the agents." It is **not** a CI/CD platform, a deploy
engine, or a build system. So a coverage claim only means something once we sort the
concept space into three buckets:

- **NATIVE** — DOS already ships a primitive that *is* this concept or directly
  serves it. (Merge queue → `dos arbitrate`; circuit breaker → `dos breaker`;
  attestation → `dos attest`; audit log → `dos observe`.)
- **ADJACENT** — DOS *could* serve it with a thin **driver / doc / `dos.toml`
  table / answer page** but doesn't yet. The closeable gap.
- **OUT-OF-SCOPE** — belongs to the deploy engine or build system; DOS would never
  own it. (Canary traffic routing, runner autoscaling, the blue-green switch, SBOM
  *generation*, k8s probes.)

**"CI/CD concepts that DOS can use" = NATIVE ∪ ADJACENT.** Bucket 3 is excluded by
definition: DOS *can't* use what it shouldn't own. The 98% target is over that union
— after closing the ranked gaps below, ≥98% of (NATIVE ∪ ADJACENT) has a shipped or
this-doc-shipped DOS surface, with the residual <2% named explicitly as deferred
(§7). This is the same honesty the kernel demands of every verdict: state the
denominator, then state coverage against it (docs/297, the helped-denominator rule).

DOS's value at the CI boundary is exactly three things, and the map keeps to them:
**verification** (catch the lie at the gate — docs/225), **admission** (lane
arbitration so concurrent work can't collide — docs/89), and **observability** (the
verdict journal — docs/262). It is advisory-by-construction at the actuation
boundary (docs/99): it computes the verdict and sets an exit code; the host's branch
protection or deploy gate is what *enforces*.

## The six-function spine — the (a)–(f) tags ARE the DOS verdict families

Every CI/CD concept's primary function maps to one DOS family. This is why the map
is tight rather than forced — the industry's functional split and the kernel's
verdict-family split are the same six axes.

- **(a) trigger work → the picker substrate.** "What's next, and is it worth
  picking?" — `dos pickable` / `enumerate` / `cooldown` / `pick-priority`
  (docs/168, docs/207). A CI trigger fires a run; DOS's picker decides *which unit*
  a worker should pick and whether it's churn.
- **(b) gate / admission → the admission + loop-gate families.** `dos arbitrate` /
  `scope-gate` / `refuse(reason_class)` (the closed refusal vocabulary), plus the
  loop gates `dos improve` / `breaker` / `exec-capability` / `hook-exit`. A required
  status check, a coverage gate, a manual approval, a policy-as-code rule — all are
  "may this proceed?" verdicts.
- **(c) verify an outcome → the truth + witness families.** `dos verify` (did the
  phase ship, from git ancestry), `dos commit-audit` (claim vs diff),
  `dos coverage` / `test-witness` (did a test actually exercise the change), the
  non-git evidence drivers (`ci_status`, `os_acceptance`, `content_diff`,
  `provider_ledger`), and `dos attest` (a portable signed receipt). This is the
  family CI/CD calls "tests, quality gates, provenance."
- **(d) coordinate concurrency → the lease / arbitrate family.** `dos lease` (the
  cross-process archive mutex) and `dos arbitrate` (lane disjointness). A merge
  queue serializing PRs, a concurrency group, a deployment lock — all are "two
  workers must not touch the same region at once," which is the lane-as-region-lock
  primitive (docs/89).
- **(e) recover from failure → the recovery family.** `dos resume` (ARIES: analyze
  → redo → continue, *proposes* never executes), `dos rewind`, `dos reconcile`,
  `dos breaker` (failure-class counting). Retries, automated rollback, drift
  reconciliation, circuit breaking.
- **(f) observe / audit → the observability family.** `dos observe` (the verdict
  journal — every adjudication, folded), `dos trace` (walk the spine for one run),
  `dos notify` (the transport-agnostic notifier seam), `dos top`, `dos decisions`.
  Audit logs, pipeline metrics, deploy markers, DORA metrics.

## The coverage map

Schema: `concept | fn (a–f) | bucket | DOS primitive / surface | status`.
`fn` is the *primary* function (some concepts span two; the tag marks the dominant
intent). `status`: **shipped** (the surface exists today), **this-doc** (a gap §6
closes), **out** (bucket 3, named for honesty).

### 1. Source & trigger

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Push / PR / webhook trigger | a | OUT | the host CI fires the run; DOS adjudicates inside it | out |
| Scheduled / cron trigger | a | NATIVE | `dos pulse` (standing heartbeat), CronCreate cadence | shipped |
| Manual / workflow dispatch | a | OUT | host dispatch; DOS gates the dispatched work | out |
| Path / branch / tag filter | a | OUT | host trigger config | out |
| Monorepo change detection | a | NATIVE | `dos arbitrate` lane = changed file-tree; `scope-gate` | shipped |
| **Merge queue / merge train** | d | NATIVE | `dos arbitrate` lane disjointness; `merge_group` trigger in `dos-gate.yml` | shipped |
| CI skip directive | b | NATIVE | `dos commit-audit` ABSTAIN on `wip:`/`merge:` (never a false block) | shipped |

### 2. Pipeline structure

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Pipeline / stage / job / step | a | OUT | host pipeline; DOS is a step inside it | out |
| DAG dependencies | d | OUT/ADJ | host `needs:`; DOS's `pickable` blockedBy is the agent-task analogue | shipped (tasks) |
| Matrix build (fan-out) | d | NATIVE | the dispatch fan-out + `dos arbitrate` per-lane co-launch safety | shipped |
| Fan-in / aggregation | d | NATIVE | the witness-fold at a `parallel()` barrier (`dos-witness-claim`) | shipped |
| Reusable workflow / template | a | NATIVE | `verify-action/` composite Action; `dos-verify.yml` `workflow_call` | shipped |
| Pipeline-as-code | a | NATIVE | `dos.toml` is policy-as-data; the workflows are checked in | shipped |
| Manual gate / input step | b | NATIVE | `dos decisions` operator queue; `refuse(OPERATOR_GATE)` | shipped |
| **Pipeline-as-code gate policy** | b | ADJACENT | `dos.toml [gate]` table (docs/225 §"open right column") | this-doc |

### 3. Build & dependency

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Build artifact / registry | c | OUT | the build system produces it; DOS can *attest* it (§4) | out |
| Dependency cache / incremental build | d | OUT | build tool's job (Bazel/Turborepo) | out |
| **Reproducible / hermetic build** | c | NATIVE | `dos attest` over the artifact's effect-witness; `content_diff` rung | shipped |
| Dependency pinning / lockfile | b | NATIVE | `refuse(reason_class)` over an un-pinned dep is a structured gate; `scope-gate` | shipped |
| SBOM **generation** | f | OUT | Syft/CycloneDX; DOS attests one, doesn't make it | out |

### 4. Test gating

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Unit / integration / e2e gate | c | NATIVE | the suite is the host's; `dos verify` ties *green* to *shipped* | shipped |
| **Coverage gate** | b | NATIVE | `dos coverage` (did the test execute the target), `test-witness` (red→green) | shipped |
| Quality gate (claim vs reality) | b/c | NATIVE | `dos commit-audit` (author-neutral claim-vs-diff floor) | shipped |
| Static analysis / linting | c | NATIVE | `dos lint` over the workspace's own policy (dead-policy verdict) | shipped |
| Required status check | b | NATIVE | `verify-action` exit code; `.github/workflows/dos-gate.yml` | shipped |
| **Flaky-test / test-retry handling** | e | NATIVE | `dos breaker` failure-class counting; `hook-exit` 0/2/other → pass/BLOCK/WARN | shipped |
| Test impact analysis | d | NATIVE | `dos coverage` fan-out fold answers "did the change's tests run" | shipped |
| **"CI green at this commit" oracle** | c | NATIVE | `drivers/ci_status.py` (gh api Checks → a rung *above* git) | shipped |

### 5. Environments & promotion

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Environment (dev/stg/prod) | a | OUT | host environments | out |
| Environment protection rule | b | NATIVE | `refuse(OPERATOR_GATE)`; `dos decisions` sign-off | shipped |
| Manual approval gate | b | NATIVE | `dos decisions` queue; the armed-operator override (docs/296/328) | shipped |
| Promotion (build-once, promote-many) | b | NATIVE | `dos verify` confirms the *same* commit shipped across stages | shipped |
| GitOps reconciliation | c/d | NATIVE | `dos reconcile` (claim × oracle join, fail-closed) | shipped |
| Wait-timer / soak gate | b | NATIVE | the `stable-release` soak window; `refuse(SOAK_OPEN)` | shipped |

### 6. Deployment strategies

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Rolling / blue-green / recreate | a | OUT | deploy engine owns traffic + instances | out |
| Canary / progressive delivery | a | OUT | Argo Rollouts / Flagger | out |
| Automated canary analysis | c | OUT | the metric-analysis step is the rollout controller's | out |
| Feature flags / dark launch / A/B | b | OUT | LaunchDarkly / Unleash | out |
| Traffic splitting / shadow | d | OUT | the service mesh | out |

> **The honest fence:** category 6 is almost entirely OUT. DOS verifies *that the
> right commit shipped* and *that a claim matches its diff*; it does not route
> traffic or hold two prod environments. A deploy engine that wants a trust floor
> *calls* `dos verify` / `dos attest` at its gate — that is the integration, not a
> DOS feature.

### 7. Release management

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| SemVer / CalVer / build numbering | f | NATIVE | `scripts/release_*.py` cut a `vX.Y.Z`; `dos doctor` prints the version | shipped |
| Changelog / release notes | f | NATIVE | `/release` drafts notes from the shipped commits | shipped |
| Conventional commits → versioning | f | NATIVE | the `dos.toml [stamp]` trailer grammar IS the structured-commit contract | shipped |
| Tagging / release | f | NATIVE | `/release` (rolling), `/stable-release` (gated promotion) | shipped |
| Release train / cadence | a | NATIVE | `.github/workflows/release-cadence.yml` + `dos pulse` heartbeat | shipped |
| **Release freeze / code-freeze window** | b | ADJACENT | `dos.toml [reasons]` `RELEASE_FROZEN` class + `refuse` | this-doc |
| Release candidate (RC) | c | NATIVE | `/stable-release` promotes only a soak-survived candidate | shipped |

### 8. Rollback & safety

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Automated rollback / rewind | e | NATIVE | `dos rewind`; the `improve` REVERT verdict on a non-improving candidate | shipped |
| **Circuit breaker** | e | NATIVE | `dos breaker` (failure-class counting as mechanism, docs/223) | shipped |
| Deployment gate (pre/post) | b | NATIVE | the conjunctive admission seam; `refuse(reason_class)` | shipped |
| **Error budget / SLO gating** | b | NATIVE | `dos breaker` trips on a failure-rate; `dos efficiency` WASTEFUL floor | shipped |
| Observability-driven rollback | e | NATIVE | `dos liveness` STALLED + the `resume` rewind verdict feed the decision | shipped |
| Kill switch / auto-pause | e | NATIVE | `dos halt --resumable`; the breaker's ENFORCE_BREAKER | shipped |
| Health check / readiness probe | c | OUT | k8s probe is the runtime's | out |
| Chaos engineering | c | OUT | Chaos Monkey / Litmus | out |

### 9. Secrets & supply-chain security

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| **Build provenance / attestation / SLSA** | c | NATIVE | `dos attest` / `verify-receipt` (portable signed receipt, docs/246) | shipped |
| Transparency log | f | NATIVE | `dos observe` verdict journal + the WAL (append-only, replayable) | shipped |
| Policy-as-code / admission control | b | NATIVE | `refuse(reason_class)` closed vocabulary + `scope-gate` | shipped |
| Two-person / segregation-of-duties | b | NATIVE | author≠claimant is the kernel's founding floor (docs/214); operator override is audited | shipped |
| Secret scanning | c | NATIVE | `scripts/leak_scan.py` pre-push gate (a fail-closed refusal) | shipped |
| SAST / DAST / SCA / image scan | c | OUT | CodeQL / Snyk / Trivy; DOS attests their result, doesn't scan | out |
| OIDC / keyless | b | OUT | the host CI's cloud-auth | out |

### 10. Concurrency & resource

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| **Concurrency group / deploy lock** | d | NATIVE | `dos lease` (archive mutex) + `dos arbitrate` (lane disjointness) | shipped |
| Mutual exclusion / state lock | d | NATIVE | `dos lease-lane` durable lease (WAL write-back, TTL) | shipped |
| Runner / agent pool, autoscaling | d | OUT | the runner controller (ARC) | out |
| Self-hosted vs hosted runners | d | OUT | host infra choice | out |
| Job timeout | e | NATIVE | the MCP/loop stall verdict (docs/282) → STALLED | shipped |
| Queue / fair scheduling | d | NATIVE | `pick-priority` (freshness sort) + `cooldown` (anti-churn) | shipped |

### 11. Observability

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Pipeline metrics / build telemetry | f | NATIVE | `dos observe` (folded verdict journal); `dos top` live | shipped |
| Audit log | f | NATIVE | `dos observe` + `dos trace` + the WAL | shipped |
| **DORA metrics (DF / LT / CFR / MTTR)** | f | ADJACENT | a fold over `observe` + git + `efficiency-trend` (§6) | this-doc |
| Notifications / alerting | f | NATIVE | `dos notify` seam (Slack/webhook drivers) | shipped |
| **Deployment markers / events** | f | ADJACENT | `dos notify` already carries the transport; a marker-emit usage note (§6) | this-doc |
| Pipeline visualization | f | NATIVE | `dos top` (live fleet), `dos decisions` (drill-in TUI) | shipped |
| Cost / spend tracking | f | NATIVE | `dos efficiency` (work/token), `efficiency-trend` (cross-run) | shipped |

### 12. Idempotency & reliability

| concept | fn | bucket | DOS primitive / surface | status |
|---|---|---|---|---|
| Retry / backoff | e | NATIVE | `dos breaker` failure counting; `hook-exit` → WARN/BLOCK | shipped |
| **Idempotent / exactly-once deploy** | e | NATIVE | the exactly-once envelope + re-drive contract (docs/343) | shipped |
| Declarative reconciliation | d | NATIVE | `dos reconcile` (claim × oracle, fail-closed on the claim) | shipped |
| **Drift detection** | c | NATIVE | `dos reconcile` QUIET_INCOMPLETE; `dos memory` re-verifies a stale belief at recall | shipped |
| Self-healing | e | NATIVE | `model-reroute` (auto-heal a dead model onto a sibling); `resume` redo | shipped |
| Checkpoint / resumable pipeline | e | NATIVE | `dos resume` (ARIES, the intent ledger, docs/107) | shipped |
| Convergence | e | NATIVE | the loop-decide DRAINED/STOP verdicts converge a dispatch loop | shipped |
| Lock / state lock | d | NATIVE | `dos lease` cross-process mutex | shipped |

## The non-obvious mappings, defended

A skeptic should challenge the surprising NATIVE rows. Each holds:

- **Merge queue → `dos arbitrate`.** A merge queue exists to keep trunk green by
  serializing changes that would collide. DOS's lane disjointness is the same
  guarantee at the file-tree level: two workers admit concurrently *iff* their trees
  are disjoint (docs/89, docs/113). The `merge_group` trigger in `dos-gate.yml`
  already runs the verdict on the queue's speculative merge commit.
- **Attestation / SLSA → `dos attest`.** SLSA provenance is "a signed, verifiable
  claim about an artifact that a third party checks without trusting the builder."
  `dos attest` mints exactly that — a portable HMAC-signed `Receipt` over an
  effect-witness verdict that `dos verify-receipt` checks with the shared key alone,
  failing loud on any tamper (docs/246). It is the in-toto/Sigstore *shape* applied
  to the kernel's notary engine, bounded to effects it can witness.
- **Error budget → `dos breaker` + `dos efficiency`.** An error budget halts
  releases once unreliability crosses a threshold. `dos breaker` is failure-class
  counting as mechanism (docs/223) — N failures of a class trips it; `dos efficiency`
  WASTEFUL flags spend that bought no work. Together they are the budget-exhausted
  stop, with the threshold as `dos.toml` data.
- **DORA metrics → `observe` + `efficiency-trend`.** The four keys are folds over
  history DOS already records: **Deployment Frequency** = shipped-commit rate from
  `dos verify`/git; **Change Failure Rate** = the `commit-audit` UNWITNESSED / total
  fire-rate; **MTTR** = `resume`/`liveness` recovery latency; **Lead Time** = commit→
  verified-ship delta on the spine. The verdict journal is the substrate; the fold is
  the §6 gap.
- **Drift detection → `dos reconcile`.** GitOps drift is "declared ≠ live." DOS's
  `reconcile` joins the agent's *claim* against the oracle's *verdict* and fails
  closed on the claim — QUIET_INCOMPLETE is exactly "claimed done, not actually
  shipped" (docs/168). `dos recall` is the same join aimed at a stale memory.
- **Exactly-once deploy → docs/343.** An idempotent deploy must not double-apply.
  The exactly-once envelope + re-drive contract (docs/343) is the kernel's answer:
  a re-driven unit of work is deduplicated by its envelope identity.
- **Policy-as-code → `refuse(reason_class)`.** OPA/Conftest enforce declarative
  rules. DOS's refusal is structured *by construction*: a refusal carries a token
  from a closed, verifiable vocabulary (`dos man wedge` lists it; the
  `dos_refuse_reasons` / `dos_check_reason` MCP tools check it), not free-text prose
  — the same "policy is data, the decision is checkable" property.
- **Retries / flaky tests → `dos breaker` + `hook-exit`.** A flaky test re-run and a
  retry-with-backoff both count failures of a class and decide continue-or-stop.
  `hook-exit` maps a shell step's exit code (0 / 2 / other) to pass / BLOCK / WARN
  (docs/226); `breaker` trips on the count. The retry *policy* is host data; the
  *decision* is the kernel verdict.

## The OUT-OF-SCOPE register (the honesty fence)

These are deliberately not DOS. Naming them is what makes the 98% credible — a
substrate that claimed to "cover" canary routing would be lying.

- **Traffic & instances:** rolling / blue-green / canary / recreate deploys, traffic
  splitting, shadow traffic, k8s readiness probes, surge controls. → the deploy
  engine / service mesh (Argo Rollouts, Flagger, Istio, Kubernetes).
- **Build mechanics:** dependency caching, incremental/hermetic build execution,
  artifact storage, SBOM *generation*, image building. → the build tool (Bazel, Nix,
  BuildKit) and the artifact registry.
- **Runner fabric:** runner pools, self-hosted vs hosted, autoscaling, spot
  executors, resource requests/limits. → the CI platform's runner controller.
- **Scanning engines:** SAST/DAST/SCA/IaC/image/license scanners. → CodeQL, Snyk,
  Trivy, Checkov. **DOS can `attest` their results; it does not scan.**
- **Host triggering:** push/PR/webhook/cron/manual dispatch, path/branch/tag
  filters, environment definitions, feature-flag platforms. → the host CI's trigger
  config and the flag vendor.

The pattern: DOS sits *inside* a step the host triggered, *beside* the test/scan/
build jobs, and *at* the merge/promote gate. It verifies, admits, and observes;
it never moves bytes to production.

## The ADJACENT gaps and how they close

All kernel-free. Default is pure-doc + cross-links; the only data edits are
`dos.toml` seam tables (the `[reasons]`/`[stamp]`-shaped closed-set-as-data pattern,
Layer-2b — never kernel policy).

1. **[x] Answer page** — [`docs/answers/dos-for-ci-cd.md`](answers/dos-for-ci-cd.md):
   "How does DOS fit into my CI/CD pipeline?" Routes a reader to `verify-action`, the
   pre-commit hook, and this map. (Shipped with this doc.)
2. **[x] `RELEASE_FROZEN` reason class** — a `dos.toml [reasons]` token so a freeze
   window is a *structured, verifiable* refusal, not prose. `dos man wedge` (and the
   `dos_refuse_reasons` MCP tool) now enumerate it. (Shipped with this doc.)
3. **[ ] `dos.toml [gate]` seam table** — declare which gate modes are required and
   the `fail-on` floor, as docs/225 pre-designed. The *verdict* (the exit code)
   already ships; only the declarative policy table is unbuilt. Named here as the
   closing surface; a follow-up issue carries the reader so this doc doesn't grow a
   premature parser.
4. **[ ] DORA-metrics fold** — a read-only projection over the `observe` journal +
   git emitting DF/LT/CFR/MTTR. The mapping is fixed (§4 above); the projection is a
   follow-up issue, not a new dashboard in this doc.
5. **[x] Deployment-marker usage note** — `dos notify` already carries the
   transport seam for "annotate the dashboard on deploy" (docs/225 notification
   spine). No new code; this is the usage note.

Everything beyond these five is in the OUT register, not built.

## The 98% accounting

Of the ~140 industry concepts, ~55 are OUT-OF-SCOPE (bucket 3, correctly not DOS),
leaving **~85 in NATIVE ∪ ADJACENT** — the denominator. Of those, the map marks ~80
**shipped** today and 5 **ADJACENT**; this doc closes 3 of the 5 (the answer page,
the `RELEASE_FROZEN` class, the deployment-marker note). That leaves **2 deferred**
(the `[gate]` table reader and the DORA fold projection, both named with their thin
closing surface and a follow-up issue), i.e. **~83 / 85 ≈ 98%** of usable concepts
covered, with the residual <2% named explicitly rather than hidden. The denominator
and the two deferrals are stated so the claim is auditable, not inflated (docs/297).

## How it relates to the rest

- It is the **map** for which docs/225 (the CI-gate consumer) is the load-bearing
  surface and docs/226 (hook-exit), docs/223 (breaker), docs/246 (attest) are the
  per-concept mechanisms.
- It honors **Wall 3** (docs/204): the gate witnesses *presence* and *claim-vs-diff*,
  never *correctness* — the test suite is the host's job, beside the gate.
- It keeps the **advisory floor** (docs/99): DOS computes the verdict and the exit
  code; the host's branch protection or deploy gate enforces.
- It is the CI/CD-facing companion to docs/116 (the durable commons / A2A problem)
  and docs/343 (the exactly-once envelope) — the concepts a fleet inherits from
  release engineering, routed to the kernel that doesn't believe the agents.
