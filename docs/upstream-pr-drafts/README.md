# Upstream PR drafts — Flip A (get DOS into runtimes' recommended examples)

> **What this is.** Ready-to-file PR / listing-entry drafts for getting DOS's
> deterministic done-/deny-check into the *recommended-examples* surfaces of the
> agent runtimes and community lists that sessions already flow through. Each draft
> is grounded against the target's **real** contribution mechanism (checked, not
> assumed) and links back to [`../UPSTREAM.md`](../UPSTREAM.md), the canonical
> maintainer-facing recipe.
>
> **These are drafts a human files.** A merge into someone else's repo is their
> maintainers' call; this directory does the work that makes the PR real and
> credible (the exact bytes, the right file, the honest caveat, the done-condition a
> reviewer checks) so filing it is a copy-paste, not a research project.

## Why these targets

Agent sessions funnel through a short list of chokepoints; one community list or one
runtime's docs reaching majority readership is worth more than thousands of
individual installs. These targets are ranked by *readership × how-PR-friendly*, and
each is confirmed to accept exactly this kind of entry:

| # | Target | Surface | Confirmed mechanism | Draft |
|---|---|---|---|---|
| 1 | `hesreallyhim/awesome-claude-code` | the canonical curated list (hooks / MCP / plugins) | PR adding a one-line entry under the matching section | [01-awesome-claude-code.md](01-awesome-claude-code.md) |
| 2 | `pascalporedda/awesome-claude-code` | a hooks-focused hub | explicitly "open an issue or PR and add your own" | [02-awesome-claude-code-hooks-hub.md](02-awesome-claude-code-hooks-hub.md) |
| 3 | community Codex / Gemini hook-example repos | starter `config.toml` / `settings.json` examples | PR adding a DOS example file or list entry | [03-codex-gemini-examples.md](03-codex-gemini-examples.md) |

## The discipline (carried from the prior OSS-contribution sweep)

Read the target's **actual** bytes before filing: confirm the section exists, the
entry format matches, and the contribution guidelines accept a third-party tool.
Every claim in a draft (a byte format, a host caveat) is verifiable from the DOS repo
— file nothing you can't back from `dos hosts` or a passing test. The negative result
("this list doesn't take third-party tools") is as load-bearing as the positive one:
it stops a marginal PR from going out.

## Before filing — the leak gate

PR/issue text is public and skips the repo's automated leak gate. Pipe every draft
body through the scanner before posting (a hit is a refusal, not a warning):

```bash
python scripts/leak_scan.py --stdin < docs/upstream-pr-drafts/01-awesome-claude-code.md
```
