# Installing DOS

DOS ships as **`dos-kernel`** on PyPI — the import name and CLI stay `dos`, only
the install/pin name is `dos-kernel`. Pick the row that matches how you work; all
of them put the same `dos` / `dos-mcp` commands on your PATH.

> **Pre-release note.** `dos-kernel` is not on PyPI *yet*. Until it is, the
> default install is **straight from the public repo** — swap `dos-kernel` for
> `git+https://github.com/anthony-chaudhary/dos-kernel.git` in any command below
> (the pip form is verified 2026-06-10: `pip install "dos-kernel[mcp] @ git+…"`
> installs, imports, and runs `dos quickstart` clean). A **clone** works the same
> way (swap in `.` / the clone dir) and is the contributor path.

> **The distribution name is `dos-kernel`, not `dos`.** A bare `pip install dos`
> pulls an unrelated package that squats the name on PyPI — it is not this
> project and would even shadow `import dos`. Always install `dos-kernel` (or
> `-e .` / a path, for a clone). See [SECURITY.md](../SECURITY.md), "Supply chain".

---

## Quick pick

| You want… | Use | Why |
|---|---|---|
| The modern, fast, isolated CLI | **`uv tool install git+https://github.com/anthony-chaudhary/dos-kernel.git`** | Brings its own Python, isolates the tool, handles PATH. (Post-PyPI: `uv tool install dos-kernel`.) |
| To try it once without installing | **`uvx --from git+https://github.com/anthony-chaudhary/dos-kernel.git dos quickstart`** | Ephemeral — runs the 60-second demo and discards, nothing left on your machine. |
| To add it to a project/host repo | **`pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"`** | The library-consumer path: a host pins `dos-kernel` in its own venv. (Post-PyPI: `pip install dos-kernel`.) |
| To hack on DOS itself | **`pip install -e .`** (clone) | Editable — your edits are live in the installed package. |
| A one-command bootstrap from a clone | **`./install.sh`** / **`.\install.ps1`** | Wraps `install.py`: venv + editable install + PATH, one line, any OS. |
| Hooks + MCP + skills in Claude Code | the **plugin** (see below) | All three runtime surfaces in one `/plugin install`. |

The core kernel's only runtime dependency is **PyYAML** — DOS is deliberately
near-stdlib. The extras below are opt-in (`mcp`, `tui`, …) and pull more only when
you ask for them.

---

## uv (recommended)

