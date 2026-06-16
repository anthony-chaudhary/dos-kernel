# How do I add an agent trust gate using only exit codes, no plugin system?

> If your runtime can run a shell command and read `$?`, it can enforce DOS — no plugin, no hook adapter, no MCP client. `pip install dos-kernel`, then `dos verify` (or `dos commit-audit`): the exit code IS the verdict — `0` shipped, non-zero not. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

This is the lowest-common-denominator tier: a self-report is not evidence, so the gate reads an artifact the agent didn't author and leaves the verdict in its exit code — the one contract every shell author already knows (`0` ok, non-zero a problem). You don't need a plugin API or a structured hook system. You need a command runner and `$?`. Every `dos` verdict verb makes its verdict the process exit code, so `dos verify docs/126 P1 ; echo $?` prints `0` when git history backs the claimed phase and `1` when nothing does — and any environment that can branch on that number can refuse to let the agent call the work done.

That reaches a population the MCP and hook tiers never do: the hook-less editors (Windsurf, Warp, Zed), a bespoke runner, a bare CI step, a git `pre-push` hook, a Makefile target. None of them speak an MCP dialect or take a `dos init --hooks` wiring, but all of them run a command and read its exit code. The verdict is the same non-forgeable signal whether a Go program, a YAML pipeline, or an LLM's auto-fix loop reads it — because the `dos` process authored that exit code, not the agent it is judging.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| `dos verify` makes did-it-ship the exit code, no plugin needed | `dos verify P PH`: `0` shipped · `1` not shipped — read straight from git ancestry + the stamp grammar, never a self-report | git history (commit ancestry), which the `dos` process reads — not the agent that claimed the phase | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The exit code is sound evidence, not a degraded fallback | the `dos` process authors the exit code; a `commit-audit` exit of `1` is the same verdict whether read by a Makefile, a CI YAML, or an LLM's fix loop — minus the JSON | the verdict-emitting `dos` process, a third party to the judged agent (the actor-witness split) | [`examples/playbooks/cookbook-exit-code-tier.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-exit-code-tier.md) |
| The verb family whose exit code is the verdict | `verify`, `commit-audit`, `answer-shape`, `test-witness` — each maps its verdict token to a distinct exit code so a shell can branch with no parser | each verdict is a pure function of evidence the judged agent did not author | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |

The exit-code contracts are verified against the shipped CLI (`dos <verb> --help` confirms them on your version): `dos verify P PH` exits `0` shipped / `1` not shipped; `dos commit-audit REF` exits `0` clean / `1` an unwitnessed claim / `2` unreadable ref. `--warn-only` on `commit-audit` flips it to print-only (always exits `0`) — the one knob that turns enforcement into advisory at this tier.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
# did the phase the agent claimed actually land in git history?
dos verify --workspace . docs/126 P1 ; echo "exit=$?"
```

```text
SHIPPED docs/126 P1 8a7a259 (via grep-subject)
exit=0
# and when nothing in history backs the claim:
#   NOT_SHIPPED docs/999 NEVER (via none)
#   exit=1
```

`0` means git ancestry witnesses the phase; `1` means the claim stands on nothing. No JSON, no parser, no DOS-specific config — just a command and `$?`. The companion `dos commit-audit --workspace . HEAD` answers a different question with the same shape: does the last commit's *subject* match its *diff*? (`0` clean, `1` the subject overclaims, `2` unreadable ref.)

## What this does — and does not — certify

`dos verify` certifies that a phase the agent claimed is attributed in git history; `commit-audit` certifies that a commit's subject matches the KIND of change its diff made. Neither certifies *correctness*: a phase can ship and still be wrong, and `commit-audit` grades the claim-vs-diff match, never whether the code is right — keep your real test suite for that. `commit-audit` ABSTAINs (exits `0`) on `wip`/`merge`/`bump` and any commit with no concrete claim, so it only fires where a real claim and a contradicting diff coexist. The exit-code tier is advisory or enforcement — the host decides which by whether it blocks on the non-zero; DOS only guarantees the number is honest.

## Sources / reproduce

- [The exit-code tier cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-exit-code-tier.md) — runnable recipes for aider, a git `pre-push` hook, a generic runner step, and the hook-less editors (Windsurf, Warp, Zed), plus `dos hook-exit` for wrapping a non-DOS script onto the intervention ladder.
- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the verbs whose exit code is the verdict (`verify`, `commit-audit`, `answer-shape`, `test-witness`) and their per-token exit codes.
- [How to add a guardrail to a coding agent with no plugin or hook system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — the same exit-code seam, wired into aider, pre-push, and CI.
- [Deterministic hook vs an agent skill — which one actually enforces](deterministic-hook-vs-agent-skill-which-enforces.md) — why a gate that reads `$?` enforces where a prompt rule only advises.
- [FAQ: Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?](../FAQ.md#does-dos-work-with-claude-code-cursor-codex-gemini-cli-or-other-agent-runtimes) — the three surfaces (MCP, hooks, exit-code).

## Also asked as

- How do I gate an AI agent with just a shell exit code?
- Can I enforce DOS without installing a plugin or MCP server?
- Trust gate for an agent in an editor that has no hook system
- Make `dos verify` block "done" from a CI step or git hook
- The simplest possible way to check an agent's work — no integration
- How do I wire a kernel verdict into a bare bash runner?
- Exit-code-only trust check for a hook-less coding agent (Windsurf, Warp, Zed)

> The kernel is the part that doesn't believe the agents.
