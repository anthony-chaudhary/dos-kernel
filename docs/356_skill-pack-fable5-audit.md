# docs/356 — the bundled SKILL.md pack vs Anthropic's Fable-5 prompting guide (#172)

> **Status:** audit complete (Phase 1). The `reasoning_extraction` axis is
> CLEAN; the over-prescription axis is a *measured* thin-down deferred to a
> Fable A/B harness (the issue's own "measure, don't guess" gate). Closes #172
> axis 1 with the finding recorded; axis 2 stays open as a measured follow-on.

## Why this audit exists

The DOS skill pack (`src/dos/skills/*/SKILL.md`) is the part of the kernel that
runs **inside the agent's context** on whatever host model is configured —
increasingly Claude Fable 5. The verdict CLIs (`dos verify`, `dos arbitrate`,
`dos gate`) are model-invariant, but the SKILL.md prose that wraps them is not.
Anthropic's [*Prompting Claude Fable 5*
guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
makes two claims that bear on shipped skill prose:

1. **`reasoning_extraction`.** "Prompts, skills, or harness instructions that
   tell the model to echo, transcribe, or explain its internal reasoning as
   response text can trigger the `reasoning_extraction` refusal category on
   Claude Fable 5, causing elevated fallbacks to Claude Opus 4.8." A skill that
   trips this means the operator pays for Fable and silently gets Opus.
2. **Over-prescription.** "Skills developed for prior models are often too
   prescriptive for Claude Fable 5 and can degrade output quality. Review and
   consider removing older instructions if default performance is better."

## Axis 1 — `reasoning_extraction`: CLEAN

**Finding: the pack carries zero reasoning-extraction triggers.** A sweep of all
15 SKILL.md files (3,064 lines) for the trigger shapes the guide names —
*show / explain / echo / transcribe / reproduce / write-out / narrate* applied
to *your reasoning / thinking / thought process / chain-of-thought* — returns
**0 matches**. The broader reflection family (`think step by step`, `reflect`,
`show your thinking`, `verbalize`, `chain of thought`) returns only two benign
hits:

- `dos-next-up` "a `grep` ship the plan doc doesn't *reflect*" — *mirror* sense,
  not "reflect on your reasoning".
- `dos-skillify` "Walk it step by step. For each step, decide…" — instructs the
  agent to walk the **skill's own conversion steps**, not to emit its reasoning
  as response text. Procedural, not extractive.

**The counterintuitive root cause — DOS doctrine is the opposite of the
trigger.** The pack uses *narrate / narration* pervasively (every file), but
always to describe the **skill's role relative to the kernel**: "the kernel
decides; the skill narrates", "witness, never narrate", "do NOT fold the
*narrated* claim". That is the docs/138 distrust thesis — *don't trust the
agent's self-report* — which is structurally the inverse of "show your
reasoning in the response". The `--narrated` CLI flag (`dos improve`,
`witness_effect`) carries the worker's claim and is **parsed for nothing**. So
the very doctrine that makes DOS DOS also keeps its skill prose on the right
side of the Fable refusal — by construction, not by luck.

The shipped-generic-skill litmus (a skill names no host) and this audit are
complementary: neither the host nor the model's internal reasoning is ever a
thing a DOS skill asks the agent to surface.

## Axis 2 — over-prescription: candidates identified, thin-down DEFERRED (measured)

The dispatch/loop/supervise/fleet skills are long step-numbered procedures.
Lines per skill, descending — the four over the ~250-line over-prescription
threshold:

| skill | lines |
|---|---|
| dos-goal-fleet | 329 |
| dos-goal-gate | 297 |
| dos-dispatch-loop | 296 |
| dos-witness-claim | 251 |

The guide's claim is that step-by-step prescription that *helped Opus can hurt
Fable*. But #172 itself sets the discipline: **"Keep the verdict-call contracts
(those are load-bearing); thin the surrounding narration"** and **"Measure,
don't guess … keep the thinner version only if a witness confirms equal-or-
better outcomes."** This is a `dos-self-improve`-shaped change — a candidate is
KEPT only if a witness (a Fable before/after on a representative skill, e.g.
`dos-dispatch-loop`) confirms equal-or-better outcomes.

Thinning these on faith, without that A/B, would (a) violate the issue's own
"measure, don't guess" gate and (b) risk degrading the load-bearing verdict-call
contracts the skills exist to carry. So Phase 1 records the candidates and the
measurement method; the thin-down is a measured follow-on, not a faith edit.

### What a Phase-2 thin-down would target (not yet applied)

The steps that exist to compensate for *prior-model* failure modes Fable handles
by default — over-planning guards, scope-drift fences, "do not narrate options
you won't pursue" framing — are the thinning candidates. The verdict-call
contracts (`dos arbitrate` before fan-out, `dos verify`/`commit-audit` on the
fold, `dos gate` for the loop decision) are **load-bearing and stay verbatim**.
The witness for keeping a thinner version: a Fable run of the representative
skill scores equal-or-better on outcome (the same `dos-self-improve` KEEP rule).

## Provenance

Audit performed against the guide as of 2026-06-16. Sweep commands and counts
are reproducible from `src/dos/skills/*/SKILL.md`. Complements #122 (compaction
re-grounding) and the witness/verify rungs; distinct in that those are about
*not believing* narration, while this is about the *skill prose itself* not
tripping the Fable refusal or over-steering.
