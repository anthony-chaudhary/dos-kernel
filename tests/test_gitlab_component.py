"""Pin the GitLab CI/CD Catalog component (gitlab-ci/templates/dos-verify.yml).

The catalog-grade twin of the raw include (pinned by test_gitlab_ci_template).
A component is a TWO-document YAML stream — a `spec:inputs` interface, then the
job config that interpolates those inputs via `$[[ inputs.x ]]`. If it loses a
load-bearing piece (the typed inputs, the full-clone GIT_DEPTH, the
exit-code-is-the-verdict script, the named job the consumer's `include` resolves)
it ships broken to every consumer who pins it from the catalog.

The component is published from a GitLab mirror project, but the SOURCE OF TRUTH
is here — so the contract is pinned here, the same way verify-action's action.yml
is pinned in this repo even though the Marketplace listing is external.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_COMPONENT = (
    Path(__file__).resolve().parents[1] / "gitlab-ci" / "templates" / "dos-verify.yml"
)


def _docs() -> list[dict]:
    """The component is a 2-doc stream: [spec, job]. Parse both."""
    return list(yaml.safe_load_all(_COMPONENT.read_text(encoding="utf-8")))


def test_component_is_a_two_doc_spec_then_job() -> None:
    docs = _docs()
    assert len(docs) == 2, f"a component is `spec --- job`; got {len(docs)} docs"
    assert "spec" in docs[0] and "inputs" in docs[0]["spec"], "missing spec:inputs"
    assert "dos-verify" in docs[1], "the named job IS the consumer's include contract"


def test_inputs_are_the_typed_interface() -> None:
    """The whole point of a component over a raw include: a typed, defaulted
    interface. Lose an input and a consumer's `inputs:` block silently breaks."""
    inputs = _docs()[0]["spec"]["inputs"]
    for name in ("dos_version", "fail_on", "workspace", "plan", "phase", "stage", "image"):
        assert name in inputs, f"the `{name}` input is part of the consumer contract"
    # Defaults make every input optional — a bare `include: - component:` must work.
    assert inputs["fail_on"]["default"] == "unwitnessed"
    assert inputs["stage"]["default"] == "test"


def test_full_clone_is_forced() -> None:
    # The audit reads ancestry; GitLab's default shallow clone amputates it.
    job = _docs()[1]["dos-verify"]
    assert job["variables"]["GIT_DEPTH"] == "0"


def test_runs_on_merge_requests() -> None:
    job = _docs()[1]["dos-verify"]
    conditions = [r.get("if", "") for r in job["rules"]]
    assert any("merge_request_event" in c for c in conditions)


def test_script_carries_both_verdicts_and_the_observe_mode() -> None:
    job = _docs()[1]["dos-verify"]
    script = "\n".join(job["script"])
    assert "commit-audit --sweep" in script
    assert "CI_MERGE_REQUEST_DIFF_BASE_SHA" in script   # MR base..head default
    assert "--warn-only" in script                       # fail_on=none path
    assert "dos verify" in script                        # the optional phase rung


def test_install_names_the_real_dist_and_threads_the_version_input() -> None:
    job = _docs()[1]["dos-verify"]
    before = "\n".join(job["before_script"])
    # The dist name is dos-kernel (the bare `dos` on PyPI is a squatter); the
    # version-spec input concatenates a pip pin for reproducible CI.
    assert "dos-kernel$[[ inputs.dos_version ]]" in before


def test_inputs_are_actually_interpolated_in_the_job() -> None:
    """A typed input nobody references is dead. Assert the job body uses the
    `$[[ inputs.x ]]` interpolation for the knobs that drive behavior."""
    job = _docs()[1]["dos-verify"]
    body = yaml.safe_dump(job)
    for ref in ("inputs.fail_on", "inputs.workspace", "inputs.plan",
                "inputs.phase", "inputs.stage", "inputs.image"):
        assert f"$[[ {ref} ]]" in body, f"the `{ref}` input is declared but never used"
