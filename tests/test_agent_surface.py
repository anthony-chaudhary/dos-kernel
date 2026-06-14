"""The agent-surface litmus tier — the docs/290 defect classes pinned in the suite.

The cold-clone agent-view A/B (`docs/reports/2026-06-10_agent-view-ab.md`) asked
"does this repo's agent-facing surface actually serve a stranger's agent?" and
found seven defect classes (D1–D8). What that experiment TAUGHT is deterministic:
each defect reduces to a checkable invariant of the repo's bytes. This module is
the always-on ORACLE-rung tier of docs/290 Phase 1 — one litmus per defect class,
in the genre of `test_skill_pack_*.py` / `test_install_drift.py`: grep +
ground-truth assertions, no network, no agent in the loop.

The single-sourcing rule (what keeps these litmuses from rotting): every
assertion resolves against the artifact that OWNS the fact, never a hand-copied
literal — the extras that provide pytest come from `pyproject.toml` through
`scripts/install_facts.py`'s own reader; the blocking lint argv comes from
parsing `.github/workflows/ci.yml`; the consumer verbs are resolved in the real
CLI parser (`dos.cli.build_parser()`); the suite size is the count pytest
actually collects. When a doc and its ground truth drift apart, the litmus
follows the ground truth and reddens on the doc.

The tier:

  AV1 (pins D1) — every `hooks` command in the committed `.claude/settings.json`
        is cold-machine safe (degrades to exit 0 when `dos` is absent, the
        `|| true` shape), and `.claude/settings.local.json` is gitignored +
        untracked. A cold machine must have nothing that errors on it; a hook
        that no-ops when `dos` is missing is safe to ship (the dogfood goal-gate,
        issue #18).
  AV2 (pins D2) — any fenced block in AGENTS.md / CLAUDE.md that runs pytest is
        preceded, in the same block, by an install line whose extras actually
        provide pytest (a bare `-e .` is PyYAML-only — the exact cold failure).
  AV3 (pins D3) — the lint command AGENTS.md documents equals the BLOCKING lint
        CI runs (argv-compared against the workflow YAML, never prose-compared).
  AV4 (pins D5) — the "When the user asks you ABOUT DOS" table exists, CLAUDE.md
        points at it, every `dos <verb>` move it names resolves in the CLI
        parser, and every file it links exists.
  AV5 (pins D6) — the documented suite size (~N tests) is within ±15% of the
        collected count. A hand-kept number rots; a banded number is honest.
  AV6 (pins D8) — running the suite modifies no tracked file. The enforcement is
        the session-scoped autouse guard in `tests/conftest.py`
        (`_suite_is_effect_free_on_tracked_files`), so it arms EVERY pytest
        session; the tests here pin the pure delta classifier and that the
        guard is actually registered and armed.

D4 and D7 already have their own pinning tests (the hermes bash-resolution skip
probe; `test_vendor_agnostic_kernel.py`) and are deliberately not duplicated.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import conftest as suite_conftest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two files an agent host auto-loads / reads first — the agent surface the
# A/B measured. (README is a human surface; its install claims are already
# leashed by test_install_drift.py.)
AGENT_DOCS = ("AGENTS.md", "CLAUDE.md")


def _text(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"agent-surface file is missing: {rel}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fenced-block parsing — the docs' COMMANDS are the surface under test, so the
# checks below read code blocks, never prose
# ---------------------------------------------------------------------------


def _fenced_blocks(text: str) -> list[list[str]]:
    """The fenced ``` blocks of a markdown file, as lists of raw lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if in_block:
                blocks.append(current)
                current = []
            in_block = not in_block
            continue
        if in_block:
            current.append(line)
    return blocks


def _command(line: str) -> str:
    """A fenced-block line reduced to its command: inline `# …` comment stripped."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return ""
    return re.split(r"\s+#", stripped, maxsplit=1)[0].strip()


def _is_install_line(cmd: str) -> bool:
    tokens = cmd.split()
    return "install" in tokens and bool({"pip", "pipx", "uv"}.intersection(tokens))


