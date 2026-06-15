"""The apply-gate — the binding diff turnstile, as a PURE verdict (docs/126 Phase 1).

DOS's first **enforcement point**, the keystone of docs/126: the verdict a
`dos`-mediated write (the commit, the staged diff) runs *at the moment the effect
would land*, so a write that escapes the lane the agent leased — or collides with a
sibling's live lease — is **refused before it enters history**, not graded after.
docs/114 named this the missing PEP ("a sound PDP with no PEP"); this leaf supplies
the decision the CLI `dos apply` boundary binds.

The whole design economy (docs/126 §2): this function writes **no new decision
logic.** It composes two pure verdicts the kernel already ships and replay-tests —

  1. `scope.gate`           — did the diff escape the held lane's DECLARED tree?
                              (IN_SCOPE / SCOPE_CREEP / WRONG_TARGET → allow/refuse)
  2. `lock_modes.region_conflict` — does the diff's footprint collide with ANOTHER
                              live lease's region, under the sound `ratio_max = 0`
                              floor (docs/114 §A1 — never the ⅓ ratio's admit-window)?
  3. the FENCE (docs/342 M2)  — is the generation this run holds SUPERSEDED by a
                              later grant on an overlapping region? (docs/114 §A2 —
                              Kleppmann's fencing token: a stale-paused holder that
                              wakes after a SCAVENGE must not write over a region
                              re-granted to another agent.)

— and returns the first refusal. So this leaf is the same `classify(evidence,
policy) -> verdict` shape as every other kernel verdict: PURE (no subprocess, no
file, no clock — the evidence is gathered at the CLI boundary and frozen in), and
**refuse-MORE only** (two refusing verdicts conjoined can only ever block more than
either alone, never admit what one refused).

The FENCE (rung 3) is the docs/342 M2 / docs/114 §A2 addition. The lease WAL hands a
holder a monotonic GENERATION (`lane_journal.lease_generations`, folded by
append-order — the same way `replay` reconstructs state), and the gate REFUSES a
write whose held generation is superseded by a LATER grant on an overlapping region.
The generation is checked AT THE GATE from the WAL, never trusted from the holder's
heartbeat (itself a self-report — §A2's sharpest point). It FAILS CLOSED: a footprint
that overlaps a live grant while this run resolves NO generation (paused-past-scavenge,
or never fenced) is refused — an unresolvable token is treated as superseded, never
waved through. Monotonic by construction (the fold only ever counts UP), so the rung
can only refuse-MORE.

Layer 1 (kernel). Imports `scope` + `lock_modes` + stdlib only — names no host, no
plan, no lane taxonomy (the lane's declared tree arrives as evidence the caller
resolved). The two design facts unique to this leaf:

* **The self-lease arrives as evidence, never derived here.** "Which lane does this
  run hold?" is boundary I/O (match the run's pid / loop-ts / run-id against the WAL
  lease set) — the caller resolves it and freezes `self_lane` / `self_tree` in, the
  same way `SelfModifyPredicate` receives its protected file set as constructor data.
  An empty `self_tree` is an UNKNOWN blast radius, not a zero one: the gate cannot
  certify containment against an undeclared lane, so it REFUSES (the conservative
  `_tree.lane_trees_disjoint` / `scope.classify` stance, carried to the gate).
* **`reason_class` is the typed, closed-vocabulary refusal token** — `SCOPE_ESCAPE`
  (category MISROUTE), declared as `dos.toml [reasons]` data, never a `reasons.py`
  edit (this is host policy, opt-in at the `dos apply` surface — not a built-in
  predicate). The token rides into the audited `--force` override and the operator's
  decision queue; it is empty on an allow.
"""
from __future__ import annotations

from dataclasses import dataclass

from dos import lock_modes, scope

#: The closed-vocabulary refusal token a refused apply carries. MISROUTE-class
#: ("work aimed at a lane it doesn't own"); declared as `dos.toml [reasons]` data
#: in any workspace that opts the gate in, never added to `reasons.BASE_REASONS`
#: (the litmus: a driver/opt-in policy's reason is data, not a kernel edit).
SCOPE_ESCAPE = "SCOPE_ESCAPE"

