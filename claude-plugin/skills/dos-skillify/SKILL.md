---
name: dos-skillify
description: Convert any agent skill into a trust-grounded variant — read the skill, find every place it believes a worker's word (a self-certified "done", an ungrounded "it shipped", a blind file-edit, a filter(Boolean) fan-out fold), and emit an ADDITIVE new-copy variant whose trust seams shell a `dos` verb and read the verdict, plus a re-derivable conversion report. Driven by `dos` verbs and the workspace's own `dos.toml` — no host-specific paths, lanes, or commit conventions. Use when an operator hands you a skill and says "make this DOS-aware", "ground this skill's self-checks", or "what would DOS add to this skill". The converter is itself a judged agent: it abstains on an unwitnessable claim and its output is admitted by witness, never by its own say-so.
---

# dos-skillify — make any skill ask a witness instead of believing itself

> **A skill is a screenplay, not a program — and most screenplays trust the
> worker.** When a skill concludes "the fix shipped" because the agent said it
> committed, "the goal is met" by re-reading its own transcript, or "all 7
> workers returned" by `filter(Boolean)`, the part deciding ground truth *is*
> the part being judged. That is consistency, not grounding (docs/138). This
> skill does not rewrite the *job*; it changes **how the skill knows a step
> worked** — it finds every belief-bit and replaces the self-check with a `dos`
> verb whose byte-author is not the agent. The kernel decides; the converted
> skill narrates.

The shape is domain-free: **parse the skill into claim-sites → classify each as
ADD / REPLACE / GUARD / LEAVE → emit an additive new copy + a report.** The
*policy* (which lanes, which plan grammar, which exit codes) is data read from
`dos doctor --json`, never literals — which is exactly what lets the converted
skill run unchanged on a host with different lanes. Full design: docs/345.

## When to use this (and when not)

- **Use it** when you have a skill (a `SKILL.md`, an agent prompt, a workflow
  script) that sets belief bits from self-report and you want those seams
  grounded — your own skill, a vendored one, or a public skill you're preparing
  a variant of.
- **Do not use it** to rewrite a skill's job, make it "smarter", or convert a
  pure-prose/taste skill that sets no checkable belief bit — there is no witness
  to add, and forcing one is the fake-witness anti-pattern this skill refuses.
- **This is a layer-3 helper / screenplay (a PDP, docs/99).** It shells existing
  `dos` verbs and mints no new verdict. It produces a *variant + a report*; it
  decides no ground truth itself. Its OWN output is admitted by witness (Step 5).

## The one rule under this skill

The converter is a judged agent. It must never *claim* "I made this skill
DOS-aware" — it must produce a report whose every row a reviewer re-derives from
the diff without trusting the converter. **A converter that self-certifies its
own output is the exact failure DOS exists to fix.** That is why Step 4 (the
report) and Step 5 (admit-by-witness) are not optional.

## Step 0 — Discover the workspace once (the WCR on-ramp)

```bash
dos doctor --workspace . --json
```

