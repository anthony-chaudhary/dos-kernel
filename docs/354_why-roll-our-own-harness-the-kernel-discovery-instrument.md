# 354 — Why roll our own harness: the kernel-discovery instrument, not the product

> **Status:** theory / strategy note. No litmus, no mechanism. It carries one
> falsifier (the plugin-boundary test, §4) that a later note or example could
> turn into a checkable claim, and it corrects one over-claim in the `leet`
> product sketch ([`products-leet-fleet-harness.md`](products-leet-fleet-harness.md)).

> **Operator question (2026-06-15), verbatim.** *"From a design perspective what
> really is the benefit of rolling our own harness? To understand challenges and
> be ready as things move? Or real value-add that would be a 10x better harness?
> Or something else?"*

This note answers it. The short version: the phrase "roll our own harness" hides
**three different bets**, and they are worth wildly different amounts. The
romantic one — *a 10x-better harness* — is the weakest. The unglamorous one —
*the harness as the instrument that proves the kernel is load-bearing and tells
us which primitive to build next* — is the strongest, and it is the one DOS's own
docs already half-know but have not stated plainly. The job of this note is to
state it plainly, and to draw the one design consequence that follows.

It sits in the harness-strategy family alongside
[`333`](333_verification-as-steering-and-the-verification-first-harness.md) (the
control-loop reframe — a verifier-bolted-on loop and a verification-first loop are
a *fork, not a spectrum*), [`338`](338_token-efficiency-as-a-first-class-harness-primitive-and-the-interconnect.md)
(spend is a verdict the host treats as a meter), and
[`352`](352_skill-span-the-wall-the-harness-wont-build.md) (the host won't author
the per-skill span — so we fence it ourselves). Those three are the evidence; this
note is the verdict over them.

---

## 1. The three bets the phrase hides

"Roll our own harness" is not one decision. It is three, stacked:

| Bet | What you're buying | Honest worth |
|---|---|---|
| **A — Option value** ("be ready as the field moves") | Insurance against the harness layer commoditizing in a way that strands the kernel. | Real, but **cheap to buy and mostly already owned** (§2). |
| **B — The product** ("a 10x-better harness") | A coding harness people prefer to Claude Code / Codex / Cursor. | **The seductive trap.** A commodity war against teams with 100× the headcount (§3). |
| **C — The instrument** ("something else") | The dogfood that proves the kernel is load-bearing, and the integration surface that *generates* the kernel's roadmap. | **The strongest reason, and the underrated one** (§4). |

The mistake is to argue about B while quietly meaning C. The rest of this note
ranks them and draws the design line C implies.

---

## 2. Bet A — option value is real, but you already paid for it

The "be ready as things move" instinct is sound: the harness layer is moving fast,
and you do not want the kernel stranded by a seam you never understood. But notice
what the option actually requires. It requires *understanding the seams* — where
isolation lives, where the verifier bolts on, where token accounting leaks — **not
shipping and operating a full harness.**

And the kernel has already extracted that understanding, from outside, without
owning a harness:

- [`352`](352_skill-span-the-wall-the-harness-wont-build.md): the host fires
  PostToolUse on the *decision to load* a skill, not its execution, and bills
  tokens only at SessionEnd, aggregated — so the host structurally cannot author a
  clean per-skill `(duration, tokens)`. We learned the seam by *hitting* it.
- [`338`](338_token-efficiency-as-a-first-class-harness-primitive-and-the-interconnect.md):
  the host reads the token meter and forwards it to the invoice; it does not treat
  spend as a verdict the loop can steer on. We learned the seam by *naming the gap*.
- [`333`](333_verification-as-steering-and-the-verification-first-harness.md): a
  loop with a verifier stapled to its exhaust and a loop built verification-first
  are different machines — a fork, not a tuning. We learned the seam *conceptually*.

That is the option, **already held.** Building a full harness to keep learning the
seams is buying the same insurance twice. Bet A does not justify the build; it is a
by-product the kernel already harvests by integrating *into* the commodity harness,
not by replacing it.

---

## 3. Bet B — "10x-better harness" is a commodity war the kernel doesn't need to fight

This is the bet to push back on hardest, precisely because it is the one that feels
like ambition.

A coding harness is a **horizontal commodity** with the best-funded teams in the
field shipping into it monthly. The `leet` sketch is itself honest about this when
it surveys the landscape: *isolation by worktree / container / microVM is a solved,
commoditized layer*, and *verification by test-execution + CI-as-judge + human PR
review is the standard oracle* (leet §1). So "10x better" **cannot** mean a better
loop, better isolation, or better UX — you will lose that race to people with two
orders of magnitude more headcount, and the surface you'd have to build to compete
(terminal, permissions, MCP plumbing, IDE integration, model routing) is almost
entirely **undifferentiated lift**: work that makes you *equal*, never *better*.

There is exactly one axis on which a 10x is defensible, and it is not a harness
axis. It is the axis nobody else treats as first-class: **a loop that structurally
cannot believe the agent.** Read the `leet` pitch for what is actually 10x in it —
*admission before the write*, *can never declare "done" on its own say-so* — and
every one of those is **DOS the kernel**, not the harness wrapped around it. The
harness is the delivery vehicle; the differentiation is one layer down. Which is
the whole point of §4.

> **The §3 verdict.** "Build a 10x harness" is a category error. The harness is a
> commodity; the kernel is the differentiation. Aim the 10x at the kernel and let
> the harness be thin.

