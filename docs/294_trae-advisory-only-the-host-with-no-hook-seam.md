# 294 — Trae, proved out: advisory-only support for the host with no hook seam

> **The honest verdict on a sixth host is sometimes "there is nothing to render."**
> ByteDance's **Trae** (the AI IDE at trae.ai, its SOLO mode, and TRAE CLI) was
> proved out for full cross-vendor binding the way Antigravity was (docs/269). The
> facts came back different: **Trae has no lifecycle hook system at all** — no hook
> events, no stdout JSON contract, no exit-code semantics. So Trae gets the two
> surfaces it actually exposes (the kernel syscalls + MCP, plus its rules/skills
> files for stickiness), and **deliberately does NOT get a `TraeDialect` or a
> `trae_install_spec`**. A dialect for a grammar that does not exist is not
> support — it is a silent fail-open manufactured at install time.

*Status: SHIPPED. As of 2026-06-10.*

## 0. What was asked, and what the answer turned out to be

"Prove out and add end-to-end support for Trae." DOS binds to an agent runtime on
three surfaces (docs/217 §0, restated in docs/269 §0):

1. **Kernel syscalls** — vendor-agnostic by construction. Trae gets these for
   free, like every host: `dos verify` / `dos arbitrate` / `dos commit-audit`
   adjudicate git + the lane journal and never ask who is acting.
2. **The MCP server** (`dos_mcp`) — the *advisory* path, the agent CALLS the
   referee. Trae is a real MCP client (stdio / SSE / Streamable HTTP, project
   config at `.trae/mcp.json`), so this surface is live today. §3 has the wiring.
3. **Hooks** — the *enforcement* path, the runtime DENIES a call on a DOS verdict.
   **Trae does not have this surface.** Not "DOS doesn't speak it yet" — the host
   has no user-scriptable hook layer for a verdict to be rendered into.

So "end-to-end support for Trae" honestly means: surfaces 1 and 2 wired and
documented end-to-end, the rules/skills files used to make the advisory
discipline sticky inside Trae's own config surface, and the absence of surface 3
recorded as a **typed, pinned decision** (§2) rather than papered over with a
renderer that emits bytes nothing parses.

## 1. The facts (web-grounded 2026-06-10)

Checked across the official docs (docs.trae.ai and the more exhaustive TRAE CN
documentation index on volcengine.com), the changelog through May 2026, the
`bytedance/trae-agent` open-source repo, and the Trae-AI/TRAE issue tracker.
Like every vendor's surface these churn per release — pin and re-verify.

| Facet | Trae |
|---|---|
| Lifecycle hooks (PreToolUse/Stop analogues) | **None in the personal/international editions.** No Hooks page anywhere in the doc index; `docs.trae.ai/ide/hooks` does not exist; no hooks entry in any international 2025–2026 changelog release. The TRAE CLI doc set enumerates slash commands / custom agents / MCP / skills / memory / tool permissions / permission modes / ACP — a taxonomy that otherwise mirrors Claude Code's, with hooks conspicuously absent. **One edition is the exception**: the TRAE Enterprise (CN) changelog dated 2026-06-09 announces, for IDE Enterprise v3.3.65, "Hooks — a lifecycle hook mechanism … interception, validation, and extension of agent behavior" — enterprise-CN-only, with **no detail page published** (no config path, no event names, no output grammar, no exit codes). See §4: the trigger has fired; the grammar has not. |
| Modes (where the §3 wiring applies) | The IDE's two top-level modes (**IDE mode** with its Agent, **SOLO mode**) and **TRAE CLI** all load the SAME project surfaces — `.trae/mcp.json`, `.trae/rules/`, `.trae/skills/` (CLI compatibility is documented explicitly). SOLO's `/plan` & `/spec` sub-modes add confirmation pauses, not gates. **TRAE Work** — the standalone enterprise workspace (web/desktop/mobile, launched 2026-06-09, built on SOLO) with its **Work / Code modes** — is the exception: its web/cloud clients take MCP from the enterprise admin console, not the project file, and rules/skills loading there is undocumented. |
| Hook output grammar / exit codes | **N/A** — no hook executes user code, so there is no deny/allow JSON shape and no exit-code contract to target. |
| Closest built-in gates | UI-configured only: the Agent "Auto-Run & security" command denylist (deny → manual approval in the IDE), TRAE CLI tool permissions / permission modes, enterprise command-blacklist + MCP-whitelist policies. None runs user code; none reads JSON. |
| MCP | **Yes** (since v1.3.0, ~April 2025). Project config: **`.trae/mcp.json`**, standard `mcpServers` object map (`command`/`args`/`env`); stdio, SSE, and Streamable HTTP; `${workspaceFolder}` substitution. |
| Rules | `.trae/rules/project_rules.md` (project; Markdown, loaded at agent init) + a UI-managed global `user_rules.md`. |
| Skills | `.trae/skills/<name>/SKILL.md` — the same SKILL.md convention the DOS generic pack (docs/74) ships. |
| Demand signal | Trae-AI/TRAE issue #2169 *requests* deterministic agent boundary enforcement as missing; #2258 reports the sandbox bypasses even git's own pre-push hooks. Users are asking for the seam, not configuring it. |

