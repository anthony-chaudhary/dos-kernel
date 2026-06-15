# How do I make any agent skill verify its own work?

> Don't rewrite the skill — bolt a witness onto it. `pip install dos-kernel`,
> then the `dos-skillify` skill reads any `SKILL.md` and emits an additive
> `-dos` copy whose every "done / shipped / found" step shells a `dos` verb and
> reads the verdict, instead of trusting the agent's own word. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

A skill is a screenplay: prose that tells an agent how to do a job. Most
screenplays trust the worker — they conclude "the fix shipped" because the agent
said it committed, "the goal is met" by re-reading the agent's own transcript,
"all 7 workers returned" by `filter(Boolean)`. In every case the part deciding
ground truth *is* the part being judged. That is consistency, not grounding.

You do not have to rewrite the skill to fix this. You add a **trust layer**: at
each place the skill sets a belief bit, it asks a witness the agent did not
author — git ancestry that a phase shipped, a CI run's own `conclusion` field, a
file read-back — and branches on *that*, not on the narration. The job logic, the
domain steps, the taste are untouched. The diff is small and every hunk is
traceable to one trust-claim.

`dos-skillify` does this conversion mechanically: it parses the source skill into
**claim-sites**, classifies each as ADD / REPLACE / GUARD / LEAVE, and writes a
new `<name>-dos/` copy plus a re-derivable report — never overwriting your
original.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
# then, in an agent with the DOS plugin, run the skill:
#   "use dos-skillify to make ./my-skill DOS-aware"
```

The converter reads your workspace once (`dos doctor --json`) so the seams it
adds name *your* lanes and plan grammar, not a baked-in literal — which is what
lets one converter produce a skill that runs unchanged on a host with different
lanes.

## What it actually adds, per claim-kind

The conversion is not ad-hoc: each belief-bit a skill can set maps to one witness
rung. This is the closed taxonomy `dos-skillify` walks:

| The step concludes… | What replaces the self-check |
|---|---|
| "(plan, phase) / this code shipped" | `dos verify` — read the rung (git ancestry), never the bare claim |
| "this commit did what its subject says" | `dos commit-audit` — the subject vs. its own diff |
| "keep working until the goal is met" | `dos hook stop` — wired alongside the harness goal, refuses a self-certified stop |
| "a CI / workflow run concluded green/red" | the run's own `conclusion` field, never the run-log narration |
| "I'm editing these files (no collision)" | `dos arbitrate` over the tree **before** writing |
| "all N fan-out workers returned a real result" | `dos verify-result` + `dos coverage --declared N` |
| "this recalled memory is still true" | `dos recall` — re-verify at read time |
| taste / intent / future work | **nothing — left as prose; no witness exists, faking one is the anti-pattern** |

That last row is the load-bearing one. A claim with no honest witness is **not**
turned into a fake `dos` call — it is recorded `UNWITNESSABLE` and surfaced in
the report. A converter that manufactures a check it can't actually ground would
be the exact failure DOS exists to fix.

## The output is admitted by witness, not by say-so

The converter is itself a judged agent. It does not get to *claim* "I made this
skill DOS-aware." It produces:

- the additive `<name>-dos/SKILL.md` — each edited hunk carries a trailer like
  `<!-- dos:R1 verify — was: "I committed it, so it shipped" -->`, so a reviewer
  sees *why* every hunk exists and what it replaced;
- a `CONVERSION.md` report — one row per claim-site, re-derivable from the diff
  without trusting the converter.

Then it admits its own output the same way it grounds everything else:

```bash
dos commit-audit --workspace . HEAD   # the diff did the KIND of thing claimed
```

## What this does — and does not — certify

It grounds the **trust seams** and nothing else. It does not make the skill
smarter, change its job, or judge whether the domain work is correct — only
whether each "done" bit rests on a witness or on the agent's word. Two honest
boundaries:

- **Inline gating is not the novel part.** Several agent frameworks already block
  a step inline; what is unusual here is *what* the gate reads — an
  independently-authored effect (did the commit land?), not the agent's own
  output re-scored, not a content-safety classifier, not an offline eval on a
  logged trace.
- **It only grounds what has a witness.** If a step's "done" has no checkable
  effect (a matter of taste, a future intention), the honest output is an
  abstain in the report, not a check. The skill degrades gracefully if `dos`
  isn't installed — the witness steps become advisory and the original job still
  runs, so the converted skill never hard-depends on the kernel.

This was dogfooded on this repo's own 18 skills first: the 14 already-grounded
skills came out **all-LEAVE** (the converter doesn't churn a skill that already
asks a witness), and the sweep surfaced one missing taxonomy row (`CI_GREEN`),
which was added — the honesty-check loop working as designed.

## Sources / reproduce

- [`src/dos/skills/dos-skillify/SKILL.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/skills/dos-skillify/SKILL.md) — the converter skill itself.
- [`docs/345`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/345_skill-to-dos-conversion-and-the-free-on-ramp.md) — the full design: the conversion procedure, the politics of a new-copy PR, and the benchmark.
- [How to make an agent prove the work, not self-certify](make-an-agent-prove-the-work-not-self-certify.md) — the same rule at the loop level.
- [Deterministic hook vs. agent skill — which enforces?](deterministic-hook-vs-agent-skill-which-enforces.md) — where a skill's witness should sit.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — consistency vs. grounding.

> The kernel is the part that doesn't believe the agents.
