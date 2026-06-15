# The "Verified by DOS" badge

> **Paste-me adoption mark.** Drop it in your README so the world can see your
> repo gates its agents' ship-claims with [`dos verify`](../README.md) — ground
> truth from git history, **not the agent's word**.

[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

> **Just caught your AI in a lie and want to *share* it?** This page is the
> badge you wear after. The shareable-moment on-ramp — how to make your own
> caught-lie screenshot and post it — is **[docs/SHARE.md](SHARE.md)**.

## What it means

A repo wearing this badge **runs `dos verify` in its ship-gate** — every claim
that a phase/PR "shipped" is checked against git ancestry by the kernel before it
counts, instead of being taken on the worker's self-report. The badge is the
visible tell of one discipline: *the kernel is the part that doesn't believe the
agents*, wired into your pipeline.

## Paste this (the shields.io static endpoint — no service to run)

The recommended form. It hits shields.io's **static** badge endpoint (a fixed
label/colour, no live query), so there's nothing to host and nothing to break,
and it links back to the DOS repo:

```markdown
[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)
```

Rendered:
[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

- `verified%20by` / `DOS` / `2ea44f` are the label, message, and the DOS ship
  green (`#2ea44f`) — the same green the oracle prints `SHIPPED` in.
- The link target is the DOS repo, so a reader can click through to *why* the
  badge means something.

## Alternative — vendor the SVG, depend on no one

If you'd rather not call out to shields.io at render time (air-gapped repo,
zero-external-host policy), copy the hand-built badge into your own tree and
reference it as a local `<img>`. It's a small, self-contained, two-segment pill
that versions cleanly as text:

```html
<a href="https://github.com/anthony-chaudhary/dos-kernel">
  <img src="docs/assets/verified-by-dos.svg" alt="verified by DOS" height="20">
</a>
```

The source SVG lives at [`docs/assets/verified-by-dos.svg`](assets/verified-by-dos.svg)
— copy it into your repo (adjust the `src` path to wherever you put it). It
renders inline on GitHub with no external host, exactly like the other
[hand-built assets](assets/README.md).

## Honesty note — what the static badge does and doesn't assert

Read this rung carefully, because the kernel's whole point is to not over-claim:

- **The static badge asserts adoption, not a verdict.** It says *"this repo wires
  `dos verify` into its gate."* It is **not** a live, per-commit pass/fail — it
  shows the same green whether your last `verify` answered `SHIPPED` or
  `NOT_SHIPPED`. Wearing it is a claim about your **process**, not a real-time
  truth syscall result.
- **The live, per-commit badge exists today in one place: on DOS's own repo.**
  The kernel repo's README wears a "verified by DOS" badge that is the status of
  [`.github/workflows/dos-gate.yml`](../.github/workflows/dos-gate.yml) — its own
  [`verify-action`](../verify-action/README.md) running `dos commit-audit` over
  every pushed commit plus `dos verify` of the badge's own ship-stamp, so the
  colour is a kernel verdict a CI runner computed from git ancestry, never a
  static pill ([`docs/112`](112_the-dynamic-verified-by-dos-badge.md) Phase 0).
  The *generalized* dynamic badge — the three-state shields endpoint
  (green/neutral/red) safe to aim at any repo — is docs/112 Phases 1–2, still
  future work, because a binary badge must never be pointed at a foreign repo
  (it would false-accuse the `via none` majority). Until then, for **your** repo
  the static badge is an honest *adoption* mark, and the **gate itself** (below)
  is what makes it true.

## Embed an answer card

The badge is a pill; an **answer card** is a pill plus a sentence and a link to
the canonical, sourced answer. Paste it into your README, your `CONTRIBUTING`,
or a docs page so a reader — or a model crawling the repo — lands on the page
that explains, with evidence, what gating agents on a witness actually buys:

```markdown
[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

> **The kernel is the part that doesn't believe the agents.** This repo gates
> its agents' ship-claims on git evidence, not their word —
> [how to verify an AI agent actually did the work](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md).
```

Rendered:

[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

> **The kernel is the part that doesn't believe the agents.** This repo gates
> its agents' ship-claims on git evidence, not their word —
> [how to verify an AI agent actually did the work](answers/how-to-verify-an-ai-agent-actually-did-the-work.md).

Swap the link for whichever [answer page](answers/README.md) fits your repo —
[stopping two agents colliding](answers/how-to-stop-two-ai-agents-overwriting-each-other.md),
[detecting a spinning loop](answers/how-to-detect-an-agent-loop-spinning-without-progress.md),
or the [category page](answers/do-ai-coding-agents-lie-about-what-they-shipped.md).
The same honesty rung as the badge applies: the card asserts your **process**
(you gate on a witness), not a live verdict — see the honesty note above.

## Where to earn it — three paths, by repo shape

The badge is only honest if the kernel actually gates your ships. Each path
below ends with a green check on a real PR; the badge comes after. The worked
example for everything here is this repo itself — the self-gate in
[`.github/workflows/dos-gate.yml`](../.github/workflows/dos-gate.yml) runs both
legs, and its workflow badge is the live form of this mark. (**New to DOS?**
Start at [`examples/playbooks/01_onboard-a-repo.md`](../examples/playbooks/01_onboard-a-repo.md)
— `pip install dos-kernel` → `dos verify` on a phase, in ~10 minutes.)

### Path 1 — any git repo, ~10 minutes (claim-vs-diff; no DOS vocabulary needed)

The floor, deliberately reachable by a repo that has never declared a plan or a
phase: `dos commit-audit` grades whether each commit's **subject** is witnessed
by its own **diff** — a `fix:` that touched only a README, an `--allow-empty
"shipped"` — and ABSTAINS on `wip:`/`merge:`/no-claim subjects, so a plain
Conventional-Commits repo earns the badge without adopting any ship grammar:

```yaml
# .github/workflows/dos-gate.yml
name: dos-gate
on: [pull_request]
jobs:
  claim-vs-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }       # the audit walks git ancestry
      - uses: anthony-chaudhary/dos-kernel/verify-action@master  # pin a release tag for reproducible CI
        with:
          mode: commit-audit
          fail-on: unwitnessed         # or: none = observe-only while you trial it
```

Then make `dos-gate` a **required check** in branch protection. DOS is a PDP —
it computes the verdict and sets the exit code; GitHub's required-check setting
is the PEP that actually blocks the merge
([`verify-action/README.md`](../verify-action/README.md)). Once the check has
refused — or had nothing to refuse — on a real PR, paste the badge.

### Path 2 — a repo with declared phases (the truth syscall in the gate)

If your repo stamps ships with a `(plan, phase)` grammar — or you want it to —
add the `verify` leg, so a PR that *claims* to close a phase is checked against
git ancestry, not the author's word. Prove the grammar locally first:

```bash
pip install dos-kernel
dos init .                            # scaffold dos.toml; declare [stamp] to match your ship grammar
dos doctor --workspace . --check      # exit 1 → the [stamp] grammar doesn't match this repo
dos verify --workspace . AUTH AUTH2   # SHIPPED / NOT_SHIPPED (via grep|registry|none)
```

…then add the leg beside the audit leg:

```yaml
      - uses: anthony-chaudhary/dos-kernel/verify-action@master
        with:
          mode: verify
          plan: AUTH                   # the (plan, phase) the PR claims to close
          phase: AUTH2
```

The raw-steps form (parse the claim out of the PR body, branch on the exit code
yourself) is Recipe 1 in
[`examples/playbooks/cookbook-ci-integration.md`](../examples/playbooks/cookbook-ci-integration.md);
the same verdict fires locally before the push via the bundled
[`.pre-commit-hooks.yaml`](../.pre-commit-hooks.yaml).

### Path 3 — upgrade to the live badge (the form this repo wears)

Once the `dos-gate` workflow exists, GitHub's native workflow badge is the
**live** form of the mark — its colour is the last gate run's verdict, computed
by a CI runner over git ancestry, never a static pill
([`docs/112`](112_the-dynamic-verified-by-dos-badge.md) §4, trust posture 2):

```markdown
[![verified by DOS](https://github.com/OWNER/REPO/actions/workflows/dos-gate.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/dos-gate.yml)
```

The honesty note above carries over: the workflow badge is **binary**
(passing/failing), which is honest aimed at your **own** repo — both gate legs
constitutionally abstain, so a red really is a caught over-claim or a broken
grammar, never "this repo doesn't use DOS" — and must never be aimed at a
*foreign* repo (the docs/112 kill-list; the three-state endpoint for that case
is docs/112 Phase 1, future work).

## Agent examples — earning the badge with (and against) your agents

The badge's whole subject is agents, so the most common earner *is* one — and
the wiring has to survive the obvious objection: **the loop that installs the
gate must not be the loop that certifies it works.** Four worked shapes, in
increasing fleet size:

### 1. Paste-a-prompt — a coding agent wires the gate end-to-end

Hand your coding agent (Claude Code, Cursor, Codex, Gemini CLI, …) this,
verbatim:

```text
Wire the "verified by DOS" gate into this repo and earn its badge:

1. Add .github/workflows/dos-gate.yml using
   anthony-chaudhary/dos-kernel/verify-action with mode: commit-audit,
   fail-on: unwitnessed, and a fetch-depth: 0 checkout.
2. pip install dos-kernel, then run `dos commit-audit HEAD~20..HEAD` and
   report the drift you find. Do NOT rewrite old commits to make it pass.
3. Open a PR. Do NOT claim the gate works until the dos-gate check is green
   on the PR itself — point me at the check run.
4. Only after the check is green: paste the badge markdown from the DOS
   repo's docs/BADGE.md into README.md, in the same PR.
```

Steps 3–4 are load-bearing: the order forces the agent to earn the green before
wearing it. Then close the loop the kernel's way — don't take the agent's "the
gate is green" on faith either:

```bash
gh pr checks <PR-NUMBER>             # the runner's verdict, not the agent's narration
dos commit-audit origin/main..HEAD   # audit the agent's own PR commits
```

The agent that wires the gate is the gate's first defendant: if its own
`ci: add dos-gate` commit over-claims, the gate it just installed catches it.

### 2. Hook-wired runtimes — the agent that can't claim "done" falsely

`dos init --hooks <runtime>` wires the verdicts into the agent runtime itself;
the Stop/AfterAgent hook runs `dos hook stop`, which refuses the stop while a
claimed effect is unverified:

```bash
pip install dos-kernel
dos init --hooks claude-code .       # or: cursor | codex | gemini | antigravity
```

Now "I added the gate and earned the badge" is a claim the runtime re-checks
against git before the agent is allowed to stop — the agent cannot
self-certify the badge work complete. Claude Code users get the same hooks plus
the MCP tools and the skill pack from the bundled plugin:

```text
/plugin marketplace add anthony-chaudhary/dos-kernel
/plugin install dos-kernel@dos
```

…where `/dos-goal-gate` turns an operator goal ("earn the badge") into
checkable effect claims the Stop hook holds the agent to.

### 3. MCP — the agent calls the referee before claiming

For an agent that should *verify*, not just *be verified*: expose the syscalls
as MCP tools (`pip install 'dos-kernel[mcp]'`) and the agent calls
`dos_verify` / `dos_commit_audit` natively before reporting:

```jsonc
// .mcp.json (Claude Code) or claude_desktop_config.json (Claude Desktop)
{ "mcpServers": { "dos": { "command": "dos-mcp" } } }
```

A standing instruction worth putting in your repo's agent docs
(`CLAUDE.md` / `AGENTS.md`):

```markdown
Before reporting any phase shipped, call dos_verify for it and quote the
verdict line ("SHIPPED … via grep"). A claim without a verdict is not a claim.
```

Full tool surface: [`src/dos_mcp/README.md`](../src/dos_mcp/README.md); the
wiring recipe is cookbook Recipe 5.

### 4. A fleet — the badge over mostly-agent-authored history

The badge means the most where most commits are agent-authored, and
`commit-audit` is deliberately **author-neutral** — a human's hollow `fix:` is
caught exactly like an agent's. Quantify the fleet's honesty before and after
the gate:

```bash
dos commit-audit --sweep --workspace . origin/main..HEAD
#   the DRIFT RATE (unwitnessed / checkable) + a by-claim-kind grid
```

Inside the fleet, guard the **fold sites** too: an orchestrator banking a
subagent's "I shipped it" should re-read it against a byte-author the worker
doesn't control — `dos verify-result` for harness-authored deaths
([playbook 07](../examples/playbooks/07_verify-subagent-results.md); ~32% of a
measured 2,305-subagent corpus returned one), and the `/dos-witness-claim`
pattern for effect claims. The badge then asserts the discipline end to end:
workers witnessed at the fold, commits at the PR — both by evidence the
claimant didn't author.

## The pre-paste checklist

The order matters: the gate is the substrate, the badge is the projection.
Before the badge goes in your README:

- [ ] the `dos-gate` check ran green on a **real PR** — not only on a push with
      no checkable claims in it;
- [ ] it is a **required check** in branch protection — the exit code is a
      verdict; only the required-check setting makes it a gate;
- [ ] if you declared a `[stamp]` grammar, `dos doctor --workspace . --check`
      exits 0 — a wrong grammar silently turns every `verify` into `via none`;
- [ ] the badge links somewhere a reader can audit the claim: the DOS repo (the
      static form) or your own gate workflow (the live form).

Don't wear the mark until the kernel is the thing that closes a phase in your
repo.

## The audited tier — the commit-claims endpoint badge (docs/312, issue #85)

The badge above is a **static adoption mark**: it says *this repo gates its
agents' ship-claims on `dos verify`*. The scoreboard's opt-in tier adds a
second, **measured** badge: a shields.io *endpoint* badge whose message is the
result of an actual `dos commit-audit --sweep` over your history — counts with
an as-of date, backed by a published per-repo
[`verdict.json`](scoreboard/methodology.md) carrying the receipts (the audited
range, the offending SHAs, the grader version).

```markdown
[![commit-claims](https://img.shields.io/endpoint?url=<served URL of your badge.json>)](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/scoreboard/methodology.md)
```

It renders as `commit-claims: audited clean (as of <date>)` when the sweep
found every checkable claim witnessed, or the honest counts when not — this
repo's own reads `3 unwitnessed of 134`, all three deliberate empty re-stamp
commits ([methodology §2](scoreboard/methodology.md)). The two badges answer
different questions: the static mark says *the gate is wired*; the audited
badge says *the history was swept, and here is what it read*. Registration
(opt-in, write-access required) is documented on the
[methodology page](scoreboard/methodology.md); the badge JSON is generated by
[`scripts/scoreboard_surfaces.py`](../scripts/scoreboard_surfaces.py), never
hand-written, and a repo is published only on its own request.