Sources: docs.trae.ai (`/ide/agent`, `/ide/rules`, `/ide/skills`,
`/ide/model-context-protocol`, `/ide/add-mcp-servers`, `/ide/changelog`), the
TRAE CN doc index (volcengine.com/docs/86677), github.com/Trae-AI/TRAE issues
#2169/#2258, github.com/bytedance/trae-agent.

## 2. The decision: no dialect, no install spec — and why that is the support

The dialect seam's own discipline decides this. `resolve_dialect` fails LOUD on
an unknown name precisely because *a wrong dialect against a real host is a
silent no-op* (docs/217) — the host finds no refusal in bytes it doesn't parse
and proceeds. A `TraeDialect` would be that bug built on purpose: there is no
documented shape to render, so any envelope we invented would be ignored by
construction. Worse, a `trae_install_spec` would let `dos init --hooks trae`
write a `.trae/hooks.json` that Trae never reads — handing the operator **fake
enforcement**: config that looks wired, denies nothing, and quietly converts a
SELF_MODIFY DENY into a proceed. The kernel exists to catch exactly this class
of lie; it must not mint one.

This is the docs/278 precedent applied again: OpenClaw (in-process TS hook, not
a stdout surface) and SwarmClaw (no pre-tool hook documented) were deliberately
NOT given dialects. Hermes got one only because its shell hook is a genuine
emit-JSON-on-stdout surface. Trae is the OpenClaw/SwarmClaw case, one step
further out: not a different seam shape, but no seam.

What ships instead:

- **Fail-LOUD stays correct and is now pinned by name.** `dos init --hooks trae`
  raises `unknown hook host 'trae'` and `resolve_dialect("trae")` raises — that
  *is* the correct behavior, and `tests/test_hook_dialect.py` +
  `tests/test_init_hooks_crossvendor.py` now pin `trae` specifically as a
  DELIBERATE absence, with this note as the rationale. A future implementer who
  adds a Trae dialect must consciously delete the pin — i.e. must have re-run §1
  and found the facts changed.
- **No speculative renderer.** When ByteDance ships hooks, Trae becomes one data
  row + two entry-point lines in a driver, exactly like Antigravity (docs/269 §4)
  — an afternoon, not a refactor. Inventing the row early buys nothing and risks
  shipping a wrong grammar.

## 3. The support that IS end-to-end today (advisory, in Trae's own config)

### 3a. MCP — the referee as a tool Trae's agent can call

