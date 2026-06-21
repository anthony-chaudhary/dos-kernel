"""dos.drivers.sealed_acceptance — a `ScopeSource` whose external scope account is a
GIT-SEALED acceptance manifest (docs/390 Phase 1).

What this adds over `plan_scope`
================================

`PlanScopeSource` (the sibling reference source) cross-checks a run's *declared*
extent against an *expected* unit set read off `config` — a good Gap-B check
(docs/117 §5.3), but the expected set is just configuration: nothing pins WHEN it
was authored or proves it was not co-designed with the work after the fact.

`SealedAcceptanceScope` answers the docs/390 question: *what if the external scope
account is a manifest the worker committed BEFORE it started, and we want a
machine-checkable proof of that "before"?* The proof is **git ancestry** — no new
crypto, no new trust root. The seal anchor is the run's own **start commit**
(`LedgerState.start_sha`): the acceptance manifest must already be present *at that
commit*, read via `vcs.read_blob(start_sha, path)`. If it is, it was authored
before the work; if it is not, the seal is broken (the manifest was added during or
after the run — exactly the post-hoc co-design the seal exists to refuse).

The one property the seal buys, and the one it does NOT
======================================================

The seal buys **integrity** — provenance + timing, made a git-checkable fact. It
buys **no grounding**: a sealed-but-wrong manifest is still wrong. So this source
obeys the docs/390 no-go rules verbatim, and the seam already enforces the
load-bearing one structurally:

  > A `ScopeSource` can only ever WITHHOLD `COMPLETE` (push toward
  > `UNDERDECLARED`); it can never grant it.

So even a perfectly-sealed, fully-covered manifest does **not** make a run
`COMPLETE` — the positive done-bit still bottoms out in `resume`'s non-forgeable
"every declared step verified against git ancestry". This source only ever adds a
*refusal*: a broken seal, an unverifiable seal, a malformed manifest, or a sealed
required claim the run never declared each flip an otherwise-`COMPLETE` run to
`UNDERDECLARED`. It is the docs/390 sweet spot — a tamper-evident external scope
account — wired into the existing refuse-more-only machinery, adding the new
*check* (the seal) without one byte of new kernel *decision* logic.

What is deliberately NOT here (later docs/390 phases)
====================================================

  * **Per-rung effect adjudication.** A claim carries a `rung` (`oracle` / `witness`
    / `judge`); this minimal source uses only the claim's *identity* as a required
    extent unit (the same coverage check `plan_scope` does). Routing a `witness`
    claim through `effect_witness`, or a `judge` claim through `dos.judges`
    (advisory, fail-to-ABSTAIN), is docs/390 Phase 2–3.
  * **Authorship independence.** The seal proves *timing* (present at the run's start
    commit), not that a *different author* wrote it. Binding the manifest's
    declaring-commit author / sealing run-id to "not the worker" is a Phase-2
    refinement; this source proves the cheap, strong git anchor (before-the-work)
    and is honest that authorship is future.
  * **Append-only refinement.** docs/390 no-go rule 4 (monotone, tighten-only
    re-authoring of discovered scope) is not modelled yet; this source reads the
    single sealed version present at `start_sha`.

Why it is a driver
==================

It reads the manifest blob out of git (`read_blob`) — I/O a kernel verdict may not
do. The discipline that keeps it safe is the seam's, not purity: `run_scope`
fail-to-strict + `honest_under_floor` mean a raising / lying / broken source can
only ever withhold completion. The kernel imports nothing from here; the decision
is the pure `classify_sealed_acceptance` below, handed evidence gathered at the
boundary.

Wiring it
=========

Register under the `dos.scope_sources` entry-point group (name ``sealed-acceptance``);
a workspace names it in `dos.toml [completion] scope_sources = ["sealed-acceptance"]`
and points it at its manifest with `[completion] acceptance_manifest = "acceptance.toml"`
(a repo-root-relative git pathspec). The CLI boundary resolves it via
`scope_source.active_scope_sources` and threads the verdict into
`completion.classify`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import Optional

# Imports the kernel — never the other way round (the driver rule).
from dos.intent_ledger import LedgerState
from dos.scope_source import ScopeVerdict

SOURCE_NAME = "sealed-acceptance"


# ───────────────────────────── the manifest (data) ────────────────────────────
@dataclass(frozen=True)
class AcceptanceClaim:
    """One sealed acceptance clause: an id, the rung that will adjudicate it, and the
    effect it names. For this minimal source only ``id`` + ``required`` are
    load-bearing (the coverage check); ``rung``/``effect`` are carried for the later
    per-rung adjudication phases and for the operator surface."""

    id: str
    rung: str = "oracle"
    effect: str = ""
    required: bool = True


def parse_acceptance(blob: bytes) -> Optional[tuple[AcceptanceClaim, ...]]:
    """Parse a sealed acceptance manifest's bytes into claims, or ``None`` if malformed.

    Pure (TOML parse over bytes — no I/O). Returns ``None`` on any structural problem
    (not valid TOML, no `claim` array, a claim missing an `id`) — the caller
    (`classify_sealed_acceptance`) reads ``None`` as a *withhold* (a malformed sealed
    manifest cannot certify anything; the fail-to-strict direction). An EMPTY claim
    list parses to ``()`` (a manifest that imposes no required units — honest, nothing
    to contest), distinct from ``None`` (a broken manifest)."""
    try:
        doc = tomllib.loads(blob.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError):
        return None
    raw_claims = doc.get("claim")
    if raw_claims is None:
        # No [[claim]] table at all — treat as malformed (a manifest with no claims is
        # almost certainly a mistake; an intentionally-empty bar uses `claim = []`).
        return None
    if not isinstance(raw_claims, list):
        return None
    out: list[AcceptanceClaim] = []
    for c in raw_claims:
        if not isinstance(c, dict):
            return None
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            return None  # a claim with no usable id makes the bar unparseable
        rung = c.get("rung", "oracle")
        effect = c.get("effect", "")
        required = c.get("required", True)
        out.append(AcceptanceClaim(
            id=cid.strip(),
            rung=str(rung),
            effect=str(effect),
            required=bool(required),
        ))
    return tuple(out)


def required_ids(claims: tuple[AcceptanceClaim, ...]) -> tuple[str, ...]:
    """The ids of the REQUIRED claims — the sealed extent the run must have declared.
    Order-preserving + deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for c in claims:
        if c.required and c.id not in seen:
            seen.add(c.id)
            out.append(c.id)
    return tuple(out)


