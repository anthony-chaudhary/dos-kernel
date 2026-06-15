# DOS and the alternatives

> When people find DOS they ask, reasonably: "how is this different from my
> eval platform / my framework's guardrails / Temporal / in-toto / plain CI?"
> This page answers that the way [FastAPI's alternatives page](https://fastapi.tiangolo.com/alternatives/)
> does: generously. Every tool below is good at its job. Several of them
> taught DOS something, and DOS ships integrations for more than one. The
> point of this page is not that you should use DOS *instead* of them — it is
> to show which job each tool actually does, so you can see the one job none
> of them do, which is the only job DOS does.

DOS is a small deterministic kernel that adjudicates **completed agent work**
from **evidence the agent did not author**: git ancestry, exit codes, file
trees, read-backs of the world. `dos verify` answers "did this actually
ship?" from git history, never from the agent's "done." `dos commit-audit`
checks a commit's *claim* against its own *diff*. `dos arbitrate` referees
which agents may touch which files. It is a referee, not an orchestrator: it
runs beside everything on this page, and most rows below end with the two
composing.

**The one question to carry through this page** — ask it of every tool here,
including DOS: *when this tool says "OK," what evidence did it read, and who
wrote that evidence?* Everything below sorts cleanly by its answer.

---

## Hosted evals and observability (LangSmith-class)

Evaluation and observability platforms like
[LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts) are
how you find out whether your agent is any *good*: offline evaluation
against curated datasets, LLM-as-judge and pairwise scoring, human
annotation queues, code evaluators ("deterministic, rule-based functions"
in LangSmith's words), and online evaluation that scores live production
traffic in real time. If you are iterating on prompts, comparing model
versions, or watching quality drift in production, this is the right
tooling and there is no DOS substitute for it — DOS has no opinion about
whether your agent's output is *good*.

**What DOS adds.** Look at the input. An evaluator — human, code, or LLM
judge — scores the *run*: the inputs, outputs, and intermediate steps your
application emitted ([LangSmith's evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
describe exactly this contract). That is the right input for quality
questions. But for the question "did the agent actually do what it claims?",
the run is the wrong witness, because the claim under test is *part of the
run* — the agent authored it. DOS's verdicts deliberately read nothing the
agent emitted: `dos verify` reads git ancestry, `dos commit-audit` reads the
diff the commit machinery recorded, `dos reward` admits a trajectory into a
training set only when a witness the agent didn't author confirms its claim.

**Use both.** Evals tell you the work is good; DOS tells you the work is
real. A practical split: score quality on your eval platform, and gate
"believed done" on a DOS exit code.

## Framework guardrails (OpenAI Agents SDK, CrewAI)

Both big agent frameworks give a task's output a typed checkpoint, and both
designs are genuinely well made. The
[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/guardrails/)
runs input and output guardrails alongside your agents; when one detects a
violation it trips a tripwire that raises a typed exception and halts the
run. [CrewAI task guardrails](https://docs.crewai.com/en/concepts/tasks)
"validate and transform task outputs before they are passed to the next
task": a guardrail function returns `(True, result)` or
`(False, "error")`, and a failed validation sends the task back for retry.
These are the right seams in the right places — a structural admission that
an agent's output should be checked before anyone downstream believes it.

**What DOS adds.** The seat is right; the question is what sits in it. A
guardrail that checks the output's *form* — schema, safety, content — is
still reading text the agent wrote. DOS likes these seams so much that it
ships a driver for each ([docs/305](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/305_guardrail-seat-pair-openai-agents-and-crewai-plan.md)):
an Agents SDK output guardrail and a CrewAI task guardrail, one import line
each, that check the output's *claim* — "I committed X", "I created Y" —
against a read-back the agent didn't author. The CrewAI retry loop then
retries until the work is *done*, not until the narration *parses*.

**Use both.** Keep your content and schema guardrails; add the effect-check
guardrail behind them. The frameworks built the checkpoint; DOS supplies the
one check the agent can't talk its way past.

## Durable execution (Temporal-class)

[Temporal](https://docs.temporal.io/temporal) guarantees durable execution:
every step of a workflow is recorded in an event history, and when a worker
crashes the execution "resumes from the last recorded event," so your code
runs effectively once and to completion even across failures. For
long-running, must-not-be-lost processes this is the engineered standard,
and DOS's own recovery design learned from it — the `resume` syscall and its
intent ledger are the same write-ahead instinct applied to agent runs (and
Temporal's replay-testing discipline is one this repo is adopting for its
own journals).

**What DOS adds.** Durable execution makes the *execution* trustworthy: the
event history faithfully records that each step ran and what each step
*returned*. Whether a returned value's claim about the world is *true* is a
different question, outside durability's scope — if an agent step returns
"deployed successfully," the history durably and correctly records that the
step said so. DOS adjudicates exactly that residue: the claim against the
world, from evidence outside the claimant. The natural composition is a
validation step that runs a DOS verdict before the workflow believes a
claimed effect.

**Use both.** Temporal makes sure the work *survives*; DOS makes sure the
claimed work *happened*.

## Supply-chain attestation (in-toto, witness)

[in-toto attestations](https://github.com/in-toto/attestation) are
"verifiable claims about any aspect of how a piece of software is produced":
a signed Statement binds an artifact (the subject) to a typed Predicate, so
a consumer can verify provenance instead of trusting a release's word.
[witness](https://github.com/in-toto/witness) runs this during the build —
it creates "an audit trail for your software's entire journey through the
software development lifecycle," gathering evidence at each pipeline step
and verifying it against policy. Philosophically this is DOS's closest
relative on the page: both refuse to let the party doing the work be the
only author of the evidence about it.

**What's different.** Subject and scale. in-toto attests *pipeline steps* —
who ran what, with which materials and products — and brings real signing
infrastructure to make those claims portable across organizations. DOS
adjudicates *an agent's work claims* at runtime, inside one workspace, with
no keys and no infrastructure: a plain git repo is enough, because git
ancestry is already an archive the claimant can't rewrite quietly. One is
portable signed provenance for artifacts; the other is a live referee for
agents. They compose naturally — a DOS verdict is itself a claim-vs-evidence
record that could ride in an attestation predicate, and aligning the verdict
envelope with the in-toto Statement/Predicate layering is on the public
roadmap ([issue #70](https://github.com/anthony-chaudhary/dos-kernel/issues/70)).

**Choose in-toto/witness** when the question is "can a third party verify
how this artifact was built?" Choose DOS when the question is "is my agent
telling me the truth right now?"

## Plain CI and branch protection

Do not skip this row: required status checks are the one
evidence-authored gate almost everyone already runs. With
[branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches),
GitHub refuses the merge until the required checks pass — a deterministic
verdict, computed by machinery the author doesn't control, enforced at a
choke point. That is the same trust shape DOS is built on, and for plenty of
setups it is genuinely sufficient (see the next section).

**What DOS adds.** CI guards one path — the merge — at one time — the end.
An agent fleet's false claims mostly happen before and beside that path: an
agent reports "committed and pushed" when no commit exists, two agents
overwrite each other in one working tree, a loop burns all night without
landing anything, a commit's message claims work its own diff doesn't
contain. None of those ever reach a pull request. DOS runs the same
exit-code discipline at the work surface itself — `verify`, `arbitrate`,
`liveness`, `commit-audit` — while the work is happening. And because every
DOS verdict is an exit code, CI is also where DOS plugs in: the repo ships a
[GitHub Action](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md)
that runs `dos commit-audit` on every PR and posts the verdict as a required
status check — this very repo gates on it.

**Use both.** Keep CI as the merge floor; add DOS where the agents actually
work.

## Legal citation checkers (CiteCheck-class, Westlaw/Lexis verifiers)

A wave of tools now checks AI-generated legal citations against real case law:
commercial citation-validation engines and the verification features built into
the big legal-research platforms. They are good at what they do — they scan a
finished brief at document scale, cross-reference a managed, licensed caselaw
corpus, and many reach toward the harder question of whether a case is still
good law. If your need is post-hoc review of a completed document against a
comprehensive paid corpus, that is the right tool and DOS does not replace it.

**What DOS adds.** Look at *where* and *when* the check runs, and *who* it runs
for. Those tools scan the document *after* it's written, for the lawyer
reviewing it. DOS's `citation_resolve` is the same existence-and-quote check
aimed *inside the agent that writes the cite* — a free, open-source MCP tool (or
exit-code command) the legal agent calls at the moment it emits a citation, so a
fabrication is refused *before* it becomes a paragraph. It resolves against a
third-party reporter the model didn't author (CourtListener / Free Law Project),
needs no API key to abstain safely, and is deliberately Tier-1: it witnesses
existence and quote-fidelity, and **abstains** on whether the case supports your
argument — the over-claim that, in this domain, is a liability. The mechanism is
reproducible at $0 ([`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md)),
not a number you have to take on faith.

**Use both — and choose a commercial tool when** you need argument-level review
(is this good law? does it support the proposition?), managed-corpus breadth
beyond CourtListener's coverage, or a turnkey product for non-engineers. Choose
DOS when you are *building* the legal agent and want the cheap, non-forgeable
existence rung wired in at emit time, before the irreversible act of filing.
The [sourced walkthroughs](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md)
are in the answer corpus.

---

## When NOT to use DOS

The honest section. DOS earns nothing by being installed where it isn't
needed.

- **One agent, reviewed diffs, good CI.** If a single agent works under your
  eyes and every change goes through a PR you actually read, your review plus
  required checks already provide the independent witness. A referee for one
  honest player is overhead.
- **Fully isolated agents merging through a gated queue.** If each agent
  works in its own sandbox or worktree and integration happens only through
  CI-gated merges, the collision half of DOS (`arbitrate`) has little to do —
  isolation already serialized the effects. (The verification half can still
  matter; see the commit-message case above.)
- **You need hard prevention, in-band.** DOS decides and reports; it blocks
  an action only where a host exposes an enforcement hook (Claude Code
  hooks, a CI required check, a framework guardrail seat). If your
  requirement is "this write must be physically impossible," you want a
  sandbox or a policy engine in the execution path, not (only) a referee.
- **Your question is quality, not truth.** "Is this code good / safe /
  on-style?" is eval and review territory. DOS never grades correctness or
  quality — it grades whether a *claim* is *witnessed*. If nobody is making
  checkable claims, there is nothing for it to adjudicate.

## The map at a glance

| Tool | Gates what | Reads what | When |
|---|---|---|---|
| Evals / observability | quality of outputs | the run the app emitted | offline + online |
| Framework guardrails | one task's output | the output text (DOS's drivers: a read-back) | at the checkpoint |
| Temporal-class | execution progress | its own event history | continuously |
| in-toto / witness | artifact provenance | signed step attestations | per pipeline run |
| Plain CI | the merge | the checks' exit codes | at the PR |
| Legal citation checkers | a finished brief's cites | a managed, paid caselaw corpus | post-hoc, for the reviewer |
| **DOS** | **belief in "done"** (incl. *does this cited case exist?*) | **git ancestry, exit codes, read-backs, a third-party reporter — never the agent's narration** | **during and after the work** |

Every row above is a tool this repo either uses, integrates with, or learned
from. Start with the [README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md)
for what DOS itself does, the
[FAQ](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/FAQ.md)
for the arriving questions, and the
[fleet-framework cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-fleet-frameworks.md)
for the per-framework recipes (LangGraph, CrewAI, AutoGen, the Agents SDKs).

*Citations checked 2026-06-15 against each project's primary documentation.
Spotted a claim about your project that's wrong or stale?
[Open an issue](https://github.com/anthony-chaudhary/dos-kernel/issues) — this
page holds itself to the standard it describes.*
