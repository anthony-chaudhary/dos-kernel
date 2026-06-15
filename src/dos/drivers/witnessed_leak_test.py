"""dos.drivers.witnessed_leak_test — the author-disjoint successor to opus-fable-mode's
`leak_test.py` (docs/339 §4).

The r/ClaudeCode post `opus-fable-mode` (u/coolreddy,
`Poorna-Repos/opus-fable-mode`) independently built docs/333's
verification-as-steering control loop — setpoint (an 8-rule governor), actuator
(a re-injection hook), sensor (`leak_test.py`) — to steer Opus 4.8 to *behave*
like the suspended Fable 5. Its one structural flaw is the one the whole DOS
program is about: **its sensor reads the agent's OWN output bytes** (median
words, tool:**text** ratio, self-opener %, caveat %), all docs/332 tier-1/3
forgeable signals. The agent can move every metric to target without the
underlying disposition changing — it optimizes the *narration* of conciseness,
not conciseness.

This module is the 2.0 docs/339 §4 prescribes: **keep the loop, swap the
sensor.** It reads the same `~/.claude/projects/**.jsonl`, buckets the same way
(by `message.model`, opus pre/post a cutoff vs. fable as the target), but
computes metrics whose witness the agent did NOT author, by joining each claim
to the kernel's existing adjudicators:

    forgeable signal (opus-fable-mode)  →  author-disjoint replacement (this)   witness
    tool : TEXT ratio                   →  tool : LANDED-EFFECT ratio           commit_audit
    "done"/"task" opener %              →  claim-vs-truth rate on "done" msgs    commit_audit / effect_witness
    caveat / hedge %                    →  typed-refusal rate (closed vocab)     reasons (refuse vocabulary)
    median words/msg                    →  (kept, ADVISORY — a forgeable proxy)  —

The reframe (docs/339 §4): opus-fable-mode measured "does Opus *sound* like
Fable?"; this measures "does Opus *do the work* Fable's texture was a proxy
for — land effects instead of narrating, ship decisions instead of hedging,
refuse legibly instead of armor-hedging?" His own governor rules 5 ("commit;
convert open questions to closed") and 8 ("act, don't narrate") are already
effect-shaped — they just lacked an effect-shaped sensor. This supplies it.

Why a DRIVER, not a kernel leaf (CLAUDE.md layering)
====================================================

It NAMES A HOST. `~/.claude/projects/<ws>/**.jsonl` is Claude Code's on-disk
session layout; `claude-opus-4-8` / `claude-fable-5` are vendor model ids. A
kernel module may name no host and no vendor (the `test_vendor_agnostic_kernel`
litmus), so this lives in `drivers/`, the one place a host/vendor name belongs.
It obeys the one-way arrow: it imports the kernel (`commit_audit`,
`effect_witness`, `evidence`, `config`, `reasons`); the kernel never imports it
(it is NOT in `drivers/__init__`'s eager imports — loaded on demand, like
`paste_log`/`ci_status`).

The kernel-discipline split (the `liveness.classify` / `commit_audit` shape)
============================================================================

The metric arithmetic is PURE — `fold_buckets(buckets) -> ConvergenceReport`
takes parsed `BucketStats` (data) and folds them into the convergence table, no
disk, no clock, no subprocess. That is the unit-test surface. ALL I/O — the
glob over `~/.claude/projects`, the per-line JSON parse, the git reads behind
the claim-vs-truth join — happens in the boundary readers below
(`scan_sessions`, `bucket_messages`), exactly as `commit_audit.classify` is pure
and `read_commit` shells out.

What it can and cannot witness (state it; do not pretend)
=========================================================

* tool:landed-effect — a non-forgeable *ratio over the bucket*: tool-use blocks
  the agent emitted (a count) vs. commits whose subject its own diff WITNESSES
  (`commit_audit` OK on a `diff-/data-witnessed` rung). The agent cannot pad the
  numerator into more landed effects: a commit only counts when git's diff
  corroborates its subject.
* claim-vs-truth — of the assistant messages that SAY "done"/"shipped"/"fixed"
  (a forgeable opener), what share land in a window where a witness (a
  diff-witnessed commit on the same bucket) corroborates *some* effect. This is
  the honest, log-scoped slice: the session JSONL does not carry a per-message
  effect key, so the join is bucket-windowed, not per-claim — labelled as such.
* typed-refusal — of the messages that HEDGE (the same caveat phrases
  opus-fable-mode counts), what share carry a *typed* refusal token from the
  workspace's closed `refuse` vocabulary instead of prose armor. A hedge with a
  kernel reason-class is legible distrust; a bare "to be fair" is forgeable
  texture. Higher = better (the inverse of opus-fable-mode's caveat %).
* median words/msg — KEPT but ADVISORY and explicitly labelled forgeable, the
  one texture proxy carried so the table lines up with the original.

It is a SENSOR, advisory — it reports a convergence verdict, it gates nothing.
"""

