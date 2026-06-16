# Medium launch article

> For **anthony-chaudhary.medium.com**. Written as the sequel to "Building an AI
> Factory: Scaling Autonomous Development to 18,000 Commits in 30 Days"
> (Oct 2025) — that piece documented the failure patterns; this one ships the
> referee. Voice matched to that article: thesis in sentence one, short named
> sections, metrics forward, a candid weak-spots section, CTA close.
>
> *Drafted 2026-06-12 against v0.25.0 (PyPI live). Numbers trace to
> `paper/sections/*.html` + `paper/meta.py` — re-check before posting. The
> CLI transcript is verbatim `dos quickstart` output from a real run.*
>
> **Posting notes (delete this whole block before publishing):**
>
> - **Verify the continuity claims read true to you.** The opening references
>   your October article and frames DOS as what that experience demanded. The
>   sentences are written so they assert nothing about *when* DOS was built —
>   only that the factory's failure patterns are the ones DOS adjudicates —
>   but you are the witness for that framing; adjust if it reads wrong.
> - **Images.** Medium does not render SVG. Use `docs/assets/loop-hero.png`
>   (3600×1920) as the header image, and screenshot the caught-lie cast
>   (`docs/assets/caught-lie-cast.svg` rendered in a browser) for the inline
>   figure. Alt text is in the README's `<img>` tags — reuse it.
> - **Tags (Medium allows 5):** AI, Software Engineering, Artificial
>   Intelligence, Open Source, AI Agents.
> - **Sequencing.** Publish this *before* the LinkedIn post so LinkedIn can
>   link it as the "read more". It does not burn the Show HN (HN submits the
>   repo URL, not this article). Per the timing note, HN's slot is its own
>   decision; this article has no slot sensitivity.
> - **Code blocks:** paste as Medium code blocks (triple-backtick in the
>   editor). They render monospace, no highlighting — already accounted for.
> - **The one rule** (from `docs/announce/README.md`): lead with the fleet
>   angle, keep every number exact (6 of 8, not "most"), concede the wrapper
>   and the transactional store, and never present `via none` without the
>   contrast frame. All four are already baked into the prose below — a
>   trimming pass must not cut the concessions.

---

# The Kernel That Doesn't Believe the Agents

*An AI agent will tell you it finished. The only fix that scales is a referee
that never reads the agent's story — it reads the evidence the work left
behind. I built that referee, and it's open source.*

Last October I wrote about running autonomous development at factory scale —
thousands of AI-generated commits in a month, dozens of agents working in
parallel. The most-quoted parts of that piece were the failure patterns: the
idiotic code next to the brilliant code, the planning penalties, the
infrastructure strain. But there is one failure underneath all of those, and I
didn't have a name for it yet.

It's this: **the agent's report of its own work is the one artifact you can
never trust, and at scale it is the only artifact anyone reads.**

