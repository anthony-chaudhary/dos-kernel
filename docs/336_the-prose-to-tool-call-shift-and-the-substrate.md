# 336 — The prose-to-tool-call shift: why fewer words make the substrate matter more

> **The trend, stated plainly.** Each model generation emits *more tool calls and
> less prose*. The thinking moves out of the visible paragraph and into the
> sequence of actions. The operator's directive in this very repo —
> [`CLAUDE.md`](../CLAUDE.md)'s "Prefer tool calls over prose (HIGH PRIORITY)" —
> is the same pressure, applied by hand, to a model that already trends that way.
> This note asks the question that directive provokes: **as agents narrate less
> and act more, what happens to the layer whose whole job is to not believe the
> narration?**

This is a theory note in the family of [`102`](102_when-to-trust-an-agent.md)
(when to trust an agent — reports vs. commitments),
[`138`](138_what-is-truth-the-throughline.md) (truth is the un-authored effect,
never the narration), and [`333`](333_verification-as-steering-and-the-verification-first-harness.md)
(verification is the steering wheel). Those say *don't read the prose; read the
effect.* This one takes the next step in time: **the prose is going away on its
own.** It carries no litmus and ships no mechanism; it is a forecast and its
consequences, built from first principles and checked against the kernel that
already exists.

The verdict in one line, so the rest can earn it: **the shift is, on net, the
strongest tailwind DOS has — because a layer that reads effects and ignores
narration is exactly the layer that keeps working when narration thins out — but
it converts two of DOS's *advisory* surfaces into *load-bearing* ones, and opens
one genuinely new failure mode (the silent multi-call collision) that did not
exist when agents narrated every move.**

---

## 1. What is actually changing — the unit of agent output

Be precise about the trend before reasoning from it. A model turn produces two
kinds of token: *prose* (text addressed to a human or to its own scratchpad) and
*tool calls* (structured actions addressed to the world). The historical mix was
prose-heavy: an agent explained what it would do, did one thing, explained the
result, asked permission. Each generation shifts the mix:

- **Capability.** Better tool-use training means the model needs less natural-
  language scaffolding to plan; the plan lives in the call sequence itself.
- **Economics.** Prose addressed to a human is overhead the moment the human
  stops reading every line; a fleet operator reads *outcomes*, not transcripts.
- **Harness pressure.** Directives like this repo's "act, then report" actively
  suppress narration — and the model complies, because compliance is rewarded.

The end state of the trend is an agent that emits a long sequence of tool calls
with almost no connective prose: `read`, `grep`, `edit`, `run`, `edit`, `commit`
— and a one-line summary at the end, if that. **The agent's reasoning has not
disappeared; it has changed address.** It used to be deposited in paragraphs a
human could read. Now it is deposited in the *choice and order of the calls* — a
form a human reads far less easily, and a machine reads far more easily.

That last sentence is the whole note. Legibility is not vanishing; it is moving
from the human channel to the machine channel. The question for any oversight
layer is: *were you reading the channel that is growing, or the channel that is
shrinking?*

---

## 2. Why this is a tailwind — DOS was never reading the prose

The single most important fact about DOS under this trend is structural, not
lucky: **the kernel's one invariant already forbids it from reading narration.**
[`138`](138_what-is-truth-the-throughline.md)'s throughline is that a verdict
stands on an effect whose *author is not the judged agent* — git ancestry, the
diff footprint, the OS exit code, the lease journal. The agent's own words were
never an input to a verdict; they were demoted at the door to a hint that must
clear a non-forgeable checkpoint. The hook observation surface that records what
the fleet did
([`hook_observation.py`](../src/dos/hook_observation.py)) writes *env-authored*
records — verb, outcome, reason_class — and its own docstring is blunt that "no
agent narration" enters the count.

So play the trend forward against that design:

- **A prose-reading oversight layer degrades as prose thins.** Anything that
  watched the agent by reading its explanations — a human skimming the
  transcript, an LLM-judge summarizing "what the agent said it did," a
  guardrail keyed on natural-language intent — loses signal in exact proportion
  to how much the agent stops explaining. Its sensor is wired to the shrinking
  channel.
- **An effect-reading oversight layer is *invariant* to prose volume.** DOS
  reads git, the exit code, the lease WAL. Those signals do not get quieter when
  the agent narrates less — a commit is a commit whether the agent wrote three
  paragraphs about it or none. **The kernel's sensor is wired to the channel that
  is growing.** The trend does not erode DOS; it erodes DOS's *substitutes*.

This is the deepest true thing in the note, and it is worth stating as a law:

> **The legibility-relocation law.** As agent output shifts from prose to tool
> calls, oversight that reads narration loses signal and oversight that reads
> effects holds it. A trust layer's durability under the trend is decided by
> which channel its sensor was wired to — and that was decided at the
> foundation, not tunable later.

DOS wired its sensor to effects on day one, for reasons ([`102`](102_when-to-trust-an-agent.md),
[`138`](138_what-is-truth-the-throughline.md)) that had nothing to do with this
trend. The trend now pays that decision back. **The substrate does not merely
survive the prose-to-tool shift; it is the thing the shift makes necessary**,
because the shift removes the human-readable narration that was the *only* other
oversight channel most stacks had.

---

## 3. Why this is also a sharpening — three advisory things become load-bearing

A tailwind that only flattered the design would be a self-report
([`102 §5`](102_when-to-trust-an-agent.md) set this honesty bar). The same trend
that validates the sensor placement *raises the stakes* on parts of DOS that were
comfortable being approximate, because the prose they could lean on is gone.

### 3.1 The human can no longer skim the transcript as a backstop — so the verdict is the *only* oversight

Today, even with DOS running, a human operator's last line of defense is reading
the transcript: a paragraph that says "I'll delete the old config and regenerate
it" gives a human a chance to intervene *before* the effect. That backstop is a
prose backstop. As prose thins, **it stops existing** — there is no paragraph to
skim, only a `run rm` call buried in a sequence of forty calls that a human
cannot follow in real time.