from __future__ import annotations

import dataclasses
import glob
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from dos import commit_audit as _ca
from dos import config as _config

__all__ = [
    "MODEL_OPUS",
    "MODEL_FABLE",
    "DEFAULT_CUTOFF",
    "BUCKET_OPUS_PRE",
    "BUCKET_OPUS_POST",
    "BUCKET_FABLE",
    "MessageFacts",
    "BucketStats",
    "MetricRow",
    "ConvergenceReport",
    "accumulate",
    "fold_buckets",
    "projects_dir_for",
    "encode_project_dirname",
    "scan_sessions",
    "build_report",
    "render_report",
]


# ---------------------------------------------------------------------------
# The host vocabulary — the model ids and the on-disk layout. THIS is the host
# name the driver layer exists to carry; a kernel module could not name these.
# ---------------------------------------------------------------------------

MODEL_OPUS = "claude-opus-4-8"
MODEL_FABLE = "claude-fable-5"

# The buckets, mirroring opus-fable-mode: opus split pre/post a cutoff (did the
# governor+sensor move opus?), fable the target it should converge toward.
BUCKET_OPUS_PRE = "opus_pre"
BUCKET_OPUS_POST = "opus_post"
BUCKET_FABLE = "fable"

# The default cutoff opus-fable-mode used (the date the governor went in). An ISO
# date; a message's timestamp >= cutoff is "post". Overridable at the boundary.
DEFAULT_CUTOFF = "2026-06-13"

# Cap per bucket so a giant log can't make a scan unbounded (opus-fable-mode's
# `--cap`, default 15k). 0 = unbounded.
DEFAULT_CAP = 15000


# The "done"-class openers — a message that CLAIMS an effect landed. Forgeable
# (the agent picks its first words); that is the POINT — we count how many of
# these claims a witness corroborates, not whether the agent typed them.
_DONE_OPENERS = (
    "done", "shipped", "ship", "fixed", "fix", "implemented", "landed",
    "completed", "complete", "resolved", "merged", "added", "created",
    "task complete", "all set", "finished",
)

# The hedge / unsolicited-caveat phrases opus-fable-mode counts (its caveat %).
# A message containing one is HEDGING. We then ask: does it ALSO carry a typed
# refusal token (legible distrust) — the non-forgeable upgrade of the same axis.
_HEDGE_PHRASES = (
    "to be fair", "that said", "honest caveat", "to be honest", "i should note",
    "it's worth noting", "worth noting", "one caveat", "a caveat", "i'll admit",
    "in fairness", "i'm not sure", "i am not sure", "hard to say",
    "it depends", "i could be wrong",
)

# A "tool block" / "text block" is a content-block type the session JSONL records.
_TOOL_BLOCK_TYPES = frozenset({"tool_use", "server_tool_use"})
_TEXT_BLOCK_TYPES = frozenset({"text"})


# ---------------------------------------------------------------------------
# The parsed facts of ONE assistant message — what a witness can read off it.
# Every field here is either env-recorded (block counts, timestamp, model) or a
# forgeable-opener FLAG (we count the flag's corroboration, never trust it).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MessageFacts:
    """One assistant message, reduced to the facts the metrics fold over. PURE data."""

    model: str
    ts: str                  # ISO-8601, as the session JSONL records it
    word_count: int          # words across this message's TEXT blocks (advisory)
    tool_blocks: int         # tool_use blocks emitted (env-recorded)
    text_blocks: int         # text blocks emitted (env-recorded)
    says_done: bool          # opens with a "done"-class word (forgeable claim)
    hedges: bool             # contains an unsolicited-caveat phrase
    has_typed_refusal: bool  # carries a token from the workspace refuse vocabulary


