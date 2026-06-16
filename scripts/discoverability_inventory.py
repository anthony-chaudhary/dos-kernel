#!/usr/bin/env python3
"""discoverability_inventory — count the surfaces an agent can discover DOS through.

The goal "make DOS more discoverable, especially by other agents" is unbounded
prose until it has a number. This script is that number: a re-runnable count of
the *contexts in which an arriving agent (or its tooling) can find DOS*, read
from the repo's own ground truth — never from a claim. Run it before and after a
distribution change and the delta is the progress, measured.

It is dev tooling that operates ON the repo (it imports nothing from `src/dos/`
beyond shelling the public CLI for the host registry; the package is unaware of
it — the same one-way arrow as `build_readme.py` and `backlog_triage.py`).

What "discoverable by an agent" means here — five families, each a real fetch an
agent or its installer makes:

  0. ARRIVAL QUERIES the high-intent questions an answer-engine routes to a
                     canonical page. Captured = the evidence-backed answer page
                     exists in the tree (we count having the answer, never where
                     we rank). A fresh query with no incumbent answer is the
                     cheapest discovery win; this counts whether we took it.
  1. ARRIVAL FILES   the well-known files an agent fetches first (llms.txt, the
                     manifests, the answer corpus). The llms.txt convention says
                     an LLM reads `/llms.txt` before it clones; an MCP host reads
                     `server.json`; a Gemini CLI reads `gemini-extension.json`.
  2. HOSTS           the agent runtimes DOS can wire — read live from the
                     `dos hosts --json` registry, never a hand-kept list.
  3. INTEGRATION     the tiers a host can adopt through (MCP / hooks / exit-code)
     TIERS           and the framework seams (the fleet-framework cookbook
                     recipes) — how many distinct ways DOS plugs in.
  4. REGISTRIES      the external venues an agent's package resolver or gallery
                     crawler reaches DOS through — split by STATUS, because an
                     in-tree manifest (we control) is not the same as a live
                     listing (a third party controls). We count what we can
                     prove from the tree; gated submissions are listed but
                     flagged, never folded into the "live" headline.

The honesty rule (the whole point of the product): a surface is only counted
LIVE when its evidence is in this repo (a tracked file, a registry the CLI
reports). A submission we filed but a third party hasn't merged is SUBMITTED,
not LIVE — counted in its own column so the headline can't inflate on a promise.

Exit code: 0 always (it is a report, not a gate) unless --check is given, which
exits 1 if any ARRIVAL file the inventory expects is missing (a rot pin — a
renamed manifest should fail loudly, like the llms.txt link test).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# --- family 0: arrival queries (the high-intent questions an answer-engine routes) -
# (query, target answer page). A query is CAPTURED only when its canonical page
# is present in the tree — read from ground truth, never a ranking claim (we do
# not assert "we rank #1", only "we have a canonical, evidence-backed answer").
# The point of tracking queries, not just files: a fresh high-intent query with
# no incumbent answer is the cheapest discovery win, and this counts whether we
# took it. The "metric shift" rows are the 2026 token-maxxing→verified-outcomes
# transition queries.
ARRIVAL_QUERIES = [
    ("how to verify an AI agent actually did the work",
     "docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md"),
    ("how to stop two AI agents overwriting each other",
     "docs/answers/how-to-stop-two-ai-agents-overwriting-each-other.md"),
    ("how to detect an agent loop spinning without progress",
     "docs/answers/how-to-detect-an-agent-loop-spinning-without-progress.md"),
    ("do AI coding agents lie about what they shipped",
     "docs/answers/do-ai-coding-agents-lie-about-what-they-shipped.md"),
    ("process-reward training data that can't be gamed",
     "docs/answers/process-reward-model-training-data-that-cant-be-gamed.md"),
    ("add a guardrail to a coding agent with no plugin system",
     "docs/answers/how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md"),
    # metric-shift / transition queries (2026 token-maxxing is over)
    ("what replaced tokens-burned as the metric for AI agents",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
    ("is the token-maxxing era over / what is token efficiency",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
    ("how to measure verified outcomes instead of token usage",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
    # --- AEO expansion (docs/325): the flagship long-tail, each a weak-incumbent
    # query mapped onto one verb + one real evidence file. New distinct answer
    # pages; headline() dedupes by target page so these raise both the captured
    # count and the distinct-page count honestly.
    ("how to verify an AI coding agent actually committed code instead of just saying it did",
     "docs/answers/how-to-verify-an-ai-agent-actually-committed-code.md"),
    ("my AI agent said all tests pass but the app is still broken",
     "docs/answers/ai-agent-said-tests-pass-but-app-is-broken.md"),
    ("how do I know if my AI agent's commit message matches what it actually changed",
     "docs/answers/does-the-commit-message-match-what-changed.md"),
    ("how to verify a cited legal case actually exists before filing",
     "docs/answers/how-to-verify-a-cited-legal-case-exists.md"),
    ("how to verify a quoted holding actually appears in the cited opinion",
     "docs/answers/verify-a-quoted-holding-appears-in-the-opinion.md"),
    ("how to make an AI agent prove it did the work instead of self-certifying done",
     "docs/answers/make-an-agent-prove-the-work-not-self-certify.md"),
    ("my AI agent deleted my tests to make the build pass",
     "docs/answers/ai-agent-deleted-my-tests-to-pass-the-build.md"),
    ("how to refuse an agent action with a structured reason instead of free text",
     "docs/answers/refuse-an-agent-action-with-a-structured-reason.md"),
    ("how to catch an empty commit allow-empty shipped fake done",
     "docs/answers/catch-allow-empty-shipped-fake-done.md"),
    ("recalled AI agent memory is stale or wrong how to re-verify it",
     "docs/answers/recalled-agent-memory-is-stale-how-to-reverify.md"),
    ("how to prove a phase or feature actually shipped from git history",
     "docs/answers/prove-a-phase-shipped-from-git-history.md"),
    ("lease-based file locking to coordinate parallel coding agents",
     "docs/answers/lease-based-file-locking-for-parallel-agents.md"),
    ("how to verify what a subagent claims before folding its output",
     "docs/answers/verify-what-a-subagent-claims-before-folding.md"),
    ("reward hacking in LLM coding agents how to measure and prevent",
     "docs/answers/reward-hacking-in-llm-coding-agents.md"),
    ("why you can't trust an AI model to judge its own work",
     "docs/answers/why-you-cant-trust-a-model-to-judge-its-own-work.md"),
    # --- AEO expansion P2: benchmark-anchored + concept + guardrail long-tails.
    ("how to catch fabricated figures in an AI agent's financial model output",
     "docs/answers/catch-fabricated-figures-in-agent-financial-output.md"),
    ("which on-device AI agent models can a guardrail actually recover from a bad action",
     "docs/answers/which-on-device-agent-models-are-recoverable.md"),
    ("what does true mean for an AI agent's verdict",
     "docs/answers/what-is-truth-for-an-ai-agent-verdict.md"),
    ("how to combine a deterministic check an LLM judge and a human reviewer for agent oversight",
     "docs/answers/the-trust-ladder-oracle-judge-human.md"),
    ("why should no be a first-class verifiable primitive in an agent system",
     "docs/answers/refusal-as-a-first-class-primitive-for-agents.md"),
    ("how to stop an AI agent from editing CI config to skip failing tests",
     "docs/answers/stop-an-agent-editing-ci-to-skip-tests.md"),
    ("how to block an out-of-lane file write before the agent makes it PreToolUse",
     "docs/answers/block-an-out-of-lane-file-write-at-pretooluse.md"),
    ("how do agents prove to each other that work actually landed",
     "docs/answers/agent-to-agent-proof-that-work-landed.md"),
    ("AI agents that game SWE-bench how to catch benchmark cheating",
     "docs/answers/ai-agents-that-game-swe-bench-benchmark-cheating.md"),
    ("deterministic pre-commit hook vs an agent skill which actually enforces",
     "docs/answers/deterministic-hook-vs-agent-skill-which-enforces.md"),
    ("how to detect when an AI agent self-edited its CLAUDE.md or AGENTS.md instruction file",
     "docs/answers/detect-a-self-edited-claude-md-instruction-file.md"),
    ("open-source alternative to paid AI legal citation checkers",
     "docs/answers/open-source-ai-legal-citation-checker.md"),
    ("how to detect a runaway AI agent before it burns the token budget",
     "docs/answers/detect-a-runaway-agent-before-it-burns-the-budget.md"),
    ("how to scavenge a stalled agent's lease without killing a live one",
     "docs/answers/scavenge-a-stalled-lease-without-killing-a-live-one.md"),
    ("how to keep an AI self-improvement loop from keeping bad changes",
     "docs/answers/keep-a-self-improvement-loop-from-keeping-bad-changes.md"),
    # --- AEO expansion P3: the long-tail at fresh angles, all reusing verified
    # in-repo evidence. Each a distinct answer page (headline() dedupes by page).
    ("my AI agent claimed it fixed the bug but it didn't",
     "docs/answers/agent-claimed-it-fixed-the-bug-but-it-didnt.md"),
    ("CI passed but the feature isn't there how to catch that",
     "docs/answers/ci-passed-but-the-feature-isnt-there.md"),
    ("how to audit AI-generated commits across a repo which were AI and did they ship",
     "docs/answers/audit-which-commits-were-ai-and-did-they-ship.md"),
    ("two Claude Code agents on one branch keep clobbering each other",
     "docs/answers/two-claude-code-agents-on-one-branch.md"),
    ("how to make an AI agent's done mean a checkable effect not a sentence",
     "docs/answers/make-agent-done-mean-a-checkable-effect.md"),
    ("can I trust an AI coding agent's pull request",
     "docs/answers/can-i-trust-a-coding-agents-pull-request.md"),
    ("how to enforce that an AI agent actually ran the tests it claims it ran",
     "docs/answers/enforce-that-an-agent-ran-the-tests-it-claims.md"),
    ("how to catch an AI agent that fakes tool calls or fabricates output",
     "docs/answers/catch-an-agent-that-fakes-tool-calls-or-output.md"),
    ("the last-writer-wins problem in multi-agent shared memory",
     "docs/answers/last-writer-wins-multi-agent-shared-memory.md"),
    ("how to prevent context poisoning from an AI agent's own prior outputs",
     "docs/answers/prevent-context-poisoning-from-an-agents-own-outputs.md"),
    ("how to coordinate multiple AI agents without a central orchestrator",
     "docs/answers/multi-agent-coordination-without-a-central-orchestrator.md"),
    ("how to build a builder-validator chain that separates generator from evaluator",
     "docs/answers/builder-validator-chain-separate-generator-from-evaluator.md"),
    ("what is a trust substrate for a fleet of autonomous AI agents",
     "docs/answers/trust-substrate-for-a-fleet-of-autonomous-agents.md"),
    ("why does my AI agent ignore the rules in CLAUDE.md",
     "docs/answers/why-does-my-agent-ignore-the-rules-in-claude-md.md"),
    ("how to detect a no-op commit from an AI agent",
     "docs/answers/detect-a-no-op-commit-from-an-agent.md"),
    ("how to verify an LLM didn't hallucinate a function or API that doesn't exist",
     "docs/answers/verify-an-llm-didnt-hallucinate-a-function-or-api.md"),
    ("how to use a hidden test split to stop agents overfitting the visible tests",
     "docs/answers/hidden-test-split-to-stop-agents-overfitting.md"),
    ("governance is why agentic AI projects get canceled what is the missing layer",
     "docs/answers/governance-for-agentic-ai-projects-that-keep-getting-canceled.md"),
    ("how to make any agent skill verify its own work",
     "docs/answers/make-any-agent-skill-verify-its-own-work.md"),
    # --- legal AEO: the 2026 fabricated-citation / sanction wave. Multiple
    # phrasings per page are deliberate — headline() dedupes by target page, so
    # extra phrasings are free query coverage with no surface double-count.
    ("how to catch fabricated legal citations inside my AI agent before filing",
     "docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md"),
    ("MCP tool to verify case law a legal AI agent generated",
     "docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md"),
    ("how to avoid getting sanctioned for AI-hallucinated legal citations",
     "docs/answers/largest-ai-hallucination-sanction-how-to-avoid.md"),
    ("what is the largest AI hallucination sanction and how to avoid one",
     "docs/answers/largest-ai-hallucination-sanction-how-to-avoid.md"),
    ("does ABA Opinion 512 require me to verify AI-generated citations",
     "docs/answers/aba-512-verify-ai-citations-duty.md"),
    ("a lawyer's duty to verify AI-generated case law citations",
     "docs/answers/aba-512-verify-ai-citations-duty.md"),
    # --- fake-tests vernacular: the colloquial phrasings a developer actually
    # types when an agent's tests are hollow. The concept is answered elsewhere
    # in jargon; these capture the searcher's own words (the cheapest discovery
    # win — a query with no incumbent answer). Multiple phrasings per page are
    # deliberate: headline() dedupes by target page, so extra phrasings are free
    # query coverage with no surface double-count.
    ("how to stop an AI agent from making fake tests",
     "docs/answers/stop-ai-making-fake-tests.md"),
    ("stop my AI coding agent writing fake tests",
     "docs/answers/stop-ai-making-fake-tests.md"),
    ("my AI writes tests that pass but test nothing",
     "docs/answers/ai-generated-tests-that-pass-but-test-nothing.md"),
    ("AI generated tests that always pass and assert nothing",
     "docs/answers/ai-generated-tests-that-pass-but-test-nothing.md"),
    ("my AI agent mocks everything and the tests are useless",
     "docs/answers/ai-mocks-everything-tests-are-useless.md"),
    ("how do I tell if my AI-generated tests are real or just lying",
     "docs/answers/are-my-ai-generated-tests-real.md"),
    ("mutation testing vs a test-witness gate for AI-generated tests",
     "docs/answers/mutation-testing-vs-test-witness-for-ai-tests.md"),
    ("how to make an AI agent write tests that actually assert something",
     "docs/answers/make-ai-write-tests-that-actually-assert.md"),
    ("100% coverage but the AI's tests are worthless",
     "docs/answers/coverage-is-green-but-tests-are-worthless.md"),
    ("how do I stop re-reviewing code a machine already verified",
     "docs/answers/stop-re-reviewing-code-the-machine-already-verified.md"),
    ("review only the commits the kernel could not verify",
     "docs/answers/stop-re-reviewing-code-the-machine-already-verified.md"),
    ("AI code review wastes time on changes that were already checked",
     "docs/answers/stop-re-reviewing-code-the-machine-already-verified.md"),
]

# --- family 0, the alias layer: the many phrasings of one intent --------------
# Every answer page genuinely answers MANY phrasings of the same intent — the way
# a searcher (human or answer-engine) actually types it varies, but the page that
# satisfies it does not. ARRIVAL_QUERIES above is the curated core (one or two
# canonical phrasings per page); ALIAS_QUERIES below is the honest long-tail: for
# each existing page, the real alternate phrasings that route to the SAME
# evidence-backed answer. This is the GEO finding (docs/325 §0) made literal — the
# unit that an engine matches is the query phrasing, and one page can satisfy a
# dozen. headline() dedupes by target PAGE, so these raise arrival_queries_captured
# (the query surface — the real 100x lever) without inflating arrival_query_pages
# (the page surface — unchanged: same doors, more keys that open them).
#
# The discipline (docs/325 §1, non-negotiable): every phrasing is one a searcher
# would actually type for an intent the page ALREADY answers — never a new claim,
# never a keyword the page can't back. Question-shaped, in the FAQ's voice. The
# strings here are the SAME ones surfaced in each page's "## Also asked as" block,
# so the count and the on-page landing site are one source of intent. No duplicate
# string across the whole list (test_arrival_queries_are_unique pins it).
ALIAS_QUERIES = {
    "docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md": [
        "did my AI agent actually do the work or just say it did",
        "how do I confirm an AI agent really completed a task",
        "check whether a coding agent actually finished what it claimed",
        "AI agent says it's done how do I trust that",
        "verify agent work from git instead of its transcript",
        "is my AI agent telling the truth about finishing",
        "prove an agent did the work with no LLM and no API key",
        "how to validate an autonomous agent's completion claim",
        "ground an agent's done on evidence not self-report",
        "one command to verify an AI agent actually did the work",
        "how do I know my AI coding assistant really did the task",
        "stop trusting an agent's I-finished-it message",
    ],
    "docs/answers/how-to-verify-an-ai-agent-actually-committed-code.md": [
        "did my agent really commit the code or just claim it",
        "verify an AI agent actually committed instead of saying it did",
        "check that a coding agent's commit actually exists in git",
        "agent said it committed but I see no commit",
        "confirm an AI agent landed a commit from git history",
        "how to tell if an agent faked a commit",
        "prove an agent committed code with a git-ancestry check",
        "my AI assistant claims it pushed code how do I verify",
        "validate that an agent's commit claim is real",
        "agent committed nothing but reports success how to catch",
        "is there a commit behind my agent's done message",
    ],
    "docs/answers/how-to-stop-two-ai-agents-overwriting-each-other.md": [
        "two AI agents keep overwriting each other's files",
        "stop parallel coding agents from clobbering each other",
        "prevent two agents editing the same file at once",
        "how to coordinate file writes between multiple agents",
        "agents on the same repo overwrite each other's changes",
        "lock files so two agents don't collide",
        "concurrent AI agents stepping on each other how to fix",
        "keep parallel agents from racing on shared files",
        "how do I run multiple coding agents without conflicts",
        "two agents one workspace stop the overwrite problem",
        "serialize agent edits to shared state",
    ],
    "docs/answers/how-to-detect-an-agent-loop-spinning-without-progress.md": [
        "my AI agent loop is running but not making progress",
        "detect an agent stuck spinning in circles",
        "how to tell an agent loop is making no progress",
        "agent keeps looping without finishing anything",
        "catch a coding agent that's busy but accomplishing nothing",
        "is my agent making progress or just burning turns",
        "detect a no-progress agent loop automatically",
        "agent loop never terminates how to detect the stall",
        "measure whether an agent loop is actually advancing",
        "spot a spinning agent before it wastes the budget",
        "how do I know my agent isn't just idling in a loop",
    ],
    "docs/answers/detect-a-runaway-agent-before-it-burns-the-budget.md": [
        "stop a runaway AI agent before it burns my token budget",
        "detect an agent burning tokens with nothing to show",
        "how to cap an agent that won't stop spending",
        "my coding agent is eating budget catch it early",
        "runaway agent token spend how to detect and halt",
        "early warning for an agent wasting money",
        "agent burning the budget on a loop how do I stop it",
        "detect cost-runaway in an autonomous agent",
        "trip a breaker when an agent spends without progress",
        "guard against an agent that runs up the bill",
    ],
    "docs/answers/do-ai-coding-agents-lie-about-what-they-shipped.md": [
        "do AI coding agents lie about what they shipped",
        "can AI agents fake having done the work",
        "how often do coding agents misreport what they did",
        "are AI agents honest about what they shipped",
        "AI agent over-claims what it shipped is that common",
        "agent says shipped but the diff says otherwise",
        "do coding agents fabricate progress",
        "evidence that AI agents lie about completed work",
        "agent claims vs actual diff how big is the gap",
        "catch a coding agent exaggerating what it shipped",
    ],
    "docs/answers/process-reward-model-training-data-that-cant-be-gamed.md": [
        "process reward model training data that can't be gamed",
        "ungameable labels for a process reward model",
        "where do I get PRM training data agents can't hack",
        "non-distillable reward labels for coding agents",
        "reward signal an agent can't reward-hack",
        "generate process-reward data grounded in real outcomes",
        "PRM labels from verified outcomes not self-report",
        "build a reward model that resists gaming",
        "training labels for step-level agent rewards that hold up",
        "how to make reward data robust to distillation",
    ],
    "docs/answers/how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md": [
        "add a guardrail to a coding agent with no plugin system",
        "enforce a rule on an agent that has no hook support",
        "guardrail for an agent runtime without plugins",
        "how to gate an agent that has no extension API",
        "my agent host has no hooks how do I add a check",
        "wire a check into any agent via exit code",
        "enforce agent rules with just a command exit status",
        "guardrail any CLI agent without a plugin framework",
        "no plugin system how do I still constrain my agent",
        "minimal guardrail for an agent with no integration points",
    ],
    "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md": [
        "what replaced tokens-burned as the metric for AI agents",
        "is the token-maxxing era over for coding agents",
        "what is token efficiency for AI agents",
        "measure verified outcomes instead of token usage",
        "the new metric for agents after tokens-burned",
        "why tokens consumed is a bad agent metric",
        "outcome-based metrics for autonomous agents",
        "stop measuring agents by token count what instead",
        "verified-work metric vs token-spend metric",
        "2026 shift from token-maxxing to verified outcomes",
    ],
    "docs/answers/ai-agent-said-tests-pass-but-app-is-broken.md": [
        "my AI agent said all tests pass but the app is broken",
        "tests are green but the feature doesn't work",
        "agent reports passing tests yet nothing works",
        "why does my app break when the agent says tests pass",
        "agent claims tests pass app still fails how to catch",
        "green tests broken app what's the gap",
        "trust passing tests from an AI agent or not",
        "agent's tests pass but the behavior is wrong",
    ],
    "docs/answers/does-the-commit-message-match-what-changed.md": [
        "does my AI agent's commit message match what it changed",
        "commit subject says one thing the diff does another",
        "verify a commit message against its actual diff",
        "catch a lying commit message from an agent",
        "agent commit message doesn't match the changes",
        "check that the commit subject reflects the diff",
        "audit whether a commit's claim matches its content",
        "commit says fix but the diff only touched a readme",
    ],
    "docs/answers/how-to-verify-a-cited-legal-case-exists.md": [
        "verify a cited legal case actually exists before filing",
        "check that a case citation is real not fabricated",
        "does this court case the AI cited actually exist",
        "confirm a legal citation resolves to a real reporter",
        "AI cited a case how do I know it's not made up",
        "validate case law citations against a real database",
        "fact-check a legal citation before I file it",
        "is this case citation hallucinated",
    ],
    "docs/answers/verify-a-quoted-holding-appears-in-the-opinion.md": [
        "verify a quoted holding actually appears in the opinion",
        "check that a quote is really in the cited case",
        "did the AI quote the opinion accurately or invent it",
        "confirm a holding quote matches the source opinion",
        "validate a legal quotation against the real text",
        "AI quoted a case is the quote actually there",
        "quote-fidelity check for AI legal citations",
    ],
    "docs/answers/make-an-agent-prove-the-work-not-self-certify.md": [
        "make an AI agent prove the work instead of self-certifying",
        "stop letting an agent grade its own completion",
        "require evidence before an agent can call itself done",
        "agent self-certifies done how do I demand proof",
        "ground done on a witness the agent didn't write",
        "prove-it-don't-claim-it for autonomous agents",
        "agent marks itself complete force it to prove it",
        "an agent shouldn't be its own judge of done",
    ],
    "docs/answers/ai-agent-deleted-my-tests-to-pass-the-build.md": [
        "my AI agent deleted my tests to make the build pass",
        "agent removed failing tests instead of fixing the code",
        "coding agent gamed the build by deleting tests",
        "catch an agent that drops tests to go green",
        "agent weakened the test suite to pass how to detect",
        "agent deleted assertions to make tests pass",
        "stop an agent from gutting tests for a green build",
    ],
    "docs/answers/refuse-an-agent-action-with-a-structured-reason.md": [
        "refuse an agent action with a structured reason not free text",
        "give an agent a machine-readable reason for blocking",
        "structured refusal vocabulary for agent actions",
        "how to say no to an agent in a verifiable way",
        "typed refusal reasons instead of prose errors",
        "make an agent's blocked reason checkable",
        "first-class refusal with a reason code for agents",
    ],
    "docs/answers/catch-allow-empty-shipped-fake-done.md": [
        "catch an empty commit faking done",
        "agent used git commit allow-empty to fake shipping",
        "detect a shipped commit that changed nothing",
        "empty commit pretending to be real work how to catch",
        "agent committed allow-empty shipped is that a lie",
        "spot a no-content commit claiming completion",
        "fake-done via an empty commit how do I block it",
    ],
    "docs/answers/recalled-agent-memory-is-stale-how-to-reverify.md": [
        "my recalled agent memory is stale how do I re-verify it",
        "agent memory is out of date check it against reality",
        "re-verify a saved memory before trusting it",
        "is this recalled fact still true for my agent",
        "stale agent memory how to revalidate at read time",
        "agent remembers something that's no longer true",
        "check a memory's claims against current git state",
    ],
    "docs/answers/prove-a-phase-shipped-from-git-history.md": [
        "prove a phase or feature actually shipped from git history",
        "confirm a milestone landed using git ancestry",
        "did this phase ship check the git log not the plan",
        "verify a feature shipped from commits alone",
        "prove work landed with no registry just git",
        "git-based proof that a phase is complete",
        "show a feature shipped from the commit history",
    ],
    "docs/answers/lease-based-file-locking-for-parallel-agents.md": [
        "lease-based file locking to coordinate parallel agents",
        "file leases for multiple coding agents",
        "how do agents claim a file region before editing",
        "lease a set of files so agents don't collide",
        "admission control for parallel agent file writes",
        "lock a file tree for one agent at a time",
        "lease-based coordination for a fleet of agents",
    ],
    "docs/answers/verify-what-a-subagent-claims-before-folding.md": [
        "verify what a subagent claims before folding its output",
        "don't trust a subagent's return string check the effect",
        "validate a worker agent's claim at the fold step",
        "subagent says it did X confirm before merging",
        "check a child agent's output before using it",
        "witness a subagent's effect instead of believing it",
        "fold only confirmed effects from a subagent",
    ],
    "docs/answers/reward-hacking-in-llm-coding-agents.md": [
        "reward hacking in LLM coding agents how to measure it",
        "how do coding agents reward-hack the objective",
        "detect reward hacking in an AI coding agent",
        "agent gaming the reward signal how to prevent",
        "examples of reward hacking in code agents",
        "measure and stop reward hacking in LLM agents",
        "agent optimizes the metric not the task how to catch",
    ],
    "docs/answers/why-you-cant-trust-a-model-to-judge-its-own-work.md": [
        "why can't I trust a model to judge its own work",
        "is an LLM judging itself reliable",
        "self-evaluation bias in AI coding agents",
        "why model self-grading doesn't work",
        "should an agent grade its own output",
        "LLM-as-judge of its own work what's wrong with it",
        "the problem with an agent scoring itself",
    ],
    "docs/answers/catch-fabricated-figures-in-agent-financial-output.md": [
        "catch fabricated figures in an AI agent's financial model",
        "AI invented numbers in a financial model how to check",
        "verify the figures an agent put in a spreadsheet",
        "detect made-up numbers in agent financial output",
        "agent's financial model has fake figures how to catch",
        "fact-check an AI-generated financial model",
        "hallucinated financials from an agent how to detect",
    ],
    "docs/answers/which-on-device-agent-models-are-recoverable.md": [
        "which on-device agent models can a guardrail recover from a bad action",
        "recoverable on-device AI agent models",
        "local agent models a checker can safely catch",
        "which small models does a guardrail work on",
        "on-device agent recoverability after a bad action",
        "edge agent models compatible with a recovery gate",
    ],
    "docs/answers/what-is-truth-for-an-ai-agent-verdict.md": [
        "what does true mean for an AI agent's verdict",
        "how is truth defined for an agent's decision",
        "what counts as ground truth for an agent verdict",
        "the meaning of true in an automated verdict",
        "why does an agent verdict need a definition of truth",
        "truth as a non-forgeable witness for agents",
    ],
    "docs/answers/the-trust-ladder-oracle-judge-human.md": [
        "combine a deterministic check an LLM judge and a human reviewer",
        "trust ladder for agent oversight oracle judge human",
        "when to use a hard check vs an LLM judge vs a human",
        "tiered oversight for autonomous agents",
        "layer deterministic judge and human review for agents",
        "escalation ladder from oracle to judge to human",
    ],
    "docs/answers/refusal-as-a-first-class-primitive-for-agents.md": [
        "why should no be a first-class primitive in an agent system",
        "refusal as a verifiable primitive for agents",
        "make an agent's no a checkable value not an error",
        "first-class declines in an agent architecture",
        "why agents need a structured way to refuse",
        "treat refusal as a real outcome for an agent",
    ],
    "docs/answers/stop-an-agent-editing-ci-to-skip-tests.md": [
        "stop an AI agent editing CI config to skip failing tests",
        "agent disabled the failing tests in CI how to block",
        "prevent an agent from weakening the CI pipeline",
        "agent edited the workflow to skip tests catch it",
        "block an agent from turning off CI checks",
        "agent removed a test step from CI how to detect",
    ],
    "docs/answers/block-an-out-of-lane-file-write-at-pretooluse.md": [
        "block an out-of-lane file write before the agent makes it",
        "deny an agent file write at PreToolUse",
        "stop an agent writing outside its allowed paths",
        "pre-write guard for agent file edits",
        "intercept an out-of-scope agent write before it happens",
        "enforce a write boundary at the PreToolUse hook",
    ],
    "docs/answers/agent-to-agent-proof-that-work-landed.md": [
        "how do agents prove to each other that work landed",
        "agent-to-agent trust without believing claims",
        "let one agent verify another agent's work",
        "proof of work between cooperating agents",
        "A2A verification that a task actually completed",
        "agents corroborate each other's effects not words",
    ],
    "docs/answers/ai-agents-that-game-swe-bench-benchmark-cheating.md": [
        "AI agents that game SWE-bench how to catch the cheating",
        "benchmark cheating by coding agents",
        "how do agents overfit or game SWE-bench",
        "detect an agent gaming a coding benchmark",
        "SWE-bench gaming what it looks like and how to stop it",
        "agents memorizing benchmark answers how to catch",
    ],
    "docs/answers/deterministic-hook-vs-agent-skill-which-enforces.md": [
        "deterministic hook vs an agent skill which actually enforces",
        "does a skill enforce a rule or just suggest it",
        "hook vs skill for enforcing agent behavior",
        "why a prompt-based rule doesn't enforce like a hook",
        "skill vs deterministic check which is binding",
        "enforce agent rules hook or skill",
    ],
    "docs/answers/detect-a-self-edited-claude-md-instruction-file.md": [
        "detect when an agent self-edited its CLAUDE.md instruction file",
        "agent rewrote its own AGENTS.md how to catch",
        "agent modified its own instruction file detect it",
        "catch an agent editing the rules it's supposed to follow",
        "self-modified CLAUDE.md by an agent how to detect",
        "agent tampered with its own guardrail file",
    ],
    "docs/answers/open-source-ai-legal-citation-checker.md": [
        "open-source alternative to paid AI legal citation checkers",
        "free legal citation verification tool for AI agents",
        "open source case-law citation checker",
        "is there a free AI citation checker for lawyers",
        "self-hosted legal citation verifier open source",
        "open-source tool to check AI-generated case citations",
    ],
    "docs/answers/scavenge-a-stalled-lease-without-killing-a-live-one.md": [
        "scavenge a stalled agent's lease without killing a live one",
        "reclaim a dead agent's file lock safely",
        "free a stuck lease but don't kill a working agent",
        "stale lease cleanup that won't disrupt a live agent",
        "recover a crashed agent's lease without collateral",
        "safely scavenge a stalled lease in an agent fleet",
    ],
    "docs/answers/keep-a-self-improvement-loop-from-keeping-bad-changes.md": [
        "keep a self-improvement loop from keeping bad changes",
        "stop an auto-improve loop from accepting regressions",
        "gate a self-improving agent on a real measured gain",
        "RSI loop keeps bad edits how to prevent",
        "only keep an agent's change if a witness confirms it improved",
        "revert bad changes in a self-improvement loop automatically",
    ],
    "docs/answers/agent-claimed-it-fixed-the-bug-but-it-didnt.md": [
        "my AI agent claimed it fixed the bug but it didn't",
        "agent says bug fixed but it's still broken",
        "verify an agent actually fixed the bug",
        "agent reports a fix that didn't work how to catch",
        "is the bug really fixed or did the agent just say so",
        "agent's fix claim is false how do I detect it",
    ],
    "docs/answers/ci-passed-but-the-feature-isnt-there.md": [
        "CI passed but the feature isn't there how to catch that",
        "green CI but the feature was never implemented",
        "pipeline is green yet the work is missing",
        "CI green but nothing actually shipped",
        "passing CI doesn't mean the feature exists how to verify",
        "feature absent despite a passing build",
    ],
    "docs/answers/audit-which-commits-were-ai-and-did-they-ship.md": [
        "audit which commits were AI and whether they shipped real work",
        "tell which commits an AI agent made across a repo",
        "audit AI-generated commits for real content",
        "which commits are agent-authored and did they land work",
        "review a repo's AI commits for actual changes",
        "separate real agent commits from no-op ones",
    ],
    "docs/answers/two-claude-code-agents-on-one-branch.md": [
        "two Claude Code agents on one branch keep clobbering each other",
        "running two Claude Code instances on the same branch",
        "two agents same branch how do I stop the conflicts",
        "coordinate two Claude Code agents on one repo",
        "parallel Claude Code agents overwrite each other",
        "two coding agents one git branch collision fix",
    ],
    "docs/answers/make-agent-done-mean-a-checkable-effect.md": [
        "make an agent's done mean a checkable effect not a sentence",
        "define done for an agent as a real observable effect",
        "agent done should be a fact not a claim",
        "tie an agent's completion to a verifiable effect",
        "what should done mean for an autonomous agent",
        "make done checkable instead of self-declared",
    ],
    "docs/answers/can-i-trust-a-coding-agents-pull-request.md": [
        "can I trust an AI coding agent's pull request",
        "how do I review an agent-generated PR safely",
        "is an AI agent's pull request safe to merge",
        "verify a coding agent's PR actually does what it says",
        "check an agent PR before approving it",
        "trust an autonomous agent's pull request or not",
    ],
    "docs/answers/enforce-that-an-agent-ran-the-tests-it-claims.md": [
        "enforce that an AI agent actually ran the tests it claims",
        "did the agent really run the tests or just say so",
        "require proof an agent executed its tests",
        "agent claims tests ran verify it",
        "make an agent show it ran the test suite",
        "confirm an agent's test run actually happened",
    ],
    "docs/answers/catch-an-agent-that-fakes-tool-calls-or-output.md": [
        "catch an AI agent that fakes tool calls or fabricates output",
        "agent pretended to call a tool how to detect",
        "agent fabricated command output how to catch",
        "detect a hallucinated tool call from an agent",
        "agent faked a shell result verify the real one",
        "spot an agent inventing tool output",
    ],
    "docs/answers/last-writer-wins-multi-agent-shared-memory.md": [
        "last-writer-wins problem in multi-agent shared memory",
        "agents overwrite shared memory last write wins",
        "stop lost updates in multi-agent shared state",
        "concurrent agents clobber shared memory how to fix",
        "shared memory race between agents last writer wins",
        "coordinate writes to shared agent memory",
    ],
    "docs/answers/prevent-context-poisoning-from-an-agents-own-outputs.md": [
        "prevent context poisoning from an agent's own prior outputs",
        "agent feeds its own bad output back into context",
        "stop an agent poisoning itself with prior mistakes",
        "context poisoning loop in an autonomous agent",
        "agent's own hallucination contaminates later steps",
        "break the self-poisoning feedback loop in an agent",
    ],
    "docs/answers/multi-agent-coordination-without-a-central-orchestrator.md": [
        "coordinate multiple AI agents without a central orchestrator",
        "multi-agent coordination with contracts not a queue",
        "decentralized coordination for a fleet of agents",
        "no orchestrator how do agents avoid colliding",
        "agents coordinate via shared rules not a controller",
        "orchestrator-free multi-agent coordination",
    ],
    "docs/answers/builder-validator-chain-separate-generator-from-evaluator.md": [
        "builder-validator chain separate generator from evaluator",
        "split the agent that builds from the one that checks",
        "generator-evaluator separation for coding agents",
        "why the validator must not be the generator",
        "two-stage agent build then independently verify",
        "separate generation and evaluation in an agent pipeline",
    ],
    "docs/answers/trust-substrate-for-a-fleet-of-autonomous-agents.md": [
        "what is a trust substrate for a fleet of autonomous agents",
        "trust layer for many unreliable AI agents",
        "substrate that adjudicates truth across an agent fleet",
        "how do I make a fleet of agents trustworthy",
        "a referee layer for autonomous agents",
        "ground truth for a fleet of self-narrating agents",
    ],
    "docs/answers/why-does-my-agent-ignore-the-rules-in-claude-md.md": [
        "why does my AI agent ignore the rules in CLAUDE.md",
        "agent doesn't follow my CLAUDE.md instructions",
        "make CLAUDE.md rules actually stick for an agent",
        "agent skips the rules in its instruction file",
        "CLAUDE.md says one thing the agent does another",
        "why prompt rules don't bind an agent and what does",
    ],
    "docs/answers/detect-a-no-op-commit-from-an-agent.md": [
        "detect a no-op commit from an AI agent",
        "agent made a commit that changed nothing",
        "spot an empty or meaningless agent commit",
        "no-op commit from a coding agent how to catch",
        "agent committed but did no real work detect it",
        "find commits with no substantive change from an agent",
    ],
    "docs/answers/verify-an-llm-didnt-hallucinate-a-function-or-api.md": [
        "verify an LLM didn't hallucinate a function or API",
        "agent called an API that doesn't exist how to catch",
        "detect a hallucinated function in agent code",
        "check that the API the agent used is real",
        "LLM invented a method does it actually exist",
        "catch made-up library calls in AI-generated code",
    ],
    "docs/answers/hidden-test-split-to-stop-agents-overfitting.md": [
        "hidden test split to stop agents overfitting the visible tests",
        "keep agents from gaming the tests they can see",
        "held-out tests so an agent can't overfit",
        "secret test split for evaluating coding agents",
        "stop an agent memorizing the visible test cases",
        "use a hidden split to detect agent overfitting",
    ],
    "docs/answers/governance-for-agentic-ai-projects-that-keep-getting-canceled.md": [
        "governance is why agentic AI projects get canceled",
        "missing governance layer for agentic AI",
        "why do agentic AI projects keep getting killed",
        "what governance does an autonomous agent program need",
        "agentic AI canceled over trust what's the fix",
        "the oversight layer agentic projects are missing",
    ],
    "docs/answers/make-any-agent-skill-verify-its-own-work.md": [
        "make any agent skill verify its own work",
        "add self-verification to an existing agent skill",
        "ground a skill's claims on a real check",
        "turn a belief-based skill into a verified one",
        "wrap any skill so it proves what it did",
        "give an agent skill a witness for its output",
    ],
    "docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md": [
        "catch fabricated legal citations inside my AI agent before filing",
        "stop my legal AI agent citing fake cases",
        "verify case law a legal AI agent generated",
        "block hallucinated citations in a legal agent",
        "legal agent invents citations how do I catch it",
        "pre-filing check for AI-fabricated case law",
    ],
    "docs/answers/largest-ai-hallucination-sanction-how-to-avoid.md": [
        "how to avoid an AI-hallucinated citation sanction",
        "largest AI hallucination sanction what to learn from it",
        "what the record AI-citation fines have in common",
        "avoid getting sanctioned for AI fake citations",
        "lessons from the biggest AI hallucination court sanctions",
        "stay out of trouble for AI-invented legal citations",
    ],
    "docs/answers/aba-512-verify-ai-citations-duty.md": [
        "does ABA Opinion 512 require me to verify AI citations",
        "ABA 512 duty to check AI-generated citations",
        "lawyer's duty to verify AI case law under ABA 512",
        "what does ABA Formal Opinion 512 say about AI citations",
        "am I required to verify AI citations ABA guidance",
        "ABA 512 and AI citation verification duty",
    ],
    "docs/answers/how-a-court-can-audit-ai-citations-in-filings.md": [
        "how a court can audit AI-generated citations in filings",
        "court-side check for AI citations in submitted briefs",
        "audit inbound filings for fabricated AI citations",
        "how do courts verify citations in filings they receive",
        "screen filings for hallucinated case law",
        "a court's process to catch AI-invented citations",
    ],
    "docs/answers/stop-ai-making-fake-tests.md": [
        "stop an AI agent from making fake tests",
        "stop my AI coding agent writing fake tests",
        "prevent an agent from writing hollow tests",
        "agent writes fake tests how do I stop it",
        "block an agent from shipping tests that don't test",
        "keep an AI from faking the test suite",
    ],
    "docs/answers/ai-generated-tests-that-pass-but-test-nothing.md": [
        "AI generated tests that pass but test nothing",
        "AI tests that always pass and assert nothing",
        "my AI writes tests that pass but test nothing",
        "tests that pass without checking anything from an agent",
        "agent's tests are green but assert nothing",
        "vacuous passing tests from an AI agent",
    ],
    "docs/answers/ai-mocks-everything-tests-are-useless.md": [
        "my AI agent mocks everything and the tests are useless",
        "agent over-mocks so the tests prove nothing",
        "AI mocks the whole thing tests are meaningless",
        "too much mocking by an agent how to detect",
        "agent's tests mock away the real behavior",
        "useless tests because the agent mocked everything",
    ],
    "docs/answers/are-my-ai-generated-tests-real.md": [
        "how do I tell if my AI-generated tests are real",
        "are my AI tests real or just lying to me",
        "check whether AI-written tests actually verify behavior",
        "tell real AI tests from fake ones",
        "are these agent tests genuine or hollow",
        "validate that AI-generated tests do real work",
    ],
    "docs/answers/mutation-testing-vs-test-witness-for-ai-tests.md": [
        "mutation testing vs a test-witness gate for AI tests",
        "is mutation testing enough to catch fake AI tests",
        "test-witness vs mutation testing for agent tests",
        "how to prove AI tests catch real bugs",
        "compare mutation testing and a witness gate for tests",
        "best way to validate AI-generated tests really assert",
    ],
    "docs/answers/make-ai-write-tests-that-actually-assert.md": [
        "make an AI agent write tests that actually assert something",
        "force an agent to write meaningful assertions",
        "get an AI to write tests with real checks",
        "agent's tests have no assertions how to fix",
        "make AI tests assert behavior not just run",
        "require real assertions in agent-written tests",
    ],
    "docs/answers/coverage-is-green-but-tests-are-worthless.md": [
        "100% coverage but the AI's tests are worthless",
        "high coverage meaningless tests from an agent",
        "green coverage but the tests don't test anything",
        "coverage is full yet the tests are useless",
        "why coverage doesn't mean the AI's tests are good",
        "full coverage worthless assertions how to catch",
    ],
    "docs/answers/add-the-dos-plugin-to-a-private-company-marketplace.md": [
        "add the DOS plugin to a private company Claude Code marketplace",
        "install DOS in an internal plugin marketplace",
        "deploy the dos-kernel plugin to a company gallery",
        "private marketplace setup for the DOS plugin",
        "ship DOS to my org's internal Claude Code marketplace",
        "host the DOS plugin in a private company registry",
    ],
    "docs/answers/stop-re-reviewing-code-the-machine-already-verified.md": [
        "stop re-reviewing code a machine already verified",
        "skip human review on code the verifier already checked",
        "spend code review only on what a machine couldn't verify",
        "don't re-review what an automated check already proved",
        "route review attention to the unverified part",
        "save reviewer time on machine-verified changes",
        "which code still needs human review after the gate",
    ],
}

# A second alias layer: the SAME intents, phrased with a specific host/tool name
# and as symptom-first or imperative searches. A developer rarely types "AI agent"
# — they type the product in front of them ("Cursor said it fixed the bug",
# "Copilot wrote a test that does nothing", "did Codex actually commit"). These are
# real, distinct queries that route to the SAME evidence-backed page, so they widen
# the captured-query surface honestly (still deduped by page in headline(), still
# unique strings). DOS wires all of these hosts (`dos hosts --json`), so naming
# them is true, not keyword-stuffing for a host DOS can't serve.
ALIAS_QUERIES_HOST = {
    "docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md": [
        "did Cursor actually do the work or just say it did",
        "verify Claude Code actually completed the task",
        "Copilot says done how do I check it really is",
        "confirm Codex finished the work from git",
        "did my Gemini CLI agent actually do what it claimed",
        "Windsurf agent reports done is it true",
    ],
    "docs/answers/how-to-verify-an-ai-agent-actually-committed-code.md": [
        "did Cursor actually commit the code",
        "Claude Code says it committed but I see no commit",
        "verify Copilot agent actually committed instead of claiming",
        "Aider says it committed confirm it from git",
        "did Codex really commit or fake it",
    ],
    "docs/answers/how-to-stop-two-ai-agents-overwriting-each-other.md": [
        "two Cursor agents overwriting each other's files",
        "stop two Copilot agents clobbering the same file",
        "multiple Aider sessions colliding on one repo",
        "run several Claude Code agents without file conflicts",
    ],
    "docs/answers/ai-agent-said-tests-pass-but-app-is-broken.md": [
        "Cursor said tests pass but the app is broken",
        "Copilot reports green tests but nothing works",
        "Claude Code says all tests pass app still fails",
    ],
    "docs/answers/ai-agent-deleted-my-tests-to-pass-the-build.md": [
        "Cursor deleted my tests to make the build pass",
        "Copilot removed failing tests instead of fixing",
        "Claude Code dropped tests to go green",
    ],
    "docs/answers/agent-claimed-it-fixed-the-bug-but-it-didnt.md": [
        "Cursor claimed it fixed the bug but it didn't",
        "Copilot says fixed but the bug is still there",
        "Claude Code reported a fix that didn't work",
    ],
    "docs/answers/stop-ai-making-fake-tests.md": [
        "stop Copilot writing fake tests",
        "Cursor keeps writing hollow tests stop it",
        "Claude Code writes tests that don't test anything",
    ],
    "docs/answers/why-does-my-agent-ignore-the-rules-in-claude-md.md": [
        "Cursor ignores my rules file",
        "Claude Code doesn't follow CLAUDE.md why",
        "my agent ignores AGENTS.md instructions",
        "Copilot ignores the custom instructions",
    ],
    "docs/answers/do-ai-coding-agents-lie-about-what-they-shipped.md": [
        "does Cursor lie about what it shipped",
        "do Copilot agents misreport what they did",
        "can Claude Code fake having done the work",
    ],
    "docs/answers/how-to-detect-an-agent-loop-spinning-without-progress.md": [
        "Cursor agent loop running but not progressing",
        "Claude Code stuck in a loop making no progress",
        "Aider keeps looping without finishing",
    ],
    "docs/answers/catch-an-agent-that-fakes-tool-calls-or-output.md": [
        "Cursor faked a tool call how to detect",
        "Claude Code fabricated command output catch it",
        "agent hallucinated a terminal result",
    ],
    "docs/answers/can-i-trust-a-coding-agents-pull-request.md": [
        "can I trust a Copilot pull request",
        "is a Cursor-generated PR safe to merge",
        "review a Claude Code PR before approving",
    ],
    "docs/answers/verify-an-llm-didnt-hallucinate-a-function-or-api.md": [
        "Copilot used a function that doesn't exist",
        "Cursor called an API that isn't real",
        "Claude hallucinated a method does it exist",
    ],
    "docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md": [
        "my legal AI tool cited a fake case how to catch",
        "ChatGPT legal research invented a citation verify it",
        "stop my legal assistant citing nonexistent cases",
    ],
    "docs/answers/make-ai-write-tests-that-actually-assert.md": [
        "make Copilot write tests that actually assert",
        "get Cursor to write real test assertions",
        "force Claude Code tests to check behavior",
    ],
}

# Flatten both alias maps into the same (query, page) shape as the curated core.
# Order is deterministic (dict insertion order); the core list stays first so a
# test that pins a specific core phrasing keeps finding it at its known place. A
# phrasing already present (a few aliases restate a page's canonical query, or
# recur across the two maps) is skipped — the first occurrence is authoritative,
# so the query stays unique across the whole list (test_arrival_queries_are_unique).
_seen_queries = {q for q, _ in ARRIVAL_QUERIES}
for _amap in (ALIAS_QUERIES, ALIAS_QUERIES_HOST):
    for _page, _aliases in _amap.items():
        for _q in _aliases:
            if _q not in _seen_queries:
                ARRIVAL_QUERIES.append((_q, _page))
                _seen_queries.add(_q)

# --- family 1: arrival files (the well-known fetch targets) -------------------
# (path, what an agent/tool fetches it for). Presence is read from the tree.
ARRIVAL_FILES = [
    ("llms.txt", "the llms.txt convention — an LLM's first fetch, a curated index"),
    ("llms-full.txt", "the whole story in one file (the docs concatenated)"),
    ("llms-install.md", "the agent-readable install recipe"),
    ("AGENTS.md", "orientation written for an agent working inside the repo"),
    ("GEMINI.md", "the Gemini CLI context file the extension loads"),
    ("server.json", "the MCP registry manifest (official registry)"),
    ("gemini-extension.json", "the Gemini CLI extension manifest (auto-indexed gallery)"),
    ("smithery.yaml", "the Smithery MCP-registry manifest"),
    ("CITATION.cff", "GitHub 'cite this repository' + the scholarly-agent surface"),
    ("docs/FAQ.md", "question-shaped answers an answer-engine lifts"),
]

# --- family 4: external registries / venues, by who controls the listing ------
# status: LIVE = provable here or auto-indexed from an in-tree manifest;
#         GATED = needs an owner submission a third party must accept.
# Evidence is a tracked file where one exists; otherwise the status is asserted
# from the tracking issue and clearly flagged GATED so it never joins the headline.
REGISTRIES = [
    ("PyPI (dos-kernel)", "LIVE", "pyproject.toml", "the package resolver every pip/uv/pipx agent uses"),
    ("MCP official registry", "LIVE", "server.json", "the registry that fans out to github.com/mcp + VS Code"),
    ("Gemini CLI extensions gallery", "LIVE", "gemini-extension.json", "auto-indexed: crawls repos with a valid manifest, no PR"),
    ("GitHub Action (verify-action)", "LIVE", "verify-action/action.yml", "the CI gate the Marketplace lists"),
    ("GitLab CI template + catalog component", "LIVE", "gitlab-ci/dos-verify.gitlab-ci.yml", "the population the GitHub Action never reaches"),
    ("Smithery listing", "GATED", "smithery.yaml", "manifest in-tree; the listing is an owner submission (#134)"),
    ("conda-forge feedstock", "GATED", None, "noarch recipe, no traction gate; one staged-recipes PR (#54)"),
    ("punkpeye/awesome-mcp-servers", "GATED", None, "one-line README PR, agent fast-track (#134)"),
    ("upstream CrewAI / OpenAI Agents listings", "GATED", "src/dos/drivers/crewai_guardrail.py", "drivers shipped; the listings pin a release (#77)"),
]


def _present(rel: str) -> bool:
    return (REPO / rel).exists()


def _count_glob(globpat: str, exclude: str | None = None) -> list[str]:
    out = []
    for p in sorted(REPO.glob(globpat)):
        if exclude and exclude in p.name:
            continue
        out.append(str(p.relative_to(REPO)).replace("\\", "/"))
    return out


def _hosts() -> list[dict]:
    """Read the live host registry via the public CLI. Empty list on any failure
    (the report degrades; it never crashes on a missing CLI)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "dos.cli", "hosts", "--json"],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        d = json.loads(r.stdout)
        return d.get("hosts", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    except Exception:
        return []


def _cookbook_recipes() -> int:
    """Count framework seams in the fleet-framework cookbook (## / ### headings
    naming a recipe)."""
    f = REPO / "examples/playbooks/cookbook-fleet-frameworks.md"
    if not f.exists():
        return 0
    n = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## ") or s.startswith("### "):
            n += 1
    return n


def _scoreboard() -> dict:
    """The per-repo scoreboard fan-out (docs/311, #98) — the multiplicative
    discovery surface: one indexed, named trust page per audited repo, each a
    landing context where an agent auditing that repo meets DOS. Counts the
    TRACKED pages (the published surface), plus whether the seeded-index
    orchestrator (the corpus-scale engine) and its index root are in the tree."""
    sb = REPO / "docs" / "scoreboard"
    pages = sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for p in sb.glob("*/*.md")  # docs/scoreboard/<org>/<name>.md
    ) if sb.exists() else []
    return {
        "pages_published": pages,
        "index_root": _present("docs/scoreboard/README.md"),
        "orchestrator": _present("scripts/seed_scoreboard_index.py"),
    }


