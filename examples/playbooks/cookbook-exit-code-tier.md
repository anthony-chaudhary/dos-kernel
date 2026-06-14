# Cookbook — the exit-code tier: integrate any environment that runs a command

> **You do not need a hook adapter. An exit code is enough.** DOS has three
> integration tiers, in order of how much the host has to know about DOS:
>
> | Tier | What the host does | Surface | Posture |
> |---|---|---|---|
> | **MCP** | the agent *calls* the referee | `dos-mcp` (tools over JSON-on-stdio) | advisory |
> | **Hooks** | the host *blocks* on a verdict | `dos init --hooks <host>` (a per-host dialect) | enforcement |
> | **Exit code** | *anything* that runs a command reads `$?` | a `dos` verb's own exit code | advisory **or** enforcement, the host decides |
>
> The first two need DOS to speak the host's language (an MCP client; a hook
> dialect). The **exit-code tier needs nothing** — every `dos` verb already
> follows the universal Unix convention every shell author knows (`0` = ok,
> non-zero = a verdict), so DOS drops into the long tail no dialect will ever
> cover: every CLI agent with a lint/test hook, every bespoke runner, every CI
> that the GitHub Action and the GitLab template don't reach, and every
> hook-less host (Windsurf, Warp, Zed today). The verdict *is* the exit code.

This page is the third tier. For the MCP and hook tiers see
[the agent-hosts section of the README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md#give-your-agent-a-lie-detector-mcp);
for CI/pre-commit specifically see
[`cookbook-ci-integration.md`](cookbook-ci-integration.md). The recipes here
are the ones those pages don't cover: an *agentic CLI* (aider), a *local
network boundary* (git pre-push), and a *generic command step* for any runner.

## Why the exit code is sound evidence (not a degraded fallback)

The exit code is authored by the **`dos` process**, not by the agent it is
judging — it is a third party's verdict on the agent's work, which is the whole
actor-witness split the kernel is built on (the byte-author is not the judged
party). A `dos commit-audit` that exits `1` is the same non-forgeable signal
whether it is read by a Go program, a Makefile, a CI YAML, or an LLM's auto-fix
loop. So "I only have a shell that runs a command" is not a lesser integration —
it carries the same verdict the MCP tool and the hook carry, minus the JSON.

The exit-code contracts these recipes rely on (all verified against the shipped
CLI — run `dos <verb> --help` to confirm on your version):

| Command | Exit codes |
|---|---|
| `dos commit-audit REF` | `0` clean · `1` an unwitnessed claim found · `2` unreadable ref |
| `dos verify P PH` | `0` shipped · `1` not shipped |
| `dos doctor --check` | `0` clean · `1` a finding fired |
| `dos hook-exit --code N` | maps a *wrapped* script's code onto the intervention ladder: PASS `0` · contract-error `2` · BLOCK `3` · WARN `4` · DEFER `5` · OBSERVE `6` (the verb's *own* exit is the rung, so a wrapper branches with no JSON parser) |

`--warn-only` on `commit-audit` flips it to observe-only (prints findings,
always exits `0`) — the one knob that turns enforcement into advisory at this
tier.

---

## Recipe 1 — aider: a DOS verdict inside a top-tier CLI agent, zero hooks