# ---------------------------------------------------------------------------
# The per-bucket accumulator — the SUM a fold needs, plus the witnessed
# numerators the boundary joins fill in (commits, corroborated claims). PURE.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BucketStats:
    """The folded counts for one model×window bucket. PURE — built by `accumulate`.

    The agent-authored axes (word_count, says_done, hedges) are kept only to
    DERIVE the non-forgeable ratios against a witness:

      * `landed_effects` is NOT a count of messages — it is the number of
        WITNESSED commits attributed to this bucket's window (a `commit_audit`
        OK on a diff-/data-witnessed rung). The agent cannot inflate it by
        narrating; git's diff has to corroborate the commit subject.
      * `done_claims_corroborated` is, of the `done_claims` messages, how many
        fall in a window a witness corroborates (a diff-witnessed commit landed
        in the bucket). Bucket-windowed, not per-claim — the session JSONL has
        no per-message effect key — so it is an honest lower-resolution join,
        labelled as such in the render.
      * `typed_refusals` is, of the `hedges` messages, how many carry a kernel
        `refuse` token (legible distrust) rather than only prose armor.
    """

    bucket: str
    messages: int = 0
    word_total: int = 0
    tool_blocks: int = 0
    text_blocks: int = 0
    done_claims: int = 0
    done_claims_corroborated: int = 0
    hedges: int = 0
    typed_refusals: int = 0
    # The witnessed numerator the boundary fills in (commits this bucket's window
    # owns whose subject its own diff witnessed). Defaults 0 for a pure fold test.
    landed_effects: int = 0

    # --- the four metrics, each a pure property over the counts above ---

    @property
    def median_words(self) -> float:
        """Mean words/msg — the ADVISORY, forgeable texture proxy (kept for the
        original's table alignment). We carry a mean, not a true median, because
        the streaming accumulator keeps a sum not a sample; labelled advisory so
        no verdict rests on it."""
        if self.messages <= 0:
            return 0.0
        return self.word_total / self.messages

    @property
    def tool_landed_effect_ratio(self) -> float:
        """tool-use blocks : WITNESSED landed effects. The non-forgeable analogue
        of opus-fable-mode's tool:text — actions per *witnessed* commit, not per
        prose block. Lower means fewer tool actions per landed effect (more of the
        work actually lands). 0 effects → 0.0 (undefined, reported as no-data)."""
        if self.landed_effects <= 0:
            return 0.0
        return self.tool_blocks / self.landed_effects

    @property
    def claim_truth_rate(self) -> float:
        """Of the messages that SAY done, the share a witness corroborates. Higher
        is better (more claims that land). 0 claims → 0.0 (no-data)."""
        if self.done_claims <= 0:
            return 0.0
        return self.done_claims_corroborated / self.done_claims

    @property
    def typed_refusal_rate(self) -> float:
        """Of the HEDGING messages, the share that carry a typed refusal token
        (legible distrust) instead of only prose armor. Higher is better — the
        inverse of opus-fable-mode's caveat %. 0 hedges → 0.0 (no-data)."""
        if self.hedges <= 0:
            return 0.0
        return self.typed_refusals / self.hedges

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "messages": self.messages,
            "tool_blocks": self.tool_blocks,
            "text_blocks": self.text_blocks,
            "done_claims": self.done_claims,
            "done_claims_corroborated": self.done_claims_corroborated,
            "hedges": self.hedges,
            "typed_refusals": self.typed_refusals,
            "landed_effects": self.landed_effects,
            "mean_words": round(self.median_words, 1),
            "tool_landed_effect_ratio": round(self.tool_landed_effect_ratio, 3),
            "claim_truth_rate": round(self.claim_truth_rate, 3),
            "typed_refusal_rate": round(self.typed_refusal_rate, 3),
        }