# ───────────────────────────── the seal evidence ──────────────────────────────
@dataclass(frozen=True)
class SealEvidence:
    """Boundary-gathered facts about a sealed manifest's integrity (the verdict's input).

    ``configured`` — was an acceptance manifest path wired for this source at all?
    (False → nothing to check → honest, the `plan_scope` "no external account" posture.)
    ``anchored``   — did the run carry a `start_sha` to verify the seal against?
                     (False → the seal is *unverifiable* → withhold, fail-to-strict.)
    ``sealed_blob``— the manifest's bytes AS OF the run's start commit, or ``None`` if
                     the manifest was not present there (the seal is BROKEN — the
                     criteria were not authored before the work). Always the
                     *committed* blob, never the working-tree copy, so a worker that
                     edits the manifest mid-run cannot move the bar.
    """

    configured: bool
    anchored: bool = False
    sealed_blob: Optional[bytes] = None


def classify_sealed_acceptance(
    ev: SealEvidence, declared_steps: tuple[str, ...]
) -> ScopeVerdict:
    """The pure verdict: does the SEALED manifest contest the declared extent?

    Returns a `ScopeVerdict` that can only ever push toward `UNDERDECLARED`
    (`extent_honest=False`); an honest vote never *grants* `COMPLETE` (the residual
    floor does that). The decision ladder, every dishonest rung a docs/390 no-go-rule
    refusal:

      1. not configured            → honest  (no manifest wired; nothing to contest)
      2. configured, not anchored  → WITHHOLD (cannot verify the seal — no start commit)
      3. anchored, blob absent      → WITHHOLD (broken seal: manifest not present before the work)
      4. blob present, unparseable → WITHHOLD (malformed sealed manifest — fail-to-strict)
      5. parsed, a required id      → WITHHOLD (under-declared: a sealed bar the run never
         missing from declared        put on the books — docs/117 Gap B, now tamper-evident)
      6. parsed, all covered        → honest  (every sealed required claim declared; the
                                        positive done-bit STILL comes from the residual floor)
    """
    if not ev.configured:
        return ScopeVerdict(
            extent_honest=True,
            reason="no acceptance manifest configured — declared extent not contested",
            source=SOURCE_NAME,
        )
    if not ev.anchored:
        return ScopeVerdict(
            extent_honest=False,
            reason=("cannot anchor the seal — the run carries no start commit, so "
                    "'authored before the work' is unverifiable; withholding COMPLETE"),
            source=SOURCE_NAME,
        )
    if ev.sealed_blob is None:
        return ScopeVerdict(
            extent_honest=False,
            reason=("acceptance manifest absent at the run's start commit — the seal is "
                    "broken (the criteria were not committed before the work began); "
                    "withholding COMPLETE"),
            source=SOURCE_NAME,
        )
    claims = parse_acceptance(ev.sealed_blob)
    if claims is None:
        return ScopeVerdict(
            extent_honest=False,
            reason=("the sealed acceptance manifest is malformed — withholding COMPLETE "
                    "(a manifest that cannot be parsed cannot certify done; fail-to-strict)"),
            source=SOURCE_NAME,
        )
    req = required_ids(claims)
    declared = set(declared_steps)
    missing = tuple(i for i in req if i not in declared)
    if missing:
        return ScopeVerdict(
            extent_honest=False,
            reason=(f"{len(missing)} sealed required claim(s) absent from the declared "
                    f"extent — the run under-declared against criteria sealed before it "
                    f"began"),
            source=SOURCE_NAME,
            missing=missing,
        )
    return ScopeVerdict(
        extent_honest=True,
        reason=(f"all {len(req)} sealed required claim(s) are in the declared extent "
                f"(seal intact, present at the run's start commit)"),
        source=SOURCE_NAME,
    )


