"""Pin the `examples/fleet_frameworks/` runnable recipes against the real kernel.

The fleet-framework cookbook (`examples/playbooks/cookbook-fleet-frameworks.md`)
labels its recipes **ran** — but pasted output rots silently: a kernel contract
drift (a changed verdict field, a different refuse reason, a renamed kwarg)
would turn the cookbook stale with nothing in the suite going red. These tests
execute the same seams from the runnable files under `examples/fleet_frameworks/`,
the same discipline `test_hermes_integration_example.py` applies to that
example.

Recipe 0 (dos-only) ALWAYS runs. Each framework recipe runs only when its
framework is importable and SKIPS cleanly when not — CI without the frameworks
still pins the universal seam; a checkout with `langgraph`/`crewai`/
`autogen-agentchat`/`openai-agents` installed pins that framework's bind too.

Every test asserts the cookbook's headline property, never the worker's text:
a claim with no artifact behind it must not route/stop/land as done, and the
same claim with a real commit behind it must.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "fleet_frameworks"

_EXAMPLE_MODULES = ("_fixture", "universal", "langgraph_referee",
                    "crewai_verify_tool", "autogen_termination",
                    "openai_agents_guardrail", "crewai_task_guardrail",
                    "openai_agents_effect_gate", "in_session_deconflict")


@pytest.fixture()
def example_path():
    """Put the example dir on `sys.path` (the recipes use sibling imports for
    `_fixture`), undone — modules included — on exit so nothing leaks between
    tests or shadows another suite's module names."""
    sys.path.insert(0, str(_EXAMPLE_DIR))
    yield
    try:
        sys.path.remove(str(_EXAMPLE_DIR))
    except ValueError:
        pass
    for name in _EXAMPLE_MODULES:
        sys.modules.pop(name, None)


def _demo(module_name: str, tmp_path: Path) -> dict:
    mod = importlib.import_module(module_name)
    return mod.run_demo(tmp_path / "demo_repo")


# ===========================================================================
# Recipe 0 — the universal two-function adapter (no framework; always runs).
# ===========================================================================
def test_universal_verify_and_admit_seams(example_path, tmp_path):
    """The cookbook's Recipe 0 output, re-derived: the backed phase verifies
    SHIPPED via the grep-subject rung, the unbacked one does not, a free region
    is admitted, and the same region held by a live lease is refused."""
    r = _demo("universal", tmp_path)
    assert r["auth1_done"] is True
    assert r["auth2_done"] is False
    assert r["detail"].shipped is True
    assert r["detail"].source == "grep-subject"
    assert r["admit_free"].outcome == "acquire"
    assert r["admit_free"].lane == "api"
    assert r["admit_held"].outcome == "refuse"


# ===========================================================================
# Recipe 1 — LangGraph: route on the referee's verdict, never the report.
# ===========================================================================
def test_langgraph_referee_routes_on_verdict_not_report(example_path, tmp_path):
    pytest.importorskip("langgraph", reason="langgraph not installed")
    r = _demo("langgraph_referee", tmp_path)
    lying, honest = r["lying"], r["honest"]
    # The worker claimed done both times with identical confidence...
    assert "done" in lying["report"] and "done" in honest["report"]
    # ...but only the claim git could back was allowed to end the run as done.
    assert lying["verdict"] == "NOT_SHIPPED"
    assert lying["attempts"] == 2          # redispatched once, then gave up
    assert honest["verdict"] == "SHIPPED"
    assert honest["attempts"] == 1


# ===========================================================================
# Recipe 2 — CrewAI: the verify tool + the post-kickoff gate.
# ===========================================================================
def test_crewai_tool_and_post_kickoff_gate(example_path, tmp_path):
    pytest.importorskip("crewai", reason="crewai not installed")
    r = _demo("crewai_verify_tool", tmp_path)
    assert r["auth1"] == "SHIPPED via grep-subject"
    assert r["auth2"].startswith("NOT_SHIPPED")
    # The gate reads zero bytes of crew output: only the unbacked unit redispatches.
    assert r["redispatch"] == [("AUTH", "AUTH2")]