[aider](https://aider.chat) runs a lint command and a test command **after
every edit** and, when one returns a non-zero exit code, feeds that command's
output back to the model for an automatic fix loop
([lint/test docs](https://aider.chat/docs/usage/lint-test.html)). That is
exactly the seam the exit-code tier was built for: point aider's test command at
a `dos` verb and a kernel verdict drops into a top-tier agentic CLI with **no
hook machinery, no MCP client, no DOS-specific config** — just a command that
exits non-zero.

The highest-value verb here is `dos commit-audit`: after aider commits its own
edit (aider auto-commits by default), audit whether that commit's *subject*
matches its *diff*. When aider writes `fix: handle the empty case` but the diff
only touched a comment, `commit-audit` exits `1`, aider reads the failure, and
the model gets told its own claim isn't witnessed — *inside its own fix loop*.

```bash
# one flag — audit the HEAD commit aider just made against its diff
aider --test-cmd 'dos commit-audit --workspace . HEAD' --auto-test
```

Or pin it in the project's `.aider.conf.yml` so every session in the repo
inherits it (note: aider's YAML keys use dashes):

```yaml
# .aider.conf.yml
test-cmd: dos commit-audit --workspace . HEAD
auto-test: true
```

What aider sees on a clean commit is `exit 0` — silence, the loop proceeds. On
an over-claim it sees `commit-audit`'s finding line and `exit 1`, and starts a
fix turn with the verdict as context. You have turned aider's generic
"tests failed, fix it" loop into a "your claim isn't witnessed, make it true"
loop — with one line and zero coupling.

> **Scope.** `commit-audit` grades *did the diff do the KIND of thing the
> subject claimed*, never *was it correct* — keep your real test suite as the
> `--lint-cmd` or a second command for correctness; this rides *beside* it as the
> claim-honesty gate. It ABSTAINs (exits `0`) on `wip`/`merge`/`bump` and any
> commit with no concrete claim, so it only fires where a real claim and a
> contradicting diff coexist — it won't spuriously derail the loop.

For a repo that stamps phases, `dos verify` works the same way — point
`--test-cmd` at `dos verify <SERIES> <PHASE>` to make aider's loop unable to
settle until the phase it claims is actually attributed in history.

---

## Recipe 2 — git pre-push: the last gate before work leaves the machine

The cheapest enforcement boundary that needs no host at all: a `pre-push` hook
audits every commit about to leave the machine, so an over-claiming commit never
reaches the remote (or the reviewer, or the next agent that trusts the log). The
exit code is the verdict — a non-zero `pre-push` aborts the push.

```bash
#!/usr/bin/env bash
# .git/hooks/pre-push   (chmod +x)
#
# Audit every commit being pushed (claim vs diff) before it leaves the machine.
# git feeds "<local-ref> <local-sha> <remote-ref> <remote-sha>" lines on stdin.
rc=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "0000000000000000000000000000000000000000" ] && continue  # branch delete
  if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
    range="$local_sha"                 # new branch: audit just the tip (or pick a base)
  else
    range="$remote_sha..$local_sha"     # the commits this push actually adds
  fi
  if ! dos commit-audit --workspace . "$range"; then
    echo "dos: refusing push — a commit's claim isn't witnessed by its diff (range $range)" >&2
    echo "     fix the subject or the change, then push again (or --no-verify to override)" >&2
    rc=1
  fi
done
exit $rc
```

Want it advisory first (print, never block) while a team gets used to it? Add
`--warn-only` to the `commit-audit` call — it always exits `0`, so the push
proceeds and the finding is just printed. Flip to enforcement by dropping the
flag. That single knob is the whole advisory↔enforcement choice at this tier.

A new agent or teammate installs it once with no DOS knowledge:

```bash
pip install dos-kernel                       # the dist name; the bare `dos` on PyPI is unrelated
# (paste the script above into .git/hooks/pre-push, then:)
chmod +x .git/hooks/pre-push
```

---

## Recipe 3 — a generic command step: any runner, any host

For everything the GitHub Action ([`verify-action/`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md))
and the GitLab template ([`gitlab-ci/`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/gitlab-ci/README.md))
don't reach — a Jenkins stage, a Buildkite step, a Makefile target, a `package.json`
script, a Taskfile, a bare `bash` in someone's bespoke runner — the integration
is the same one line. The runner already branches on exit codes; `dos` already
emits them.

```bash
# audit a PR's own commits against a base — fail if any over-claims
dos commit-audit --workspace . "origin/main..HEAD"
# exit 0 clean · 1 an unwitnessed claim found · 2 unreadable ref
```

The only portable pitfall is **shallow clones**: `commit-audit` and `verify`
read git *ancestry*, so a runner that fetches a shallow tip (many CI defaults do)
audits against a hole. Fetch full history before the step — `git fetch
--unshallow` (or the runner's "full clone" knob; on GitHub Actions it is
`fetch-depth: 0`, on GitLab `GIT_DEPTH: "0"`).

As targets in the tools your repo already has:

```makefile
# Makefile
.PHONY: audit
audit:
	dos commit-audit --workspace . "origin/main..HEAD"   # claim-vs-diff gate; exit code is the verdict
```

```jsonc
// package.json
{ "scripts": { "audit:claims": "dos commit-audit --workspace . origin/main..HEAD" } }
```

### Wrapping a non-DOS script onto the intervention ladder

If what you have is *not* a `dos` verb but an arbitrary script (a linter, a
policy probe, a smoke test) and you want its exit code routed onto DOS's
intervention vocabulary — so a `0` is PASS, a `2` is BLOCK, an unanticipated
non-zero degrades to WARN rather than a silent pass — `dos hook-exit` is the
bridge ([docs/226](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/226_the-hook-exit-classifier-a-shell-scripts-exit-code-as-a-verdict.md)):

```bash
your-policy-script.sh
dos hook-exit --code $?        # PASS 0 · BLOCK 3 · WARN 4 · DEFER 5 · OBSERVE 6 · contract-error 2
case $? in
  0) ;;                                       # PASS — proceed
  3) echo "BLOCK"; exit 1 ;;                   # blocking error → stop
  4) echo "WARN (proceeding)" ;;               # non-blocking → surface, continue
  *) echo "other intervention" ;;
esac
```

The default map is the universal convention (`0` ok, `2` blocking, any other
non-zero a non-blocking warning); a fleet declares its own in `dos.toml`
`[hook_exit]` so `exit 3 = DEFER` org-wide is data, not a code change.

---

## Recipe 4 — Windsurf (and Warp, Zed): the hook-less editor, for a vibe coder

> **No hooks here — the exit code is the verdict.** Windsurf, Warp, and Zed
> can't take the `dos init --hooks <host>` wiring that Cursor and Claude Code
> do: there is no PreToolUse/Stop seam for DOS to block on. Do not look for a
> hooks install for these editors — there isn't one, and a recipe that promised
> one would be lying. What these editors *can* do is run a command in a terminal
> and read its exit code. That is the whole exit-code tier (the framing and the
> soundness argument above apply verbatim), and it is enough: the `dos` process
> authors the verdict, not the in-editor agent it's judging.

If you're a vibe coder shipping in Windsurf and your Cascade agent just told you
the feature is done, here is how to make the editor check that claim against git
evidence instead of taking the agent's word. Two moves: a check you (or the
agent) can run, and a rule that points the agent at it before it says "done."

### 1. The check — red is "not yet," green is "shipped"

Open Windsurf's terminal (or wire it as a Windsurf **workflow**, below) and run
the truth syscall on the phase the agent claimed. The exit code *is* the verdict
— `0` shipped, `1` not shipped:

```bash
# did the phase the agent claimed actually land in git history?
dos verify --workspace . docs/126 P1
# SHIPPED docs/126 P1 8a7a259 (via grep-subject)   → exit 0   (real run, this repo)
# NOT_SHIPPED docs/999 NEVER (via none)            → exit 1   (nothing in history backs the claim)
```

Two equally hook-less checks for a vibe coder who isn't stamping numbered
phases:

```bash
# "the agent committed — does its commit message match what the diff did?"
dos commit-audit --workspace . HEAD     # exit 0 clean · 1 an unwitnessed claim · 2 unreadable ref
```

`dos verify` answers "did this ship?"; `commit-audit` answers "does this commit's
*subject* match its *diff*?" — both from git, neither from the agent's narration.
(See the exit-code table at the top of this page and the
[verb table in the README](README.md#the-verbs-by-the-question-they-answer)
for the full contracts.)

> **Why not `dos complete`?** `dos complete` needs a `--run-id` and a declared
> intent ledger to compute `residual = declared − verified`; a vibe coder in
> Windsurf has neither. `dos verify` (one phase) and `dos commit-audit` (the
> last commit) are the no-setup checks for this seam — use them.

### 2. The Windsurf workflow — `/verify` in the editor

Windsurf reads `/`-invokable workflows from `.windsurf/workflows/*.md`. Drop this
in and type `/verify` in Cascade; the red/green is the exit code:

```md
<!-- .windsurf/workflows/verify.md -->
---
description: Check a claimed phase against git evidence — exit code is the verdict
---

Run `dos verify --workspace . docs/<plan> <phase>` in the terminal.
- exit 0 → SHIPPED, the claim is witnessed in git history.
- exit 1 → NOT_SHIPPED, history does not back the claim — keep working.
No hooks are involved; the `dos` process authors the verdict, not the agent.
```

### 3. The rule — make the agent run it before it claims done

Windsurf reads project rules from `.windsurf/rules/*.md` (the modern form) or a
plain `.windsurfrules` file at the repo root (the legacy form). Either keeps the
honesty gate in the agent's context. Copy-pasteable, modern form:

```md
<!-- .windsurf/rules/dos-verify.md -->
---
trigger: always_on
---

# Don't claim done on your own say-so

Before telling me a numbered phase is complete, run in the terminal:
`dos verify --workspace . docs/<plan> <phase>`
- exit code 0 → it shipped; you may say done.
- exit code 1 → it did NOT ship; keep working, do not claim done.

Before claiming a commit does what its message says, run:
`dos commit-audit --workspace . HEAD`   (exit 0 = clean, exit 1 = the subject overclaims the diff).

There are NO DOS hooks in this editor — the exit code is the verdict. Read it,
don't narrate around it.
```

Legacy single-file form (same content, plain text at the repo root) for an older
Windsurf or a team that hasn't migrated:

```text
# .windsurfrules
Before claiming a numbered phase is done, run `dos verify --workspace . docs/<plan> <phase>`
in the terminal: exit 0 = shipped (you may say done), exit 1 = not shipped (keep working).
Before claiming a commit matches its message, run `dos commit-audit --workspace . HEAD`:
exit 0 = clean, exit 1 = the subject overclaims the diff.
No DOS hooks exist in this editor — the exit code is the verdict.
```

That's the whole on-ramp: no hook adapter, no MCP client, no `dos init`. The
agent runs a command; you (and it) read the exit code; a green is a witnessed
ship and a red is "not yet." For the gentler, non-coder framing of the same
idea — "Probably yes" / "Not yet" instead of exit codes — see
[`00_non-coder-verdict-in-15-minutes.md`](00_non-coder-verdict-in-15-minutes.md)
and the front door
[`00b_did-my-ai-do-it.md`](00b_did-my-ai-do-it.md).

---

## Notes

- **No `--force` in automation.** A refuse/non-zero is information; forcing past
  it defeats the gate. `--force` is an operator action.
- **`--warn-only` is the one advisory knob** at this tier — it flips
  `commit-audit` from blocking to print-only without changing anything else.
- **Full history, always.** Every ancestry-reading verb (`verify`,
  `commit-audit`) needs a non-shallow clone; the only recurring footgun here is a
  shallow CI checkout.
- For the **MCP** (advisory, agent-calls-it) and **hook** (enforcement,
  host-blocks-on-it) tiers, see
  [the agent-hosts README section](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md#give-your-agent-a-lie-detector-mcp);
  for the **Python-API** equivalent of these gates (embedding instead of
  shelling), see [`cookbook-python-api.md`](cookbook-python-api.md).
