"""Pin the skill DOS grader (`src/dos/skill_grade.py` + `dos skillify --grade`).

The grader scores how well a skill grounds its belief-bits on `dos` verbs. It is
deliberately a coarse *signal*, not the model's per-bit verdict (docs/345 §3):
forcing a precise number would mean reverse-engineering the scanner to a known
answer — the bias DOS refuses. These tests pin the contract that keeps the
signal honest:

  * a grounded synthetic skill scores STRONG; a self-certifying one scores WEAK
    with its self-certify smells surfaced as REVIEW CANDIDATES (not failures);
  * UNWITNESSABLE claims (no CLI verb to ground) and ANTI-PATTERN mentions (the
    skill teaching grounding) are EXCLUDED from the denominator — the honesty
    floor: never penalise a claim no verb can ground, never pad with a faked one;
  * a pure-prose skill is N/A, never a 0 (you can't fail a skill there's no
    honest witness to ground);
  * the repo's own grounded skills are NOT falsely flagged by `--check` — the
    gate skips sparse detection and only fires on a real self-certify density;
  * `dos skillify --grade --all --check` exits 0 on the real tree; an impossible
    floor exits 1 (the gate actually bites).
"""
from __future__ import annotations

from dos import skill_grade as sg
from dos.cli import main as cli_main


# --- synthetic fixtures (neutral, self-contained — no host path) -------------

_GROUNDED = """# good-skill
## Step 1 — ship it
For each phase, never trust "I committed it"; ask the truth syscall:
`dos verify PLAN PHASE` and read the rung. The phase is shipped only when
git ancestry backs it.
## Step 2 — the commit is honest
`dos commit-audit HEAD` — the commit did what its subject says (subject vs diff).
## Step 3 — coordinate the write
Before editing these files, `dos arbitrate --lane L` over the tree so no
collision. Honor the redirect.
## Step 4 — the goal
Wire `dos hook stop` alongside the harness goal; the goal is met only when a
witness the agent did not author backs it.
## Step 5 — fold the fan-out
All N workers returned? `dos verify-result` per worker; `dos coverage --declared
N`. Every worker returned a real result, or partition on the death.
## Step 6 — gate the empty case
`dos gate` — is there work? Branch on the exit code, not the printed line.
## Step 7 — close the issue
`dos commit-audit` then close: the issue is resolved when `Fixes #N` lands.
## Step 8 — run state
`dos status` — how is the run doing? the digest with no claimed field.
"""

# A self-certifying skill: many belief-bits, ZERO dos verbs, no anti-pattern
# framing — every claim is the agent's own word.
_SELF_CERTIFY = """# bad-skill
## Step 1
I committed it so it shipped. The phase is shipped.
## Step 2
I committed the fix; it shipped. The migration phases are shipped.
## Step 3
The commit is honest. I committed it. It shipped to master.
## Step 4
All 7 workers returned. Every worker returned a real result.
## Step 5
The goal is met. The goal is complete.
## Step 6
The issue is resolved. Close the issue. It shipped.
## Step 7
This code shipped. I committed it. The phase shipped.
## Step 8
The commit did what its subject says. I committed it so it shipped.
"""

_PROSE = """# taste-skill
## Step 1 — write with care
Choose words that read well. Prefer the simpler phrasing. This is a matter of
judgment and taste; there is no checkable effect here.
## Step 2 — review the tone
Read it aloud. Does it sound right? Adjust until it does.
"""


# --- pure-function contract --------------------------------------------------

def test_grounded_skill_scores_strong():
    g = sg.grade_skill(_GROUNDED, name="good-skill")
    assert g.signal == "STRONG", g.to_dict()
    assert g.grounded >= 6
    assert g.review_candidates == 0


def test_self_certify_skill_is_flagged_with_review_candidates():
    g = sg.grade_skill(_SELF_CERTIFY, name="bad-skill")
    assert g.signal == "WEAK"
    assert g.grounded == 0
    assert g.review_candidates >= 8
    # the smells are surfaced as candidates, not silently dropped
    cands = [s for s in g.sites if not s.excluded and s.witnessable and not s.grounded]
    assert cands and all(s.verb_seen == "" for s in cands)