The consequence is not subtle: **the trend removes the informal human safety net
and leaves the kernel's verdict as the load-bearing oversight, not a supplement
to it.** A surface DOS treated as "advisory, the human will also be watching"
inherits the full weight the human used to share. Concretely, this raises the bar
on the pre-effect surfaces ([`89`](89_the-lane-is-a-region-lock.md)'s region
lock, [`102 §5`](102_when-to-trust-an-agent.md)'s scope gate): when a human could
read "I'm about to edit `auth.py`" they were a redundant check on the scope gate;
when they cannot, the scope gate is the *whole* check. The trend converts
defense-in-depth into a single point, and that point is the kernel.

### 3.2 Intent must be recovered from calls, not read from prose — which makes the intent ledger non-optional

[`117`](117_completion-as-a-verdict-the-end-of-working-in-passes.md)'s intent
ledger — *declared* extent recorded up front, kept apart from *verified* — was
already the right design. The trend makes it the *only* design, for a reason
specific to thinning prose. When an agent narrated, a reader could reconstruct
intent from the explanation: "I'm doing X because Y." When the agent only acts,
**intent is no longer stated anywhere a reader can find it** — it is implicit in
the call sequence, and inferring "what was this agent *trying* to do" from a
sequence of edits is itself an unreliable, narration-like act of interpretation.

So the residual ([`333 §3`](333_verification-as-steering-and-the-verification-first-harness.md))
cannot be computed by reading what the agent said it would finish; it can only be
computed against a *commitment recorded before the agent started acting*. A
harness that relied on end-of-run narration to learn the agent's intended scope
has nothing to read when the narration is gone. **The intent ledger stops being a
nicety for crash recovery and becomes the only durable record of what the agent
was for** — because the agent has stopped telling anyone.

### 3.3 The forgeable rung gets *more* tempting, not less

[`138 §"Where truth is still forgeable"`](138_what-is-truth-the-throughline.md)
concedes the weak rung: an agent that can write a commit can write one whose
*subject* matches the stamp grammar (`--allow-empty` on the right SHA satisfies
`grep-subject`). Under the prose trend, this rung gets worse in a specific way.
When agents narrated, a forged stamp had to coexist with prose a human might read
and find suspicious ("it says shipped but the explanation is empty"). As prose
thins, **the forged stamp is the entire record** — there is no surrounding
narration to contradict it, because there is no surrounding narration. The cheap
lie loses its only witness-of-last-resort. This is not new in kind (the rung was
always forgeable), but the trend removes the redundant human check that made the
forgery riskier, which is the same dynamic as §3.1 aimed at the weak rung
specifically. The repair is the same one [`85`](85_extending-the-verifiable-surface.md)
always pointed at — harden the binding rung to artifact/execution grade — but the
trend moves it up the priority list, because the human who used to catch the
empty commit is no longer reading.

The synthesis of §3: **the trend does not break anything DOS built. It removes
the human-prose redundancy that was silently sharing the load with three of DOS's
surfaces — pre-effect admission, the intent ledger, and the binding rung — and
hands each of them the full weight.** Things that were fine being advisory while a
human also watched are now the only thing watching.

---

## 4. The genuinely new failure mode — the silent multi-call collision

Most of §3 is "old risk, higher stakes." This section is a risk that *did not
exist* in the prose-heavy regime and is created by the trend itself, which makes
it the most important part of the note.

When agents narrated, concurrency was partly self-pacing. An agent that said
"I'll now edit the auth module" before editing it gave *other* agents — and the
operator — a beat to notice an impending collision. The narration was an informal
announcement protocol: slow, lossy, but real. Two agents about to touch the same
file usually said so before they did, and the overlap was visible in prose before
it was a conflict in the tree.

Strip the prose and that informal protocol is gone. **The first observable sign of
a collision is now the colliding write itself** — two agents emitting `edit
auth.py` calls within milliseconds, with no announcing paragraph in between,
because announcing paragraphs no longer exist. The trend compresses the window
between "about to collide" and "collided" from "however long the agent spent
narrating" to "zero." Concurrency gets *faster and quieter at the same time*,
which is precisely the condition under which an informal, prose-mediated
coordination scheme fails silently.

This is the one place the trend creates work rather than merely re-weighting it,
and it lands exactly on DOS's pre-effect admission story
([`89`](89_the-lane-is-a-region-lock.md), [`102 §5`](102_when-to-trust-an-agent.md)).
The conclusion is sharp:

> **Under thinning prose, region-lock admission stops being a defense against
> *malicious* overlap and becomes the *only* coordination signal between
> concurrent agents at all** — because the informal prose announcement that used
> to coordinate them no longer happens. `arbitrate` over the lease journal was
> designed as a correctness floor; the trend promotes it to the *entire*
> coordination layer, the thing standing where agent-to-agent narration used to
> stand.

And it sharpens the apply-gate argument that [`335 §3.1`](335_tcp-for-agents-validating-the-reliability-analogy.md)
and [`114 §F`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md)
already make. Those notes argue DOS needs a pre-effect *write* chokepoint to earn
the "makes actors produce trustable effects" verb. The prose trend is an
independent reason for the same chokepoint: when there is no narrated warning
before a bad or colliding write, *post-effect detection is strictly too late in
a way it was not before*, because there is no longer a prose early-warning to
make detection a redundant second line. The trend raises the value of the gate
DOS has not built yet — which is the most actionable thing this note produces.

---

## 5. The second-order risk — the watcher is the watched

One failure mode deserves its own section because the trend makes it likelier and
it attacks DOS at the root. As agents act more and explain less, the natural
response across the industry is to add *another model* to watch the first one —
an LLM monitor that reads the call stream and flags trouble, precisely because a
human no longer can (§3.1). This is the right instinct aimed at the wrong layer,
and DOS's own framing says why.

[`333 §5`](333_verification-as-steering-and-the-verification-first-harness.md)'s
last limit and [`138`](138_what-is-truth-the-throughline.md)'s locality point
both name it: a verifier generated by *the same kind of process it is verifying*
closes the loop back onto the plant. An LLM watching an LLM's tool calls is a
plant-grade sensor — it can be wrong in the correlated way the actor is wrong, and
worse, it is reading the *call stream*, which the acting model increasingly
optimizes (the trend *is* the acting model getting better at producing call
streams that look like good work). **The thing the monitor reads is the thing the
actor is being trained to make persuasive.** That is the narration problem
reincarnated one level up: the tool-call sequence becomes the new narration — a
channel the actor authors and the watcher reads — and a watcher that trusts it is
back to believing a self-report, just a structured one.

DOS's discipline is the antidote, and the trend makes the antidote matter more:
**do not put the trust in a model reading the call stream; put it in the
un-authored effect the calls produce.** A tool-call sequence that *claims* to have
fixed the bug is still narration, however structured; the fix is in the diff and
the green test, which the acting model does not author. The trend's pressure to
"add a model to watch the calls" must be resisted in favor of "read the effects
the calls produced," for exactly the reason DOS has always given — and the trend
makes the wrong path more tempting, because the call stream *looks* like ground
truth in a way prose never did. It is not. **A structured self-report is still a
self-report.** The shift from prose to tool calls does not change the byte-author
invariant; it disguises it, by making the self-report look like telemetry.

---

## 6. What DOS should do about it — the consequences, ranked

This note ships no mechanism, but a forecast that named no actions would be
idle. The trend implies a priority ordering, and it is mostly a *re-ranking* of
work DOS already knew it wanted:

1. **The apply-gate moves up (§4, §5).** The pre-effect write chokepoint that
   [`114 §F`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md) /
   docs/119 reserve and [`335 §6`](335_tcp-for-agents-validating-the-reliability-analogy.md)
   says earns the "makes … produce" verb is now justified *twice over*: once as
   the enforcement point the TCP analogy needs, and once as the replacement for
   the prose early-warning the trend deletes. When agents stop announcing bad
   writes, catching them before they land stops being a luxury.

2. **Region-lock admission is now coordination, not just safety (§4).**
   `arbitrate` should be treated as the primary inter-agent coordination channel
   under thinning prose, and its ergonomics priced accordingly — it is no longer
   a correctness backstop behind an informal prose protocol; it *is* the protocol.

3. **The intent ledger becomes mandatory, not recovery-only (§3.2).** A dispatch
   that does not record declared extent up front has, under thinning prose, *no*
   recoverable statement of intent anywhere. The ledger is the only place the
   agent's purpose is written down once the agent stops writing it down in prose.

4. **Harden the binding rung sooner (§3.3).** The forgeable `grep-subject` rung
   loses its prose witness-of-last-resort; the artifact/execution rungs
   ([`85`](85_extending-the-verifiable-surface.md)) should become the default
   faster than they otherwise would.

5. **Refuse the "add a model to watch the calls" reflex (§5).** The right
   response to illegible-to-humans call streams is *effect-reading*, not a
   second model reading the calls. This is a stance, not a feature, but it is the
   one the trend will pressure hardest and the one DOS is uniquely positioned to
   hold.

None of these is a new invention. That is the point: **the trend does not demand
DOS become something else; it raises the priority of exactly the things DOS was
already building, and removes the human-prose redundancy that was letting it
defer them.** A layer designed to read effects and distrust narration is the
layer the prose-to-tool shift makes essential — and the same shift is the alarm
clock telling DOS which of its unbuilt gates to build first.

---

## 7. The synthesis (one paragraph)

Each model generation emits more tool calls and less prose, so the agent's
reasoning relocates from paragraphs a human reads into a call sequence a machine
reads — legibility does not vanish, it moves from the human channel to the
machine channel. A trust layer's fate under that move is decided by which channel
its sensor was wired to, and DOS wired its sensor to the un-authored effect — git,
the exit code, the lease journal — on day one, for reasons that predate the trend;
so the trend is a tailwind, eroding DOS's prose-reading *substitutes* (the human
skimming the transcript, the LLM-judge summarizing intent) while leaving the
kernel's effect-reading verdict untouched, because a commit is a commit whether or
not the agent narrated it. But the same disappearing prose was a silent
redundancy sharing the load with three of DOS's surfaces — pre-effect admission,
the intent ledger, the binding rung — and the trend hands each of them the full
weight, turning advisory into load-bearing; it deletes the informal prose
announcement that used to coordinate concurrent agents, promoting region-lock
admission from a safety floor to the *entire* coordination signal and creating the
new silent-collision failure mode; and it tempts the industry to bolt a second
model onto the call stream, which is the byte-author invariant violated in a new
disguise, because a structured self-report is still a self-report. The right
response is the one DOS already holds — read the effect, not the call stream — and
the trend's gift is an alarm clock: it raises the priority of the apply-gate, the
mandatory ledger, and the hardened rung that the kernel already knew it wanted,
and tells it which to build first.