`.trae/mcp.json` at the project root (Trae auto-loads it; also addable via the
IDE's MCP UI):

```json
{
  "mcpServers": {
    "dos": {
      "command": "dos-mcp",
      "env": { "DISPATCH_WORKSPACE": "${workspaceFolder}" }
    }
  }
}
```

`dos-mcp` is stdio (the transport Trae's `command` form speaks). With this
mounted, Trae's agent can call `dos_verify` ("did that actually ship?"),
`dos_commit_audit`, `dos_arbitrate`, `dos_refuse_reasons`/`dos_check_reason`,
`dos_status`, `dos_doctor` — the same advisory surface every other MCP host
gets. The one file covers IDE-mode Agent, SOLO mode, and TRAE CLI alike (the
CLI's compatibility with project-level `.trae/mcp.json` is documented); the
standalone TRAE Work workspace's web/cloud clients are the exception — they
take MCP from the enterprise admin console instead (§1 modes row). The
per-host snippet also lives in `src/dos_mcp/README.md` alongside
Cursor/Gemini/Antigravity.

### 3b. Rules — make the discipline sticky

Trae loads `.trae/rules/project_rules.md` at agent init. The advisory analogue
of verify-on-stop (docs/134) is a rule the agent reads every session:

```markdown
## Ground truth before "done"
Before claiming a task/phase is complete, call the `dos_verify` MCP tool
(or run `dos verify <plan> <phase>`) and report its verdict verbatim.
A claim the oracle answers NOT_SHIPPED is not done — say so and continue.
After committing, run `dos commit-audit HEAD` and surface any drift.
```

This is weaker than a hook (the agent can ignore prose; it cannot ignore a
denied tool call) — that gap is exactly what §2 records, and the honest line in
every doc that mentions Trae: **advisory-only until Trae ships a hook seam.**

### 3c. Skills — the generic pack runs in Trae too

Trae's skills convention is the same SKILL.md shape the DOS skill pack ships as
package data (docs/74), and the pack's screenplays shell only `dos` verbs — no
host names, pinned by `tests/test_skill_pack_*.py`. Copy what you need:

```bash
python -c "import dos, pathlib, shutil; src = pathlib.Path(dos.__file__).parent / 'skills'; [shutil.copytree(src / n, pathlib.Path('.trae/skills') / n, dirs_exist_ok=True) for n in ('dos-next-up', 'dos-dispatch')]"
```

(`dos init --skills` writes to `.claude/skills/` today; a host-aware skills
destination is future surface, noted in §5 — the copy above is the same bytes.)

## 4. The revisit trigger — PARTIALLY FIRED 2026-06-09 (enterprise CN)

> Tracking handle: [issue #27](https://github.com/anthony-chaudhary/dos-kernel/issues/27)
> — the implementation ticket that closes when the contract below is published
> and the docs/269 playbook lands.

The TRAE Enterprise (CN) changelog (volcengine doc 2529909, 2026-06-09)
announces Hooks for IDE Enterprise v3.3.65: "a lifecycle hook mechanism …
allowing enterprise users to insert custom logic at key nodes of agent
execution — interception, validation, and extension of agent behavior." That
is exactly the seam this note found missing. What is still missing is the
CONTRACT: no config-file path, no event vocabulary, no stdout grammar, no
exit-code semantics are published, and the international/personal editions
have nothing. A dialect is bytes a host parses — with no documented bytes
there is still nothing to render, so the §2 decision stands unchanged.

Implement the docs/269 playbook (driver renderer + install spec + two
entry-point rows + Go transcode case + parity bytes) and delete the §2
absence pins when any of these lands:

- Trae publishes the hooks detail page (config path, events, output/exit
  grammar) — for ANY edition; an enterprise-only dialect is still a dialect,
  shipped with an "enterprise-only" note like Codex's coverage caveat;
- the international changelog (`docs.trae.ai/ide/changelog`) ships hooks;
- Trae-AI/TRAE #2169 (deterministic enforcement) closes as shipped;
- a `.trae/hooks.json` (or hooks key in any Trae settings file) appears in the
  official docs — or the grammar is verified directly against an enterprise
  build.

## 5. Change set + scope fence

- `docs/294` (this note) — the proved-out record.
- `tests/test_hook_dialect.py` + `tests/test_init_hooks_crossvendor.py` — the
  deliberate-absence pins (§2).
- `src/dos_mcp/README.md` — the Trae wiring section (§3a) + the advisory-only
  caveat.
- `docs/readme/50_agent-hosts.md` (→ regenerated `README.md`) + `AGENTS.md` —
  Trae named where hosts are enumerated, always with the advisory-only caveat.
- **Not done, on purpose:** no `TraeDialect`, no `trae_install_spec`, no Go
  transcode case (§2); no host-aware `dos init --skills` destination (a real
  future lift — it needs a seam, not a `.trae` literal in `cli.py`, per the
  vendor-blindness litmus); no TRAE-CLI `dos guard` integration (its launch-flag
  surface is undocumented/JS-rendered — unverified facts don't ship).

## 6. Provenance

Facts web-grounded 2026-06-10 (sources in §1; the official docs are JS-rendered
SPAs, so the doc-index TOCs, search-indexed page content, changelog, the
open-source `trae-agent` repo, and the issue tracker were the checkable record).
The decision logic is docs/217's fail-LOUD rule + docs/278's
no-surface-no-dialect precedent; the three-surface frame is docs/269 §0. The
DOS-side facts (the seam's resolver behavior, the skill pack's host-freedom)
were read from `src/dos/hook_dialect.py` / `hook_install.py` /
`src/dos/skills/`, not from the contract.
