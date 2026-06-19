---
name: dos-goal-gate
description: "Ground a keep-working goal in evidence the worker did not author by wiring `dos hook stop` to refuse false done claims. Use for one self-stopping agent or loop worker; use `dos-witness-claim` for fold barriers."
---

# dos-goal-gate — gate "I'm done" on a witness, not on self-report

> **A completion condition the agent checks against its own narration is the
> exact failure DOS exists to fix.** When an agent decides "the goal is met" by
> re-reading what it just wrote, the part deciding ground truth *is* the part
> being judged — that is **consistency, not grounding** (docs/138). This skill
> never decides done-ness by re-reading the transcript. It turns the goal into a
> set of checkable EFFECT claims, then wires the Stop decision so the agent
> cannot stop until a read-back whose **byte-author is not the agent** (git
> ancestry, an OS exit code, a fresh state read) corroborates each effect. The
> kernel decides; the skill narrates.

The shape is domain-free: **state the goal as effects → wire the Stop gate →
on each stop, witness every effect → stop only when all are corroborated.** The
*policy* (which plan grammar, which lanes, where state lives) is data read from
`dos doctor --json`, never literals this screenplay hardcodes.

**Why this is not just "ask the model if it's done."** A harness stop-condition
("keep going until X") is evaluated by a model reading the session — so a
confident, fluent narration of an X that the world does not corroborate ends the
work early (the silent fail of docs/177). The gate below replaces that judgment
with a verdict over evidence the agent did not write: a phase is done when *git*
says a commit backs it, not when the agent says it committed; a file exists when
a *fresh read* finds it, not when the agent says it wrote it.

## When to use this (and when not)

- **Use it** when a single agent self-paces toward a stated goal and you want its
  "done" to be unfalsifiable-by-narration — a `/loop` worker, a self-stopping
  task agent, or any run where the operator stated a completion condition.
- **Use `dos-witness-claim` instead** when many workers' results are folded at a
  `parallel()`/`pipeline()` barrier — that skill is the *fan-out fold* analogue;
  this one is the *single-agent self-stop* analogue. Same witness discipline,
  different site.
- **This is advisory (a PDP, not a PEP — docs/99).** It computes a Stop verdict
  and lets the runtime act on it via the user-owned hook seam. It blocks a *false*
  done; it never blocks a *true* one, and it never enforces beyond the Stop hook.

## Inputs

- The operator's **goal** — the completion condition in plain words ("the auth
  refactor is shipped and the suite is green", "the migration plan's phases are
  all landed"). Free prose is the *input*, never the *test*.
- Optionally, a **transcript path** for the running session (the Stop event
  carries it; you rarely pass it by hand).

## Step 0 — Discover the workspace layout (one call)

Run the doctor verb and read the result. **Every path / lane / exit-code below
comes from here, never a literal.**

```bash
dos doctor --workspace . --json
```

The fields this skill uses:

- `exit_codes.verify` — `{shipped, not_shipped, contract_error}`. Branch on these
  for a **git-phase** effect (Step 2a), never on parsing prose.
- `exit_codes."verify-result"` — `{healthy, unreadable, dead, contract_error}`
  (`dead=3`). Branch on these for the **terminal-state** witness when a transcript
  is in play.
- `paths.plans_glob` / `lanes` / `stamp` — the host's plan grammar, lane taxonomy,
  and ship-stamp convention, if a goal names a `(plan, phase)`.
- `git` — if `false`, the git-existence witness (Step 2a) has no history; every
  git-phase effect resolves `source="none"`. Say so; do not silently pass it.
- `runtime_hooks` — which runtimes already have a `dos hook …` Stop entry wired
  (so you know whether Step 1 still needs doing).

## Step 1 — Wire the Stop gate (once per workspace)

The gate is `dos hook stop`: a Stop / SubagentStop hook that refuses to let the
agent stop while a confidently-CLAIMED phase is NOT corroborated by git. Wire it
into the runtime's settings — idempotent, merged into any existing hooks, never
clobbering the operator's own:

```bash
dos init --with-hooks --workspace .      # Claude Code (the default runtime)
# cross-runtime: dos init --hooks <runtime> --workspace .   (preview with --dry-run)
```

This binds three hooks; the load-bearing one here is **Stop → `dos hook stop`**.
On every stop it extracts the `(plan, phase)` the agent claimed and refuses the
stop (feeding the verdict back as the next instruction) if git does not back it.
That refusal IS the grounded form of "keep working until the goal is met": the
work cannot end on the agent's word that a phase shipped.

> **Two Stop hooks, opposite triggers — do not conflate them.** `dos hook stop`
> blocks a *false done* (the agent claimed a phase, git disagrees). `dos hook
> marker` (docs/259) blocks a *premature give-up while a keep-alive budget is
> unspent*, then gets out of the way. A host may wire both; this skill is about
> the first. If your goal is "don't stop polling yet," that is `dos hook marker`,
> not this gate.

### Step 1a — How this composes with the harness `/goal` command

The Claude Code `/goal <condition>` command is itself a **session-scoped Stop
hook — but a *model-evaluated* one**: a fast model re-reads the session after each
turn and rules on whether the condition holds. That is precisely the
consistency-not-grounding shape this skill exists to fix — the judge is a model
reading the agent's own narration, with no witness the agent did not author.

You do **not** replace `/goal`; you wire `dos hook stop` (Step 1) **alongside**
it, and the harness combines them for you. Claude Code runs every matching Stop
hook in parallel and applies an **ANY-block** rule: *the stop is refused if **any**
hook blocks it* (a `{"decision":"block",…}` at exit 0, or exit 2). So with both
active, the agent may stop only when the `/goal` model-judge **AND** the grounded
git gate both allow it — a logical AND of "the narration reads done" and "ground
truth backs it." The grounded gate can only ever *add* a refusal the model-judge
missed; it never loosens `/goal`. That is the whole win: keep using `/goal` for
its fluent, free, prose-level check, and let `dos hook stop` veto the case where
the prose says done but git does not corroborate it.

> **The two hooks are independent — the gate does not read the goal text.** A
> settings.json Stop hook receives the session/transcript, not the `/goal`
> condition string (the harness exposes no goal field to hooks). So `dos hook
> stop` does not "check the goal"; it independently checks that every phase the
> agent *claimed shipped* is backed by git. The alignment between the two is the
> operator's job: state the goal as the same checkable effects the gate verifies
> (Step 2), so "the model thinks it's done" and "git backs every claimed phase"
> converge on the same finish line. Do not write the gate as if it parses the
> goal — it parses the agent's claims and asks git.

### Step 1b — The Fable-5 guide's "check your last paragraph" is the advisory form of this gate

Anthropic's Claude Fable 5 prompting guide has a section, **"Rare cases of early
stopping,"** for a known failure: deep in a long session the model can end a turn
on a text-only statement of intent ("I'll now run X") without the tool call, or
pause to ask permission it already has. Its fix is a **prompt**:

> Before ending your turn, check your last paragraph. If it is a plan, an analysis,
> a question, a list of next steps, or a promise about work you have not done
> ("I'll…", "let me know when…"), do that work now with tool calls. End your turn
> only when the task is complete or you are blocked on input only the user can
> provide.

That is the **advisory** version of this skill, and `dos hook stop` is the
**enforced** version that subsumes it. Same failure, opposite ends: the guide asks
the model to re-read its OWN last paragraph (consistency, not grounding — the same
shape Step 1a flags for `/goal`); the gate refuses the stop until a witness the
agent did not author backs the claimed effect. The prompt lowers the *frequency* of
"I'll now run X" early stops; the gate makes them *impossible to get away with*.

