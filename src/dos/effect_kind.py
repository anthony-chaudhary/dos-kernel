"""effect_kind — the typed NON-FILE blast-radius axis for admission.

docs/371 (#202). Today admission models ONE blast radius: the file tree a call
touches (`AdmissionRequest.tree`). `pretool_sensor._NO_FOOTPRINT_TOOLS` (Agent,
Task, TaskCreate, TaskUpdate, ToolSearch) write no repo file, so on the FILE axis
they correctly pass the lane-collision check clean (issue #46 — a known-empty
footprint must not earn a spurious "unknown blast radius" WARN).

But "writes no file" is NOT "touches nothing". Each of those tools mutates AGENT /
FLEET state the file model cannot see:

  - **Agent** forks the fleet — a new seat, token spend, and the child can itself
    spawn. A single holder spawned 140 in a 6-day journal: a 140-wide fan-out from
    one worker, exactly the runaway-fan-out class DOS exists to serialize. Under
    the file model it is invisible.
  - **Task / TaskCreate / TaskUpdate** mutate shared coordination state — the work
    queue other agents read to claim work; two agents racing on it is a
    last-writer-wins collision, not a no-op.
  - **ToolSearch** mutates the agent's own capability set — it changes what the
    agent can do on its next turn.

So the kernel has a SECOND blast-radius axis with no model, currently collapsed
into "footprint-empty like a Read". A Read touches nothing; an Agent forks the
fleet. Conflating them means a runaway Agent storm surfaces nowhere.

This leaf is the TYPE for that second axis — domain-free, like every kernel leaf:

  - `EffectKind` — the closed set of non-file effect classes (NONE / SPAWN /
    COORDINATION / CAPABILITY). The kernel names the CLASSES; it never names a
    tool. The mapping tool→EffectKind is HOST data (it names `Agent`, a CC tool),
    so it lives in the host-shaped adapter (`pretool_sensor`), exactly as the
    file-tree extraction is host-shaped today (`_tree_from_event`).
  - `classify_spawn_pressure(live_count, policy)` — the breaker-mold pure verdict:
    given how many SPAWN-effect calls a single holder already has live, is that
    CLEAR / ELEVATED / a STORM? The kernel COUNTS; the policy names the thresholds.
    This makes a fan-out storm SURFACEABLE (the #202 done-condition) without
    re-introducing a file-collision WARN — a SPAWN is adjudicated on the spawn
    axis, never pretended to be a file collision.

Layer-1 pure: a class + a value-in / verdict-out fold. No I/O, no host name, no
tool name. It is `breaker`/`queue_saturation`'s shape aimed at a new question —
"has this holder forked the fleet too wide?" — and, like them, the counting is the
caller's (the journal fold); this leaf only judges a count against a policy.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class EffectKind(enum.Enum):
    """The closed set of NON-FILE blast-radius classes a tool call can carry.

    Distinct from the FILE `tree` axis on `AdmissionRequest`: a call may write no
    file (empty tree) yet still carry one of these effects. The kernel names the
    classes only — the per-tool MAPPING (which CC tool is a SPAWN) is host data.

      - NONE        — no non-file effect (a plain read / a file write; the file
                      axis already covers its blast radius).
      - SPAWN       — forks the fleet: a new seat / token spend / a child that can
                      recurse. The runaway-fan-out class (`Agent`).
      - COORDINATION — mutates shared coordination state other agents read (the
                      work queue): `Task` / `TaskCreate` / `TaskUpdate`.
      - CAPABILITY  — mutates the agent's OWN capability set (what it can do next):
                      `ToolSearch`.
    """

    NONE = "none"
    SPAWN = "spawn"
    COORDINATION = "coordination"
    CAPABILITY = "capability"

    @property
    def is_non_file(self) -> bool:
        """True for any effect beyond the plain file axis (i.e. not NONE)."""
        return self is not EffectKind.NONE


@dataclass(frozen=True)
class SpawnPressurePolicy:
    """The thresholds for the spawn-pressure verdict — policy, named by the caller.

    `elevated_at` — a holder with this many live SPAWN-effect calls is ELEVATED
                    (worth surfacing; a wide-but-not-yet-runaway fan-out).
    `storm_at`    — this many is a STORM (a runaway fan-out from one holder, the
                    140-from-one-worker class). `storm_at >= elevated_at`.
    `window_seconds` — the trailing observation window a reader should count over
                    when surfacing a burst. Default 6d, matching the observed
                    "140 from one holder in 6 days" class.

    The defaults are deliberately loose — this axis SURFACES, it does not yet
    refuse (the #202 first leg is visibility; a refusing spawn-gate is a later
    leg, opt-in like the apply-gate). A host tightens them in its own policy.
    """

    elevated_at: int = 8
    storm_at: int = 25
    window_seconds: int = 6 * 24 * 60 * 60

    def __post_init__(self) -> None:
        if self.elevated_at < 1 or self.storm_at < self.elevated_at:
            raise ValueError(
                "spawn-pressure policy needs 1 <= elevated_at <= storm_at"
            )
        if self.window_seconds < 1:
            raise ValueError("spawn-pressure policy needs window_seconds >= 1")

    def to_dict(self) -> dict:
        return {
            "elevated_at": self.elevated_at,
            "storm_at": self.storm_at,
            "window_seconds": self.window_seconds,
        }


# The generic default: loose enough that a normal multi-agent turn never trips,
# tight enough that the observed 140-from-one-holder fan-out reads as a STORM.
GENERIC_SPAWN_PRESSURE_POLICY = SpawnPressurePolicy()


class SpawnPressure(enum.Enum):
    """The verdict band for one holder's live SPAWN count."""

    CLEAR = "clear"        # below elevated_at — an ordinary fan-out
    ELEVATED = "elevated"  # at/above elevated_at — wide, worth a glance
    STORM = "storm"        # at/above storm_at — a runaway fan-out from one holder


