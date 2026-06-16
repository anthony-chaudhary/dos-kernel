# Answers — one sourced page per question you'd ask a model

This is the answer corpus: one self-contained page per high-intent question
about catching autonomous AI agents that misreport their work. Each page is
written to be read on its own — it names the package (`dos-kernel`), gives the
one command, shows real output, and carries an **evidence table where every
number links to the file in this repo that proves it**. If you arrived from a
search or an answer engine, you're in the right place; if you want the whole
story, start at the [README](../../README.md) or the
[five-minute quickstart](../QUICKSTART.md).

| The question | The command | Page |
|---|---|---|
| How do I verify an AI agent actually did the work? | `dos verify` | [how-to-verify-an-ai-agent-actually-did-the-work](how-to-verify-an-ai-agent-actually-did-the-work.md) |
| How do I stop two AI agents overwriting each other? | `dos arbitrate` | [how-to-stop-two-ai-agents-overwriting-each-other](how-to-stop-two-ai-agents-overwriting-each-other.md) |
| How do I detect an agent loop spinning without progress? | `dos liveness` / `productivity` / `efficiency` | [how-to-detect-an-agent-loop-spinning-without-progress](how-to-detect-an-agent-loop-spinning-without-progress.md) |
| Where do I get process-reward training data that can't be gamed? | `dos reward` | [process-reward-model-training-data-that-cant-be-gamed](process-reward-model-training-data-that-cant-be-gamed.md) |
| Do AI coding agents lie about what they shipped? | `dos verify` / `dos commit-audit` | [do-ai-coding-agents-lie-about-what-they-shipped](do-ai-coding-agents-lie-about-what-they-shipped.md) |
| How do I add a guardrail to a coding agent with no plugin/hook system? | `dos commit-audit` (exit code) | [how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) |
| What replaced tokens-burned as the metric for AI agents? | `dos verify` / `dos efficiency` / `dos reward` | [what-replaced-tokens-burned-as-the-metric-for-ai-agents](what-replaced-tokens-burned-as-the-metric-for-ai-agents.md) |
| How do I verify an agent actually committed code instead of just saying it did? | `dos verify` | [how-to-verify-an-ai-agent-actually-committed-code](how-to-verify-an-ai-agent-actually-committed-code.md) |
| My AI agent said "all tests pass" but the app is still broken | `dos test-witness` / `dos coverage` | [ai-agent-said-tests-pass-but-app-is-broken](ai-agent-said-tests-pass-but-app-is-broken.md) |
| How do I know if my agent's commit message matches what it changed? | `dos commit-audit` | [does-the-commit-message-match-what-changed](does-the-commit-message-match-what-changed.md) |
| How do I verify a cited legal case actually exists before filing? | `citation-resolve` (MCP) / `dos doctor` | [how-to-verify-a-cited-legal-case-exists](how-to-verify-a-cited-legal-case-exists.md) |
| How do I catch fabricated legal citations inside my AI agent? | `citation-resolve` (MCP / exit code) | [catch-fabricated-legal-citations-in-my-ai-agent](catch-fabricated-legal-citations-in-my-ai-agent.md) |
| How do I avoid an AI-citation sanction? | `citation-resolve` (MCP) / `dos doctor` | [largest-ai-hallucination-sanction-how-to-avoid](largest-ai-hallucination-sanction-how-to-avoid.md) |
| Does ABA Opinion 512 require me to verify AI-generated citations? | `citation-resolve` (MCP) / `dos doctor` | [aba-512-verify-ai-citations-duty](aba-512-verify-ai-citations-duty.md) |
| How do I make an agent prove it did the work instead of self-certifying done? | `dos improve` / `dos verify` | [make-an-agent-prove-the-work-not-self-certify](make-an-agent-prove-the-work-not-self-certify.md) |
| My AI agent deleted my tests to make the build pass | `dos test-witness` / `dos commit-audit` | [ai-agent-deleted-my-tests-to-pass-the-build](ai-agent-deleted-my-tests-to-pass-the-build.md) |
| How do I refuse an agent action with a structured reason instead of free text? | `dos refuse-reasons` / `dos check-reason` | [refuse-an-agent-action-with-a-structured-reason](refuse-an-agent-action-with-a-structured-reason.md) |
| How do I catch an empty commit / `--allow-empty "shipped"` fake-done? | `dos commit-audit` / `dos verify` | [catch-allow-empty-shipped-fake-done](catch-allow-empty-shipped-fake-done.md) |
| How do I verify a quoted holding actually appears in the cited opinion? | `citation-resolve` (MCP) | [verify-a-quoted-holding-appears-in-the-opinion](verify-a-quoted-holding-appears-in-the-opinion.md) |
| How can a court audit AI-generated citations in filings it receives? | `citation-resolve` (MCP) / `dos attest` | [how-a-court-can-audit-ai-citations-in-filings](how-a-court-can-audit-ai-citations-in-filings.md) |
| My recalled agent memory is stale or wrong — how do I re-verify it? | `recall` (MCP) | [recalled-agent-memory-is-stale-how-to-reverify](recalled-agent-memory-is-stale-how-to-reverify.md) |
| How do I prove a phase or feature actually shipped from git history? | `dos verify` | [prove-a-phase-shipped-from-git-history](prove-a-phase-shipped-from-git-history.md) |
| How do I do lease-based file locking to coordinate parallel coding agents? | `dos arbitrate` / `dos lease-lane` | [lease-based-file-locking-for-parallel-agents](lease-based-file-locking-for-parallel-agents.md) |
| How do I verify what a subagent claims before folding its output? | `dos verify` / `dos commit-audit` | [verify-what-a-subagent-claims-before-folding](verify-what-a-subagent-claims-before-folding.md) |
| Reward hacking in LLM coding agents — how do I measure and prevent it? | `dos reward` / `dos improve` | [reward-hacking-in-llm-coding-agents](reward-hacking-in-llm-coding-agents.md) |
| Why can't I trust an AI model to judge its own work? | `dos verify` / `dos improve` | [why-you-cant-trust-a-model-to-judge-its-own-work](why-you-cant-trust-a-model-to-judge-its-own-work.md) |
| How do I catch fabricated figures in an agent's financial model output? | `formula_recompute` / `dos doctor` | [catch-fabricated-figures-in-agent-financial-output](catch-fabricated-figures-in-agent-financial-output.md) |
| Which on-device agent models can a guardrail actually recover from a bad action? | `dos commit-audit` / `dos verify` | [which-on-device-agent-models-are-recoverable](which-on-device-agent-models-are-recoverable.md) |
| What does "true" mean for an AI agent's verdict? | `dos verify` | [what-is-truth-for-an-ai-agent-verdict](what-is-truth-for-an-ai-agent-verdict.md) |
| How do I combine a deterministic check, an LLM judge, and a human reviewer? | `dos verify` | [the-trust-ladder-oracle-judge-human](the-trust-ladder-oracle-judge-human.md) |
| Why should "no" be a first-class, verifiable primitive in an agent system? | `dos refuse-reasons` / `dos verify` | [refusal-as-a-first-class-primitive-for-agents](refusal-as-a-first-class-primitive-for-agents.md) |
| How do I stop an AI agent from editing CI config to skip failing tests? | `dos commit-audit` / `dos scope-gate` | [stop-an-agent-editing-ci-to-skip-tests](stop-an-agent-editing-ci-to-skip-tests.md) |
| How do I block an out-of-lane file write before the agent makes it (PreToolUse)? | `dos arbitrate` | [block-an-out-of-lane-file-write-at-pretooluse](block-an-out-of-lane-file-write-at-pretooluse.md) |
| How do agents prove to each other that work actually landed? | `dos status` / `dos verify` | [agent-to-agent-proof-that-work-landed](agent-to-agent-proof-that-work-landed.md) |
| AI agents that game SWE-bench — how do I catch benchmark cheating? | `dos reward` / `dos commit-audit` | [ai-agents-that-game-swe-bench-benchmark-cheating](ai-agents-that-game-swe-bench-benchmark-cheating.md) |
| Deterministic pre-commit hook vs an agent skill — which actually enforces? | `dos commit-audit` (exit code) | [deterministic-hook-vs-agent-skill-which-enforces](deterministic-hook-vs-agent-skill-which-enforces.md) |
| How do I detect when an agent self-edited its CLAUDE.md / AGENTS.md? | `dos commit-audit` | [detect-a-self-edited-claude-md-instruction-file](detect-a-self-edited-claude-md-instruction-file.md) |
| Is there an open-source alternative to paid AI legal citation checkers? | `citation-resolve` (MCP) | [open-source-ai-legal-citation-checker](open-source-ai-legal-citation-checker.md) |
| How do I detect a runaway AI agent before it burns the token budget? | `dos liveness` / `dos breaker` | [detect-a-runaway-agent-before-it-burns-the-budget](detect-a-runaway-agent-before-it-burns-the-budget.md) |
| How do I scavenge a stalled agent's lease without killing a live one? | `dos liveness` / `dos reap` | [scavenge-a-stalled-lease-without-killing-a-live-one](scavenge-a-stalled-lease-without-killing-a-live-one.md) |
| How do I keep an AI self-improvement loop from keeping bad changes? | `dos improve` | [keep-a-self-improvement-loop-from-keeping-bad-changes](keep-a-self-improvement-loop-from-keeping-bad-changes.md) |
| My AI agent claimed it fixed the bug, but it didn't | `dos verify` / `dos commit-audit` | [agent-claimed-it-fixed-the-bug-but-it-didnt](agent-claimed-it-fixed-the-bug-but-it-didnt.md) |
| CI passed but the feature isn't there — how do I catch that? | `dos verify` / `dos test-witness` | [ci-passed-but-the-feature-isnt-there](ci-passed-but-the-feature-isnt-there.md) |
| How do I audit AI-generated commits across a repo? | `dos commit-audit` | [audit-which-commits-were-ai-and-did-they-ship](audit-which-commits-were-ai-and-did-they-ship.md) |
| Two Claude Code agents on one branch keep clobbering each other — how do I fix it? | `dos arbitrate` | [two-claude-code-agents-on-one-branch](two-claude-code-agents-on-one-branch.md) |
| How do I make an agent's "done" mean a checkable effect, not a sentence? | `dos verify` (stop hook) | [make-agent-done-mean-a-checkable-effect](make-agent-done-mean-a-checkable-effect.md) |
| Can I trust an AI coding agent's pull request? | `dos commit-audit` / `dos verify` | [can-i-trust-a-coding-agents-pull-request](can-i-trust-a-coding-agents-pull-request.md) |
| How do I enforce that an agent actually ran the tests it claims it ran? | `dos test-witness` / `dos coverage` | [enforce-that-an-agent-ran-the-tests-it-claims](enforce-that-an-agent-ran-the-tests-it-claims.md) |
| How do I catch an agent that fakes tool calls or fabricates output? | `dos verify` / `dos commit-audit` | [catch-an-agent-that-fakes-tool-calls-or-output](catch-an-agent-that-fakes-tool-calls-or-output.md) |
| The last-writer-wins problem in multi-agent shared memory — how do I stop it? | `dos arbitrate` | [last-writer-wins-multi-agent-shared-memory](last-writer-wins-multi-agent-shared-memory.md) |
| How do I prevent context poisoning from an agent's own prior outputs? | `recall` (MCP) | [prevent-context-poisoning-from-an-agents-own-outputs](prevent-context-poisoning-from-an-agents-own-outputs.md) |
| How do I coordinate multiple AI agents without a central orchestrator? | `dos arbitrate` | [multi-agent-coordination-without-a-central-orchestrator](multi-agent-coordination-without-a-central-orchestrator.md) |
| How do I build a builder-validator chain that separates the generator from the evaluator? | `dos verify` / `dos commit-audit` | [builder-validator-chain-separate-generator-from-evaluator](builder-validator-chain-separate-generator-from-evaluator.md) |
| What is a trust substrate for a fleet of autonomous AI agents? | `dos verify` / `dos arbitrate` | [trust-substrate-for-a-fleet-of-autonomous-agents](trust-substrate-for-a-fleet-of-autonomous-agents.md) |
| Why does my AI agent ignore the rules in CLAUDE.md — how do I make them stick? | `dos commit-audit` (exit code) | [why-does-my-agent-ignore-the-rules-in-claude-md](why-does-my-agent-ignore-the-rules-in-claude-md.md) |
| How do I detect a no-op commit from an agent? | `dos commit-audit` | [detect-a-no-op-commit-from-an-agent](detect-a-no-op-commit-from-an-agent.md) |
| How do I verify an LLM didn't hallucinate a function or API that doesn't exist? | `dos test-witness` / `dos commit-audit` | [verify-an-llm-didnt-hallucinate-a-function-or-api](verify-an-llm-didnt-hallucinate-a-function-or-api.md) |
| How do I use a hidden test split to stop agents overfitting the visible tests? | `dos improve` / `dos reward` | [hidden-test-split-to-stop-agents-overfitting](hidden-test-split-to-stop-agents-overfitting.md) |
| Governance is why agentic AI projects get canceled — what is the missing layer? | `dos verify` / `dos arbitrate` | [governance-for-agentic-ai-projects-that-keep-getting-canceled](governance-for-agentic-ai-projects-that-keep-getting-canceled.md) |
| How do I make any agent skill verify its own work? | `dos-skillify` / `dos verify` / `dos commit-audit` | [make-any-agent-skill-verify-its-own-work](make-any-agent-skill-verify-its-own-work.md) |
| How do I add the DOS plugin to a private company Claude Code marketplace? | `dos doctor` / `/dos-kernel:dos-setup` | [add-the-dos-plugin-to-a-private-company-marketplace](add-the-dos-plugin-to-a-private-company-marketplace.md) |
| How do I stop an AI agent from making fake tests? | `dos test-witness` / `dos commit-audit` | [stop-ai-making-fake-tests](stop-ai-making-fake-tests.md) |
| My AI writes tests that pass but test nothing | `dos test-witness` | [ai-generated-tests-that-pass-but-test-nothing](ai-generated-tests-that-pass-but-test-nothing.md) |
| My AI mocks everything and the tests are useless | `dos test-witness` | [ai-mocks-everything-tests-are-useless](ai-mocks-everything-tests-are-useless.md) |
| How do I tell if my AI-generated tests are real or lying? | `dos test-witness` | [are-my-ai-generated-tests-real](are-my-ai-generated-tests-real.md) |
| Mutation testing vs a test-witness gate for AI tests? | `dos test-witness` | [mutation-testing-vs-test-witness-for-ai-tests](mutation-testing-vs-test-witness-for-ai-tests.md) |
| How do I make an AI agent write tests that actually assert something? | `dos test-witness` | [make-ai-write-tests-that-actually-assert](make-ai-write-tests-that-actually-assert.md) |
| I have 100% coverage but the AI's tests are worthless | `dos test-witness` / `dos coverage` | [coverage-is-green-but-tests-are-worthless](coverage-is-green-but-tests-are-worthless.md) |
| How does DOS fit into my CI/CD pipeline? | `dos commit-audit` / `dos verify` / `dos arbitrate` | [dos-for-ci-cd](dos-for-ci-cd.md) |
| How do I stop re-reviewing code a machine already verified? | `dos commit-audit` (residual review) | [stop-re-reviewing-code-the-machine-already-verified](stop-re-reviewing-code-the-machine-already-verified.md) |
| How do I gate a CI job on whether an agent's claim is actually backed? | `dos commit-audit` / `dos verify` (exit code) | [gate-a-ci-job-on-an-agents-claim](gate-a-ci-job-on-an-agents-claim.md) |
| How do I wire a trust gate into Claude Code, Cursor, or Codex with one command? | `dos init --hooks` | [wire-a-trust-gate-into-claude-code-cursor-codex](wire-a-trust-gate-into-claude-code-cursor-codex.md) |
| How does an agent read a workspace's layout instead of hardcoding it? | `dos doctor --json` | [machine-readable-workspace-report-for-an-agent](machine-readable-workspace-report-for-an-agent.md) |
| How do I check my agent's trust-gate hooks haven't silently stopped enforcing? | `dos doctor --wiring` | [check-the-agent-guardrail-hooks-havent-drifted](check-the-agent-guardrail-hooks-havent-drifted.md) |
| Is this agent output a real answer or a leaked reasoning log? | `dos answer-shape` | [is-this-agent-output-an-answer-or-a-leaked-reasoning-log](is-this-agent-output-an-answer-or-a-leaked-reasoning-log.md) |
| How do I price a parallel agent fan-out before launching it? | `dos arbitrate` (plan_price) | [price-a-parallel-agent-fan-out-before-launching-it](price-a-parallel-agent-fan-out-before-launching-it.md) |
| How do I add an agent trust gate using only exit codes, no plugin system? | `dos verify` / `dos commit-audit` (exit code) | [add-a-trust-gate-with-only-exit-codes](add-a-trust-gate-with-only-exit-codes.md) |
| How do I detect which model died across a fleet and reroute? | `dos model-health` | [detect-a-model-outage-mid-fleet-and-reroute](detect-a-model-outage-mid-fleet-and-reroute.md) |
| Is `dos-kernel` the real package — how do I avoid the squatter? | `pip install dos-kernel` / `dos doctor` | [is-dos-kernel-the-real-package-supply-chain](is-dos-kernel-the-real-package-supply-chain.md) |
| How does an agent auto-pick a free, non-colliding lane to work in? | `dos arbitrate` / `dos pickable` | [auto-pick-a-free-lane-for-an-agent](auto-pick-a-free-lane-for-an-agent.md) |

## How to read the numbers on these pages

Every result on every page is a **J** — a count of failures *blocked off ground
truth*, never a downstream outcome delta. "Blocked 10 real over-claims against
the environment's own database hash" is a proven sentence; "made the fleet 10%
better" is a different sentence, and these pages do not write it. Each number is
scored against a **witness whose bytes the judged agent did not author** — git
ancestry, an environment's database state, a task's own oracle — and links to
the benchmark or design doc that reproduces it. That is the same rule the kernel
applies to agents, applied to our own claims.

## Embed an answer card

Run a repo that catches agent over-claims with DOS? Paste this into your README
to point readers (and the models that crawl it) at the canonical answer — see
[the answer-card block in `docs/BADGE.md`](../BADGE.md#embed-an-answer-card).