[uv](https://docs.astral.sh/uv/) is the fast Python package/tool manager. It
installs CLIs into isolated environments, downloads a matching Python if you
don't have one, and wires PATH for you — the modern replacement for `pipx`.

```bash
# Don't have uv yet? (installs uv itself — review the script first if you like:
#   curl -LsSf https://astral.sh/uv/install.sh | less )
curl -LsSf https://astral.sh/uv/install.sh | sh                      # macOS / Linux / WSL
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows
```

Then install DOS:

```bash
# straight from the public repo — the default today (pre-PyPI):
uv tool install git+https://github.com/anthony-chaudhary/dos-kernel.git
uvx --from git+https://github.com/anthony-chaudhary/dos-kernel.git dos doctor --workspace .

# post-publish, the registry forms:
uv tool install dos-kernel              # the `dos` + `dos-mcp` commands, isolated, on PATH
uv tool install "dos-kernel[mcp]"       # + the MCP server framework
uv tool upgrade dos-kernel              # later: bump to the newest release
uv tool uninstall dos-kernel            # remove it cleanly

# Run it once without installing (ephemeral):
uvx --from dos-kernel dos doctor --workspace .      # (post-publish)
uvx --from "dos-kernel[mcp]" --with mcp dos-mcp     # the MCP server, ephemerally

# from a clone (contributors):
uv tool install .                       # from inside a clone of this repo
uvx --from . dos doctor --workspace .
uvx --from ".[mcp]" --with mcp dos doctor --workspace .
```

**The fastest first contact** — the whole 60-second caught-lie demo, one command,
nothing installed, nothing left behind:

```bash
uvx --from "git+https://github.com/anthony-chaudhary/dos-kernel" dos quickstart
                                                    # straight from GitHub — no clone, no install (works now)
uvx --from /path/to/clone dos quickstart            # from a clone (works now — verified 2026-06-10)
uvx --from dos-kernel dos quickstart                # from PyPI (post-publish)
```

uv builds the package in an ephemeral environment, runs the demo (a real repo, a
real commit, one `SHIPPED`, one `NOT_SHIPPED`, the fleet arbiter act), and
discards everything. Zero commitment — if the verdict contrast doesn't sell it,
nothing on your machine has changed.

Already standardized on `pipx`? `pipx install dos-kernel` works the same way
(`pipx install .` from a clone). uv is faster and manages Python versions too, so
we lead with it — but pick whichever your team already uses.

---

## pip

The library-consumer path. A host repo adds DOS as a pinned dependency and points
it at its own tree (DOS resolves the workspace from `--workspace` ›
`$DISPATCH_WORKSPACE` › cwd, never its own install location):

```bash
# straight from the public repo — the default today (pre-PyPI; verified 2026-06-10):
pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"        # core kernel (PyYAML only)
pip install "dos-kernel[mcp] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"   # + the MCP server (dos-mcp)

# post-publish, the registry forms:
pip install dos-kernel                  # core kernel (PyYAML only)
pip install "dos-kernel[mcp]"           # + the MCP server (the dos-mcp command)
pip install "dos-kernel[tui]"           # + the live `dos top` / `dos decisions` screens
pip install "dos-kernel[mcp,tui]"       # several extras at once

# from a clone — editable, the contributor path:
git clone https://github.com/anthony-chaudhary/dos-kernel && cd dos-kernel
pip install -e .                        # editable: your edits are live
pip install -e ".[mcp]"                 # editable + an extra
```

A host pins it in its own `pyproject.toml` as `dos-kernel>=X.Y` (or `==X.Y.Z`;
pre-PyPI, pin the git URL: `dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git`);
dev installs are `pip install -e` against a clone.

### Extras

Pre-PyPI, every row below works straight from the public repo as
`pip install "dos-kernel[<extra>] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"`.

| Extra | Adds | Command |
|---|---|---|
| `mcp` | the MCP server (`dos-mcp`) — the syscalls as MCP tools | `pip install "dos-kernel[mcp]"` |
| `tui` | the auto-refreshing `dos top` / `dos decisions` screens (the plain-text floor needs no extra) | `pip install "dos-kernel[tui]"` |
| `notify-slack` | the Slack notification transport (a `dos.notifiers` driver) | `pip install "dos-kernel[notify-slack]"` |
| `export-otlp` | the OTLP verdict exporter (a `dos.exporters` driver) | `pip install "dos-kernel[export-otlp]"` |
| `dev` | the test/lint/type/property-test toolchain (contributors) | `pip install -e ".[dev]"` |
| `paper` | the arXiv LaTeX-cleaner used by the bundle script (build-time) | `pip install -e ".[paper]"` |

---

## One-command bootstrap from a clone

If you've cloned the repo and just want the `dos` command, the repo-local
wrappers do venv + editable install + PATH in one line. They are thin shells over
[`install.py`](../install.py): they find a Python 3.11+ and forward every flag.

```bash
# macOS / Linux / WSL / Git Bash:
./install.sh                  # venv + editable install + `dos` on PATH
./install.sh --extras mcp     # + the MCP server
./install.sh doctor           # read-only health check (resolved path + version)
./install.sh uninstall        # remove the venv + PATH shims
```

```powershell
# Windows PowerShell:
.\install.ps1                 # venv + editable install + `dos` on PATH
.\install.ps1 --extras mcp    # + the MCP server
.\install.ps1 doctor          # read-only health check
.\install.ps1 uninstall       # remove the venv + PATH shims

# If PowerShell refuses to run the script ("running scripts is disabled"),
# launch it once with a process-scoped bypass (changes NO machine policy):
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

> **These are not remote `curl | sh` installers.** There is nothing to download —
> you already have the source, and the script you're about to run is committed in
> the repo you cloned, so you can read it first. The wrappers run only
> `python install.py …` against the local tree; they fetch nothing from the
> network. (Contrast the uv bootstrap above, which *is* a remote installer for uv
> itself — review it with `… | less` if you want to see it before it runs.)

`install.py` also offers `--fresh` (rebuild the venv), `--system` / `--user`
(POSIX install scope), `--no-symlink` (confine to the venv), and `fix-shadowing`
(remove stale `dos` shims from other PATH dirs — the multi-worktree trap). Run
`python install.py --help` for the full set.

---

## WSL (Windows Subsystem for Linux)

DOS runs natively in WSL — it's just Linux there. Inside your WSL distro:

```bash
sudo apt install python3 python3-venv     # if not already present
git clone https://github.com/anthony-chaudhary/dos-kernel && cd dos-kernel
./install.sh                              # or: uv tool install .
dos doctor --workspace .
```

A note on the filesystem: keep the clone on the **Linux filesystem** (e.g.
`~/dos`), not under `/mnt/c/…`. A venv created on `/mnt/c` mixes Windows and Linux
interpreters and is slow; `install.py` detects WSL-on-`/mnt` and steers the venv
to `~/.local/share/dos/venv` to avoid that trap.

---

## Homebrew / WinGet / Scoop (planned)

OS package managers need a published formula/manifest, which lands after the
PyPI release. When they're live, the one-liners will be:

```bash
brew install dos-kernel                   # macOS / Linux (planned)
winget install dos-kernel                 # Windows (planned)
scoop install dos-kernel                  # Windows (planned)
```

Until then, use **uv**
(`uv tool install git+https://github.com/anthony-chaudhary/dos-kernel.git`) — it
gives the same "one command, isolated, on PATH, self-updating" experience on
every OS today, straight from the public repo.

---

## Claude Code plugin — hooks + MCP + skills

If you drive a fleet with **Claude Code**, the bundled plugin packages all three
runtime surfaces (the fail-safe hooks, the MCP server, the generic skill pack) in
one install:

```bash
# 1. Install the package FIRST (the plugin ships JSON + markdown; the brains ship
#    as the pip package the MCP server imports; straight from the public repo
#    while dos-kernel is pre-PyPI):
pip install "dos-kernel[mcp] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"

# 2. Then, inside Claude Code:
/plugin marketplace add anthony-chaudhary/dos-kernel
/plugin install dos-kernel@dos
```

Run **`/dos-kernel:dos-setup`** once after installing — it confirms the package
is importable and reports what the plugin wired. The same hooks are available à
la carte via `dos init --hooks claude-code` (and for Cursor / Codex / Gemini /
Antigravity). Details: [claude-plugin/README.md](../claude-plugin/README.md) and
[docs/221](221_the-cross-vendor-hook-installer.md).

---

## Verify the install

However you installed, confirm it with DOS itself — don't trust the installer's
say-so, ask the kernel:

```bash
dos doctor --workspace .        # reports the RESOLVED dos path + version + workspace facts
dos doctor | head -1            # → "DOS vX.Y.Z" (there is no `dos --version` flag)
```

`dos doctor` prints the **resolved** source path and version, not merely "the
command exists" — the honest signal when DOS is checked out in several worktrees
(a stale shim can otherwise point `dos` at the wrong tree). If `doctor` reports a
different path than you expect, run `python install.py fix-shadowing` to clear
stale shims, or `uv tool install --force dos-kernel` to repin.

## Upgrade

| Installed via | Upgrade with |
|---|---|
| `uv tool` | `uv tool upgrade dos-kernel` (git+ installs: re-run `uv tool install --force git+…`) |
| `pip` (from the public repo, pre-PyPI) | `pip install --upgrade --force-reinstall "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"` (same version on a moving `master` needs the force) |
| `pip` (from PyPI) | `pip install --upgrade dos-kernel` |
| `pip install -e .` (clone) | `git pull` (editable — the working tree *is* the install) |
| `install.sh` / `install.ps1` | `git pull && ./install.sh --fresh` |
| the Claude Code plugin | `/plugin marketplace update`, then re-install the package (`pip install --upgrade --force-reinstall "dos-kernel[mcp] @ git+…"` pre-PyPI; `pip install --upgrade "dos-kernel[mcp]"` after) |

## Uninstall

| Installed via | Remove with |
|---|---|
| `uv tool` | `uv tool uninstall dos-kernel` |
| `pip` | `pip uninstall dos-kernel` |
| `install.sh` / `install.ps1` | `./install.sh uninstall` (removes the venv + PATH shims) |
