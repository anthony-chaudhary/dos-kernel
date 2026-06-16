# 221 — The cross-vendor hook installer (`dos init --hooks <host>`)

> **The verdict is the kernel; the envelope is a driver; the *wiring* is an
> installer.** docs/217 made DOS render a deny into the bytes Cursor / Codex /
> Gemini honor. This plan closes the last inch: a one-command installer that writes
> the *host's own hook-config file* so a team already running one of those runtimes
> binds the DOS PEP with **no hand-authored YAML/TOML/JSON** — the `dos init
> --skills` analogue for the runtime-binding seam (docs/217 §4 Phase 4, the
> installer half).

*Status: PLAN. Implements docs/217 §4 Phase 4 (installer only; the `[hook_dialect]`
config table and `dos hook-dialect-eval` stay future). As of 2026-06-07.*

> **Phase stamp.** Phase `P1` (the `dos init --hooks <host>` cross-vendor
> installer + the host-wiring roster) shipped across the docs/221 series (e.g.
> `b795e3b`, `e9bd9ac`, `124d105`); `dos verify --workspace . docs/221 P1`
> resolves it via this commit's `(docs/221 P1)` trailer. The `[hook_dialect]`
> config table stays a future leg, by design.

## 0. The gap this closes (the adoption finding)

DOS has three binding surfaces with different cross-vendor reach (docs/217 §0). Two
are already cross-vendor: the **kernel syscalls** (adjudicate git + the lane
journal, the worker's vendor is invisible) and the **MCP server** (a tool the agent
*calls*, all three rivals are MCP clients). The third — **hooks**, the PEP that
*denies* a call at the runtime seam — shipped its renderers for all four hosts in
docs/217 (`hook_dialect.py`: `claude-code` / `codex` / `gemini` / `cursor`, +
`--dialect` on `dos hook pretool|posttool`). But the **installer** that writes a
host's hook-config file is **Claude-Code-only**: `dos init --with-hooks` merges into
`.claude/settings.json` and nothing else (`cli.py:_install_hooks`,
`_DOS_HOOK_COMMANDS`).

So a team **already on Cursor / Codex / Gemini** — exactly the "existing userbase"
DOS wants to integrate into — gets the right *renderer* but must **hand-author**
their host's hook config: three different file paths, two different file *formats*
(JSON for Cursor/Gemini, TOML for Codex), three different event-name vocabularies.
That is the 80/20 cliff: the verdict is computed and rendered correctly, but the
last wiring step is manual, undocumented per-host, and easy to get silently wrong
(a misnamed event = a no-op deny, the original `dos hook stop`-vs-CC bug).

This installer makes the wiring **one command per host**:

```bash
dos init --hooks cursor .      # writes .cursor/hooks.json
dos init --hooks codex  .      # writes .codex/config.toml
dos init --hooks gemini .      # writes .gemini/settings.json
dos init --hooks antigravity . # writes .agents/hooks.json
dos init --hooks claude-code . # writes .claude/settings.json (== today's --with-hooks)
dos init --hooks claude-cowork . # writes the SAME .claude/settings.json (shared harness, §3d)
```

## 1. The design — `hook_install.py`, the install-facts sibling of `hook_dialect.py`

docs/217's `hook_dialect.py` owns **what bytes a verdict becomes** (the envelope).
It deliberately does **not** own **where/how those bytes get wired into a host** (the
config-file path, format, and event-name strings) — that is a different fact, and
folding it into the renderer would mix "what the deny looks like" with "which file
registers the hook." So this plan adds a parallel pure-data module:

`src/dos/hook_install.py` — for each of the four hosts, a frozen `HostHookSpec`:

```python
class ConfigFormat(enum.Enum):
    JSON = "json"   # Claude Code, Cursor, Gemini
    TOML = "toml"   # Codex

@dataclass(frozen=True)
class HostHookSpec:
    host: str                       # "cursor" — the --hooks value AND the --dialect name
    config_path: tuple[str, ...]    # path parts under the workspace: (".cursor", "hooks.json")
    fmt: ConfigFormat
    # which host event fires each DOS lifecycle moment → the DOS verb that handles it.
    # A moment maps to ONE-OR-MORE host events (Cursor's PRE is shell+MCP); each event
    # gets the same dos-hook command (with the right --dialect appended).
    pre_events: tuple[str, ...]     # ("beforeShellExecution", "beforeMCPExecution")
    post_events: tuple[str, ...]    # ("afterFileEdit",)
    stop_events: tuple[str, ...]    # ("stop",)
    version_key: bool               # Cursor's hooks.json needs {"version": 1}
```

The DOS verb per moment is fixed (the same three shipped verbs as the CC installer):
`pre → dos hook pretool`, `post → dos hook posttool`, `stop → dos hook stop`. The
installer appends `--workspace .` (as today) **and** `--dialect <host>` for the
non-CC hosts, so the verb emits the right envelope. (`claude-code` omits
`--dialect`, keeping its command byte-identical to today's `--with-hooks` — the
parity floor.)

### 1a. The verified facts table (web-grounded, 2026-06-07)

Confirmed against each vendor's then-current hook docs (Gemini CLI hooks reference;
Codex CLI hooks reference; Cursor hooks reference) — not guessed. Event names and
config shapes **churn every minor release** (docs/217 §0 caveat); this targets the
dialects as of 2026-06-07, and the `[hook_dialect]` table (the *other* Phase-4 half,
deferred) is the eventual override seam so drift becomes a data change.