---

## 4. Bet C — the harness as a kernel-discovery-and-dogfood instrument

Here is the reason that actually justifies a build, and it is two reasons braided.

**4.1 The harness is the honest dogfood.** You cannot credibly sell *"the kernel
that does not believe the agent"* if your own fleet runs on a harness that does.
A unit test proves a verdict is *correct in isolation*; only a real loop proves a
verdict is *load-bearing* — that the loop's control flow actually bends to it, that
a DENY actually stops a write, that a NOT-shipped actually refuses a "done." The
harness is the one place that happens. That is a credibility asset a competitor
cannot fake by reading the README, because it lives in whether the loop *obeys* the
kernel, not in what the loop *says*.

**4.2 The harness is the integration forcing-function that writes the kernel's
roadmap.** This is the deeper half. Look at where `352` and `338` came from: someone
tried to use the kernel *inside a real harness loop* and hit a wall where the host
does not author the fact the kernel needs. Each wall became a kernel primitive —
the skill-span fence, the spend-as-verdict rung — that **you would never have
discovered from the outside.** The harness is the thing that generates the kernel's
backlog: every seam where the commodity host won't author the truth is a leaf the
kernel should grow. Without a harness you are guessing which primitive matters next;
with one, the loop tells you by stalling.

> **The §4 verdict.** The harness is worth building **as an instrument, not as a
> product** — a dogfood that makes the kernel's verdicts load-bearing, and a probe
> that surfaces the next primitive by hitting the wall the host won't build.

---

## 5. The crux, and the design line it draws

The tension underneath the operator's question:

> **A harness is a distribution channel for the kernel; it is not the product.**

Frame it as *our harness vs. Claude Code* and you have entered a commodity war you
can't win and don't need to fight (§3). Frame it as *the reference loop that proves
the kernel is load-bearing, and the integration surface that tells us what to build
next* (§4), and it is the highest-leverage thing you could build — because it is the
only place the kernel's central claim gets **exercised** instead of **asserted**.

So the design answer is not "build a 10x harness." It is:

> **Build the thinnest possible harness that forces every one of the kernel's
> verdicts to be load-bearing in a real fleet loop — and treat it as a
> kernel-discovery instrument and a dogfood proof, not as a competitor to the
> commodity layer.**

This is the shape `leet` already describes ("a controller, not an agent with a
verifier stapled to its exhaust"). But `leet` **over-claims** when it bills itself
as *"the first coding harness whose loop is a closed-loop controller"* (leet §0, and
echoed in its closing summary).
It is not competing on harness-ness, and saying so invites exactly the commodity war
§3 says to avoid. The honest pitch is one layer down: *the harness is how the kernel
earns the right to be believed in a real loop.* The differentiation is the kernel;
the harness is its proving ground. (Recommended edit to the `leet` note: keep the
controller framing, drop "first coding harness" for "the reference loop that proves
the kernel is the controller.")

---

## 6. The falsifier — the plugin-boundary litmus

A strategy note with no way to be wrong is a slogan. Here is the test that keeps
this one honest, and it doubles as the build's go/no-go:

> **The plugin-boundary falsifier.** If the *only* things that make the harness
> better than the commodity host are things you could also ship as a Claude-Code
> plugin / MCP server / hook pack, then you did not need a harness — you needed
> better *packaging of the kernel into the harness everyone already uses.*

And most of the kernel already crosses that boundary cleanly: `arbitrate`,
`verify`, `commit-audit`, the pre/post-tool sensors — **DOS ships them as a plugin
and MCP server today.** So the harness is justified **only for the parts of the
kernel a plugin boundary cannot reach.** As of this note, those are exactly two:

1. **Pre-write admission that gates the *actual* write.** A hook can DENY a write,
   but it cannot *schedule the fleet* — decide which of N agents gets the lane,
   admit the write *before* it happens as part of the loop's own dispatch. A plugin
   observes the write; the harness *owns* it. (This is the `arbitrate`/`lease`
   primitive `leet` §1 calls the first uncommoditized hole.)
2. **A loop whose control flow *is* the verdict.** A plugin lets the host's loop
   *consult* a verdict; a verification-first harness makes the verdict the loop's
   *steering term* — the §1-of-`333` distinction between a sensor the plant reads
   and a controller that closes the loop. You cannot retrofit that through a hook;
   it is the loop's architecture.

Those two are real, plugin-unreachable, and they are the **entire** case for the
build. Everything else — every verdict that already rides the plugin/MCP boundary —
ship as a plugin and **skip the harness for it.** If a proposed harness feature
fails the §6 falsifier (i.e., it could be a plugin), that is not a feature of the
harness; it is a packaging task for the kernel, and it belongs on the other side of
the line.

---

## 7. The one-paragraph answer

The benefit of rolling our own harness is **not** a better harness (a commodity war
the kernel can't win and needn't fight) and **not** primarily option value (already
bought by integrating *into* the commodity host). It is that the harness is the only
place the kernel's central claim — *a loop that cannot believe the agent* — stops
being asserted and starts being **load-bearing**: it is the honest dogfood, and it
is the forcing-function that discovers the next kernel primitive by hitting the wall
the host won't build. Build it thin, build it as an instrument, and build only the
two things a plugin can't reach — admission that owns the write, and a loop whose
control flow is the verdict. Everything else is a plugin.