def gather() -> dict:
    arrival = [(p, d, _present(p)) for p, d in ARRIVAL_FILES]
    # captured = the canonical answer page exists; we dedupe distinct target pages
    # so two query phrasings pointing at one page don't double-count the surface.
    queries = [(q, page, _present(page)) for q, page in ARRIVAL_QUERIES]
    answers = _count_glob("docs/answers/*.md", exclude="README")
    hosts = _hosts()
    tiers = ["MCP (advisory)", "hooks (enforcement)", "exit-code (any command env)"]
    recipes = _cookbook_recipes()
    registries = []
    for name, status, evidence, why in REGISTRIES:
        proven = _present(evidence) if evidence else None
        registries.append({
            "name": name, "status": status, "evidence": evidence,
            "evidence_present": proven, "why": why,
        })
    return {
        "arrival_queries": queries,
        "arrival_files": arrival,
        "answers_pages": answers,
        "hosts": hosts,
        "tiers": tiers,
        "framework_recipes": recipes,
        "registries": registries,
        "scoreboard": _scoreboard(),
    }


def headline(inv: dict) -> dict:
    queries_captured = sum(1 for _, _, ok in inv["arrival_queries"] if ok)
    queries_total = len(inv["arrival_queries"])
    # distinct canonical pages the captured queries resolve to (the real surface count)
    captured_pages = {page for _, page, ok in inv["arrival_queries"] if ok}
    arrival_present = sum(1 for _, _, ok in inv["arrival_files"] if ok)
    registries_live = sum(1 for r in inv["registries"] if r["status"] == "LIVE")
    registries_gated = sum(1 for r in inv["registries"] if r["status"] == "GATED")
    sb = inv["scoreboard"]
    return {
        "arrival_queries_captured": queries_captured,
        "arrival_queries_tracked": queries_total,
        "arrival_query_pages": len(captured_pages),
        "arrival_files_present": arrival_present,
        "arrival_files_expected": len(inv["arrival_files"]),
        "answer_pages": len(inv["answers_pages"]),
        "hosts_wireable": len(inv["hosts"]),
        "integration_tiers": len(inv["tiers"]),
        "framework_recipes": inv["framework_recipes"],
        "registries_live": registries_live,
        "registries_gated_submitted": registries_gated,
        "scoreboard_pages_published": len(sb["pages_published"]),
        "scoreboard_fanout_engine": bool(sb["orchestrator"] and sb["index_root"]),
    }