# `pip install -e ".[dev,mcp]"` / `dos-kernel[mcp]` / `uv tool install ".[mcp]"` —
# group 1 is the extras list inside the brackets (same anchor discipline as
# test_install_drift.py: never sweep up an unrelated [...] in prose).
_EXTRA_BRACKET = re.compile(r'(?:dos-kernel|"?\.)\[([a-z0-9,\s-]+)\]')


def _install_extras(cmd: str) -> set[str]:
    extras: set[str] = set()
    for blob in _EXTRA_BRACKET.findall(cmd):
        extras.update(part.strip() for part in blob.split(",") if part.strip())
    return extras


def _is_pytest_invocation(cmd: str) -> bool:
    if not cmd or _is_install_line(cmd):
        return False
    return "pytest" in cmd.split()


# ---------------------------------------------------------------------------
# ground-truth readers
# ---------------------------------------------------------------------------


def _install_facts_module():
    """Import `scripts/install_facts.py` by path (scripts/ is not a package).

    Same loader discipline as `tests/test_install_drift.py`: register the module
    in `sys.modules` before exec so its frozen dataclass can resolve its own
    `__module__`; reuse an already-loaded instance so the two test modules share
    one read.
    """
    if "install_facts" in sys.modules:
        return sys.modules["install_facts"]
    spec = importlib.util.spec_from_file_location(
        "install_facts", REPO_ROOT / "scripts" / "install_facts.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["install_facts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _extras_providing(package: str) -> set[str]:
    """The pyproject extras whose dependency list contains `package`.

    Resolved through `install_facts._project_table` — the one place that knows
    where the install facts live in the TOML (docs/290: extend that single
    source rather than re-deriving pyproject's layout here).
    """
    table = _install_facts_module()._project_table()
    providers: set[str] = set()
    for extra, deps in table.get("optional-dependencies", {}).items():
        for dep in deps:
            name = re.split(r"[\s<>=!~\[;(]", dep, maxsplit=1)[0].lower()
            if name == package:
                providers.add(extra)
    return providers


# ---------------------------------------------------------------------------
# AV1 / D1 — the committed Claude Code settings must be cold-machine safe
# ---------------------------------------------------------------------------


def _claude_hook_commands(data: dict) -> list[str]:
    """Every shell command string under a Claude-Code `hooks` config.

    Claude Code nests entries two deep: `hooks[event] -> [matcher-group] ->
    group["hooks"] -> [entry]`, each entry an `{type, command, …}`. We flatten
    to the command strings so AV1 can check each for cold-safety.
    """
    out: list[str] = []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get("hooks", []) if isinstance(group, dict) else []
            for entry in entries:
                cmd = entry.get("command", "") if isinstance(entry, dict) else ""
                if isinstance(cmd, str) and cmd.strip():
                    out.append(cmd.strip())
    return out


def _is_cold_safe(command: str) -> bool:
    """A committed hook command is cold-safe iff it cannot error on a machine
    where `dos` is not importable — i.e. it swallows the missing-binary failure
    and exits 0. The canonical shape is a trailing `|| true` (so a `command
    not found` / non-zero `dos` exit becomes a clean exit 0). This is the D1
    floor expressed as a property of the command, not a ban on hooks.
    """
    return command.endswith("|| true") or command.endswith("|| exit 0")


def test_av1_committed_settings_hooks_are_cold_safe():
    """D1: the committed `.claude/settings.json` ships to every clone, so any
    `hooks` it carries must be cold-machine safe — every command must exit 0
    when `dos` is absent. A bare `dos hook …` (which 127s on a cold clone, and
    whose `python -m dos.cli` fallback ModuleNotFoundErrors) is the D1 defect;
    a `dos hook … || true` form is allowed because it degrades to a no-op.
    The maintainer's non-cold-safe rig still belongs in the gitignored
    `.claude/settings.local.json`.
    """
    data = json.loads(_text(".claude/settings.json"))
    commands = _claude_hook_commands(data)
    not_cold_safe = [c for c in commands if not _is_cold_safe(c)]
    assert not not_cold_safe, (
        ".claude/settings.json (committed — it ships to every clone) carries "
        f"hook command(s) that are NOT cold-safe: {not_cold_safe}. That is the "
        "D1 defect: on a cold machine without `dos` importable they error on "
        "every Stop. End each committed hook command with `|| true` (degrades "
        "to a no-op when `dos` is absent) or move the rig to the gitignored "
        ".claude/settings.local.json."
    )


def test_av1_local_settings_are_gitignored_and_untracked():
    rel = ".claude/settings.local.json"
    check = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", rel],
        capture_output=True,
    )
    assert check.returncode == 0, (
        f"{rel} is not matched by any .gitignore rule — the maintainer's "
        "machine-local rig could be committed by a blanket `git add` and ship "
        "the D1 defect to every clone."
    )
    ls = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", rel],
        capture_output=True,
        text=True,
    )
    assert not ls.stdout.strip(), (
        f"{rel} is TRACKED — the personal rig ships to every clone (D1)."
    )


