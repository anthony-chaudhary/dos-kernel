#!/usr/bin/env python
"""build_plugin.py — regenerate the Claude Code plugin's bundled skills from source.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like the release scripts, this consumes the repo
> and is unaware to the kernel: nothing under `src/dos/` imports it. It exists to
> keep ONE fact true — that the skills shipped inside the Claude Code plugin
> (`claude-plugin/skills/`) are a faithful copy of the single-sourced generic skill
> pack (`src/dos/skills/`), never a hand-maintained fork that drifts.

Why a copy at all (the constraint that forces this)
===================================================

A Claude Code plugin is distributed as a self-contained directory; an installed
plugin is cloned standalone, so a component path that escapes the plugin root
(`../src/dos/skills`) is DROPPED after install, and a symlink whose target is
outside the marketplace is SKIPPED for security (plugins-reference.md, "Path
traversal limitations" / "Share files within a marketplace with symlinks"). So the
skills must PHYSICALLY live under `claude-plugin/skills/`. The generic pack is
single-sourced at `src/dos/skills/` (it ships as wheel package-data too), so the
honest move is: generate the plugin copy from that source, and pin the copy is
in sync with a test (`tests/test_plugin_manifest.py`).

What it does
============

  * Copies every generic SKP skill directory (`src/dos/skills/<name>/`, each holding
    a `SKILL.md` + any supporting files) into `claude-plugin/skills/<name>/`.
  * Copies the pack's shared `EXAMPLES.md` alongside them.
  * Writes the plugin-only onboarding skill (`dos-setup`) — the one skill that is
    NOT in the generic pack, because it is about adopting THIS plugin (checking the
    `dos-kernel` pip install), not a domain-free dispatch screenplay. Its content is
    authored here so the build is the single source for it too.
  * Removes any stale skill dir under `claude-plugin/skills/` that no longer has a
    source (so a renamed/removed skill doesn't linger in the plugin).

It is idempotent: running it twice produces byte-identical output. `--check` makes
no changes and exits non-zero if the plugin copy is out of sync — the mode the test
and a pre-release gate run.

Usage
=====

    python scripts/build_plugin.py            # regenerate claude-plugin/skills/
    python scripts/build_plugin.py --check    # verify in sync (exit 1 if not), write nothing
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """The repo top-level — git's answer, NOT __file__ relative math.

    The release scripts anchor on `git rev-parse --show-toplevel` for the same
    reason (CLAUDE.md): this script ships with the repo it builds, so the git
    top-level is the honest root even when invoked from a subdir.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # Fallback for a non-git checkout (a release tarball): this file is at
        # <root>/scripts/build_plugin.py.
        return Path(__file__).resolve().parents[1]


SRC_SKILLS_REL = Path("src") / "dos" / "skills"
PLUGIN_SKILLS_REL = Path("claude-plugin") / "skills"
PLUGIN_BIN_REL = Path("claude-plugin") / "bin"
PLUGIN_HOOKS_REL = Path("claude-plugin") / "hooks" / "hooks.json"

# The generic pack ships an EXAMPLES.md beside the per-skill dirs (not a skill
# itself). Copy it through so the plugin's skills can reference it in place.
SHARED_FILES = ("EXAMPLES.md",)

# ---------------------------------------------------------------------------
# Launcher reachability (the "present != reached" guard).
#
# A launcher under bin/ that hooks.json never invokes is dead code that LOOKS
# live — `test_launchers_are_tracked` proves it is PRESENT and TRACKED, but
# presence is not reachability (the docs/204 §3 presence-not-goal wall, turned
# inward on the plugin's own packaging). So we assert every TRACKED launcher
# script is either referenced by some hooks.json command OR is on this explicit
# allowlist of manual-integration helpers. The allowlist is DATA (not a parsed
# comment): a launcher is intentionally-unwired iff its basename is listed here.
#
#   * dos-hook.ps1 — the PowerShell launcher. hooks.json declares `shell: bash`,
#     which Claude Code runs through Git Bash even on Windows, so the POSIX
#     `dos-hook` already serves Windows. This .ps1 is for a hand-wired
#     `shell: powershell` hook; it is deliberately not referenced by the bundle.
_MANUAL_LAUNCHERS = frozenset({"dos-hook.ps1"})

