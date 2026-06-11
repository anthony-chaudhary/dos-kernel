# 298 — Claude Cowork, the sixth host (the shared-surface host)

> **A vendor is a data row — even when the row equals the default.** Claude Cowork
> is Anthropic's agentic desktop app for general knowledge work. Under the UI it
> runs the **Claude Code agent harness** inside a Linux VM. So unlike every prior
> host, Cowork brings no new config file, no new event names, and no new output
> grammar: its hook surface IS the `claude-code` one. First-class support means the
> name resolves everywhere a host name can appear — and the row carries, as data,
> the one fact that is Cowork's own: **the product does not fire hooks yet.**

*Status: SHIPPED. As of 2026-06-10.*

## 0. What "first-class support for Claude Cowork" means

DOS binds to an agent runtime on three surfaces (docs/217 §0, the same frame
docs/269 used for Antigravity):

1. **Kernel syscalls** — vendor-agnostic by construction. Cowork gets these free.
2. **The MCP server** (`dos_mcp`) — the *advisory* path. Cowork reads Claude
   Desktop's `claude_desktop_config.json` and passes its MCP servers into the VM
   session, so the snippet the README already shows for Claude Desktop is
   literally Cowork's wiring. **This surface works in Cowork today** and is the
   working DOS surface there.
3. **Hooks** — the *enforcement* path. This is the per-vendor surface, and for
   Cowork it is a peculiar one: the right file, shape, events, and envelope all
   already exist in DOS under the name `claude-code`. What was missing is the
   NAME: `dos init --hooks claude-cowork` failed loud as an unknown host, telling
   a Cowork operator DOS does not know their runtime — false at every level that
   matters. After this change the name resolves (installer, dialect, doctor, Go
   fast path), and the operator gets Cowork's real facts printed instead of a
   refusal.

## 1. The facts (web-grounded 2026-06-10)

Confirmed against Anthropic's own Cowork architecture overview (the Claude Help
Center), the Anthropic engineering post on containment, an Anthropic engineer's
public description, and the upstream issue tracker. Like every vendor's surface
these churn per release — pin and re-verify.

