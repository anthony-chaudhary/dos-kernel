# How to add a guardrail to a coding agent that has no plugin or hook system

> If your agent runs a command after it edits — **aider**, a CI step, a git
> hook, or a hook-less CLI like Windsurf / Warp / Zed — you already have the
> only seam you need. `pip install dos-kernel`, then point that command at a
> `dos` verb: the exit code is the verdict. The PyPI name is `dos-kernel` — the
> bare `dos` package is an unrelated squatter; never install that.

## The short answer

Most guardrail tutorials assume a plugin API or a structured hook system. Many
coding agents don't have one — but nearly all of them **run a command** at some
point (a lint command, a test command, a CI step, a git hook) and branch on its
**exit code**. That exit code is a universal contract every shell author already
knows: `0` means ok, non-zero means a problem. DOS speaks it natively — every
`dos` verb makes its verdict the process exit code — so you wire a kernel
guardrail into any command-running environment with **no adapter, no plugin, and
no MCP client**.

The highest-leverage verb here is `dos commit-audit`: after the agent commits,
it grades whether the commit's *subject* (which the agent wrote) is witnessed by
its *diff* (which git wrote). A `fix: handle the empty case` whose diff only
touched a comment exits `1` — and your agent's own "command failed, fix it" loop
gets handed the verdict.

```bash
# aider runs --test-cmd after every edit and feeds a non-zero exit back to the
# model for an auto-fix turn — so a kernel verdict drops in with one flag:
aider --test-cmd 'dos commit-audit --workspace . HEAD' --auto-test
```

## The evidence

DOS verdicts are pure functions of a witness whose bytes the judged agent did
not author. The numbers behind the verb this page uses, each scored against such
a witness:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| `commit-audit` flags a commit whose subject its own diff doesn't back | J = the claim-vs-diff verdict is `diff-witnessed` vs `subject-only`, decided from the commit's files — author-neutral (a human's `fix:` touching only a README is the same unwitnessed claim as an agent's `--allow-empty "shipped"`) | the commit's diff, which git authored, not the agent that wrote the subject | [`docs/214`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/214_commit-audit-the-author-neutral-claim-vs-diff-floor.md) |
| The verdict is the exit code — no JSON, no parser, no DOS coupling | `commit-audit`: `0` clean · `1` an unwitnessed claim · `2` unreadable ref (verified against the shipped CLI) | the `dos` process authors the exit code, not the judged agent (the actor-witness split) | [`docs/226`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/226_the-hook-exit-classifier-a-shell-scripts-exit-code-as-a-verdict.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. `commit-audit` grades *did the diff do the KIND of thing the
subject claimed* — never *was it correct* (run your tests for that); it
ABSTAINs (exit `0`) on `wip`/`merge`/`bump` and any commit with no concrete
claim, so it fires only where a real claim and a contradicting diff coexist.

## The one command, three places

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
```

**aider** — a verdict inside its after-every-edit auto-fix loop (or pin it in
`.aider.conf.yml`; aider's YAML keys use dashes):

```yaml
# .aider.conf.yml
test-cmd: dos commit-audit --workspace . HEAD
auto-test: true
```

**a git `pre-push` hook** — the last gate before work leaves the machine, so an
over-claiming commit never reaches the remote or the next agent that trusts the
log:

```bash
# .git/hooks/pre-push  (chmod +x) — audit the commits this push adds
dos commit-audit --workspace . "$(git rev-parse '@{push}')..HEAD" \
  || { echo "dos: a commit's claim isn't witnessed by its diff" >&2; exit 1; }
```

**a generic CI / runner step** — any pipeline the GitHub Action and GitLab
template don't reach (Jenkins, Buildkite, Make, `package.json`):

```bash
dos commit-audit --workspace . "origin/main..HEAD"   # exit 1 if any commit over-claims
```

Want it advisory first? Add `--warn-only` — it prints findings and always exits
`0`, so nothing blocks while a team gets used to it. Dropping the flag flips it
to enforcement. That one knob is the whole advisory↔enforcement choice at this
tier.

## Why this works when there's no plugin API

The exit code is authored by the **`dos` process**, not by the agent it judges —
a third party's verdict on the agent's work. That is the same actor-witness
split the kernel is built on, so a `commit-audit` exit of `1` is the same
non-forgeable signal whether it's read by aider's loop, a Makefile, or a CI
YAML. "I only have a shell that runs a command" is not a lesser integration: it
carries the same verdict the MCP tool and the hook carry, minus the JSON. It is
also the *honest* surface for a hook-less host — rather than invent a fake
enforcement envelope a host would never read, DOS rides the one contract every
runner already has.

## Sources / reproduce

- [The exit-code tier cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-exit-code-tier.md) — runnable recipes for aider, git pre-push, and a generic step, plus `dos hook-exit` for wrapping a non-DOS script onto the intervention ladder.
- [`docs/226`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/226_the-hook-exit-classifier-a-shell-scripts-exit-code-as-a-verdict.md) — the exit-code classifier: why a script's exit code is sound evidence.
- [FAQ: Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?](../FAQ.md#does-dos-work-with-claude-code-cursor-codex-gemini-cli-or-other-agent-runtimes) — the three surfaces (MCP, hooks, exit-code).
- [Do AI coding agents lie about what they shipped?](do-ai-coding-agents-lie-about-what-they-shipped.md) — the failure this guardrail catches, as a story.

> The kernel is the part that doesn't believe the agents.
