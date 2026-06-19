---
name: dos-witness-claim
description: "Route subagent claims through independent read-back before another agent relies on them. Use at parallel, pipeline, or synthesis barriers where shipped phases, files, rows, messages, or other effects must be witnessed."
---

# dos-witness-claim — fold the witnessed effect, never the narrated one

> **This is the screenplay for the one move the whole substrate exists to make:**
> when a subagent hands you a result, the result is a *claim*, not a fact. A claim
> re-narrating the agent's own work is **consistency, not grounding** (docs/138).
> Belief is earned only by a read-back whose **byte-author is not the judged
> agent** (a fresh GET, a git existence check, an OS exit code, a state diff). This
> skill never decides ground truth itself — it shells `dos` verbs and reads the
> verdict. The kernel decides; the skill narrates.

The shape is domain-free: **discover the layout → classify the claim type →
witness it on a non-forgeable rung → fold ONLY confirmed.** The *policy* (which
lanes, which plan grammar, where state lives) is data the screenplay reads from
`dos doctor --json`, never literals it hardcodes.

**The seed-2 payoff.** This makes the worker's MODEL TIER irrelevant to trust. A
weak worker and a strong worker face the SAME witness gate: both have their
claimed effect re-read from a surface they did not author. A confident, fluent,
frontier-strength narration of a success the world does not corroborate is exactly
the silent fail this gate catches (docs/177) — and a weak worker that actually
shipped the effect passes it. Distrust is aimed at the *byte-author of the
evidence*, not at the worker's eloquence.

## Inputs

- A set of subagent **results** to fold (return strings + their transcript paths),
  e.g. the children of a `parallel()` barrier or a `pipeline()` stage. A result is
  the thing you are tempted to interpolate directly (`${result}`) — don't.
- For each result, the **effect it claims** (a `(plan, phase)`, a created file
  path, a DB row key, a sent-message id). If the worker emitted no checkable
  effect, that is a real outcome (NO_CLAIM), not a pass — see Step 2.

## Step 0 — Discover the workspace layout (one call)

Run the doctor verb and read the result. **This is the WCR on-ramp: every
path/lane/exit-code below comes from here, never a literal.**

```bash
dos doctor --workspace . --json
```

Parse the JSON object. The fields this skill uses:

- `exit_codes.verify` — `{shipped, not_shipped, contract_error}`. Branch on these,
  never on parsing the prose, for the **git-phase** claim type (Step 3a).
- `exit_codes."verify-result"` — `{healthy, unreadable, dead, contract_error}`
  (`dead=3`). Branch on these for the **terminal-state** witness (Step 3, the
  prerequisite gate every claim type runs first).
- `paths.plans_glob` / `lanes` / `stamp` — the host's plan grammar and lane
  taxonomy, if a claim names a `(plan, phase)`.
- `git` — if `false`, the git-existence witness (Step 3a) has no history to read;
  every git-phase claim will be `source="none"`. Say so; do not silently pass it.

You may also read `admission_predicates` and `overlap_policy` here, but they are
not load-bearing for witnessing — they describe the arbiter, not the witness rung.

## Step 1 — For each result: is the worker even ALIVE? (the terminal-state gate)

**Run this BEFORE you read the worker's return string at all.** A return string is
worthless if the "worker" was a harness-synthesized death (a rate-limit / quota /
auth / server error the harness wrote and the worker never authored). ~32% of real
subagents return such a string (docs/197 §2), and it survives a naive
`.filter(Boolean)` to be banked as a finished finding.

```bash
dos verify-result --workspace . --transcript <agent-transcript.jsonl>
echo "exit=$?"
```

Branch on `exit_codes."verify-result"`:

- `3` **DEAD** — the terminal record was harness-authored (`message.model ==
  "<synthetic>"`, the unforgeable authorship marker). **Do NOT fold this result.**
  Route the worker's OWN unit for re-dispatch, and **count it in the denominator**
  (a 4-of-7 fan-out is 4/7, never silently 7/7). The catch is grounding because it
  read a DIFFERENT byte-author than the worker — the harness, not the model.
- `0` **HEALTHY** — a real terminal result; proceed to Step 2. (`0` also covers
  **UNREADABLE**, the fail-safe floor: a read fault never fabricates a death. An
  UNREADABLE result is *not* trusted as confirmed — it simply isn't classified
  DEAD; its effect still must be witnessed in Step 3.)
- `2` — a contract error (no transcript). Fix the wiring; do not treat as HEALTHY.