# What makes a bin/ file a "launcher" (a script hooks.json could invoke) rather
# than a compiled per-arch binary: the launchers are the extensionless POSIX
# `dos-hook` and the `.ps1`. The per-arch `dos-hook-<os>-<arch>[.exe]` binaries
# are dispatched-TO by a launcher, never named directly by hooks.json, so they
# are not subject to the reachability rule (test_hook_binaries_bundled pins them).
def _is_launcher(name: str) -> bool:
    return name == "dos-hook" or name.endswith(".ps1")


# ---------------------------------------------------------------------------
# The plugin-only onboarding skill. NOT in the generic pack (src/dos/skills/)
# because it is about adopting THIS plugin — it names the `dos-kernel` pip package
# and the plugin's own surfaces — whereas the generic pack's litmus is "names no
# host." So it is authored HERE and written into the plugin copy by the build. It
# is intentionally a thin, read-only checklist: it verifies the prerequisite and
# points at the bundled surfaces; it wires nothing and mutates nothing.
# ---------------------------------------------------------------------------
DOS_SETUP_SKILL = """\
---
name: dos-setup
description: "One-time check that the DOS kernel plugin is ready to use — confirm the `dos-kernel` Python package is importable (the hooks and MCP server need it), report what the plugin bundled (hooks, the `dos` MCP tools, the generic skill pack), and point at the next skill to run. Use right after installing the dos-kernel plugin, or when `/mcp` shows the `dos` server failing to start or a `dos hook` command erroring. Read-only: it runs `dos doctor` to confirm wiring; it installs nothing and changes no config."
---

# dos-setup — confirm the plugin is wired, then point at the next move

> **The plugin ships JSON + markdown; the brains ship as a pip package.** The
> bundled hooks shell `python -m dos.cli …` and the bundled MCP server runs
> `python -m dos_mcp.server` — both need the `dos-kernel` package importable in the
> SAME Python that Claude Code launches. This skill verifies that one prerequisite
> and shows you what the plugin gave you. It is the only DOS skill that names the
> pip package; the rest are domain-free.

## Step 1 — Is the kernel importable? (the one prerequisite)

```bash
python -c "import dos, dos_mcp; print('dos', dos.__version__)"
```

- **Prints a version** → the package is installed and importable. Go to Step 2.
- **`ModuleNotFoundError: dos`** → the plugin's hooks/MCP cannot work yet. Install
  the package (the `[mcp]` extra pulls the MCP server framework the bundled server
  needs):

  ```bash
  pip install "dos-kernel[mcp]"
  ```

  Install it into the SAME interpreter Claude Code runs (the one `python` resolves
  to here). Then re-run Step 1. If `python` is the wrong interpreter, the hooks will
  silently no-op (they fail safe, by design) and the MCP server will print an
  install hint in `/mcp`.
- **`ModuleNotFoundError: dos_mcp`** → the core installed but not the MCP extra;
  re-run the `pip install "dos-kernel[mcp]"` line above.

## Step 2 — Does the kernel see this workspace? (confirm wiring)

```bash
python -m dos.cli doctor --workspace . --json
```

A JSON object with `lanes`, `paths`, and `stamp` means the kernel is reading THIS
repo. Skim it once — those values are the layout the bundled skills and the MCP
`dos_arbitrate` / `dos_verify` tools read instead of hardcoding anything. If this
repo has no `dos.toml`, the lanes are auto-derived from your top-level directories
and everything still works (the generic default).

## Step 3 — See it work end to end (60 seconds, throwaway repo)

Before reading the skills, watch the kernel make a real call. This runs in a
disposable git repo and cleans up after itself:

```bash
python -m dos.cli quickstart --driver workshop
```

It scaffolds a repo under the **`workshop` reference driver** (a worked
host-policy pack with two concurrent lanes + one exclusive release lane), then
shows BOTH halves of DOS against real artifacts:

- **the truth syscall** — `AUTH1` was committed → SHIPPED (exit 0); `AUTH2` was
  only claimed → NOT_SHIPPED (exit 1). The verdict comes from git, not the agent.
- **the admission kernel** — `frontend` and `backend` acquire *concurrently*
  (their file trees are disjoint), while `release` *refuses* (it is exclusive and
  runs alone). This is the concurrency policy your own lanes will get.

`workshop` is the **copy-me template** for declaring your own lanes — see Step 5.
(The plain `dos quickstart`, no `--driver`, shows just the truth-syscall half.)

## Step 4 — What the plugin bundled (so you know what you have)

- **Hooks** (active on every Claude Code launch in this project, all fail-safe —
  they emit nothing and exit 0 if anything goes wrong, so they never break a turn):
  - **PreToolUse** → `dos hook pretool`: can DENY a structurally-refused call before
    it runs (e.g. a self-modify of the kernel's own path). Advisory by default —
    a behavioral deny needs a ruling handler wired; out of the box it only observes.
  - **PostToolUse** → `dos hook posttool`: re-surfaces a stalled tool stream
    (a read-loop spinning on the same file) as advisory context.
  - **Stop** → `dos hook stop`: refuses to stop on an unverified claim, so the loop
    does not close on "I'm done" the git history does not back.

  Every one of those calls is **counted and logged**: the bundled native `dos-hook`
  binary appends one record per hook call to `.dos/metrics/observations.jsonl`, so the
  kernel emits evidence about its OWN behavior (docs/276). Read it back any time with
  `/dos-kernel:dos-stats` (or `"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace .`)
  — counts by verb/outcome, the delegate + stop-block rates, and per-verb latency.
- **MCP tools** (the `dos` server — check `/mcp` shows it connected): `dos_verify`
  (did a claim actually ship, from git not self-report), `dos_arbitrate` (may two
  workers run concurrently without colliding), `dos_commit_audit`, `dos_refuse_reasons`
  / `dos_check_reason`, `dos_status`, `dos_recall`, `dos_doctor`.
- **The generic skill pack** — the domain-free dispatch screenplays, namespaced
  under this plugin. The usual first one to read is `/dos-kernel:dos-next-up` (snapshot
  the portfolio) or `/dos-kernel:dos-dispatch` (plan-and-ship one lane safely). And
  `/dos-kernel:dos-stats` shows the kernel's own activity on this project.

## Step 5 — Adopt DOS in this repo (optional, makes it yours)

The plugin works against any git repo with zero config. To make the layout explicit
and editable (a `dos.toml` with your lanes, and the skills as local files you can
tune), scaffold it:

```bash
python -m dos.cli init .                    # lanes auto-derived from your top-level dirs
python -m dos.cli init . --example workshop  # OR start from the worked reference taxonomy
```

The bare form derives one concurrent lane per top-level source dir. The
`--example workshop` form scaffolds the same lanes the Step-3 demo used (two
concurrent clusters + an exclusive release lane) for you to rename to your real
concurrency topics. Both are optional — the bundled surfaces already work without
any `dos.toml`.

## What this skill deliberately does NOT do

- **It installs nothing.** It only TELLS you the pip command if the import fails;
  it never runs `pip` for you (that would mutate your environment without asking,
  and the target interpreter is ambiguous). You run the install.
- **It wires nothing.** The hooks and MCP server are already wired BY the plugin;
  this skill confirms them, it does not re-install or edit any config file.
- **It decides no ground truth.** Like every DOS skill, it shells `dos` verbs and
  reads the verdict; the kernel decides, the skill reports.
"""