@dataclass(frozen=True)
class SpawnPressureVerdict:
    """The pure verdict: a holder's live SPAWN count judged against a policy."""

    pressure: SpawnPressure
    live_count: int
    elevated_at: int
    storm_at: int

    @property
    def surfaceable(self) -> bool:
        """True iff this is worth surfacing (ELEVATED or STORM) — the
        #202 'a fan-out storm is surfaceable' predicate."""
        return self.pressure is not SpawnPressure.CLEAR

    def reason(self) -> str:
        """A re-derivable one-line explanation (operator-facing)."""
        if self.pressure is SpawnPressure.STORM:
            return (
                f"{self.live_count} live SPAWN-effect calls from one holder "
                f">= {self.storm_at} (a runaway fleet fan-out from a single worker)"
            )
        if self.pressure is SpawnPressure.ELEVATED:
            return (
                f"{self.live_count} live SPAWN-effect calls from one holder "
                f">= {self.elevated_at} (a wide fan-out worth a glance)"
            )
        return f"{self.live_count} live SPAWN-effect call(s) — within normal range"


def classify_spawn_pressure(
    live_count: int,
    policy: SpawnPressurePolicy = GENERIC_SPAWN_PRESSURE_POLICY,
) -> SpawnPressureVerdict:
    """Judge ONE holder's live SPAWN-effect count against `policy`. PURE.

    `live_count` is the number of SPAWN-effect calls attributed to a single holder
    (the caller counts them — e.g. a fold over the enforce journal keyed by the
    run lineage). The kernel only judges the count; it never gathers it.

    A negative count is clamped to 0 (a fold can never legitimately go below zero;
    treat a bad input as 'nothing live' rather than raise on the hot path).
    """
    n = max(0, int(live_count))
    if n >= policy.storm_at:
        pressure = SpawnPressure.STORM
    elif n >= policy.elevated_at:
        pressure = SpawnPressure.ELEVATED
    else:
        pressure = SpawnPressure.CLEAR
    return SpawnPressureVerdict(
        pressure=pressure,
        live_count=n,
        elevated_at=policy.elevated_at,
        storm_at=policy.storm_at,
    )


