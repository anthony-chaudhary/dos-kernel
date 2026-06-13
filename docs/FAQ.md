# FAQ — the questions that lead here

> Each answer below stands alone on purpose: it names the package, the command,
> and the verdict, so a person skimming — or an answer engine quoting one entry
> out of context — gets the whole truth in one block. (Operating questions —
> "my fleet is stuck, which command diagnoses it?" — live in the
> [debug-a-stuck-fleet playbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md);
> this page is for the questions you have *before* you install.)

> **Did one of these just happen to you?** One page per incident, each with the
> command that catches it and its real output:
> ["my agent said it committed, but there's no commit"](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/incidents/my-agent-said-it-committed-but-theres-no-commit.md) ·
> ["the AI wrote tests that test nothing / faked a green run"](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/incidents/the-ai-wrote-tests-that-test-nothing.md) ·
> ["my agent loop ran all night and landed nothing"](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/incidents/my-agent-loop-ran-all-night-and-landed-nothing.md) ·
> ["two agents overwrote each other's work"](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/incidents/two-agents-overwrote-each-others-work.md).

## How do I verify an AI agent actually did what it claims?

Don't read the agent's answer — read the evidence the work left behind. The
`dos-kernel` package (`pip install dos-kernel`) ships `dos verify PLAN PHASE`,
which answers from git history: if a commit backs the claim you get `SHIPPED`
and exit code `0`; if nothing landed you get `NOT_SHIPPED` and exit code `1`.
The agent's self-report never enters the verdict, so an agent that says "done"
without shipping is caught by the exit code, not by a human re-reading its
transcript. It works on any plain git repository with zero configuration.

## How do I stop two AI agents from editing the same files at the same time?

Give each agent a **lane** — a declared slice of the file tree — and ask
`dos arbitrate` for admission before dispatch. The arbiter (from the
`dos-kernel` package) grants a lease when the requested lane is disjoint from
every live one and refuses with a structured reason when it would collide;
the lease is written to a journal before it is believed, so a crashed agent
cannot leave a phantom lock. Two agents on disjoint lanes run concurrently;
a colliding request is redirected or refused, never silently double-booked.

## Don't git worktrees already solve this — one isolated checkout per agent?