This step is itself the cheap, shipped form of "byte-author ≠ judged agent": the
liveness of the worker is read from the harness's own authorship marker.

## Step 2 — Classify the claim TYPE (so you know which witness to ask)

For each HEALTHY result, decide what checkable effect it claims. **Extract the
claim at the boundary — abstain, never invent.** Free prose ("I'm done", "shipped
the auth work") yields NO claim: there is no honest way to derive the effect's
*identifier* from prose without inventing it (docs/134 §2.1). A claim you cannot
name is a **NO_CLAIM** — surface it for a human, do not manufacture one to witness.

Map each named claim to its witness rung:

| Claim type | What the worker asserts | Witness (byte-author ≠ agent) | Verb today |
|---|---|---|---|
| **git phase** | "(plan, phase) shipped" | git ancestry + ship-stamp grammar | `dos verify` (Step 3a) — SHIPPED |
| **terminal state** | the result itself is real | harness authorship marker | `dos verify-result` (Step 1) — SHIPPED |
| **created file / DB row / sent message / deploy** | "I created X / inserted row Y / sent Z" | a fresh GET / state-diff / OS exit / counterparty record | **NO CLI verb** (Step 3b) — Python API gap |

The first two are shipped CLI verbs. The third is the open seam — handled honestly
in Step 3b.

## Step 3 — Witness the claim on a non-forgeable rung

### Step 3a — git-phase claims: ask the truth syscall

For a `(plan, phase)` claim, never trust the worker's "I committed it" and never
grep commit subjects yourself:

```bash
dos verify --workspace . <PLAN> <PHASE> --json
echo "exit=$?"
```

Read the `ShipVerdict`: `{shipped, source, sha?}`. Branch on `exit_codes.verify`
(`shipped:0`, `not_shipped:1`, `contract_error:2`), and **read the rung**:

- `source: "registry"` — the strongest git ship; a ship row exists. Fold it.
- `source: "grep-subject"` — a commit *subject* carried the phase token. SHIPPED,
  but weaker — a subject can flip the verdict even if little was built. Fold it,
  but mark the rung; do not treat it as equal to `registry`.
- `source: "none"` — no positive git evidence. The worker CLAIMED the phase but
  git does not corroborate it. **Do NOT fold the claim** — this is the narrated
  success the world does not back. Surface it; route the unit for re-dispatch.

This is grounding because git is a witness the worker did not author: the commit's
existence in ancestry is a fact about the repository, not about the worker's prose.

### Step 3b — created-file / DB-row / sent-message claims: the read-back gap

**There is NO `dos verify-effect` (or `dos witness`) CLI verb.** The effect-witness
join is shipped in the kernel as a **Python API**, not a command — exactly like
`tool_stream` has no `dos tool-stream` verb (see EXAMPLES.md Recipe 5). So for any
effect outside git, the skill documents the pattern and **logs the gap at
runtime**, rather than pretending a verb exists.

The pattern (kernel modules, all pure; the read-back I/O lives in a `drivers/*`
witness, never in the skill):

```python
from dos.claim_extract import extract_claims          # (a) abstain, never invent
from dos.effect_witness import EffectClaim, witness_effect
from dos.evidence import gather_evidence, resolve_evidence_source  # (b) read-back at the boundary

# (a) Extract the claim from the worker's terminal text (boundary read done already).
#     extract_claims fires only on an explicit ID-shaped token or a DOS-CLAIM:
#     marker — free prose yields []. That [] is a NO_CLAIM, not a pass.

# (b) Gather a read-back from a witness the agent did not author. A real witness
#     (a fresh HTTP GET, an OS exit code, a state-snapshot diff) lives in a driver
#     registered under the `dos.evidence_sources` entry-point group; the skill
#     resolves it by name and runs it at the boundary:
source = resolve_evidence_source("<your-witness-name>")   # built-in `null` if none wired
facts = gather_evidence(source, subject="<effect key>", config=cfg)

# (c) Fold ONLY confirmed: the join is capped by the read-back's accountability.
verdict = witness_effect(EffectClaim(key="<effect key>", narrated="<what worker said>"),
                         (facts,))
# verdict.verdict -> CONFIRMED | REFUTED | UNWITNESSED | NO_CLAIM
# verdict.believe -> True ONLY on CONFIRMED (a non-forgeable witness saw it PRESENT)
```

The load-bearing rule, enforced structurally by `believe_under_floor`: a read-back
on the **forgeable floor** (`AGENT_AUTHORED` — the agent re-reading its own
surface, a pasted receipt, an mtime) is recorded but **structurally incapable** of
granting belief. Belief requires a `OS_RECORDED` / `THIRD_PARTY` witness. So the
worst a lying same-surface read-back can do is be ignored (a safe-direction
no-op), never manufacture a CONFIRMED.

**The four outcomes a consumer routes on:**

- **CONFIRMED** — fold it. A non-forgeable witness re-read the world and the effect
  is PRESENT.
- **REFUTED** — do NOT fold; RED-flag it. A non-forgeable witness re-read and the
  effect is ABSENT (the silent frontier-fail made visible). Route for re-dispatch.
- **UNWITNESSED** — do NOT fold as confirmed. No accountable witness was reached
  (or only a forgeable-floor read). The honest abstain — surface it; this is the
  runtime gap to log (see "What this skill does NOT do").
- **NO_CLAIM** — the worker asserted no checkable effect. Nothing to witness;
  surface it for a human. NOT a pass.

**Log the gap, never silently skip it.** The first time a non-git effect needs
witnessing and **no `dos.evidence_sources` driver is wired** (so `gather_evidence`
returns `null` → UNWITNESSED), emit a one-line `log` naming the unwitnessed effect
and the missing witness — so the capability gap is surfaced at runtime, not buried
here. An UNWITNESSED effect must be reported up, never laundered into the fold.

## Step 4 — Partition the fold and carry coverage forward

Fold the results into three buckets, and **carry the count into whatever consumes
the fold** (a synthesis prompt, a downstream stage):

- **CONFIRMED / SHIPPED** → folded. These are the only results another agent's
  input may be built from.
- **REFUTED / NOT_SHIPPED / DEAD** → routed for re-dispatch, **counted in the
  denominator**. A fan-out of N that confirmed M is M/N — never silently N/N.
- **UNWITNESSED / NO_CLAIM** → surfaced for a human; held out of the fold.

The coverage fact (`confirmed M of declared N`, plus the bucket each result landed
in) is data the synthesizer must SEE — a synthesis told "7/7 returned" when only
4/7 were witnessed will confidently launder the gap.

## What this skill deliberately does NOT do (no silent gap)

- **No model-judge over the return TEXT.** It NEVER asks a model (or a heuristic,
  or itself) "does this return string *look* right / complete / successful?" That
  is the docs/197 §4d wishful trap: a judge reading the worker's own narrated bytes
  is re-deriving the author's output — **consistency, never grounding**. Belief
  comes only from a read-back whose byte-author is not the worker. (A JUDGE rung
  exists — the `dos.judges` seam — but it is advisory, fail-to-abstain, and applies
  to the *residue the oracle ABSTAINED on*, fed independent evidence, never the
  worker's own string as a substitute for the witness.)
- **No `dos verify-effect` verb (there isn't one).** Non-git effects are witnessed
  via the Python API of Step 3b, and the gap is logged at runtime. The skill does
  not pretend a CLI verb exists where it does not (the EXAMPLES.md Recipe 5
  discipline).
- **No host evidence source baked in.** Which witness re-reads a created file or a
  DB row is a `dos.evidence_sources` driver the host wires; the skill resolves it
  by name, never names a host backend as a literal.
- **No enforcement.** It REPORTS a fold partition and proposes a re-dispatch; it
  does not kill a worker or block a write. DOS is a PDP, not a PEP.

## Anti-patterns

- ❌ Interpolating `${result}` (a worker's return string) directly into a synthesis
  prompt — that folds the worker's self-report as ground truth. Witness first.
- ❌ Treating a non-empty return string as success — a harness-synthesized death is
  non-empty and survives `.filter(Boolean)`. Run `dos verify-result` first.
- ❌ Asking a model "is this output complete/correct?" — consistency, not
  grounding. Gather a read-back from a surface the worker did not author.
- ❌ Counting only the workers that returned a string in the denominator — a DEAD or
  UNWITNESSED result must be counted, or a 4/7 fan-out launders as 7/7.
- ❌ Folding an UNWITNESSED / NO_CLAIM result "because it probably worked" — abstain
  surfaces it; it never folds as confirmed.
- ❌ Naming a specific lane, plan dir, or witness backend as a literal — read the
  active layout from `dos doctor --json` and resolve witnesses by name.

## The one rule under this skill

A subagent's result is a **claim with a byte-author**. Fold it only when a witness
whose byte-author is *not the worker* corroborates the claimed effect. The worker's
model tier, its confidence, and its eloquence are irrelevant to that gate — which
is the whole point: the part that decides ground truth is never the part being
judged.