def test_pure_prose_skill_is_na_not_zero():
    g = sg.grade_skill(_PROSE, name="taste-skill")
    # nothing scorable → N/A signal, grounded_fraction is None (never a 0)
    assert g.grounded_fraction is None
    assert g.signal == "NONE"


def test_unwitnessable_claims_are_excluded_from_denominator():
    text = """# effect-skill
## Step 1
I created file X and inserted row Y; a CI run concluded green.
## Step 2 — but ground what we can
`dos verify PLAN PHASE` — the phase is shipped per git ancestry.
"""
    g = sg.grade_skill(text, name="effect-skill")
    # EFFECT + CI_GREEN have no CLI verb → unwitnessable, out of the denominator
    assert g.unwitnessable >= 2
    # the one witnessable bit is grounded → fraction is 1.0, not dragged down
    assert g.grounded_fraction == 1.0


def test_anti_pattern_mentions_are_excluded():
    text = """# warn-skill
## Anti-patterns
- ❌ Deciding "the goal is met" by re-reading the transcript.
- ❌ Greping commit subjects to conclude it shipped.
## Step 1 — the grounded way
`dos verify PLAN PHASE`; `dos hook stop` wired to the goal.
"""
    g = sg.grade_skill(text, name="warn-skill")
    assert g.anti_pattern >= 2
    # the anti-pattern mentions don't count as ungrounded review candidates
    assert g.review_candidates == 0


def test_grounding_counts_any_dos_verb_in_the_section():
    # a SHIPPED bit grounded by a verb OTHER than `dos verify` still counts —
    # the robust signal is "shells a witness at all".
    text = """# alt-skill
## Step 1
Is this candidate already shipped? `dos reconcile --claimed-done`; only ground
truth removes work — the phase shipped per the kernel, not the claim.
"""
    g = sg.grade_skill(text, name="alt-skill")
    assert g.grounded >= 1
    assert g.review_candidates == 0


# --- the dogfood idempotence claim, made into a re-runnable assertion ---------

def test_real_grounded_skills_are_not_falsely_failed():
    """Every shipped skill the dogfood verified as grounded must either clear the
    `--check` floor or be skipped as too-sparse — NEVER failed. This is the
    dogfood's all-LEAVE finding (docs/345 §4) as a regression pin."""
    import glob
    import os

    repo = os.path.dirname(os.path.dirname(__file__))
    failed = []
    for path in glob.glob(os.path.join(repo, "src", "dos", "skills", "*", "SKILL.md")):
        name = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as fh:
            g = sg.grade_skill(fh.read(), name=name)
        verdict, why = sg.check_verdict(g)
        if verdict == "fail":
            failed.append((name, why))
    assert not failed, f"grounded SKP skills falsely failed --check: {failed}"


# --- CLI contract ------------------------------------------------------------

def test_cli_grade_one_exits_zero(capsys):
    import os
    repo = os.path.dirname(os.path.dirname(__file__))
    target = os.path.join(repo, "src", "dos", "skills", "dos-goal-gate", "SKILL.md")
    rc = cli_main(["skillify", "--grade", target])
    assert rc == 0
    out = capsys.readouterr().out
    assert "signal:" in out and "dos-goal-gate" in out


def test_cli_bare_without_grade_points_at_the_skill(capsys):
    import os
    repo = os.path.dirname(os.path.dirname(__file__))
    target = os.path.join(repo, "src", "dos", "skills", "dos-goal-gate", "SKILL.md")
    rc = cli_main(["skillify", target])
    assert rc == 2  # not a grade mode — defers to the screenplay
    err = capsys.readouterr().err
    assert "dos-skillify" in err


def test_cli_all_check_passes_on_the_real_tree():
    rc = cli_main(["skillify", "--grade", "--all", "--check"])
    assert rc == 0


def test_cli_check_impossible_floor_fails():
    rc = cli_main(["skillify", "--grade", "--all", "--check", "--min-coverage", "1.1"])
    assert rc == 1


def test_cli_json_shape(capsys):
    import os
    repo = os.path.dirname(os.path.dirname(__file__))
    target = os.path.join(repo, "src", "dos", "skills", "dos-dispatch", "SKILL.md")
    rc = cli_main(["skillify", "--grade", "--json", target])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert set(d) >= {"skill", "signal", "grounded", "review_candidates",
                      "unwitnessable", "anti_pattern", "sites"}
