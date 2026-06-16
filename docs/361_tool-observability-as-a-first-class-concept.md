# docs/361 — Tool observability as a first-class concept: the per-call spine

> **One line.** DOS already has four byte-clean tool logs that each fold to
> *counts*; what it lacks is a *spine* — one ordered, call-identity-keyed reader
> that joins each tool call to the kernel's decision, its stall state, and its
> spend. The first-class concept is **per-call observability**, and the
> surviving addition is a **read projection (`dos tool-trace`)**, not a new
> verdict.

Status: design. No code shipped by this doc. It records a decision and scopes
the one addition that makes the concept coherent.

**Phase stamp.** The surviving addition — the `dos tool-trace` per-call read
projection scoped here — shipped as `5e5278b` (phase `P1`);
`dos verify --workspace . docs/361 P1` resolves it via this commit's
`(docs/361 P1)` trailer. The doc itself records the decision; `5e5278b` is the
git witness that the spine landed.

---

## 1. The question

"Make tool observability a first-class concept in DOS." The honest first move
was to ask whether it *isn't* one already — and it half is. Four leaves observe
tool calls today, each byte-clean, each load-bearing, each with a reader:

| leaf | log | the one question it answers | grain it keys on |
|---|---|---|---|
| `hook_observation` | `.dos/metrics/observations.jsonl` | "light touch or nanny?" — what share of calls the kernel touched (passed / refused / advised), the docs/297 denominator | `verb` + `run_id` |
| `tool_stream` / `posttool_sensor` | `.dos/streams/<sid>.jsonl` | "is this tool loop stuck?" — did the env return byte-identical results N times (ADVANCING/REPEATING/STALLED) | `session_id` + `step_index` |
| `model_call` | `.dos/metrics/model_calls.jsonl` | "how long did each model call take and cost?" — per-model latency + spend | `model` + `run_id` |
| `verdict_journal` / `observe` | `verdict-journal.jsonl` | "what did each syscall decide?" — the kernel's own verdict stream | `syscall` + `run_id` |

So the substrate is not blind to tool calls. It is **fragmented**: five count
projections (`helped`, `headline`, `census`, `model-calls`, `observe`) over four
disjoint logs, none of which cross-references another except via prose caveats
in their own docstrings.

---

## 2. The idea I tried first — and why it is refuted

The tempting move was a *verdict*: a tool-call analogue of `commit-audit`.
`commit-audit` joins a forgeable claim (the commit subject) to a non-forgeable
witness (the diff git wrote); the in-flight tool stream seemed to have no
equivalent. So: **a tool-result attestation** —
`classify(claimed-effect-of-the-call, env-authored-result-bytes) ->
{CORROBORATED, UNWITNESSED, CONTRADICTED}`.

Three independent adversarial reviews (redundancy, forgeability, host-boundary)
**refuted it on all three lenses, converging on the same survivor.** Recording
why, because the refutation is the substance:

1. **Redundancy.** `effect_witness.witness_effect(claim, readbacks)` already *is*
   this leaf, field-for-field. It joins an agent-narrated `EffectClaim` against
   accountability-capped env/world-authored read-back bytes and returns
   `{CONFIRMED, REFUTED, UNWITNESSED, NO_CLAIM}` — the proposed states renamed,
   plus the honest abstain floor. `attest.py` already mints a portable signed
   `Receipt` over that verdict. The attestation is **double-covered** (the join
   in `effect_witness`, the receipt in `attest`); building it again duplicates a
   kernel leaf.

2. **Forgeability (§5a).** `commit-audit` works because *both* operands are
   non-narrative: the claim is a token from a closed grammar (`fix:`/`feat:`),
   the witness is the diff. The proposed attestation has no structured claim —
   "what the agent narrated it learned" is unconstrained prose. Extracting a
   checkable proposition from it is done either **by the agent** (it authors both
   sides → the §5a satisfaction predicate verbatim) or **by a judge** (a
   Layer-4 JUDGE-rung driver, advisory, fail-to-abstain — which by DOS's own
   layering *cannot* mint a binding `CONTRADICTED`). There is no deterministic
   `classify(evidence, policy)` because one operand can't be obtained without an
   agent or a non-deterministic judge minting it.

3. **Host-boundary.** The two operands are authored at *different hook events on
   different turns*. The env result bytes arrive at PostToolUse
   (`posttool_sensor.step_from_event` reads `tool_response`/`tool_output`); the
   agent's narration about that result is generated in the *next* assistant
   turn, surfacing at a later event whose payload carries no structured link
   back. At the only event where the bytes are in hand, the claim does not yet
   exist; at the event where the claim exists, the bytes are a stale fossil keyed
   by `session_id`. `commit-audit` works precisely because subject + diff are
   co-located in one immutable git object read at one time. The tool stream has
   no such co-located object, so a *synchronous* verdict has nowhere to stand.

