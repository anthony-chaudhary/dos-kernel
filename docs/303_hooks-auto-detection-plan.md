# 303 — `--hooks auto`: detect the runtime, wire the hooks, ask nothing

> **Status:** planned (2026-06-12). The forcing question (operator `/goal`):
> *make DOS 10× more attractive to people who face the problem it solves but
> have no idea what a trust layer is — people who just want to add it and get
> the benefits without thinking.* This plan ships the product half (P1: a
> `dos init --hooks auto` that finds the agent runtime for you) and the funnel
> half (P2: a two-command "just add it" block at the top of the README, in
> benefit words, with `auto` as the lead command everywhere a host name used
> to be required).

---

## 1. The problem, plainly

Someone runs coding agents. The agents sometimes say "done" when nothing
landed. That person does not know the words "trust substrate", "PDP", or
"hook dialect", and does not want to. Today, to get DOS's protection, the
README asks them to make four decisions before anything happens:

1. pip, uv, or clone?
2. MCP (advisory) or hooks (enforcement) or the Claude Code plugin?
3. Which host name to type: `claude-code`, `cursor`, `codex`, `gemini`,
   `antigravity`, or `claude-cowork`?
4. Core install or the `[mcp]` extra?

Each decision is a place to stop reading. The person we want already has the
answer to #3 sitting on disk — their repo has a `.cursor/` or `.claude/`
directory, because their runtime put it there. DOS should read that fact
instead of asking the user to know it. The same "I/O at the boundary, facts
as data" move the kernel already makes everywhere else (docs/82, the
`WorkspaceFacts` seam) applies to the installer itself.

## 2. The design

### P1 — the `auto` host (mechanism)

`dos init --hooks auto` detects which runtimes this workspace already uses and
wires every one it finds. Detection has two rungs, both read-only:

- **The config-dir rung.** A host is in use if its config directory exists
  under the workspace root. The signal is already carried as data by every
  `HostHookSpec.config_path` (`.claude/`, `.cursor/`, `.codex/`, `.gemini/`,
  `.agents/`) — no new vendor facts, no new vendor names in the kernel. A new
  pure helper `detection_probe(spec)` returns the parts to check (the config
  file's parent dir, or the file itself for a root-level config).
- **The env-marker rung.** A fresh repo has no config dir yet — but if the
  installer is being run *from inside* a host (the most common first-touch:
  an agent or its terminal running `dos init`), that host announces itself in
  the environment. A new `HostHookSpec.env_markers: tuple[str, ...] = ()`
  field carries the variable names as per-spec DATA; the built-in default
  host carries `("CLAUDECODE",)` (the variable Claude Code exports into every
  shell it spawns). Driver hosts whose markers are unverified carry `()` —
  the dir rung covers them. The detection machinery reads the field; it never
  compares against a vendor literal, so the vendor-agnostic-kernel pins
  (`tests/test_vendor_agnostic_kernel.py`) hold unchanged.

Two disciplines carried over from docs/221:

- **Shared config files dedupe.** `claude-code` and `claude-cowork` write the
  same `.claude/settings.json`; wiring one wires both. A pure
  `choose_auto_hosts(detected)` keeps one owner per config path (the default
  host wins its shared file), and reports the covered siblings so the
  operator message can say "also covers claude-cowork" instead of wiring
  twice or staying silent.
- **Nothing detected fails LOUD, with the menu.** An empty repo with no
  markers gets exit 1 and the list of probe dirs + explicit host names —
  never a guessed default. A wrong guess would wire a no-op deny against the
  real runtime, the exact silent failure docs/217/221 exist to prevent.
  `host_spec("auto")` keeps raising: `auto` is an installer mode, not a host.

Everything else reuses the shipped path: per detected host, the same
`_install_host_hooks` merge (idempotent, user-config-preserving, `--force`
repairs, `--dry-run` previews each detected host and writes nothing).

### P2 — the funnel (the two-command install)

With `auto` shipped, the zero-decision adoption story is two lines:

```bash
pip install dos-kernel
dos init --hooks auto
```

P2 puts that block where the "don't make me think" reader actually lands, in
benefit words (what stops happening to you), not mechanism words:

- **README front door** (`docs/readme/00_front-door.md`): a callout right
  after the 60-second-demo pointer — two commands, then "from now on an
  agent can't tell you *done* unless the work really landed; two agents
  can't silently overwrite each other; a stalled run gets flagged. Nothing
  about your workflow changes." Undo line included (the installer prints the
  one file it wrote).
- **The hooks section** (`docs/readme/50_agent-hosts.md`): `auto` becomes the
  first line of the per-host block; the named hosts stay as the explicit
  forms.
- **The CLI's own strings**: the `dos doctor` "none wired" hint, the
  quickstart adoption router, and the `dos init` help examples all lead with
  `--hooks auto`.
- **AGENTS.md / docs/INSTALL.md**: the consumer-moves table and install matrix
  name `auto` first.

## 3. What this is NOT

- Not a new verb. `dos setup` was considered and rejected: the surface stays
  one installer (`init`), and `auto` is a value of the flag that already
  exists. Fewer names to learn is the point of the plan.
- Not enforcement-by-default at install time beyond what `--hooks <host>`
  already wires. `auto` changes *which host names you must know* (none), not
  *what gets wired* (the same three hooks).
- Not a probe of user-level config (`~/.claude`, …). The workspace is the
  unit DOS serves; the env rung already covers "I'm inside the host but the
  repo is fresh".

## 4. Phases

- **P1** — the `auto` host: `hook_install` seam (`AUTO_HOST`, `env_markers`,
  `detection_probe`, `choose_auto_hosts`) + the `cmd_init` boundary loop +
  `tests/test_init_hooks_auto.py`. Done when: `dos init --hooks auto` wires
  every detected host, dedupes the shared file, fails loud on nothing, and
  the suite is green.
- **P2** — the funnel: README front-door block + hooks-section lead +
  CLI strings (doctor hint, quickstart router, init help) + AGENTS.md +
  docs/INSTALL.md. Done when: the README's first screen contains the
  two-command path and `auto` leads every host-name list.

## 5. The litmus this plan answers to

The kernel stays vendor-blind: detection reads spec DATA (`config_path`,
`env_markers`) and never branches on a vendor name; the only kernel-resident
vendor fact remains the unshadowable default host, exactly as docs/217/221
drew the line. And the installer stays honest: it detects, names what it
found, and wires the same auditable hooks — it never invents a host.