def accumulate(
    facts: Iterable[MessageFacts],
    *,
    bucket: str,
    corroborated_done: int = 0,
    landed_effects: int = 0,
) -> BucketStats:
    """Fold a bucket's `MessageFacts` into a `BucketStats`. PURE — no I/O.

    `corroborated_done` / `landed_effects` are the WITNESS-side numerators the
    boundary supplies (it does the git join); a pure fold test passes them
    directly. The agent-authored counts (words, done-claims, hedges) come from
    the facts; the witnessed counts come from outside the agent's bytes.
    """
    messages = word_total = tool_blocks = text_blocks = 0
    done_claims = hedges = typed_refusals = 0
    for f in facts:
        messages += 1
        word_total += max(0, f.word_count)
        tool_blocks += max(0, f.tool_blocks)
        text_blocks += max(0, f.text_blocks)
        if f.says_done:
            done_claims += 1
        if f.hedges:
            hedges += 1
            if f.has_typed_refusal:
                typed_refusals += 1
    return BucketStats(
        bucket=bucket,
        messages=messages,
        word_total=word_total,
        tool_blocks=tool_blocks,
        text_blocks=text_blocks,
        done_claims=done_claims,
        done_claims_corroborated=min(corroborated_done, done_claims),
        hedges=hedges,
        typed_refusals=typed_refusals,
        landed_effects=max(0, landed_effects),
    )


# ---------------------------------------------------------------------------
# The convergence table — the PURE fold over the three buckets. Mirrors
# opus-fable-mode's `pre → post (target) [✓/✗]` verdict, per non-forgeable axis.
# ---------------------------------------------------------------------------

# Per-metric "direction toward target": True = lower is better (closer to fable
# is the smaller number), False = higher is better. We do NOT hardcode fable's
# value as the target — fable's OWN measured value IS the target, exactly as the
# original used fable as the setpoint.
_METRICS = (
    # (key, label, higher_is_better, advisory)
    ("tool_landed_effect_ratio", "tool:landed-effect", False, False),
    ("claim_truth_rate", "claim-vs-truth", True, False),
    ("typed_refusal_rate", "typed-refusal", True, False),
    ("median_words", "median words/msg (ADVISORY)", False, True),
)


@dataclasses.dataclass(frozen=True)
class MetricRow:
    """One metric's pre/post/target values + its convergence verdict. PURE.

    `verdict` mirrors opus-fable-mode: CONVERGING (post moved toward the fable
    target vs. pre), DIVERGING (moved away), MOVED (changed but the direction is
    ambiguous), STEADY (no change), or NO_DATA (a denominator was 0 in a bucket
    the metric needs). `advisory` flags the one forgeable proxy carried for
    alignment — its verdict is informational, never a steering signal.
    """

    key: str
    label: str
    pre: float
    post: float
    target: float
    verdict: str
    advisory: bool

    @property
    def converging(self) -> bool:
        return self.verdict == "CONVERGING"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "pre": round(self.pre, 3),
            "post": round(self.post, 3),
            "target": round(self.target, 3),
            "verdict": self.verdict,
            "advisory": self.advisory,
        }


@dataclasses.dataclass(frozen=True)
class ConvergenceReport:
    """The folded answer — the per-metric convergence table + the headline. PURE."""

    rows: tuple[MetricRow, ...]
    opus_pre: BucketStats
    opus_post: BucketStats
    fable: BucketStats
    cutoff: str

    @property
    def non_advisory_rows(self) -> tuple[MetricRow, ...]:
        return tuple(r for r in self.rows if not r.advisory)

    @property
    def converging_count(self) -> int:
        return sum(1 for r in self.non_advisory_rows if r.converging)

    @property
    def scored_count(self) -> int:
        """Non-advisory metrics that actually scored (had data, not NO_DATA)."""
        return sum(1 for r in self.non_advisory_rows if r.verdict != "NO_DATA")

    @property
    def verdict(self) -> str:
        """The headline: are the non-forgeable axes converging toward fable?

        CONVERGING iff a strict majority of the SCORED non-forgeable axes are
        converging; INSUFFICIENT if nothing scored; otherwise NOT_CONVERGING.
        Advisory (forgeable) rows never count toward the headline — the whole
        point of the 2.0 is that the verdict rests on non-forgeable axes only.
        """
        scored = self.scored_count
        if scored <= 0:
            return "INSUFFICIENT"
        return "CONVERGING" if self.converging_count * 2 > scored else "NOT_CONVERGING"

    def to_dict(self) -> dict:
        return {
            "cutoff": self.cutoff,
            "verdict": self.verdict,
            "converging": self.converging_count,
            "scored": self.scored_count,
            "rows": [r.to_dict() for r in self.rows],
            "buckets": {
                BUCKET_OPUS_PRE: self.opus_pre.to_dict(),
                BUCKET_OPUS_POST: self.opus_post.to_dict(),
                BUCKET_FABLE: self.fable.to_dict(),
            },
        }


