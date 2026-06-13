"""DOS — the Dispatch Operating System.

The domain-free trust substrate the `job` repo's dispatch family invented by
accident: a small, boring, deterministic kernel whose primary job is
**adjudicating ground truth across many unreliable, self-narrating workers** and
serializing their effects on shared state *without believing what they say they
did*. The tagline (dispatch-os-vision §0): **the kernel is the part that doesn't
believe the agents.**

This package is the "Stage-1 kernel extraction" — the spine lifted out of
the reference userland app's scripts into a standalone, pip-installable,
**workspace-parameterized** package. It carries the *mechanism* (verdict enum, ship oracle, structured
refusal, lease arbiter, correlation spine) and **no policy**: which lanes exist,
where plans live, and what counts as a ship stamp are per-workspace data the
host supplies via `dos.config.SubstrateConfig`. The package never assumes it
lives inside the repo whose state it manages.

The syscall ABI (dispatch-os-vision §4), mapped to the modules here:

    verify()           -> dos.oracle        (the truth syscall — artifact over narration)
    refuse(reason)     -> dos.wedge_reason  (structured refusal — the closed WedgeReason enum)
                          dos.picker_oracle (provable no-pick verification)
    lease()/arbitrate()-> dos.arbiter       (the pure admission kernel — ACR Plane ①)
    spawn()/reap()     -> dos.run_id        (the correlation spine across subprocess boundaries)
                          dos.lane_journal  (the write-ahead log for lease decisions)

The first userland app written against this kernel is `job` (job search), the
way `cat` was the first program for Unix.
"""

from __future__ import annotations

# Single-source the version from installed package metadata so it can never
# drift from pyproject.toml (it did: __version__ said 0.1.0 while pyproject
# shipped 0.2.0, so every `dos` CLI command misreported its version). The
# literal fallback is only hit when running from an uninstalled source tree;
# keep it equal to pyproject's version for that case.
#
# NB: the distribution name is `dos-kernel`, not `dos` (the bare `dos` name on
# PyPI is an unrelated package). The metadata lookup MUST use the dist name —
# looking up "dos" would miss our metadata and, if the squatter were installed,
# could even read ITS version. The import name is still `dos`; only the dist
# name differs.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("dos-kernel")
except Exception:  # pragma: no cover - source-tree / not-installed fallback
    __version__ = "0.26.0"

from dos import config  # noqa: F401  (re-export the seam as the package entry)
from dos.config import (  # noqa: F401
    SubstrateConfig,
    LaneTaxonomy,
    PathLayout,
    HomeLayout,
    job_config,
    default_config,
    active,
    set_active,
    resolve_dos_home,
    active_home,
    set_active_home,
)
from dos.reasons import (  # noqa: F401  (the refusal vocabulary, as data)
    ReasonSpec,
    ReasonRegistry,
    BASE_REASONS,
)
from dos.stamp import (  # noqa: F401  (the ship-stamp grammar, as data)
    StampConvention,
    JOB_STAMP_CONVENTION,
    GENERIC_STAMP_CONVENTION,
    parse_phase_labels,
)
from dos.retention import (  # noqa: F401  (the scratch-retention caps, as data)
    RetentionPolicy,
    GENERIC_RETENTION,
    UNBOUNDED_RETENTION,
    should_compact,
)
from dos.intervention import (  # noqa: F401  (the actuation ladder, docs/143 §13)
    Intervention,
    Confidence,
    InterventionSpec,
    InterventionLadder,
    BASE_INTERVENTIONS,
    InterventionPolicy,
    DEFAULT_POLICY as DEFAULT_INTERVENTION_POLICY,
    InterventionDecision,
    assess_confidence,
    choose_intervention,
    synthetic_corrective_result,
)
from dos.intervention_eval import (  # noqa: F401  (the net-task-delta eval, docs/143 §13.2)
    InterventionCase,
    InterventionReport,
)
from dos.enforce import (  # noqa: F401  (the enforcement-handler seam, docs/189 §A1)
    EffectProposal,
    EnforcementHandler,
    ObserveHandler,
    run_handler,
    resolve_handler,
    active_handlers,
    active_handler_names,
)
from dos.tool_stream import (  # noqa: F401  (the loop-economics stall reader, docs/145)
    StreamState,
    StreamPolicy,
    DEFAULT_POLICY as DEFAULT_STREAM_POLICY,
    StreamStep,
    ToolStream,
    StreamVerdict,
    classify_stream,
    policy_from_table as stream_policy_from_table,
)
from dos.tool_stream_eval import (  # noqa: F401  (the stall-reader recovery eval, docs/145 §9)
    StreamCase,
    StreamEvalReport,
)
from dos.dangling_intent import (  # noqa: F401  (the premature-completion DETECTOR, docs/150)
    Dangling,
    DanglingPolicy,
    DEFAULT_POLICY as DEFAULT_DANGLING_POLICY,
    DEFAULT_CUES as DEFAULT_DANGLING_CUES,
    StopEvidence,
    DanglingVerdict,
    classify_stop,
)
from dos.firing_label import (  # noqa: F401  (detector self-labeling — the data-multiplier, docs/179)
    DetectorFiring,
    LabelOutcome,
    LabeledPoint,
    LabelSummary,
    label_one,
    label_firings,
    dedupe_firings,
)
from dos.pickable import (  # noqa: F401  (the pre-dispatch gate, docs/168 Concept 2)
    HoldReason,
    Pickability,
    classify as pickable_classify,
)
from dos.pick_priority import (  # noqa: F401  (the freshness sort-key, docs/254)
    AttemptSummary,
    Freshness,
    PickPriority,
    classify as pick_priority_classify,
)
from dos import render  # noqa: F401  (the renderer seam — Axis 4 output, RND)
from dos.render import (  # noqa: F401
    Renderer,
    BaseRenderer,
    TextRenderer,
    JsonRenderer,
    BUILTIN_RENDERERS,
    resolve_renderer,
    known_renderers,
    UnknownRenderer,
)
from dos.reward import (  # noqa: F401  (the reward-set admission verdict, docs/230/234)
    RewardVerdict,
    RewardLabel,
    admit as reward_admit,
    AcceptanceAB,
    acceptance_ab as reward_acceptance_ab,
)
from dos.deprecation import (  # noqa: F401  (the typed deprecation category, docs/308)
    DosDeprecationWarning,
    warn_deprecated,
)
from dos.verified import (  # noqa: F401  (the in-process verify gate, issue #75)
    # NB: this deliberately rebinds the `dos.verified` attribute from the
    # submodule to the callable, so `from dos import verified` yields the
    # decorator/context-manager (the issue-#75 surface), datetime.datetime-style.
    verified,
    NotShippedError,
)