So a kernel reader migrating to Fable does **not** need to add the guide's prompt as
a separate safeguard — the witness gate already covers that case. It is still fine
to keep the prompt as **defense-in-depth**: it cuts the early stop before it happens,
the gate catches the one that slips through. (An "I'll now run X" turn with no tool
call and no shipped effect is exactly the unwitnessed-claim case the gate refuses —
the operator sees a structured "your goal isn't witnessed yet, keep going," not a
generic block.)

## Step 2 — State the goal as checkable effects (the decomposition)

A goal is met when its **effects** are present in the world. Translate the prose
goal into a list of named, checkable effects — and **abstain rather than invent**.
An effect you cannot name an identifier for is not a passable goal; it is a
**NO_CLAIM** to surface for the operator, never a test you fabricate (docs/134).

Map each effect to its witness rung:

| Effect the goal asserts | Witness (byte-author ≠ agent) | Verb today |
|---|---|---|
| "(plan, phase) is shipped" | git ancestry + ship-stamp grammar | `dos verify` (Step 2a) — SHIPPED |
| "this terminal result is real" (a transcript is in play) | harness authorship marker | `dos verify-result` — SHIPPED |
| "I created file X / inserted row Y / sent message Z / deployed" | a fresh GET / state-diff / OS exit / counterparty record | **no CLI verb** (Step 2b) — Python-API gap |

The first two are shipped CLI verbs. The third is the open seam — handled
honestly in Step 2b. The goal's "done" is the **conjunction** of all its effects'
witnesses: every one CONFIRMED/SHIPPED, or the goal is not met.

### Step 2a — git-phase effects: ask the truth syscall

For each `(plan, phase)` the goal names, never trust "I committed it" and never
grep commit subjects yourself:

```bash
dos verify --workspace . <PLAN> <PHASE> --json
echo "exit=$?"
```

Read the `ShipVerdict` `{shipped, source, sha?}`, branch on `exit_codes.verify`,
and **read the rung**:

- `source: "registry"` — the strongest git ship; a ship row exists. Effect met.
- `source: "grep-subject"` — a commit *subject* carried the phase token. SHIPPED,
  but weaker (a subject can flip the verdict even if little was built). Met, but
  mark the rung; do not treat it as equal to `registry`.
- `source: "none"` — no positive git evidence. The agent CLAIMED the phase but git
  does not corroborate it. **The effect is NOT met** — this is the narrated
  success the world does not back. The goal is unmet; keep working.

This is grounding because git is a witness the agent did not author: a commit's
existence in ancestry is a fact about the repository, not about the agent's prose.

### Step 2b — created-file / DB-row / sent-message effects: the read-back gap

**There is NO `dos verify-effect` (or `dos witness`) CLI verb.** The
effect-witness join is shipped in the kernel as a **Python API**, not a command
(exactly as `tool_stream` has no `dos tool-stream` verb — see EXAMPLES.md Recipe
5). So for any effect outside git, document the pattern and **log the gap at
runtime**, rather than pretending a verb exists:

```python
from dos.claim_extract import extract_claims          # (a) abstain, never invent
from dos.effect_witness import EffectClaim, witness_effect
from dos.evidence import gather_evidence, resolve_evidence_source  # (b) read-back

# (a) Extract the claim from the agent's terminal text. extract_claims fires only
#     on an explicit ID-shaped token or a DOS-CLAIM: marker — free prose yields [].
#     That [] is a NO_CLAIM (surface it), not a met effect.

# (b) Gather a read-back from a witness the agent did not author. A real witness
#     (a fresh HTTP GET, an OS exit code, a state-snapshot diff) lives in a driver
#     registered under the `dos.evidence_sources` entry-point group; resolve it by
#     name and run it at the boundary:
source = resolve_evidence_source("<your-witness-name>")   # built-in `null` if none wired
facts = gather_evidence(source, subject="<effect key>", config=cfg)

# (c) The effect is met ONLY on CONFIRMED — the join is capped by the read-back's
#     accountability (believe_under_floor): a forgeable same-surface read-back
#     (AGENT_AUTHORED) is recorded but structurally CANNOT grant belief.
verdict = witness_effect(EffectClaim(key="<effect key>", narrated="<what agent said>"),
                         (facts,))
# verdict.verdict -> CONFIRMED | REFUTED | UNWITNESSED | NO_CLAIM
# verdict.believe -> True ONLY on CONFIRMED
```

