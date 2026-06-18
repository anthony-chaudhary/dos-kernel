# Skill pack audit against the Fable 5 prompting guide (issue #172)

> **Status: audit half DONE; measured thin-down NOT done (named as follow-up).**
> This doc is the in-repo, audit half of [#172](https://github.com/anthony-chaudhary/dos-kernel/issues/172).
> It reads each bundled `SKILL.md` against Anthropic's
> [*Prompting Claude Fable 5*](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
> guide for two failure shapes, and records a per-skill verdict plus the minimal
> edit each would take. It does **not** edit the skills and does **not** run the
> before/after measurement — those are the heavier "measured thin-down" half of
> #172 (see *Follow-up* at the end).

## What the guide says (the two claims being audited)

The guide makes two claims that bear on skill prose that runs inside the agent's
context on a Fable host:

1. **`reasoning_extraction` trigger.** *"Don't instruct Claude to reproduce its
   reasoning in the response. Prompts, skills, or harness instructions that tell
   the model to echo, transcribe, or explain its internal reasoning as response
   text can trigger the `reasoning_extraction` refusal category on Claude Fable 5,
   causing elevated fallbacks to Claude Opus 4.8."* The fix the guide names: read
   structured `thinking` blocks, or use a `send_to_user` tool — never ask for
   reasoning as prose.

2. **Over-prescription.** *"Skills developed for prior models are often too
   prescriptive for Claude Fable 5 and can degrade output quality. Review and
   consider removing older instructions if default performance is better."* The
   guide's separate brevity/scope notes add the shape of the risk: long
   step-numbered procedures that exist to stop a prior model over-planning,
   surveying options, or drifting scope — behaviours Fable handles by default.

## The distinction this audit turns on

DOS's whole doctrine is **"don't fold the worker's narration as fact."** That
shows up all over the pack as lines like *"the kernel decides; the skill
narrates"* and *"close only by ancestry, never by narration."* Those are about
the harness **not trusting** an agent's self-report. That is the **opposite**
surface from the Fable trigger:

- **Fable trigger (bad):** prose that tells **the model** to *write out / show /
  transcribe / explain* its own reasoning as the response. None found (see below).
- **DOS anti-narration doctrine (fine):** prose that tells **the harness** not to
  believe a worker's narrated "done". This is everywhere and is correct — it does
  not ask the model to emit its reasoning, so it cannot trip
  `reasoning_extraction`.

A grep of the whole pack for the trigger verbs
(`narrate|explain|show your|transcribe|echo|reproduce|reasoning|think out loud`
and the tighter `report your reasoning|show your thinking|chain-of-thought|…`)
returned **zero** lines that ask the model to emit its own reasoning. Every
`narration`/`reasoning` hit is the doctrine above, a verb name (`--narrated`,
parsed for nothing), or an `echo` in a shell snippet.

## Part A — `reasoning_extraction` triggers

**Result: clean across all skills. No skill instructs the model to reproduce its
internal reasoning as response text.**

| Skill | Verdict | Note |
|---|---|---|
| dos-dispatch | clean | `narration` only at L136 ("close … never by narration") — anti-narration doctrine, not a show-your-reasoning ask. |
| dos-dispatch-loop | clean | "the kernel decides … route every outcome through the kernel loop decision" — distrust prose, not reasoning emission. |
| dos-next-up | clean | No reasoning-emission ask; `echo "exit=$?"` (L218) is a shell idiom. |
| dos-replan | clean | L106 "never `gh issue close` off your own narration", L167 "drop the doc's self-narrated status" — both anti-narration. |
| dos-replan-loop | clean | L107 "rather than … false-narrate" — anti-narration. |
| dos-self-improve | clean | L21 warns the loop must not "*narrate* improvement instead of making it"; `--narrated` (L131/143) is carried "parsed for nothing". Both are the doctrine. |
| dos-supervise-loop | clean | "typed kernel verdict … not inline prose" — distrust prose. |
| dos-goal-gate | clean | The whole skill exists to *not* decide done-ness by re-reading the agent's narration. L232 "check the gate's **reasoning** out-of-band" = inspect the kernel's verdict via a CLI, not ask the model to emit reasoning. |
| dos-witness-claim | clean | "No model-judge over the return TEXT" (L211) — actively forbids reading narrated bytes. |
| dos-unstick | clean | No reasoning-emission ask. |
| dos-promote | clean | No reasoning-emission ask. |
| dos-class-cycle | clean | No reasoning-emission ask. |
| dos-goal-fleet | clean | L118 "the condition is **echoed** into the child's log" echoes *input* text, not reasoning. Step 5 reads a child's *result envelope*, not its reasoning. |
| dos-skillify | clean | "the converted skill narrates" + "never *claim* 'I made this skill DOS-aware'" — anti-self-cert doctrine. |

**One thing to watch (not a trigger today).** Several skills end with a "Report"
or "Rollup" step (`dos-self-improve` Step 5, `dos-goal-fleet` Step 6,
`dos-skillify` Step 6, `dos-replan` Step 6). These ask for an **outcome summary**
(what shipped, the verdict, the counts) — a deliverable, not the model's internal
reasoning. They are clean. The line to not cross in a future edit: a rollup step
must keep asking for *the outcome and the evidence*, never "explain how you
decided" or "show your reasoning for each verdict." If a future thin-down adds a
"justify each call" instruction to make a rollup richer, that would be the first
`reasoning_extraction` risk in the pack. Today there is none.

## Part B — over-prescription (step-numbered procedure that may hurt Fable)

The guide's claim is that step-by-step prescription that helped a prior model can
*hurt* Fable, which navigates ambiguity, stays in scope, and dispatches subagents
well by default. The load-bearing content of every DOS skill — the **verdict-call
contracts** (`dos verify`, `dos arbitrate`, `dos gate`, `dos improve`, the exit-code
tables) — must be **kept**; the guide itself says keep the contracts and thin the
surrounding narration. So Part B asks only: *which steps are prior-model
scaffolding around those contracts?*

Verdict scale: **lean** (mostly load-bearing contract, little to thin) ·
**moderate** (some thinnable scaffolding) · **prescriptive** (long procedure with
prior-model compensation worth measuring a thin-down on).

| Skill | Lines | Verdict | What is load-bearing (keep) | What is thinnable scaffolding (measure) |
|---|---|---|---|---|
| dos-unstick | 81 | lean | the mine→key→cluster→rank fold + `dos man wedge`/`decisions` verbs | little; the steps map 1:1 to the fold. |
| dos-class-cycle | 83 | lean | the trigger→judge→enact contract + failsafes | little. |
| dos-promote | 85 | lean | the `pickable` exit-code table + hold→action map | little. |
| dos-replan-loop | 88 | lean | the trunk-resolution + release guard (the one host fact) | Step 1/3 entry/re-entry bookkeeping could compress. |
| dos-replan | 133 | moderate | closure detection via `dos verify` + the 0-2 operator filter | the long Step 5/5b filing discipline duplicates `dos-dispatch`'s issue prose — could cross-link instead of restate. |
| dos-skillify | 143 | moderate | the claim-kind taxonomy table + ADD/REPLACE/GUARD/LEAVE | the worked self-test and anti-pattern list are long; the taxonomy is the contract. |
| dos-supervise-loop | 149 | moderate | the SPAWN/REAP/HOLD/FLAG verdict from `dos loop` | the `--max-concurrency`/docs/283 explanation (L34-42) is dense host-tuning prose; the worked transcript repeats the SELF_MODIFY redirect shown in 4 other skills. |
| dos-dispatch | 166 | prescriptive | the doctor→arbitrate→gate→ship→archive contract + exit codes | Step 6 lease-release, the long "Out-of-scope findings" 4-step issue ritual, and the worked transcript are scaffolding; the issue ritual is restated near-verbatim in `dos-next-up`/`dos-replan`/`dos-dispatch-loop`. |
| dos-self-improve | 168 | prescriptive | the propose→verify→measure→keep table + `dos improve` exit codes | "Why this is the honest version of RSI", the RSI-literature aside (L26-30), and the anti-pattern list are explanatory prose around a 4-step contract. |
| dos-next-up | 180 | prescriptive | the verify→classify→gate contract + the dispositions JSON schema | Step 2's three-way classification prose is long; the worked transcript repeats the same two `dos verify` example commits as 4 other skills. |
| dos-witness-claim | 200 | prescriptive | the terminal-gate → classify → witness → fold contract + the CONFIRMED/REFUTED/UNWITNESSED/NO_CLAIM table | the Python read-back block (L147-175) is duplicated almost verbatim in `dos-goal-gate` Step 2b; one could own it and the other cross-link. |
| dos-dispatch-loop | 203 | prescriptive | the 7 stop conditions + the kernel loop-decision contract + reconcile exit codes | Step 4's git-hygiene close-out (L141-181) is a long generic-git tutorial; the DOS-repo note (L176-181) is host-specific; the worked transcript repeats shared examples. |
| dos-goal-gate | 219 | prescriptive | the effect-decomposition + `dos hook stop` + verify exit codes | Step 1a/the two-hooks asides and the Python Step 2b (shared with witness-claim) are long; the contract is ~5 steps under a lot of explanation. |
| dos-goal-fleet | 260 | prescriptive | the decompose→arbitrate→wave-launch→witness→rollup contract + the seat-pool gate | the seat-rotation/host-seam prose, the per-instance launch hygiene, and the 6-item anti-pattern list are heavy; longest skill in the pack. |

**The cross-cutting over-prescription finding.** The single biggest thinnable
mass is **repetition across skills**, not any one skill's internal steps:

- The **"Out-of-scope findings → file an issue"** ritual (dedupe → done-condition
  → leak-check → close-by-ancestry) is restated nearly verbatim in `dos-dispatch`
  (L114-142), `dos-replan` (L92-106), and referenced again in `dos-dispatch-loop`.
- The **Python effect-witness read-back block** is duplicated between
  `dos-witness-claim` (L147-175) and `dos-goal-gate` (L180-212).
- The **same worked `dos verify` transcript** (the `docs/82` grep-subject ship and
  the `docs/99` none ship) appears in `dos-dispatch`, `dos-next-up`, `dos-replan`,
  `dos-replan-loop`, and `dos-supervise-loop`.
- The **SELF_MODIFY auto-pick redirect** transcript appears in `dos-dispatch`,
  `dos-dispatch-loop`, `dos-replan-loop`, and `dos-supervise-loop`.

This repetition is the prior-model habit the guide flags: it was added so a less
capable model would not miss the rule on whichever skill it loaded. On Fable,
which retains instructions across long runs and follows a brief well, the
canonical move is to state each ritual **once** (a shared `EXAMPLES.md` recipe the
pack already has) and **cross-link** from each skill, rather than restate. That is
the lowest-risk, highest-yield thin-down candidate because it removes bytes
without touching any verdict-call contract.

## Recommended minimal edits (for the thin-down half — NOT applied here)

Per the issue, the SKILL.md files are **not edited in this pass** (the thin-down
needs the measured before/after). The edits below are the audit's recommendation,
ordered safest-first, for whoever runs the `dos-self-improve`-shaped measurement:

1. **De-duplicate the three repeated blocks** (issue-filing ritual, Python
   read-back, the shared worked transcripts) down to one canonical home each in
   `EXAMPLES.md`, with a one-line cross-link from each skill. Highest yield, zero
   contract risk.
2. **Thin the explanatory asides** in the four longest skills (`dos-goal-fleet`,
   `dos-goal-gate`, `dos-dispatch-loop`, `dos-witness-claim`) — keep the step
   contract and the exit-code tables; compress the "why this is the honest
   version" / RSI-literature / two-hooks prose to a sentence + a docs link.
3. **Leave every verdict-call contract and exit-code table intact** — the guide
   is explicit that these are load-bearing; thinning them is out of scope.
4. **Add no "explain/justify your reasoning" instruction** to any rollup step
   while thinning — that is the one change that would *introduce* a
   `reasoning_extraction` risk the pack does not have today.

## Scope statement

- **Audited in this report:** 14 shared skills (the 11 named in #172 plus
  `dos-goal-fleet` and `dos-skillify`, which now live in the generic source
  pack). The current bundle has grown since this audit; this count is the
  audited subset. The
  `claude-plugin/skills/<name>/SKILL.md` and `src/dos/skills/<name>/SKILL.md`
  copies of the 14 shared skills are byte-identical, so each finding applies to
  both copies; `dos-stats` and `dos-setup` exist only under `claude-plugin/` and
  are setup/reporting helpers with no agent-steering procedure (not in #172's
  list, not separately audited).
- **`reasoning_extraction` triggers found:** 0 of 14. The pack is clean on Part A.
- **Over-prescription:** 7 of 14 rated *prescriptive* (worth a measured
  thin-down), 3 *moderate*, 4 *lean*. The dominant thinnable mass is cross-skill
  repetition, not any single skill's internal steps.

## Follow-up (the measured thin-down — NOT done here)

This doc completes the **audit + findings** half of #172. The remaining,
heavier half is the **measured thin-down**: run a `dos-self-improve`-shaped
before/after on a representative prescriptive skill (the issue suggests
`dos-dispatch-loop`) on a Fable host, apply the edits above only if a witness
confirms equal-or-better outcomes, and keep the thinner version only on a measured
win. Per #172 §3 ("Measure, don't guess"), that work must not thin on faith — so
it is deliberately left to a follow-up that has a Fable measurement loop. Because
this doc does not close that half, the resolving commit uses *Addresses #172*, not
*Fixes #172*.
