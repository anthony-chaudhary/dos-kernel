"""The admission-predicate seam — Axis 3 of hackability: pluggable safety hooks (ADM, docs/73).

The arbiter's admission logic — known-tree intersection plus shared/exclusive
lock compatibility, with the legacy ratio scorer still selectable by config — is the kernel's
**safety element**: it is what stops two agents editing the same files
concurrently. That logic used to be fixed. A workspace could not add its own
admission rule ("refuse a new lease when over the monthly token budget,"
"refuse a lease that would touch the orchestrator's own running code") without
forking the arbiter.

This module is the seam that lets it *register* one instead. An admission
predicate is a pure callable ``(request, live_lease, config) -> AdmissionVerdict``
resolved from the ``dos.predicates`` entry-point group (Phase 3). The arbiter
runs the built-in disjointness predicate **plus** any registered ones.

The one invariant that makes an *open* predicate set safe: **conjunctive-only**
=====================================================================================

This is the highest-risk axis — a buggy predicate that *loosens* admission could
let two agents collide, the exact failure the arbiter exists to prevent. The
guardrail is structural, not careful coding:

  > **A predicate may only REFUSE. It can never force-admit over a built-in
  > refusal.** Predicates compose conjunctively: admission requires the built-in
  > disjointness check **and** every registered predicate to admit. Adding a
  > predicate can only make admission *stricter*, never looser.

So the worst a buggy/malicious predicate can do is refuse too much (a visible,
safe-direction failure an operator notices immediately), never admit a collision.
The ``--force`` operator override stays the *only* thing that can overrule a
refusal — a predicate refusal is overridable by ``--force`` the same way a
disjointness refusal is; a predicate cannot itself force anything. There is
deliberately no return value that forces admission (`AdmissionVerdict` has only
``.admit()`` / ``.refuse(reason)`` — no "admit harder"), so the conjunctive-only
guarantee is enforced by the *shape of the type*, not by reviewer vigilance.

Purity & fail-closed
====================

A predicate is **pure**, exactly like the arbiter it runs inside (`arbiter.py`
"No I/O — `live_leases` is passed in, the decision is returned"): any I/O it
needs (reading a token-budget file) happens *before* the call, with the result
passed in via ``config`` or a pre-computed input — never inside the predicate
during arbitration. This mirrors how `pick_oracle` already does its I/O outside
the arbiter.

A predicate that *raises* is caught and converted to a **refuse** naming the
predicate (fail-closed) — the safe direction for a safety hook. This is the
*inverse* of the renderer rule (a renderer that raises degrades to ugly text,
because presentation is downstream of the kernel and can never mis-decide) and
is deliberate: a safety predicate that cannot answer must not admit. This is the
same posture as the design-law "oracle failure can only ADD refusals, never
remove one."

Pure stdlib + the kernel leaves it delegates to (`lane_overlap`) — no I/O, no
host names — so it sits in the kernel layer beside `arbiter`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable



@dataclass(frozen=True)
class AdmissionVerdict:
    """One predicate's answer: admit, or refuse with a reason.

    Frozen and two-valued by design — there is no "force admit" constructor, so
    a predicate is *structurally* incapable of overriding another's refusal
    (the conjunctive-only invariant, enforced by the type). The boolean state is
    read via the ``admitted`` property; the two constructors are ``.admit()`` and
    ``.refuse(reason)`` — the exact spelling the plan's north-star uses.

    ``reason`` is the operator-facing string a refusal carries (empty on an
    admit). ``reason_class`` optionally carries a typed `reason_class` token (a
    ``dos.reasons`` registry token, e.g. ``SELF_MODIFY``) so a refusal is not
    just prose but a verifiable/refusable/`dos man`-documented reason — the
    Axis-1 mechanism. Built-in predicates set it; a workspace predicate may
    leave it empty (its prose ``reason`` still surfaces).

    The stored field is named ``_admit`` (private) so the ergonomic ``.admit()``
    CONSTRUCTOR and the ``.admitted`` accessor do not collide with it — a public
    field named ``admit`` would shadow the classmethod of the same name. Callers
    read ``v.admitted`` (or just ``if not v.admitted``), never the underscore.
    """

    _admit: bool
    reason: str = ""
    reason_class: str = ""

    @property
    def admitted(self) -> bool:
        """True iff this verdict admits. The public read accessor for the state."""
        return self._admit

    @classmethod
    def admit(cls) -> "AdmissionVerdict":
        """An admit verdict — the predicate raised no objection to this lease."""
        return cls(_admit=True)

    @classmethod
    def refuse(cls, reason: str, *, reason_class: str = "") -> "AdmissionVerdict":
        """A refuse verdict carrying an operator-facing ``reason`` (and an
        optional typed ``reason_class`` token). The ONLY non-admit constructor —
        there is deliberately no force-admit (the conjunctive-only invariant)."""
        return cls(_admit=False, reason=reason, reason_class=reason_class)


@runtime_checkable
class AdmissionPredicate(Protocol):
    """The contract a workspace implements to add an admission rule.

    ``name`` is the human label `dos doctor` lists and a fail-closed refusal
    names. ``__call__`` is pure: it is handed the requested lease (lane/kind/
    tree), ONE already-live lease to check against, and the active config, and
    returns an `AdmissionVerdict`. It must do NO I/O — any data it needs is
    pre-computed and read off ``config`` (or a field the caller cached there).

    A predicate is called once per (request, live_lease) pair, the same shape
    the built-in disjointness check has (it compares the request against each
    live lease). A predicate that does not care about a specific lease admits.
    """

    name: str

    def __call__(self, request: "AdmissionRequest", live_lease: dict,
                 config: object) -> AdmissionVerdict:
        ...


@dataclass(frozen=True)
class AdmissionRequest:
    """The requested lease, as the pure datum a predicate sees.

    A small frozen value (not the arbiter's loose kwargs) so a predicate has a
    stable, documented shape to read — ``lane`` / ``kind`` / ``tree`` — without
    being handed the arbiter's internals. Built by `arbiter.arbitrate` from its
    ``requested_*`` args just before the predicate sweep.

    ``command`` / ``arg_values`` (both default-empty) carry the proposed CALL's
    agent-authored argument bytes — the Bash command string and the flattened
    string argument values — for a predicate that adjudicates the call's SHAPE
    (`call_shape.DeclaredCallShapePredicate`, docs/364) rather than only its file
    tree. They default empty so every existing construction site
    (`arbiter.arbitrate`, the lane-only callers, the tests) is byte-unchanged and
    the tree-only predicates (`DisjointnessPredicate`, `SelfModifyPredicate`)
    never read them. Both are agent-authored, both present at PRE — so a predicate
    reading them stays sound at the one moment a deny can prevent an effect. The
    PreToolUse sensor (`pretool_sensor.decide`) populates them from the event it
    already parses; a pure/test caller leaves them empty.
    """

    lane: str
    kind: str
    tree: tuple[str, ...]
    command: str = ""
    arg_values: tuple[str, ...] = ()
    mode: str = ""


class DisjointnessPredicate:
    """The built-in tree-disjointness predicate — today's fixed admission rule,
    now the FIRST registered predicate.

    Delegates the both-known scoring to a resolved `overlap_policy.OverlapPolicy`.
    The default is `LockModeOverlapPolicy`: EXCLUSIVE/EXCLUSIVE intersections
    refuse at zero tolerance while SHARED/SHARED intersections admit. The legacy
    `PrefixOverlapPolicy` is still selectable explicitly, where it is AND-ed under
    the deterministic prefix floor.

    The **empty-tree rules** (asymmetric on the lease side) are owned HERE, not in
    the policy — they are soundness invariants about *unknown blast radius*, not a
    *scoring* choice, so a swappable scorer never sees them (it cannot weaken the
    unknown-blast-radius refusal). Reproduced verbatim from `arbiter._lease_blocks`:
      * empty LEASE tree → does NOT block (a lease naming no blast radius cannot
        claim conflict).
      * empty REQUESTED tree vs a KNOWN lease tree → blocks (unknown blast
        radius is never safe).
      * both empty → does NOT block (lone-loop safe).
      * both known → delegate to the policy via `admissible_under_floor`.

    ``policy`` is the scorer for the both-known case. It defaults to the built-in
    `LockModeOverlapPolicy` (pure, no I/O). A boundary caller
    (`built_in_predicates`) resolves a workspace's declared `dos.overlap_policies`
    plugin and passes it in here — the resolve-at-the-boundary, I/O-free-hot-path
    discipline `SelfModifyPredicate`'s `runtime_files` already uses. Whatever the
    non-lock policy is, `admissible_under_floor` AND-s it under the unforgeable
    prefix floor, so a misbehaving scorer can only refuse-more, never admit a
    legacy prefix collision.
    """

    name = "disjointness"

    def __init__(self, policy=None) -> None:
        # Lazy import keeps the DAG (`overlap_policy` imports `lane_overlap`, the
        # same leaf `admission` already imports — no cycle, but keep it local so a
        # default-constructed predicate has zero extra import cost on the hot path).
        if policy is None:
            from dos.overlap_policy import LockModeOverlapPolicy
            policy = LockModeOverlapPolicy()
        self._policy = policy

    def __call__(self, request: AdmissionRequest, live_lease: dict,
                 config: object) -> AdmissionVerdict:
        from dos.overlap_policy import admissible_under_floor, lock_mode_decision
        requested_tree = list(request.tree)
        lease_tree = list(live_lease.get("tree") or [])
        if not lease_tree:
            return AdmissionVerdict.admit()
        if not requested_tree:
            return AdmissionVerdict.refuse(
                f"lane {request.lane!r} has an EMPTY tree (unknown blast "
                f"radius) and cannot share live lane "
                f"{live_lease.get('lane')!r} — unknown blast radius is never "
                f"safe to admit concurrently."
            )
        if getattr(self._policy, "name", "") == "lock_modes":
            ov = lock_mode_decision(
                requested_tree,
                lease_tree,
                requested_mode=request.mode,
                lease_mode=live_lease.get("mode", live_lease.get("lock_mode", "")),
            )
        else:
            ov = admissible_under_floor(self._policy, requested_tree, lease_tree, config)
        if ov.admissible:
            return AdmissionVerdict.admit()
        return AdmissionVerdict.refuse(
            f"lane {request.lane!r} cannot share live lane "
            f"{live_lease.get('lane')!r}: {ov.reason}."
        )


def run_predicates(
    predicates: list[AdmissionPredicate],
    request: AdmissionRequest,
    live_leases: list[dict],
    config: object,
) -> AdmissionVerdict:
    """Run the conjunction: every predicate against every live lease.

    Returns the **first refusal** encountered (conjunctive — first refuse wins,
    the conjunction short-circuits) or an admit if every predicate admits
    against every live lease. The order is stable and documented: for each live
    lease in turn, every predicate in ``predicates`` order is consulted; the
    first ``refuse`` returned is the verdict. (Lease-outer / predicate-inner
    mirrors the arbiter's inline per-lease sweep — `_lease_blocks` was checked
    for each live lease — so the FIRST refusing lease is reported, the same lease
    the inline code would have named.)

    A predicate that **raises** — OR returns anything that is not an
    `AdmissionVerdict` (a buggy plugin returning ``None`` / a dict / a look-alike
    object) — is caught and converted to a refuse naming the predicate
    (fail-closed): a safety hook that cannot give a well-typed answer must not
    admit. This NEVER propagates the exception and NEVER trusts a foreign object's
    truthiness: a buggy predicate degrades to a (safe-direction) refusal, it never
    crashes arbitration and never sneaks an admit through a duck-typed
    ``.admitted``. The type check is what makes "a predicate can only refuse"
    hold even against a predicate that does not return our type at all.

    With ``live_leases == []`` there is no lease to compare against, but the
    conjunction is NOT skipped: it runs once against a synthetic empty lease
    (``{}``) so that **request-absolute** predicates — ones that refuse based on
    the request alone, like `SelfModifyPredicate` (a self-modifying lease is a
    hazard whether or not anything else is live) — still fire on an otherwise
    idle repo. **Lease-relative** predicates (like `DisjointnessPredicate`) see
    the empty lease, hit their "empty lease tree ⇒ admit" branch, and contribute
    nothing — so a free lane with no leases still admits, exactly as before. This
    closes the idle-repo gap the adversarial review found: SELF_MODIFY is no
    longer silently bypassed when ``live_leases`` is empty. (A workspace predicate
    that wants to ignore the no-lease case simply admits when ``live_lease`` is
    falsy — `BudgetGuard` and `SelfModifyPredicate` both answer from the request,
    so they are unaffected by the empty sentinel.)
    """
    leases = live_leases if live_leases else [{}]
    for lease in leases:
        for pred in predicates:
            name = getattr(pred, "name", type(pred).__name__)
            try:
                verdict = pred(request, lease, config)
            except Exception as e:  # fail-closed: a predicate that raises refuses
                return AdmissionVerdict.refuse(
                    f"admission predicate {name!r} raised ({e!r}) — refusing "
                    f"fail-closed (a safety hook that cannot answer must not "
                    f"admit).",
                )
            # A predicate MUST return our `AdmissionVerdict`. Anything else (None,
            # a dict, a duck-typed look-alike) is fail-closed-refused — we never
            # consult a foreign object's `.admitted`, so no admit can leak through
            # a wrong return type (the conjunctive-only invariant must hold even
            # for a predicate that ignores the contract entirely).
            if not isinstance(verdict, AdmissionVerdict):
                return AdmissionVerdict.refuse(
                    f"admission predicate {name!r} returned a "
                    f"{type(verdict).__name__}, not an AdmissionVerdict — "
                    f"refusing fail-closed (a predicate that does not return the "
                    f"verdict type cannot be trusted to admit).",
                )
            if not verdict.admitted:
                return verdict
    return AdmissionVerdict.admit()


# ---------------------------------------------------------------------------
# Phase 3 — workspace predicate discovery via the `dos.predicates` entry-point
# group. Mirrors `render._discover_entry_point_renderers` exactly: load each
# registered predicate, append it AFTER the built-ins in the conjunction. The
# conjunctive runner only honors *refusals*, so a discovered predicate is
# structurally incapable of loosening admission — there is no "admit harder"
# return value to misuse. That is the safety contract of the open seam.
# ---------------------------------------------------------------------------

# The entry-point group a workspace registers a predicate under.
PREDICATE_ENTRY_POINT_GROUP = "dos.predicates"


def _discover_entry_point_predicates(*, _stderr=None) -> list[tuple[str, AdmissionPredicate]]:
    """Find workspace predicates registered under the `dos.predicates` group.

    A predicate plugin registers ``name = "pkg.module:PredicateClass"`` in its
    ``[project.entry-points."dos.predicates"]``. We load each, instantiate it,
    and return ``(entry_point_name, predicate)`` pairs in sorted-by-name order
    (stable, so `dos doctor` and the conjunction are deterministic).

    A plugin that fails to load (bad import, constructor raises) is skipped with
    a one-line stderr note rather than crashing every `dos arbitrate` (a broken
    third-party plugin is the operator's to fix, not a kernel fault) — the same
    posture `render._discover_entry_point_renderers` takes. There is no
    built-in-name-collision concern here (unlike renderers): predicates are not
    addressed by name, they are all simply appended to the conjunction, so a
    duplicate name cannot shadow a built-in's behavior — it would only add
    another refuse-only voice, which is always safe.
    """
    stderr = _stderr if _stderr is not None else sys.stderr
    out: list[tuple[str, AdmissionPredicate]] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=PREDICATE_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(PREDICATE_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash arbitration
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
            predicate = obj() if isinstance(obj, type) else obj
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: admission predicate plugin {ep.name!r} failed to "
                f"load ({e}); skipping",
                file=stderr,
            )
            continue
        out.append((ep.name, predicate))
    return out


def built_in_predicates(*, workspace=None, config=None) -> list[AdmissionPredicate]:
    """The always-on predicates, in conjunction order.

    Disjointness FIRST (the original fixed rule — its refusal is the one
    `--force` is documented to skip), then `SelfModifyPredicate` (the
    self-modification guard). Both are always present; a workspace's discovered
    predicates append AFTER these.

    Two ways to make the SELF_MODIFY guard **workspace-aware**, in precedence:

      ``config`` (PREFERRED, I/O-FREE) — a `SubstrateConfig` whose
        ``workspace`` facts were already gathered at build time
        (`config.gather_workspace_facts`). The guard reads the CACHED
        `config.kernel_runtime_files`, so NO disk access happens here. This is
        what lets `arbiter.arbitrate` thread the config it already holds and stay
        PURE while still scoping the guard to the served repo — the whole reason
        the facts live on the config (see `config.WorkspaceFacts`). A config whose
        facts are ``None`` (never gathered — a hand-built test config) falls
        through to the conservative full set, exactly as `workspace=None` does.

      ``workspace`` (LEGACY, performs I/O) — a bare path. Triggers the existence
        probe (`self_modify.existing_runtime_files`) inline. Kept for the
        `active_predicates(workspace=…)` boundary callers (CLI/MCP/doctor) that
        pass a path rather than a built config; their I/O is already boundary I/O.

    With NEITHER given, the guard uses the full static `_DISPATCH_RUNTIME_FILES`
    set — conservative: a `**/*` lane is treated as self-modifying when we cannot
    prove otherwise (the safe direction for a safety guard). `config` wins over
    `workspace` when both are passed (cached data beats a redundant probe).

    Imported lazily from `dos.self_modify` to keep the import graph a DAG
    (`self_modify` pulls `admission`; the list is rebuilt cheaply per call —
    these are tiny stateless objects).

    The **overlap policy** (the both-known disjointness scorer) is resolved HERE
    too, at the boundary, and threaded into `DisjointnessPredicate(policy=…)` — so
    the pure `arbitrate` never does the discovery I/O that resolving a third-party
    policy needs. With no `config` (or a config naming no policy / the built-in
    `lock_modes`), `active_overlap_policy` returns `LockModeOverlapPolicy` with NO
    discovery, so the default predicate list uses the sound S/X lock-mode rule.
    A workspace that declares `dos.toml [overlap] policy = "import-graph"` (or sets
    `config.overlap_policy_name`) gets its plugin resolved and AND-ed under the
    deterministic prefix floor inside the predicate.
    """
    from dos.self_modify import SelfModifyPredicate, existing_runtime_files
    from dos.overlap_policy import active_overlap_policy
    from dos.call_shape import DeclaredCallShapePredicate, EMPTY_CALL_SHAPE
    cached = getattr(config, "kernel_runtime_files", None) if config is not None else None
    if cached is not None:
        # I/O-free path: the config already probed the workspace at build time.
        guard = SelfModifyPredicate(runtime_files=tuple(cached))
    elif workspace is not None:
        # Legacy boundary path: probe the workspace now.
        guard = SelfModifyPredicate(runtime_files=existing_runtime_files(workspace))
    else:
        # Conservative: no workspace info → guard against the full static set.
        guard = SelfModifyPredicate()
    policy = active_overlap_policy(config=config)
    # The declared-call-shape guard (docs/364, OWASP ASI02) — appended LAST so it
    # can only ADD a refusal after the two structural guards, never displace them.
    # The ruleset is read off the already-loaded config (I/O-free, resolved at the
    # config-load boundary like `kernel_runtime_files`); a config that declares no
    # `[call_shape]` yields `EMPTY_CALL_SHAPE`, and the predicate short-circuits to
    # admit before touching any bytes — so the default conjunction is byte-identical
    # in VERDICT to the prior two-predicate list (only `dos doctor`'s name list grows).
    ruleset = getattr(config, "call_shape", None) if config is not None else None
    call_shape_guard = DeclaredCallShapePredicate(
        ruleset=ruleset if ruleset is not None else EMPTY_CALL_SHAPE)
    return [DisjointnessPredicate(policy=policy), guard, call_shape_guard]


def active_predicates(*, workspace=None, config=None, _stderr=None) -> list[AdmissionPredicate]:
    """The full conjunction a CALLER passes into `arbitrate`: built-ins THEN
    discovered plugins.

    This is the one place the order is composed, and it does ENTRY-POINT
    DISCOVERY (I/O) — so it is called at the CALL BOUNDARY (the CLI's
    `cmd_arbitrate`, `dos doctor`), NOT inside the pure `arbitrate` (whose
    `predicates=None` default is the now-config-aware `built_in_predicates`).
    Built-ins always lead (so a workspace plugin can only ADD a refuse-only voice
    after them, never displace the disjointness/self-modify guards); discovered
    plugins follow in sorted-by-name order.

    ``config`` (PREFERRED) forwards the built config so the SELF_MODIFY guard reads
    its CACHED workspace facts — no redundant probe. ``workspace`` (a bare path) is
    the legacy form that probes inline; both forward to `built_in_predicates`,
    where `config` wins. A boundary caller that already built the config (the CLI
    after `_apply_workspace`) should pass `config=cfg`; one that only has a path
    passes `workspace=`. Either way the I/O is boundary I/O, the same category as
    the entry-point discovery this function always does.
    """
    discovered = [p for _name, p in _discover_entry_point_predicates(_stderr=_stderr)]
    return built_in_predicates(workspace=workspace, config=config) + discovered


def active_predicate_names(*, _stderr=None) -> list[str]:
    """The names of every active predicate (built-in + discovered), in
    conjunction order — what `dos doctor` lists so an operator can see exactly
    what gates their arbiter (the predicate analogue of "see the active reason
    set")."""
    return [getattr(p, "name", type(p).__name__)
            for p in active_predicates(_stderr=_stderr)]
