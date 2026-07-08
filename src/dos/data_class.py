"""The data-class policy — what KIND of data a path holds, *as data*.

This is the direct answer to the recurring "tag agent-trajectory data vs actual
product changes" problem: a repo's emission tree accretes two very different
things under the same `docs/_*` (or `.dos/`) roots — **agent-trajectory scratch**
(run READMEs, result envelopes, per-iteration verdicts, audit reports) that is
re-derivable and should age out, and **product artifacts** (plans, schemas,
design docs, baseline anchors) that are deliverables. A reaper can only treat the
two differently if it can *ask a path which it is*. Before this seam every
consumer hard-coded its own root list + filename rules (the LC3 sweeper's
`RUN_DIR_LOG_ROOTS`, the home reaper's `_scratch_classes`), so the classification
lived in N places and drifted. This module lifts it to ONE declared policy.

Why a seam and not a constant
=============================

WHICH paths are trajectory vs product is **policy** — it differs per workspace
(the reference userland app keeps its runs under `docs/_chained_runs/`; a foreign
repo keeps none, or keeps them elsewhere). So it rides `SubstrateConfig` next to
`.reasons`/`.stamp`/`.retention`, declarable in `dos.toml [data_class]`, with a
**generic default keyed only off `.dos/`-relative shapes** (the kernel's OWN
emissions) so DOS stays domain-free: the kernel names no host's `docs/` tree, the
host declares its own patterns. This is the `docs/HACKING.md`
closed-enum→declared-data pattern that already governs `[reasons]`/`[stamp]`/
`[retention]`.

The four classes
================

A path classifies into exactly one closed token (the `default_class` when no
pattern matches):

  * ``TRAJECTORY`` — re-derivable agent-run scratch (run dirs, result envelopes,
    iteration verdicts, audit reports). The class a retention reaper may age out.
  * ``AUDIT``      — a point-in-time audit/verdict artifact. Re-derivable like
    trajectory but called out separately because some audits are referenced
    (a reaper may keep more of them, or grace them longer).
  * ``BASELINE``   — a measure-then-change anchor (the DD ⚓ baselines.yaml world).
    Re-derivable in principle but load-bearing for the "freeze a baseline before
    you change code" discipline, so the default policy NEVER reaps it — it is
    surfaced for human REVIEW, not auto-collected.
  * ``PRODUCT``    — a deliverable (plan, schema, design doc, source). Never reaped.

The shape
=========

A `DataClassPolicy` is the closed set of per-class glob patterns plus one pure
classifier the kernel exposes:

  * ``classify(path) -> str`` — match the POSIX-normalized repo-relative path
    against each class's patterns in the fixed priority order
    TRAJECTORY → AUDIT → BASELINE → PRODUCT (first match wins), else
    ``default_class``. Pure, no I/O — a driver (the LC3 sweeper, the home reaper,
    a clutter audit) calls it per path the way `retention.plan_reap` is called per
    scratch class.

Patterns are gitignore-flavored globs: ``*`` matches within a path segment, ``**``
matches across segments (any depth), a trailing ``/`` or ``/**`` matches the dir
and everything under it. A bare ``foo`` is treated as ``foo`` AND ``foo/**`` (a
directory pattern matches its contents) — the intuition a host expects when they
write ``docs/_chained_runs`` and mean "that whole tree."

Two named constants ship in the package:

  * ``GENERIC_DATA_CLASS`` — the generic default: `.dos/`-relative patterns only,
    so a fresh workspace classifies the kernel's own emissions correctly and every
    host path falls through to ``PRODUCT`` until the host declares its own
    ``[data_class]`` patterns. Domain-free — names no host tree.
  * ``NONE_DATA_CLASS`` — every path → ``PRODUCT``. The opt-out / byte-faithful
    baseline for a consumer that wants no classification (everything is a
    deliverable, nothing is reapable by class).

Pure stdlib — no third-party imports, no I/O (the `load_from_toml` half opens the
toml file at the call boundary, exactly as `stamp.load_from_toml` /
`retention.load_from_toml` do, and is the only function here that touches the
disk). Leaf module: nothing in the kernel imports *down* into a driver to use it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

# The closed set of data-class tokens. A path is exactly one of these.
TRAJECTORY = "TRAJECTORY"
AUDIT = "AUDIT"
BASELINE = "BASELINE"
PRODUCT = "PRODUCT"

# The priority order the classifier walks: the first class whose patterns match
# wins. Trajectory before audit before baseline before product so the most
# aggressively-collectable class is checked first and a path that could read as
# either (an audit report under a run dir) lands in the more-reapable bucket.
_CLASS_ORDER = (TRAJECTORY, AUDIT, BASELINE, PRODUCT)

# The valid `default_class` values — any of the four tokens.
_VALID_CLASSES = frozenset(_CLASS_ORDER)


def _normalize(path: str) -> str:
    """Repo-relative path with forward slashes and no leading ``./`` or ``/``.

    The classifier speaks POSIX so a Windows caller (`docs\\_chained_runs\\...`)
    and a POSIX caller match the same patterns — the same `_rel`-with-forward-
    slashes normalization the LC3 sweeper already does at its I/O boundary.
    """
    s = str(path).replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one gitignore-flavored glob to an anchored regex.

    Rules (kept deliberately small and unit-tested, not a full gitignore engine):
      * ``**`` matches any number of path segments (including zero), so
        ``a/**/b`` matches ``a/b`` and ``a/x/y/b``.
      * ``*`` matches any run of non-``/`` characters (within one segment).
      * ``?`` matches a single non-``/`` character.
      * a trailing ``/`` means "this dir and everything under it" → the same as
        appending ``**``.
      * everything else is matched literally.
    The result is anchored at both ends (``fullmatch`` semantics) so a pattern
    describes the WHOLE relative path, not a substring.
    """
    pat = pattern.replace("\\", "/").strip()
    # A trailing slash → match the dir and its whole subtree.
    if pat.endswith("/"):
        pat = pat + "**"
    out: list[str] = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # ``**`` — any depth. Consume an immediately-following ``/`` so
                # ``a/**/b`` allows ``a/b`` (zero segments) as well as ``a/x/b``.
                j = i + 2
                if j < n and pat[j] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                else:
                    out.append(".*")
                    i = j
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _expand_dir_pattern(pattern: str) -> tuple[str, ...]:
    """Treat a pattern as the path it names AND that path's whole subtree.

    A host writing ``docs/_chained_runs`` (or the wildcarded ``docs/_*_baselines``)
    means "that tree," not "a file named exactly that." So a pattern expands to
    ``(p, p + "/**")`` — match the path itself, and anything under it. A single
    ``*`` stays within a segment, so the base pattern still names a *directory*
    whose contents we want included; the ``/**`` sibling supplies the subtree.

    A pattern that already spans depth (contains ``**``) or already names its
    subtree (ends with ``/``) is used as-is — appending ``/**`` would be redundant
    (``a/**`` already covers ``a/**/**``) or wrong (``a/`` already → ``a/**`` in
    `_glob_to_regex`).
    """
    p = pattern.replace("\\", "/").strip()
    if not p:
        return ()
    if "**" in p or p.endswith("/"):
        return (p,)
    return (p, p + "/**")


