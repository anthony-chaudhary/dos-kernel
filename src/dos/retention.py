"""The retention policy — how much DOS scratch to keep, *as data*.

This is the direct answer to the question [`docs/94 §7`](../docs/94_checkpoints-and-recovery-from-slop.md)
left open and [`docs/106 §3.3`](../docs/106_garbage-collection-and-the-reachability-verdict.md)
specified: **retention is policy, so it is declared per-workspace and carried on
the config seam as data** — the `docs/HACKING.md` closed-enum→declared-data
pattern that already governs `[reasons]` and `[stamp]`.

Why a seam and not a constant
=============================

DOS has the garbage-collection *problem* in two shapes the operator feels (the
append-only lane journal that grows without bound, and the per-project `.dos/`
scratch — run-dirs, verdict sidecars, **audit reports** — that nobody auto-reaps).
docs/106 argues the collector itself is NOT new machinery: `replay`+`compact` is
already a correct mark-and-copy collector, missing only a *trigger*, a
*generational split*, and a *safe-point*. The trigger needs a *threshold*, and a
threshold is a number a host should be able to set (a host on a tiny disk keeps
little; a host that wants a long forensic tail keeps lots). That number is policy,
so it rides `SubstrateConfig` next to `.reasons`/`.stamp`/`.overlap_ratio_max`,
declarable in `dos.toml [retention]`, with a **generic default that is never zero**.

The floor is NOT these numbers
==============================

The load-bearing safety floor (docs/106 §5) is *reachability*, enforced by the
collector independently of any retention count: **a live lease is never collected,
ever**, regardless of how small the caps are set. A misconfigured `[retention]`
may keep *too much* (waste disk) — `False`-keep is tolerable — but it must never
cause a `False`-collect of state the kernel still needs. So this module carries
only the *recency / size* knobs; the "never reap a live lease" invariant lives in
the collector (the journal `compact` fold and the reaper's liveness gate), not
here. These numbers tune *how aggressively* to collect the already-collectable;
they cannot loosen *what* is collectable.

The shape
=========

A `RetentionPolicy` is the closed set of size/recency caps, plus one pure
predicate the kernel exposes for the trigger:

  * ``should_compact(entries, policy, *, now_ms)`` — `True` when the journal has
    more than ``journal_max_entries`` lines OR its oldest non-checkpoint entry is
    older than ``journal_max_age_days``. Reads ONLY the materialized list
    `read_all` already produced (no extra I/O) — the docs/106 §3.2 threshold,
    pure, so a driver fires it on a cadence the way `dos watch` fires
    `liveness.classify`.

The *reapers* that consume the keep-last-N caps (run-dirs / verdicts / audits)
live in the helper/driver layer (they do filesystem I/O — `os.scandir`, `unlink`),
never in this pure leaf; this module only declares the numbers and the one pure
threshold. That is the same kernel/driver split as `overlap_policy` (the seam is
data; the scorer that does work is a driver) — I/O at the boundary, data to the
pure core.

Two named constants ship in the package:

  * ``GENERIC_RETENTION`` — the generic default: generous caps, never zero. This
    is what every workspace gets out of the box (the floor is "never reap a live
    lease," which the collector enforces independently of these numbers).
  * ``UNBOUNDED_RETENTION`` — every cap effectively infinite + ``should_compact``
    always `False`. The opt-out for a host that wants today's keep-everything
    behaviour explicitly (and the byte-faithful baseline for any consumer built
    before this seam existed).

Pure stdlib — no third-party imports, no I/O (the `load_from_toml` half opens the
toml file at the call boundary, exactly as `stamp.load_from_toml` does, and is the
only function here that touches the disk). Leaf module: nothing in the kernel
imports *down* into a driver to use it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

# A day in milliseconds — the journal `ts` rung and `should_compact` both speak ms
# (the same unit `journal_delta`/`liveness` use), so the age cap is converted once
# here rather than scattering `* 86_400_000` at the call sites.
_MS_PER_DAY = 86_400_000

# A sentinel "no cap" for the keep-last-N / max-entries knobs. `None` means "keep
# everything on this axis" — distinct from `0` (which would mean "keep nothing",
# a foot-gun the floor forbids but the data type should still be able to express
# for an explicit opt-out). The predicate treats `None` as "this rung never fires."
NO_CAP: None = None


@dataclass(frozen=True)
class RetentionPolicy:
    """The per-workspace scratch-retention caps, as immutable data.

    Every field is optional-with-a-default; a host overrides only what it cares
    about in `dos.toml [retention]`. ``None`` on any cap means "unbounded on this
    axis" (keep everything) — NOT zero. The caps are size/recency tuning; the
    "never collect a live lease" floor is the collector's, not this object's.

      * ``journal_max_entries`` — compact the WAL when it grows past this many
        lines. ``None`` = never compact by size. (docs/106 §3.2)
      * ``journal_max_age_days`` — …or when its oldest non-checkpoint entry is
        older than this. ``None`` = never compact by age. (IDE checkpointers
        persist ~30d — the docs/94 §7 calibration anchor.)
      * ``runs_keep_last`` — reap `.dos/runs/` run-dirs beyond the newest N
        (liveness-gated by the reaper: a live run is kept even if old). ``None`` =
        keep all run-dirs.
      * ``verdicts_keep_last`` — reap `.dos/**/.verdict-*.json` beyond the newest
        N. A verdict is a point-in-time artifact with no liveness, so recency is
        the honest rule. ``None`` = keep all verdicts.
      * ``audits_keep_last`` — reap `.dos/audits/trajectory-audit-*` beyond the
        newest N. The scratch class the 2026-06-03 trajectory audit surfaced (NOT
        in docs/106 §1.2's original table — the audit's own output is itself an
        unbounded-growth source). Same recency rule as verdicts. ``None`` = keep
        all audit reports.
      * ``projections_compact`` — when ``True``, let `dos reindex` *rewrite* the
        central `~/.dos` projections to their live digest, not only append/prune.
        (docs/106 §3.4)
    """

    journal_max_entries: int | None = 5000
    journal_max_age_days: float | None = 30.0
    runs_keep_last: int | None = 200
    verdicts_keep_last: int | None = 500
    audits_keep_last: int | None = 200
    projections_compact: bool = True

    def with_overrides(self, **changes: Any) -> "RetentionPolicy":
        """Return a copy with the named caps replaced (thin `dataclasses.replace`)."""
        return replace(self, **changes)


# The generic default — generous, never zero. Every workspace gets this out of the
# box. The numbers are deliberately provisional (docs/106 §6: "generous-and-
# provisional, floored on 'never collect a live lease,' with the bench as the
# eventual evidence source"); the floor that makes them SAFE is the collector's
# reachability gate, not these values.
GENERIC_RETENTION = RetentionPolicy()

# The explicit keep-everything opt-out: every cap unbounded, `should_compact`
# always False. The byte-faithful "no retention seam" baseline — a consumer that
# installs this behaves exactly as the kernel did before `[retention]` existed.
UNBOUNDED_RETENTION = RetentionPolicy(
    journal_max_entries=NO_CAP,
    journal_max_age_days=NO_CAP,
    runs_keep_last=NO_CAP,
    verdicts_keep_last=NO_CAP,
    audits_keep_last=NO_CAP,
    projections_compact=False,
)


def should_compact(
    entries: list[Mapping[str, Any]],
    policy: RetentionPolicy = GENERIC_RETENTION,
    *,
    now_ms: int,
) -> bool:
    """The pure auto-compaction threshold (docs/106 §3.2).

    `True` when the journal is over ``journal_max_entries`` lines OR its oldest
    non-checkpoint entry is older than ``journal_max_age_days``. Reads ONLY the
    already-materialized ``entries`` list (the one `lane_journal.read_all`
    produces) plus the supplied ``now_ms`` clock — no I/O, so a driver fires it on
    a cadence the way `dos watch` fires `liveness.classify`. The clock is HANDED
    in (the way a pure verdict is handed a clock), never read here.

    A cap of ``None`` makes its rung never fire. Both caps ``None`` (or an empty
    journal) ⇒ `False`. The predicate is monotone in journal size: it can only ask
    to collect *more* as the log grows, never less — it never blocks a compaction
    the operator triggers by hand, it only decides when one should fire on its own.

    Note this is a *should-we* signal, not a *may-we* safety check: the SAFE point
    to actually run `compact` (the beat-anchor caveat, docs/106 §3.2(ii)) is the
    collector/driver's concern. A `True` here means "the journal is big/old enough
    to be worth collecting," not "it is safe to collect this instant."
    """
    n = len(entries)
    if not n:
        return False
    max_entries = policy.journal_max_entries
    if max_entries is not None and n > max_entries:
        return True
    max_age_days = policy.journal_max_age_days
    if max_age_days is not None:
        oldest = _oldest_non_checkpoint_ms(entries)
        if oldest is not None and (now_ms - oldest) > max_age_days * _MS_PER_DAY:
            return True
    return False


def plan_reap(
    entries: list[tuple[str, float]], keep_last: int | None
) -> list[str]:
    """The pure keep-last-N reaper plan: which entries to DROP by recency.

    ``entries`` is ``[(identifier, mtime_seconds), ...]`` — the reaper gathers it
    at the I/O boundary (`os.scandir`), this function decides. Keeps the ``keep_last``
    newest by ``mtime`` (ties broken by identifier, descending, so the order is
    total and deterministic) and returns the identifiers to drop, NEWEST-DROPPED
    first is NOT guaranteed — the returned list is the drop SET as a list; callers
    that want a stable display sort it. ``keep_last=None`` (unbounded) ⇒ drop
    nothing. ``keep_last=0`` ⇒ drop everything (an explicit "keep none"; the
    collector's reachability floor still spares anything live, but that gate is the
    I/O reaper's, applied BEFORE this — see `home.reap_scratch`).

    Pure: no I/O, no clock. This is the recency half of docs/106 §3.4 ("a verdict
    is a point-in-time artifact with no liveness, so recency is the honest rule"),
    factored out of the filesystem walk so it is unit-testable in isolation — the
    same kernel/driver split as `should_compact` (pure threshold) vs the driver
    that fires `compact`.
    """
    if keep_last is None:
        return []
    if keep_last <= 0:
        return [ident for ident, _ in entries]
    # Newest first: primary key mtime desc, secondary identifier desc (total order).
    ordered = sorted(entries, key=lambda em: (em[1], em[0]), reverse=True)
    return [ident for ident, _ in ordered[keep_last:]]


def _oldest_non_checkpoint_ms(entries: list[Mapping[str, Any]]) -> int | None:
    """The smallest ``ts`` over non-CHECKPOINT entries, or None if none carry one.

    Checkpoints are excluded because a CHECKPOINT line is the *snapshot* a prior
    compaction wrote, not original history — counting its age would make a
    freshly-compacted journal look stale and re-trigger immediately (a compaction
    loop). A line with no parseable integer ``ts`` is skipped (the same forgiving
    posture `journal_delta` takes on a malformed beat) rather than crashing the
    threshold.
    """
    oldest: int | None = None
    for e in entries:
        if e.get("op") == "CHECKPOINT":
            continue
        ts = e.get("ts")
        if not isinstance(ts, int):
            continue
        if oldest is None or ts < oldest:
            oldest = ts
    return oldest


# ---------------------------------------------------------------------------
# The `dos.toml [retention]` reader — the data attachment, file I/O at the boundary.
# Mirrors `stamp.load_from_toml` / `config.load_overlap_from_toml` in shape.
# ---------------------------------------------------------------------------

# The cap fields that take an int|None. `journal_max_age_days` is float|None and is
# coerced separately; `projections_compact` is a bool. Splitting them keeps the
# per-field coercion honest (an int cap rejects 1.5; the age accepts it).
_INT_CAP_KEYS = frozenset({
    "journal_max_entries", "runs_keep_last", "verdicts_keep_last", "audits_keep_last",
})
_FLOAT_CAP_KEYS = frozenset({"journal_max_age_days"})
_BOOL_KEYS = frozenset({"projections_compact"})
_ALLOWED_KEYS = _INT_CAP_KEYS | _FLOAT_CAP_KEYS | _BOOL_KEYS


def policy_from_table(
    table: Mapping[str, Any], *, base: RetentionPolicy = GENERIC_RETENTION
) -> RetentionPolicy:
    """Build a `RetentionPolicy` from a parsed `[retention]` table, over ``base``.

    A present key OVERRIDES the corresponding base field; an absent key inherits
    it. An UNKNOWN key raises `ValueError` (a typo'd cap — ``runs_keep_lsat`` —
    is a host mistake worth surfacing loudly, the same posture every other seam
    reader takes). A cap may be set to the TOML value ``-1`` or the string
    ``"none"`` to mean "unbounded on this axis" (the `None` sentinel — TOML has no
    null literal, so we accept those two spellings); any other negative is a
    mistake and raises. ``0`` is accepted verbatim (an explicit "keep nothing" the
    collector's reachability floor still overrides for live state).
    """
    if not isinstance(table, Mapping):
        raise ValueError(f"[retention] must be a table, got {type(table).__name__}")
    unknown = set(table) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"unknown [retention] key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(_ALLOWED_KEYS))})"
        )
    changes: dict[str, Any] = {}
    for key in _INT_CAP_KEYS & set(table):
        changes[key] = _coerce_cap(table[key], key, integral=True)
    for key in _FLOAT_CAP_KEYS & set(table):
        changes[key] = _coerce_cap(table[key], key, integral=False)
    for key in _BOOL_KEYS & set(table):
        raw = table[key]
        if not isinstance(raw, bool):
            raise ValueError(f"[retention] {key} must be a boolean, got {raw!r}")
        changes[key] = raw
    return replace(base, **changes)


def _coerce_cap(raw: Any, key: str, *, integral: bool) -> int | float | None:
    """Coerce one cap value: a number, or the `None`-sentinel spellings.

    TOML has no null, so ``-1`` and the (case-insensitive) string ``"none"`` both
    mean "unbounded on this axis." A non-negative number is taken as the cap; any
    other negative, or a non-numeric non-``"none"`` value, raises.
    """
    if isinstance(raw, str) and raw.strip().lower() == "none":
        return None
    if isinstance(raw, bool):  # bool is an int subclass — reject it for a cap
        raise ValueError(f"[retention] {key} must be a number or \"none\", got {raw!r}")
    if not isinstance(raw, (int, float)):
        raise ValueError(f"[retention] {key} must be a number or \"none\", got {raw!r}")
    if raw == -1:
        return None
    if raw < 0:
        raise ValueError(
            f"[retention] {key} must be >= 0 (or -1 / \"none\" for unbounded), got {raw!r}"
        )
    return int(raw) if integral else float(raw)


def load_from_toml(
    path: Path | str, *, base: RetentionPolicy = GENERIC_RETENTION
) -> RetentionPolicy:
    """Build a `RetentionPolicy` from a `dos.toml`'s `[retention]` table.

    Returns ``base`` unchanged when the file is absent, has no `[retention]` table,
    or `tomllib` is unavailable (Python < 3.11 with no `tomli`) — the declarative
    path is purely additive, so a missing/empty config degrades to the supplied
    base, never an error. A *present but malformed* `[retention]` table raises
    (`policy_from_table`), surfaced by `load_workspace_config`'s warn-and-fall-back.
    Mirrors `stamp.load_from_toml` / `reasons.load_from_toml` exactly.
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
    table = data.get("retention")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
