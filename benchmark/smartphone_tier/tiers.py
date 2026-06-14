"""tiers.py — the declared model-capability corpus (docs/341).

The fixed substrate, the `forge_arena/arena.py` analogue. Each `Trajectory` is one
synthetic agent run reduced to EXACTLY the facts the three real kernel detectors read —
nothing model-specific, because the detectors are model-agnostic by construction
(docs/153 §5). A tier is a NAMED param band (`<=1B` … `frontier`) carrying a declared
COUNT of trajectories of each failure kind.

THE FAILURE KINDS (each maps to one shipped byte-clean detector):

  DANGLE  — the narrating premature stop. Terminal turn admits an open obligation
            ("I still need to …") and no tool ran after. `dangling_intent.classify_stop`
            fires. Recoverable: re-surface the agent's own sentence.
  MINT    — a mutating call whose id arg never appeared in env-authored bytes (the model
            invented an FK). `arg_provenance.classify_call` returns believe=False.
            Recoverable: nudge a read first.
  LOOP    — the same (tool, args, result) triple repeats N times — a no-progress loop.
            `tool_stream.classify_stream` returns REPEATING/STALLED. Recoverable:
            re-surface the value the agent already holds.
  SILENT  — the UNREACHABLE remainder: a silent stop / planning failure that emits no
            cue, no minted id, no loop. The detectors are honestly BLIND to it; it is the
            denominator docs/149 found dominates on a strong model.

THE MODELLING RULE (the soundness lives here): each trajectory is built so that its
`gold_kind` detector ACTUALLY fires on it when folded by the real kernel (a DANGLE
trajectory has a real future-intent cue + zero results-after; a MINT has a real minted id
on a real mutating call with a non-empty env corpus that does not contain it; a LOOP has
a real repeated triple). A SILENT trajectory is built to fire NONE of them. The harness
never trusts these labels — it re-derives the fire from the kernel; the labels only set
up the input. If a kind ever stops firing, the harness's `detectors_real`/`labels_fire`
checks catch it loud.

THE PRE-REGISTERED SHAPE (docs/341 §3, the DECLARED model — not a measurement):

  Smaller param tier => MORE recoverable failures (dangle/mint/loop) AND more silent ones
  (a weaker model fails more, every which way). Frontier => almost all SILENT (the
  measured gemini null: ~9% dangling-detectable, ~92% premature-but-unreachable, docs/149).
  The recoverable FRACTION (recoverable / all failures) therefore falls monotonically from
  `<=1B` to `frontier`. That monotone fall is the thesis's directional prediction and the
  harness's headline soundness check. The magnitudes are synthetic; the real numbers
  arrive via `--recordings` over on-device model dumps.

This module builds only pure data. The harness hands each trajectory to the real kernel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# The reduced trajectory datum — exactly what the three detectors need, no more.
# Field provenance mirrors the real boundary readers (StopEvidence / StreamStep /
# ToolCall+PriorResults); the harness builds the kernel inputs from these.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Trajectory:
    """One synthetic agent run, reduced to the detector-relevant facts.

      failed         — did the run FAIL its task? (recoverable-fraction is over failures.)
      gold_kind      — the intended failure shape: "dangle" | "mint" | "loop" | "silent"
                       | "" (a PASSED run). A label only — the harness re-derives the
                       actual detector fire from the kernel and never trusts this.
      final_turn     — the agent's terminal narration (DANGLE surface).
      results_after  — env-authored tool results that landed AFTER the terminal turn
                       (the DANGLE corroborator; >0 => not a terminal dangle).
      steps          — the tool-result stream as (tool, args_digest, result_digest)
                       triples (the LOOP surface).
      mutating_call  — (tool_name, {arg: value}) for a single mutating call, or None
                       (the MINT surface). The harness marks it mutating + reference.
      env_blobs      — prior env-authored result texts (the MINT provenance corpus). A
                       minted id is one NOT contained in any blob.
    """

    task_id: str
    failed: bool
    gold_kind: str = ""
    final_turn: str = ""
    results_after: int = 0
    steps: Tuple[Tuple[str, str, Optional[str]], ...] = ()
    mutating_call: Optional[Tuple[str, Dict[str, object]]] = None
    env_blobs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelTier:
    """One param band and its declared failure-mode mix.

      name      — the band label, e.g. "<=1B". Smaller bands are more phone-like.
      exemplars — named models the band stands for (a LABEL for the page; the corpus is
                  synthetic, so no model is actually run).
      counts    — declared trajectory counts per kind on the FAILED side
                  ({"dangle","mint","loop","silent"}) plus "passed" (the clean runs that
                  give the enrichment guard a pass-rate denominator). Counts, never coin
                  flips — an auditable denominator (the forge_arena rule).
    """

    name: str
    exemplars: Tuple[str, ...]
    counts: Dict[str, int] = field(default_factory=dict)

    def n_failed(self) -> int:
        return sum(self.counts.get(k, 0) for k in ("dangle", "mint", "loop", "silent"))

    def n_recoverable_declared(self) -> int:
        return sum(self.counts.get(k, 0) for k in ("dangle", "mint", "loop"))


# ---------------------------------------------------------------------------
# Trajectory builders — each constructs an input the REAL detector fires on (or,
# for SILENT/passed, fires on NONE). Kept tiny + deterministic.
# ---------------------------------------------------------------------------
def _dangle(task_id: str, failed: bool = True) -> Trajectory:
    """A narrating premature stop: a real first-person-future-intent cue, nothing after.
    `dangling_intent.classify_stop` fires (cue + results_after == 0)."""
    return Trajectory(
        task_id=task_id, failed=failed, gold_kind="dangle",
        final_turn=("I have looked at the request. I still need to assign the new "
                    "member to the group before this is complete."),
        results_after=0,
    )


def _mint(task_id: str, failed: bool = True) -> Trajectory:
    """A mutating call referencing an id that appears in NO env blob — a minted FK.
    `arg_provenance.classify_call` returns believe=False (UNSUPPORTED)."""
    return Trajectory(
        task_id=task_id, failed=failed, gold_kind="mint",
        mutating_call=("update_incident", {"incident_id": "INC0099999"}),
        # the env corpus the agent observed — it never contained INC0099999
        env_blobs=("incident INC0010023 is open and assigned to the network team",
                   "the change request CHG0040001 was approved yesterday"),
    )


def _loop(task_id: str, failed: bool = True) -> Trajectory:
    """The same (tool, args, result) triple repeated past the stall window.
    `tool_stream.classify_stream` returns REPEATING/STALLED."""
    triple = ("get_incident", "args:{id:INC0010023}", "result:open/network-team")
    return Trajectory(
        task_id=task_id, failed=failed, gold_kind="loop",
        steps=tuple(triple for _ in range(6)),  # 6 >= stall_n(5) => STALLED
    )


def _silent(task_id: str, failed: bool = True) -> Trajectory:
    """A silent stop / planning failure: a terminal turn with NO future-intent cue, no
    minted id, no loop. The detectors are honestly blind to it (the unreachable
    remainder). Fires NONE of the three."""
    return Trajectory(
        task_id=task_id, failed=failed, gold_kind="silent",
        final_turn="The task is complete.",   # a (wrong) DONE claim, no open-obligation cue
        results_after=0,
        steps=(("get_incident", "args:{id:INC0010023}", "result:open/network-team"),
               ("get_user", "args:{id:U7}", "result:name=Alex")),  # advancing, distinct
    )


def _passed(task_id: str) -> Trajectory:
    """A clean PASSED run — fires none of the detectors (the enrichment pass-rate
    denominator; a detector that fires here too is NOISE, not signal)."""
    return Trajectory(
        task_id=task_id, failed=False, gold_kind="",
        final_turn="I assigned the member and verified the group now lists them. Done.",
        results_after=1,  # a real tool ran after the narration => never a dangle
        steps=(("get_group", "args:{id:G1}", "result:members=[]"),
               ("add_group_member", "args:{group:G1,user:U7}", "result:ok")),
        mutating_call=("add_group_member", {"group_id": "G1"}),
        env_blobs=("the group G1 currently has no members",),  # G1 IS in the corpus => not minted
    )


# ---------------------------------------------------------------------------
# The declared tiers. The mix is the PRE-REGISTERED shape (docs/341 §3): the
# recoverable fraction (dangle+mint+loop)/(all failed) FALLS from <=1B to frontier,
# and frontier is dominated by SILENT (the measured gemini null, docs/149). These
# are synthetic magnitudes, NOT a measurement — see RESULTS.md / module docstring.
# ---------------------------------------------------------------------------
def default_tiers() -> List[ModelTier]:
    return [
        ModelTier(
            name="<=1B",
            exemplars=("Llama-3.2-1B", "Qwen2.5-0.5B/1.5B", "Gemma-2-2B (low end)"),
            # the most phone-like band: fails most, and the failures are the most
            # DOS-shaped (it narrates abandoned steps and invents ids it cannot resolve).
            counts={"dangle": 14, "mint": 10, "loop": 8, "silent": 8, "passed": 20},
        ),
        ModelTier(
            name="1-3B",
            exemplars=("Phi-3-mini (3.8B≈)", "Qwen2.5-3B", "Gemma-2-2B"),
            counts={"dangle": 10, "mint": 7, "loop": 6, "silent": 12, "passed": 35},
        ),
        ModelTier(
            name="3-7B",
            exemplars=("Llama-3.1-8B", "Qwen2.5-7B", "Mistral-7B"),
            counts={"dangle": 6, "mint": 3, "loop": 3, "silent": 18, "passed": 55},
        ),
        ModelTier(
            name="frontier",
            exemplars=("gemini-2.5-flash", "frontier cloud"),
            # the measured null self-test: a strong model's failures are almost all
            # SILENT premature completion (docs/149 ~92%), only a thin DANGLE slice.
            counts={"dangle": 3, "mint": 0, "loop": 1, "silent": 30, "passed": 80},
        ),
    ]


def trajectories_for(tier: ModelTier) -> List[Trajectory]:
    """Materialize the declared counts into concrete trajectories the harness folds."""
    out: List[Trajectory] = []
    builders = {"dangle": _dangle, "mint": _mint, "loop": _loop, "silent": _silent}
    for kind, build in builders.items():
        for i in range(tier.counts.get(kind, 0)):
            out.append(build(f"{tier.name}:{kind}:{i}"))
    for i in range(tier.counts.get("passed", 0)):
        out.append(_passed(f"{tier.name}:passed:{i}"))
    return out