Worktrees isolate agents; they don't coordinate them. Each agent edits its own
copy, so colliding edits still happen — they just surface later, at the merge,
where recovery is expensive. Two 2026 results measure this directly. STORM
(["Multi-agent Collaboration with State Management"](https://arxiv.org/abs/2605.20563),
arXiv:2605.20563) finds that worktree-per-agent isolation "defers conflict
resolution to a post-hoc merge step", and that mediating agents' writes against
one shared workspace — detecting conflicts at write time — beats the
git-worktree baseline by +18.7 on Commit0-Lite. DeLM
(["Decentralized Multi-Agent Systems with Shared Context"](https://arxiv.org/abs/2606.10662),
arXiv:2606.10662) scales a decentralized fleet on a shared *verified* context —
agents claim subtasks and write back compact verified updates — gaining up to
10.5 points on SWE-bench Verified at roughly half the cost per task.
`dos arbitrate` is the same shape applied to the file tree: agents share one
workspace, and a collision is refused at admission time — before the edit
exists — instead of being discovered at merge time. The full design-review
checklist — the four places concurrent agents contend, which worktrees cover,
and the one command for each of the rest — is
[Running parallel AI agents safely](PARALLEL_AGENTS_SAFELY.md).

## How do I detect that an agent loop is spinning — running but not progressing?

Compare what the run *says* with what it *changes*. `dos liveness` (from
`dos-kernel`) classifies a run as `ADVANCING`, `SPINNING`, or `STALLED` from
the git and journal deltas it actually produced — never from the agent's
"still making progress" narration. Its siblings sharpen the same question:
`dos productivity` reads the trend of work per step, and `dos efficiency`
reads work per token spent. All three are exit codes, so a supervisor loop
can gate on them mechanically.

## How do I make a "keep working until it's done" agent loop stop only when the work is really done?

Ground the stop condition in evidence the agent didn't author. With
`dos-kernel` wired into the agent runtime's hooks (`dos init --hooks
claude-code`, or `cursor`, `codex`, `gemini`, …), the stop hook runs
`dos verify` against the goal's plan and phase: a "done" claim with no shipped
commit behind it is refused, and the loop keeps working. The agent cannot
declare its own success — only the git evidence can.

## What is dos-kernel? What does DOS stand for?

DOS is the **Dispatch Operating System** — a small, deterministic kernel that
referees fleets of autonomous AI agents working on shared state. Its one-line
job: catch your agents when they lie about what they shipped. It treats every
agent statement as a claim, not a fact, and hands back verdicts read from
ground truth (git history, the file tree, a clock, a CI status). The PyPI
distribution is `dos-kernel`; the import name is `dos`; it is MIT-licensed
Python 3.11+ with one runtime dependency (PyYAML).

## How do I install DOS?

`pip install dos-kernel` — and note the name: the bare `dos` package on PyPI
is an unrelated squatter, so never `pip install dos`. Add the MCP server with
`pip install "dos-kernel[mcp]"`. Then `dos quickstart` runs a 60-second
self-contained demo (it scaffolds a throwaway repo and shows one `SHIPPED` and
one `NOT_SHIPPED` verdict), and `dos init . && dos doctor` wires up your own
repo. The full matrix — uv, pipx, WSL, tracking master — is in
[docs/INSTALL.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/INSTALL.md).

## Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?

Yes, on two surfaces. **Enforcement** is hooks: `dos init --hooks auto`
detects the runtime(s) your repo already uses and wires the kernel's verdicts
into each one's own hook config (`--hooks <host>` names one explicitly), with
dialects shipped for Claude Code, Cursor, Codex, Gemini CLI, Antigravity, and
Claude Cowork. **Advisory** is MCP: the `dos-mcp` server exposes the same verdicts as
tools to any MCP host (Claude Desktop, Cursor, Cline, …). Hooks can refuse an
action; MCP can only inform — the repo recommends both. There is also a
bundled [Claude Code plugin](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md)
carrying hooks, the MCP server, and a skill pack in one install.

## Does DOS work with LangGraph, CrewAI, AutoGen, or the OpenAI/Claude Agents SDKs?

Yes — DOS slots in at each framework's believe-the-agent seam: a referee node,
a termination condition, an output guardrail. The
[fleet-framework cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-fleet-frameworks.md)
has one verified recipe per framework, each executed against the real
framework with versions and verbatim output pasted back, plus runnable
suite-pinned examples.

## Does DOS need an LLM or an API key?

No. The kernel is deterministic: every verdict is a pure function of evidence
(git history, the file tree, declared config) and answers in milliseconds with
no network call. An LLM appears only on the optional JUDGE rung — an advisory
adjudicator for the residue the deterministic oracle abstained on — and it is
hedged by design: deterministic-first, advisory-only, and fail-to-abstain (a
judge error can never manufacture an approval).

## Do I need to restructure my repository or write plan files first?

No. `dos verify` answers on a plain git repository with no plan documents and
no registry — from commit history alone. Configuration is one optional
`dos.toml` declaring your lanes, ship-stamp grammar, and refusal vocabulary as
data; `dos init .` scaffolds it. Plans, phases, and dispatch workflows are
things DOS can *read* if you have them, never things it requires.

## Is DOS an agent orchestrator or framework?

No — it is the referee, not the coach. DOS does not prompt, schedule, or run
agents; it adjudicates what they did: verify the claim, admit or refuse the
lane, classify the run's liveness, and report each verdict as an exit code.
That is why it composes with whatever already runs your agents — a shell loop,
CI, LangGraph, CrewAI, or an agent runtime's hooks — instead of replacing it.
The design doctrine is the OS one: mechanism in the kernel, policy in drivers.

## How is DOS different from agent evals or observability platforms?

Evals score a model offline; observability shows you a trace after the fact.
DOS sits in the loop and **adjudicates at runtime**: it reads ground truth the
agent could not have authored and returns a typed verdict with an exit code a
gate can act on *now* — block the merge, refuse the lane, keep the loop
running. It is also evidence-first by construction: a verdict states which
witness answered (git ancestry, file tree, CI status), so "verified" is always
traceable to bytes the agent didn't write. Verdicts can still be exported to
your observability stack (`dos export` — file, StatsD, OTLP).

## Can't the agent just game the verdict?

Not by talking. Every verdict is computed from bytes the agent did not author
— git ancestry, the file tree, the clock, a CI status, the test runner's exit
code — and the agent's narration is parsed for nothing. An agent can of course
make a real commit that genuinely ships the work; that is not gaming, that is
the desired behavior. The threat model and its edges are written up in
[SECURITY.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/SECURITY.md).