# ---------------------------------------------------------------------------
# AV2 / D2 — a documented pytest run must be preceded by an install that
# actually provides pytest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", AGENT_DOCS)
def test_av2_every_pytest_block_installs_pytest_first(rel):
    providers = _extras_providing("pytest")
    assert providers, (
        "pyproject.toml declares no extra that provides pytest — the suite "
        "command cannot be documented honestly anywhere."
    )
    pytest_blocks = 0
    for block in _fenced_blocks(_text(rel)):
        commands = [_command(line) for line in block]
        pytest_at = [i for i, c in enumerate(commands) if _is_pytest_invocation(c)]
        if not pytest_at:
            continue
        pytest_blocks += 1
        provided = any(
            _is_install_line(c) and _install_extras(c) & providers
            for c in commands[: pytest_at[0]]
        )
        assert provided, (
            f"{rel}: a fenced block runs pytest without first installing an "
            f"extra that provides it (need one of {sorted(providers)}; a bare "
            "`pip install -e .` is PyYAML-only — the D2 cold failure: "
            "`No module named pytest`). The block:\n" + "\n".join(block)
        )
    assert pytest_blocks, (
        f"{rel} no longer documents the suite command in any fenced block — "
        "the D2 fix is that block; an agent with no documented command "
        "improvises one."
    )


# ---------------------------------------------------------------------------
# AV3 / D3 — the documented lint command IS the CI-blocking lint command
# ---------------------------------------------------------------------------


def test_av3_documented_lint_equals_ci_blocking_lint():
    ci = yaml.safe_load(_text(".github/workflows/ci.yml"))
    blocking: list[list[str]] = []
    for job in ci.get("jobs", {}).values():
        for step in job.get("steps", ()):
            run = step.get("run") or ""
            if "ruff" not in run or step.get("continue-on-error"):
                continue
            for line in run.splitlines():
                line = line.strip()
                if line.startswith("ruff "):
                    blocking.append(shlex.split(line))
    assert len(blocking) == 1, (
        f"expected exactly one blocking ruff command in ci.yml, found "
        f"{blocking} — CI's lint surface changed shape; re-aim this litmus."
    )
    documented = [
        shlex.split(cmd)
        for block in _fenced_blocks(_text("AGENTS.md"))
        for cmd in (_command(line) for line in block)
        if cmd.startswith("ruff ")
    ]
    assert documented, (
        "AGENTS.md no longer documents a lint command — the D3 fix told agents "
        "the CI-exact line so they stop 'fixing' the deliberately-unclean "
        "wider tree."
    )
    for argv in documented:
        assert argv == blocking[0], (
            f"AGENTS.md documents {shlex.join(argv)!r} but CI's blocking lint "
            f"is {shlex.join(blocking[0])!r} — the D3 defect: a cold agent runs "
            "the documented line, sees hundreds of findings, and concludes the "
            "tree is dirty (or starts 'fixing' it)."
        )