__all__ = [
    "__version__",
    "config",
    "SubstrateConfig",
    "LaneTaxonomy",
    "PathLayout",
    "HomeLayout",
    "job_config",
    "default_config",
    "active",
    "set_active",
    "resolve_dos_home",
    "active_home",
    "set_active_home",
    "ReasonSpec",
    "ReasonRegistry",
    "BASE_REASONS",
    "StampConvention",
    "JOB_STAMP_CONVENTION",
    "GENERIC_STAMP_CONVENTION",
    "parse_phase_labels",
    "RetentionPolicy",
    "GENERIC_RETENTION",
    "UNBOUNDED_RETENTION",
    "should_compact",
    "Intervention",
    "Confidence",
    "InterventionSpec",
    "InterventionLadder",
    "BASE_INTERVENTIONS",
    "InterventionPolicy",
    "DEFAULT_INTERVENTION_POLICY",
    "InterventionDecision",
    "assess_confidence",
    "choose_intervention",
    "synthetic_corrective_result",
    "InterventionCase",
    "InterventionReport",
    "EffectProposal",
    "EnforcementHandler",
    "ObserveHandler",
    "run_handler",
    "resolve_handler",
    "active_handlers",
    "active_handler_names",
    "StreamState",
    "StreamPolicy",
    "DEFAULT_STREAM_POLICY",
    "StreamStep",
    "ToolStream",
    "StreamVerdict",
    "classify_stream",
    "stream_policy_from_table",
    "StreamCase",
    "StreamEvalReport",
    "DetectorFiring",
    "LabelOutcome",
    "LabeledPoint",
    "LabelSummary",
    "label_one",
    "label_firings",
    "dedupe_firings",
    "Dangling",
    "DanglingPolicy",
    "DEFAULT_DANGLING_POLICY",
    "DEFAULT_DANGLING_CUES",
    "StopEvidence",
    "DanglingVerdict",
    "classify_stop",
    "HoldReason",
    "Pickability",
    "pickable_classify",
    "AttemptSummary",
    "Freshness",
    "PickPriority",
    "pick_priority_classify",
    "render",
    "Renderer",
    "BaseRenderer",
    "TextRenderer",
    "JsonRenderer",
    "BUILTIN_RENDERERS",
    "resolve_renderer",
    "known_renderers",
    "UnknownRenderer",
    "reward",
    "RewardVerdict",
    "RewardLabel",
    "reward_admit",
    "AcceptanceAB",
    "reward_acceptance_ab",
    "DosDeprecationWarning",
    "warn_deprecated",
    "verified",
    "NotShippedError",
]