def _verdict_for(pre: float, post: float, target: float,
                 *, higher_is_better: bool, pre_n: int, post_n: int,
                 fable_n: int) -> str:
    """The per-metric convergence verdict. PURE — pure arithmetic over the values.

    NO_DATA whenever a bucket the metric needs is empty (a 0.0 from a 0
    denominator is "no data", not "the value is zero"); otherwise compare the
    pre→post move against the fable target: closer = CONVERGING, farther =
    DIVERGING, unchanged = STEADY. `higher_is_better` only affects how we read a
    raw 0.0 (it is still a no-data sentinel when the denominator was 0, caught by
    the n-checks), not the distance math — distance to target is direction-free.
    """
    if pre_n <= 0 or post_n <= 0 or fable_n <= 0:
        return "NO_DATA"
    d_pre = abs(pre - target)
    d_post = abs(post - target)
    if d_post < d_pre:
        return "CONVERGING"
    if d_post > d_pre:
        return "DIVERGING"
    return "STEADY"


def fold_buckets(opus_pre: BucketStats, opus_post: BucketStats,
                 fable: BucketStats, *, cutoff: str = DEFAULT_CUTOFF) -> ConvergenceReport:
    """Fold the three buckets into the convergence report. PURE — the test surface.

    For each metric, read the per-bucket value, take fable's value as the target
    (its own measured number, the setpoint), and verdict the opus pre→post move.
    """
    # The per-metric denominator counts that decide NO_DATA — a metric needs the
    # bucket field its ratio divides by to be non-zero.
    def _n(stats: BucketStats, key: str) -> int:
        if key == "tool_landed_effect_ratio":
            return stats.landed_effects
        if key == "claim_truth_rate":
            return stats.done_claims
        if key == "typed_refusal_rate":
            return stats.hedges
        return stats.messages  # median_words

    rows: list[MetricRow] = []
    for key, label, higher_is_better, advisory in _METRICS:
        pre = getattr(opus_pre, key)
        post = getattr(opus_post, key)
        target = getattr(fable, key)
        verdict = _verdict_for(
            pre, post, target,
            higher_is_better=higher_is_better,
            pre_n=_n(opus_pre, key), post_n=_n(opus_post, key),
            fable_n=_n(fable, key),
        )
        rows.append(MetricRow(
            key=key, label=label, pre=pre, post=post, target=target,
            verdict=verdict, advisory=advisory,
        ))
    return ConvergenceReport(
        rows=tuple(rows), opus_pre=opus_pre, opus_post=opus_post,
        fable=fable, cutoff=cutoff,
    )


# ---------------------------------------------------------------------------
# Boundary I/O — the host-layout read + the witness joins. NOT pure.
#
# This is the part a kernel module could not contain: it names `~/.claude/
# projects`, the vendor model ids, and shells out to git through `commit_audit`.
# Every failure degrades to a safe empty (the `git_delta`/`ci_status` posture):
# a missing dir, a torn JSONL line, a non-git repo — none crash a scan.
# ---------------------------------------------------------------------------


def encode_project_dirname(workspace: Path | str) -> str:
    """Claude Code's on-disk project-dir encoding of a workspace path.

    Claude Code stores each project's sessions under
    `~/.claude/projects/<encoded>/`, where `<encoded>` is the absolute path with
    EACH non-alphanumeric character replaced by a single dash — runs are NOT
    collapsed, so `C:\\proj\\demo-kernel` → `C--proj-demo-kernel` (the `:` and the
    `\\` each become a dash). HOST KNOWLEDGE — exactly why this is a driver. We
    derive it rather than guess so a scan finds the workspace's own logs.
    """
    real = str(Path(workspace).resolve())
    return re.sub(r"[^A-Za-z0-9]", "-", real)


def projects_dir_for(workspace: Path | str,
                     home: Optional[Path | str] = None) -> Path:
    """The `~/.claude/projects/<encoded-workspace>/` dir for a workspace. PURE path.

    `home` overrides `~` for hermetic tests (no real `$HOME` read). Never creates
    anything — a read-only resolver.
    """
    base = Path(home) if home is not None else Path.home()
    return base / ".claude" / "projects" / encode_project_dirname(workspace)