def count_spawns_per_holder(
    records: "list[tuple[str, str]]",
) -> dict[str, int]:
    """Fold (holder_id, effect_kind_value) records → {holder_id: spawn_count}. PURE.

    Each record is a `(holder_id, effect_kind)` pair the CALLER extracted from a
    journal entry (the run lineage id + the `effect_kind` tag the pretool outcome
    carries). Only `SPAWN`-effect records are counted; a record whose holder id is
    empty/None is dropped (an unattributable spawn cannot be charged to a holder).
    The kernel COUNTS; it never reads the journal — that I/O is the caller's, the
    same boundary discipline `breaker`/`liveness` keep.
    """
    counts: dict[str, int] = {}
    for rec in records:
        if not isinstance(rec, (tuple, list)) or len(rec) != 2:
            continue
        holder, kind = rec
        if not holder or str(kind) != EffectKind.SPAWN.value:
            continue
        h = str(holder)
        counts[h] = counts.get(h, 0) + 1
    return counts


def spawn_storm_holders(
    records: "list[tuple[str, str]]",
    policy: SpawnPressurePolicy = GENERIC_SPAWN_PRESSURE_POLICY,
) -> dict[str, SpawnPressureVerdict]:
    """The surfaceable fold: holders whose live SPAWN count is ELEVATED or a STORM.

    Folds the records per holder (`count_spawns_per_holder`), classifies each count
    (`classify_spawn_pressure`), and returns ONLY the holders worth surfacing
    (`verdict.surfaceable`). A CLEAR holder is omitted — the map is the
    fan-out-storm surface a supervisor/pulse reads, not a full census. PURE.
    """
    out: dict[str, SpawnPressureVerdict] = {}
    for holder, n in count_spawns_per_holder(records).items():
        v = classify_spawn_pressure(n, policy)
        if v.surfaceable:
            out[holder] = v
    return out


# ---------------------------------------------------------------------------
# The `[spawn_pressure]` config seam — the fan-out burst thresholds as data.
# ---------------------------------------------------------------------------


def policy_from_table(
    table: dict, *, base: SpawnPressurePolicy = GENERIC_SPAWN_PRESSURE_POLICY
) -> SpawnPressurePolicy:
    """Build a `SpawnPressurePolicy` from a parsed `[spawn_pressure]` table.

    Each named field overrides `base`; omitted fields inherit. The table is
    advisory-only: a bad value warns and keeps base at the config layer, and the
    decision fold only surfaces rows. It never denies a tool call or takes a lease.
    """
    if not isinstance(table, dict):
        raise ValueError(f"[spawn_pressure] must be a table, got {type(table).__name__}")
    known = {"elevated_at", "storm_at", "window_seconds", "window_hours"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[spawn_pressure] has unknown key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)}"
        )
    if "window_seconds" in table and "window_hours" in table:
        raise ValueError(
            "[spawn_pressure] declares both window_seconds and window_hours; pick one"
        )

    def _int(key: str) -> int:
        v = table[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"[spawn_pressure].{key} must be an integer, got {type(v).__name__}"
            )
        return int(v)

    elevated_at = base.elevated_at
    if "elevated_at" in table:
        elevated_at = _int("elevated_at")
    storm_at = base.storm_at
    if "storm_at" in table:
        storm_at = _int("storm_at")
    if "elevated_at" not in table and elevated_at > storm_at:
        elevated_at = storm_at
    window_seconds = base.window_seconds
    if "window_seconds" in table:
        window_seconds = _int("window_seconds")
    elif "window_hours" in table:
        window_seconds = _int("window_hours") * 60 * 60
    return SpawnPressurePolicy(
        elevated_at=elevated_at,
        storm_at=storm_at,
        window_seconds=window_seconds,
    )


def load_from_toml(
    path, *, base: SpawnPressurePolicy = GENERIC_SPAWN_PRESSURE_POLICY
) -> SpawnPressurePolicy:
    """Build a `SpawnPressurePolicy` from a `dos.toml` `[spawn_pressure]` table."""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("spawn_pressure")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
