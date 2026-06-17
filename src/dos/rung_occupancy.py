"""The rung-occupancy backtest — measure whether the floor is doing any work.

`verify` grades every ship-verdict by FORGEABILITY: a `registry` row or a
`grep-artifact` (file-path) match is non-forgeable — its byte-author is git, not
the judged agent — while a `grep-subject` match rests on the commit subject the
agent itself typed (`git commit --allow-empty -m 'docs/X: PHASE — done'` clears
it with zero code shipped). The grade is already DATA on the verdict
(`oracle.ShipVerdict.source` / `.rung`; the partition is `resume.NONFORGEABLE_RUNGS`
and `oracle._NONFORGEABLE_GREP_RUNGS`). What the kernel has never shipped is the
*instrument that reads the grade back over real history* — the question docs/138
§"Collecting more data" item 3 names and leaves open:

    > over a real commit history, WHICH `source=` actually answered each `verify`
    > — and of the `grep-subject` answers, how many were later refuted by a
    > `grep-artifact` re-check. … if 80% of green verdicts rest on the forgeable
    > rung, the floor is doing little work.

This module is that instrument, and it is the truth-syscall analogue of
`judge_eval.compose_deterministic_first`'s rung-occupancy column (which exists for
the JUDGE rung but had no equivalent for the oracle). It folds a stream of
already-built `ShipVerdict`s into:

  1. **rung occupancy** — of the GREEN (shipped) verdicts, the fraction that stood
     on each forgeability class (non-forgeable floor vs forgeable subject rung vs
     an ungraded bare-`grep`). The headline is `forgeable_green_rate`: the docs/138
     "80%" number, measured instead of feared.
  2. **the re-check delta** — of the forgeable greens, how many an independent
     `grep-artifact` re-check (the diff rung, which the agent cannot forge) FAILS
     to corroborate. A high refute count is the empirical proof that the floor was
     load-bearing after all; a zero refute count means every forgeable green was
     *also* a real artifact ship (the subject rung happened to agree with the diff).

Everything here is **pure**: it consumes already-built verdicts + an optional
re-check map the caller gathered at the I/O boundary, and counts. No git, no host
names — it sits in the kernel layer beside `judge_eval` and `verdict_census`. The
honesty stance is the same as those: the eval is only as honest as its inputs, so
the *script* that runs `verify` over real history (`scripts/rung_occupancy_backtest.py`)
gathers the verdicts and the re-check from git — this module never trusts a label
it could not re-derive from the verdict's own `source`/`rung` fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Optional


class Forgeability(str, Enum):
    """The forgeability class a green verify-verdict stood on.

    `NONFORGEABLE` — the byte-author of the witness is NOT the judged agent
    (`registry`: an orchestration-layer `mark done`; `grep-artifact`: the
    file-path/diff rung — a commit cannot lie about which files it touched).
    `FORGEABLE` — the witness is the commit SUBJECT/BODY the agent authored
    (`grep-subject`; a no-op `--allow-empty` commit clears it).
    `UNGRADED` — a bare `grep` source whose raw rung was unknown, so the oracle
    could not place it on either side (counted apart, never silently folded into
    the floor — that would flatter the measurement).
    `NONE` — not a green verdict (a miss / `shipped=False`); excluded from the
    occupancy denominator, which is GREEN verdicts only.
    """

    NONFORGEABLE = "nonforgeable"
    FORGEABLE = "forgeable"
    UNGRADED = "ungraded"
    NONE = "none"


# The graded `source` vocabulary `verify` emits, partitioned by forgeability. This
# is the SAME split `resume.NONFORGEABLE_RUNGS` ({file-path, registry}) and
# `oracle._NONFORGEABLE_GREP_RUNGS` ({file-path}) encode, re-expressed over the
# GRADED `source` label (`oracle._grade_grep_source`): `file-path` has already been
# graded to `grep-artifact` by the time a verdict carries a `source`. Kept as data
# so a new source label is a one-line addition, never a scattered edit — the same
# discipline the oracle keeps for its rung sets.
NONFORGEABLE_SOURCES = frozenset({"registry", "grep-artifact"})
FORGEABLE_SOURCES = frozenset({"grep-subject"})
# A bare `grep` (the oracle's "rung unknown" fallback, `_grade_grep_source`) is
# neither — it is graded UNGRADED so it cannot pad the floor's apparent load.
_UNGRADED_SOURCES = frozenset({"grep"})


def classify_forgeability(shipped: bool, source: str) -> Forgeability:
    """Place one ship-verdict on the forgeability axis from its graded `source`.

    Pure and total: a green verdict whose `source` is in neither known set is
    `UNGRADED` (conservative — an unrecognized label is never credited to the
    floor). A non-green verdict is `NONE` regardless of source. ``shipped`` and
    ``source`` are exactly the `ShipVerdict` fields; the caller need not import
    the oracle's dataclass to use the census (a `(bool, str)` pair is enough),
    which keeps this leaf free of any oracle dependency.
    """
    if not shipped:
        return Forgeability.NONE
    s = (source or "").strip()
    if s in NONFORGEABLE_SOURCES:
        return Forgeability.NONFORGEABLE
    if s in FORGEABLE_SOURCES:
        return Forgeability.FORGEABLE
    # Includes the bare `grep` fallback and any unknown/empty label.
    return Forgeability.UNGRADED


# A re-check verdict for ONE forgeable green: did an independent `grep-artifact`
# (diff-rung) re-derivation of the SAME (plan, phase) corroborate the ship? The
# caller gathers these at the git boundary; the census only counts them.
#   True  — the artifact rung ALSO found the ship (the subject green was real)
#   False — the artifact rung did NOT (the green stood ONLY on the forgeable rung)
#   absent — no re-check was run for this pair (counted as `recheck_skipped`)
ReCheck = bool


@dataclass(frozen=True)
class RungOccupancy:
    """The forgeability census over a set of verify-verdicts.

    The occupancy counts (`nonforgeable + forgeable + ungraded == green`) answer
    docs/138's open question: of the verdicts the kernel called SHIPPED, how many
    rested on the non-forgeable floor vs the forgeable subject rung. The re-check
    counts answer the follow-on: of the forgeable greens, how many an independent
    artifact re-derivation refuted — the empirical test of whether the floor was
    actually carrying load or merely shadowing a subject rung that happened to be
    honest.
    """

    n: int                 # all verdicts folded (green + non-green)
    green: int             # shipped=True verdicts — the occupancy denominator
    nonforgeable: int      # green on registry / grep-artifact (the floor)
    forgeable: int         # green on grep-subject (the agent-authored rung)
    ungraded: int          # green on a bare/unknown `grep` (placed on neither side)
    # --- the re-check, over the FORGEABLE greens only ---
    recheck_corroborated: int  # forgeable greens the artifact rung ALSO found
    recheck_refuted: int       # forgeable greens the artifact rung did NOT find
    recheck_skipped: int       # forgeable greens with no re-check run

    # --- derived rates (all guard divide-by-zero by returning 0.0) ---

    @property
    def forgeable_green_rate(self) -> float:
        """Of the GREEN verdicts, the fraction that stood on the FORGEABLE rung —
        the docs/138 headline. The number that, if high, means the non-forgeable
        floor is doing little work: most "shipped"s rest on a commit subject the
        agent could have typed over an empty commit."""
        return (self.forgeable / self.green) if self.green else 0.0

    @property
    def floor_load(self) -> float:
        """Of the GREEN verdicts, the fraction the non-forgeable floor carried —
        the complement that matters. `floor_load + forgeable_green_rate +
        ungraded_rate == 1.0` over greens. This is "how much of the kernel's
        trust output was actually un-authored by the judged agent."""
        return (self.nonforgeable / self.green) if self.green else 0.0

    @property
    def ungraded_rate(self) -> float:
        """Of the GREEN verdicts, the fraction on a bare/unknown `grep` rung the
        oracle could not grade — neither credited to the floor nor charged to the
        forgeable rung. A high value means the *grading* itself is incomplete (the
        verdict didn't carry a `rung`), which is its own finding."""
        return (self.ungraded / self.green) if self.green else 0.0

    @property
    def recheck_refute_rate(self) -> float:
        """Of the forgeable greens that WERE re-checked, the fraction the artifact
        rung REFUTED — the floor's measured payload. A nonzero value is the proof
        that the forgeable rung cleared ships the diff rung would not: every refuted
        pair is a green that rested ONLY on the agent's own subject line. (Skipped
        re-checks are excluded from the denominator — an un-run check is not a
        corroboration.)"""
        denom = self.recheck_corroborated + self.recheck_refuted
        return (self.recheck_refuted / denom) if denom else 0.0

    @property
    def floor_held(self) -> bool:
        """The honest headline predicate: did every re-checked forgeable green ALSO
        clear the artifact rung? True iff no re-check refuted (the forgeable rung
        never out-cleared the diff on the sample). False the moment one forgeable
        green fails its artifact re-check — DOS reports the hole, the same way
        `forge_page` renders FORGERY LANDED on a single floor breach. A set with no
        re-checked forgeable greens is vacuously `True` (nothing was tested), which
        the `recheck_*` counts disambiguate so a vacuous hold can't masquerade as a
        proven one."""
        return self.recheck_refuted == 0

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "green": self.green,
            "occupancy": {
                "nonforgeable": self.nonforgeable,
                "forgeable": self.forgeable,
                "ungraded": self.ungraded,
            },
            "rates": {
                "forgeable_green_rate": round(self.forgeable_green_rate, 4),
                "floor_load": round(self.floor_load, 4),
                "ungraded_rate": round(self.ungraded_rate, 4),
                "recheck_refute_rate": round(self.recheck_refute_rate, 4),
            },
            "recheck": {
                "corroborated": self.recheck_corroborated,
                "refuted": self.recheck_refuted,
                "skipped": self.recheck_skipped,
            },
            "floor_held": self.floor_held,
        }