@dataclass(frozen=True)
class DataClassPolicy:
    """The per-workspace path → data-class rules, as immutable data.

    Each field is a tuple of glob patterns naming the paths in that class; an
    empty tuple means "no path matches this class here." A host overrides only the
    classes it cares about in `dos.toml [data_class]`. The patterns are matched in
    the fixed priority order TRAJECTORY → AUDIT → BASELINE → PRODUCT; the first
    class with a matching pattern wins, else ``default_class``.

      * ``trajectory_patterns`` — re-derivable agent-run scratch (run dirs,
        result envelopes, iteration verdicts).
      * ``audit_patterns`` — point-in-time audit / verdict artifacts.
      * ``baseline_patterns`` — measure-then-change anchors (NEVER auto-reaped by
        the default policy; surfaced for human review).
      * ``product_patterns`` — explicit product deliverables. Usually empty
        (everything unmatched is product via ``default_class``); present only when
        a host wants a path UNDER a trajectory root pinned as product (an explicit
        keep — checked last, so a more-specific trajectory pattern still wins; use
        a narrower trajectory pattern if you need product to win).
      * ``default_class`` — the class for a path no pattern matches (default
        ``PRODUCT``: unknown ⇒ treat as a deliverable, the safe direction — a
        reaper keying off this class can never reap an unclassified path).
    """

    trajectory_patterns: tuple[str, ...] = ()
    audit_patterns: tuple[str, ...] = ()
    baseline_patterns: tuple[str, ...] = ()
    product_patterns: tuple[str, ...] = ()
    default_class: str = PRODUCT

    def _compiled(self) -> dict[str, tuple[re.Pattern[str], ...]]:
        """Per-class compiled regex tuples (dir-expanded). Recomputed per call —
        the policy is small and classify is not a hot loop; keeping it stateless
        avoids caching on a frozen dataclass."""
        raw = {
            TRAJECTORY: self.trajectory_patterns,
            AUDIT: self.audit_patterns,
            BASELINE: self.baseline_patterns,
            PRODUCT: self.product_patterns,
        }
        compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
        for cls, patterns in raw.items():
            regexes: list[re.Pattern[str]] = []
            for p in patterns:
                for expanded in _expand_dir_pattern(p):
                    regexes.append(_glob_to_regex(expanded))
            compiled[cls] = tuple(regexes)
        return compiled

    def classify(self, path: str) -> str:
        """Pure classifier: map a repo-relative path to its data-class token.

        Normalizes the path to POSIX, then checks each class's patterns in the
        fixed priority order; returns the first class whose pattern matches, or
        ``default_class`` if none do. No I/O.
        """
        rel = _normalize(path)
        compiled = self._compiled()
        for cls in _CLASS_ORDER:
            for rx in compiled[cls]:
                if rx.match(rel):
                    return cls
        return self.default_class

    def with_overrides(self, **changes: Any) -> "DataClassPolicy":
        """Return a copy with the named fields replaced (thin `dataclasses.replace`)."""
        return replace(self, **changes)


