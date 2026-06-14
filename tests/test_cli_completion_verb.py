"""`dos completion {bash,zsh,fish}` + `dos start-here` — the daily-CLI DX verbs.

(Named test_cli_completion_VERB to stay clear of `test_completion.py`, which pins
the unrelated `dos.completion` loop-convergence module.)

Completion is generated from the LIVE parser, so the load-bearing property is
that it can never advertise a verb that does not exist and needs zero edits when
a verb is added: every pin here DERIVES the expected verb set from
`cli.build_parser()` rather than hardcoding it (the same anti-drift idiom
`tests/test_cli_ergonomics.py` uses for the curated help). `start-here` is a thin
router whose every named verb must resolve to a real subparser.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import dos
from dos import cli


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


def _registered_verbs() -> set[str]:
    parser = cli.build_parser()
    verbs: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs |= set(action.choices)
    return verbs


# ---------------------------------------------------------------------------
# dos completion
# ---------------------------------------------------------------------------


def test_completion_verb_is_registered():
    assert "completion" in _registered_verbs()


def test_completion_bash_mentions_known_verbs():
    """The bash script lists the verbs DERIVED from the live parser — not a
    hand-kept list, so it cannot drift out of sync with the CLI."""
    proc = _cli("completion", "bash")
    assert proc.returncode == 0, proc.stderr
    script = proc.stdout
    assert "complete -F _dos_completion dos" in script
    for verb in ("verify", "arbitrate", "init", "pickable", "completion"):
        assert verb in _registered_verbs()
        assert verb in script, f"{verb} missing from bash completion"


def test_completion_each_shell_emits_appropriate_script():
    for shell, needle in (
        ("bash", "complete -F _dos_completion dos"),
        ("zsh", "#compdef dos"),
        ("fish", "complete -c dos"),
    ):
        proc = _cli("completion", shell)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip(), f"{shell} produced an empty script"
        assert needle in proc.stdout, f"{shell} script missing {needle!r}"


def test_completion_bash_is_syntactically_valid_when_bash_available():
    """If bash is on PATH, the emitted script must parse (`bash -n`).

    Pipe the script as BYTES so the parent shell does not re-encode \n→\r\n on
    Windows — a CR would make `bash -n` fail on a script that is actually fine.
    The CLI emits pure-LF bytes (see cmd_completion); this checks them as-is.
    """
    if shutil.which("bash") is None:
        return  # bash not on this runner — skip the syntax check
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    raw = subprocess.run(
        [sys.executable, "-m", "dos.cli", "completion", "bash"],
        capture_output=True, env=env,  # bytes mode (no text=True)
    )
    assert raw.returncode == 0, raw.stderr
    assert b"\r" not in raw.stdout, "completion script must be pure-LF (no CR)"
    check = subprocess.run(["bash", "-n"], input=raw.stdout,
                           capture_output=True)
    assert check.returncode == 0, check.stderr


def test_completion_rejects_unknown_shell():
    proc = _cli("completion", "tcsh")
    assert proc.returncode == 2
    assert "bash" in proc.stderr and "zsh" in proc.stderr and "fish" in proc.stderr


def test_completion_covers_every_verb():
    """Every registered verb appears in the bash completion's verb list — the
    no-silent-gap property (a new verb is completable the moment it is added)."""
    proc = _cli("completion", "bash")
    assert proc.returncode == 0, proc.stderr
    for verb in _registered_verbs():
        assert verb in proc.stdout, f"{verb} is registered but not completable"


# ---------------------------------------------------------------------------
# dos start-here
# ---------------------------------------------------------------------------


def test_start_here_is_registered():
    assert "start-here" in _registered_verbs()


def test_start_here_names_only_real_verbs():
    """Every verb the router points at must be a real subparser — it can never
    advertise a dead verb (cmd_start_here also cross-checks at render time)."""
    known = _registered_verbs()
    for _task, verb, _blurb in cli._START_HERE_ROWS:
        assert verb in known, f"start-here points at unknown verb {verb!r}"


def test_start_here_prints_router():
    proc = _cli("start-here")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "what do you want to do?" in out
    assert "dos quickstart" in out
    assert "dos verify" in out