def _iter_assistant_messages(path: Path) -> Iterable[dict]:
    """Yield each assistant `message` dict from one session JSONL. Boundary I/O.

    Torn-tail / corrupt-line tolerant (the `read_jsonl` discipline): a blank or
    unparseable line is skipped, never fatal. Only `type=="assistant"` records
    with a dict `message` are yielded; everything else (user, system, mode, …)
    is ignored. A read error on the file degrades to no rows.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        # carry the envelope timestamp onto the message (the model id is on the
        # message; the timestamp is on the envelope).
        if "timestamp" in obj and "_ts" not in msg:
            msg = {**msg, "_ts": obj.get("timestamp")}
        yield msg


_WORD_RE = re.compile(r"\b\w+\b")


def _message_text(content: object) -> str:
    """Concatenate the TEXT blocks of a message's content. PURE."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for b in content:
        if isinstance(b, dict) and b.get("type") in _TEXT_BLOCK_TYPES:
            t = b.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def _count_blocks(content: object) -> tuple[int, int]:
    """(tool_blocks, text_blocks) over a message's content. PURE."""
    if not isinstance(content, list):
        # a bare-string message is one text block, no tool blocks
        return (0, 1 if isinstance(content, str) and content.strip() else 0)
    tool = text = 0
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt in _TOOL_BLOCK_TYPES:
            tool += 1
        elif bt in _TEXT_BLOCK_TYPES:
            text += 1
    return tool, text


def _opens_done(text: str) -> bool:
    """Does the message OPEN with a 'done'-class word? PURE (the forgeable claim)."""
    head = text.strip().lower()
    if not head:
        return False
    # look at the first ~12 words so "Done." and "Task complete —" both hit, but
    # a "done" buried mid-paragraph does not (the opener axis, like the original).
    window = " ".join(_WORD_RE.findall(head)[:12])
    for opener in _DONE_OPENERS:
        if window == opener or window.startswith(opener + " ") or head.startswith(opener):
            return True
    return False


def _hedges(text: str) -> bool:
    """Does the message contain an unsolicited-caveat phrase? PURE."""
    low = text.lower()
    return any(p in low for p in _HEDGE_PHRASES)


def _carries_typed_refusal(text: str, tokens: frozenset[str]) -> bool:
    """Does the message name a token from the workspace's closed refuse vocabulary?

    A whole-token match (the reason tokens are UPPER_SNAKE, e.g. SELF_MODIFY,
    LANE_DRAINED) — a legible, machine-checkable refusal vs. prose armor. PURE.
    """
    if not tokens:
        return False
    found = set(re.findall(r"[A-Z][A-Z0-9_]{2,}", text))
    return bool(found & tokens)


def _refuse_tokens(workspace: Path | str) -> frozenset[str]:
    """The workspace's closed refuse-reason vocabulary (built-ins + dos.toml). I/O.

    Read through the SubstrateConfig so a workspace that declares extra reasons in
    `dos.toml [reasons]` contributes them. Degrades to the built-ins on any
    config error.
    """
    try:
        cfg = _config.load_workspace_config(workspace, warn=lambda *_a, **_k: None)
        reg = cfg.reasons
        toks = {t.strip().upper() for t in reg.tokens() if t and t.strip()}
        return frozenset(toks)
    except Exception:  # noqa: BLE001 — a config read fault degrades to no tokens
        return frozenset()


def _facts_of(msg: dict, refuse_tokens: frozenset[str]) -> Optional[MessageFacts]:
    """One assistant `message` dict → `MessageFacts`, or None if it has no model. PURE."""
    model = msg.get("model")
    if not isinstance(model, str) or not model:
        return None
    content = msg.get("content")
    text = _message_text(content)
    tool_blocks, text_blocks = _count_blocks(content)
    return MessageFacts(
        model=model,
        ts=str(msg.get("_ts") or ""),
        word_count=len(_WORD_RE.findall(text)),
        tool_blocks=tool_blocks,
        text_blocks=text_blocks,
        says_done=_opens_done(text),
        hedges=_hedges(text),
        has_typed_refusal=_carries_typed_refusal(text, refuse_tokens),
    )