# ---------------------------------------------------------------------------
# AV4 / D5 — the consumer-moves table exists, is pointed at, and every move
# it names is live
# ---------------------------------------------------------------------------

_CONSUMER_TABLE_HEADING = "When the user asks you ABOUT DOS"

# `dos <verb> [--flag …]` as the consumer table writes a move. The verb must
# resolve in the real parser; any --flag written right after it must be an
# option that verb actually accepts.
_DOS_MOVE = re.compile(r"\bdos\s+([a-z][a-z0-9-]*)((?:\s+--[a-z][a-z0-9-]*)*)")


def _consumer_table_section(agents_text: str) -> str:
    lines = agents_text.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line.startswith("##") and _CONSUMER_TABLE_HEADING in line
        ),
        None,
    )
    assert start is not None, (
        f"AGENTS.md lost its '## {_CONSUMER_TABLE_HEADING}' table — the D5 fix. "
        "Without it a cold agent re-derives the consumer moves from the long "
        "README and recommends the unpublished PyPI install again."
    )
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_av4_consumer_table_exists_and_claude_md_points_at_it():
    section = _consumer_table_section(_text("AGENTS.md"))
    assert any(line.lstrip().startswith("|") for line in section.splitlines()), (
        "the consumer-moves section carries no table rows"
    )
    claude = _text("CLAUDE.md")
    pos = claude.find(_CONSUMER_TABLE_HEADING)
    assert pos >= 0, (
        "CLAUDE.md lost the pointer to the consumer-moves table — the "
        "auto-loaded context carrying zero consumer content was the D5 root "
        "cause."
    )
    assert "AGENTS.md" in claude[pos : pos + 300], (
        "CLAUDE.md names the consumer table but no longer routes to AGENTS.md "
        "next to it — the pointer must say where the table lives."
    )


def test_av4_every_consumer_move_resolves_in_the_cli_parser():
    from dos.cli import build_parser

    section = _consumer_table_section(_text("AGENTS.md"))
    moves = _DOS_MOVE.findall(section)
    assert moves, (
        "no `dos <verb>` moves found in the consumer table — the table or the "
        "extraction regex changed shape."
    )
    parser = build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    for verb, flags in moves:
        assert verb in sub.choices, (
            f"the consumer table tells agents to run `dos {verb}`, but the CLI "
            "parser has no such command — a documented move must be live (D5)."
        )
        options = {
            opt
            for action in sub.choices[verb]._actions
            for opt in action.option_strings
        }
        for flag in flags.split():
            assert flag in options, (
                f"the consumer table documents `dos {verb} {flag}`, but "
                f"`{verb}` accepts no {flag} flag — a documented move must be "
                "live (D5)."
            )


def test_av4_every_file_the_consumer_table_links_exists():
    section = _consumer_table_section(_text("AGENTS.md"))
    targets = [
        target
        for target in re.findall(r"\[[^\]]+\]\(([^)\s#]+)\)", section)
        if not target.startswith(("http://", "https://"))
    ]
    assert targets, (
        "the consumer table links no files — the table or the extraction "
        "changed shape."
    )
    for target in targets:
        assert (REPO_ROOT / target).is_file(), (
            f"the consumer table links {target}, which does not exist — a "
            "documented pointer must be live (D5)."
        )


# ---------------------------------------------------------------------------
# AV5 / D6 — the documented suite size tracks the collected count
# ---------------------------------------------------------------------------

_DOC_SUITE_SIZE = re.compile(r"~\s*([\d,]+)\s+tests")
_SUITE_SIZE_BAND = 0.15


def _is_full_suite_run(config) -> bool:
    """Did this invocation select the whole suite (so its own collection count
    IS the suite size)? A path/nodeid arg narrower than the repo or tests/
    root, or a -k/-m filter, means partial."""
    if config.getoption("keyword", default="") or config.getoption("markexpr", default=""):
        return False
    invocation_dir = Path(str(config.invocation_params.dir))
    for raw in config.invocation_params.args:
        if raw.startswith("-"):
            continue
        candidate = Path(raw.split("::", 1)[0])
        if not candidate.is_absolute():
            candidate = invocation_dir / candidate
        if not candidate.exists():
            continue  # an option VALUE (e.g. `-p no:x`), not a selection
        if candidate.resolve() not in (REPO_ROOT, REPO_ROOT / "tests"):
            return False
    return True


