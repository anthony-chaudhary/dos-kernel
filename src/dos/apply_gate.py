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

— and returns the first refusal. So this leaf is the same `classify(evidence,
policy) -> verdict` shape as every other kernel verdict: PURE (no subprocess, no
file, no clock — the evidence is gathered at the CLI boundary and frozen in), and
**refuse-MORE only** (two refusing verdicts conjoined can only ever block more than
either alone, never admit what one refused).

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

from dataclasses import dataclass, field

from dos import lock_modes, scope

#: The closed-vocabulary refusal token a refused apply carries. MISROUTE-class
#: ("work aimed at a lane it doesn't own"); declared as `dos.toml [reasons]` data
#: in any workspace that opts the gate in, never added to `reasons.BASE_REASONS`
#: (the litmus: a driver/opt-in policy's reason is data, not a kernel edit).
SCOPE_ESCAPE = "SCOPE_ESCAPE"


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
    """

    touched_files: frozenset[str]
    self_lane: str = ""
    self_tree: tuple[str, ...] = ()
    other_trees: tuple[tuple[str, ...], ...] = ()

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
      reason_class  — the typed refusal token (`SCOPE_ESCAPE`) on a refuse; "" on an
                      allow. Rides into the audited `--force` row and the decision
                      queue (a closed-vocabulary reason, never free-text drift).
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
      2. Sibling collision → REFUSE. `lock_modes.region_conflict` of the footprint
         vs each OTHER live lease's tree, under the sound `ratio_max = 0` floor
         (EXCLUSIVE↔EXCLUSIVE = intersect-at-all; docs/114 §A1, never the ⅓ window).
         A footprint that overlaps a held sibling region is blocked, naming the
         file(s) that collide.
      3. Otherwise → ALLOWED.

    Refuse-MORE only: steps 1 and 2 each only ever ADD a refusal, so the conjunction
    can never admit what either pure verdict refused (the `admission` conjunctive-
    only safety, here as two ANDed gates). The collision floor uses EXCLUSIVE↔
    EXCLUSIVE — the conservative write-vs-write reading appropriate to an apply
    (a commit is a write); SHARED-mode concurrency is the arbiter's to grant at
    lease time, not the apply-gate's to assume.
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

    # 2. Sibling collision — does the footprint overlap ANOTHER live lease's region?
    #    The sound floor: EXCLUSIVE (write) vs each held lease's EXCLUSIVE region,
    #    so any prefix intersection conflicts (ratio_max = 0). The footprint is the
    #    request tree; an empty `other_trees` (no siblings) skips this entirely.
    footprint = sorted(touched)
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

    # 3. Contained and collision-free — the write may land. `sgate.reason` already
    #    begins "write ALLOWED — …"; carry it verbatim (no double prefix).
    return ApplyDecision(
        allowed=True,
        reason=sgate.reason,
        scope_gate=sgate,
    )


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
