r"""model_health — the per-MODEL fleet death rollup (the '3x observability' half).

> **A read-only projection that answers one question the existing folds cannot:
> WHICH model is down across a fleet's children and grandchildren — and how many
> descendants died on it.** `result_state` classifies ONE child's death;
> `coverage` folds N deaths into a quorum count with a per-CLASS breakdown. Neither
> attributes a death to the named model it died on, so a down model on a
> grandchild three hops deep is invisible until someone reads the transcript by
> hand. This module folds a set of transcripts into a per-model tally of
> MODEL_UNAVAILABLE deaths, so the operator sees "model X is down: 4 children + 2
> grandchildren dead on it; heal = reroute" at a glance.**

Why a NEW leaf, not a field on `coverage`
=========================================

`coverage` answers "is the fan-out done, or only declared done?" — a quorum
verdict over ONE fan-out level, keyed on the workflow-DECLARED count. Model health
is orthogonal: it folds across an arbitrary DEPTH of descendants (child →
grandchild → …), is not gated on a declared count, and keys on the model NAME the
death carries, not the terminal class. Bolting a per-model map onto `CoverageVerdict`
would conflate two questions ("did enough return?" vs "which model is down?") and
drag a model-name extractor into the quorum path. So this is the `fleet_roll`
sibling one rung over — a fold over the SAME already-adjudicated `result_state`
verdicts, grouped by a different key.

The model-name extraction — purely, from the death's own text
=============================================================

A MODEL_UNAVAILABLE death's terminal record carries `model == "<synthetic>"` (the
harness-authorship marker — NOT the down model's name). The down model's NAME lives
in the error TEXT the harness wrote: "Claude Fable 5 is currently unavailable". So
`model_name_from_text` pulls the name out of that text purely. It is best-effort by
construction: a name it cannot parse is reported as the sentinel `"<unnamed>"` —
never guessed, never dropped (an unnamed model-down is still a model-down the
operator must see). It names NO specific model in code (the provider-invariance
floor — the kernel knows the SHAPE of the unavailability sentence, not the roster).

⚓ Kernel discipline (the litmus): a PURE fold + a boundary reader. It imports only
the sibling kernel module `result_state` (+ stdlib), names no host and no specific
model, resolves nothing against `__file__`, takes no lease, mints ZERO new labels
(every death it counts was already adjudicated by `result_state.classify_terminal`).
Delete it and you lose the reader, not the data. ADVISORY (PDP, not PEP): it REPORTS
which models are down + the reroute heal; it never re-dispatches a worker (that is
the conductor's / a driver's act — the roster of live models is host policy).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from dos.result_state import (
    ResultStateVerdict,
    TerminalClass,
    TerminalState,
)

# The sentinel for a MODEL_UNAVAILABLE death whose model name could not be parsed
# from the error text. Reported, never dropped — an unnamed model-down is still a
# real outage the operator must see (the honest-floor direction).
UNNAMED_MODEL = "<unnamed>"


# A down-model sentence the harness writes, e.g.
#   "Claude Fable 5 is currently unavailable"
#   "The model claude-fable-5 is currently unavailable"
#   "The requested model 'opus-x' is unavailable"
#   "`claude-fable-5` is currently unavailable"
# The NAME is the phrase just before the unavailability clause. We capture it
# permissively (quotes/backticks allowed inside the capture, stripped afterward),
# then strip a leading "the (requested) model" lead-in. The capture is anchored on
# the clause; the lazy `+?` makes it start as late as possible so a long sentence
# does not swallow leading prose into the name.
_UNAVAIL_CLAUSE = re.compile(
    r"(?P<name>[A-Za-z0-9'\"`“”‘’][\w '\"`./\-\[\]“”‘’]*?)"
    r"\s+is\s+(?:currently\s+)?unavailable",
    re.IGNORECASE,
)
# Lead-ins to strip off the front of a captured name — "The model claude-x" →
# "claude-x", "The requested model 'opus-x'" → "'opus-x'". The trailing \s* (not
# \s+) lets a bare "model" with nothing after it collapse to the empty string,
# which `model_name_from_text` then reports as honestly unnamed.
_NAME_LEADIN = re.compile(r"^(?:the\s+)?(?:requested\s+)?model\b\s*", re.IGNORECASE)
# Surrounding quotes/backticks a name may be wrapped in.
_NAME_QUOTES = "\"'`“”‘’"


def model_name_from_text(text: str) -> str:
    """Extract the down model's NAME from a MODEL_UNAVAILABLE death's error text. PURE.

    Anchors on the "<name> is (currently) unavailable" clause and returns the
    captured name, stripped of a "the (requested) model" lead-in and surrounding
    quotes. Returns :data:`UNNAMED_MODEL` when no name can be parsed (including the
    bare "model is unavailable" case, where the only "name" was the lead-in word
    itself) — never guesses a model and never raises. Best-effort by construction:
    the operator needs the name when it is there, and an honest "<unnamed>" when not.
    """
    if not text:
        return UNNAMED_MODEL
    m = _UNAVAIL_CLAUSE.search(text)
    if not m:
        return UNNAMED_MODEL
    name = m.group("name").strip()
    # Drop a leading "the (requested) model" lead-in, THEN strip quotes (the name
    # may have been quoted: "model 'opus-x'" → "'opus-x'" → "opus-x").
    name = _NAME_LEADIN.sub("", name).strip().strip(_NAME_QUOTES).strip()
    # A name that collapsed to nothing (the text was just "model is unavailable",
    # so the capture was only the lead-in word) is honestly unnamed.
    return name if name else UNNAMED_MODEL


@dataclass(frozen=True)
class ModelDeath:
    """One witnessed MODEL_UNAVAILABLE death, attributed to its model. PURE-built.

      * model     — the down model's name (or :data:`UNNAMED_MODEL`).
      * source    — an optional legibility label for WHERE the death was seen (a
                    transcript path, an agent id, a "child"/"grandchild" tag). Not
                    load-bearing — the fold groups by `model`, not `source`.
    """

    model: str
    source: str = ""


@dataclass(frozen=True)
class ModelTally:
    """One model's rollup row — how many descendants died on it.

      * model  — the model name (or :data:`UNNAMED_MODEL`).
      * deaths — how many witnessed MODEL_UNAVAILABLE deaths named this model.
      * sources — the (bounded) list of source labels, for the drill-down.
    """

    model: str
    deaths: int
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"model": self.model, "deaths": self.deaths, "sources": list(self.sources)}


@dataclass(frozen=True)
class ModelHealth:
    """The folded per-model death rollup over a fleet's transcripts — the render surface.

      * tallies          — one `ModelTally` per down model, most deaths first.
      * model_unavailable — total MODEL_UNAVAILABLE deaths across the fleet.
      * other_dead       — deaths from OTHER classes (rate-limit/quota/…): counted
                           so the rollup is honest about its scope, but NOT grouped
                           by model (a rate-limit is the account's, not a model's).
      * healthy          — transcripts whose terminal was a real-model result.
      * unreadable       — transcripts that could not be read (LIVE, never a death —
                           the `result_state` fail-safe floor, carried through).
      * considered       — total transcripts folded.

    `any_model_down` is the at-a-glance signal: True iff at least one model is down
    somewhere in the fleet, which is exactly the condition the goal's auto-routing
    should fire on. `reroute_targets` is the de-duplicated list of down model names
    a driver must route AWAY from.
    """

    tallies: tuple[ModelTally, ...]
    model_unavailable: int
    other_dead: int
    healthy: int
    unreadable: int
    considered: int

    @property
    def any_model_down(self) -> bool:
        """True iff any model is down across the fleet — the auto-routing trigger."""
        return self.model_unavailable > 0

    @property
    def reroute_targets(self) -> tuple[str, ...]:
        """The down model names a driver must route AWAY from (de-duplicated, ordered
        most-deaths-first). The named, actionable output of the projection — a host
        roster driver reads this to pick a sibling, NEVER naming a model here."""
        return tuple(t.model for t in self.tallies)

    @property
    def headline(self) -> str:
        """A one-line at-a-glance summary — the projection's whole point.

        "model X is down (N descendants dead); reroute" when a model is down,
        "all models healthy across N descendant(s)" otherwise. Generated from the
        REAL partition — it never asserts a model is down without a witnessed death.
        """
        if not self.any_model_down:
            return (
                f"all models healthy across {self.considered} descendant(s) "
                f"({self.healthy} healthy, {self.other_dead} dead on other causes, "
                f"{self.unreadable} unreadable)"
            )
        worst = self.tallies[0]
        n_models = len(self.tallies)
        models_clause = (
            f"{worst.model}" if n_models == 1 else f"{worst.model} + {n_models - 1} other model(s)"
        )
        return (
            f"MODEL DOWN: {models_clause} — {self.model_unavailable} descendant(s) "
            f"died on an unavailable model; heal = reroute the unit(s) to a sibling model"
        )

    def to_dict(self) -> dict:
        return {
            "any_model_down": self.any_model_down,
            "model_unavailable": self.model_unavailable,
            "other_dead": self.other_dead,
            "healthy": self.healthy,
            "unreadable": self.unreadable,
            "considered": self.considered,
            "reroute_targets": list(self.reroute_targets),
            "tallies": [t.to_dict() for t in self.tallies],
            "headline": self.headline,
        }


# How many source labels to keep per model in the drill-down. Bounded so a fleet
# with hundreds of descendants dead on one model does not produce an unbounded list.
_MAX_SOURCES_PER_MODEL = 20


def fold_model_health(
    verdicts: Sequence[ResultStateVerdict],
    *,
    sources: Optional[Sequence[str]] = None,
    texts: Optional[Sequence[str]] = None,
) -> ModelHealth:
    """Fold a set of already-adjudicated `result_state` verdicts into a per-model
    death rollup. PURE — verdicts in, rollup out, no I/O. Mints ZERO new labels.

    Each verdict is partitioned by its `result_state` state/class:

      * SYNTHETIC + cls == MODEL_UNAVAILABLE → a model death, attributed to the
        model named in its `texts[i]` (the error text). The verdict itself does not
        carry the down model's name (its `model` field is `<synthetic>`), so the
        caller passes the leading error text per verdict via `texts` — when omitted,
        the death is still counted, attributed to `UNNAMED_MODEL`.
      * SYNTHETIC + any other class, or EMPTY → `other_dead` (a death, but not a
        model-down — a rate-limit/quota/auth death is the account's, not a model's).
      * HEALTHY → `healthy`.
      * UNREADABLE → `unreadable` (LIVE, never a death — the fail-safe floor).

    `sources`/`texts`, when given, must be index-aligned with `verdicts`. Either may
    be shorter/omitted — a missing entry degrades to "" / unnamed, never an error.
    """
    model_unavailable = other_dead = healthy = unreadable = 0
    by_model: dict[str, int] = {}
    model_sources: dict[str, list[str]] = {}

    for i, v in enumerate(verdicts):
        src = sources[i] if sources is not None and i < len(sources) else ""
        if v.state is TerminalState.HEALTHY:
            healthy += 1
        elif v.state is TerminalState.UNREADABLE:
            unreadable += 1  # FAIL-SAFE: LIVE, not a death.
        elif v.state is TerminalState.SYNTHETIC and v.cls is TerminalClass.MODEL_UNAVAILABLE:
            model_unavailable += 1
            txt = texts[i] if texts is not None and i < len(texts) else ""
            model = model_name_from_text(txt)
            by_model[model] = by_model.get(model, 0) + 1
            bucket = model_sources.setdefault(model, [])
            if src and len(bucket) < _MAX_SOURCES_PER_MODEL:
                bucket.append(src)
        else:  # SYNTHETIC (other class) or EMPTY — a death, but not a model-down.
            other_dead += 1

    # Order: most deaths first, then model name (stable for equal counts). The
    # UNNAMED_MODEL bucket sorts by its count like any other — an unnamed cluster of
    # deaths is as visible as a named one.
    tallies = tuple(
        ModelTally(model=m, deaths=n, sources=tuple(model_sources.get(m, ())))
        for m, n in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return ModelHealth(
        tallies=tallies,
        model_unavailable=model_unavailable,
        other_dead=other_dead,
        healthy=healthy,
        unreadable=unreadable,
        considered=len(verdicts),
    )


# ───────────────────────────── boundary I/O ───────────────────────────────────
def model_health_from_transcripts(
    paths: Sequence[str],
    *,
    sources: Optional[Sequence[str]] = None,
) -> ModelHealth:
    """Fold a list of descendant transcript paths into a model-health rollup. NOT pure.

    Reads each path via `result_state.verify_transcript` at the boundary (a missing /
    garbled file yields UNREADABLE → counted LIVE, the fail-safe floor), capturing
    each verdict AND the leading error text (so a MODEL_UNAVAILABLE death can be
    attributed to its named model), then folds with the pure `fold_model_health`.
    This is the HARNESS-GROUNDED path: it runs the `model=='<synthetic>'`
    classification itself, so the counts cannot be forged by a self-reporting fleet
    (the `git_delta`/`liveness` "I/O at the boundary, data to the pure core" rule).

    `sources`, when given, is index-aligned with `paths` (e.g. a "child"/"grandchild"
    depth tag or an agent id); when omitted, each path is its own source label.
    """
    from dos import result_state

    verdicts: list[ResultStateVerdict] = []
    texts: list[str] = []
    srcs: list[str] = []
    for i, p in enumerate(paths):
        ev = result_state.terminal_evidence_from_transcript(str(p))
        verdicts.append(result_state.classify_terminal(ev))
        texts.append(ev.text or "")
        srcs.append(
            sources[i] if sources is not None and i < len(sources) else str(p)
        )
    return fold_model_health(verdicts, sources=srcs, texts=texts)
