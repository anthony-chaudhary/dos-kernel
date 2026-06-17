# 314 — verification memory: gate what ENTERS a memory, then meet the memory providers

> docs/103 proved an agent's memory is an unverified agent and shipped the
> RECALL half: `dos memory recall/verify` re-probes a stored claim against
> ground truth before it is believed. This plan ships the other half and the
> integration story around it. Three lifts, in trust order: (1) the **write
> gate** — adjudicate a candidate memory BEFORE it enters the store, so a
> session's over-claim cannot become every future session's inherited belief;
> (2) the **store seam** — recall/admit today read only a directory of
> markdown files, but the memories of real fleets increasingly live behind
> provider APIs (Mem0, Zep, Letta, LangMem), so the store becomes a resolved
> protocol the way judges/notifiers/dialects are; (3) **verification-memory
> fossils** — the kernel remembering its own adjudications (which claims were
> probed, what the witness said) so a STALE verdict is durable, not
> rediscovered every session. Operator prompt 2026-06-12: "integration with
> memory providers and our own memory plan — verification memory is a
> non-trivial thing to expand on."

*Status: P1–P4 SHIPPED 2026-06-12 (P1 the write gate — `dos memory admit`;
P2 the `dos.memory_stores` seam + `--store-kind`; P3 the Mem0 driver behind
`[memory-mem0]`, real-SDK-probed — the hosted round-trip waits on an
operator `MEM0_API_KEY`, issue #99's last box; P4 the verdict fossils +
flap surface, issue #100). P5 designed below (design-only by scope); its
annex shape is now SETTLED against the shipped seam — implementation is a
follow-on handle.*

## 0. Why the write moment is the high ground

The recall gate (docs/103) catches a stale claim at READ time — necessary,
because true claims age into lies. But the nastiest poison is not aged truth;
it is a claim that was **never true**, written by the same self-narrating
session DOS exists to distrust. The hosted memory products make this
structural: their headline feature is AUTO-EXTRACTING memories from the
conversation — the writer believes the agent's narration *by design*. An
agent that says "deployed the fix and all tests pass" mints a durable memory
saying so, and every future session inherits it wearing the authority of a
fact. That is the docs/229 peer-B handoff, at memory scale, on an
industry-default pipeline.

Two asymmetries make write-time the right gate:

- **Evidence is richest at write time.** The working tree, the git delta,
  the run's own fossils are all live; a claim is cheapest to witness the
  moment it is made. At recall, weeks later, the probe must reconstruct.
- **Write-time refusal is cheap; recall-time refusal is lossy.** Refusing a
  poisoned candidate costs one memory that never should have existed.
  Refusing at recall costs whatever the sessions in between already
  inherited.

The gate is TYPING, not censorship (the docs/118 forgeability grading,
applied to memory): only a claim **contradicted by ground truth right now**
is refused. Everything else is admitted — but typed, so recall knows what
authority each memory may wear.

## Phase 1 — the write gate: `dos memory admit`

The recall driver already owns the whole pipeline (extract claims → probe
against ground truth → classify); the write gate is that pipeline pointed at
a CANDIDATE text instead of a stored file, plus a typing verdict:

| Admission | Meaning | Recall analogue |
|---|---|---|
| `ADMIT_WITNESSED` | ≥1 checkable claim, every checkable claim CONFIRMS now — may enter wearing fact authority | `RECALL_FRESH` |
| `ADMIT_AS_CLAIM` | checkable claims present but ≥1 probe abstained; none contradicted — enters as a dated claim, not a fact | (the honest middle) |
| `ADMIT_OPINION` | nothing checkable (preference / positioning) — enters typed opinion; recall will have nothing to re-probe | `RECALL_UNVERIFIABLE` |
| `REJECT_POISON` | ≥1 checkable claim CONTRADICTED by ground truth at write time — refuse: this would mint a lie with memory authority | `RECALL_STALE` |

Mechanics: `gather()` splits into `gather_text()` (pure-ish core on the
candidate bytes) + the existing file reader; `classify_admission` derives the
typing from the same `RecallEvidence`; `dos memory admit --text-file F |
--stdin` is the CLI (exit: POISON 3, everything else 0 — advisory, the host's
memory writer decides what to do with a 3). Provider-agnostic by
construction: ANY memory pipeline — the file store, a Mem0 webhook, a Zep
wrapper — pipes the candidate through the verb before committing it.
Driver-level (`dos.drivers.memory_recall`), zero kernel edits.

**Done:** `dos memory admit` returns the typed verdict on each of the four
fixture classes; pinned in `tests/test_memory_admit.py`.

## Phase 2 — the store seam (`dos.memory_stores`)

**Files:** `docs/CLI.md`, `docs/STABILITY.md`, `src/dos/drivers/memory_recall.py`, `src/dos/memory_stores.py`, `tests/test_memory_stores.py`

`recall_one`/`sweep` hardcode a directory-of-markdown layout. Lift the store
behind the fifth by-name-resolved protocol (the judges/notifiers/dialects/
overlap pattern): a `MemoryStore` protocol (`list() -> ids`, `read(id) ->
(text, meta)`, optionally `annotate(id, banner)`) + a resolver + the ONE
unshadowable built-in `file` store (today's behavior, byte-identical — the
`AbstainJudge` analogue). The recall/admit/sweep/route paths take
`--store-kind NAME [--store ARG]`; default `file` keeps every existing call
byte-identical. The kernel seam holds no vendor name (the litmus); every
provider store is a driver.

**Done:** the protocol + resolver + `file` built-in land with the existing
recall suite green and unchanged; a toy in-memory store in tests proves a
second store resolves by name.

## Phase 3 — provider drivers (Mem0 first) — issue #99

**Files:** `src/dos/drivers/memory_mem0.py`, `tests/test_memory_mem0.py`

A `dos.memory_stores` driver per provider, extras-gated like
`notify_slack`: `[memory-mem0]` first (largest mindshare; its OpenMemory MCP
server makes the demo runnable beside our own `dos-mcp`), then Zep/Letta as
the pattern proves. Each driver maps the provider's record shape into the
seam and gets BOTH halves for free: `dos memory verify --store-kind mem0`
(sweep the hosted store for stale claims) and the admit gate at the
provider's add-memory boundary. The caught-lie demo writes itself: let the
provider auto-extract a memory from an over-claiming conversation, then
`dos memory admit` the same text and watch REJECT_POISON name the
contradiction.

**Done:** recall-verify + admit run against a real Mem0 store in a scratch
venv (the docs/305 real-SDK discipline); the driver names its vendor, the
seam does not.

## Phase 4 — verification-memory fossils — issue #100

**Files:** `docs/CLI.md`, `src/dos/drivers/memory_recall.py`, `tests/test_memory_fossils.py`, `tests/test_memory_stores.py`

The kernel's own memory of its adjudications. Today a `RECALL_STALE` verdict
is computed, printed, and forgotten; next session re-probes from scratch (or
worse, never asks). Two lifts:

- **Journal the verdicts.** `memory recall/verify/admit` append
  schema-tagged rows to the verdict journal (the docs/262 spine), keyed by
  memory name + claim — the same WAL discipline every other verdict gets.
- **Consult before re-probing.** A sweep reads the journal first: a memory
  already adjudicated STALE and unchanged since (size/hash) is reported from
  the fossil, not re-probed (the cooldown analogue); a memory whose verdict
  history flapped (FRESH→STALE→FRESH) is surfaced as suspicious — claim
  history IS evidence.

**Done:** two `dos memory verify` runs in a row probe once; the journal
carries the rows; flap-detection has a fixture.

## Phase 5 — the write-time verification annex (design only)

**Files:** none — design-only phase; ships no deliverable (its commit touched only this plan doc), so the floor abstains here.

At write time the claims are known and the probes just ran — so STAMP the
memory with its own re-verification recipe: a frontmatter annex listing each
extracted claim, its polarity, and the probe that confirmed it (the
`expected_doc`-style disambiguators included). Recall then re-runs the
declared probes instead of re-deriving them from prose — cheaper, and immune
to extraction drift. This is the memory analogue of the docs/312
`verdict.json`: the artifact carries its own checkable surface.

**The shape, settled against the shipped P2 seam (2026-06-12).** The seam's
`read(id)` returns `(text, meta)`, and the two travel differently: every
store round-trips the TEXT verbatim (it is the thing the verdict is computed
from), while `meta` is provider-shaped and lossy (Mem0's `metadata` dict,
the file store's path facts). So the annex rides INSIDE the bytes — a
`verification:` list under the frontmatter `metadata:` block, one row per
claim — never in the store's side-channel meta. That is what survives a
provider round-trip by construction:

```yaml
metadata:
  type: project
  verification:
    schema: 1
    adjudicated: 2026-06-12T00:00:00Z       # when the probes ran
    claims:
      - raw: "from os import path"          # the literal the probe binds
        kind: CODE_TOKEN                     # ClaimKind token
        polarity: ASSERTS_PRESENT            # Polarity token
        target_file: src/app.py              # the probe anchor
        probe: grep                          # which rung answered
        status: CONFIRMS                     # what it said at write time
```

Three rules carry over from the shipped halves: the annex is a RECIPE, not
a verdict cache — recall re-RUNS the declared probes (an annex can never
substitute for ground truth, only aim the probe; the P4 fossil is the
verdict cache, with its own staleness rules); a memory whose body drifted
from its annex (claims extracted from prose that the annex does not name)
is adjudicated on the UNION, worst-wins — an annex can add probes, never
shadow one; and the writer of the annex is the writer of the memory, so the
annex is FORGEABLE and grants no authority — it saves re-derivation, the
probes still read only env-authored bytes (docs/138). Emission handle: a
`dos memory admit --annex` flag rendering the gathered evidence as the YAML
block above. Design-only by scope; implementation is a follow-on issue once
the shape has soaked.

## The throughline tie (docs/313)

A memory store is just another host surface: state the kernel reads but does
not own. The same contract applies — the verdict is computed from evidence
the claimant didn't author; the store (file dir, Mem0, Zep) is resolved at
the boundary; nothing under `src/dos/` names a vendor. And the fossils of P4
live where every other fossil lives: `.dos/`, the substrate's own memory,
traveling with the work.