| Host | Config file (under workspace) | Format | PRE event(s) | POST event | STOP event | Entry shape |
|---|---|---|---|---|---|---|
| **claude-code** | `.claude/settings.json` | JSON | `PreToolUse` | `PostToolUse` | `Stop` | `{"hooks":[{"type":"command","command":C}]}` (group list) |
| **cursor** | `.cursor/hooks.json` | JSON (`"version":1`) | `beforeShellExecution`, `beforeMCPExecution` | `afterFileEdit` | `stop` | `{"command":C}` (flat list) |
| **codex** | `.codex/config.toml` | TOML | `PreToolUse` | `PostToolUse` | `Stop` | `[[hooks.EVENT]]` + nested `[[hooks.EVENT.hooks]]` `type/command` |
| **gemini** | `.gemini/settings.json` | JSON | `BeforeTool` | `AfterTool` | `AfterAgent` | `{"type":"command","command":C}` (flat list) |
| **antigravity** | `.agents/hooks.json` | JSON | `PreToolUse` | `PostToolUse` | `Stop` | `{"hooks":[{"type":"command","command":C}]}` (group list, CC-shaped) — but the hook **output** is Gemini-shaped `{"decision":"deny"}` (§3c) |
| **claude-cowork** | `.claude/settings.json` — the **same file** as claude-code (same harness) | JSON | `PreToolUse` | `PostToolUse` | `Stop` | CC group-shaped; the wired command carries **no** `--dialect` (a shared file must serve both runtimes — §3d; the app doesn't fire hooks yet, #63360) |

Notes that shape the installer (each a real per-vendor constraint):

- **Cursor's PRE is two events** (`beforeShellExecution` + `beforeMCPExecution`) so
  a refused call is caught whether it is a shell command or an MCP tool. Its entry
  is a *flat* `{"command": …}` (no `type`, no group wrapper), and the file needs
  `{"version": 1}`. Its `stop` event "fires when the agent loop ends." Cursor honors
  `failClosed` — the binding note recommends it for the PRE deny, but the installer
  does **not** set it by default (DOS's own fail-direction is fail-to-PASS; a host
  failing closed on DOS *crashing* is the operator's call — documented, not imposed).
- **Codex is CC-shaped but TOML** (`[[hooks.PreToolUse]]` → `[[hooks.PreToolUse.hooks]]`
  with `type="command"`). Its one real divergence is *coverage* (only
  Bash/apply_patch/unified_exec/mcp handlers fire `PreToolUse`) — a host limit, not
  a render/install difference; documented, not worked around (docs/217 §3).
- **Gemini uses its own event names** (`BeforeTool`/`AfterTool`/`AfterAgent`) and a
  flat `{"type":"command","command":…}` entry under `hooks`. `AfterAgent` ("once per
  turn after the model generates its final response") is the `Stop` analogue — the
  seam where `dos hook stop` refuses a premature done.
- **The anti-laundering rule is inherited unchanged** (docs/191 §4): the installer
  wires the deny/observe verbs; it never wires a tool-input *rewrite*. No host's
  rewrite key (`updatedInput`/`updated_input`) is ever written — the renderers
  already forbid it, and the installer adds no new surface for it.

### 1b. The pure core + the I/O boundary (the litmus-preserving split)

`hook_install.py` is **pure data + pure merge functions** — it owns the facts table
and the in-memory merge ("given the parsed config object, return it with the DOS
hooks added, idempotently"). The **file read/parse/write is at the CLI boundary**
(`cmd_init`), exactly as `_install_hooks` does today and exactly the
"I/O at the boundary, data to the pure core" rule the kernel rests on
(`git_delta`/`journal_delta` → `liveness.classify`).

Two merge functions, one per format:

- `merge_json(existing: dict, spec) -> (dict, wired, already)` — for
  claude-code/cursor/gemini. Mirrors today's `_install_hooks` merge: set up
  `hooks[event]` lists, append the DOS entry iff no DOS entry is already present
  (idempotent), preserve every other key/hook the user has. Cursor's `version` key
  is set if absent.
- `merge_toml(existing_text: str, spec) -> (str, wired, already)` — for codex.
  Codex's `config.toml` is hand-edited and comment-rich, so the safe move is to
  **append** a clearly-fenced `# >>> dos hooks >>> … # <<< dos hooks <<<` block of
  `[[hooks.EVENT]]` tables iff a DOS block is not already present (idempotent on the
  fence marker), never to re-serialize the whole file (which would strip the user's
  comments). This is the one host where we manipulate *text*, not a parsed object —
  because TOML round-tripping with comments is not in the stdlib (`tomllib` is
  read-only) and pulling in `tomlkit` would violate the PyYAML-only kernel dependency
  floor.

Idempotency key per host: a JSON entry whose `command` starts with `dos hook `
(today's `_is_dos_hook_group` rule, generalized); the TOML fence marker for Codex.

## 2. The CLI surface (`cli.py`)

One new flag on the `init` parser, plus keeping `--with-hooks` as the CC alias:

```
dos init [--hooks {claude-code,cursor,codex,gemini}] [--with-hooks] DIR
```

- `--hooks <host>` selects the host installer. `--with-hooks` is exactly
  `--hooks claude-code` (kept for backward-compat; the existing `test_init_hooks.py`
  suite must stay green unchanged — the parity floor).
- `cmd_init` routes both to a single `_install_host_hooks(target, host, force=…)`
  helper that: resolves the `HostHookSpec`, reads+parses the existing config file (if
  any) at the boundary, calls the right `merge_*`, writes it back, and prints the
  `wired …` / `left … untouched` lines (same UX as today).
- An **unknown host** fails LOUD on stderr (exit 1) — never a silent no-op, the same
  discipline `resolve_dialect` takes (a wrong host against the wrong file is the bug
  this plan prevents). `argparse choices=` gives the first line of defense; the
  helper re-checks so a programmatic caller gets the same guarantee.
- A **malformed existing config** is a reported error (exit 1) unless `--force`
  (which rescues it), identical to today's `_install_hooks` behavior.

The printed summary names the file and the events wired, so the operator sees
exactly what changed and where:

```
$ dos init --hooks cursor .
wrote dos.toml
  [lanes] derived from 6 top-level dirs …
wired 4 DOS hook(s) into .cursor/hooks.json:
  beforeShellExecution, beforeMCPExecution → dos hook pretool --dialect cursor
  afterFileEdit                            → dos hook posttool --dialect cursor
  stop                                     → dos hook stop --dialect cursor
  Cursor honors "failClosed": true on the PRE deny — add it if you want DOS-crash to block.
```

## 3. Build order (each rung independently shippable + testable)

1. **`hook_install.py`** — the `HostHookSpec` table for all four hosts + `merge_json`
   + `merge_toml` + a by-name `host_spec(name)` resolver (fail-LOUD on unknown).
   Pure; no I/O. Unit-tested directly (a spec → a merged object → asserted bytes).
2. **`cmd_init` wiring** — the `--hooks` flag, the `_install_host_hooks` boundary
   helper (read/parse/write), `--with-hooks` re-expressed as `--hooks claude-code`.
   **Gate: `test_init_hooks.py` passes byte-unchanged** (the CC parity floor).
3. **Cross-vendor tests** (`test_init_hooks_crossvendor.py`) — per host: the right
   file at the right path in the right format; the right events; the dialect flag on
   the command; merge preserves a user's own hooks/keys; idempotent on re-run;
   `--force` repairs a malformed file; unknown host fails loud.
4. **Docs** — a short "wire DOS into your runtime" section pointing at the one
   command per host (README + the onboarding playbook); the per-host MCP wiring
   snippets already live in `src/dos_mcp/README.md` (docs/217 §6).

## 3a. As-built (2026-06-07) — the kernel/driver split the vendor litmus forced

The §1 sketch put all four `HostHookSpec` rows in `hook_install.py`. Building it hit
`tests/test_vendor_agnostic_kernel.py`: a non-driver kernel module may not name a
vendor as a **code identifier** (`_GEMINI`, `_codex_block`) or as a **comparison
operand** (`if self.host != "claude-code"`). That litmus is correct and load-bearing
— it is the structural proof no kernel *decision* can branch on which vendor is
acting — so the build moved to honor it, mirroring the split a concurrent session had
just made for the dialect *renderers* (the per-vendor `CodexDialect`/`GeminiDialect`/
`CursorDialect` now live in `drivers/hook_dialects.py`, discovered via the
`dos.hook_dialects` entry-point group; only `ClaudeCodeDialect`, the default, stays
in the kernel `hook_dialect.py`).

So the as-built install seam is the **same** shape:

- **Kernel `hook_install.py`** holds the pure, vendor-blind machinery: the
  `HostHookSpec` type, the `merge_json` / `merge_toml` algorithms (named `merge_toml`,
  not `merge_codex_toml` — it fences a block for *any* TOML host, carrying no vendor
  token), and the ONE unshadowable baseline `claude_code_spec()` (the
  `ClaudeCodeDialect` analogue). The host's identity rides a `dialect_flag` **data
  field**, never a `self.host == "…"` branch — so `command_for` compares nothing.
- **Driver `drivers/hook_dialects.py`** gains `cursor_install_spec()` /
  `codex_install_spec()` / `gemini_install_spec()` — the rows that must name a vendor
  — co-located with the dialect renderers they pair with. They register under a new
  `dos.hook_installs` entry-point group; `hook_install.host_spec("cursor")` discovers
  them by name, exactly as `resolve_dialect("cursor")` discovers the renderer. The
  kernel imports none of them.

The one allowance, identical to the dialect seam: the kernel names the `claude-code`
**default** (`claude_code_spec`, `DEFAULT_HOST`) — the baseline DOS's own sensors
emit, not an adjudication branch — added to `_VENDOR_CODE_EXCEPTIONS` as the same
`claude`-only exception `hook_dialect.py` already carries.

## 3b. As-built (2026-06-07) — `dos doctor` reports the binding

A mis-wired hook is a **silent no-op** (the host ignores an envelope it doesn't
parse, the deny never fires, and nothing tells the operator). So the installer is
paired with a read-only **confirmation surface**: `dos doctor` grew a `runtime hooks`
line that probes each host's config file under the workspace and reports which DOS
events are wired —

```
runtime hooks       none wired   (run `dos init --hooks <runtime>` to bind)   # before
runtime hooks       claude-code (3), cursor (4)                               # after
```

— and the same fact rides the `--json` form as `runtime_hooks: {host: [events…]}`
for a skill/CI to gate on. It is the inverse of the merge (`hook_install.wired_events_json`
/ `wired_events_toml`, both PURE), read at the doctor boundary; doctor writes no
config (its read-only contract holds — a probe never creates a file). This closes
the "did my `dos init --hooks` actually take effect?" loop, which is the single
biggest silent-failure risk of the whole feature. Pinned by
`test_init_hooks_crossvendor.py` (detection-inverts-merge + doctor-reports-binding +
doctor-is-read-only).

## 3c. As-built (2026-06-09) — Antigravity, the hybrid host (a new row, zero new machinery)

Google **Antigravity** (the agentic IDE + CLI) was added as the fifth host. It is the
clean test of "a vendor is one data row": the entire change is `antigravity_install_spec()`
+ `AntigravityDialect` in `drivers/hook_dialects.py` and two `pyproject.toml` entry-point
rows (`dos.hook_installs` + `dos.hook_dialects`) — **no edit to `hook_install.py`,
`cli.py`, or any kernel module**. `host_names()` / `host_spec()` / the `--hooks` choices /
the `dos doctor` runtime-hooks line all pick it up by discovery.

Antigravity is the interesting row because it **mixes the two axes** the first four hosts
kept aligned:

- its **config shape is Claude-Code's** — group-wrapped `{"hooks":[{"type":"command",
  "command":C}]}` entries under `PreToolUse`/`PostToolUse`/`Stop` in `.agents/hooks.json`
  (so `json_group_wraps=True`, exactly like `claude_code_spec`), but
- its **hook output is Gemini's** — a top-level `{"decision":"deny","reason":…}` on stdout
  (not CC's nested `permissionDecision`), so the wired command carries
  `--dialect antigravity` pointing at the Gemini-shaped `AntigravityDialect` (docs/217 §7).

This is exactly why §3a's **`dialect_flag`-as-data** design matters: a host that wants CC's
config grammar with a different output grammar is still a pure data row — `command_for`
appends the flag, never compares `self.host`. The byte-author floor (no tool-input rewrite
key) and the fail-LOUD-on-unknown-host discipline are inherited unchanged. Facts
web-grounded 2026-06-09; pinned by the `antigravity` cases in
`test_init_hooks_crossvendor.py` (CC-shaped config + the `--dialect antigravity` flag +
idempotency) and `test_hook_dialect.py` (the Gemini-shaped output bytes).

## 3d. As-built (2026-06-10) — Claude Cowork, the shared-surface host (a row that equals the default)

**Claude Cowork** (Anthropic's agentic desktop app) was added as the sixth host —
the inverse stress of Antigravity's hybrid: every facet of
`claude_cowork_install_spec()` equals `claude_code_spec()` (file, format, shape,
events, and a deliberately EMPTY `dialect_flag`), because Cowork runs the same
Claude Code harness and reads the same workspace `.claude/settings.json`. So
wiring either host name wires both (the merge is idempotent on the `dos hook `
prefix — pinned in both orders), `dos doctor` truthfully reports both bindings,
and the row's whole value is the NAME resolving plus the `note` carrying Cowork's
one host-specific fact: the app does not fire hooks yet
(anthropics/claude-code#63360) — the "coverage limit as data" discipline (§1a's
Codex note) at its maximum. Zero kernel change, two entry-point rows, the
docs/269 playbook verbatim. Facts web-grounded 2026-06-10; details in
[docs/298](298_claude-cowork-the-sixth-host-shared-surface.md).

## 4. The litmus tests this plan keeps green

- **Kernel imports no host.** `hook_install.py` names host *wire-formats and
  file-paths* (data — `".cursor/hooks.json"`, `"beforeShellExecution"`), never a host
  *module*. Identical to how `stamp`/`reasons` name grammars and `hook_dialect` names
  envelopes. Grep-checkable (`import job` / a host lane never appears).
- **The default is byte-for-byte today.** `dos init --hooks claude-code` (and the
  `--with-hooks` alias) reproduces today's `.claude/settings.json` exactly — the
  existing `test_init_hooks.py` suite, run unchanged, is the proof (the Phase-1-style
  parity gate).
- **A wrong host fails LOUD.** `host_spec("typo")` raises; `dos init --hooks typo`
  exits 1 with the known-hosts list — never writes a file to the wrong place, never a
  silent no-op (the `resolve_dialect` discipline).
- **Merge never clobbers the user.** A pre-existing config with the user's own
  hooks + unrelated keys survives; the DOS entries are *added*, and a second run adds
  nothing (idempotent). Pinned per host.
- **No rewrite key is ever wired.** The installer wires only the deny/observe verbs
  (`dos hook pretool|posttool|stop`); it writes no host input-rewrite key. The
  docs/191 §4 byte-author floor, preserved at the install layer too.
- **PyYAML-only kernel dependency floor holds.** Codex TOML is handled by
  text-fencing (append a marked block), not by adding a TOML round-trip dependency —
  the kernel's dependency set stays PyYAML-only (`tomllib` read is stdlib; we don't
  even need it for the append path).

## 5. What this does NOT do (scope fence)

- It does **not** build the `[hook_dialect]` config table or `dos hook-dialect-eval`
  (the other Phase-4 half + Phase 5 of docs/217). Those stay future; this is the
  installer alone — the highest-leverage adoption rung.
- It does **not** make DOS a PEP by default. The wired PRE hook runs `dos hook
  pretool`, whose default Rung-B posture is still observe/deny-only-on-structural-refusal
  (SELF_MODIFY et al.); this plan changes *which runtimes can be wired*, not *whether
  DOS denies by default* (docs/217 §6).
- It does **not** chase per-vendor coverage gaps (Codex's partial `PreToolUse`
  handler set; Cursor's headless-mode firing). Host limits; DOS wires correct bytes
  and documents the limit (docs/217 §3).
- It does **not** set `failClosed` for Cursor by default — it *documents* the option
  (DOS's own fail-direction is fail-to-PASS; the host's fail-on-crash is the
  operator's call).

## 6. Provenance

Implements docs/217 §4 Phase 4 (installer half). Vendor config-file facts
web-grounded 2026-06-07 on each runtime's then-current hook docs (Gemini CLI hooks
reference — `BeforeTool`/`AfterTool`/`AfterAgent`, `.gemini/settings.json`; Codex CLI
hooks reference — `[[hooks.PreToolUse]]`, `.codex/config.toml`; Cursor hooks
reference — `beforeShellExecution`/`beforeMCPExecution`/`afterFileEdit`/`stop`,
`.cursor/hooks.json` with `"version":1`). The DOS-side install mechanics (the
`_install_hooks` merge, `_DOS_HOOK_COMMANDS`, the idempotency rule) were read
directly from `src/dos/cli.py`. Builds on `hook_dialect.py` (docs/217, the renderer
this installer wires) and the SKP `--skills` installer (docs/207 Phase 7, the
`dos init` scaffolding pattern this mirrors). See
[[project-dos-cross-vendor-binding-audit]].