The load-bearing rule, enforced structurally by `believe_under_floor`: a read-back
on the forgeable floor (`AGENT_AUTHORED` — the agent re-reading its own surface, a
pasted receipt, an mtime) is recorded but **structurally incapable** of granting
belief. So the worst a lying same-surface read-back can do is be ignored, never
manufacture a CONFIRMED. **Log the gap, never silently skip it:** the first time a
non-git effect needs witnessing and no `dos.evidence_sources` driver is wired,
emit a one-line `log` naming the unwitnessed effect — surface it up, never launder
it into "goal met."

## Step 3 — The gate's verdict is the stop decision

With the gate wired (Step 1), the runtime already enforces the git-phase rung on
every stop. Your job in the screenplay is to keep the agent's claimed effects and
the goal's effect-list aligned, and to read the gate's verdict honestly:

- **All effects CONFIRMED / SHIPPED** → the goal is met. The Stop hook emits
  nothing (`{"checked": N, "ok": true}` under `--json`) and the agent stops. This
  is the only path to "done."
- **Any effect `source: "none"` / REFUTED** → the goal is unmet. `dos hook stop`
  emits `{"decision": "block", "reason": "…"}`, the runtime declines to stop, and
  the reason is fed back as the next instruction — *keep working*. The agent does
  not get to overrule this by asserting completion again.
- **Any effect UNWITNESSED / NO_CLAIM** → you cannot say the goal is met. Surface
  it for the operator; do not let the agent stop on an effect no accountable
  witness reached. (The git-phase rung fails *safe* — a `source:"none"` is treated
  as not-shipped, so the gate keeps working rather than passing an unproven claim.)

You can check the gate's reasoning out-of-band, before relying on it, by feeding a
synthetic Stop event:

```bash
echo '{"transcript_path":"<session.jsonl>"}' | dos hook stop --workspace . --json
# {"ok": false, "reason": "DOS verify: you claimed … shipped, but git has no commit backing it …", "results": [...]}
#   ok:false  → the goal is NOT met; the agent will be kept working.
#   ok:true   → every claimed phase is corroborated; the agent may stop.
```

This is the same out-of-loop check as `dos plan --once` / `dos commit-audit`: a
verdict over ground truth, run from outside the loop that wrote the claim.

## Anti-patterns

- ❌ Deciding "the goal is met" by re-reading the transcript / the agent's last
  message. That is consistency, not grounding — the agent judging its own bytes.
  Decompose into effects and witness each one.
- ❌ Treating "I committed the phase" as the phase shipping. Run `dos verify`; a
  `source:"none"` means git does not back the claim, regardless of the narration.
- ❌ Grepping commit subjects yourself to confirm a ship. The ship-stamp grammar
  is `dos.toml` `[stamp]` data the oracle reads — ask `dos verify`, do not
  re-implement the rung (a subject is forgeable; let the oracle weigh the rung).
- ❌ Letting the agent stop on an UNWITNESSED / NO_CLAIM effect "because it
  probably worked." Abstain surfaces it; it is never a met effect.
- ❌ Wiring `dos hook marker` when you mean this gate (or vice versa). Marker
  bounds keep-alive polling; this gate refuses a false done. Opposite triggers.
- ❌ Naming a specific lane, plan dir, ship prefix, or witness backend as a
  literal — read the active layout from `dos doctor --json` and resolve witnesses
  by name.

## The one rule under this skill

"The goal is met" is a **claim with a byte-author**. Let the agent stop only when
a witness whose byte-author is *not the agent* corroborates every effect the goal
named. The agent's confidence and its fluent description of success are irrelevant
to that gate — which is the whole point: the part that decides the goal is done is
never the part being judged.
