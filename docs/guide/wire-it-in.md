# Wire it into your stack

> ← Part of the [DOS README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md). The adopter's journey: how deep your config goes, which surface you call the referee through (MCP, hooks, exit-code, Python, a fleet framework), and the full install matrix.

## How far you take it

It works on a plain `git init` with zero config, and gets smarter the more you
tell it. You don't adopt a framework and pick a tier; you start at the shallow
end and it keeps paying off as you wade deeper — the same kernel the whole way:

- **Zero config.** Point `dos verify PLAN PHASE` at a plain git
  repo — no plan, no registry, no `dos.toml`. It answers from commit history
  alone (`via grep-subject` / `via none`). This is the whole of
  [QUICKSTART](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md) and the day-one CI win.
- **Tell it your structure.** `dos init` writes a `dos.toml` (lanes, paths,
  ship grammar as data); add a plan doc and `dos plan` lays each phase's
  *claim* beside the oracle's verdict. Here's [exactly what a plan file looks
  like](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/plans/example-plan.md) (copyable, round-trips with the built-in
  reader), and four worked [example workspaces](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/workspaces).
- **Teach it your own types.** Declare your own block reasons, gate
  verdicts, output renderers, admission predicates, a model-backed judge, a
  custom plan dialect, or a whole host driver — all as workspace policy,
  never a fork. The map is **[docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)** (seven extension
  axes) + the copy-me **[`examples/dos_ext/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/dos_ext)**.

## How you plug it in

That slope is how deep your config goes. The other axis is how you call the
referee at all — and you adopt through whichever surface matches how you
already work, not by restructuring your stack. The same kernel verdicts are
reachable through every row here, lowest-friction first:

| Surface | Adopt it when… | The move |
|---|---|---|
| **MCP server** | you drive an agent through an MCP host (Claude Desktop, Cursor, Cline, an Agent-SDK app) | add one line to the host config (`{ "command": "dos-mcp" }`) and ask the agent to `dos_verify` its own last claim — **zero code**. The *advisory* path (the agent asks). See [Give your agent a lie detector](#give-your-agent-a-lie-detector-mcp). |
| **Runtime hooks** | you run an agent loop (Claude Code, Cursor, Codex CLI, Gemini CLI) and want the verdict to *act*, not just be available | `dos init --hooks <runtime>` wires the verdict into that host's own hook config — a refused call is **denied before it runs**, a false "done" is **refused**. The *enforcement* path (the host denies). One command, no hand-edited YAML. See [QUICKSTART](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md) + [docs/221](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/221_the-cross-vendor-hook-installer.md). |
| **CLI exit-code** | you have *any* command-running environment — a CI step, a `pre-push` hook, or an agentic CLI like **aider** whose lint/test-cmd trusts a "done" | branch on a `dos` verb's exit code (`dos verify`: `0` shipped / `1` not; `dos commit-audit`: `0` clean / `1` over-claim) — **the verdict *is* the exit code**, no hook adapter and no MCP client. The honest tier for hook-less hosts (Windsurf, Warp, Zed). The [exit-code tier cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-exit-code-tier.md). |
| **Python API** | your dispatcher/orchestrator is already Python | `import dos` and call the pure syscalls (`dos.oracle.is_shipped`, `dos.arbiter.arbitrate`, …) — state-in / verdict-out, no subprocess. The [Python cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-python-api.md). |
| **Fleet framework** | your fleet already runs on LangGraph, CrewAI, AutoGen, or the OpenAI/Claude Agents SDK | bolt the referee onto the framework's own seam — a referee node, a termination condition only git can satisfy, an output guardrail with a git tripwire. One function, no rewrite; every seam executed against the real framework. The [fleet-framework cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-fleet-frameworks.md). |
| **Swarm runtime** | your agents run on **Hermes, OpenClaw**, or a SwarmClaw-style autonomous swarm — privileged tools, shared memory docs / task boards, and **no lock manager** for either | drop a two-function adapter into the tool-execution loop: `guard_action` refuses an arbitrary-exec command **before it runs**, and `acquire_lease` / `release_lease` bracket each shared-state write so the lost update never lands. No `import dos` — it shells the CLI; Hermes' `pre_tool_call` hook also speaks DOS natively (`dos hook pretool --dialect hermes`). The runnable, A/B-measured [Hermes / OpenClaw worked example](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/hermes_integration) + [docs/278](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/278_integrating-dos-with-hermes-and-openclaw-the-missing-lock-manager-for-agent-swarms.md). |
| **Skill pack** | you run agents in Claude Code and want the workflow, not just the verdict | `dos init --skills` drops editable `SKILL.md` screenplays that wire the syscalls into a snapshot → audit → gate → take-a-lane loop. See [QUICKSTART §2](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md). |
| **Driver** | your lanes must be *computed*, or you add a provider-backed judge | write one `dos/drivers/<host>.py` (a `LaneTaxonomy` + a config factory), loaded by name, never imported by the kernel. The map is [HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md). |

The two axes are independent: a zero-config repo can adopt through any surface,
and a deeply-configured one still answers over the same CLI and MCP tools.
Start at the top row — it's the one that costs nothing to try. The first two
rows also compose: MCP advises (the agent checks its own work), hooks enforce
(the host stops a bad action) — wire both for the full loop.

Those surfaces are the upstream half of the value chain — who calls the
referee. The same verdicts also flow downstream, to the systems that act on
them: every adjudication lands in a verdict journal that `dos export` drains to
your observability stack (Datadog / Honeycomb / Grafana —
[docs/266](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/266_the-verdict-exporter-shipping-the-journal-to-where-dashboards-live.md)),
`dos notify` pushes what-needs-a-human to Slack, `dos reward` gates what a
fine-tune may train on, and `dos attest` mints a signed receipt a skeptic can
check without loop access
([docs/246](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/246_dos-attest-the-portable-signed-receipt.md)). One kernel, one
verdict vocabulary, from the agent's tool call to your dashboard.

## Give your agent a lie detector (MCP)

The easiest way in doesn't involve writing any Python. Point the agent host you
already use at the bundled MCP server, then ask your agent to `dos_verify` its
own last claim. The first time it comes back `NOT_SHIPPED … (via none)` on work
the agent *swore* it finished, you'll see why this repo exists — in your
terminal, on your fleet.

Installed with the `[mcp]` extra (`pip install -e ".[mcp]"` from your clone — see
[Install](#install)), DOS exposes the syscalls as MCP tools — the truth tools
first (`dos_verify` "did it ship?", `dos_commit_audit` "does this commit's claim
match its diff?", `dos_status` one folded fact about a run), then
`dos_arbitrate` (may two workers run without colliding?), the structured-refusal
pair (`dos_refuse_reasons` / `dos_check_reason`), `dos_recall` (is this recalled
memory still true?), `dos_citation_resolve` (does this cited legal case exist in
a third-party reporter? — the *Mata v. Avianca* witness), and `dos_doctor` (the
workspace report) — so any
MCP-speaking host — Claude Desktop, Claude Cowork, Cursor, Cline, Trae, an
Agent-SDK app — can call the referee over JSON-on-stdio with zero Python coupling. Each verdict comes
back with a one-line interpretation of what it means for the agent's next move.
(See **[the MCP server surface](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md)**.)

```jsonc
// claude_desktop_config.json — paste, restart, then say:
//   "use dos_verify to confirm you actually shipped that"
{ "mcpServers": { "dos": { "command": "dos-mcp" } } }
```

The MCP server is **advisory**: the agent *calls* the referee when it (or you)
thinks to. The per-host wiring for Cursor / Codex / Gemini is in
**[the MCP README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos_mcp/README.md)** — all four are MCP clients, so this
works on every one of them with zero code.

**Building a legal agent?** `dos_citation_resolve` is the same lie-detector
aimed at the failure class that gets lawyers sanctioned: a fabricated case
citation (the *Mata v. Avianca* class). Wired in as an MCP tool, it checks each
cite the agent emits *before* the brief is written — a pre-effect gate, not a
post-hoc document scan — by resolving it against a third-party reporter the
model didn't author (CourtListener), and abstains rather than guess when it has
no corpus access. It witnesses existence and quote-fidelity, never whether the
case supports your argument. The sourced walkthroughs:
[catch fabricated citations inside your agent](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/catch-fabricated-legal-citations-in-my-ai-agent.md),
[avoid an AI-citation sanction](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/largest-ai-hallucination-sanction-how-to-avoid.md),
and [the ABA Opinion 512 duty](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/aba-512-verify-ai-citations-duty.md).

**Gemini CLI users get a one-liner.** DOS ships a `gemini-extension.json`
manifest at the repo root, so the whole referee — the MCP server plus a context
file that tells the model to gate its own done-claims — installs as one Gemini
extension, no clone and no config edit:

```bash
pip install 'dos-kernel[mcp]'   # the server the extension launches
gemini extensions install https://github.com/anthony-chaudhary/dos-kernel
```

The same manifest is what Google's auto-indexed
[extensions gallery](https://geminicli.com/extensions/) crawls, so the listing
is automatic.

**Browsing an MCP registry?** A repo-root `smithery.yaml` lists DOS on
[Smithery](https://smithery.ai), the de-facto MCP-server registry. It declares a
**local stdio** launch (`uvx --from 'dos-kernel[mcp]' dos-mcp`) on purpose: DOS
adjudicates *your* git repo, so it runs next to it rather than in a hosted
sandbox that couldn't see your history. No API key — DOS is deterministic.

### …then make the verdict *act* (hooks)

To go from "the agent can ask" to "the host won't let a bad call through", wire
DOS's hooks into the runtime you actually run. One command per host — it writes
that host's own hook-config file, merged into anything already there:

```bash
dos init --hooks auto .          # don't know the names below? this detects the
                                 # runtime(s) this repo already uses and wires them all
dos init --hooks claude-code .   # .claude/settings.json
dos init --hooks cursor .        # .cursor/hooks.json
dos init --hooks codex .         # .codex/config.toml
dos init --hooks gemini .        # .gemini/settings.json
dos init --hooks antigravity .   # .agents/hooks.json
dos init --hooks claude-cowork . # the SAME .claude/settings.json Claude Code reads
```

The list above is illustrative, not authoritative — the live matrix is a verb:
`dos hosts` prints every host DOS can wire, sourced from the registries themselves
(`dos hosts --json` for tooling), so the table never rots out of sync with what
`dos init --hooks` actually installs. Each row carries the host's tier, the events
it binds, its dialect, its config path, the exact wiring command, and the host's
own caveat (Codex's partial tool coverage, Cowork's not-yet-firing hooks). A host
with **no** row is itself the signal: it has no hook seam, so its DOS surface is
the advisory one (MCP + skills).

That binds three shipped hooks: `pretool` denies a structurally-refused call
before it runs, `stop` refuses a stop on an unverified "done," `posttool`
re-surfaces a stalled stream. This is the **enforcement** path (the *host*
denies on a DOS verdict) — the complement to MCP's advisory path. Until
recently this spoke only Claude Code; it now installs across six hosts —
Claude Code, Cursor, Codex, Gemini, Antigravity, and Claude Cowork
([docs/221](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/221_the-cross-vendor-hook-installer.md),
[docs/269](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/269_antigravity-the-fifth-host.md),
[docs/298](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/298_claude-cowork-the-sixth-host-shared-surface.md)).
`--with-hooks` is the back-compat alias for `--hooks claude-code`. `auto`
([docs/303](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/303_hooks-auto-detection-plan.md))
names the host *for* you: it probes which of those config dirs already exist in
the repo — plus the shell's own environment, so a fresh repo opened inside
Claude Code still detects — wires every runtime it finds (a shared config file
is wired once), and fails loud with the list above when nothing is detectable,
never guessing. Claude
Cowork is the *shared-surface* host: it runs the same agent harness as Claude
Code, so wiring either name binds both — one file, one set of hooks. (One
honest caveat, carried on the install note itself: the Cowork app doesn't
*fire* hooks yet — anthropics/claude-code#63360 — so until that closes,
Cowork's working DOS surface is the advisory one above.)

Under the installer sits a pluggable **dialect seam**: the verdict is decided
once, then rendered into whatever JSON shape each host parses — so a host the
installer doesn't cover yet (a seventh dialect speaks **Hermes**) is a driver,
never a kernel edit. Its honest flip side: a host with *no* hook seam gets *no*
dialect. ByteDance's **Trae** ships no user-scriptable hook system, so DOS binds
it **advisory-only** (MCP + a verify-before-"done" rule + the skills) and
`dos init --hooks trae` fails loud rather than writing an invented envelope Trae
would never read — fake enforcement is the exact failure the seam prevents.
([docs/217](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/217_the-cross-vendor-hook-dialect-seam.md) ·
[docs/294](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/294_trae-advisory-only-the-host-with-no-hook-seam.md))

Because these hooks run on *every* tool call, the hot-path predicates are
reimplemented in a native Go `dos-hook` binary — byte-identical to the Python
kernel on the gated decision (the docs/124 parity contract) but ~16–43× faster
(~10 ms vs. interpreter cold-start), bundled in the per-platform wheels with a
clean fall-back to the always-available pure-Python path on any platform without
it ([docs/124](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/124_the-go-core-build-plan-and-the-parity-contract.md),
[docs/125](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/125_go-hook-fastpath-build-plan.md),
[docs/270](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/270_go-hook-fastpath-benchmarks.md),
[docs/286](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/286_shipping-the-go-binary-through-pypi-per-platform-wheels.md)).

### …and when your host has neither (the exit-code tier)

MCP is the **advisory** tier (the agent *asks*); hooks are the **enforcement**
tier (the host *blocks*). Both ask DOS to speak the host's language — an MCP
client, or a per-host hook dialect. The third tier asks nothing: **any
environment that runs a command** reads a `dos` verb's exit code, because every
verb already follows the universal Unix convention (`0` = ok, non-zero = a
verdict). The verdict *is* the exit code.

That tier is what reaches the long tail no dialect will ever cover, and it is
the **honest** surface for a hook-less host (Windsurf, Warp, Zed today): rather
than invent a fake enforcement envelope a host would never read, DOS rides the
one contract every runner already has.

```bash
# aider runs --test-cmd after every edit and feeds a non-zero exit back to the
# model's auto-fix loop — so a kernel verdict drops into a top-tier CLI agent
# with NO hook machinery and NO MCP client, in one flag:
aider --test-cmd 'dos commit-audit --workspace . HEAD' --auto-test
```

The same one line is a `pre-push` gate, a Jenkins/Buildkite stage, a `Makefile`
target, or a `package.json` script. The full set of runnable recipes — aider, a
git `pre-push` hook, and a generic command step, plus `dos hook-exit` for
wrapping a *non-DOS* script onto the intervention ladder — is
**[the exit-code tier cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-exit-code-tier.md)**.

## Install

Requires **Python 3.11+**. Pick the row that matches how you work — the full
matrix (every OS, every channel, upgrade/uninstall, WSL, troubleshooting) is in
**[docs/INSTALL.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/INSTALL.md)**:

```bash
# pip — the default (the line the 60-second demo ran; also how a host pins it):
pip install dos-kernel            # core kernel (PyYAML only)
pip install "dos-kernel[mcp]"     # + the MCP server (dos-mcp)

# uv — the isolated CLI install (keeps `dos` + `dos-mcp` off your project venv):
uv tool install dos-kernel        # `dos` + `dos-mcp` on PATH
uvx --from dos-kernel dos doctor  # or run it once, ephemerally

# from a clone — editable, the contributor path (tracking unreleased master:
# pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel", no clone needed):
git clone https://github.com/anthony-chaudhary/dos-kernel.git && cd dos-kernel
pip install -e .                  # editable: your edits are live in the install
./install.sh                      # or .\install.ps1 on Windows — venv + install + PATH, one line
```

> **The distribution name is `dos-kernel`, not `dos`** — a bare `pip install dos`
> pulls an unrelated package that squats the name. The *import* name and the CLI
> are still `dos`. The core kernel's only runtime dependency is PyYAML (the
> `[mcp]` extra adds the MCP framework; `[tui]` adds the live `dos top` screens).
> See [SECURITY.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/SECURITY.md), "Supply chain."

`pip install dos-kernel` is the whole install — if it worked in the demo,
you're done here. The other rows exist for how *your* team works: **uv** if you
want the CLI isolated from your project venv (faster than `pipx`, manages
Python versions; `pipx install dos-kernel` works the same way), the clone if
you're contributing. Homebrew / WinGet / Scoop one-liners are next on the
release runway (see [docs/INSTALL.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/INSTALL.md)).

A host repo adds DOS as a pinned dependency and points it at its own tree — never
by vendoring the code in. DOS is stateless about which repo it serves: it
resolves the workspace from `--workspace` › `$DISPATCH_WORKSPACE` › cwd, never
its own install location, so the ground truth stays legible as the codebase
grows. (The full separation contract — mechanism in the package, policy in the
workspace's `dos.toml` — is in **[CLAUDE.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/CLAUDE.md)**.)

For most repos that one `dos.toml` is the whole policy surface. When your lanes
must be *computed* rather than listed, or you add a provider-backed JUDGE, you
write a small **driver** instead — loaded by name, never imported by the kernel;
the driver/plugin map and a copy-me template are in
**[docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)**.

### Claude Code plugin — hooks + MCP + skills in one install

If you drive a fleet with Claude Code, the lowest-friction way to bind the
verdict to the runtime is the bundled plugin under
[`claude-plugin/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/claude-plugin) — it packages all three runtime surfaces
at once: the **hooks** (`PreToolUse` deny · `PostToolUse` sensor · `Stop` verify,
all fail-safe), the **MCP server** (`dos_verify` / `dos_arbitrate` / … as tools
the model calls), and the **generic skill pack** (the domain-free dispatch
screenplays, namespaced `/dos-kernel:dos-next-up`, …):

```bash
# 1. The plugin ships JSON + markdown; the brains ship as the pip package, so
#    install it FIRST into the interpreter Claude Code runs (the [mcp] extra is
#    what the bundled MCP server needs):
pip install "dos-kernel[mcp]"

# 2. Then, inside Claude Code:
/plugin marketplace add anthony-chaudhary/dos-kernel
/plugin install dos-kernel@dos
```

After installing, run **`/dos-kernel:dos-setup`** once — it confirms the package
is importable, reports what the plugin wired, and points at the next skill. The
same three hooks are available à la carte via `dos init --hooks auto` (detects
your runtime; or name one — claude-code, cursor, codex, gemini, antigravity,
claude-cowork); the plugin is just the pre-packaged Claude Code form. The bundle's design + the build that keeps its skills in lockstep
with the source are in **[claude-plugin/README.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md)**.

---

*Next: [the full syscall + CLI reference](./cli-reference.md) · [operating a fleet day-to-day](./operating-a-fleet.md) · [extend it without forking](./extending.md).*
