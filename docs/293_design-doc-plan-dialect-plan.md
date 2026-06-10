# 293 — the design-doc plan dialect: `dos plan` rows from this repo's own prose plans

> **Status:** PLAN (design accepted; Phase 1 shipped; Phase 2 in flight).
>
> **Design revision (2026-06-10, during Phase 2):** the first cut of Phase 2 put
> the `[plan]` reader in `config.py` as a `SubstrateConfig.plan_source_name`
> field — and the kernel's own SELF_MODIFY hook **refused the edit**
> (`config.py` is in the T1 runtime set; `dos man wedge SELF_MODIFY`: the
> agent-side fix is to not edit runtime files mid-loop — `--force` is the
> operator's move, deliberately absent from the PreToolUse ABI). The revision
> honors the refusal instead of routing around it: the `[plan]` grammar lives in
> `plan_source.py` (the seam module that owns the axis, exactly as `stamp.py`
> owns `[stamp]` — and not in the T1 set), read at the `plan_board.snapshot`
> boundary where the projection's other reads already happen. Same declared
> data, same resolution order, no live-kernel edit — the guard adjudicating
> this work shaped it, which is the dogfood working.

## The gap this closes

On this repo `dos plan --once` prints **"(no plans declared)"** — and the
CLAUDE.md "DOS on DOS" contract (step 5) is explicit that this is the *correct*
conservative degrade, not a bug: `[paths].plans_glob` matches the `docs/NN_*-plan.md`
corpus, but the built-in `markdown` source harvests only the strict
`### N. PLAN PHASE — …` grammar (letter+digit phase ids), and this repo's plans
are PROSE — status lives in a `> **Status:**` sentence, phases live under
`## Phase N — …` / `### GHF1 — …` headings. The built-in deliberately
under-harvests that dialect rather than mine prose for phantom phases (the
docs/228 Run-A empty case: a sound witness aimed at a distribution with no
parseable claims in it).

The documented fix has always been named in `plan_source.py` itself: *"DOS's own
`### Phase N:` design-doc dialect … wants a `dos.plan_sources` plugin, not a
guess."* This plan builds that plugin — **dogfooding the Axis the kernel ships**
(HACKING.md: data in `dos.toml`, behavior in `entry_points`) instead of loosening
the kernel harvester. The kernel default stays byte-identical; the dialect is a
driver, selected as declared data.

## The design

Two pieces, one per phase:

1. **The dialect driver** — `dos.drivers.design_doc_plan:DesignDocPlanSource`, a
   `PlanSource` (name `design-docs`) registered under the
   `dos.plan_sources` entry-point group in `pyproject.toml`. It lives in
   `drivers/` because it encodes a *host convention* (this repo's plan-doc
   grammar); the kernel seam (`dos.plan_source`) is not edited.
2. **The `[plan]` data seam** — a workspace declares its plan source by name in
   `dos.toml` (`[plan] source = "design-docs"`), mirroring `[overlap] policy`.
   The grammar lives with the seam (`plan_source.load_plan_source_name_from_toml`
   + the boundary helpers `declared_source_name` / `default_source` — see the
   status-note revision: NOT a `config.py` field), read by `plan_board.snapshot`
   when neither explicit phases nor `--source` was given. Resolution: explicit
   rows › `--source` flag › declared `[plan].source` › built-in `markdown`. A
   declared name that does not resolve (plugin not installed) **fails to
   empty** — the board degrades to its no-plan floor, never to a silently
   substituted harvester.

### The harvest grammar (closed, and why each guard exists)

The dialect recognises exactly two heading shapes, both anchored at a markdown
heading (`##`–`####`), and nothing else:

* **The `Phase N` keyword form** — `## Phase 1 — title`, `### Phase 0: title`.
  The literal keyword `Phase` followed by a bare ordinal IS the guard: the
  built-in rejects digit-only phase tokens because in *its* grammar a bare
  ordinal is prose noise (`### 1. Phase 2 of 3 — done`); here the keyword is
  required and the heading must START with it, so prose like `## Phased roadmap`
  or `## 2. Phases` cannot match. The emitted phase token is `Phase N`
  (normalised spacing) — the spelling this repo's ship trailers use
  (`(docs/112 Phase 0)`) and `phase_shipped._phase_variants` bridges.
* **The id-led series form** — `### GHF1 — title`, `### ISV0 — …`, `### F2 — …`.
  The token must start with a LETTER, contain a DIGIT, and be followed by an
  em/en-dash or colon separator. Letter-start rejects the numbered-section noise
  (`### 8.2.1 — Scoping RESULT`, `## 3a. How much…`); the digit requirement
  rejects prose words (`### Design A — …` has no digit); the required separator
  rejects mid-sentence ids.

The **plan id** is the doc's root-relative path minus `.md`
(`docs/82_liveness-oracle-plan`) — the exact positional string the oracle takes,
whose `docs/82` head `_series_variants` already bridges to this repo's trailer
stamps. A doc with no recognised heading yields **no rows** (docs/75, docs/97,
docs/263 today) — under-harvest, never a guessed row.

### The claimed-status read (the part the board distrusts)

`claimed_status` is the plan's self-report, shown only to contrast against the
oracle. It is read from a CLOSED vocabulary, never mined from body prose:

1. **The heading line only** (not the section body — long design-doc bodies
   mention "shipped" incidentally): `SHIPPED` (any case, word-bounded — the
   `\b` rejects `phase_shipped`), `DONE` (upper-case only), or a `✅` mark ⇒
   claimed shipped; the kernel's blocked vocabulary
   (`SOAK|SOAKING|BLOCKED|AWAITING|GATED|DEFERRED`) ⇒ claimed blocked.
2. **The plan-wide close-out mark**: a `> **Status:**` line whose text begins
   with `✅` and contains `SHIPPED` claims every phase of that doc shipped
   (docs/70/72/73/74). A `🚧` mixed-status sentence ("Phases 1–2 shipped;
   Phase 3 design") is deliberately NOT parsed — per-heading marks carry those
   docs, and prose ranges are exactly the mining this dialect refuses.
3. Otherwise ⇒ claimed open.

Duplicate `(plan, phase)` rows keep the FIRST-seen claim (docs/290 declares
`## Phase 1` as a section and `### Phase 1 — shipped` as its close-out record;
first-seen reads open, and the cost is a benign under-claim, never a
manufactured over-claim).

### What the board will honestly show on this repo

This clone's git history begins at the v0.22.0 squash, so a pre-cutover
`✅ SHIPPED` claim has no ship-stamp in THIS repo's ancestry: the oracle answers
`NOT_SHIPPED (via none)` and the board shows **⚠over-claim**. That is the
witness speaking truly — *this repository's history does not witness that
claim* — and it is the divergence cell the screen exists for, not a false
positive to suppress. Post-cutover ships stamped with the declared trailer
grammar (`(docs/NN Phase M)`) confirm normally, and this plan's own phases are
the first rows to prove it.

## Phase 1 — the dialect driver + entry point (`dos plan --source design-docs`)

`src/dos/drivers/design_doc_plan.py`: the harvester above as a `PlanSource`,
pure-stdlib, fail-to-empty under `run_plan_source` like every source. Registered
in `pyproject.toml` under `[project.entry-points."dos.plan_sources"]` as
`design-docs`. Targeted tests (`tests/test_design_doc_plan_source.py`) pin the
grammar: both heading shapes harvest; numbered-section/prose/digit-only noise
does not; the claim vocabulary including the `phase_shipped` word-boundary trap;
the ✅-status plan-wide rule vs the 🚧 mixed-status refusal; first-seen dedup;
plan-id derivation; fail-to-empty on unreadable docs. Acceptance:
`dos plan --once --source design-docs --workspace .` renders real rows.

## Phase 2 — the `[plan]` data seam (declared, not flagged)

A `[plan] source = "name"` reader in `plan_source.py` (the seam owns its table,
as `stamp.py` owns `[stamp]`: unknown keys fail loud, absent table degrades to
the base; see the status-note revision for why it is not a `config.py` field),
plus the boundary helpers `declared_source_name` (warn-and-fall-back on a
malformed table) and `default_source` (the (name, source) the default branch
reads, unresolvable → `(name, None)` = fail-to-empty); `plan_board.snapshot`
consults them in the default branch (resolution order above); this repo's
`dos.toml` declares `[plan] source = "design-docs"`. Tests pin the loader, the
resolution order, and the fail-to-empty degrade. Acceptance: a bare
`dos plan --once --workspace .` on this repo shows the rows — the goal's
headline — with `dos lint --strict` still clean.

Deferred (named, not silent): the `dos doctor` plan-source line + `--json` key
(the auditability surface) — `cli.py` carried a sibling session's uncommitted
work when this phase landed, and staging it would have swept their edits into
this commit; it is a follow-up edit to `cmd_doctor` mirroring the
`overlap_policy` block.

## Out of scope (explicitly)

* Loosening the kernel's built-in `markdown` harvester — the whole point is the
  extension surface.
* Parsing prose status ranges ("Phases 1–2 shipped") or `> **Status:**`
  sentences beyond the leading-✅ close-out rule.
* The id-led docs whose claims live only in the status sentence (docs/125's
  GHF roster) — they harvest as claimed-open; honest under-claim.
* Multi-source composition (running `markdown` AND `design-docs` together) —
  `plan_source.default_rows` keeps that as its named future one-line edit.

## See also

* `docs/HACKING.md` — "Custom plan dialects (`dos.plan_sources`)", the axis this
  plan dogfoods.
* `src/dos/plan_source.py` — the seam, the built-in's under-harvest contract.
* `docs/289_*` — the trailer-stamp grammar this repo's ships ride.
* CLAUDE.md "DOS on DOS" step 5 — the Run-A empty case this plan retires.