This article introduces [DOS](https://github.com/anthony-chaudhary/dos-kernel)
— a small, open-source, deterministic kernel whose whole job is to not believe
the agents. It answers "did that actually happen?" from bytes the agent didn't
write: git history, the file tree, the clock, a database's own state. MIT
licensed, one `pip install`, works on a plain git repo with zero config.

## The failure mode nobody screenshots

Run a coding agent on a real task. It works for twenty tool calls, then
reports, cheerfully:

> "Done! Shipped the login endpoint and the password reset."

Sometimes that's true. Sometimes nothing landed — no commit, no diff, nothing.
And here is the part that took me longest to internalize: **you cannot catch
this by reading what the agent wrote.** There is nothing wrong with the
sentence. A confident lie and an honest "my commit silently failed" are
*byte-identical* in the transcript. The only place the difference exists is
somewhere the sentence can't reach — the git history.

With one agent, you catch it yourself, because you read the work before
trusting it. That discipline is exactly what stops scaling.

## Scale doesn't dilute the problem — it concentrates it

Run twenty agents and nobody reads everything. Each worker grades its own
homework, you believe the reports because what else is there, and the unchecked
failures pile up quietly. They have a taxonomy, and if you run fleets you will
recognize every entry:

- **The phantom done.** "All work completed" — and the commit doesn't exist.
  Found hours or days later, usually by a human who went looking for the
  feature.
- **The fake green.** "All tests pass" — and the test never imports the thing
  it claims to test, or asserts `true == true`. Found when production
  disagrees.
- **The overnight burn.** A loop narrates "making progress" for six hours. The
  bill arrives; the diff is empty. The only failure with a dollar sign printed
  on it.
- **The clobber.** Two agents touch the same files; a merge silently destroys
  one agent's work. Nobody notices until someone asks where the change went.
- **The poisoned handoff.** Agent B builds on agent A's false claim, and the
  failure surfaces three steps downstream — where it gets misdiagnosed as
  B's bug. The most expensive one, and the one that almost never gets
  attributed to its real cause.

None of these is loud. The codebase ends up *sorta* working, and nobody can
safely change it. That is the real ceiling on autonomous development — not
model capability. The factory's bottleneck is trust.

## The fix is not a better agent

Every fix I see proposed for this is some version of "make the agent more
honest" — better prompts, self-reflection, a second model reviewing the first.
All of it shares one flaw: it reads the agent's bytes. Asking a model to
confirm its own report measures whether the story is internally consistent,
not whether it's true. I think of it as the mirror-verifier trap.

The fix that works is older than any of this: **separate the worker from the
referee, and give the referee evidence the worker can't author.**

That's DOS. The smallest version is one command. An agent claims a unit of
work shipped — ask git, not the agent:

```
$ dos verify AUTH AUTH1
  SHIPPED AUTH AUTH1 d7e362f (via grep-subject)
  exit=0

$ dos verify AUTH AUTH2
  NOT_SHIPPED AUTH AUTH2 (via none)
  exit=1
```

The agent claimed both. Git backs one. The first claim has a commit behind it
— `SHIPPED`, exit 0. The second has nothing — `NOT_SHIPPED`, exit 1. The
`(via …)` suffix is the receipt for *how the kernel knows*: `via grep-subject`
means it found the ship-stamp in commit history; `via none` means it looked
everywhere it knows how to look and found zero evidence for the claim. What
the agent *said* never enters the computation.

And because **the exit code is the verdict**, you don't parse anything. A CI
step, a dispatch loop, or a stop-hook branches on it directly: replace the
line of your pipeline that trusts an agent's "done" with `dos verify`, and a
false claim can no longer land.

There's a discipline underneath this that's worth naming, because it's the
whole idea: a check is trustworthy exactly when **whoever wrote the bytes of
the evidence is not the agent being judged.** Git's ancestry qualifies — the
commit machinery wrote it. A database's own state hash qualifies. The
environment's tool output qualifies. The agent's prose does not, and neither
does another model's summary of the agent's prose.

## Why you can't wrap your way out

Checking a *single* agent is easy, and it's not the problem. Anyone can wrap
one agent in "if the check fails, run it again" in a few lines. But that
assumes the world holds still between the check and the act — and with
concurrent agents it doesn't. Your check reads true the moment agent A looks
and false by the time A writes, because agent B changed the shared state in
between.

Systems people will recognize this on sight: it's a time-of-check-to-time-of-use
race, moved off files and onto world state. They'll also know the textbook
cure — fuse the check and the act into one atomic step at the store: a
transaction, or a compare-and-swap. That cure is real, and where the shared
state offers it, a wrapper around even one agent closes the race with no
referee needed. I want to concede that plainly.

But agents in the wild don't get that primitive. They act through high-level
tool APIs — edit a git tree, file a ticket, cancel a reservation — that expose
only unconditional writes. The check and the act stay two separate calls with
a gap between them, and no number of re-runs makes two calls one. Nothing
bolted onto agent A fixes it. It takes a referee *between* the agents: one
that orders their writes and trusts none of their reports.

DOS is that referee too. Before touching files, each agent takes a *lease* on
a disjoint region of the file tree. The second agent asking for held territory
doesn't collide — it gets redirected to free, disjoint work, with the real
reason stated in a machine-readable refusal. The fleet's memory is a
write-ahead journal on disk, so the ordering survives any individual agent's
crash.

## By the numbers

I held one thing fixed — a check the agent cannot forge — and measured where
that verdict is actually worth spending. The answer surprised me enough to
become a paper.

**Handed back to the agent that produced the work, the verdict is worth
roughly nothing.** I ran a live bake-off of every in-loop fix: warn the agent,
rewind its bad turns, inject the environment's own correction. All flat to
negative. A capable agent already re-checks and fixes its own phantom; butting
in just disturbs a run that was going to pass. The one in-loop action that
survived is the negative one — give up on a doomed run when the environment
keeps repeating the same error. That gate earned **zero false halts across
1,634 winning runs** and saved about 11% of compute.

**Handed to the rest of the fleet, the same verdict pays — on both ways a
fleet fails:**

- **A race.** Two live agents sharing one reservation record through a
  customer-service benchmark: the naive flow let the second agent silently
  clobber the first. With the DOS arbiter ordering them, **6 of 8 lost-update
  clobbers were prevented** — checked against the environment's own database
  hash, a byte no agent wrote. A later live run reproduced it at 9 of 10 on
  fresh conflict pairs.
- **A claim.** One agent reports a write that never landed, and a downstream
  peer is about to inherit it as truth. The same referee, aimed here, is a
  write-admission gate: it blocked **10 genuine over-claims across 120 clean
  tasks** while admitting every correct write — at an identical 8.3% rate on a
  mid-tier and a strong model, so the signal doesn't fade as models improve.

And there's a third consumer that isn't an agent at all: **the loop that
trains the next model.** When a model is fine-tuned on its own runs scored
right-or-wrong, every banked over-claim is a poisoned reward label — a wrong
answer recorded as a win, teaching the model to over-claim *more*. Scoring
those rows against the byte the model can't fabricate purged every poisoned
label on the live rows (label purity went from 60% to 100%). And the label is
non-distillable by construction: no amount of training against a check that
reads only bytes the model didn't author can teach the model to move it.

So the boundary is the result: a verdict the agent cannot forge is *harmful*
handed back to that agent and *valuable* handed to anyone else — and "anyone
else" is the rest of the fleet and the training loop behind it.

## Where it's weak

I held myself to one rule throughout: state the strength of every claim in the
same breath as the claim. So:

- **The detectors are precise but low-recall.** Precision runs 88–98%, but on
  an offline, third-party-scored replay across 22 models they catch only
  ~2–7% of all failures — and that share shrinks on the strongest models.
  That low ceiling is exactly what pushes the verdict out-of-loop in the
  first place.
- **The downstream delta from blocking one phantom is ≈0 at the immediate
  hop**, because a capable consumer recovers on its own. I report the blocked
  count of real corruptions — never a downstream outcome delta — as the
  headline.
- **The training-loop result is a measured label purge.** Whether a model
  fine-tuned on the cleaned set actually over-claims less is set up but not
  yet measured through a weight update.
- **It verifies that a ship *happened*, never that the code is *good*.**
  Quality stays with your tests, your reviews, and you. DOS stops you from
  trusting a sentence over an artifact; it does not review the artifact.
- **The smallest version really is a smarter `git log --grep`.** I won't
  pretend the atom is exotic. What you're buying is the closed contract around
  it — a fixed verdict vocabulary, a provenance receipt on every answer, the
  same check working with or without a plan, exit-code-is-the-verdict — and
  the fleet surfaces (leases, liveness, the journal) that genuinely don't fit
  in one line of bash.

## What it looks like in your stack

DOS imposes no framework and runs beside whatever you already have:

- **Two commands, zero decisions.** `pip install dos-kernel`, then
  `dos init --hooks auto` from the repo where your agent works. It detects
  the agent runtime(s) you already use — Claude Code, Cursor, Codex CLI,
  Gemini CLI, Google Antigravity, Claude Cowork — and wires in the checks:
  your agent can't report "done" unless the work actually landed, two agents
  can't silently overwrite each other, and a stalled run gets flagged instead
  of quietly spinning. One config file, reversible by deleting the entries it
  prints.
- **CI:** a GitHub Action and a pre-commit hook gate on the same exit codes.
- **MCP:** a bundled MCP server exposes the syscalls as tools, so you can
  point your agent host at it and ask the agent to `dos_verify` its own last
  claim — the verdict still comes from git, not from the agent.
- **Frameworks:** guardrail adapters for the OpenAI Agents SDK and CrewAI sit
  on the same gate.
- **Python:** everything above is a thin shell over a pure library.

Adoption is deliberately graduated: **observe → warn → block → arbitrate.**
Install it observing and it just counts — how many claims it adjudicated, how
many "done"s had no evidence behind them. Your own counters, not my pitch,
justify each next step.

One more thing, because a trust tool should eat its own dog food: the DOS repo
is developed under DOS. Its own commits are audited by `dos commit-audit`
(does each commit message's claim match its own diff?), and its phases close
only when `dos verify` says git backs them — never on a "done" in a doc.

## Try it in 60 seconds

```
pip install dos-kernel     # the package name is dos-kernel — NOT `dos`
dos quickstart             # scaffolds a throwaway repo, plays the caught lie, cleans up
```

The quickstart plays out the exact story above: an agent claims two features
shipped, git backs one, and you watch the false claim exit `1`. Every line of
output is the real CLI, re-runnable on your machine.

Then run it against your own repo — `dos verify --workspace . PLAN PHASE` —
or just wire it in with `dos init --hooks auto` and let the counters
accumulate.

- **Repo:** [github.com/anthony-chaudhary/dos-kernel](https://github.com/anthony-chaudhary/dos-kernel) (MIT)
- **Install:** `pip install dos-kernel` — v0.26.0, 5,600+ tests, the only
  runtime dependency is PyYAML
- **Docs:** the README runs shallow → deep, and there's a no-code
  plain-words version up top

If you run more than one agent against the same codebase, I'd genuinely like
to know where it breaks for you. And whether you try it or not, I'll leave you
with the question that started all of this: **when your agent says "done" —
how do you currently know?**