# ───────────────────────────── the driver ─────────────────────────────────────
class SealedAcceptanceScope:
    """A `ScopeSource` whose external scope account is a git-sealed acceptance manifest.

    `name` is ``sealed-acceptance`` — the token a workspace names in `dos.toml
    [completion] scope_sources` and `dos doctor` lists. `scope_verdict` gathers the
    seal evidence out of git at the boundary, then defers the decision to the pure
    `classify_sealed_acceptance`.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        manifest_path: Optional[str] = None,
        *,
        vcs: object = None,
    ) -> None:
        """``manifest_path`` is the repo-root-relative git pathspec of the acceptance
        manifest (e.g. ``"acceptance.toml"``); injected here it takes precedence over
        ``config.acceptance_manifest``. ``vcs`` lets a caller / test inject a VCS
        backend; absent it, `scope_verdict` resolves one via `vcs.active_vcs(root=…)`.
        With no manifest path from either source, the source is inert (votes honest)."""
        self._manifest_path = manifest_path
        self._vcs = vcs

    def _resolve_path(self, config: object) -> Optional[str]:
        if self._manifest_path:
            return self._manifest_path
        raw = getattr(config, "acceptance_manifest", None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    def _resolve_vcs(self, config: object):
        if self._vcs is not None:
            return self._vcs
        from dos.vcs import active_vcs  # boundary I/O — allowed in a driver
        root = getattr(config, "root", None)
        if root is None:
            return None
        return active_vcs(root=root, cfg=config)

    def scope_verdict(self, state: LedgerState, config: object) -> ScopeVerdict:
        path = self._resolve_path(config)
        if not path:
            return classify_sealed_acceptance(
                SealEvidence(configured=False), state.declared_steps
            )
        start_sha = (getattr(state, "start_sha", "") or "").strip()
        if not start_sha:
            return classify_sealed_acceptance(
                SealEvidence(configured=True, anchored=False), state.declared_steps
            )
        vcs = self._resolve_vcs(config)
        sealed_blob = None
        if vcs is not None:
            sealed_blob = vcs.read_blob(start_sha, path)
        return classify_sealed_acceptance(
            SealEvidence(configured=True, anchored=True, sealed_blob=sealed_blob),
            state.declared_steps,
        )