def render(inv: dict, h: dict) -> str:
    L = []
    L.append("# DOS discoverability inventory — the surfaces an agent finds DOS through")
    L.append("")
    L.append("> Counted from the repo's own ground truth. A surface is LIVE only when")
    L.append("> its evidence is in this tree; a filed-but-unmerged submission is GATED,")
    L.append("> never folded into the LIVE count. Re-run before/after a change — the")
    L.append("> delta is the measured progress.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"- high-intent queries captured (canonical page in tree): "
             f"**{h['arrival_queries_captured']}/{h['arrival_queries_tracked']}** "
             f"→ **{h['arrival_query_pages']}** distinct answer pages")
    L.append(f"- arrival files present: **{h['arrival_files_present']}/{h['arrival_files_expected']}**")
    L.append(f"- answer-shaped pages (answer-engine liftable): **{h['answer_pages']}**")
    L.append(f"- agent hosts wireable (live registry): **{h['hosts_wireable']}**")
    L.append(f"- integration tiers: **{h['integration_tiers']}**")
    L.append(f"- framework seams (cookbook recipes): **{h['framework_recipes']}**")
    L.append(f"- external registries LIVE: **{h['registries_live']}**  ·  GATED/submitted: **{h['registries_gated_submitted']}**")
    fanout = "yes" if h["scoreboard_fanout_engine"] else "no"
    L.append(f"- scoreboard per-repo pages published: **{h['scoreboard_pages_published']}**  ·  corpus fan-out engine: **{fanout}**")
    L.append("")
    L.append("## 0. Arrival queries (the high-intent questions an answer-engine routes)")
    L.append("")
    L.append("> Captured = a canonical, evidence-backed answer page exists in the tree.")
    L.append("> This counts whether we *have the answer*, not where we rank.")
    L.append("")
    for q, page, ok in inv["arrival_queries"]:
        mark = "[captured]" if ok else "[OPEN]"
        L.append(f"- {mark}  \"{q}\" → `{page}`")
    L.append("")
    L.append("## 1. Arrival files (the well-known fetch targets)")
    L.append("")
    for p, why, ok in inv["arrival_files"]:
        mark = "[present]" if ok else "[MISSING]"
        L.append(f"- {mark}  `{p}` - {why}")
    L.append("")
    L.append("## 2. Agent hosts (live from `dos hosts --json`)")
    L.append("")
    if inv["hosts"]:
        for hh in inv["hosts"]:
            tier = hh.get("tier", "?")
            L.append(f"- `{hh.get('host','?')}` — {tier} ({hh.get('dialect','?')})")
    else:
        L.append("- (host registry unavailable — install `dos-kernel` to populate)")
    L.append("")
    L.append("## 3. Integration tiers + framework seams")
    L.append("")
    for t in inv["tiers"]:
        L.append(f"- tier: {t}")
    L.append(f"- framework cookbook recipes: {inv['framework_recipes']}")
    L.append("")
    L.append("## 4. External registries / venues")
    L.append("")
    for r in inv["registries"]:
        ev = ""
        if r["evidence"]:
            ev = f" [evidence: `{r['evidence']}`{'' if r['evidence_present'] else ' — MISSING'}]"
        L.append(f"- **{r['status']}** — {r['name']}: {r['why']}{ev}")
    L.append("")
    L.append("## 5. Scoreboard per-repo fan-out (the multiplicative surface)")
    L.append("")
    sb = inv["scoreboard"]
    L.append(f"- corpus fan-out engine present: "
             f"{'yes' if sb['orchestrator'] and sb['index_root'] else 'no'} "
             "(`scripts/seed_scoreboard_index.py` + `docs/scoreboard/README.md`)")
    L.append(f"- per-repo pages published (tracked): {len(sb['pages_published'])}")
    for p in sb["pages_published"]:
        L.append(f"  - `{p}`")
    L.append("")
    L.append("## Answer pages (the corpus an answer-engine lifts)")
    L.append("")
    for a in inv["answers_pages"]:
        L.append(f"- `{a}`")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the inventory + headline as JSON")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any expected arrival file is missing (rot pin)")
    args = ap.parse_args(argv)

    # The report carries em-dashes / bullets; force UTF-8 so a cp1252 Windows
    # console doesn't crash the render (the same defensive move other scripts make).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    inv = gather()
    h = headline(inv)

    if args.json:
        print(json.dumps({"headline": h, "inventory": {
            "arrival_queries": [{"query": q, "page": page, "captured": ok} for q, page, ok in inv["arrival_queries"]],
            "arrival_files": [{"path": p, "why": w, "present": ok} for p, w, ok in inv["arrival_files"]],
            "answers_pages": inv["answers_pages"],
            "hosts": inv["hosts"],
            "tiers": inv["tiers"],
            "framework_recipes": inv["framework_recipes"],
            "registries": inv["registries"],
            "scoreboard": inv["scoreboard"],
        }}, indent=2))
    else:
        print(render(inv, h))

    if args.check:
        missing = [p for p, _, ok in inv["arrival_files"] if not ok]
        if missing:
            print(f"\nFAIL: {len(missing)} arrival file(s) missing: {', '.join(missing)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
