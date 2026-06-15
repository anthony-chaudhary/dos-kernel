"""skill_grade — score how well a skill grounds its belief-bits on `dos` verbs.

A skill is a **screenplay** (EXAMPLES.md): prose that tells an agent how to do a
job. The kernel's one rule, aimed at a skill, is *never set a "done / safe /
found" bit from what the agent says — set it from a read-back the agent did not
author.* So the quality of a skill's DOS usage is measurable: of all the places
the skill concludes something is true (a **belief-bit**), what fraction are
**grounded** on a `dos` verb whose byte-author is not the agent, versus
**self-certified** (re-reads its own output, greps subjects itself,
`filter(Boolean)`s a fan-out)?

This module is that measurement. It is the **deterministic core** docs/345 §3
foretold: "promote the deterministic core to a verb only once the skill proves
the claim-site taxonomy is right." The dogfood (docs/345 §4,
`.dos/scratch/skillify_dogfood_report.md`) proved it by hand; this scores it
re-runnably.

It emits a **grounding-density signal**, deliberately NOT a precise per-belief-bit
grade. For each detected belief-bit it asks one robust question — *does this
step shell a `dos` verb near its trust language at all?* — and folds the answers:

  - GROUNDED        — the bit's step shells a `dos` verb (a witness it can read).
  - REVIEW CANDIDATE — the bit concludes truth but no `dos` verb is near it; it
                       MAY be a self-certify, or the scanner just missed the
                       grounding. It is a candidate for the `dos-skillify` model
                       pass to confirm — NOT a confirmed failure.
  - EXCLUDED         — a claim no CLI verb can ground today (EFFECT, CI_GREEN,
                       LOOP_LIVE → UNWITNESSABLE), or a phrase the skill WARNS
                       against (an `❌` / "never X" mention — the skill teaching
                       grounding, not failing it). Excluded sites count in
                       neither tally (the honesty floor: never penalise a claim
                       no verb can ground; never pad the score with a faked one).

  signal = STRONG / MODERATE / WEAK / NONE        from grounded vs. candidates

**Why a signal, not a coverage percentage.** A static scanner cannot replicate
the model's per-bit classification: real skills phrase belief-bits in ways no
keyword list fully captures and ground them with verbs (`dos enumerate`,
`dos reconcile`, `dos judge-eval`) beyond any fixed claim→verb map. Forcing a
precise number would mean reverse-engineering the scanner to a known answer — the
exact bias DOS refuses (docs/333). So this reports a coarse, honest estimate and
hands the precise verdict to the model running the `dos-skillify` screenplay. The
self-certify SMELLS it surfaces (a "shipped" with no verb in the step) are
reliable; the absence of a smell is not proof of grounding. This mirrors the
LIVE-vs-SUBMITTED discipline in `discoverability_inventory.py`: never inflate on
a promise, and say plainly what the number is and is not.

Layer 3, pure: text + the `dos doctor` facts in, a score out. No I/O, no host
name. The claim-kind tokens and `dos` verb names are domain-free DOS vocabulary
(read against `dos doctor --json` so the verb set is data, never a literal).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- the claim-kind taxonomy as data (1:1 with the docs/345 §2 / dos-skillify
# Step-1 table; there is no other Python representation of it). Each row:
#   token        — the claim_kind name
#   recipe       — the EXAMPLES.md recipe id (provenance, for the report)
#   verbs        — the `dos` verb(s) that ground this claim, as they appear in
#                  skill text ("dos verify" …). Empty ⇒ no first-party CLI verb
#                  today ⇒ the claim is UNWITNESSABLE by a CLI grounding.
#   witnessable  — does a CLI verb exist to ground it? False ⇒ excluded from the
#                  coverage denominator (the honesty floor).
#   seeds        — the belief-bit detector: phrases that, in skill prose,
#                  signal this claim-kind. Seeds are matched case-insensitively
#                  as substrings; they only SEED the search — grounding is then
#                  confirmed by a verb token in the same window.


@dataclass(frozen=True)
class ClaimKind:
    token: str
    recipe: str
    verbs: tuple[str, ...]
    witnessable: bool
    seeds: tuple[str, ...]


# The order is the report order. Witnessable kinds first, then the abstain band.
CLAIM_KINDS: tuple[ClaimKind, ...] = (
    ClaimKind("SHIPPED", "R1", ("dos verify",), True,
              ("it shipped", "this code shipped", "phase shipped", "phases shipped",
               "is shipped", "already shipped", "already-shipped", "claimed shipped",
               "did (plan,phase) ship", "did it ship", "(plan, phase)", "(plan,phase)")),
    ClaimKind("COMMIT_HONEST", "R1", ("dos commit-audit",), True,
              ("commit's subject", "subject vs", "subject matches its diff",
               "the commit did what", "subject vs. its own diff", "subject vs its own diff",
               "i committed", "i committed it", "commit is honest", "commits are honest")),
    ClaimKind("GOAL_DONE", "R1b", ("dos hook stop",), True,
              ("goal is met", "until the goal", "keep working until", "the goal is complete",
               "until done", "keep going until", "self-stopping", "stop condition")),
    ClaimKind("WRITE", "R2", ("dos arbitrate",), True,
              ("editing these files", "edit these files", "no collision", "won't collide",
               "no one else is", "without colliding", "i'm editing", "before writing",
               "before you write", "take a lane", "take a lease")),
    ClaimKind("GATE", "R3", ("dos gate",), True,
              ("is there work", "safe to proceed", "safe to ship", "the empty case",
               "branch on the exit code", "is it safe", "gate the empty")),
    ClaimKind("RUN_STATE", "R4", ("dos status",), True,
              ("how is run", "how is the run", "how is run r doing", "run digest",
               "no `claimed` field", "no claimed field", "the digest")),
    ClaimKind("ALIVE", "R6", ("dos liveness", "dos lease-lane"), True,
              ("this long job is alive", "is the run alive", "this job is alive",
               "heartbeat", "still alive", "the wal beat", "wal beat alive")),
    ClaimKind("RECALL", "R7", ("dos recall", "dos memory"), True,
              ("recalled memory is still true", "this recalled memory", "re-verify at read",
               "trust this recalled", "re-verify the memory")),
    ClaimKind("FANOUT", "R9", ("dos verify-result", "dos coverage"), True,
              ("all n fan-out", "all n workers returned", "all 7 workers", "fan-out returned",
               "every worker returned", "all workers returned", "n of declared",
               "real coverage", "synthetic death", "synthetically-dead")),
    ClaimKind("ISSUE_DONE", "-", ("dos reward", "dos commit-audit"), True,
              ("this issue is resolved", "the issue is resolved", "this ticket is resolved",
               "close the issue", "issue is closed", "fixes #")),
    # --- the abstain band: no first-party CLI verb today ⇒ UNWITNESSABLE ⇒
    # excluded from the denominator. Detecting these is still useful: the report
    # shows them so a reader sees what DOS could NOT ground (and why).
    ClaimKind("EFFECT", "R9", (), False,
              ("i created file", "created file", "inserted row", "sent message",
               "deployed", "i created", "effect read-back", "external effect")),
    ClaimKind("CI_GREEN", "-", (), False,
              ("ci run concluded", "workflow run concluded", "ci concluded",
               "ci is green", "ci green", "run's conclusion", "conclusion field",
               "ci on the", "gh run view")),
    ClaimKind("LOOP_LIVE", "R5", (), False,
              ("tool-loop is progressing", "this tool-loop", "loop is making progress",
               "doomed tool-loop", "tool_stream", "distrust repetition")),
)

# A claim is recognised as a belief-bit only if it is one of the above. Anything
# the scanner cannot place is, by construction, NOT counted — the conservative
# default that keeps the score honest.

# The signal band from the grounded fraction of SCORED bits (grounded /
# (grounded + review-candidates)). Coarse on purpose — see the module docstring
# on why this is a signal, not a coverage grade.
SIGNAL_BANDS: tuple[tuple[float, str], ...] = (
    (0.85, "STRONG"),
    (0.60, "MODERATE"),
    (0.0, "WEAK"),
)

# The `--check` gate floor. A skill is FLAGGED only when the scanner clearly
# sees its belief-bits (>= _CHECK_MIN_SCORED scored) AND a meaningful share have
# no `dos` verb in their step (grounded fraction < min). Sparse detection is
# SKIPPED, never failed — the gate catches self-certify DENSITY, it never
# punishes the scanner being blind to an idiosyncratically-phrased skill.
#
# _CHECK_MIN_SCORED is set so the fraction is statistically meaningful: the
# scanner's per-bit precision is modest, so with only a few scored bits one
# false-positive swings the fraction wildly (4 bits → one miss is 25%). A
# genuinely self-certifying skill produces MANY belief-bits (every "shipped"
# with no verb), so it clears this sample floor easily and still fails; a
# well-grounded skill the scanner only partly parses stays under the floor and
# is skipped, not failed. Measured against the repo's own 19 skills: every
# grounded skill either scores >=0.6 or detects <8 bits (skipped); a synthetic
# all-self-certify skill scores 0.0 over 14 bits (failed). See docs/345.
_CHECK_MIN_SCORED = 8
_CHECK_DEFAULT_MIN_FRACTION = 0.6

# Grounding is scored at the SECTION level (the text between two markdown
# headers, `#`/`##`/`###`). A belief-bit is GROUNDED if ANY `dos` verb appears
# in the same section — the robust signal is "this step shells a witness at
# all", not "it shells the one verb my claim→verb map predicted" (real skills
# legitimately ground a SHIPPED bit with `dos verify` OR `dos reconcile` OR
# `dos enumerate`). The claim-kind's OWN verb is still recorded when present (a
# richer report row). A section is the right unit because a step's prose, its
# code fence, and its belief-bit travel together — a fixed window splits a step.
#
# Two contexts where a belief-bit phrase is NOT a live claim the skill makes —
# it is the skill TEACHING grounding — and so is excluded from scoring (it can
# neither be grounded nor ungrounded; counting it would penalise a skill for
# explaining the anti-pattern it prevents):
#
#   - an ANTI-PATTERN line: a `❌` bullet, or under an "Anti-pattern" header.
#   - a NEGATED / quoted-as-bad mention: the phrase sits next to language that
#     marks it as the thing NOT to do ("never", "don't", "instead of",
#     "re-read", "self-cert", "without a", "the failure", "exists to fix").
#
# This is the static scanner's honest approximation of what the model does by
# hand in the dogfood: it tells "the skill concludes X" from "the skill warns
# never conclude X without a verb" by surface cues, not by understanding — so it
# is deliberately conservative and labels these EXCLUDED rather than guess.
_NEGATION_CUES = (
    "never", "don't", "do not", "instead of", "rather than", "re-read",
    "self-cert", "self cert", "without a", "without the", "the failure",
    "exists to fix", "not when", "❌", "anti-pattern", "consistency, not",
    "the exact failure", "unfalsifiable", "by re-reading", "the agent's word",
    "the agent's say-so", "say-so", "trust \"i", "trust 'i",
    "byte-author", "not on its own", "not its own", "not the agent",
)


@dataclass
class Site:
    """One detected belief-bit and whether it is grounded."""
    claim_kind: str
    recipe: str
    grounded: bool
    witnessable: bool
    verb_seen: str   # the verb token that grounded it, or "" if none / n/a
    seed: str        # the phrase that triggered detection
    line: int        # 1-based source line of the seed
    excerpt: str     # the source line, trimmed
    excluded: str = ""  # "" = scored; else why excluded ("anti-pattern" /
    #                     "unwitnessable") — excluded sites count in neither
    #                     numerator nor denominator (the honesty floor).


@dataclass
class SkillGrade:
    skill: str
    sites: list[Site] = field(default_factory=list)

    @property
    def grounded(self) -> int:
        """Belief-bits whose step shells a `dos` verb (a witness it can read)."""
        return sum(1 for s in self.sites
                   if not s.excluded and s.witnessable and s.grounded)

    @property
    def review_candidates(self) -> int:
        """Belief-bits that conclude truth with no `dos` verb near them — a
        self-certify SMELL. NOT a confirmed failure: a candidate for the
        `dos-skillify` model pass to confirm or wave off."""
        return sum(1 for s in self.sites
                   if not s.excluded and s.witnessable and not s.grounded)

    @property
    def unwitnessable(self) -> int:
        return sum(1 for s in self.sites if s.excluded == "unwitnessable")

    @property
    def anti_pattern(self) -> int:
        """Belief-bit phrases the skill warns AGAINST (a `❌` / 'never X' /
        'the failure DOS fixes' mention). Excluded from scoring — the skill is
        teaching grounding here, not failing it."""
        return sum(1 for s in self.sites if s.excluded == "anti-pattern")

    @property
    def scored(self) -> int:
        return self.grounded + self.review_candidates

    @property
    def grounded_fraction(self) -> float | None:
        """grounded / (grounded + review-candidates). None when there is nothing
        scored — a pure-prose / all-excluded skill is N/A, never a 0 (you can't
        fail a skill there is no honest witness to ground)."""
        d = self.scored
        if d == 0:
            return None
        return self.grounded / d

    @property
    def signal(self) -> str:
        """The grounding-density signal. STRONG/MODERATE/WEAK on the grounded
        fraction; NONE when nothing is scorable."""
        frac = self.grounded_fraction
        if frac is None:
            return "NONE"
        for floor, label in SIGNAL_BANDS:
            if frac >= floor:
                return label
        return "WEAK"

    def to_dict(self) -> dict:
        frac = self.grounded_fraction
        return {
            "skill": self.skill,
            "signal": self.signal,
            "grounded_fraction": None if frac is None else round(frac, 4),
            "grounded": self.grounded,
            "review_candidates": self.review_candidates,
            "unwitnessable": self.unwitnessable,
            "anti_pattern": self.anti_pattern,
            "sites": [
                {
                    "claim_kind": s.claim_kind,
                    "recipe": s.recipe,
                    "grounded": s.grounded,
                    "witnessable": s.witnessable,
                    "verb_seen": s.verb_seen,
                    "seed": s.seed,
                    "line": s.line,
                    "excerpt": s.excerpt,
                    "excluded": s.excluded,
                }
                for s in self.sites
            ],
        }


# A `dos <verb>` / `dos_<verb>` token anywhere — the broad "this step shells a
# witness" detector. It catches verbs beyond any fixed claim→verb map
# (`dos enumerate`, `dos reconcile`, `dos judge-eval`, the `dos_*` MCP tools)
# without enumerating them — the verb set stays open, not a literal list.
_DOS_VERB_RE = re.compile(r"\bdos[ _][a-z][a-z-]+", re.IGNORECASE)


def _claim_kind_verbs(facts: dict | None) -> set[str]:
    """The per-claim-kind verb tokens — used only to RECORD which canonical verb
    grounded a bit (the richer report row), never to gate grounding (that is the
    broad `_DOS_VERB_RE`). Read additively from `dos doctor` facts so the names
    are data, not a literal (a host can add verbs, never shrink the set)."""
    declared: set[str] = set()
    for ck in CLAIM_KINDS:
        declared.update(ck.verbs)
    if facts:
        ec = facts.get("exit_codes") if isinstance(facts, dict) else None
        if isinstance(ec, dict):
            for verb in ec:
                declared.add(f"dos {verb}")
    return declared


def grade_skill(text: str, facts: dict | None = None, *, name: str = "") -> SkillGrade:
    """Score one skill's DOS grounding signal from its SKILL.md text.

    `text`  — the raw SKILL.md content.
    `facts` — the `dos doctor --json` dict (optional; supplies the canonical
              verb names as data for the report rows). None ⇒ the taxonomy verbs.
    `name`  — a label for the report (usually the skill dir name).
    """
    ck_verbs = _claim_kind_verbs(facts)
    grade = SkillGrade(skill=name)

    lines = text.splitlines()
    lowered_lines = [ln.lower() for ln in lines]

    # --- section map: each line → the [start, end) of its markdown section and
    # whether that section is an anti-pattern section (its header names one).
    header_idx = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("#")]

    def section_of(li: int) -> tuple[int, int]:
        start = 0
        for h in header_idx:
            if h <= li:
                start = h
            else:
                break
        end = len(lines)
        for h in header_idx:
            if h > li:
                end = h
                break
        return start, end

    def section_text(li: int) -> str:
        s, e = section_of(li)
        return "\n".join(lowered_lines[s:e])

    def section_is_antipattern(li: int) -> bool:
        s, _ = section_of(li)
        return "anti-pattern" in lowered_lines[s] or "anti pattern" in lowered_lines[s]

    # To avoid double-counting one belief-bit when two seeds of a kind land on
    # the same line, remember (claim_kind, line) pairs already emitted.
    seen: set[tuple[str, int]] = set()

    for ck in CLAIM_KINDS:
        for seed in ck.seeds:
            for li, low in enumerate(lowered_lines):
                if seed not in low:
                    continue
                # A markdown header line is a title, never a live belief-bit.
                if lines[li].lstrip().startswith("#"):
                    continue
                key = (ck.token, li)
                if key in seen:
                    continue
                seen.add(key)

                excerpt = lines[li].strip()
                if len(excerpt) > 120:
                    excerpt = excerpt[:117] + "..."

                # Is this a mention the skill WARNS against (teaching grounding),
                # not a live claim it makes? Such sites are excluded from scoring.
                excluded = ""
                if not ck.witnessable:
                    excluded = "unwitnessable"
                elif section_is_antipattern(li) or any(c in low for c in _NEGATION_CUES):
                    excluded = "anti-pattern"

                # Grounding: does ANY `dos` verb appear in the SAME section (the
                # step that states the bit)? That is the robust signal. If the
                # claim-kind's OWN canonical verb is the one present, record it
                # for a richer report row; otherwise note grounding generically.
                grounded = False
                verb_seen = ""
                if not excluded:
                    sect = section_text(li)
                    m = _DOS_VERB_RE.search(sect)
                    if m:
                        grounded = True
                        for v in ck.verbs:
                            if v in sect and v in ck_verbs:
                                verb_seen = v
                                break
                        if not verb_seen:
                            verb_seen = m.group(0).lower()

                grade.sites.append(Site(
                    claim_kind=ck.token,
                    recipe=ck.recipe,
                    grounded=grounded,
                    witnessable=ck.witnessable,
                    verb_seen=verb_seen,
                    seed=seed,
                    line=li + 1,
                    excerpt=excerpt,
                    excluded=excluded,
                ))

    # Stable report order: by source line.
    grade.sites.sort(key=lambda s: (s.line, s.claim_kind))
    return grade


def render_text(g: SkillGrade) -> str:
    """A compact human report for one skill."""
    L = [
        f"skill: {g.skill}",
        f"DOS grounding: {g.grounded} verb-grounded bits, "
        f"{g.review_candidates} review candidate(s), "
        f"{g.anti_pattern} anti-pattern mention(s) excluded",
        f"signal: {g.signal}  (a static estimate, NOT the model's per-bit "
        f"verdict — run the dos-skillify skill for the precise classification)",
    ]
    if g.unwitnessable:
        L.append(f"unwitnessable: {g.unwitnessable} (no CLI verb to ground — excluded)")
    if g.review_candidates:
        L.append("")
        L.append("review candidates (a belief-bit with no `dos` verb in its step — "
                 "confirm with the model whether it self-certifies):")
        for s in g.sites:
            if not s.excluded and s.witnessable and not s.grounded:
                verbs = ", ".join(_kind(s.claim_kind).verbs) or "(no CLI verb)"
                L.append(f"  L{s.line} [{s.claim_kind}/{s.recipe}] "
                         f"would ground with `{verbs}`")
                L.append(f"        {s.excerpt}")
    return "\n".join(L)


def _kind(token: str) -> ClaimKind:
    for ck in CLAIM_KINDS:
        if ck.token == token:
            return ck
    raise KeyError(token)


def check_verdict(g: SkillGrade, min_fraction: float = _CHECK_DEFAULT_MIN_FRACTION
                  ) -> tuple[str, str]:
    """The `--check` verdict for one skill: ("pass" | "fail" | "skip", why).

    - "skip" — too few belief-bits detected to judge (the scanner is blind to
      this skill's phrasing). NOT a failure.
    - "fail" — the scanner clearly sees the bits and a meaningful share have no
      `dos` verb in their step (a self-certify smell density).
    - "pass" — judged and at/above the floor.
    """
    if g.scored < _CHECK_MIN_SCORED:
        return ("skip", f"only {g.scored} belief-bit(s) detected — too sparse to judge")
    frac = g.grounded_fraction
    if frac is None:
        return ("skip", "nothing scorable")
    if frac < min_fraction:
        return ("fail",
                f"grounding {frac * 100:.0f}% < {min_fraction * 100:.0f}% floor "
                f"({g.review_candidates} review candidate(s) over {g.scored} bits)")
    return ("pass", f"grounding {frac * 100:.0f}% (signal {g.signal})")