# One folded verdict: the (shipped, source) pair the census reads + the (plan,
# phase) key the caller uses to look up the re-check. A caller hands in
# `ShipVerdict`s directly (duck-typed on `.shipped`/`.source`/`.plan`/`.phase`),
# or any object/namedtuple with those four attributes — the fold never imports the
# oracle, so the kernel layering stays one-directional.
def _attr(v: object, name: str, default=None):
    return getattr(v, name, default)


def census(
    verdicts: Iterable[object],
    rechecks: Optional[Mapping[tuple[str, str], ReCheck]] = None,
) -> RungOccupancy:
    """Fold a stream of ship-verdicts into a forgeability census.

    ``verdicts`` is any iterable of objects carrying ``.shipped`` (bool) and
    ``.source`` (str) — `oracle.ShipVerdict` is the canonical one, but the fold is
    duck-typed so a test can pass a tiny stub and the kernel layer never depends on
    the oracle. ``rechecks`` maps ``(plan, phase)`` → the artifact-rung re-check
    result for that pair (``True`` corroborated, ``False`` refuted); a pair absent
    from the map is a SKIPPED re-check. Only FORGEABLE greens consult the re-check —
    a non-forgeable green is already on the floor and needs no second opinion, and a
    non-green has nothing to re-check.

    Pure: it reads the verdicts + the map and counts. The re-check map is the only
    thing the caller had to gather from git; everything else is derived from the
    verdict's own already-graded `source`, so the census cannot credit the floor
    with a rung the oracle did not itself grade as non-forgeable.
    """
    rechecks = rechecks or {}
    n = green = nf = fg = ug = 0
    rc_corr = rc_ref = rc_skip = 0
    for v in verdicts:
        n += 1
        shipped = bool(_attr(v, "shipped", False))
        source = str(_attr(v, "source", "") or "")
        cls = classify_forgeability(shipped, source)
        if cls is Forgeability.NONE:
            continue
        green += 1
        if cls is Forgeability.NONFORGEABLE:
            nf += 1
        elif cls is Forgeability.UNGRADED:
            ug += 1
        else:  # FORGEABLE — the only class that consults the re-check
            fg += 1
            key = (str(_attr(v, "plan", "")), str(_attr(v, "phase", "")))
            if key in rechecks:
                if rechecks[key]:
                    rc_corr += 1
                else:
                    rc_ref += 1
            else:
                rc_skip += 1
    return RungOccupancy(
        n=n, green=green, nonforgeable=nf, forgeable=fg, ungraded=ug,
        recheck_corroborated=rc_corr, recheck_refuted=rc_ref,
        recheck_skipped=rc_skip,
    )