**The lesson:** the scattered surfaces were never missing a verdict — the
verdict work is done (four logs + `effect_witness`). They were missing a
**spine**.

---

## 3. The concept, named

**Per-call observability.** Every kernel signal about a *single tool call* —
what the kernel decided (`hook_observation`), whether the result stalled
(`tool_stream`), what the model spend was (`model_call`), and whether its
claimed effect was later witnessed (`effect_witness`, when a verdict was
persisted) — folded into **one ordered row per tool call**, joined by a single
call identity.

The concept becomes first-class when "tool observability" is a *thing you read*
(`dos tool-trace --run R` → an ordered, one-row-per-call replay) instead of five
count projections you must know how to assemble in your head.

The operator question that has **no verb today**, stated plainly:

> "What tool calls happened in run R, in order, and what did DOS do about
> each one — pass, warn, or deny; stalled or advancing; and what did it cost?"

`observe --run R` shows the kernel's *verdict events* for the run but not the
tool calls they were about. `helped` shows only the *enforced* subset
(BLOCK/WARN/DEFER), never the passthroughs, with no per-call ordering. The one
log that captures the actual ordered tool stream — the `posttool_sensor`
per-session accumulator — has a **writer but no operator-facing `dos` reader**.
Every caller of `read_stream` is a *sensor re-replaying it to compute its own
verdict* — the PostToolUse hook command in `cli.py` and `pretool_sensor` both
re-fold the stream for their stall/marker logic — never a projection that shows
the stream *to a human*. There is no `tool-trace` / `tool_trace` verb anywhere in
the tree.

---

## 4. The minimal addition

A **Layer-3 helper projection**, `src/dos/tool_trace.py` + a CLI handler, in the
exact projection-pair pattern of `observe`/`verdict_journal`. **Not** a new
Layer-1 verdict leaf — there is nothing left to classify; the verdicts already
exist. A read-only projection that gives the concept a name is the legitimate
answer (the `observe`/`decisions`/`trace` precedent: read the journals, mint no
new belief).

**Pure fold, all I/O at the CLI boundary:**

```python
def correlate_calls(
    observations: list[dict],     # hook_observation.read_observations(...)
    stream_records: list[dict],   # the RAW posttool stream records (see note below)
    model_calls: list[dict],      # model_call.read_model_calls(...)
    effect_verdicts: list[dict],  # optional — only when a host persisted them
    *, run_id: str | None, session_id: str | None,
) -> ToolTrace:                   # records in, rows out, no disk
```

