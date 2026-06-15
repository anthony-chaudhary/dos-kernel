# 345 — Skill → DOS: convert any skill to be trust-grounded, and the free on-ramp

> **The pitch in one line.** Bring any agent skill; DOS reads it, finds every
> place it trusts a worker's word, and hands back a copy that asks a witness
> instead. Same screenplay, same outputs — minus the silent over-claims.

This plan answers the operator goal: *the ideal way to convert any skill into
DOS support — DOS reads the skill and adds / replaces / leaves-alone every
relevant part — and how to expose that as a free, easy path.* It folds in the
three follow-ups: (1) dogfood the converter on this repo's own skills; (2) sweep
popular skills and offer a **new-copy** DOS PR (never replace — political care);
(3) a GitHub issue to benchmark a skill **with vs. without** DOS.

It is a design plan, not yet code. Nothing here invents a kernel verb: the
machinery already exists (the EXAMPLES.md recipes, `dos doctor --json`, the
witness rungs). What is missing — and what this plan specifies — is the
**conversion procedure** that maps a skill's trust-claims onto those recipes,
plus the packaging that makes it a one-command free service.

---

## 1. What "DOS support" means for a skill (the target state)

A skill is a **screenplay**: prose that tells an agent how to do a job. It is
*not* a program (EXAMPLES.md). DOS does not rewrite the job; it changes **how
the skill knows a step worked**. The kernel's one rule, aimed at a skill:

> Never set a "done / safe / found" bit from what the agent *says* — set it
> from a read-back the agent did not author.

So a DOS-aware skill differs from its original in exactly the places where the
original **believed a worker**. Concretely, three edit kinds, and a fourth
non-edit that matters just as much:

- **ADD** — a witness step where the original had none. "I committed the fix" →
  add `dos verify` / `dos commit-audit`. "All 7 workers returned" → add `dos
  coverage --declared 7`. "Keep going until done" → add `dos hook stop`.
- **REPLACE** — a self-check with a grounded one. "Re-read the transcript to
  decide if the goal is met" → replace with the witnessed Stop gate. "grep the
  commit subjects to see if it shipped" → replace with `dos verify` (let the
  oracle weigh the rung). "filter(Boolean) the fan-out" → replace with the
  terminal-state partition.
- **GUARD** — a coordination step the original did blind. "edit these files" →
  precede with `dos arbitrate` over the file-tree so a concurrent agent can't
  collide. "trust this recalled note" → precede with `dos recall` re-verify.
- **LEAVE ALONE (the discipline)** — the job logic, the taste, the domain
  steps. DOS touches the *trust seams*, nothing else. A converter that rewrites
  the whole skill is wrong; the diff should be small and legible, every hunk
  traceable to one trust-claim. This is also the political answer (§5): a small,
  additive, reviewable diff is one a maintainer can accept.

The litmus for "is this skill DOS-aware now": **every place the skill sets a
belief bit, it shells a `dos` verb and reads the verdict; the job logic is
untouched.**

---

## 2. The conversion procedure (the algorithm)

This is the heart of the plan. It is itself a **DOS skill** — `dos-skillify`
(name provisional) — a screenplay that converts other screenplays. It runs in
five passes. It is read-only on the source skill; it emits a new copy + a report.

### Pass 0 — Discover the workspace (the WCR on-ramp)

`dos doctor --workspace . --json` once. Everything host-specific the converted
skill will reference — lane names, plans glob, the exit-code tables, the stamp
grammar — is **data** read here, never a literal baked into the output. This is
what lets one converter produce a skill that runs unchanged on a host with
different lanes. (EXAMPLES.md Recipe 0.)

### Pass 1 — Parse the skill into claim-sites

Read the source `SKILL.md` (+ any supporting files). Tokenize it into **steps**
and, for each step, detect whether it sets a **belief bit** — a place the skill
concludes something is true and acts on it. The detector is a **claim-site
taxonomy** (the closed set below). Each match is a `{step, span, claim_kind,
evidence_today}` record. Free prose that sets no belief bit is left untouched.

A claim-site is recognized by **what it concludes**, not by keywords (keywords
seed the search; the model confirms the conclusion). The taxonomy maps 1:1 onto
the EXAMPLES.md recipes — that is the design's spine:

| Claim-kind the step makes | Recipe | The `dos` verb the converted step uses |
|---|---|---|
| "(plan, phase) / this code shipped" | R1 | `dos verify` — read the rung, never the bare bool |
| "this commit did what its subject says" | R1 | `dos commit-audit` — subject vs. its own diff |
| "keep working until the goal is met" | R1b | `dos hook stop` wired alongside the harness goal |
| "I created file / row / message / deploy" | R9/§2b | effect read-back (Python API gap — log it honestly) |
| "a CI / workflow run concluded GREEN/RED" | issue-verify R-run | the run's own `conclusion` field (`gh run view --json conclusion`, the `dos.drivers.ci_status` rung), not the run-log — **driver-witnessed; no first-party CLI verb yet, log the gap** |
| "I'm editing these files (no one else is)" | R2 | `dos arbitrate` over the tree **before** writing |
| "is there work / is it safe to proceed" | R3 | `dos gate` — branch on the exit code, not prose |
| "how is run R doing" | R4 | `dos status` — the no-`claimed`-field digest |
| "this tool-loop is making progress" | R5 | `tool_stream` (Python API) — distrust repetition |
| "this long job is alive" | R6 | `dos lease-lane heartbeat` + `dos liveness` |
| "this recalled memory is still true" | R7 | `dos recall` — re-verify at read |
| "all N fan-out workers returned a real result" | R9/R10 | `dos verify-result` + `dos coverage --declared N` |
| "this issue / ticket is resolved" | (issue-verify) | witness each effect, fold, then close |
| taste / intent / future work | — | **no witness exists → leave as prose, never fake one** |

The last row is load-bearing: a claim with **no witness** is not converted into
a fake check. It is surfaced in the report as `UNWITNESSABLE` (the
fail-to-abstain discipline). The converter never manufactures a `dos` call that
can't actually ground the claim.

### Pass 2 — Classify each site: ADD / REPLACE / GUARD / LEAVE

For each claim-site, decide the edit kind (§1) by comparing `evidence_today`
against the recipe's witness:

- The step already shells the right `dos` verb → **LEAVE** (idempotent: running
  the converter on an already-DOS skill is a no-op — important for re-runs and
  for the dogfood test).
- The step self-certifies (re-reads its own output, greps subjects, trusts a
  return string) → **REPLACE** the self-check with the verb.
- The step concludes truth with no check at all → **ADD** the verb.
- The step mutates shared state with no coordination → **GUARD** with
  `arbitrate` / `recall` ahead of it.

### Pass 3 — Emit the converted copy (additive, legible)

Write a **new** skill dir — `<name>-dos/SKILL.md` (never overwrite the source;
§5 politics). Insert / replace / guard exactly the classified spans. Each edited
hunk carries a one-line trailer comment tying it to its recipe, e.g.
`<!-- dos:R1 verify — was "I committed it, so it shipped" -->`, so a reviewer
sees *why* every hunk exists. The frontmatter gains:

- `allowed-tools:` extended with `Bash`/`PowerShell` if a `dos` shell-out was
  added (and the MCP `dos_*` tools if the host uses the MCP mount).
- a `Step 0` that runs `dos doctor --json` (if not already present).
- a top note: *"DOS-aware variant of `<name>`. The job is unchanged; the trust
  seams now ask a witness. Original: <link>."*

### Pass 4 — Emit the conversion report (the audit)

A `CONVERSION.md` next to the new skill: a table of every claim-site, its
claim-kind, the edit kind, the recipe, and the resulting hunk — plus the
`UNWITNESSABLE` sites left as prose with *why*. This is the artifact a
maintainer reads to trust the PR (§5) and the artifact the benchmark scores
against (§6). It is also the converter eating its own dogfood: the report is the
*witness* that the conversion did what it claimed — a reviewer re-derives the
diff from it without trusting the converter's say-so.

### The one rule under the procedure

The converter is itself a judged agent. It must not *claim* "I made this skill
DOS-aware" — it must produce a report whose every row a reviewer can check
against the diff. **The converter that self-certifies its own output is the
exact failure DOS exists to fix.** (This is why Pass 4 exists and why §4
dogfoods it through `commit-audit`.)

---

## 3. Two surfaces: the verb and the skill (and why both)

The procedure needs a home. Two complementary surfaces, smallest-first:

- **`dos-skillify` (the SKP skill)** — the screenplay above, shipped in the
  generic skill pack (`src/dos/skills/dos-skillify/`), synced to the plugin by
  `build_plugin.py`. This is the primary surface: an agent that has the DOS
  plugin can say "make this skill DOS-aware" and the screenplay runs. It names
  no host (it reads `dos doctor --json`), so it qualifies for the SKP. **Start
  here — it ships the capability with zero new kernel code.**
- **`dos skillify --grade <path|--all>` (the CLI verb — shipped)** — a thin
  read-only command that runs the deterministic part: it scans a skill for
  belief-bits and scores its **DOS grounding signal** (how many bits shell a
  `dos` verb near their trust language vs. how many are self-certify smells), and
  emits a per-bit report a maintainer can read without an agent loop, in CI,
  headless. The full 5-pass *conversion* (the prose-judgment hunks) stays the
  `dos-skillify` SKILL screenplay; the verb is the static estimate that feeds the
  §6 benchmark and the §7 on-ramp headline. It is a *helper* (layer 3,
  policy-free): it shells/reads only and mints **no new verdict**
  (`src/dos/skill_grade.py` + `cmd_skillify` in `cli.py`).

  Crucially, the grade is a **signal, not the model's verdict.** A static scanner
  cannot replicate the per-bit classification (skills phrase belief-bits in ways
  no keyword list captures, and ground them with verbs beyond a fixed claim→verb
  map). Forcing a precise number would mean reverse-engineering the scanner to a
  known answer — the exact bias the kernel refuses (docs/333). So the verb
  reports STRONG/MODERATE/WEAK with the ungrounded-looking sites as **review
  candidates** for the SKILL pass to confirm, and `--check` skips sparse
  detection (it never punishes the scanner being blind), firing only on a real
  self-certify density. The honesty floor of §2 is preserved: UNWITNESSABLE
  claims and anti-pattern mentions are excluded from the denominator.

Build the skill first; promote the deterministic core to a verb only once the
skill proves the claim-site taxonomy is right. The M2 dogfood (§4) proved it, so
the grade verb is built — as a signal, with the precise verdict left to the model.

---

## 4. Dogfood — convert THIS repo's own skills first (follow-up 1)

