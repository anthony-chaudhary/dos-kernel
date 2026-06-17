"""The cadence WORKFLOW is wired the way the unattended release contract requires.

`.github/workflows/release-cadence.yml` is the cron that runs the decide→cut→
push→tag-after-green pipeline with no human in the loop (docs/353). The generic
`tests/test_workflow_yaml_parses.py` already guarantees it PARSES and has runnable
jobs (the ci.yml#L300 class). This test pins the cadence-SPECIFIC contract that a
careless edit could break without breaking the parse:

  * it has a `schedule` trigger (else it never fires unattended) AND a manual
    `workflow_dispatch` with a `dry_run` input (the live read-back arm);
  * the repo guard is present (it pushes to the trunk + fires the publish
    pipeline — must never run on a fork/the private archive);
  * it has the `contents: write` permission (it pushes the commit + tag);
  * a concurrency group with cancel-in-progress: false (two ticks must not race,
    a cut mid-flight must finish);
  * the body keeps the tag-after-green discipline (it waits for ci.yml before
    tagging) — a structural grep, so dropping the wait reds the suite.

Dev/release TOOLING territory; never imported by the kernel.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import dos

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_WF = _REPO_ROOT / ".github" / "workflows" / "release-cadence.yml"


def _load() -> dict:
    assert _WF.is_file(), f"cadence workflow missing at {_WF}"
    return yaml.safe_load(_WF.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML parses the bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), "workflow has no trigger mapping"
    return on


def test_has_schedule_and_dispatch_with_dry_run():
    on = _on(_load())
    assert "schedule" in on, "cadence has no schedule trigger — it would never fire unattended"
    sched = on["schedule"]
    assert isinstance(sched, list) and sched and "cron" in sched[0]
    assert "workflow_dispatch" in on, "no manual arm"
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert "dry_run" in inputs, "the dispatch dry-run (live read-back) input is missing"


def test_repo_guard_present():
    doc = _load()
    job = doc["jobs"]["cadence"]
    guard = job.get("if", "")
    assert "github.repository ==" in guard and "dos-kernel" in guard, (
        "the cadence pushes to the trunk and fires publish.yml — it MUST be guarded "
        "to the canonical repo so a fork/private archive never auto-releases"
    )


def test_can_push_commit_and_tag():
    doc = _load()
    job = doc["jobs"]["cadence"]
    perms = job.get("permissions") or {}
    assert perms.get("contents") == "write", "cadence cannot push without contents: write"


def test_concurrency_serializes_ticks():
    doc = _load()
    conc = doc.get("concurrency") or {}
    assert conc.get("group"), "no concurrency group — two ticks could race the push/tag"
    assert conc.get("cancel-in-progress") is False, (
        "cancel-in-progress must be false — a cut mid-flight should finish, not be killed"
    )


def test_keeps_tag_after_green_discipline():
    """The body must wait for ci.yml to go green before tagging (docs/295). A
    structural grep — if a refactor drops the wait, this reds rather than letting
    the cadence tag an unwitnessed commit."""
    body = _WF.read_text(encoding="utf-8")
    assert "ci.yml" in body, "the tag-after-green wait references ci.yml — it's gone"
    assert "conclusion == \"success\"" in body or 'conclusion == "success"' in body, (
        "the green-wait predicate is missing"
    )
    # the tag is minted only after the wait (git tag appears after the poll loop)
    assert "git tag -a" in body, "no annotated tag step"