def _bucket_of(facts: MessageFacts, cutoff: str) -> Optional[str]:
    """Which bucket does a message fall in? PURE.

    Fable → BUCKET_FABLE (any date). Opus → pre/post the cutoff by its timestamp;
    a missing/unparseable timestamp is treated as PRE (the conservative side — it
    cannot manufacture post-cutoff convergence). A non-opus, non-fable model
    (`<synthetic>`, an older model) → None (excluded).
    """
    if facts.model == MODEL_FABLE:
        return BUCKET_FABLE
    if facts.model != MODEL_OPUS:
        return None
    ts = (facts.ts or "")[:10]  # the YYYY-MM-DD prefix; ISO sorts lexically
    if ts and ts >= cutoff:
        return BUCKET_OPUS_POST
    return BUCKET_OPUS_PRE


def scan_sessions(
    workspace: Path | str = ".",
    *,
    cutoff: str = DEFAULT_CUTOFF,
    home: Optional[Path | str] = None,
    cap: int = DEFAULT_CAP,
) -> dict[str, list[MessageFacts]]:
    """Read the workspace's session JSONL into the three buckets of `MessageFacts`.

    Boundary I/O: globs `~/.claude/projects/<encoded-ws>/*.jsonl`, parses each
    assistant message, classifies it, and buckets it — capped per bucket (`cap`,
    0 = unbounded). Returns `{bucket: [facts...]}`. A missing projects dir yields
    three empty buckets (the honest "no logs here"), never an error.
    """
    refuse_tokens = _refuse_tokens(workspace)
    pdir = projects_dir_for(workspace, home=home)
    buckets: dict[str, list[MessageFacts]] = {
        BUCKET_OPUS_PRE: [], BUCKET_OPUS_POST: [], BUCKET_FABLE: [],
    }
    if not pdir.is_dir():
        return buckets
    for fp in sorted(glob.glob(str(pdir / "*.jsonl"))):
        for msg in _iter_assistant_messages(Path(fp)):
            facts = _facts_of(msg, refuse_tokens)
            if facts is None:
                continue
            b = _bucket_of(facts, cutoff)
            if b is None:
                continue
            if cap and len(buckets[b]) >= cap:
                continue
            buckets[b].append(facts)
    return buckets


def _ts_window(facts: Iterable[MessageFacts]) -> tuple[str, str]:
    """The (earliest, latest) timestamp across a bucket's messages. PURE."""
    stamps = sorted(f.ts for f in facts if f.ts)
    if not stamps:
        return ("", "")
    return (stamps[0], stamps[-1])


def _witness_landed_effects(
    workspace: Path | str,
    window: tuple[str, str],
    *,
    limit: int = 2000,
) -> int:
    """Count the WITNESSED commits whose author-date falls in `window`. Boundary I/O.

    A commit counts iff `commit_audit` grades it OK on a non-forgeable rung
    (`diff-witnessed` / `data-witnessed`) — i.e. its subject's claim is
    corroborated by its own diff. This is the author-disjoint numerator: the
    agent's narration cannot add a landed effect; git's diff has to back it. An
    empty window or a non-git repo → 0 (the safe floor).
    """
    lo, hi = window
    if not lo or not hi:
        return 0
    # `git log --since/--until` over the bucket's time window; ISO timestamps are
    # accepted by git. Then audit each commit's claim-vs-diff.
    rc, out = _ca._git(
        workspace, "log", f"-{int(limit)}", "--pretty=format:%H",
        f"--since={lo}", f"--until={hi}",
    )
    if rc != 0:
        return 0
    witnessed = 0
    for sha in out.splitlines():
        sha = sha.strip()
        if not sha:
            continue
        v = _ca.audit_commit(sha, root=workspace)
        if v is None:
            continue
        if v.verdict is _ca.Verdict.OK and v.witness in (
            _ca.Witness.DIFF_WITNESSED, _ca.Witness.DATA_WITNESSED
        ):
            witnessed += 1
    return witnessed


