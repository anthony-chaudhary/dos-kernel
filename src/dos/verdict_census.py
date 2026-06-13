"""The verdict-usage census — *which kernel verbs actually earn their keep?* (issue #20).

DOS ships ~70 CLI verbs and a wide syscall surface, but "which of them this
repo's own operation (or a fleet's) actually invokes" was an ANECDOTE — the
numbers in issue #20 took two hand audits to produce. Two telemetry sinks
exist, and neither had a per-verb reader:

    verdict_journal   (docs/262)  the kernel's own adjudications — every verify /
                                  liveness / efficiency / … verdict, keyed by `syscall`.
    hook_observation  (docs/297)  per-call hook telemetry — every pretool / posttool /
                                  stop firing, keyed by `verb`.

`dos observe` folds the FIRST into per-syscall verdict counts; `dos-stats`
folds the SECOND into an intervention rate. Neither answers the census
question: across BOTH logs, **how many times did each verb fire — and which
verbs never fired at all?** A never-fired list is the whole point (issue #20's
orphan set: `notify reward breaker resume improve reconcile productivity
enumerate` — surfaces consumed by neither CI/release wiring nor the userland
loop), and a never-fired verb leaves NO trace to count, so it can only be found
by subtracting the fired set from a KNOWN UNIVERSE.

The universe is **derived, never hand-listed** (issue #20, the no-rot
direction): `census_verbs()` walks the live CLI subparser registry, so a new
`dos` verb joins the denominator the day it is registered — there is no second
list to keep in sync (the roster-is-the-directory-listing rule, restated for
the verb surface). What a bare verb count cannot tell you — is a silent verb a
real orphan or a read-only projection that mints no verdict by design? — is
answered by `VERDICT_BEARING`: the declared set of verdict-emitting verbs
(`verdict_journal.KNOWN_SYSCALLS` plus the CLI verbs that classify a verdict
but are not yet journal-wired — the issue's orphans). A never-fired verb is an
ORPHAN only if it is verdict-bearing; a never-fired projection (`observe`,
`trace`, `decisions`, …) is expected-silent — the documented attic policy.

Design rules (the `observe` / `efficiency_trend` postures):

* **Pure where it can be.** `fold_counts()` and `census()` take records +
  the verb universe and return data — no disk — so the suite folds them without
  a file. Only `build_census()` does the two boundary reads.
* **Read-only projection.** Like `observe`, this reads the two logs, mints no
  belief, takes no lease, adjudicates nothing new. Delete it and you lose the
  reader, not the data.
* **Like-for-like, additively.** A verb fired in EITHER log counts as fired;
  the two per-log counts are reported separately AND summed, so an operator can
  see whether a verb's firings came from the CLI surface, the hooks, or both.
* **Drift-pinned.** `VERDICT_BEARING ⊇ verdict_journal.KNOWN_SYSCALLS` is
  asserted by a test — a new journal syscall that forgets to register here trips
  the suite rather than silently dropping out of the orphan denominator.
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from typing import Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover
        pass
elif not isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dos import hook_observation as _ho
from dos import verdict_journal as _vj


# ---------------------------------------------------------------------------
# The verdict-bearing verb surface — the orphan denominator.
#
# A verb is "verdict-bearing" iff invoking it mints a typed kernel verdict (a
# `classify`), so its silence is a real finding ("this adjudication surface is
# dead in this workspace"). A read-only projection (observe/trace/decisions/…)
# or a scaffold (init/quickstart) mints nothing, so its silence is expected and
# it is NOT an orphan — the attic policy made structural.
#
# The set is `verdict_journal.KNOWN_SYSCALLS` (the syscalls already wired to the
# journal) PLUS the CLI verbs that classify a verdict but have no telemetry sink
# yet — the exact orphan set issue #20 hand-audited. Adding a journal wire for
# one of these does not change the census; it just lets the verb start showing a
# non-zero count. The drift test pins KNOWN_SYSCALLS ⊆ this set.
# ---------------------------------------------------------------------------

# Verdict-bearing CLI verbs not (yet) in verdict_journal.KNOWN_SYSCALLS. These
# classify a verdict at the CLI boundary but do not call verdict_journal.record,
# so they are invisible to `dos observe` — the journal-wiring gap issue #20
# names. Listed by CLI verb spelling (hyphenated as the user types it).
_UNWIRED_VERDICT_VERBS: tuple[str, ...] = (
    "notify",       # NotifyOutcome — advisory, never journaled
    "resume",       # ResumeProposal — proposes, never journaled
    "improve",      # the keep/revert/escalate verdict
    "reconcile",    # the picker reconcile verdict
    "enumerate",    # the picker enumerate verdict
    "efficiency-trend",  # DEGRADING/STEADY — a verdict, journal wire is a follow-up
    "work-account",      # the env-authored work tally verdict
    "exec-capability",   # the capability gate verdict
)

# The full verdict-bearing surface: the journal's own syscalls + the unwired
# verbs above. Stored as a frozenset for O(1) membership; the journal syscalls
# use their journal spelling (e.g. `hook_exit`), the CLI verbs their CLI
# spelling — `_norm_verb` reconciles the two at fold time.
VERDICT_BEARING: frozenset[str] = frozenset(_vj.KNOWN_SYSCALLS) | frozenset(
    _UNWIRED_VERDICT_VERBS
)


# ---------------------------------------------------------------------------
# Verb-name normalization — the two logs spell some verbs differently.
# ---------------------------------------------------------------------------

# The verdict journal keys hook sensors as `hook_exit`/`pretool`/`posttool`/
# `stop`; the CLI verb is `hook-exit` (and the hooks fire under `hook`). Map the
# journal spelling onto the CLI spelling so a verb counts once across both logs
# and lines up with the derived universe. Only the genuinely-divergent names are
# listed; everything else is identity.
_JOURNAL_TO_CLI: Mapping[str, str] = {
    "hook_exit": "hook-exit",
}


def _norm_verb(name: str) -> str:
    """Canonicalize a verb name to its CLI spelling (the universe's spelling)."""
    n = (name or "").strip()
    return _JOURNAL_TO_CLI.get(n, n)


# ---------------------------------------------------------------------------
# The verb universe — derived from the live CLI subparser registry (no rot).
# ---------------------------------------------------------------------------


def census_verbs() -> tuple[str, ...]:
    """Every `dos` verb the CLI registers, sorted. The census denominator.

    Walks `cli.build_parser()`'s subparser choices, so the universe is exactly
    the shipped verb surface — a new verb joins the day it is registered, with
    no hand-list to maintain (issue #20's no-rot direction). Imported lazily so
    this module does not pull the whole CLI at import time (the kernel-leaf
    posture); a failure to introspect degrades to the verdict-bearing set alone
    (better a smaller honest universe than a crash in a read-only projection).
    """
    try:
        from dos import cli as _cli

        parser = _cli.build_parser()
        for act in parser._actions:  # noqa: SLF001 — argparse exposes no public API
            choices = getattr(act, "choices", None)
            if choices and getattr(act, "dest", None) == "cmd":
                return tuple(sorted(choices.keys()))
    except Exception:  # pragma: no cover - defensive; CLI import is normally fine
        pass
    return tuple(sorted(_norm_verb(v) for v in VERDICT_BEARING))


# ---------------------------------------------------------------------------
# The pure fold — records in, per-verb counts out, no disk.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerbUsage:
    """One verb's census row — its firing count, split by source.

    `journal` is the count from the verdict journal (`syscall` field),
    `observation` from the hook observation log (`verb` field); `total` is their
    sum. `verdict_bearing` records whether a zero count is an ORPHAN (a dead
    adjudication surface) or expected silence (a read-only projection / scaffold).
    """

    verb: str
    journal: int = 0
    observation: int = 0
    verdict_bearing: bool = False

    @property
    def total(self) -> int:
        return self.journal + self.observation

    @property
    def fired(self) -> bool:
        return self.total > 0

    @property
    def is_orphan(self) -> bool:
        """A verdict-bearing verb that never fired — the issue-#20 finding."""
        return self.verdict_bearing and not self.fired

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "journal": self.journal,
            "observation": self.observation,
            "total": self.total,
            "verdict_bearing": self.verdict_bearing,
            "fired": self.fired,
            "is_orphan": self.is_orphan,
        }


