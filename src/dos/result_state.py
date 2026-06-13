r"""result_state — the fold-site result-state witness (docs/197 §7(1), the keystone).

> **An ultracode `Workflow` folds `agent()`'s self-authored return value as ground
> truth at exactly one place — the `${result}` interpolation — and 32% of real
> subagents (736/2305, docs/197 §2) fold a HARNESS-authored terminal-error string
> there as a finished "finding." The death is non-null, so it survives the
> `.filter(Boolean)` used in 89/114 real scripts; a smaller numerator is the only
> signal, and code that computes `failed = N − survivors.length` cannot tell a dead
> worker from a real negative. This module is the byte-clean referee at that fold:
> it classifies a subagent transcript's TERMINAL assistant message and refuses to
> believe a harness-synthesized abnormal termination — because the catch reads a
> DIFFERENT byte-author than the judged worker (`message.model == "<synthetic>"`
> means the Claude Code HARNESS synthesized the bytes, not the subagent's model).**

The byte-author law, restated for the fold (docs/138 / docs/116 §2.5):

  > A verdict is *grounding* only when the byte-author of the evidence differs from
  > the judged agent. A subagent re-narrating its own output is consistency, not
  > grounding.

The terminal `model:"<synthetic>"` record is the cleanest possible grounding: the
`role:"assistant"` slot is merely the conversation position, but `<synthetic>` is
the harness's authorship stamp — the subagent's model did NOT generate it. So
asking "is this terminal record harness-authored?" is a pure byte question about
bytes the judged agent could not forge in its favor. This is the same shape as
`tool_stream` keying on the env-authored `result_digest`, one rung over: there the
env authors the repeated result, here the harness authors the death.

Why a NEW grammar — not a reuse of `terminal_error` (docs/197 §4c, VERIFIED)
===========================================================================

`benchmark/toolathlon/trajectory.py:terminal_error_fired` is the structural-error
detector for tool RESULTS — but it (a) walks ONLY `role=="tool"` messages
(`trajectory.py:471`), and (b) its `_STRUCT_ERR` grammar anchors `^\s*Error:`,
which does NOT match the synthetic string that LEADS with `API Error:`
(`trajectory.py:343`). The synthetic terminal is a `role:"assistant"` record with
`model:"<synthetic>"`, so it never reaches that classifier. This module is the
genuinely-new grammar over the ASSISTANT role the keystone needs — and it lives in
the kernel (not `benchmark/`) because the fold-site catch is a reusable distrust
primitive, not a one-benchmark instrument.

The discriminators (grounded in 2,935 REAL synthetic records, not the doc's spec)
=================================================================================

An empirical sweep of every `model:"<synthetic>"` record across the operator's real
`~/.claude/projects` corpus (2,935 records) fixes the byte-exact shape. Critically,
it is BROADER than docs/197's "429" framing — 43% of synthetic deaths are NOT 429:

  * `message.model == "<synthetic>"`        — 100% (the unforgeable harness marker)
  * `message.stop_reason == "stop_sequence"`— 100%
  * top-level `isApiErrorMessage == true`   — 100%
  * top-level `apiErrorStatus`              — present with the HTTP code (429/401/
                                              403/500) on 2885/2935; ABSENT on 50
                                              (the subscription/limit-text deaths)
  * `message.content[0].text` classes observed: "API Error: … Rate limited" (1688,
    57%), "organization has disabled …" (248), "hit your weekly limit" (205),
    "API Error: 500 Internal server error" (66), "out of extra usage" / "session
    limit" (the rest). A 429-only match (docs/197's literal spec) would MISS 43%.

So the PRIMARY signal is `model == "<synthetic>"` (harness-authored) — the
unforgeable rung. `isApiErrorMessage` and `stop_reason == "stop_sequence"`
corroborate it. `apiErrorStatus` + a coarse `class` (RATE_LIMIT / USAGE_LIMIT /
AUTH / SERVER / OTHER) are reported as DETAIL, never as the gate (keying the gate
on 429 would conflate the HTTP code with the harness-authorship fact and miss the
non-HTTP limit-text deaths). docs/197 §2.1 also placed `isApiErrorMessage` /
`apiErrorStatus` INSIDE the `message` object; in real records they are TOP-LEVEL
siblings of `message` — corrected here.

Why it is ADVISORY (the docs/197 §6.5 line, the −9 pp wound)
============================================================

A DEAD verdict's safe action is to route the dead child to a DEAD bucket and
re-dispatch ITS OWN unit — never to re-prompt the synthesizer mid-plan (the
docs/143 −9 pp DEFER-shaped derail). This module REPORTS (a verdict + an exit
code a workflow branches on); it never re-runs a worker, never edits the fold. It
is a PDP, not a PEP (`enforce.py` is the proposal seam; nothing here actuates).

⚓ Kernel discipline (the litmus): a PURE verdict + a boundary reader. It imports
only sibling kernel modules (`claim_extract` for the transcript-read boundary,
`wedge_reason` for the refusal envelope, `config`), names no host beyond the
Claude-Code transcript JSON shape, resolves nothing against `__file__`, takes no
lease, carries no policy of its own. The transcript I/O is the caller's boundary
(reused via `claim_extract._read_lines`), exactly the `liveness`/`posttool_sensor`
"I/O at the boundary, data to the pure core" rule.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


# The literal harness-authorship marker. A terminal `message.model` of this exact
# string means the Claude Code HARNESS synthesized the record (a rate-limit / quota
# / server-error stop it injected), NOT the subagent's model — the byte-author the
# fold must distrust. An exact-string compare, never a pattern (the real model field
# in a healthy record is a model id like `claude-opus-4-8`).
SYNTHETIC_MODEL = "<synthetic>"

# The terminal `stop_reason` every synthetic record carries (100% of 2,935 real
# records). A corroborating signal, never the sole gate — a healthy record can also
# carry `stop_sequence` in principle, so this only STRENGTHENS the `<synthetic>`
# verdict, it does not stand alone.
SYNTHETIC_STOP_REASON = "stop_sequence"


class TerminalState(str, enum.Enum):
    """The classification of a transcript's terminal assistant record. `str`-valued
    so it round-trips a `--json` token without a lookup table.

      * HEALTHY    — the terminal assistant record was authored by a real model
                     (`model` is a real id, no synthetic/error markers). Its return
                     value is a genuine result the fold may believe (modulo the
                     well-formed-but-empty residue, which needs `effect_witness`).
      * SYNTHETIC  — the terminal record is HARNESS-authored (`model ==
                     "<synthetic>"` and/or `isApiErrorMessage`): an abnormal
                     termination (rate-limit / quota / auth / server error). The
                     "result" the fold would bank is the error string, not a
                     finding. → route to a DEAD bucket, count in the denominator,
                     REFUSE to fold.
      * EMPTY      — no assistant record with content was found at all (a worker
                     that produced nothing). Distinct from HEALTHY: there is no
                     result to fold. Treated as DEAD (no deliverable).
      * UNREADABLE — the transcript could not be read/parsed (missing/garbled).
                     The fail-safe floor: we cannot witness a death, so we do NOT
                     claim one — UNREADABLE is NOT DEAD (the safe direction: a read
                     fault must not fabricate a death that drops a real result).
    """

    HEALTHY = "HEALTHY"
    SYNTHETIC = "SYNTHETIC"
    EMPTY = "EMPTY"
    UNREADABLE = "UNREADABLE"


class TerminalClass(str, enum.Enum):
    """A coarse, DETAIL-only class of a SYNTHETIC terminal — reported, never the gate.

    Keyed off the top-level `apiErrorStatus` (when present) and the leading text, so
    an operator/log can see WHY the worker died without the classifier ever keying
    its gate on the HTTP code (which would miss the 50/2935 limit-text deaths that
    carry no `apiErrorStatus`). NONE for a non-synthetic terminal.

    `MODEL_UNAVAILABLE` is the class for a death where the NAMED model is down /
    retired / unknown ("Claude Fable 5 is currently unavailable") — the worker
    never ran because the model it was launched on does not exist or is offline.
    It is reported separately from USAGE_LIMIT because the HEAL is different: a
    usage limit waits for a window or a human; a down model is healed by
    re-dispatching the unit on a SIBLING model (the `provider_limit.from_terminal_class`
    bridge maps this class → the `reroute_model` heal policy). This is the class
    the "model is down on a child/grandchild" cascade lands on — naming it instead
    of folding it into OTHER is what makes the failure routable.
    """

    RATE_LIMIT = "RATE_LIMIT"      # 429 / "Rate limited"
    USAGE_LIMIT = "USAGE_LIMIT"    # 403 / weekly|session limit / "out of extra usage" / org-disabled
    AUTH = "AUTH"                  # 401 / authentication_error
    SERVER = "SERVER"             # 500 / server-side
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # the NAMED model is down/retired ("…is currently unavailable")
    OTHER = "OTHER"               # synthetic but an unrecognized class
    NONE = "NONE"                 # not a synthetic terminal


@dataclass(frozen=True)
class TerminalEvidence:
    """The frozen datum `classify_terminal` sees — the fields of the terminal assistant
    record, gathered at the boundary (the transcript read). PURE-consumable.

      * found        — whether ANY assistant record was located in the transcript.
                       False → EMPTY (nothing produced) or UNREADABLE (read failed,
                       distinguished by `readable`).
      * readable     — whether the transcript could be read/parsed at all. False →
                       UNREADABLE (the fail-safe floor — never claim a death we
                       could not witness).
      * model        — the terminal assistant record's `message.model` (the
                       harness-authorship marker; `"<synthetic>"` is the tell).
      * stop_reason  — the terminal record's `message.stop_reason` (corroborating).
      * is_api_error — the top-level `isApiErrorMessage` flag (corroborating).
      * api_status   — the top-level `apiErrorStatus` HTTP code, when present (detail).
      * text         — the leading text of the terminal record's first content block
                       (detail / class inference). Bounded — only a prefix is needed.
      * has_content  — whether the terminal assistant record carried any text/tool
                       content (distinguishes a real-but-empty terminal from EMPTY).
    """

    found: bool
    readable: bool
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    is_api_error: bool = False
    api_status: Optional[int] = None
    text: str = ""
    has_content: bool = False


@dataclass(frozen=True)
class ResultStateVerdict:
    """The typed verdict — the result-state classification + the corroborating detail.

      * state    — the `TerminalState`.
      * dead     — convenience: True iff the fold must NOT believe this result
                   (SYNTHETIC or EMPTY). UNREADABLE is NOT dead (fail-safe).
      * cls      — the DETAIL-only `TerminalClass` (NONE unless SYNTHETIC).
      * api_status — the HTTP code when known (detail).
      * reason   — a short, log-greppable explanation.
    """

    state: TerminalState
    dead: bool
    cls: TerminalClass = TerminalClass.NONE
    api_status: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "dead": self.dead,
            "class": self.cls.value,
            "api_status": self.api_status,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# The PURE verdict — evidence in, verdict out (no I/O).
# ---------------------------------------------------------------------------
def _infer_class(api_status: Optional[int], text: str) -> TerminalClass:
    """The DETAIL-only class of a synthetic terminal. PURE. Never the gate.

    Prefers the HTTP code (precise), falling back to the leading text for the
    no-`apiErrorStatus` limit-text deaths (the 50/2935 records). Conservative: an
    unrecognized synthetic terminal is OTHER, never silently dropped.
    """
    t = (text or "").lower()
    if api_status == 429 or "rate limited" in t:
        return TerminalClass.RATE_LIMIT
    if api_status == 401 or "authentication" in t:
        return TerminalClass.AUTH
    # A NAMED model being down — either the transient shape ("<model> is currently
    # unavailable") or the SUSPENSION shape ("<model> is suspended" / "disabled by
    # policy" / "not available in your region" — a model PULLED by policy). Both are
    # the ONE MODEL_UNAVAILABLE terminal class; the heal split between reroute (a
    # transient) and escalate (a suspension) lives downstream in
    # provider_limit.heal_for_model_death, keyed on the SAME text (issue #140 keeps
    # one class, two heals). Checked BEFORE the broad USAGE_LIMIT cues so the cue is
    # not swallowed, and anchored on MODEL-shaped phrasing so a generic infra
    # "service unavailable" / 503 (a correlated outage a sibling CANNOT heal) is NOT
    # mis-classed — that stays OTHER. "model" OR a model-down sentence shape gates.
    _is_model_down = (
        "is currently unavailable" in t
        or ("unavailable" in t and "model" in t and "service unavailable" not in t)
        # Suspension shapes — a model pulled by policy. Require a model-context word
        # ("model"/"claude"/"the requested") so a generic "account suspended" or
        # "service disabled" does not false-match; "not available in your region" is
        # itself model/region-specific enough to gate alone.
        or (
            any(c in t for c in ("suspended", "disabled by policy", "withdrawn",
                                 "blocked by policy", "export control"))
            and ("model" in t or "claude" in t or "requested" in t)
            and "service" not in t
        )
        or "not available in your region" in t
        or "unavailable in your region" in t
    )
    if _is_model_down:
        return TerminalClass.MODEL_UNAVAILABLE
    if api_status == 500 or "internal server error" in t or "server-side" in t:
        return TerminalClass.SERVER
    if api_status == 403 or any(
        s in t for s in ("weekly limit", "session limit", "out of extra usage",
                         "disabled claude", "usage limit")
    ):
        return TerminalClass.USAGE_LIMIT
    return TerminalClass.OTHER


def classify_terminal(evidence: TerminalEvidence) -> ResultStateVerdict:
    """Classify a transcript's terminal assistant record. PURE.

    The order is the safe-direction order:

      1. UNREADABLE first — if the transcript could not be read, we cannot witness
         a death, so we DECLINE to claim one (NOT dead). A read fault must never
         fabricate a death that drops a real result (the fail-safe floor).
      2. SYNTHETIC — the primary gate is `model == "<synthetic>"` (the unforgeable
         harness-authorship marker). `isApiErrorMessage` is an alternative gate
         (some builds may stamp the flag without the literal model string), so a
         record carrying EITHER harness-death marker is SYNTHETIC. `stop_reason ==
         "stop_sequence"` corroborates but never gates alone. → DEAD.
      3. EMPTY — a located assistant terminal with no content at all (nothing
         produced). → DEAD (no deliverable to fold).
      4. HEALTHY — a real-model terminal with content. The fold may believe it
         (modulo the well-formed-but-empty residue, which is `effect_witness`'s job,
         not this terminal-state gate's — docs/197 §6.4).
    """
    if not evidence.readable:
        return ResultStateVerdict(
            state=TerminalState.UNREADABLE,
            dead=False,
            reason="transcript unreadable — declining to claim a death (fail-safe)",
        )
    # The harness-death markers. `model == "<synthetic>"` is the load-bearing one
    # (100% of real synthetic records); `isApiErrorMessage` is the corroborating
    # top-level flag and an alternative gate (belt-and-braces against a build that
    # stamps the flag but not the literal model string).
    is_synthetic_model = evidence.model == SYNTHETIC_MODEL
    if is_synthetic_model or evidence.is_api_error:
        cls = _infer_class(evidence.api_status, evidence.text)
        marker = (
            "model=<synthetic>" if is_synthetic_model else "isApiErrorMessage=true"
        )
        corrob = (
            " + stop_reason=stop_sequence"
            if evidence.stop_reason == SYNTHETIC_STOP_REASON
            else ""
        )
        status = f" apiErrorStatus={evidence.api_status}" if evidence.api_status is not None else ""
        return ResultStateVerdict(
            state=TerminalState.SYNTHETIC,
            dead=True,
            cls=cls,
            api_status=evidence.api_status,
            reason=(
                f"harness-authored terminal ({marker}{corrob}{status}) — the result "
                f"is a {cls.value} error string, not a finding; route to DEAD and do "
                f"not fold"
            ),
        )
    if not evidence.found:
        return ResultStateVerdict(
            state=TerminalState.EMPTY,
            dead=True,
            reason="no assistant terminal record found — the worker produced no result",
        )
    if not evidence.has_content:
        return ResultStateVerdict(
            state=TerminalState.EMPTY,
            dead=True,
            reason="terminal assistant record carried no content — no result to fold",
        )
    return ResultStateVerdict(
        state=TerminalState.HEALTHY,
        dead=False,
        reason="terminal assistant record is real-model authored with content",
    )


# ---------------------------------------------------------------------------
# The PURE refusal-envelope renderer — a verdict in, a wedge_reason-style envelope out.
# ---------------------------------------------------------------------------
def refusal_envelope(verdict: ResultStateVerdict) -> dict:
    """A `wedge_reason`-shaped refusal envelope for a DEAD verdict. PURE.

    Mirrors the no-pick envelope shape `wedge_reason.envelope_is_refusal` reads (the
    `do_not_render`/`blocked`/`reason_class` rungs), so a DEAD result-state can be
    surfaced through the SAME refusal plumbing as a dispatch no-pick. A non-DEAD
    verdict yields a non-refusal (`all_clear`) envelope. `reason_class` carries a
    stable, log-greppable token (`RESULT_DEAD_<CLASS>` / `RESULT_EMPTY`).
    """
    if not verdict.dead:
        return {
            "all_clear": True,
            "verdict": "LIVE",
            "state": verdict.state.value,
            "reason": verdict.reason,
        }
    if verdict.state is TerminalState.SYNTHETIC:
        reason_class = f"RESULT_DEAD_{verdict.cls.value}"
    else:
        reason_class = "RESULT_EMPTY"
    return {
        "do_not_render": True,
        "blocked": True,
        "all_clear": False,
        "verdict": "WEDGE",
        "reason_class": reason_class,
        "state": verdict.state.value,
        "api_status": verdict.api_status,
        "reason": verdict.reason,
    }


# ---------------------------------------------------------------------------
# Boundary I/O — read the terminal assistant record from a transcript JSONL.
# NOT pure (reads a file); reuses claim_extract's transcript reader so the two
# can't drift, the git_delta "I/O at the boundary" discipline.
# ---------------------------------------------------------------------------
def _leading_text(content: object) -> tuple[str, bool]:
    """The leading text of a message `content` + whether it carried ANY content. PURE.

    A synthetic record's content is `[{"type":"text","text":"API Error: …"}]`; a
    healthy record may be text and/or tool_use blocks. Returns `(leading_text,
    has_content)` — `has_content` is True if there is any text OR tool_use/tool_result
    block (so a tool-only terminal is not mis-flagged EMPTY). Bounded to a prefix.
    """
    if isinstance(content, str):
        s = content.strip()
        return (s[:400], bool(s))
    if isinstance(content, list):
        lead = ""
        has = False
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                t = b.get("text", "")
                if isinstance(t, str) and t:
                    has = True
                    if not lead:
                        lead = t.strip()[:400]
            elif bt in ("tool_use", "tool_result", "thinking", "image"):
                has = True
        return (lead, has)
    return ("", False)


def _api_status_int(value: object) -> Optional[int]:
    """Coerce a top-level `apiErrorStatus` to int, or None. PURE. Tolerant of a
    string-coded status; any non-coercible value → None (detail-only, never gates)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def terminal_evidence_from_record(record: dict) -> Optional[TerminalEvidence]:
    """Build TerminalEvidence from ONE transcript record IFF it is an assistant turn.

    Returns None for a non-assistant record (a user/tool_result line, a summary),
    so the boundary reader can walk to the LAST assistant record. The synthetic
    death is itself an assistant record (`type:"assistant"`, `message.role:
    "assistant"`) — so it IS captured by this walk. PURE.
    """
    if not isinstance(record, dict):
        return None
    msg = record.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return None
    text, has_content = _leading_text(msg.get("content"))
    model = msg.get("model")
    return TerminalEvidence(
        found=True,
        readable=True,
        model=model if isinstance(model, str) else None,
        stop_reason=msg.get("stop_reason") if isinstance(msg.get("stop_reason"), str) else None,
        # `isApiErrorMessage` and `apiErrorStatus` are TOP-LEVEL siblings of
        # `message` in real records (NOT inside message — the docs/197 §2.1
        # correction), so read them from the record, not msg.
        is_api_error=bool(record.get("isApiErrorMessage")),
        api_status=_api_status_int(record.get("apiErrorStatus")),
        text=text,
        has_content=has_content,
    )


def terminal_evidence_from_transcript(path: str) -> TerminalEvidence:
    """Read a subagent transcript JSONL → the TerminalEvidence of its LAST assistant record.

    Reuses `claim_extract._read_lines` (the one transcript reader in the kernel) so
    the two cannot drift. Walks all records, keeping the LAST one that is an assistant
    turn (the terminal record — a synthetic death is an assistant record, so it is
    captured). Distinguishes the three not-found cases:

      * read/parse failure → `readable=False` (→ UNREADABLE, the fail-safe floor:
        never claim a death we could not witness).
      * read OK but no assistant record at all → `readable=True, found=False`
        (→ EMPTY).
      * read OK, an assistant record found → its fields (→ SYNTHETIC / EMPTY /
        HEALTHY by the pure verdict).
    """
    from dos import claim_extract
    try:
        lines = claim_extract._read_lines(path)
    except OSError:
        return TerminalEvidence(found=False, readable=False)
    last: Optional[TerminalEvidence] = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        try:
            import json
            obj = json.loads(s)
        except (ValueError, TypeError):
            continue
        ev = terminal_evidence_from_record(obj)
        if ev is not None:
            last = ev
    if last is None:
        return TerminalEvidence(found=False, readable=True)
    return last


def verify_transcript(path: str) -> ResultStateVerdict:
    """The composed fold-site check: read the terminal record + classify it.

    The one call a workflow stage / the CLI makes: `verify_transcript(transcript)`
    → a `ResultStateVerdict` whose `.dead` is the branch signal at the
    `.filter(Boolean)` fold. Boundary I/O + pure verdict, composed — the
    `liveness.classify` over `git_delta` shape, one rung over.
    """
    return classify_terminal(terminal_evidence_from_transcript(path))