def render_text(occ: RungOccupancy) -> str:
    """A compact human report — the floor's measured load, in one screen.

    Mirrors `verdict_census.render_text` / `judge_eval` reporting house style:
    headline rate first, then the occupancy split, then the re-check payload, then
    the honest predicate. Pure (string in/out)."""
    pct = lambda x: f"{x * 100:5.1f}%"  # noqa: E731 — local formatter, kept inline
    lines = [
        "rung-occupancy backtest — is the non-forgeable floor doing any work?",
        "",
        f"  verdicts folded     {occ.n}  ({occ.green} green / {occ.n - occ.green} not shipped)",
    ]
    if not occ.green:
        lines.append("  (no green verdicts — nothing to grade)")
        return "\n".join(lines)
    lines += [
        "",
        f"  floor load          {pct(occ.floor_load)}   non-forgeable greens "
        f"(registry / grep-artifact)   [{occ.nonforgeable}]",
        f"  forgeable greens    {pct(occ.forgeable_green_rate)}   stood on the "
        f"agent-authored subject rung   [{occ.forgeable}]",
    ]
    if occ.ungraded:
        lines.append(
            f"  ungraded greens     {pct(occ.ungraded_rate)}   bare `grep` — the "
            f"oracle could not grade the rung   [{occ.ungraded}]")
    lines += [
        "",
        "  re-check of the forgeable greens (independent artifact-rung re-derivation):",
        f"    corroborated      {occ.recheck_corroborated}   the diff rung ALSO found the ship",
        f"    REFUTED           {occ.recheck_refuted}   stood ONLY on the forgeable subject rung",
        f"    skipped           {occ.recheck_skipped}   no re-check run",
    ]
    if occ.recheck_corroborated + occ.recheck_refuted:
        lines.append(
            f"    refute rate       {pct(occ.recheck_refute_rate)}   the floor's "
            f"measured payload")
    lines += [
        "",
        ("  FLOOR HELD — no re-checked forgeable green failed its artifact re-derivation."
         if occ.floor_held else
         f"  FLOOR BREACHED — {occ.recheck_refuted} green(s) stood ONLY on the "
         "forgeable subject rung."),
    ]
    return "\n".join(lines)
