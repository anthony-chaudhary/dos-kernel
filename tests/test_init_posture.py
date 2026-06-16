"""docs/370 Phase E — `dos init --posture`: wire the enforcement posture at install.

Phase A added the posture seam (env / `dos.toml [enforcement] posture`); without an
install affordance an operator had to hand-edit TOML to turn the control-oriented
`gate` posture on. This suite pins the `dos init --posture <mode>` on-ramp:

  * the pure text upsert (`_upsert_enforcement_posture`) — append when no table,
    insert under an existing header, replace an existing value, idempotent on a
    no-op — never re-emitting (and so never reordering / decommenting) the file;
  * the CLI writes `[enforcement] posture` on a fresh scaffold AND merges it into a
    pre-existing `dos.toml` without `--force` clobbering the lanes;
  * an invalid posture is rejected by the parser (the choices come from the seam);
  * the round-trip: `dos init --posture gate` then `dos doctor --json` reads it
    back as the effective posture from config (Phase C).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos
from dos import cli


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    # Strip a leaked posture env so doctor reads the CONFIG, not a fleet override.
    env.pop("DOS_HOOK_POSTURE", None)
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# The pure text upsert — surgical, comment-preserving, idempotent.
# ---------------------------------------------------------------------------
def test_upsert_appends_when_no_enforcement_table():
    text = '[lanes]\nconcurrent = ["src"]\n'
    out, changed = cli._upsert_enforcement_posture(text, "gate")
    assert changed
    assert text in out                       # the original survives byte-for-byte
    assert "[enforcement]" in out
    assert 'posture = "gate"' in out
    assert out.endswith("\n")


def test_upsert_inserts_under_existing_header_without_posture():
    text = "[enforcement]\n# some note\n"
    out, changed = cli._upsert_enforcement_posture(text, "gate")
    assert changed
    assert '[enforcement]\nposture = "gate"' in out
    assert "# some note" in out


def test_upsert_replaces_an_existing_value_in_place():
    text = '[enforcement]\nposture = "block"\n[other]\nx = 1\n'
    out, changed = cli._upsert_enforcement_posture(text, "gate")
    assert changed
    assert 'posture = "gate"' in out
    assert 'posture = "block"' not in out
    assert "[other]\nx = 1" in out           # the trailing table is untouched
    assert out.count("[enforcement]") == 1   # no duplicate table


def test_upsert_is_idempotent_on_a_noop():
    text = '[enforcement]\nposture = "gate"\n'
    out, changed = cli._upsert_enforcement_posture(text, "gate")
    assert not changed
    assert out == text


def test_upsert_only_matches_the_posture_key_not_a_lookalike():
    """A `posture_note` key is NOT the posture value — the upsert sets posture
    separately, it does not rewrite a different key that starts with `posture`."""
    text = '[enforcement]\nposture_note = "x"\n'
    out, _ = cli._upsert_enforcement_posture(text, "gate")
    assert 'posture_note = "x"' in out
    assert 'posture = "gate"' in out


# ---------------------------------------------------------------------------
# The CLI — fresh scaffold, existing-config merge, idempotency, validation.
# ---------------------------------------------------------------------------
def _enforcement_posture(cfg_path: Path) -> str:
    import tomllib
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    return data["enforcement"]["posture"]


def test_init_posture_writes_enforcement_table(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--posture", "gate", str(dest))
    assert proc.returncode == 0, proc.stderr
    cfg = dest / "dos.toml"
    assert cfg.is_file()
    assert _enforcement_posture(cfg) == "gate"
    assert "gate" in proc.stdout  # the operator is told what changed


def test_init_posture_merges_into_existing_config_without_force(tmp_path: Path):
    dest = tmp_path / "svc"
    # First a normal scaffold (lanes derived), then turn the posture on — no --force.
    assert _cli("init", str(dest)).returncode == 0
    before = (dest / "dos.toml").read_text(encoding="utf-8")
    assert "[enforcement]" not in before  # the default scaffold has no posture table
    proc = _cli("init", "--posture", "gate", str(dest))
    assert proc.returncode == 0, proc.stderr
    after = (dest / "dos.toml").read_text(encoding="utf-8")
    assert _enforcement_posture(dest / "dos.toml") == "gate"
    assert "[lanes]" in after                 # the lanes the first scaffold wrote survive
    assert before.rstrip("\n") in after       # nothing the first pass wrote was lost


def test_init_posture_change_in_place(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", "--posture", "gate", str(dest)).returncode == 0
    proc = _cli("init", "--posture", "observe", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert _enforcement_posture(dest / "dos.toml") == "observe"
    # No duplicate table left behind by the re-run.
    assert (dest / "dos.toml").read_text(encoding="utf-8").count("[enforcement]") == 1


def test_init_posture_idempotent_reports_no_change(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", "--posture", "gate", str(dest)).returncode == 0
    proc = _cli("init", "--posture", "gate", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "no change" in proc.stdout
    assert _enforcement_posture(dest / "dos.toml") == "gate"


def test_init_invalid_posture_is_rejected(tmp_path: Path):
    proc = _cli("init", "--posture", "yolo", str(tmp_path / "svc"))
    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr or "yolo" in proc.stderr


def test_init_posture_on_example_pack(tmp_path: Path):
    """The posture rides the --example driver-pack path too (one upsert home)."""
    dest = tmp_path / "svc"
    proc = _cli("init", "--example", "workshop", "--posture", "gate", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert _enforcement_posture(dest / "dos.toml") == "gate"


# ---------------------------------------------------------------------------
# The round-trip — init writes it, doctor reads it back (Phase E meets Phase C).
# ---------------------------------------------------------------------------
def test_init_then_doctor_reads_the_posture_from_config(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", "--posture", "gate", str(dest)).returncode == 0
    proc = _cli("doctor", "--workspace", str(dest), "--json")
    assert proc.returncode == 0, proc.stderr
    enf = json.loads(proc.stdout)["enforcement"]
    assert enf["posture"] == "gate"
    assert enf["source"] == "config"


def test_init_default_scaffold_unchanged_without_posture(tmp_path: Path):
    """No --posture → the scaffold is byte-for-byte the pre-Phase-E one (no
    `[enforcement]` table appears unless the operator asks for it)."""
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0
    assert "[enforcement]" not in (dest / "dos.toml").read_text(encoding="utf-8")