---

## 8. See also

- [`102_when-to-trust-an-agent.md`](102_when-to-trust-an-agent.md) — reports vs.
  commitments; §5 is the prose backstop the trend deletes (§3.1).
- [`138_what-is-truth-the-throughline.md`](138_what-is-truth-the-throughline.md)
  — the byte-author invariant that makes DOS's sensor prose-volume-invariant (§2),
  and the locality point §5 leans on.
- [`333_verification-as-steering-and-the-verification-first-harness.md`](333_verification-as-steering-and-the-verification-first-harness.md)
  — the control-theory frame; §5's co-resident-verifier limit is this note's §5.
- [`335_tcp-for-agents-validating-the-reliability-analogy.md`](335_tcp-for-agents-validating-the-reliability-analogy.md)
  — the apply-gate (§3.1 there) that this note's §4/§6 independently re-motivate.
- [`117_completion-as-a-verdict-the-end-of-working-in-passes.md`](117_completion-as-a-verdict-the-end-of-working-in-passes.md)
  — the intent ledger that §3.2 promotes from recovery-only to mandatory.
- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) /
  [`114_prior-art-audit-where-the-branding-outruns-the-mechanism.md`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md)
  — region-lock admission (§4) and the apply-gate §F (§6) the trend re-ranks.
- [`85_extending-the-verifiable-surface.md`](85_extending-the-verifiable-surface.md)
  — hardening the forgeable rung (§3.3), moved up the priority list by the trend.
