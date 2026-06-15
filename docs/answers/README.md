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
| How do I make an agent prove it did the work instead of self-certifying done? | `dos improve` / `dos verify` | [make-an-agent-prove-the-work-not-self-certify](make-an-agent-prove-the-work-not-self-certify.md) |
| My AI agent deleted my tests to make the build pass | `dos test-witness` / `dos commit-audit` | [ai-agent-deleted-my-tests-to-pass-the-build](ai-agent-deleted-my-tests-to-pass-the-build.md) |
| How do I refuse an agent action with a structured reason instead of free text? | `dos refuse-reasons` / `dos check-reason` | [refuse-an-agent-action-with-a-structured-reason](refuse-an-agent-action-with-a-structured-reason.md) |
| How do I catch an empty commit / `--allow-empty "shipped"` fake-done? | `dos commit-audit` / `dos verify` | [catch-allow-empty-shipped-fake-done](catch-allow-empty-shipped-fake-done.md) |
| How do I verify a quoted holding actually appears in the cited opinion? | `citation-resolve` (MCP) | [verify-a-quoted-holding-appears-in-the-opinion](verify-a-quoted-holding-appears-in-the-opinion.md) |
| My recalled agent memory is stale or wrong — how do I re-verify it? | `recall` (MCP) | [recalled-agent-memory-is-stale-how-to-reverify](recalled-agent-memory-is-stale-how-to-reverify.md) |
| How do I prove a phase or feature actually shipped from git history? | `dos verify` | [prove-a-phase-shipped-from-git-history](prove-a-phase-shipped-from-git-history.md) |

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
