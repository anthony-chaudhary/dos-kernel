"""Recipe 7 — OpenAI Agents SDK: the SHIPPED output guardrail (driver form of Recipe 4).

Recipe 4 wires the oracle into an `@output_guardrail` by hand, keyed on the
run context's (plan, phase). This is the same distrust as one shipped line —
and a wider claim model: `dos.drivers.openai_agents_guardrail` builds the
SDK's `OutputGuardrail` over the effect gate, so a run whose declared
deliverable (here: a commit) is ABSENT from the git read-back trips the
tripwire (`Runner.run(...)` raises `OutputGuardrailTripwireTriggered`) instead
of the claim landing. In your real fleet:

    agent = Agent(
        name="worker",
        instructions="Land the fix, then report.",
        output_guardrails=[dos_output_guardrail(".", expect=[CommitClaim()])],
    )

    python examples/fleet_frameworks/openai_agents_effect_gate.py

Needs `dos` + `openai-agents`. No LLM and no API key — the guardrail is
invoked directly both ways, because the tripwire seam is the demo.
"""

from __future__ import annotations

import asyncio
import subprocess

from agents import Agent, RunContextWrapper

from dos.drivers._effect_gate import CommitClaim
from dos.drivers.openai_agents_guardrail import dos_output_guardrail

from _fixture import make_demo_repo


async def demo(cfg) -> dict:
    # Built before the "run": the CommitClaim baseline pins to HEAD here.
    guardrail = dos_output_guardrail(str(cfg.root), expect=[CommitClaim()])
    worker = Agent(name="worker", instructions="Land the fix, then report.",
                   output_guardrails=[guardrail])

    async def invoke(claim_text: str):
        ctx = RunContextWrapper(context=None)
        result = await guardrail.run(agent=worker, agent_output=claim_text,
                                     context=ctx)
        return result.output

    # The over-claim: narrated success, no commit behind it -> tripwire.
    unbacked = await invoke("done! committed the fix.")

    # The work actually lands; the SAME guardrail now finds the commit.
    (cfg.root / "fix.py").write_text("def fix(): ...\n", encoding="utf-8")
    subprocess.run(["git", "add", "fix.py"], cwd=cfg.root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "FIX1: land the fix"],
                   cwd=cfg.root, check=True, capture_output=True)
    backed = await invoke("done! committed the fix.")

    return {"unbacked": unbacked, "backed": backed}


def run_demo(repo=None) -> dict:
    return asyncio.run(demo(make_demo_repo(repo)))


def main() -> int:
    r = run_demo()
    u, b = r["unbacked"], r["backed"]
    print(f"guardrail on unbacked 'done' -> tripwire: {u.tripwire_triggered}")
    print(f"  {u.output_info['reason']}")
    print(f"guardrail on backed 'done'   -> tripwire: {b.tripwire_triggered}  "
          f"({b.output_info['outcome']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