#: The closed-vocabulary refusal token the FENCE (docs/342 M2 / docs/114 §A2) carries.
#: STALE_CLAIM-class ("the holder's claim to this region is out of date"): a write
#: whose held generation is superseded by a later grant on an overlapping region — the
#: Kleppmann stale-fencing-token refusal. Declared as `dos.toml [reasons]` data in any
#: workspace that opts the gate in, NEVER added to `reasons.BASE_REASONS` (the same
#: litmus as SCOPE_ESCAPE: an opt-in policy's reason is data, not a kernel edit).
STALE_GENERATION = "STALE_GENERATION"


@dataclass(frozen=True)
class ApplyEvidence:
    """Everything `decide()` needs, gathered by the CALLER before the call.

    No git, no config read, no clock inside the verdict — the arbiter discipline
    (`scope.ScopeEvidence`, `liveness.ProgressEvidence`). The `dos apply` boundary
    runs `git diff --cached --name-only` (or `<base>..<head>`) for the footprint,
    resolves the run's OWN held lane from the WAL lease set, and freezes all of it
    here before handing it to the pure decision.

      touched_files — the repo-relative paths the proposed write changes (the staged
                      diff / the patch about to apply). The agent cannot forge which
                      files a diff touches — the unforgeable footprint. Empty = a
                      write of nothing, which escapes nothing and collides with
                      nothing → ALLOWED (the benign no-op floor).
      self_lane     — the lane THIS run holds (resolved at the boundary). Carried for
                      the operator-facing reason / JSON consumer; not a ladder input.
      self_tree     — the declared path globs of the lane this run holds
                      (`config.lanes.tree_for(self_lane)`). The containment target.
                      EMPTY = an unknown blast radius → the gate REFUSES a non-empty
                      diff (cannot certify containment against an undeclared lane).
      other_trees   — the declared trees of the OTHER live leases (every held lane
                      except this run's own), each a tuple of globs. The collision
                      floor checks the footprint against each. Empty = no siblings.
      self_generation — the fencing GENERATION (docs/342 M2 / docs/114 §A2) this run
                      holds, resolved at the boundary from the WAL
                      (`lane_journal.lease_generations`), NOT trusted from the holder's
                      heartbeat. ``None`` = this run resolves NO live generation for
                      its identity (it was scavenged while paused, or never fenced) →
                      the fence FAILS CLOSED against any overlapping live grant.
      other_generations — the fencing generation of each OTHER live lease, ALIGNED
                      1:1 with ``other_trees`` (index i's generation is the holder of
                      ``other_trees[i]``). A later grant on an overlapping region has a
                      strictly HIGHER generation (the fold counts up by append-order),
                      and that is exactly what supersedes this run's write. A short /
                      empty tuple (a tree with no resolved generation) is read as a
                      ``None`` generation for that lease — present-but-unfenced, which
                      cannot supersede (only a strictly-greater number fences).
    """

    touched_files: frozenset[str]
    self_lane: str = ""
    self_tree: tuple[str, ...] = ()
    other_trees: tuple[tuple[str, ...], ...] = ()
    self_generation: int | None = None
    other_generations: tuple[int | None, ...] = ()

    def __post_init__(self) -> None:
        # Normalize the footprint to a clean, forward-slashed frozenset so the
        # prefix test matches `_tree`/`scope` normalization on the tree side
        # (mirrors `scope.ScopeEvidence.__post_init__` exactly).
        cleaned = frozenset(
            (p or "").replace("\\", "/").strip()
            for p in self.touched_files
            if p and str(p).strip()
        )
        object.__setattr__(self, "touched_files", cleaned)


