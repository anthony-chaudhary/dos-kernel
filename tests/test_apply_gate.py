"""The apply-gate pure verdict (docs/126 Phase 1) — DOS's first enforcement point.

`apply_gate.decide` is the binding diff turnstile: the verdict a `dos`-mediated
write runs at the moment the effect would land, so a write that escapes the held
lane's declared tree — or collides with a sibling's live lease — is REFUSED before
it enters history. docs/114 named this the missing PEP; this leaf supplies the
decision the CLI `dos apply` boundary binds.

The whole point of the design (docs/126 §2): NO new decision logic — `decide`
composes two pure verdicts the kernel already replay-tests (`scope.gate` for
declared-tree escape, `lock_modes.region_conflict` for sibling collision) and
returns the first refusal. These tests pin:

  * the ladder (empty → allow; escape → refuse SCOPE_ESCAPE; collision → refuse;
    contained-and-clear → allow);
  * that an EMPTY self_tree (undeclared lane) fails CLOSED — an unknown blast
    radius is refused, never admitted;
  * that the gate is refuse-MORE only (the conjunction never admits what either
    pure verdict refused);
  * purity (no I/O — replay-testable on frozen evidence, like `scope.gate`);
  * the typed closed-vocabulary reason_class on every refuse, "" on every allow.
"""

from __future__ import annotations

from dos import apply_gate
from dos.apply_gate import ApplyDecision, ApplyEvidence, SCOPE_ESCAPE, decide


def _ev(touched, *, self_lane="docs", self_tree=("docs/**",), other_trees=()):
    """An ApplyEvidence with a docs-lane shape; override per test."""
    return ApplyEvidence(
        touched_files=frozenset(touched),
        self_lane=self_lane,
        self_tree=tuple(self_tree),
        other_trees=tuple(tuple(t) for t in other_trees),
    )


# ---------------------------------------------------------------------------
# 0. The empty-footprint floor — a write of nothing escapes/collides with nothing.
# ---------------------------------------------------------------------------


def test_empty_footprint_is_allowed():
    """No files touched → ALLOW (the benign no-op floor `scope.gate` returns)."""
    d = decide(_ev([]))
    assert isinstance(d, ApplyDecision)
    assert d.allowed is True
    assert d.reason_class == ""
    assert d.refused_files == ()
    assert d.reason.startswith("write ALLOWED")


# ---------------------------------------------------------------------------
# 1. Declared-tree escape — the keystone: a write outside the held lane is refused.
# ---------------------------------------------------------------------------


def test_contained_write_is_allowed():
    """Every touched file inside the held lane's tree → ALLOW."""
    d = decide(_ev(["docs/readme.md", "docs/sub/x.md"]))
    assert d.allowed is True
    assert d.reason_class == ""
    assert d.refused_files == ()


def test_escaping_write_is_refused_scope_escape():
    """Held lane `docs/**`, but the patch also writes `src/y.py` → REFUSE.

    The docs/126 north-star, pre-effect: the agent leased docs but its diff escapes
    into src; the gate REFUSES that write before it can land, naming the spill and
    carrying the typed SCOPE_ESCAPE token (never free-text drift).
    """
    d = decide(_ev(["docs/readme.md", "src/y.py"]))
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE
    assert "src/y.py" in d.refused_files
    assert d.reason.startswith("write REFUSED")
    # The legible distrust: the underlying scope verdict is carried for a consumer.
    assert d.scope_gate is not None and d.scope_gate.allowed is False


def test_total_miss_is_refused():
    """Held lane `docs/**`, the WHOLE diff lands in src (nothing in docs) → REFUSE.

    `scope.gate` calls this WRONG_TARGET (the stamp names a lane the diff never
    entered); the apply-gate blocks it the same as a partial escape.
    """
    d = decide(_ev(["src/a.py", "src/b.py"]))
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE


# ---------------------------------------------------------------------------
# 2. Fail-closed — an undeclared (empty) lane tree is an UNKNOWN blast radius.
# ---------------------------------------------------------------------------


def test_empty_self_tree_with_real_diff_is_refused():
    """No declared tree for the held lane + a non-empty diff → REFUSE (fail-closed).

    The conservative `_tree.lane_trees_disjoint` / `scope.classify` stance carried to
    the gate: we cannot certify containment against a lane that declared no tree, so
    a real write is refused rather than waved through. This is the boundary's signal
    that the self-lease could not be resolved — the gate must not admit on ignorance.
    """
    d = decide(_ev(["src/y.py"], self_lane="", self_tree=()))
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE


def test_empty_self_tree_with_empty_diff_still_allowed():
    """No declared tree but ALSO no files touched → ALLOW (the no-op floor wins).

    The empty-footprint check precedes the containment ladder: a write of nothing is
    never refused, even against an undeclared lane (it cannot escape or collide).
    """
    d = decide(_ev([], self_lane="", self_tree=()))
    assert d.allowed is True
    assert d.reason_class == ""


# ---------------------------------------------------------------------------
# 3. Sibling collision — a footprint overlapping ANOTHER live lease is refused.
# ---------------------------------------------------------------------------


def test_collision_with_live_sibling_is_refused_at_the_floor():
    """In the held lane's OWN tree, but a sibling holds an overlapping region → REFUSE.

    The run holds `src/**` and writes `src/dos/z.py` — contained in its own lane, so
    `scope.gate` allows it. But a sibling lease holds `src/dos/**`; the sound floor
    (`lock_modes.region_conflict`, ratio_max = 0, EXCLUSIVE↔EXCLUSIVE) refuses the
    write before two writers can clobber the shared region — the collision-PREVENTION
    that `verify` could only DETECT after a lost update (docs/114 §A1 case (i)).
    """
    d = decide(_ev(
        ["src/dos/z.py"],
        self_lane="src", self_tree=("src/**",),
        other_trees=[("src/dos/**",)],
    ))
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE
    assert "src/dos/z.py" in d.refused_files
    assert "collides with a live lease" in d.reason


def test_no_collision_with_disjoint_sibling_is_allowed():
    """Contained in the held lane AND disjoint from every sibling → ALLOW."""
    d = decide(_ev(
        ["src/dos/z.py"],
        self_lane="src", self_tree=("src/**",),
        other_trees=[("docs/**",), ("tests/**",)],
    ))
    assert d.allowed is True
    assert d.reason_class == ""
    assert d.refused_files == ()


def test_escape_is_checked_before_collision():
    """A diff that BOTH escapes its lane AND would collide → the escape reason wins.

    Ladder order (escape step 1, collision step 2): the first refusal short-circuits.
    Both are SCOPE_ESCAPE-class refusals, so the token is identical; this pins that
    the escape is reported (its `scope_gate` is the refusing one), not the collision.
    """
    d = decide(_ev(
        ["src/y.py"],
        self_lane="docs", self_tree=("docs/**",),
        other_trees=[("src/**",)],
    ))
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE
    # The escape (scope) step refused, so the carried scope_gate is the refuser.
    assert d.scope_gate is not None and d.scope_gate.allowed is False
    assert "src/y.py" in d.refused_files


# ---------------------------------------------------------------------------
# 4. Purity + serialization (the verdict is replay-safe and JSON-renderable).
# ---------------------------------------------------------------------------


def test_decision_is_serializable():
    """`to_dict` carries the bit, the reason, the typed class, and the named spill."""
    d = decide(_ev(["src/y.py"]))
    out = d.to_dict()
    assert out["allowed"] is False
    assert out["reason_class"] == SCOPE_ESCAPE
    assert "src/y.py" in out["refused_files"]
    assert out["scope"] is not None  # the underlying scope verdict is carried


def test_decide_is_pure_idempotent():
    """Calling twice on the same frozen evidence yields the same verdict (no I/O)."""
    ev = _ev(["src/y.py"])
    a, b = decide(ev), decide(ev)
    assert (a.allowed, a.reason_class, a.refused_files) == (
        b.allowed, b.reason_class, b.refused_files)


def test_backslash_footprint_is_normalized():
    """A Windows-style `docs\\x.md` path normalizes to forward-slash before the test.

    Mirrors `scope.ScopeEvidence` normalization, so containment is decided on the
    same footing cross-platform — a backslash diff against a `docs/**` lane is
    correctly IN_SCOPE, not a spurious escape.
    """
    d = decide(ApplyEvidence(
        touched_files=frozenset({"docs\\x.md"}),
        self_lane="docs", self_tree=("docs/**",)))
    assert d.allowed is True