The converter's first user is DOS itself. This repo ships 4 host skills
(`.claude/skills/`) and 14 generic skills (`src/dos/skills/`). Most generic ones
are *already* DOS-aware (they're born shelling `dos` verbs) — so they are the
**idempotence test**: running `dos-skillify` on `dos-goal-gate` must produce a
near-empty diff (all LEAVE), proving the converter doesn't churn an
already-grounded skill.

The interesting dogfood targets are skills that have trust seams *not yet*
grounded, or external skills vendored in. The dogfood ritual:

1. Run `dos-skillify` over each skill; collect the `CONVERSION.md` reports.
2. For an already-DOS skill → assert the report is all-LEAVE (idempotence). A
   non-LEAVE row on `dos-goal-gate` is a converter bug, not a finding.
3. For a skill with a real ungrounded seam → land the converted hunk, then
   prove it with the kernel: `dos commit-audit HEAD` (subject vs. diff) and, if
   it touched a phase, `dos verify`. **The converter's output is admitted the
   same way any work is — by witness, not by its own report.**
4. Write the dogfood result into the plan's evidence and a memory.

This is also the honesty check on §2's taxonomy: if converting the repo's own
skills surfaces a claim-kind not in the table, the taxonomy is incomplete — add
the row (and the recipe, if it's genuinely new).

> **Headless spawn (operator's "next step").** A headless `claude -p` worker,
> wrapped by `dos guard` so it can call the referee, runs the dogfood sweep and
> the §5 corpus design unattended. Spec in §8.

### Dogfood result (2026-06-15, M1 — verified)

The taxonomy was run by hand over all 18 repo skills (14 generic + 4 host).
Findings, each ground-checked:

- **Idempotence PASS — no converter churn.** All 14 generic skills come out
  **all-LEAVE**; so do 2 of the 4 host skills (`issue-work`, `issue-verify`).
  `dos-goal-gate` (the canonical idempotence target) is clean — every belief-bit
  already shells the right `dos` verb. This validates the converter's core design
  promise: it does not rewrite an already-grounded skill.
- **One taxonomy gap found and CLOSED — `CI_GREEN`.** "a CI/workflow run concluded
  green/red" is a load-bearing belief-bit in `release` / `stable-release` /
  `issue-verify`, but it mapped onto no §2 row (it is neither `SHIPPED` git
  ancestry nor a file/row `EFFECT`). Its witness is the run's own `conclusion`
  field (the `dos.drivers.ci_status` rung) — driver-witnessed, with **no
  first-party CLI verb today**, so a converter logs it as a gap. Added as a §2
  row. This is exactly the honesty-check loop above working as designed.
- **Two arguable host-skill seams (not bugs).** `release` Step 7.6 runs
  `dos commit-audit --warn-only` (advisory by design — correct for an
  already-immutable tag → LEAVE-with-a-note); `stable-release` Step 2 gathers the
  PyPI read-back (`pip index versions`) *by hand, not in the gate* — the most
  defensible GUARD, promotable to a `stable_release_context.py` gate row. Neither
  is a forced conversion; both are surfaced, not auto-edited.

---

## 5. Sweep popular skills, offer a NEW-COPY DOS PR (follow-up 2) — "think politics"

The growth loop: find widely-used public skills, convert each to a DOS-aware
**new copy**, and offer it as a PR. The political constraints are first-class
design inputs, not afterthoughts:

- **New copy, never replace.** The PR adds `skills/<name>-dos/` (or a
  clearly-marked variant); it never edits the maintainer's `<name>`. A
  maintainer who'd reject "you rewrote my skill" accepts "here's an optional
  grounded variant you can ignore." The original keeps working byte-for-byte.
- **Small, additive, legible.** The `CONVERSION.md` (Pass 4) ships *in the PR*
  so the maintainer sees every hunk's justification without trusting us. No hunk
  without a recipe citation.
- **Honest about scope.** The PR body says exactly what DOS adds (witnessed
  trust seams) and what it does **not** (it doesn't make the skill smarter or
  change its job). Over-claiming here would be the same sin the kernel refuses.
- **Opt-in, no lock-in.** The variant degrades gracefully if `dos` isn't
  installed (the steps say "if `dos` is available…"); it never hard-requires the
  kernel to keep the original job working. This removes the "now I depend on your
  tool" objection.
- **Leak gate + public-text discipline.** Every PR body and every committed file
  is public: run it through `scripts/leak_scan.py --stdin` before posting (the
  repo's standing rule), and use the fork-and-PR mechanics already proven here
  (write_bytes not write_text for CRLF; watch the fork-name collision).
- **Attribution & licence.** The variant credits the original author and
  preserves the source licence. A DOS-aware copy is a derivative work; treat it
  like one.

**Corpus selection** (designed, not yet run): rank candidate skills by real
usage — public skill marketplaces, high-star agent repos that ship a `skills/`
or `.claude/skills/` dir, the awesome-* lists. Prefer skills whose job involves
**a checkable effect** (ship code, file an issue, run CI, fan out workers) —
those are where DOS adds the most and the diff is most compelling. Skip skills
that are pure prose/taste (DOS has nothing to add — and a forced conversion
would be the fake-witness anti-pattern). Cap the first wave small (3–5 PRs) to
learn maintainer reactions before scaling. **One PR is the proof; a wave is the
growth loop — gate the wave on the first PR's reception.**

---

## 6. Benchmark: skill with vs. without DOS (follow-up 3 → a GitHub issue)

The claim "a DOS-aware skill is more trustworthy" must be **measured**, not
asserted (the kernel's own discipline applied to its growth pitch). This becomes
a tracked issue (§8 drafts it). The design:

- **Unit of measure: the silent over-claim rate.** Run the original skill and
  its `-dos` variant on the same task corpus, where some tasks are rigged so the
  *correct* answer is "this did NOT work" (a commit that didn't land, a fan-out
  worker that died synthetically, a phase claimed-but-not-shipped). Score: how
  often does each variant **declare success when ground truth says failure**?
  The DOS variant should drive that rate toward zero; the original should leak.
- **This connects to existing measured curves.** docs/341 already measures the
  recoverable fraction across model capability (the inverted-U); docs/322 the
  poisoned-pool over-claim. The skill-with/without-DOS benchmark is the same
  instrument aimed at the *skill* layer instead of the model layer. Reuse the
  fixtures and the `gate_fraction` harness where they fit.
- **Honest negatives.** The benchmark must be able to show DOS *not* helping
  (e.g. on a pure-prose skill, or where the witness rung is forgeable) — a
  benchmark that can only confirm the thesis is the bias the kernel refuses
  (docs/333). Report refused/advisory, never "caught N" headlines (the `helped`
  discipline).
- **Cost axis.** Record the added latency/tokens of the witness steps. A
  grounded skill that's 3× slower is a real trade-off a maintainer weighs; hiding
  it would be dishonest.

---

## 6.5 Why this is unique — and where it is not (the honest positioning)

The operator's question behind this whole plan: *"DOS can be added to any skill —
how unique is that, really?"* The kernel's own discipline says: don't assert it,
check it against the field. A landscape scan of what shipped agent ecosystems,
eval frameworks, guardrails, and the self-verification literature actually do (as
of mid-2026) gives a sharp, falsifiable answer — including two things we must
**not** claim.

The claim only matters if **four** properties hold together. Grade any neighbor
against all four:

1. **Additive over an *arbitrary* existing skill** — it bolts a layer onto a
   skill you already have, without rewriting its job. Not "adopt our framework."
2. **Grounds on an independently-authored witness** — the "done/shipped/found"
   bit is decided by something the agent did not write (git ancestry, a CI run's
   `conclusion` field, a file read-back), not the agent's own output re-scored,
   not a content-safety classifier, not a supplied reference corpus.
3. **Witness-type-agnostic** — it spans many effect kinds (a phase shipped, a
   file written, a CI run, a fan-out fold), not one fixed oracle.
4. **Inline per-run** — it gates the belief *before* the agent acts on it, not an
   offline eval over a logged trace.

### The two things we do NOT get to claim

- **Inline gating is not novel.** CrewAI task guardrails, the OpenAI Agents SDK,
  Galileo Agent Control, and DeepEval guards all block a step inline. Property 4
  is table stakes, not a differentiator. What is unusual is *what our gate reads*
  (property 2), never that it gates.
- **"Distrust the narration, check an oracle" is not novel.** It is an active
  research direction (execution-grounded verification — *AgentForge*, *Verify
  Before You Fix*) and at least one shipped tool (the Swarm merge-gate
  orchestrator demotes transcript-parsing and checks `git_diff` / `test_exec`).
  We did not invent the principle. Claiming we did would be exactly the
  over-claim the kernel refuses.

### What the field actually leaves unoccupied — each neighbor breaks *exactly one* property

With those two concessions made, the surveyed field still does **not** deliver
all four properties together. The sharp, falsifiable form of the finding: **the
five nearest neighbors each break exactly one of the properties** — and the five
breaks, taken together, cover the whole conjunction. None breaks zero (that would
be a true competitor); none needs two breaks to be excluded (that would mean the
properties are redundant). One property cleanly cedes to each:

> A note on the count. The four numbered properties above are the *claim* surface
> — what a maintainer is buying. To map "exactly one break each" cleanly, property
> 2 ("grounds on an independently-authored witness") splits into the two distinct
> ways a check can fail to be that: it can score the agent's **own** output
> (not *independent*), or it can compare text against a **supplied document**
> (not an *effect in the world*). Those are different failures, broken by
> different neighbors — so the honest grading axis is five-wide, and each
> neighbor trips exactly one wire.

- **Agent skill formats** (Claude / OpenAI skills) are prompt + tool-binding with
  **no outcome-verification construct at all.** Breaks property 2 at the root:
  there is no grounding step to be independent *of*. (Passes 1, 3, 4 vacuously —
  there is simply nothing there to check.)
- **"Online evals" / eval frameworks** (LangSmith, Braintrust, Arize) market
  continuous scoring, but it is LLM-judge scoring of *logged traces* —
  observability that runs **async, after the fact, not as a gate.** Breaks
  **property 4 (inline-per-run)** specifically. This is the single most common way
  a reviewer wrongly concludes the problem is already solved — so name the exact
  miss: it never gates the belief *before* the agent acts on it.
- **Guardrails** (NeMo, Guardrails AI, Llama Guard) validate text against a
  rule / schema / safety taxonomy, or entailment against a *retrieved* corpus.
  They check **text-vs-rule / text-vs-RAG-corpus, never effect-vs-world.** Breaks
  the **effect-in-the-world** half of property 2. Their fact-check rails are the
  most *confusable* — they do check a claim against a source, inline — but the
  source is a supplied document, not a witness that an effect occurred.
- **Self-verification research** (Reflexion, Self-Refine, CriticGPT, LLM-judge)
  is the model checking its **own** work — **consistency, not grounding.** Breaks
  the **independent-authorship** half of property 2 (the self-preference-bias
  literature is direct evidence the checker is not independent of the checked).
- **Execution-grounded research & Swarm** are the closest: they genuinely ground
  on an independently-authored effect (property 2 holds). But they ground on **one
  narrow oracle (code execution / a test pass) inside a bespoke pipeline you
  adopt** — so they break **property 1 (additive over an arbitrary skill) and
  property 3 (witness-type-agnostic) as a single failure**: the same "it's one
  fixed oracle wired into one purpose-built pipeline" fact trips both. It is not a
  layer you wrap around a pre-existing skill, and it does not span effect kinds.

That last neighbor is the one honest seam in the "exactly one" framing: P1 and P3
co-fail because they fail *for the same reason* — a single-oracle bespoke pipeline
is both non-additive and non-agnostic by construction. They are one wire, not two.
The other four neighbors each trip a genuinely distinct wire.

### The defensible one-liner

> Not *"first to verify agent claims"* — that over-claims, and the field has been
> verifying claims for years. The honest, narrow, defensible claim is the
> **conjunction**, not any one property: **the first to make independent-witness
> grounding a mechanical, additive, witness-type-agnostic layer over *any*
> existing skill, inline — rather than a property of a bespoke pipeline, an
> offline eval, or a content-safety gate.**

Each clause in that sentence is load-bearing because it is the exact property a
named neighbor lacks: *mechanical / additive over any existing skill* (vs.
execution-grounded's bespoke pipeline), *witness-type-agnostic* (vs. its one
oracle), *independent-witness grounding* (vs. self-verification's consistency and
guardrails' text-vs-corpus), *inline* (vs. eval frameworks' offline traces). Drop
any clause and a neighbor catches up; the claim is precisely as strong as the
four-property conjunction and no stronger.

That is the value: a maintainer keeps their skill, their job logic, their taste,
and gains a grounded "done" without adopting a framework — at the cost of the
witness steps' latency (§6's cost axis) and the honest `UNWITNESSABLE` rows DOS
can't ground. The benchmark (§6) is what turns this positioning from an argument
into a measurement; this section is what the benchmark is *for*.

> Sourcing note: this section is grounded in a 2026 landscape scan across the
> five categories above (agent skill ecosystems, eval/observability frameworks,
> prompt/skill linters, guardrails, and the self-verification literature). The two
> concessions are load-bearing — a positioning that can only confirm itself is the
> bias the kernel refuses (docs/333). Treat any specific 2026-dated arXiv ID as
> claimed-not-verified until resolved; the *shape* of the field (who holds which
> properties) is the robust finding. The two property-assignments most likely to be
> contested are **primary-source-verified**: LangChain's own docs call online eval
> an *async* handler that "continuously evaluates real user interactions... to
> monitor quality" (observability, not a gate → the P4 break, not a P2 one); and
> NVIDIA's NeMo docs define the fact-checking rail as entailment "given an evidence
> passage" from a retrieved KB, with the parametric-knowledge path being SelfCheckGPT
> (text-vs-document / self-resampling → the effect-vs-world half of P2, never an
> external effect). Those two are the neighbors a reviewer most often thinks already
> solve this; both demonstrably break exactly the property assigned.

---

## 7. The free, easy on-ramp (the "expose as a service" axis)

Three tiers, cheapest-to-stand-up first. All free; the kernel is already MIT/pip.

1. **One command, today (zero new infra).** `pip install dos-kernel`, then the
   `dos-skillify` skill (or `dos skillify ./my-skill` once §3's verb exists)
   converts a local skill and prints the report. This is the floor: the on-ramp
   is "install the kernel, point it at your skill." Document it in QUICKSTART
   and a `docs/answers/` GEO page ("how do I make my agent skill verify its own
   work?").
2. **A GitHub Action (`dos-skillify-action`).** A composite action a repo drops
   into CI: on a skill change, it runs the converter and **opens a PR with the
   `-dos` variant + the `CONVERSION.md`**. This is the self-serve form of §5 —
   maintainers convert their own skills, we don't have to PR every repo by hand.
   It reuses the `verify-action/` pattern already in this repo. Free (Actions
   minutes are the user's).
3. **A hosted "bring a skill" page (later, optional).** Paste a skill / give a
   repo URL → get back the `-dos` variant + report + a "what DOS added" summary.
   Pure marketing-funnel convenience over tiers 1–2; only worth it if the lower
   tiers show demand. Must run the converter server-side with the same leak gate;
   no skill content is stored.

The on-ramp's honesty rule mirrors the product's: the page/Action must show the
`UNWITNESSABLE` rows too — "here's what DOS could ground, and here's what it
couldn't" — so the free service can't itself over-claim what it did.

---

## 8. Headless spawn spec (operator's "next step")

Spawn one headless `claude -p` worker (wrapped by `dos guard` for the MCP mount),
armed with this plan, to do the unattended legwork — **design and dry-run, never
auto-PR** (a PR to a stranger's repo is outward-facing; it waits for operator
sign-off):

1. **Dogfood (§4):** run the `dos-skillify` taxonomy by hand over this repo's 18
   skills, produce the `CONVERSION.md` reports, assert idempotence on the
   already-DOS ones, and flag any claim-kind missing from §2's table.
2. **Corpus design (§5):** enumerate 10–20 candidate public skills ranked by
   usage, classify each as "DOS adds a lot / a little / nothing", and draft the
   first 3–5 PR bodies (through the leak gate) — **as drafts, not posted.**
3. **Benchmark issue (§6):** file the GitHub issue below (the worker drafts the
   body, leak-scans it, and the operator/`issue-work` files it).

The worker's "done" is gated on `dos hook stop`: its deliverables are checkable
files (the reports, the draft bodies, the issue), so a fresh read — not its
narration — closes the goal.

### The benchmark issue to file (draft)

> **Title:** Benchmark: skill trustworthiness with vs. without DOS conversion
>
> **Body (done-condition):** Build a harness that runs a skill and its
> `dos-skillify`-converted `-dos` variant on a shared task corpus containing
> rigged-failure tasks (a commit that didn't land, a synthetically-dead fan-out
> worker, a phase claimed-but-not-shipped), and reports the **silent over-claim
> rate** (declared success where ground truth = failure) for each variant, plus
> the added token/latency cost. Done when: (a) the harness runs on ≥3 skills;
> (b) it reports per-variant over-claim rate + cost with the rigged-failure
> denominator explicit; (c) it can demonstrate a *negative* (a skill DOS doesn't
> help) without crashing; (d) results land in `benchmark/skill_dos_ablation/`
> with a README. Reuses docs/341's recoverable-fraction instrument and the
> `gate_fraction` harness. Label `ready`.

---

## 9. Sequencing

1. **M1 — the skill** (`src/dos/skills/dos-skillify/SKILL.md` + `EXAMPLES.md`
   recipe). Ships the capability, zero kernel code. Witness: it runs on
   `dos-goal-gate` and produces an all-LEAVE report (idempotence).
2. **M2 — dogfood** (§4): convert the repo's own skills, land any real ungrounded
   seam, prove via `commit-audit`. Witness: the `CONVERSION.md` reports + a green
   `commit-audit` on each landed hunk.
3. **M3 — the benchmark issue** (§6/§8) filed, then the harness built under
   `benchmark/skill_dos_ablation/`. Witness: the issue closes via `Fixes #N` when
   the harness meets its done-condition.
4. **M4 — the on-ramp tier 1** (§7): QUICKSTART + a `docs/answers/` page.
   Witness: the page renders 200 and the command in it runs clean.
5. **M5 — the GitHub Action** (§7 tier 2) + the first §5 PR, **operator-gated**.
   Witness: the action opens a PR carrying a `CONVERSION.md`; the PR is reviewed
   by a human before send.

M1–M4 are agent-shippable here. M5 is outward-facing and waits for sign-off.

---

## 10. Litmus (what makes this DOS, not a linter)

- The converter **shells `dos` verbs**; it mints no new verdict and decides no
  ground truth itself (it is a layer-3 helper / a screenplay).
- It **abstains** on an unwitnessable claim — it never fakes a `dos` call that
  can't ground the claim (fail-to-abstain).
- Its output is **admitted by witness** (`commit-audit` / the `CONVERSION.md`
  re-derivable by a reviewer), never by the converter's own say-so.
- It is **idempotent** — re-running on a DOS-aware skill is a no-op.
- It names **no host** (reads `dos doctor --json`), so the converted skill runs
  unchanged on any host — qualifying `dos-skillify` for the generic SKP.
- The growth surfaces (§5 PRs, §7 service) carry the **same honesty floor** as
  the product: show the `UNWITNESSABLE` rows; never over-claim what DOS added.