@dataclass(frozen=True)
class ApplyDecision:
    """The binding pre-effect decision: may this proposed write LAND?

    The `scope.ScopeGate` analogue for the full apply turnstile — a decision a
    consumer ACTS on (let the commit proceed / refuse it), carrying both the binding
    bit AND the legible distrust (which files escaped, why) so the refusal names its
    cause the way every DOS verdict does.

      allowed       — True iff the write is contained by the lane it holds AND
                      collides with no sibling lease; False iff it must be REFUSED
                      before the effect.
      reason        — one-line operator-facing summary; on a refuse it NAMES the
                      offending files (the escaping spill or the colliding region).
      reason_class  — the typed refusal token on a refuse (`SCOPE_ESCAPE` for an
                      escape/collision, `STALE_GENERATION` for a superseded fence —
                      docs/342 M2); "" on an allow. Rides into the audited `--force`
                      row and the decision queue (a closed-vocabulary reason, never
                      free-text drift).
      refused_files — the files that drove the refusal (empty on allow): the set the
                      consumer reports back ("these writes were refused — outside
                      lane X's tree" / "collide with a live lease").
      scope_gate    — the underlying `scope.ScopeGate`, carried so a consumer can
                      reach its `verdict` / `out_of_scope_files` / evidence without a
                      second call.
    """

    allowed: bool
    reason: str
    reason_class: str = ""
    refused_files: tuple[str, ...] = ()
    scope_gate: scope.ScopeGate | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "reason_class": self.reason_class,
            "refused_files": list(self.refused_files),
            "scope": self.scope_gate.to_dict() if self.scope_gate is not None else None,
        }