# ---------------------------------------------------------------------------
# The plugin-only observability skill. ALSO authored here (not in the generic
# pack) because it surfaces the PLUGIN's own bundled binary: it names
# `${CLAUDE_PLUGIN_ROOT}/bin/dos-hook stats`, a `dos-hook` (Go) verb the plugin
# ships, NOT a domain-free `dos` CLI verb — so the generic "names no host" litmus
# does not apply to it (the same exemption as dos-setup). It is read-only: it folds
# the observation log the bundled hooks already write; it takes no lease, launches
# nothing, mutates no state. docs/276 is the design.
# ---------------------------------------------------------------------------
DOS_STATS_SKILL = """\
---
name: dos-stats
description: "Show what the bundled DOS hook binary has been doing — fold its per-call observation log into an at-a-glance report (how many tool calls it adjudicated, how many it DENIED / WARNED / passed through, which reason classes fired, how often verify-on-stop blocked a false \\"done\\", the wait-marker budget, and per-verb latency). Use when you want to see the trust substrate's OWN activity on this project, confirm the native fast-path is actually serving calls (not silently delegating to Python), or check how fast the hooks run. Read-only: it folds a log the hooks already wrote; it takes no lease, launches nothing, and changes nothing."
---

# dos-stats — read the kernel's own activity log

> **The DOS hooks judge your agents from non-forgeable evidence. This skill turns
> that lens on the kernel itself.** Every time a hook fires (before a tool call, after
> a read, on a stop), the bundled native `dos-hook` binary writes ONE line to a
> durable log describing what it decided. This skill folds that log into a report you
> read. It is the binary's own dogfood: the kernel emits evidence about its own
> adjudication, the same way it demands evidence from the agents it judges (docs/276).

## What writes the log (so the numbers mean something)

The plugin's hooks call the bundled `dos-hook` binary on every tool call. On each
call the binary appends one record to `.dos/metrics/observations.jsonl` under this
workspace — the verb it ran, the decision it reached, the reason class, the dialect,
the exit code, how long it took. The log is **append-only and local**; nothing reads
it back until you run this. It is on by default and opt-OUT with `DOS_HOOK_METRICS=0`.

## Step 1 — Fold the log into a report

The binary lives in the plugin bundle, so call it by its bundle path (the same
launcher the hooks use picks the right binary for your machine):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace .
```

A populated run looks like this:

```
dos-hook stats — /your/project
  observations   4
  tool calls     3 adjudicated — 2 passed untouched (66.7%), 1 intervened (33.3%)
  by verb       posttool=1  pretool=3
  by outcome    deny=1  passthrough=3
  by exit code  0=3  2=1
  pretool rung  admission=1  none=1  provenance=1
  dialect       claude-code=3
  stream state  ADVANCING=1
  delegates     0  (native declined → Python)
  stop blocks   0  (false-done refusals)
  latency (ms)  verb            n     mean    p50    p95    max
                posttool          1    2.29   2.29   2.29   2.29
                pretool           3    5.32   5.32   5.42   5.42
```

**If it says `(no observations yet …)`** that is normal and correct — the log is
empty until the hooks have fired at least once. Make any tool call in this project
(read a file, run a command) and re-run; the count climbs.

## Step 2 — Read the lines that matter

- **observations / by verb** — how many calls the kernel adjudicated, split by hook
  (`pretool` = before a tool runs, `posttool` = after a read, `stop` = on a stop,
  `marker` = the keep-alive budget).
- **tool calls** — the intervention RATE: of every tool call adjudicated (one
  `pretool` record each), what share passed untouched vs was warned or denied. This
  is the "is the substrate a light touch or a nanny?" number — numerator and
  denominator come from the same log, so the percent cannot be argued with. A
  delegated call is counted in neither (Python decided it; see **delegates**).
- **by outcome** — `passthrough` (let it run), `deny` (refused a structurally-unsafe
  call, e.g. a self-modify of the kernel's own path), `warn` (advisory re-surface),
  `block` (a stop refused on an unverified claim).
- **delegates** — how often the fast native path **declined and fell back to Python**.
  The native binary exists to erase that cold-start, so a high delegate count is the
  one number you want LOW; `0` means the fast path is owning every call.
- **stop blocks** — how many times verify-on-stop **refused to let the loop close on a
  "done" the git history did not back**. This is the headline safety number.
- **reason class** — which refusals fired (`SELF_MODIFY`, a lane collision, …), when
  the kernel denied or warned.
- **latency (ms)** — per-verb wall time. The native path's whole point is sub-30 ms
  (the docs/270 claim) — here it is, measured live and continuously on your machine.
- **⚠ panics** — only appears if the fail-safe fired (a Go crash that recovered to a
  no-op so it never broke your turn). Non-zero here is worth a bug report.

## Step 3 — Narrow the window or get the machine form (optional)

```bash
# only the last hour (or 30m, 15m, …) — the clock lives at this read-only boundary:
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace . --since 1h

# the same aggregate as a JSON object, to pipe into jq or a dashboard:
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace . --json
```

`--since` with an unreadable value (or omitted) folds everything; it never errors —
a read-only surface degrades to "show what we have."

## What this skill deliberately does NOT do

- **It changes nothing.** It is a pure fold over a log the hooks already wrote — no
  lease, no launch, no mutation. It does not even add to the log it reads (the `stats`
  verb is the one verb that is NOT itself recorded, so it cannot count its own reads).
- **It does not decide ground truth.** It reports counts; the kernel made the
  decisions those counts summarize, at the moment each hook fired.
- **It is not a server.** A hook is a one-shot process, so there is nothing to scrape;
  the durable per-call log IS the cross-process surface, and this verb folds it on
  demand. To watch it live, re-run it (or wrap it in `/loop`).
"""


