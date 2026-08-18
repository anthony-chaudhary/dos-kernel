from __future__ import annotations

from pathlib import Path

import yaml

import dos

_REPO = Path(dos.__file__).resolve().parents[2]
_WORKFLOW = _REPO / ".github/workflows/scoreboard-refresh.yml"


def _load():
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def test_self_scoreboard_refresh_is_scheduled_and_serialized():
    doc = _load()
    triggers = doc.get("on", doc.get(True))
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert doc["permissions"] == {"contents": "write", "issues": "write"}


def test_refresh_gates_before_touching_published_artifacts():
    body = _WORKFLOW.read_text(encoding="utf-8")
    gate = body.index("scoreboard_refresh_gate.py")
    publish = body.index('cp "$SWEEP" "$target/sweep.json"')
    assert gate < publish
    assert "steps.gate.outputs.safe == 'true'" in body
    assert "steps.gate.outputs.safe != 'true'" in body
    assert "gh issue create" in body


def test_refresh_checks_page_and_rollup_before_explicit_commit():
    body = _WORKFLOW.read_text(encoding="utf-8")
    commit = body.index('git commit -m "docs(scoreboard): refresh self-audit at HEAD"')
    page_check = body.index(
        '--out docs/scoreboard/anthony-chaudhary/dos-kernel.md --check'
    )
    assert page_check < commit
    assert "scoreboard_rollup.py --stamp \"$(date -u +%F)\" --check" in body
    assert "git add -- \\" in body
    assert "git add -A" not in body