def decide(
    ev: ApplyEvidence,
    policy: scope.ScopeGatePolicy = scope.DEFAULT_GATE_POLICY,
) -> ApplyDecision:
    """Decide whether a PROPOSED write may land — the binding pre-effect gate. PURE.

    No subprocess, no file, no clock — `scope.gate`'s and `lock_modes`' purity,
    inherited by delegation (neither containment nor the collision floor is
    re-implemented here). The ladder, top to bottom — first refusal wins:

      0. Empty footprint → ALLOWED. A write of nothing escapes nothing and collides
         with nothing (the benign no-op floor `scope.gate` already returns).
      1. Declared-tree escape → REFUSE. `scope.gate(self_tree)`: a diff that spills
         outside the held lane's tree (SCOPE_CREEP) — or never enters it
         (WRONG_TARGET, which an EMPTY `self_tree` also yields: an undeclared lane is
         an unknown blast radius, refused) — is blocked, naming the spill.
      2. Stale fence (docs/342 M2 / docs/114 §A2) → REFUSE. For each OTHER live lease
         whose region OVERLAPS the footprint, if its generation is strictly GREATER
         than the generation THIS run holds — a later grant superseded this holder —
         the write is refused with STALE_GENERATION (the Kleppmann stale-token
         refusal). FAILS CLOSED: an overlapping live grant while this run holds NO
         generation (paused-past-scavenge / never fenced) is treated as superseded.
         Runs BEFORE the collision rung so a superseded write gets the PRECISE fence
         reason (the §A2 cause) rather than a generic collision.
      3. Sibling collision → REFUSE. `lock_modes.region_conflict` of the footprint
         vs each OTHER live lease's tree, under the sound `ratio_max = 0` floor
         (EXCLUSIVE↔EXCLUSIVE = intersect-at-all; docs/114 §A1, never the ⅓ window).
         A footprint that overlaps a held sibling region is blocked, naming the
         file(s) that collide.
      4. Otherwise → ALLOWED.

    Refuse-MORE only: steps 1–3 each only ever ADD a refusal, so the conjunction can
    never admit what any pure verdict refused (the `admission` conjunctive-only
    safety, here as ANDed gates). Reordering the fence before the collision rung is
    still refuse-MORE — it only re-LABELS a refusal both would otherwise produce with
    the more precise §A2 cause, never admits a write either would refuse. The
    collision floor uses EXCLUSIVE↔EXCLUSIVE — the conservative write-vs-write reading
    appropriate to an apply (a commit is a write); SHARED-mode concurrency is the
    arbiter's to grant at lease time, not the apply-gate's to assume.
    """
    touched = ev.touched_files

    # 0. Empty footprint — nothing to gate. A no-op write never escapes and never
    #    collides (mirrors `scope.gate`'s and `liveness`'s benign empty floor).
    if not touched:
        return ApplyDecision(
            allowed=True,
            reason="write ALLOWED — empty footprint, nothing to gate",
        )

    # 1. Declared-tree escape — does the diff stay inside the lane this run holds?
    #    `scope.gate` REFUSES SCOPE_CREEP + WRONG_TARGET by default; an empty
    #    `self_tree` yields WRONG_TARGET (the undeclared-lane unknown-blast-radius
    #    refuse), exactly the conservative stance we want at the gate.
    scope_ev = scope.ScopeEvidence(
        touched_files=touched,
        lane_tree=tuple(ev.self_tree),
        lane=ev.self_lane,
    )
    sgate = scope.gate(scope_ev, policy)
    if not sgate.allowed:
        # `sgate.reason` already begins "write REFUSED (VERDICT) — …"; carry it
        # verbatim rather than re-prefixing (no double "write REFUSED — write…").
        return ApplyDecision(
            allowed=False,
            reason=sgate.reason,
            reason_class=SCOPE_ESCAPE,
            refused_files=sgate.refused_files,
            scope_gate=sgate,
        )

    footprint = sorted(touched)

    # 2. Stale fence (docs/342 M2 / docs/114 §A2 — Kleppmann's fencing token). Is the
    #    generation THIS run holds superseded by a LATER grant on an OVERLAPPING
    #    region? The generations were folded from the WAL at the boundary
    #    (`lane_journal.lease_generations`), NOT trusted from the holder's heartbeat.
    #    A re-grant after a SCAVENGE takes a strictly higher number (the fold counts
    #    up by append-order), so `other_gen > self_gen` is exactly "a later holder
    #    owns this region now." FAILS CLOSED: if this run resolves NO generation
    #    (`self_generation is None`) it cannot prove it still holds the lane, so any
    #    overlapping live grant supersedes it. This is the rung that overrules the
    #    forgeable self-belief a stale-paused holder has about still holding the lease.
    fenced = _stale_fence(footprint, ev)
    if fenced is not None:
        named_files, other_gen = fenced
        named = ", ".join(named_files)
        held = "<none>" if ev.self_generation is None else str(ev.self_generation)
        return ApplyDecision(
            allowed=False,
            reason=(
                f"write REFUSED — stale fence: this run holds generation {held} but a "
                f"later grant (generation {other_gen}) holds an overlapping region "
                f"({named}); the lease was re-granted while this holder was paused"
            ),
            reason_class=STALE_GENERATION,
            refused_files=tuple(named_files),
            scope_gate=sgate,
        )

    # 3. Sibling collision — does the footprint overlap ANOTHER live lease's region?
    #    The sound floor: EXCLUSIVE (write) vs each held lease's EXCLUSIVE region,
    #    so any prefix intersection conflicts (ratio_max = 0). The footprint is the
    #    request tree; an empty `other_trees` (no siblings) skips this entirely.
    for other in ev.other_trees:
        if lock_modes.region_conflict(
            footprint,
            lock_modes.LockMode.EXCLUSIVE,
            list(other),
            lock_modes.LockMode.EXCLUSIVE,
        ):
            colliding = _colliding_files(footprint, other)
            named = ", ".join(colliding) if colliding else ", ".join(other)
            return ApplyDecision(
                allowed=False,
                reason=(
                    f"write REFUSED — footprint collides with a live lease's "
                    f"region ({named}); a sibling holds an overlapping write lock"
                ),
                reason_class=SCOPE_ESCAPE,
                refused_files=tuple(colliding) if colliding else tuple(),
                scope_gate=sgate,
            )

    # 4. Contained, un-fenced, and collision-free — the write may land. `sgate.reason`
    #    already begins "write ALLOWED — …"; carry it verbatim (no double prefix).
    return ApplyDecision(
        allowed=True,
        reason=sgate.reason,
        scope_gate=sgate,
    )