@dataclass(frozen=True)
class Census:
    """The folded census over the whole verb universe — the render surface.

    `rows` is one `VerbUsage` per verb in the universe, ordered (fired first by
    descending total, then never-fired verbs alphabetically). `never_fired` and
    `orphans` are the never-fired verbs and the verdict-bearing subset of them
    (the orphan list). `corrupt` carries the verdict-journal integrity tally so
    the census surfaces it the way `observe` does.
    """

    rows: tuple[VerbUsage, ...]
    never_fired: tuple[str, ...]
    orphans: tuple[str, ...]
    total_invocations: int
    corrupt: int = 0

    def to_dict(self) -> dict:
        return {
            "total_invocations": self.total_invocations,
            "corrupt": self.corrupt,
            "never_fired": list(self.never_fired),
            "orphans": list(self.orphans),
            "rows": [r.to_dict() for r in self.rows],
        }


def fold_counts(
    journal_events: Iterable[_vj.VerdictEvent],
    observation_records: Iterable[Mapping[str, object]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Fold the two record streams into `(journal_counts, observation_counts)`.

    PURE — record streams in, two `{verb: count}` maps out, no disk. Each event
    contributes one to its verb's count, under the normalized CLI spelling, so
    `hook_exit` (journal) and `hook-exit` (CLI) are the same bucket.
    """
    jc: dict[str, int] = {}
    oc: dict[str, int] = {}
    for ev in journal_events:
        v = _norm_verb(getattr(ev, "syscall", "") or "")
        if v:
            jc[v] = jc.get(v, 0) + 1
    for rec in observation_records:
        v = _norm_verb(str(rec.get("verb") or ""))
        if v:
            oc[v] = oc.get(v, 0) + 1
    return jc, oc


def census(
    verbs: Iterable[str],
    journal_counts: Mapping[str, int],
    observation_counts: Mapping[str, int],
    *,
    corrupt: int = 0,
) -> Census:
    """Build the census from the universe + the two count maps. PURE — no disk.

    A verb seen in a log but absent from `verbs` (a custom/host verb the CLI
    introspection did not surface) is still included as a row — a real firing is
    never dropped — but it is treated as non-verdict-bearing unless it is in the
    declared `VERDICT_BEARING` set. The orphan list is the verdict-bearing verbs
    whose total is zero.
    """
    universe = {_norm_verb(v) for v in verbs}
    seen = set(journal_counts) | set(observation_counts)
    all_verbs = universe | seen
    rows: list[VerbUsage] = []
    for v in all_verbs:
        rows.append(
            VerbUsage(
                verb=v,
                journal=int(journal_counts.get(v, 0)),
                observation=int(observation_counts.get(v, 0)),
                verdict_bearing=v in VERDICT_BEARING,
            )
        )
    # Fired first, by descending total then name; never-fired after, by name.
    rows.sort(key=lambda r: (0 if r.fired else 1, -r.total, r.verb))
    never_fired = tuple(sorted(r.verb for r in rows if not r.fired))
    orphans = tuple(sorted(r.verb for r in rows if r.is_orphan))
    total = sum(r.total for r in rows)
    return Census(
        rows=tuple(rows),
        never_fired=never_fired,
        orphans=orphans,
        total_invocations=total,
        corrupt=corrupt,
    )


# ---------------------------------------------------------------------------
# The boundary read — the two logs, then the pure fold. The only I/O.
# ---------------------------------------------------------------------------


def build_census(
    *,
    verdict_path=None,
    observation_path=None,
    cfg=None,
) -> Census:
    """Read both telemetry logs once each, then fold. Read-only.

    `verdict_path` / `observation_path` override the two log locations (the
    test affordance, like `observe`'s `path=`); otherwise each resolves through
    its own module's active-workspace default. The two reads are the only I/O;
    everything after is the pure `fold_counts` + `census`.
    """
    raw = _vj.read_all(verdict_path)
    corrupt = _vj.count_corrupt(raw)
    journal_events = [
        _vj.VerdictEvent.from_record(rec) for rec in raw if rec.get("op") != "_CORRUPT"
    ]
    observations = _ho.read_observations(observation_path, cfg)
    jc, oc = fold_counts(journal_events, observations)
    return census(census_verbs(), jc, oc, corrupt=corrupt)


# ---------------------------------------------------------------------------
# Rendering — the plain-text floor (the fired table + the never-fired list).
# ---------------------------------------------------------------------------

_VERB_W = 18


def render_text(c: Census) -> str:
    """The census as a compact table: fired verbs with counts, then the
    never-fired list with the orphans called out.

    Mirrors `observe.render_rollup_text`'s small-column idiom.
    """
    out: list[str] = []
    out.append("# verdict-usage census")
    out.append(
        f"  {c.total_invocations} invocation(s) across "
        f"{sum(1 for r in c.rows if r.fired)} verb(s); "
        f"{len(c.never_fired)} never fired ({len(c.orphans)} orphan(s))"
    )
    if c.corrupt:
        out.append(
            f"  ⚠ {c.corrupt} corrupt/unreadable verdict-journal line(s) "
            f"(integrity breach — not a torn tail)"
        )
    out.append("")
    fired = [r for r in c.rows if r.fired]
    if fired:
        header = f"  {'verb':<{_VERB_W}} {'total':>6} {'journal':>8} {'hooks':>6}"
        out.append(header)
        out.append("  " + "-" * (len(header) - 2))
        for r in fired:
            out.append(
                f"  {r.verb:<{_VERB_W}} {r.total:>6} {r.journal:>8} {r.observation:>6}"
            )
    else:
        out.append("  (no verb fired in either log yet)")
    out.append("")
    if c.orphans:
        out.append(
            "  ORPHANS (verdict-bearing, never fired — a dead adjudication surface):"
        )
        out.append("    " + ", ".join(c.orphans))
    else:
        out.append("  no orphans — every verdict-bearing verb has fired")
    # The expected-silent residue (never-fired, NOT verdict-bearing) — the attic
    # policy: read-only projections and scaffolds mint no verdict, so their
    # silence is by design, surfaced but not flagged.
    expected_silent = tuple(v for v in c.never_fired if v not in c.orphans)
    if expected_silent:
        out.append(
            "  expected-silent (read-only projections / scaffolds, mint no verdict):"
        )
        out.append("    " + ", ".join(expected_silent))
    return "\n".join(out)


def render_json(c: Census) -> str:
    import json

    return json.dumps(c.to_dict(), indent=2, sort_keys=True)