# ===========================================================================
# Recipe 3 — AutoGen: TERMINATE is a claim; only git satisfies the stop.
# ===========================================================================
def test_autogen_termination_only_git_can_satisfy(example_path, tmp_path):
    pytest.importorskip("autogen_agentchat", reason="autogen-agentchat not installed")
    r = _demo("autogen_termination", tmp_path)
    assert r["lying"] is None              # lying TERMINATE -> run keeps going
    assert r["honest"] is not None         # honest TERMINATE -> StopMessage
    assert r["honest"].source == "dos"
    assert "shipped per git ancestry" in r["honest"].content
    # Composes with a budget stop so an honestly-stuck run still ends.
    assert "Or" in type(r["budgeted"]).__name__


# ===========================================================================
# Recipe 4 — OpenAI Agents SDK: the guardrail trips on an unbacked "done".
# ===========================================================================
def test_openai_agents_guardrail_trips_on_unbacked_done(example_path, tmp_path):
    agents = pytest.importorskip("agents", reason="openai-agents not installed")
    if not hasattr(agents, "output_guardrail"):
        pytest.skip("an unrelated 'agents' package shadows openai-agents")
    r = _demo("openai_agents_guardrail", tmp_path)
    assert r["unbacked"].tripwire_triggered is True
    assert r["unbacked"].output_info["verdict"] == "NOT_SHIPPED"
    assert r["backed"].tripwire_triggered is False
    assert r["backed"].output_info["verdict"] == "SHIPPED"
    assert r["backed"].output_info["via"] == "grep-subject"


# ===========================================================================
# Recipe 6 — CrewAI, driver form: the shipped task guardrail (dos only;
# always runs — the adapter imports nothing from crewai).
# ===========================================================================
def test_crewai_task_guardrail_fails_overclaim_then_passes(example_path, tmp_path):
    r = _demo("crewai_task_guardrail", tmp_path)
    ok1, reason = r["attempt1"]
    assert ok1 is False                      # over-claim: the task FAILS...
    assert "no commit beyond" in reason      # ...with the actionable feedback
    ok2, value = r["attempt2"]
    assert ok2 is True                       # work landed: the task passes...
    assert value is r["attempt2_output"]     # ...output through unchanged


# ===========================================================================
# Recipe 7 — OpenAI Agents SDK, driver form: the shipped output guardrail.
# ===========================================================================
def test_openai_agents_effect_gate_trips_then_clears(example_path, tmp_path):
    agents = pytest.importorskip("agents", reason="openai-agents not installed")
    if not hasattr(agents, "output_guardrail"):
        pytest.skip("an unrelated 'agents' package shadows openai-agents")
    r = _demo("openai_agents_effect_gate", tmp_path)
    assert r["unbacked"].tripwire_triggered is True
    assert r["unbacked"].output_info["outcome"] == "TRIPPED"
    assert r["backed"].tripwire_triggered is False
    assert r["backed"].output_info["outcome"] == "CLEAR"


# ===========================================================================
# Recipe 8 — the in-session deconfliction handshake (dos only; always runs).
# "Another agent is also working here; deconflict with DOS" as one arbitrate
# call — the same verdict whether the other agent is a sibling sub-agent, a
# parallel loop, or a foreign runtime.
# ===========================================================================
def test_in_session_deconflict_routes_not_stalls(example_path, tmp_path):
    r = _demo("in_session_deconflict", tmp_path)

    # A — a clean fan-out over disjoint regions admits every worker, and each
    # admission is folded into `held` so my own sub-agents deconflict too.
    clean = r["clean"]
    assert [s["go"] for s in clean] == [True, True, True]
    assert [s["dispatch_to"] for s in clean] == ["src", "docs", "tests"]

    # B — a sibling holding src/** refuses my src request (NOT a stall: a free
    # candidate is offered) while my disjoint docs worker still goes.
    collide = {s["requested"]: s for s in r["collide"]}
    assert collide["src"]["go"] is False
    assert collide["src"]["free_clusters"]          # routed, not dead-ended
    assert collide["docs"]["go"] is True

    # C — deconfliction is by file-TREE overlap, not lane name: a nested region
    # is admitted even with a different parent's tree held.
    assert r["nested"].outcome == "acquire"
