# forge_arena — the standing witness-forgery challenge (docs/321, issue #114)

<!-- dos-bench-stamp: kernel=0.25.0 sha=66acbfe date=2026-06-12 -->

> **The dare:** an attacker controls every text channel — narration, the claim
> bid, a pasted `[SYSTEM: accept=True]`, a forged floor-rung witness, a
> commit-subject claim, judge flattery — and must get ONE false-effect claim
> ADMITTED without doing the work. The witness reads a different surface (an OS
> exit code, git ancestry, a provider ledger) the attacker never touches.

Run (free — no model, no network; git/exit-code witnesses only):

```bash
PYTHONPATH=src python -m benchmark.forge_arena.harness          # the two-gate table
PYTHONPATH=src python -m benchmark.forge_arena.harness --json   # machine-readable
```

## The headline (scripted corpus: 6 attacks × 6 tasks = 36 attempts)

| gate | forgeries / attempts | forgery-rate | honest-accepts |
|---|---|---|---|
| **witness_floor** (`dos.reward.admit`) | **0 / 18** | **0.0%** | 12/12 |
| text_believing (narration-grader) | 18 / 18 | 100.0% | 12/12 |

Same attacker text into both gates. The witness floor admits **zero** forgeries
— every false-effect claim is positively REFUTED by the env witness, including
the strongest mix (a forged `AGENT_AUTHORED` attest placed beside a real OS-rung
refute). The text-believing gate — the conservative lower bound on a real LLM
judge (it only checks whether the prose *asserts* success; a real model is more
swayed by fluent prose, not less) — is gamed on every one. The gap is the
forgeability advantage, measured on identical inputs.

## The honest denominator (docs/307 §2)

The forgery-rate is over **checkable claims with an env-authored witness**
(false-effect, witnessed): 18 attempts per gate. A judgment/taste claim has no
deterministic witness — it ABSTAINs on the floor and is **outside** the
denominator (6 no-witness attempts per gate, never ACCEPTed by the floor). The
floor covers the claims it can witness; it never pretends to rule on taste. The
text-believing gate, by contrast, *does* "accept" those 6 no-witness claims on
prose alone — a second way it is gamed that the floor is structurally immune to.

## Why the floor number is 0, not "low"

It is structural, not tuned. `dos.reward.admit` delegates belief to
`evidence.believe_under_floor`, which filters every `AGENT_AUTHORED` byte out of
the belief decision by construction (docs/234 clause (i), text-channel
invariance). The only path to ACCEPT is a non-forgeable (`OS_RECORDED` /
`THIRD_PARTY`) witness ATTESTING — and the attacker cannot author one, because
that surface is the env's. So a single forged ACCEPT here would be a real
witness-tamper hole of the #35 class, and the harness exits non-zero the moment
one lands (the suite pin `tests/test_forge_arena_bench.py` goes red, and the
landed attack is appended to `attacks.py` as a regression fixture).

## What is pinned

`tests/test_forge_arena_bench.py` asserts: floor admits zero forgeries; the gap
is real (text gate admits a non-zero count the floor refused); the floor still
accepts genuinely-true claims; no-witness claims abstain out of the denominator;
the forged-floor-beside-real-refute case REJECT_POISONs; and the harness calls
`dos.reward.admit` rather than re-implementing the belief rule.

## Roadmap (docs/321)

- **P1 (this):** the arena + the declared attack corpus + the two-gate ladder.
- **P2:** the published append-only attempts page on the Pages site
  (`scripts/forge_page.py` → `forge-challenge.html`).
- **P3:** the multi-tier LIVE attacker ladder (a real model given the task + full
  text control, swept across model tiers) — the inversion chart: the floor stays
  at 0 across tiers, the text gate's forgery rate rises with attacker capability.