Stash it. Every lane name, plans glob, and exit-code table the converted skill
references comes from here — never a literal. (EXAMPLES.md Recipe 0.) The fields
this skill uses: `lanes` / `paths.plans_glob` / `stamp` (so a converted
`arbitrate` / `verify` step names the host's real taxonomy), and `exit_codes`
(so a converted gate branches on `$?`, not prose).

## Step 1 — Parse the source skill into claim-sites

Read the source `SKILL.md` (and any supporting files / prompt it references).
Walk it step by step. For each step, decide: **does it set a belief bit?** — a
place the skill concludes something is true and acts on it. Record each as
`{step, span, claim_kind, evidence_today}`. Prose that sets no belief bit is
left untouched.

A claim-site is recognized by **what it concludes**, not by a keyword (keywords
seed the search; you confirm the conclusion). Map each to its claim-kind — the
taxonomy is 1:1 with the EXAMPLES.md recipes, which is the whole spine:

| The step concludes… | claim_kind | Recipe | The `dos` verb the converted step uses |
|---|---|---|---|
| "(plan,phase) / this code shipped" | `SHIPPED` | R1 | `dos verify` — **read the rung** (`registry` > `grep-subject` > `none`), never the bare bool |
| "this commit did what its subject says" | `COMMIT_HONEST` | R1 | `dos commit-audit` — subject vs. its own diff |
| "keep working until the goal is met" | `GOAL_DONE` | R1b | `dos hook stop` wired *alongside* the harness goal (ANY-block) |
| "I created file / row / message / deploy" | `EFFECT` | R9/§2b | effect read-back — **no CLI verb today**; Python API + **log the gap** |
| "I'm editing these files (no collision)" | `WRITE` | R2 | `dos arbitrate` over the tree **before** writing; honor the redirect |
| "is there work / safe to proceed" | `GATE` | R3 | `dos gate` — branch on the exit code, not the printed line |
| "how is run R doing" | `RUN_STATE` | R4 | `dos status` — the digest with no `claimed` field |
| "this tool-loop is progressing" | `LOOP_LIVE` | R5 | `tool_stream` (Python API) — distrust repetition, not correctness |
| "this long job is alive" | `ALIVE` | R6 | `dos lease-lane heartbeat` + `dos liveness` |
| "this recalled memory is still true" | `RECALL` | R7 | `dos recall` — re-verify at read time |
| "all N fan-out workers returned a real result" | `FANOUT` | R9/R10 | `dos verify-result` + `dos coverage --declared N` |
| "this issue / ticket is resolved" | `ISSUE_DONE` | (issue-verify) | witness each effect, fold, then close |
| taste / intent / future work | `UNWITNESSABLE` | — | **no witness exists → leave as prose, never fake a check** |

The last row is load-bearing. A claim with no honest witness is **not** turned
into a `dos` call. It is recorded `UNWITNESSABLE` and surfaced in the report
(fail-to-abstain). Never manufacture a verb that can't actually ground the claim.

## Step 2 — Classify each site: ADD / REPLACE / GUARD / LEAVE

Compare each site's `evidence_today` against its recipe's witness:

- Already shells the right `dos` verb → **LEAVE.** (Idempotence: running this
  skill on an already-DOS skill must produce an all-LEAVE report. A non-LEAVE
  row on, e.g., `dos-goal-gate` is a converter bug — see Step 6's self-test.)
- Self-certifies (re-reads its own output, greps subjects itself, trusts a
  return string, `filter(Boolean)`s a fan-out) → **REPLACE** the self-check with
  the verb.
- Concludes truth with no check at all → **ADD** the verb.
- Mutates shared state with no coordination → **GUARD** with `arbitrate` /
  `recall` ahead of it.

## Step 3 — Emit the converted copy (additive, legible, never a rewrite)

Write a **new** skill dir — `<name>-dos/SKILL.md`. **Never overwrite the
source** (this is the political floor for the public-skill PR path, docs/345 §5,
and the safety floor for the operator's own skill). Edit *only* the classified
spans:

- Each edited hunk carries a one-line trailer tying it to its recipe, e.g.
  `<!-- dos:R1 verify — was: "I committed it, so it shipped" -->`, so a reviewer
  sees *why* every hunk exists and what it replaced.
- Add a `Step 0` running `dos doctor --json` if the source lacks one.
- Extend the frontmatter `allowed-tools:` with `Bash`/`PowerShell` if you added
  a `dos` shell-out (and the `dos_*` MCP tools if the host uses the MCP mount).
- Add a top note: *"DOS-aware variant of `<name>`. The job is unchanged; the
  trust seams now ask a witness. Original: <link>. If `dos` is not installed,
  the witness steps degrade to advisory — the original job still runs."* The
  graceful-degrade clause removes the "now I depend on your tool" objection.

Keep the diff **small**. The job logic, the domain steps, the taste — untouched.
If your diff is large, you are rewriting the skill, which is wrong.

## Step 4 — Emit the conversion report (the audit artifact)

Write `<name>-dos/CONVERSION.md`: one row per claim-site —
`{step, claim_kind, edit_kind, recipe, the resulting hunk}` — plus a section
listing every `UNWITNESSABLE` site left as prose **with why**. This is the
artifact a maintainer reads to trust a PR (docs/345 §5) and the artifact the
benchmark scores (§6). It is the converter eating its own dogfood: the report
*is* the witness that the conversion did what it claimed, re-derivable from the
diff without trusting you.

## Step 5 — Admit the converter's OWN output by witness (not by say-so)

The converter's deliverable is a checkable effect (the new files). Do not report
"done" from your narration — witness it:

```bash
dos commit-audit --workspace . HEAD     # the diff does the KIND of thing claimed (subject vs. diff)
```

If the conversion touched a tracked phase, also `dos verify`. Read the verdict
and report *that*, carrying the evidence — exactly as `issue-verify` closes an
issue carrying its witnesses, never on the claimant's word.

## Step 6 — Report (and the idempotence self-test)

- Per skill: the `CONVERSION.md` table + the count of ADD / REPLACE / GUARD /
  LEAVE / UNWITNESSABLE sites, and the `commit-audit` verdict.
- **Idempotence self-test:** run this skill on a known-already-DOS skill (e.g.
  `dos-goal-gate`). Assert the report is **all-LEAVE**. A non-LEAVE row means
  the converter is churning a grounded skill — a bug to fix, not a finding.
- **Taxonomy-gap check:** if a source skill has a belief bit whose `claim_kind`
  is not in Step 1's table, that is an incomplete taxonomy — surface it (and, if
  it's a genuinely new witness shape, the recipe is missing too). Never force it
  into the nearest row.

## Anti-patterns

- ❌ Rewriting the skill's job, prose, or taste. DOS touches the *trust seams*
  only; the diff must be small and every hunk traceable to one claim-site.
- ❌ Overwriting the source skill. The output is always a **new copy**
  (`<name>-dos/`). Replacing the original is both rude (the PR politics) and
  unsafe (the operator's working skill).
- ❌ Manufacturing a `dos` call for an `UNWITNESSABLE` claim "so the skill looks
  grounded". An abstain surfaced in the report is the correct, honest output.
- ❌ Reporting "I made it DOS-aware" without the `CONVERSION.md` + a
  `commit-audit` verdict. The converter that self-certifies is the failure DOS
  exists to fix.
- ❌ Baking a host's lane name, plan dir, or exit code into the converted skill
  as a literal — read `dos doctor --json` and keep it data.
- ❌ Hard-requiring `dos` to keep the original job working. The witness steps
  degrade to advisory if the kernel is absent; the job never depends on it.