def _iter_source_skill_dirs(src_skills: Path) -> list[Path]:
    """Every generic-pack skill directory: a subdir of src/dos/skills holding a SKILL.md."""
    if not src_skills.is_dir():
        return []
    return sorted(
        d for d in src_skills.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def _planned_layout(root: Path) -> tuple[dict[Path, Path], dict[Path, str]]:
    """Compute the desired plugin/skills/ contents from source.

    Returns:
      copies  — {dest_file -> src_file} for every file copied verbatim from source
                (each source skill dir's files + the shared EXAMPLES.md).
      written — {dest_file -> text} for files this script AUTHORS (the plugin-only
                skills: dos-setup onboarding + dos-stats observability).
    """
    src_skills = root / SRC_SKILLS_REL
    dst_skills = root / PLUGIN_SKILLS_REL

    copies: dict[Path, Path] = {}
    for skill_dir in _iter_source_skill_dirs(src_skills):
        for src_file in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
            rel = src_file.relative_to(src_skills)
            copies[dst_skills / rel] = src_file

    for shared in SHARED_FILES:
        src_file = src_skills / shared
        if src_file.is_file():
            copies[dst_skills / shared] = src_file

    written: dict[Path, str] = {
        dst_skills / "dos-setup" / "SKILL.md": DOS_SETUP_SKILL,
        dst_skills / "dos-stats" / "SKILL.md": DOS_STATS_SKILL,
    }
    return copies, written


def _desired_files(copies: dict[Path, Path], written: dict[Path, str]) -> set[Path]:
    return set(copies) | set(written)


def _existing_files(dst_skills: Path) -> set[Path]:
    if not dst_skills.is_dir():
        return set()
    return {p for p in dst_skills.rglob("*") if p.is_file()}


def _tracked_bin_launchers(root: Path) -> list[str]:
    """The git-tracked launcher scripts under claude-plugin/bin/ (basenames).

    Tracked, because a fresh marketplace clone only ships tracked files — an
    untracked launcher in a dirty tree is not part of the bundle. Falls back to
    a working-tree scan if git is unavailable (a release tarball)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", PLUGIN_BIN_REL.as_posix()],
            cwd=str(root), capture_output=True, text=True, check=True,
        )
        names = [Path(line.strip()).name for line in out.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        bin_dir = root / PLUGIN_BIN_REL
        names = [p.name for p in bin_dir.iterdir() if p.is_file()] if bin_dir.is_dir() else []
    return sorted(n for n in names if _is_launcher(n))


def _hooks_command_text(root: Path) -> str:
    """All hooks.json command strings concatenated — the surface a launcher is
    'reached' from. Returns '' if the manifest is absent or unparseable (then no
    launcher is reachable, which the reachability check will flag — fail-loud)."""
    hooks_path = root / PLUGIN_HOOKS_REL
    if not hooks_path.is_file():
        return ""
    try:
        obj = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    parts: list[str] = []
    for event_groups in obj.get("hooks", {}).values():
        for group in event_groups:
            for h in group.get("hooks", []):
                c = h.get("command")
                if isinstance(c, str):
                    parts.append(c)
    return "\n".join(parts)


def check_launcher_reachability(root: Path) -> list[str]:
    """Every tracked bin/ launcher is either referenced by hooks.json or on the
    manual-integration allowlist. Catches a launcher that is shipped + tracked
    (so it LOOKS live) but no hook event ever invokes it — present != reached."""
    commands = _hooks_command_text(root)
    findings: list[str] = []
    for launcher in _tracked_bin_launchers(root):
        if launcher in _MANUAL_LAUNCHERS:
            continue  # intentionally unwired (allowlisted as a manual helper)
        # Referenced iff its basename appears in some hooks.json command string.
        if launcher not in commands:
            findings.append(
                f"unreachable launcher: claude-plugin/bin/{launcher} is tracked "
                f"but no hooks.json command invokes it (add it to a hook command "
                f"or to _MANUAL_LAUNCHERS in scripts/build_plugin.py)")
    return findings


def check(root: Path) -> list[str]:
    """Return a list of human-readable drift findings; empty list = in sync."""
    dst_skills = root / PLUGIN_SKILLS_REL
    copies, written = _planned_layout(root)
    desired = _desired_files(copies, written)
    existing = _existing_files(dst_skills)

    findings: list[str] = []
    for dest in sorted(desired - existing):
        findings.append(f"missing in plugin: {dest.relative_to(root)}")
    for dest in sorted(existing - desired):
        findings.append(f"stale in plugin (no source): {dest.relative_to(root)}")
    for dest, src in sorted(copies.items()):
        if dest.is_file() and not filecmp.cmp(src, dest, shallow=False):
            findings.append(f"out of date: {dest.relative_to(root)} "
                            f"differs from {src.relative_to(root)}")
    for dest, text in sorted(written.items()):
        if dest.is_file() and dest.read_text(encoding="utf-8") != text:
            findings.append(f"out of date: {dest.relative_to(root)} "
                            f"differs from the authored content")
    findings.extend(check_launcher_reachability(root))
    return findings


def build(root: Path) -> tuple[int, int, int]:
    """Regenerate claude-plugin/skills/ from source. Returns (copied, written, removed)."""
    dst_skills = root / PLUGIN_SKILLS_REL
    copies, written = _planned_layout(root)
    desired = _desired_files(copies, written)

    # Remove stale files first (a renamed/removed skill must not linger).
    removed = 0
    for dest in sorted(_existing_files(dst_skills) - desired):
        dest.unlink()
        removed += 1

    copied = 0
    for dest, src in sorted(copies.items()):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not (dest.is_file() and filecmp.cmp(src, dest, shallow=False)):
            shutil.copy2(src, dest)
        copied += 1

    wrote = 0
    for dest, text in sorted(written.items()):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not (dest.is_file() and dest.read_text(encoding="utf-8") == text):
            dest.write_text(text, encoding="utf-8")
        wrote += 1

    # Prune now-empty directories left behind by removals.
    if dst_skills.is_dir():
        for d in sorted((p for p in dst_skills.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

    return copied, wrote, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify the plugin skills are in sync; write nothing, "
                             "exit 1 if out of sync")
    args = parser.parse_args(argv)
    root = _repo_root()

    if args.check:
        findings = check(root)
        if findings:
            print(f"plugin skills OUT OF SYNC ({len(findings)} finding(s)):")
            for f in findings:
                print(f"  - {f}")
            print("\nrun: python scripts/build_plugin.py")
            return 1
        print("plugin skills in sync with src/dos/skills/")
        return 0

    copied, wrote, removed = build(root)
    print(f"plugin skills regenerated: {copied} copied, {wrote} authored, "
          f"{removed} stale removed -> {PLUGIN_SKILLS_REL.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