> **Persistence nuance the implementer must know (verified against source).**
> The join keys `run_id` / `step_index` are written onto the *persisted* stream
> record by `posttool_sensor._step_entry` (additive optional fields, present only
> when the caller supplied them). But they are **not** fields on the in-memory
> `StreamStep` dataclass (`tool_name` / `args_digest` / `result_digest` only), and
> `read_stream` reconstructs bare `StreamStep`s that *drop* them. So the fold must
> read the **raw records** (a thin `read_stream_records` boundary helper, or
> widen `read_stream`'s return), not call the existing `read_stream` — otherwise
> the spine has nothing to join on. This is a read-side helper, not a schema bump:
> the fields are already on disk.

`ToolTrace` = an ordered tuple of
`CallRow{step_index, ts, tool_name, args_digest, result_digest, outcome, exit,
latency_ms, rung, reason_class, stall_state, model_spend?, effect_verdict?}`
plus a `JoinReport{joined, partial, orphan, join_grain}`.

**Typed states are *join-completeness*, not a satisfaction verdict — this is what
keeps it honest:**

- `JOINED` — the call appears in ≥2 logs and they reconcile on the shared key.
- `PARTIAL` — present in one log only (an observation with no matching step).
- `ORPHAN` — a record whose key matches no call in any other log. **Surfaced,
  never dropped** — the operator needs to see the seam, not have it hidden.

There is deliberately **no** `CORROBORATED`/`CONTRADICTED`. A row *displays* an
`effect_verdict` when `effect_witness` already produced one; `tool_trace` never
derives it. (The redundancy lens's rule: consume `witness_effect`, never
re-derive it.)

### Why it is byte-clean (the doctrine, satisfied)

The projection adjudicates nothing; it folds env/runtime-authored records each
written downstream of an already-decided verdict:

- `outcome`/`exit`/`latency_ms`/`rung`/`reason_class` — hook-authored.
- `result_digest` — env-authored (the gym/MCP server produced the bytes).
- `model`/tokens/`duration_ms` — provider-authored (the API billed them).
- `effect_verdict` (when present) — `effect_witness`'s already-byte-clean output.

The two agent-authored fields it carries — `tool_name`, `args_digest` — are the
docs/145 provenance split made visible: **displayed as proposals, never
adjudicated on.** No agent prose enters any count.

### The like-for-like rule, honored

It must **not** compute a single blended rate across logs (docs/297: windows and
scopes differ). When two records can't be joined, it emits a `PARTIAL`/`ORPHAN`
row with per-log provenance, never a silent merge. The join is a *correlation*
explicitly labeled by `join_grain`, not a unification that papers over the four
scopes.

### The join key

Use the docs/179 fields **already on the persisted stream record** (`run_id` +
`step_index` — see the persistence nuance above) as the spine; reconcile
`hook_observation` on `run_id` + a `ts`-window, `model_call` on `run_id` + a
`ts`-window. **No new write-side field in v1** — the join fields are already on
disk; the only read-side work is a helper that surfaces them past `read_stream`'s
bare `StreamStep` reconstruction. If reconciliation proves lossy, the minimal
follow-up is a one-field additive change: stamp the same `step_index` into the
`hook_observation` record at PostToolUse — deferred until the read surface proves
it's needed.

---

## 5. What this explicitly does NOT build (scope discipline)

- **No narration-vs-result attestation verdict.** Refuted on all three lenses;
  `effect_witness`/`attest` own it; the narration operand can't be fixed without
  an agent or a judge, and that smuggles the §5a satisfaction predicate into the
  kernel.
- **No synchronous in-flight gate.** Operands live at different hook events on
  different turns. `tool_trace` is a **post-hoc fold over persisted fossils**,
  advisory-only, never an in-turn block (docs/99 / PDP-not-PEP).
- **No new Layer-1 verdict leaf.** The verdict work is done; a classifier here
  would have nothing to classify.
- **No cross-log unified rate.** Forbidden by docs/297 like-for-like; report
  per-log counts + the join, never a blended numerator.
- **No new write-side schema in v1.** Reuse the docs/179 `step_index`/`run_id`.
- **No structured pre-call expected-effect token** (the unforgeable lens's
  alternative: a `breaker`/`hook_exit`-shaped pre-call assertion classified
  against the result). That is a different, defensible thesis — its own plan,
  out of scope here.

---

## 6. The smallest falsifiable proof

A single fold test that fails today (no reader exists) and passes once the
projection lands:

> **Given** a synthetic session with three persisted records keyed to one call —
> a `hook_observation{run_id=R, ts=T, outcome=warn, rung=stall}`, a
> `StreamStep{run_id=R, step_index=2, result_digest=D, verdict_state=REPEATING}`,
> and a `model_call{run_id=R, ts≈T, duration_ms=1200, output=…}` —
> `correlate_calls(...)` returns **exactly one `CallRow` with `state=JOINED`**
> carrying `outcome=warn`, `stall_state=REPEATING`, and the model spend on the
> *same row*; **a second call present only in the observation log** yields a
> `PARTIAL` row; **a stray `model_call` with an unmatched `run_id`** yields an
> `ORPHAN` — never silently dropped.

The concept-is-named corollary: `dos tool-trace --run R` prints the ordered
one-row-per-call replay where each row shows decision + stall + spend together.

The honesty falsifier: if any log can't be reconciled to the call grain by
`run_id`+`step_index`+`ts`, the surface reports `ORPHAN`/`PARTIAL` rather than a
wrong `JOINED` — proving it is honest about the seam instead of fabricating a
join.

---

## 7. The crux, in one paragraph

Tool observability was never missing a *verdict* — DOS has four byte-clean tool
logs and `effect_witness`/`attest` already adjudicate the claim-vs-witness join.
What it was missing is a *spine*: a single call-identity-keyed reader that turns
five count projections into one ordered, per-call view. Three adversaries
independently killed the obvious attestation leaf and independently landed on the
same survivor. The smallest thing that makes "tool observability" a named,
coherent concept is **a read, not a write** — `dos tool-trace`, a Layer-3
projection that consumes the verdicts that already exist and is honest about
every seam it cannot join.

---

### Appendix — relevant files (for the implementer)

- `src/dos/observe.py`, `src/dos/verdict_journal.py` — the projection-pair pattern to mirror.
- `src/dos/hook_observation.py` — the observation log + `read_observations` + the denominator rule (docs/297).
- `src/dos/posttool_sensor.py` — the per-session accumulator + `read_stream` (the unread log); `tool_stream.py` for the `StreamStep`/`StreamState` shape.
- `src/dos/model_call.py` — `read_model_calls` + the spend re-hydration.
- `src/dos/effect_witness.py`, `src/dos/attest.py` — the verdict to *consume*, never re-derive.
- `src/dos/cli.py` — handler + parser registration site (the `observe`/`helped`/`model-calls` cluster ~6700–6857; `cmd_hook_posttool` ~5145, the sole non-sensor `read_stream` caller at ~5247).