| Facet | Claude Cowork |
|---|---|
| What it is | Anthropic's agentic desktop app (research preview, Jan 2026) for general knowledge work — file organization, data extraction, reports |
| The harness | **the Claude Code agent harness**, run inside a dedicated Linux VM (Apple Virtualization.framework on macOS, Hyper-V on Windows); the agent loop itself runs natively on the device |
| Workspace model | the user selects a project folder; it is shared into the VM (VirtioFS on macOS) — a live mount of the real folder, not a copy |
| Config surface | reuses the Claude Code settings structure: `~/.claude/settings.json` user scope, the workspace `.claude/` project scope; skills auto-discovered from `~/.claude/skills/` |
| Hook config grammar | Claude Code's — `.claude/settings.json`, group-wrapped `PreToolUse`/`PostToolUse`/`Stop` (inherited with the harness) |
| Hook output grammar | Claude Code's — the nested `hookSpecificOutput` envelope (same harness, same parser) |
| **Do hooks fire?** | **Not yet.** Verified upstream 2026-05-28: user-scope hooks do not fire in Cowork sessions, and there is no `/hooks` command (anthropics/claude-code#63360, open). The wrinkle is architectural: sessions run in the VM while hook config and scripts live on the host. A Windows bug also skips mounting `.claude/` in some cases (#32538) |
| MCP | Claude Desktop's `claude_desktop_config.json` (`mcpServers`); local servers run on the host and are passed into the VM session |
| Plugins / skills | supported (plugin marketplaces; skills work as in Claude Code) |

## 2. Why Cowork is the *shared-surface* host

Every prior host owned its config file: `.cursor/hooks.json`, `.codex/config.toml`,
`.gemini/settings.json`, `.agents/hooks.json`. Cowork is the first host whose hook
surface is **another host's file** — the same `.claude/settings.json` Claude Code
reads, because they are the same harness. Three consequences, each the inverse of
a problem the seam was built for:

- **The wired command carries NO `--dialect` flag.** The shared file is read by
  BOTH runtimes, so whatever command is wired there must emit bytes both honor.
  Both run the CC harness, so the one universally-correct envelope is the default
  one — and `dialect_flag=""` (like `claude_code_spec` itself) keeps the wired
  command byte-identical to a `--hooks claude-code` install. An explicit
  `--dialect claude-cowork` would render the same bytes through one more
  resolution step (one more way to fail on stale entry-point metadata) and buy
  nothing: a per-runtime divergence could never ride a shared file anyway.
- **Wiring either name wires both — and that is the feature, not a collision.**
  The merge is idempotent on the `dos hook ` command prefix, so
  `dos init --hooks claude-cowork` after `--hooks claude-code` (or the reverse)
  reports the events `already` wired and changes nothing. One file, one set of
  hooks, two runtimes bound.
- **`dos doctor` reports both bindings, truthfully.** The probe reads the config
  file per host spec; after wiring, both `claude-code` and `claude-cowork` show
  the three events wired — which is exactly the state of the world.

The `ClaudeCoworkDialect` still ships as its own named renderer, delegating to the
CC renderer — the Codex precedent verbatim: an explicit by-name entry (so
`--dialect claude-cowork` resolves for an Agent-SDK consumer driving the verbs
directly) and a home if Cowork's envelope ever diverges. The Go fast path adds
`claude-cowork` to its CC-identical transcode case, pinned by the parity test, so
the binary and the Python verb cannot drift on the sixth host either.

## 3. The enforcement caveat, carried as data

Cowork sits between two precedents, and choosing the right one is the design:

- **Not Trae (docs/294).** Trae has NO hook seam — no events, no grammar — so a
  `trae_install_spec` would write config nothing reads in a grammar DOS invented:
  fake enforcement, refused loud. Nothing about Cowork is invented: the grammar,
  events, file, and envelope are the CC harness's own, defined and parsed by the
  code Cowork runs.
- **The Codex precedent (docs/221).** Codex fires `PreToolUse` on only some
  handlers — a host *coverage limit*, shipped as a `note` on the spec and printed
  at wiring time. Cowork's limit is the same kind, currently at its maximum: the
  harness defines the hooks, the product does not fire them yet (#63360).

So `claude_cowork_install_spec()` ships with a LOUD note: the hooks wire to the
surface Cowork's own harness defines, Claude Code enforces them on this workspace
today, and Cowork itself will start enforcing them when the upstream issue closes
— no DOS change needed then, because the bytes are already the right ones. Until
that day, Cowork's *working* DOS surface is the advisory one (MCP + skills), which
is cross-vendor and already shipped. An operator who wires hooks today loses
nothing and gains enforcement on the shared workspace wherever Claude Code runs it.

Two practical notes ride along: the wired command (`dos hook …`) must be on PATH
inside the session that fires it — for a VM session that means `pip install
dos-kernel` inside Cowork's environment (Cowork can do this itself when asked);
and on Windows the `.claude/` mount has an open bug (#32538), so verify with
`dos doctor` from inside the session before trusting the binding.

## 4. The change set

- `src/dos/drivers/hook_dialects.py` — `ClaudeCoworkDialect` (delegates to the CC
  renderer, like Codex) + `claude_cowork_install_spec()` (the CC file/shape/events
  with `dialect_flag=""` and the Cowork note). Both in the driver: each names the
  vendor as code, which the vendor-blindness litmus forbids in a kernel module.
- `pyproject.toml` — two entry-point rows: `claude-cowork` under
  `dos.hook_dialects` and under `dos.hook_installs`.
- **The Go fast path** — `claude-cowork` joins the CC-identical case in
  `dialect_transcode.go`; `parity_dialect_test.go` pins the bytes to Python's.
- `src/dos/cli.py` — help-text prose only (the `--hooks` choices come from
  `host_names()` and `--dialect` resolves by name; both pick the new host up by
  discovery).
- Tests: `tests/test_hook_dialect.py` (CC-identical render, the fail-open floor,
  PASS/no-rewrite-key matrices extended to the sixth host) +
  `tests/test_init_hooks_crossvendor.py` (writes the shared file byte-identical to
  `--hooks claude-code`, idempotent across the pair, the caveat note surfaced).
- Docs: docs/217 §7 / docs/221 §3c addenda, `docs/readme/50_agent-hosts.md` (+
  regenerated `README.md`), `src/dos_mcp/README.md`, `claude-plugin/README.md`,
  and this note.

Phases for the oracle: **P1** = driver + entry points + Python tests; **P2** = Go
fast-path parity; **P3** = the surface docs.

## 5. The litmus tests this keeps green

- **Kernel imports no host / names no vendor in code.** Every Cowork token lives
  in `drivers/` (or prose); no new entry in `_VENDOR_CODE_EXCEPTIONS`.
- **A wrong host/dialect still fails LOUD.** Cowork adds a name; the resolvers
  still raise on unknowns. `--hooks trae` keeps failing (docs/294 pin untouched).
- **The default is byte-for-byte today.** `--with-hooks` / `--hooks claude-code`
  are untouched — and the new host's install is byte-identical to them, pinned.
- **No rewrite key, on the sixth host too.** The no-`updated_input` matrix and the
  fail-open DENY floor are parametrized over every dialect, `claude-cowork` now
  included.

## 6. Scope fence (what this does NOT do)

- It does **not** claim hook enforcement works in Cowork today. The note says the
  opposite, loudly, with the upstream issue to watch. When #63360 closes, the only
  DOS change is shrinking the note.
- It does **not** wire MCP automatically — `claude_desktop_config.json` is the
  operator's file and the snippet is one line (`src/dos_mcp/README.md`); `dos
  guard --mcp-config` already injects generically.
- It does **not** touch the VM: DOS does not install itself inside Cowork's
  environment or patch mounts. The note states the PATH requirement; the operator
  (or Cowork, asked) does the install.
- It does **not** add Cowork-only events (`UserPromptSubmit`, `SessionStart`, …) —
  DOS's three lifecycle moments map onto the tool + stop seams, as on every host.

## 7. Provenance

Implements the docs/217 dialect seam + docs/221 installer for the sixth host, per
the docs/269 playbook (Antigravity, the fifth). Cowork facts web-grounded
2026-06-10: the Claude Help Center *Claude Cowork desktop architecture overview*
("the Claude Code agent harness running inside a Linux VM", the two execution
environments, the VirtioFS workspace share), the Anthropic engineering post *How
we contain Claude across products*, and the upstream tracker
(anthropics/claude-code#63360 — hooks verified not firing in Cowork, 2026-05-28;
\#32538 — the Windows `.claude/` mount gap). The DOS-side mechanics (the
`dialect_flag`-as-data design, the `dos hook ` idempotency prefix, the group-wrap
merge, the Go transcode switch) were read from `src/dos/hook_install.py` /
`hook_dialect.py` / `drivers/hook_dialects.py` / `go/internal/hook/`, not from the
contract.
