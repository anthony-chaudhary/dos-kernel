## Install

Pick the row that matches how you work — the full matrix (every OS, every
channel, upgrade/uninstall, WSL, troubleshooting) is in
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

For most repos that one `dos.toml` is the whole policy surface — but when your
lanes must be *computed* (from runtime state, an env var, a monorepo manifest)
rather than listed, or you add a provider-backed JUDGE, you write a small
**driver** instead: a `dos/drivers/<host>.py` exposing a `LaneTaxonomy` constant +
a `<host>_config` factory, loaded by name via `dos --driver <host>` and never
imported by the kernel. Copy [`dos/drivers/workshop.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/drivers/workshop.py)
as the template; the full driver/plugin map is in **[docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)**.

### Claude Code plugin — hooks + MCP + skills in one install

If you drive a fleet with Claude Code, the lowest-friction way to bind the
verdict to the runtime is the bundled plugin under
[`claude-plugin/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/claude-plugin) — it packages all three runtime surfaces at
once:

- the **hooks** (`PreToolUse` → deny a structurally-refused call · `PostToolUse` →
  re-surface a stalled tool stream · `Stop` → refuse to stop on an unverified
  claim) — all fail-safe (they emit nothing and exit 0 on any error, so they never
  break a turn);
- the **MCP server** (`dos_verify` / `dos_arbitrate` / `dos_commit_audit` /
  `dos_refuse_reasons` … as tools the model calls directly);
- the **generic skill pack** (the domain-free dispatch screenplays), namespaced as
  `/dos-kernel:dos-next-up`, `/dos-kernel:dos-dispatch`, …

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
same three hooks are available à la carte via `dos init --hooks claude-code`
(and for Cursor / Codex / Gemini); the plugin is just the pre-packaged Claude
Code form. The bundle's design + the build that keeps its skills in lockstep
with the source are in **[claude-plugin/README.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md)**.
