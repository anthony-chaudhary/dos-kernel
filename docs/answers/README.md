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