@pytest.fixture(scope="session")
def collected_suite_size(request):
    """The number of tests pytest collects for the FULL suite, derived once.

    In a full run the number is free: `tests/conftest.py` records
    `len(session.items)` at collection-finish, so CI pays nothing. A partial
    run (a path / -k / -m selection — e.g. the docs/290 red/green probes)
    cannot use its own collection as the suite size, so it pays one
    `--collect-only -q` subprocess (~6s) instead.
    """
    recorded = getattr(request.config, "_dos_collected_count", None)
    if recorded is not None and _is_full_suite_run(request.config):
        return recorded
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=600,
    )
    for line in reversed(proc.stdout.splitlines()):
        match = re.search(r"(\d+)\s+tests?\s+collected", line)
        if match:
            return int(match.group(1))
    pytest.fail(
        "could not derive the suite size from `pytest --collect-only -q` — "
        "tail:\n"
        + "\n".join(proc.stdout.splitlines()[-15:])
        + "\n"
        + proc.stderr[-2000:]
    )


def test_av5_documented_suite_size_is_in_band(collected_suite_size):
    claims = [
        (rel, int(match.group(1).replace(",", "")))
        for rel in AGENT_DOCS
        for match in _DOC_SUITE_SIZE.finditer(_text(rel))
    ]
    assert claims, (
        "neither AGENTS.md nor CLAUDE.md documents the suite size (~N tests) — "
        "the D6 expectation-setting fix is gone (an agent with no stated "
        "runtime backgrounds the suite and ends on a promise)."
    )
    low = (1 - _SUITE_SIZE_BAND) * collected_suite_size
    high = (1 + _SUITE_SIZE_BAND) * collected_suite_size
    for rel, documented in claims:
        assert low <= documented <= high, (
            f"{rel} documents ~{documented:,} tests but the suite collects "
            f"{collected_suite_size:,} (±15% band: {low:,.0f}–{high:,.0f}) — "
            "the hand-kept number rotted; update the doc sentence."
        )


# ---------------------------------------------------------------------------
# AV6 / D8 — the suite is effect-free on tracked files. The ENFORCEMENT is the
# session-scoped autouse guard in tests/conftest.py (it must arm every
# session, including ones that never collect this module); here we pin the
# pure classifier and that the guard is actually registered.
# ---------------------------------------------------------------------------


def test_av6_delta_classifier_excludes_untracked_and_preexisting_dirt():
    before = suite_conftest._modified_tracked_paths(
        " M src/dos/cli.py\n?? notes.txt\n"
    )
    after = suite_conftest._modified_tracked_paths(
        " M src/dos/cli.py\n"  # pre-existing hot-tree dirt — not the suite's doing
        " M go/internal/hook/parity/corpus.jsonl\n"  # the D8 shape: a regen rewrote a corpus
        "?? more-notes.txt\n"  # untracked scratch — out of AV6 scope
        "!! .venv\n"  # ignored — out of AV6 scope
    )
    assert sorted(after - before) == ["go/internal/hook/parity/corpus.jsonl"]
    assert suite_conftest._modified_tracked_paths("") == frozenset()


def test_av6_session_guard_is_armed_for_every_session():
    fixture_fn = suite_conftest._suite_is_effect_free_on_tracked_files
    marker = getattr(fixture_fn, "_fixture_function_marker", None) or getattr(
        fixture_fn, "_pytestfixturefunction", None
    )
    assert marker is not None, (
        "the AV6 guard in tests/conftest.py is no longer a pytest fixture"
    )
    assert marker.scope == "session", (
        "the AV6 guard must be session-scoped — snapshot before the first "
        "test, compare after the last."
    )
    assert marker.autouse, (
        "the AV6 guard must be autouse — a guard nobody requests guards "
        "nothing."
    )
