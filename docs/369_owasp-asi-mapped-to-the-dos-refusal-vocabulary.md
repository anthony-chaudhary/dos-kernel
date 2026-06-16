# 369 — OWASP ASI mapped to the DOS refusal vocabulary

> **Status:** map + design. Lays the OWASP Top 10 for Agentic Applications
> (the ASI Top 10) beside the DOS kernel's verdict surface, one threat class at a
> time, naming the verdict that covers it — or the honest gap. Lineage:
> [docs/125](125_the-trifecta-color-and-the-capability-conjunction.md) (the lethal
> trifecta as a capability conjunction),
> [docs/126](126_the-mediated-write-and-the-apply-gate-pep.md) (the mediated write),
> [docs/248](248_instruction-provenance-rejecting-the-injected-directive.md)
> (instruction provenance), and the shipped
> [`src/dos/call_shape.py`](../src/dos/call_shape.py) (the ASI02 verdict).
>
> **One line.** DOS's *closed refusal vocabulary is a threat taxonomy* — each ASI
> class maps to a typed, verifiable kernel verdict, or to an honest "out of scope,
> and here is why."

## 0. Why this map exists

The thing that makes DOS a *security* substrate, not just an orchestrator, is the
shape of its "no." A DOS refusal is not free-text prose — it is one token from a
**closed set**, and every token is at once **emittable** (a producer can stamp it),
**verifiable** (an oracle can check the condition it names), and **refusable** (the
loop routes it to a replan). [docs/166](166_emerging-fleet-governance-benchmarks-and-the-specbench-probe.md)
§3 made the claim in passing: *the DOS refusal vocabulary reads as a threat
taxonomy*. This doc makes the claim concrete by laying the vocabulary beside the
field's reference taxonomy and checking each cell.

The point is **not** to claim DOS "solves" the OWASP Top 10. It is to be precise
about which threats a deterministic admission-and-attestation kernel can actually
witness, which it can only partially throttle, and which are out of its scope by
construction — so a host wiring DOS knows exactly what it is and is not getting.

A note on the taxonomy itself: the OWASP GenAI Security Project released the **Top
10 for Agentic Applications (2026)** in December 2025, built from 100+ contributors.
Vendor paraphrases of the list disagree on a few titles (some swap the ASI02/ASI03
order of "prompt injection" and "tool misuse"). This doc uses the `ASInn:2026`
identifiers and titles as the shipped `call_shape.py` and docs/166/248 already cite
them (ASI02 = Tool Misuse & Exploitation). Treat the official OWASP PDF as
canonical; the per-class *mapping* below is robust to the title shuffle because it
keys on the threat, not the number.

## 1. The DOS verdict surface (what we are mapping FROM)

Two layers carry the security weight.

**The closed refusal vocabulary** (`dos.reasons.BASE_REASONS`, 13 tokens) — the
admission-and-picker "no"s. The security-load-bearing ones:

| Reason token | Category | What it refuses |
|---|---|---|
| `SELF_MODIFY` | OPERATOR_GATE | a call whose write touches the kernel's own running code |
| `FORBIDDEN_CALL_SHAPE` | OPERATOR_GATE | a call whose arguments match a host-declared forbidden shape (command/arg/path) |
| `NO_FREE_REGION` | TRUE_DRAIN | a lease that would overlap a live lease's file tree (concurrency safety) |
| `LANE_BLOCKED_ON_OPERATOR_DECISION` | OPERATOR_GATE | a pick gated on a human decision in the queue |
| `LANE_LEASE_HELD_BY_LIVE_DISPATCH_LOOP` | TRUE_DRAIN | a lane another live loop already holds |
| `SCHEMA_UNREADABLE` | STALE_CLAIM | input the kernel cannot parse → fail-closed, never a silent admit |

**The syscall verdict families** (the rest of the kernel) — not refusals but
verdicts an effect is checked against:

- **truth** — `verify` (did (plan,phase) ship? git ancestry + stamp grammar, never
  self-report) and `commit-audit` (a commit's subject vs its own diff).
- **temporal/economic** — `liveness` (moving vs spinning), `productivity`,
  `efficiency`, `work_account` (env-authored counts, never the agent's claim).
- **admission** — `arbitrate`/`lease` (pure region-disjointness), `refuse` (the
  closed vocabulary above), `spawn`/`reap` (run-ids + the lease WAL).
- **recovery** — `resume` (proposes a continuation; never executes it).
- **loop gates** — `improve` (KEEP only on suite-green + truth-clean + measured
  gain), `breaker` (trips on a run of bad verdicts → escalate to a human).

The single property under all of it: **a verdict reads evidence the claimant did
not author** (git ancestry, OS exit code, the provider's billed token count, the
lease WAL). That is the witness invariant — the one non-forgeable operand every
DOS verdict stands on.

## 2. The map — ASI Top 10 × DOS verdicts

| ASI | Threat (2026) | DOS coverage | The verdict / mechanism |
|---|---|---|---|
| **ASI01** | Agent Goal Hijack — instructions injected into goals/plans | **Partial (provenance)** | [docs/248](248_instruction-provenance-rejecting-the-injected-directive.md): an injected directive is the *least-witnessed* instruction; DOS rejects acting on a goal whose provenance it cannot establish. Admission still gates the *effect* (below), so a hijack that tries to write/exfil hits ASI02/ASI03. |
| **ASI02** | Tool Misuse & Exploitation — unsafe command/arg/path use | **Covered** | `FORBIDDEN_CALL_SHAPE` (`call_shape.py`, shipped): a PreToolUse call whose agent-authored command-prefix / arg-substring / path-glob matches a host-**declared** forbidden shape is refused at the one moment a deny prevents the effect. The lethal-trifecta *egress* leg ([docs/125](125_the-trifecta-color-and-the-capability-conjunction.md)). |
| **ASI03** | Agent Identity & Privilege Abuse — delegated authority misused | **Partial** | The lease WAL carries `run_id` / lineage (`CID_ROOT_ID` / `CID_PARENT_ID`, issue #188): a child's in-lane edit is admitted only when lineage ties it to an ancestor's lease AND the write is contained in that tree; an escape still denies. Identity is *tracked and scoped*, not authenticated — DOS verifies lineage, not credentials. |
| **ASI04** | Agentic Supply Chain Compromise — trusted external tools/schemas | **Out of scope (named, not enforced)** | DOS's own supply chain is hardened ([SECURITY.md](../SECURITY.md): the `dos-kernel` vs squatter `dos` pin), and `SCHEMA_UNREADABLE` fails closed on malformed input. But verifying a *third-party* tool/schema's integrity is the host's PDP — DOS adjudicates the *call*, not the provenance of the binary behind it. |
| **ASI05** | Unexpected Code Execution — agent code runs without validation | **Partial (admission, not sandbox)** | `FORBIDDEN_CALL_SHAPE` can ban an interpreter/exec shape per lane; `SELF_MODIFY` blocks a live loop rewriting its own decision path mid-flight. DOS is the *policy decision point* (refuse the shape) — the *enforcement sandbox* (cgroups, seccomp) is the host. |
| **ASI06** | Memory & Context Poisoning — injected/leaked agent memory | **Partial (recall re-verification)** | A saved memory is a frozen self-report; `dos_recall` re-checks a recalled memory's concrete claims (a SHA, a flag, a path) against git + the working tree NOW, so a poisoned/stale memory is surfaced as RECALL_STALE rather than injected as fact. The poisoning of the underlying store is out of scope. |
| **ASI07** | Insecure Inter-Agent Communication — manipulated messages | **Covered (the fold discipline)** | The witness-routing pattern ([docs/197](197_how-dos-is-directly-useful-to-ultracode.md), the `dos-witness-claim` skill): a worker's actionable CLAIM is never folded as a peer's input on its say-so — the boundary extracts the claim and folds ONLY the independently-witnessed effect. `dos_status` is fail-closed by construction (no `claimed` field). A manipulated message cannot move state it cannot prove. |
| **ASI08** | Cascading Agent Failures — small failures propagate | **Covered (the breaker)** | `breaker` trips on a run of bad verdicts and ESCALATEs to a human instead of letting an auto-recovery loop spin; `queue_saturation` adjudicates whether the human-escalation rung itself is saturated (the silent no-op when decisions outrun a human). The structural answer to a cascade is to *stop adjudicating and surface*, not to keep retrying. |
| **ASI09** | Human-Agent Trust Exploitation — misleading explanations/authority | **Covered (the whole thesis)** | This is what DOS *is*. Every "done" is checked against a witness the agent did not author (`verify` / `commit-audit`), so a confident-but-false "I shipped it" is caught by git ancestry, not believed. "The kernel is the part that doesn't believe the agents." |
| **ASI10** | Rogue Agents — goal drift, collusion, emergent behavior | **Partial (liveness + truth + arbitrate)** | A rogue's *effect* is still gated: `arbitrate` serializes its writes, `verify` catches a false ship, `liveness` flags a run that is spinning not advancing. DOS bounds what a rogue can *do to shared state*; detecting rogue *intent* is out of scope (the effect is the witnessable thing — docs/248 §the-inversion-law). |

## 3. Reading the map — three honest bands

**Covered (4): ASI02, ASI07, ASI08, ASI09.** These are the classes whose threat is
an *effect on shared state* or a *false claim about one* — exactly the witnessable
surface. ASI09 is not just covered; it is the kernel's reason to exist.

**Partial (5): ASI01, ASI03, ASI05, ASI06, ASI10.** For each, DOS gates the
*downstream effect* (a hijacked goal still has to write or exfil to do harm, and
that write hits admission) but does not adjudicate the *upstream cause* (the
injection, the stolen credential, the rogue intent). The honest framing:
**DOS is the throttle on what a compromised agent can DO, not a detector of the
compromise.**

**Out of scope (1): ASI04.** Third-party supply-chain integrity is a different kind
of verification (artifact provenance, signature chains) than DOS's git-ancestry
oracle. DOS hardens its *own* supply chain and fails closed on unparseable input,
but verifying the binary behind a tool call is the host's job.

The load-bearing distinction across all three bands: DOS adjudicates **effects and
claims**, both of which it can read from non-forgeable evidence. It does not
classify **content** ("does this prompt look malicious?") — that is the LLM-judge
rung (a driver, advisory, fail-to-abstain — [docs/86](86_the-typed-verdict-surface.md)),
never a kernel verdict. A taxonomy class whose only signal is content is, by design,
not something the deterministic kernel claims to cover.

## 4. The verb table — what to run

| To address | Run |
|---|---|
| ASI02 (tool misuse) | declare `[call_shape]` in `dos.toml`; the PreToolUse hook refuses a match |
| ASI07 (inter-agent) | route worker claims through the witness fold (`dos-witness-claim` skill; `dos status --children`) |
| ASI08 (cascades) | watch `dos breaker` / `dos queue-saturation`; escalate, don't retry |
| ASI09 (false "done") | `dos verify --workspace . PLAN PHASE`; `dos commit-audit HEAD` |
| ASI03 (privilege) | `dos arbitrate` (lineage-scoped leases); `dos status --children` |
| ASI06 (memory) | `dos_recall` (MCP) before injecting a recalled memory |

## 5. The honest limit

DOS converts the OWASP ASI taxonomy from a checklist of *content-classification
problems* into a smaller set of *effect-admission and claim-verification problems*
— the ones a deterministic kernel can witness without believing anyone. That
re-framing is the contribution. It also bounds the claim: where a threat's only
tell is in the content (a cleverly-worded injection that produces a *legitimate-
looking* tool call), the kernel admits it and the content-judge rung (a driver,
advisory) is the only line — and it abstains rather than guess. That is the correct
failure mode for a trust substrate: **never a false "safe," sometimes an honest
"I can't tell."**

---

> The kernel is the part that doesn't believe the agents.
