"""`lifecycle` — the plan-class taxonomy + transition triggers, AS DATA (docs/207 §5c).

The `job` repo's `class-cycle` hardcodes a 5-class taxonomy (ACTIVE / MAINTENANCE
/ PARK / TOMB / DRAFT) and 9 named triggers (T1..T9) — the plan-classification
lifecycle. That taxonomy is POLICY: a repo that only wants ACTIVE/DONE should not
inherit a job-shaped lifecycle. This module lifts the class set + the trigger list
into per-workspace `[lifecycle]` data, exactly as `[stamp]` lifted the ship
grammar and `[enumerate]` lifted the phase grammar.

The split (docs/207 §"less house-style"):

  * MECHANISM (the `dos-class-cycle` skill): evaluate triggers → build candidates
    deterministically → spawn a JUDGE-rung adjudicator (`dos.judges`, advisory,
    fail-to-abstain) to approve/defer each → apply gated transitions → log. Domain-
    free; the same shape for any taxonomy.
  * POLICY (this data): WHICH classes exist, WHICH transitions are legal, the
    trigger list, and the failsafes (per-cycle cap, per-plan cooldown, a veto
    class). All declared in `[lifecycle]`; a 2-class repo declares `active`/`done`.

The kernel carries the SHAPE + the validation (a transition must name known
classes; an unknown key raises); the consuming repo declares the taxonomy. The
judge *content* (the prompt) is a host `dos.judges` driver — forcing it generic
would re-couple the kernel (docs/207 §"what deliberately does NOT get genericized").

Pure stdlib — a near-leaf like `reasons`/`stamp`. No I/O in the verdict path; the
TOML read is at the `load_from_toml` boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleTransition:
    """One legal class transition + the trigger that proposes it.

    ``frm`` / ``to`` are class names (must be members of the declared class set).
    ``trigger`` is an opaque token the skill evaluates (the kernel never interprets
    it — the host's evaluator owns what `idle_30d` means). ``auto`` is whether a
    safe mechanical apply is allowed (gated, one commit) vs surface-for-a-human.
    """

    frm: str
    to: str
    trigger: str
    auto: bool = False

    def to_dict(self) -> dict:
        return {"from": self.frm, "to": self.to, "trigger": self.trigger, "auto": self.auto}


@dataclass(frozen=True)
class LifecyclePolicy:
    """The plan-class lifecycle, as data — the `[lifecycle]` table.

      * ``classes`` — the declared class set (e.g. `("active","done")` or the job
        5-class set). The FIRST class is the default for a freshly-declared plan.
      * ``transitions`` — the legal `LifecycleTransition`s; each names classes from
        ``classes`` and an opaque trigger token.
      * ``veto_class`` — a class whose plans are NEVER auto-transitioned (the job
        P0-veto): a high-priority plan a human must move by hand. Empty = no veto.
      * ``max_transitions_per_cycle`` — the per-cycle cap (the job daily cap), so a
        runaway judge cannot churn the whole portfolio in one tick. Default 5.
      * ``per_plan_cooldown_hours`` — a plan transitioned within this window is not
        a candidate again (the job 72h cooldown). Default 72.

    The defaults are GENERIC. A repo declares its own in `dos.toml [lifecycle]`.
    """

    classes: tuple[str, ...] = ("active", "done")
    transitions: tuple[LifecycleTransition, ...] = ()
    veto_class: str = ""
    max_transitions_per_cycle: int = 5
    per_plan_cooldown_hours: int = 72

    @property
    def default_class(self) -> str:
        """The class a freshly-declared plan starts in (the first declared class)."""
        return self.classes[0] if self.classes else ""

    def is_known_class(self, name: str) -> bool:
        return name in self.classes

    def legal_transition(self, frm: str, to: str) -> bool:
        """True iff `frm → to` is a declared legal transition."""
        return any(t.frm == frm and t.to == to for t in self.transitions)

    def to_dict(self) -> dict:
        return {
            "classes": list(self.classes),
            "transitions": [t.to_dict() for t in self.transitions],
            "veto_class": self.veto_class,
            "max_transitions_per_cycle": self.max_transitions_per_cycle,
            "per_plan_cooldown_hours": self.per_plan_cooldown_hours,
        }


# The generic default: two classes, one auto transition, no veto. A repo with a
# richer taxonomy declares it. This is the `[stamp]` GENERIC_ twin.
GENERIC_LIFECYCLE = LifecyclePolicy(
    classes=("active", "done"),
    transitions=(LifecycleTransition("active", "done", "all_phases_shipped", auto=True),),
)


def policy_from_table(
    table: dict, *, base: LifecyclePolicy = GENERIC_LIFECYCLE
) -> LifecyclePolicy:
    """Build a `LifecyclePolicy` from a parsed `[lifecycle]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted inherit. An unknown key
    raises (the `stamp.convention_from_table` posture). A transition that names a
    class NOT in the declared set raises (a typo'd class is a host mistake worth
    surfacing — the kernel validates the SHAPE, the repo declares the taxonomy).
    """
    if not isinstance(table, dict):
        raise ValueError(f"[lifecycle] must be a table, got {type(table).__name__}")
    known = {"classes", "transitions", "veto_class",
             "max_transitions_per_cycle", "per_plan_cooldown_hours"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[lifecycle] has unknown key(s) {sorted(unknown)}; known keys are {sorted(known)}"
        )

    classes = base.classes
    if "classes" in table:
        raw = table["classes"]
        if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) for x in raw) or not raw:
            raise ValueError("[lifecycle].classes must be a non-empty list of strings")
        classes = tuple(raw)
    class_set = set(classes)

    transitions = base.transitions
    if "transitions" in table:
        raw_t = table["transitions"]
        if not isinstance(raw_t, (list, tuple)):
            raise ValueError("[lifecycle].transitions must be a list of {from,to,trigger,auto} tables")
        out: list[LifecycleTransition] = []
        for entry in raw_t:
            if not isinstance(entry, dict):
                raise ValueError("[lifecycle].transitions entries must be tables")
            frm, to, trig = entry.get("from"), entry.get("to"), entry.get("trigger")
            if not all(isinstance(x, str) and x for x in (frm, to, trig)):
                raise ValueError(
                    "[lifecycle] transition needs string from/to/trigger; got "
                    f"{entry!r}"
                )
            if frm not in class_set or to not in class_set:
                raise ValueError(
                    f"[lifecycle] transition {frm!r}->{to!r} names a class not in "
                    f"the declared set {sorted(class_set)}"
                )
            auto = entry.get("auto", False)
            if not isinstance(auto, bool):
                raise ValueError("[lifecycle] transition `auto` must be a boolean")
            out.append(LifecycleTransition(frm=frm, to=to, trigger=trig, auto=auto))
        transitions = tuple(out)

    veto = base.veto_class
    if "veto_class" in table:
        if not isinstance(table["veto_class"], str):
            raise ValueError("[lifecycle].veto_class must be a string")
        veto = table["veto_class"]
        if veto and veto not in class_set:
            raise ValueError(
                f"[lifecycle].veto_class {veto!r} is not in the declared set {sorted(class_set)}"
            )

    def _int(key: str, current: int) -> int:
        if key not in table:
            return current
        v = table[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"[lifecycle].{key} must be an int, got {type(v).__name__}")
        return v

    return LifecyclePolicy(
        classes=classes,
        transitions=transitions,
        veto_class=veto,
        max_transitions_per_cycle=_int("max_transitions_per_cycle", base.max_transitions_per_cycle),
        per_plan_cooldown_hours=_int("per_plan_cooldown_hours", base.per_plan_cooldown_hours),
    )


def load_from_toml(
    path, *, base: LifecyclePolicy = GENERIC_LIFECYCLE
) -> LifecyclePolicy:
    """Build a `LifecyclePolicy` from a `dos.toml`'s `[lifecycle]` table.

    Returns ``base`` unchanged when the file is absent, has no `[lifecycle]` table,
    or `tomllib` is unavailable. A present-but-malformed table raises. Mirrors
    `stamp.load_from_toml` (incl. the `utf-8-sig` BOM strip)."""
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
    table = data.get("lifecycle")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