# The generic default — `.dos/`-relative patterns ONLY, so DOS stays domain-free.
# A fresh workspace classifies the kernel's own emissions (run-dirs, verdict
# sidecars, trajectory-audit reports under `.dos/`) and everything else falls
# through to PRODUCT until the host declares its own `[data_class]` patterns for
# its `docs/` tree. Names no host path.
GENERIC_DATA_CLASS = DataClassPolicy(
    trajectory_patterns=(
        # Bare dir names — dir-expansion matches the dir AND its whole subtree
        # (`.dos/runs` ⇒ `.dos/runs` + `.dos/runs/**`), so both the scratch dir
        # itself and the run-dirs inside it classify as TRAJECTORY.
        ".dos/runs",
        ".dos/fanout_runs",
        ".dos/chained_runs",
        ".dos/dispatch_loops",
        ".dos/verdicts",
    ),
    audit_patterns=(
        ".dos/audits",
        ".dos/picker_audits",
        ".dos/**/*.verdict-*.json",  # a verdict sidecar anywhere under .dos/
    ),
    baseline_patterns=(
        ".dos/baselines",
    ),
    product_patterns=(),
    default_class=PRODUCT,
)

# The explicit opt-out: every path classifies as PRODUCT, no class-based handling.
# The byte-faithful "no data-class seam" baseline — a consumer that installs this
# sees nothing as trajectory/audit/baseline, so a class-keyed reaper reaps nothing.
NONE_DATA_CLASS = DataClassPolicy(
    trajectory_patterns=(),
    audit_patterns=(),
    baseline_patterns=(),
    product_patterns=(),
    default_class=PRODUCT,
)


# ---------------------------------------------------------------------------
# The `dos.toml [data_class]` reader — the data attachment, file I/O at the
# boundary. Mirrors `stamp.load_from_toml` / `retention.load_from_toml` in shape.
# ---------------------------------------------------------------------------

# The pattern-list fields (each a tuple-of-strings) and the scalar default_class.
_PATTERN_KEYS = frozenset({
    "trajectory_patterns", "audit_patterns", "baseline_patterns", "product_patterns",
})
_ALLOWED_KEYS = _PATTERN_KEYS | {"default_class"}


def _str_tuple(value: object, key: str) -> tuple[str, ...]:
    """Coerce a TOML value to a tuple of strings, or raise naming the bad key.

    Accepts a single string (wrapped) or a list of strings. Anything else — a
    number, a nested table, a list with a non-string element — is a host mistake
    worth surfacing loudly at load (the same posture `stamp._str_tuple` takes).
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"[data_class].{key} must be a list of strings; got a "
                    f"{type(item).__name__} element ({item!r})"
                )
            out.append(item)
        return tuple(out)
    raise ValueError(
        f"[data_class].{key} must be a string or list of strings, "
        f"got {type(value).__name__}"
    )


def policy_from_table(
    table: Mapping[str, Any], *, base: DataClassPolicy = GENERIC_DATA_CLASS
) -> DataClassPolicy:
    """Build a `DataClassPolicy` from a parsed `[data_class]` table, over ``base``.

    A present key OVERRIDES the corresponding base field (override, not merge — a
    host that declares ``trajectory_patterns`` gets exactly those, not those plus
    the base's); an absent key inherits it. An UNKNOWN key raises `ValueError` (a
    typo'd field — ``trajctory_patterns`` — is a host mistake worth surfacing
    loudly, the same posture every other seam reader takes). ``default_class``
    must be one of the four tokens.
    """
    if not isinstance(table, Mapping):
        raise ValueError(f"[data_class] must be a table, got {type(table).__name__}")
    unknown = set(table) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"unknown [data_class] key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(_ALLOWED_KEYS))})"
        )
    changes: dict[str, Any] = {}
    for key in _PATTERN_KEYS & set(table):
        changes[key] = _str_tuple(table[key], key)
    if "default_class" in table:
        raw = table["default_class"]
        if not isinstance(raw, str):
            raise ValueError(
                f"[data_class].default_class must be a string, got {raw!r}"
            )
        if raw not in _VALID_CLASSES:
            raise ValueError(
                f"[data_class].default_class must be one of "
                f"{', '.join(sorted(_VALID_CLASSES))}, got {raw!r}"
            )
        changes["default_class"] = raw
    return replace(base, **changes)


def load_from_toml(
    path: Path | str, *, base: DataClassPolicy = GENERIC_DATA_CLASS
) -> DataClassPolicy:
    """Build a `DataClassPolicy` from a `dos.toml`'s `[data_class]` table.

    Returns ``base`` unchanged when the file is absent, has no `[data_class]`
    table, or `tomllib` is unavailable (Python < 3.11 with no `tomli`) — the
    declarative path is purely additive, so a missing/empty config degrades to the
    supplied base, never an error. A *present but malformed* `[data_class]` table
    raises (`policy_from_table`), surfaced by `load_workspace_config`'s
    warn-and-fall-back. Mirrors `stamp.load_from_toml` / `retention.load_from_toml`
    exactly.
    """
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("data_class")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