def _stale_fence(
    footprint: list[str], ev: ApplyEvidence
) -> tuple[list[str], str] | None:
    """The fencing verdict (docs/342 M2): is this write superseded on an overlap? PURE.

    Returns ``(named_files, other_generation)`` for the FIRST overlapping live lease
    whose generation supersedes this run's — ``named_files`` is the colliding files
    (else the lease's globs, for the operator-facing reason) — or ``None`` if no live
    grant fences the write. The supersede test, per overlapping live lease ``i``:

      * a write is fenced iff the footprint OVERLAPS ``other_trees[i]`` (the sound
        zero-tolerance region intersection, `lock_modes.region_conflict` EXCLUSIVE↔
        EXCLUSIVE — the same floor rung 3 uses) AND that lease's generation
        SUPERSEDES this run's;
      * supersede, fail-closed: if this run holds NO generation
        (``self_generation is None``), ANY overlapping live grant supersedes it (a
        holder that cannot prove its token is treated as stale — the §A2 direction).
        Else the lease supersedes iff its generation is a real int strictly GREATER
        than ``self_generation``. A lease with no resolved generation
        (``other_generations[i] is None`` / short tuple) is present-but-unfenced and
        canNOT supersede — only a strictly-greater number fences, so the rung is
        monotonic and refuse-MORE only (it never refuses on an equal/absent token).

    The generation is the WAL-folded token, NOT the holder's word — that is the whole
    §A2 point: the heartbeat is a self-report, the generation is adjudicated from the
    append-only grant record. ``other_generations`` is aligned 1:1 with
    ``other_trees``; a missing index reads as ``None`` (present-but-unfenced).

    DORMANT when no generation evidence was supplied at all (``other_generations`` is
    empty): the boundary folded no generations, so the fence has nothing to adjudicate
    and returns ``None`` — the collision rung (3) handles overlaps exactly as it did
    before M2. This is what keeps the gate byte-identical for a caller that does not
    yet supply generations (every pre-M2 `decide` call), so the fence only BINDS once
    the boundary resolves the WAL generations into the evidence — refuse-MORE, never a
    behavior change for the un-fenced path.
    """
    if not ev.other_generations:
        # No generation evidence resolved at the boundary → the fence is dormant; the
        # collision rung adjudicates overlaps. (A caller that opts INTO the fence
        # supplies `other_generations` aligned with `other_trees`.)
        return None
    self_gen = ev.self_generation
    for i, other in enumerate(ev.other_trees):
        other_gen = (
            ev.other_generations[i] if i < len(ev.other_generations) else None
        )
        if not lock_modes.region_conflict(
            footprint,
            lock_modes.LockMode.EXCLUSIVE,
            list(other),
            lock_modes.LockMode.EXCLUSIVE,
        ):
            continue  # no overlap — this live lease cannot fence this write
        # Overlap established. Does this live grant SUPERSEDE our generation?
        if self_gen is None:
            # Fail closed: we cannot prove we still hold the lane, and a live grant
            # overlaps — treat ourselves as superseded (the §A2 stale-holder case).
            # ANY overlapping live grant fences a holder that resolves no token.
            superseded = True
        elif isinstance(other_gen, int) and not isinstance(other_gen, bool):
            superseded = other_gen > self_gen
        else:
            superseded = False  # present but unfenced — cannot supersede a held token
        if superseded:
            colliding = _colliding_files(footprint, other)
            named = colliding if colliding else list(other)
            other_label = "<none>" if other_gen is None else str(other_gen)
            return named, other_label
    return None


def _colliding_files(footprint: list[str], lease_tree: tuple[str, ...]) -> list[str]:
    """The touched files that fall under one of ``lease_tree``'s prefixes (sorted).

    A legibility projection only — `region_conflict` already decided THAT the
    regions collide; this names WHICH files for the operator-facing reason, reusing
    `scope._file_in_tree`'s normalized-prefix containment so the naming matches the
    floor's own intersection test. Never re-decides the collision.
    """
    prefixes = [scope._tree.norm_tree_prefix(p) for p in lease_tree if p]
    if not prefixes:
        return []
    return sorted(f for f in footprint if scope._file_in_tree(f, prefixes))
