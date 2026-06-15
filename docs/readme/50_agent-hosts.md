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

Under the installer sits a pluggable dialect seam: the verdict is decided
once, then rendered into whatever JSON shape the host parses
([docs/217](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/217_the-cross-vendor-hook-dialect-seam.md)) — so a runtime the
installer doesn't cover yet can still consume the same hooks. A sixth shipped
dialect speaks **Hermes**: `dos hook pretool --dialect hermes` emits the
`{"decision": "block", "reason": …}` object Hermes' `pre_tool_call` shell hook
reads (wire it in `cli-config.yaml`). A new host's dialect is a driver, never a
kernel edit.

The flip side of that honesty: a host with **no** hook seam gets **no** dialect.
ByteDance's **Trae** was proved out and ships no user-scriptable hook system in
its personal/international editions (no lifecycle events, no deny/allow stdout
contract; its CN-enterprise edition announced one on 2026-06-09 with no
published grammar yet), so DOS binds to it advisory-only — the MCP server in
`.trae/mcp.json` (read alike by IDE-mode Agent, SOLO mode, and TRAE CLI), a
verify-before-"done" rule in `.trae/rules/project_rules.md`, the generic
skills in `.trae/skills/` — and `dos init --hooks trae` fails loud rather than
writing config Trae would never read
([docs/294](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/294_trae-advisory-only-the-host-with-no-hook-seam.md)).
An invented envelope would be fake enforcement, which is the exact failure the
dialect seam exists to prevent.

Because these hooks run on *every* tool call, the core kernel logic on the hot
path is reimplemented in native Go — a `dos-hook` binary that ports the actual
decision predicates (the conjunctive-only lease-admission and
prefix-disjointness floor, the `verify()` grep rung, self-modify, the marker
budget, the WAL) rather than just shelling out to Python. It serves the
per-call verdict in ~10 ms — 16–43× faster than shelling
`python -m dos.cli hook …` (~0.25–0.8 s, dominated by interpreter cold-start) —
and is byte-identical to the Python kernel on the gated decision (the docs/124
parity contract, pinned by Go parity tests). It owns the common fast path and
falls back to the always-available Python verb for anything it doesn't yet
serve, so a machine without the binary degrades cleanly with no wiring change
([docs/125](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/125_go-hook-fastpath-build-plan.md),
[docs/270](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/270_go-hook-fastpath-benchmarks.md)). You don't build it
yourself: the per-platform wheels bundle the binary, so a wheel install gets
the native fast path with no Go toolchain — and any platform without a bundled
binary (including a plain source install) just runs the pure-Python path
([docs/286](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/286_shipping-the-go-binary-through-pypi-per-platform-wheels.md)).

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

*Next level up — what to watch once a fleet runs through these hooks: [Operating a fleet](#operating-a-fleet).*