def build_report(
    workspace: Path | str = ".",
    *,
    cutoff: str = DEFAULT_CUTOFF,
    home: Optional[Path | str] = None,
    cap: int = DEFAULT_CAP,
) -> ConvergenceReport:
    """The one-call entry: scan the logs, join the witnesses, fold the report.

    The boundary orchestrator (the `audit_commit` posture). It scans the session
    logs into buckets, then for each bucket joins the non-forgeable numerators
    (landed effects via `commit_audit` over the bucket's git window; corroborated
    done-claims gated on at least one landed effect in the window), and folds the
    convergence report. Read-only — takes no lease, writes nothing.
    """
    buckets = scan_sessions(workspace, cutoff=cutoff, home=home, cap=cap)
    stats: dict[str, BucketStats] = {}
    for name, facts in buckets.items():
        window = _ts_window(facts)
        landed = _witness_landed_effects(workspace, window)
        # claim-vs-truth, log-scoped: a "done" claim is corroborated when its
        # bucket's window actually landed a witnessed effect. With no witnessed
        # effect in the window, NO done-claim in that bucket is corroborated (the
        # honest direction — an unwitnessed window confirms nothing). When the
        # window DID land effects, we credit the done-claims up to the number of
        # landed effects (each witnessed commit corroborates at most one claim) —
        # a conservative lower bound, never a per-claim overclaim.
        n_done = sum(1 for f in facts if f.says_done)
        corroborated = min(n_done, landed) if landed > 0 else 0
        stats[name] = accumulate(
            facts, bucket=name,
            corroborated_done=corroborated, landed_effects=landed,
        )
    return fold_buckets(
        stats[BUCKET_OPUS_PRE], stats[BUCKET_OPUS_POST], stats[BUCKET_FABLE],
        cutoff=cutoff,
    )


# ---------------------------------------------------------------------------
# The render — the `pre → post (target) [✓/✗]` table, mirroring opus-fable-mode.
# ---------------------------------------------------------------------------

_VERDICT_MARK = {
    "CONVERGING": "✓ converging",
    "DIVERGING": "✗ diverging",
    "STEADY": "= steady",
    "MOVED": "~ moved, check",
    "NO_DATA": "· insufficient data",
}


def render_report(report: ConvergenceReport) -> str:
    """The convergence table + headline, as plain text. PURE.

    Leads with the per-bucket sample sizes (honest small-n, the docs/332 §5
    posture), then a `pre → post (target) [verdict]` line per metric, then the
    headline over the NON-forgeable axes only.
    """
    pre, post, fab = report.opus_pre, report.opus_post, report.fable
    out: list[str] = []
    out.append("dos witnessed-leak-test — author-disjoint convergence (docs/339 §4)")
    out.append(
        f"  buckets (cutoff {report.cutoff}): "
        f"opus_pre={pre.messages} msgs, opus_post={post.messages} msgs, "
        f"fable={fab.messages} msgs"
    )
    out.append(
        f"  witnessed effects: opus_pre={pre.landed_effects}, "
        f"opus_post={post.landed_effects}, fable={fab.landed_effects} "
        f"(commits whose diff witnesses their subject — commit_audit)"
    )
    out.append("")
    out.append("  metric                         opus_pre   opus_post   FABLE(target)   verdict")
    for r in report.rows:
        mark = _VERDICT_MARK.get(r.verdict, r.verdict)
        out.append(
            f"  {r.label:<28} {r.pre:>9.3f}  {r.post:>9.3f}   {r.target:>11.3f}   "
            f"{r.pre:.3f} → {r.post:.3f} (target {r.target:.3f}) [{mark}]"
        )
    out.append("")
    headline = {
        "CONVERGING": "Opus is converging toward Fable on the NON-FORGEABLE axes",
        "NOT_CONVERGING": "Opus is NOT converging toward Fable on the non-forgeable axes",
        "INSUFFICIENT": "insufficient data on the non-forgeable axes (no witnessed window)",
    }.get(report.verdict, report.verdict)
    out.append(
        f"  VERDICT: {report.verdict} — {headline} "
        f"({report.converging_count}/{report.scored_count} scored axes converging)"
    )
    out.append(
        "  (median words/msg is ADVISORY — a forgeable texture proxy, excluded "
        "from the verdict)"
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Module-as-script — run it against this repo's own logs and print the verdict.
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m dos.drivers.witnessed_leak_test",
        description=("the author-disjoint successor to opus-fable-mode's leak_test "
                     "— non-forgeable convergence over ~/.claude/projects logs"),
    )
    parser.add_argument("--workspace", default=".",
                        help="the repo whose session logs + git to read (default: cwd)")
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF,
                        help=f"opus pre/post split date (default {DEFAULT_CUTOFF})")
    parser.add_argument("--home", default="",
                        help="override ~ for the projects dir (test/hermetic use)")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP,
                        help=f"max messages per bucket (default {DEFAULT_CAP}, 0=all)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = build_report(
        args.workspace, cutoff=args.cutoff,
        home=(args.home or None), cap=args.cap,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
