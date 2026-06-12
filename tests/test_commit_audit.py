"""Tests for `dos.commit_audit` — the author-neutral claim-vs-diff verdict.

Two layers: (1) PURE `classify` unit tests (no git) pinning the verdict logic —
the empty/doc-only/test-delete mismatches, the conservative no-fire cases, and the
abstain-on-no-claim floor; (2) a few git-backed reader tests proving `read_commit`
/ `audit_commit` map a real commit's subject + diff onto the right verdict.

The load-bearing properties:
  * a code-effect claim with NOTHING effect-bearing touched (empty, doc-only,
    or media-only) → UNWITNESSED;
  * a real code change → OK / diff-witnessed;
  * a config/data/manifest diff under a code-effect claim → OK on the honestly
    typed `data-witnessed` rung (the 98-of-99 pilot class: the data change can
    BE the claimed effect — fire only on contradiction);
  * a test claim that net-deletes assertions → UNWITNESSED;
  * `wip`/`merge`/`bump` → ABSTAIN (no false fire on uncheckable commits);
  * a `docs:` commit touching docs → OK (no false fire), and an explicit doc
    HEAD out-ranks a testing noun later in the title (the 17546b5 over-fire);
  * a claim whose own words scope it to documentation (a doc filename or doc
    noun in the title) is witnessed by its doc diff; `test results` reports
    data, not suite work — but the floor stands: empty commits and inherited
    subjects over unrelated diffs still fire;
  * the verdict is author-NEUTRAL — nothing in it reads who wrote the message.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos import commit_audit as ca
from dos.commit_audit import (
    CommitClaim, DiffFacts, ClaimKind, Verdict, Witness, classify,
    classify_claim,
)


# --- layer 1: pure classify (no git) ---------------------------------------


def _claim(subject: str) -> CommitClaim:
    return CommitClaim(sha="abc1234", subject=subject)


def test_empty_commit_claiming_code_is_unwitnessed():
    v = classify(_claim("implement the new caching layer"),
                 DiffFacts(files=(), is_empty=True))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.claim_kind is ClaimKind.CODE_EFFECT
    assert v.witness is Witness.SUBJECT_ONLY


def test_code_claim_touching_only_docs_is_unwitnessed():
    # the canonical HUMAN mismatch: "fix: ..." that only edits a README
    v = classify(_claim("fix: resolve the auth race condition"),
                 DiffFacts(files=("README.md",), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.witness is Witness.SUBJECT_ONLY
    assert "no SOURCE file" in v.reason


def test_real_code_change_is_witnessed():
    v = classify(_claim("fix: correct off-by-one in the parser"),
                 DiffFacts(files=("src/parser.py",), is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DIFF_WITNESSED
    assert v.source_files == ("src/parser.py",)


def test_powershell_and_batch_scripts_are_source():
    """The 621c1a3 over-fire: a `fix(publish):` claim whose diff touches only the
    PowerShell script it names read as subject-only, because `.ps1` was missing
    from the source set. Scripts are code on a Windows-tooled repo — same
    inclusion rule that already admits `.sh`/`.bash`/`.zsh`."""
    for path in ("scripts/seed_public_repo.ps1", "lib/util.psm1",
                 "module.psd1", "build.bat", "run.cmd"):
        v = classify(_claim("fix(publish): seed commit message names the shipped ABI"),
                     DiffFacts(files=(path,), is_empty=False))
        assert v.verdict is Verdict.OK, path
        assert v.witness is Witness.DIFF_WITNESSED, path
        assert v.source_files == (path,), path


def test_bare_dotfile_config_is_source():
    """The 04c740c over-fire: a `fix(ci): pin the corpora LF` claim whose diff
    touches only .gitattributes read as subject-only — the bare dotfile's whole
    name parses as a suffix, so it missed the extensionless-config branch that
    already admits Makefile/Dockerfile. Repo-plumbing dotfiles are behavior-
    bearing, not docs; they can witness the fix they carry."""
    for path in (".gitattributes", ".gitignore", ".flake8", "go/.gitattributes"):
        v = classify(_claim("fix(ci): pin the Go parity corpora LF"),
                     DiffFacts(files=(path,), is_empty=False))
        assert v.verdict is Verdict.OK, path
        assert v.witness is Witness.DIFF_WITNESSED, path
        assert v.source_files == (path,), path
    # A dotted dotfile (.eslintrc.json) keeps its real suffix's classification —
    # the branch admits only the bare, suffixless form. It is NOT source (the
    # boundary this test pins), so it witnesses on the lower data rung instead.
    v = classify(_claim("fix: resolve the auth race condition"),
                 DiffFacts(files=(".eslintrc.json",), is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED
    assert v.source_files == ()


def test_test_claim_net_deleting_assertions_is_unwitnessed():
    v = classify(_claim("tests pass now, all green"),
                 DiffFacts(files=("tests/test_x.py",), is_empty=False,
                           test_lines_added=1, test_lines_removed=5))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert "net-DELETES" in v.reason


# --- issue #82: the net-delete catch is scoped to the PASS/GREEN claim only ---
def test_honest_test_update_shrink_is_ok_not_a_delete_the_assertion_fire():
    """An honest `test: update tests for X` that net-DELETES lines (a stale case
    removed IS the update) grades OK — the net-delete catch keys on the pass/green
    claim, not on TEST-kind alone (issue #82, the #66 pilot artifact). This is the
    exact pilot case: subject `test: Update tests …`, test files only, +2/-17."""
    v = classify(_claim("test: update tests for git integration"),
                 DiffFacts(files=("tests/test_git.py", "tests/test_x.py"),
                           is_empty=False,
                           test_lines_added=2, test_lines_removed=17))
    assert v.claim_kind is ClaimKind.TEST
    assert v.verdict is Verdict.OK, (
        "an `update tests` claim is CONSISTENT with a shrink — it must not read as "
        "the delete-the-assertion shape (the pre-#82 over-fire)")
    assert "net-DELETES" not in v.reason


def test_pass_phrase_claim_with_net_delete_still_fires_after_scoping():
    """The scoping must NOT loosen the designed catch: a subject that claims the
    suite PASSES/green while net-deleting test lines keeps firing CLAIM_UNWITNESSED
    (the done-condition's invariant — true under BOTH resolutions of #82)."""
    for subject in ("tests pass now, all green",
                    "all tests passing after the fix",
                    "fix the failing test"):
        v = classify(_claim(subject),
                     DiffFacts(files=("tests/test_x.py",), is_empty=False,
                               test_lines_added=1, test_lines_removed=9))
        assert v.verdict is Verdict.CLAIM_UNWITNESSED, (
            f"{subject!r} claims the suite passes — a net test-line delete must "
            f"still fire the delete-the-assertion catch")
        assert "net-DELETES" in v.reason


def test_test_claim_touching_no_test_file_is_unwitnessed():
    v = classify(_claim("add tests for the widget"),
                 DiffFacts(files=("src/widget.py",), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.claim_kind is ClaimKind.TEST


def test_test_claim_adding_assertions_is_witnessed():
    v = classify(_claim("add tests for the widget"),
                 DiffFacts(files=("tests/test_widget.py",), is_empty=False,
                           test_lines_added=20, test_lines_removed=0))
    assert v.verdict is Verdict.OK
    assert v.test_files == ("tests/test_widget.py",)


def test_doc_claim_touching_docs_is_ok_no_false_fire():
    v = classify(_claim("docs: clarify the setup steps"),
                 DiffFacts(files=("README.md",), is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.claim_kind is ClaimKind.DOC


def test_wip_and_bump_abstain():
    for subj in ("wip", "chore: bump deps to 2.0", "Merge branch 'main'",
                 "revert: undo the last change", "misc cleanup"):
        v = classify(_claim(subj), DiffFacts(files=("src/a.py",), is_empty=False))
        assert v.verdict is Verdict.ABSTAIN, f"{subj!r} should abstain"
        assert v.claim_kind is ClaimKind.NONE


def test_classify_claim_kinds():
    assert classify_claim("fix: the bug") is ClaimKind.CODE_EFFECT
    assert classify_claim("add tests for x") is ClaimKind.TEST
    assert classify_claim("docs: update readme") is ClaimKind.DOC
    assert classify_claim("wip") is ClaimKind.NONE
    assert classify_claim("bump version to 1.2") is ClaimKind.NONE


def test_scope_prefix_grammar_engages():
    """A `path/scope: <verb> …` subject (DOS's own grammar) reads the verb AFTER the
    colon — the old parser only read the pre-colon head and false-ABSTAINed."""
    assert classify_claim("benchmark/foo: implement the verifier") is ClaimKind.CODE_EFFECT
    assert classify_claim("mcp: add the tool") is ClaimKind.CODE_EFFECT
    assert classify_claim("auth: fix the race") is ClaimKind.CODE_EFFECT
    assert classify_claim("docs/206: clarify the payoff") is ClaimKind.DOC
    assert classify_claim("core: add unit tests for the engine") is ClaimKind.TEST


def test_scope_widening_stays_conservative():
    """The widening must not create false fires: a buried verb or a non-verb lead
    after the scope must still ABSTAIN (the phase_shipped six-widenings lesson)."""
    assert classify_claim("kernel: report the fix status") is ClaimKind.NONE  # 'fix' buried
    assert classify_claim("kernel: productivity verdict") is ClaimKind.NONE   # no verb
    assert classify_claim("status: everything broke") is ClaimKind.NONE       # no leading verb


def test_doc_scope_prefix_is_a_doc_claim_not_code():
    """A `<doc-file>: <action verb> …` subject is a documentation edit, even though
    the verb is `add`/`update` — scoping to a doc file is itself the 'this is docs'
    signal (the e2d5aa9/b2f58fb real-history finding). A SOURCE-scope stays code."""
    assert classify_claim("CLAUDE.md: add a glossary") is ClaimKind.DOC
    assert classify_claim("README: document the install") is ClaimKind.DOC
    assert classify_claim("docs/206: add the payoff section") is ClaimKind.DOC
    # a non-doc scope with an action verb is still a code claim
    assert classify_claim("src/foo.py: add the parser") is ClaimKind.CODE_EFFECT
    assert classify_claim(".claude/settings.json: wire the sensor") is ClaimKind.CODE_EFFECT


def test_ci_scoped_claim_on_workflows_only_diff_is_witnessed():
    """The 5b2b940/e5debd1 over-fire: a `fix(ci):` / `ci:` claim whose diff touches
    ONLY a canonical CI-config path is the claim's NATURAL location — corroboration,
    not contradiction. Verdict is OK / diff-witnessed (the workflows file IS a
    witnessable change location for a ci-scoped claim), NOT abstain: the diff
    carries real evidence, and the sweep's checkable denominator should count it."""
    for subj in ("fix(ci): set up uv in the test job so the levels run",
                 "ci: fix the cache key on the windows leg",
                 "ci(install): enable the uv matrix leg"):
        v = classify(_claim(subj),
                     DiffFacts(files=(".github/workflows/ci.yml",), is_empty=False))
        assert v.verdict is Verdict.OK, f"{subj!r} should be witnessed"
        assert v.witness is Witness.DIFF_WITNESSED
        assert v.ci_files == (".github/workflows/ci.yml",)


def test_ci_witness_needs_the_conjunction():
    """Either half alone must not reach the STRONG rung — the ci widening is
    conjunctive. A ci scope over a doc-only diff still fires; a CI-config diff
    under a non-ci claim gets only the lower data rung (any yml is data), never
    the diff-witnessed rung the conjunction grants."""
    # ci-shaped claim, but the diff never touches CI config → still unwitnessed
    v = classify(_claim("fix(ci): repair the flaky matrix"),
                 DiffFacts(files=("README.md",), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    # workflows-only diff, but the claim is NOT ci-scoped → data rung only
    v = classify(_claim("fix: resolve the auth race condition"),
                 DiffFacts(files=(".github/workflows/ci.yml",), is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED          # not DIFF_WITNESSED
    assert v.ci_files == (".github/workflows/ci.yml",)  # reported, not promoted


def test_ci_scope_and_path_match_structurally_never_loosely():
    """The docs/243 lesson: no word-search, no substring. A prose 'ci' does not
    engage the scope; a scope merely containing the letters does not; a stray
    .yml outside the closed CI-config set is not a CI witness. None of these
    reaches the conjunction's DIFF_WITNESSED rung — a yml diff is still a data
    file, so each lands on the lower data rung, with `ci_files` honest."""
    # prose mention of CI (no conventional-commit head) does not engage the scope
    v = classify(_claim("fix the ci cache invalidation"),
                 DiffFacts(files=(".github/workflows/ci.yml",), is_empty=False))
    assert v.witness is Witness.DATA_WITNESSED
    # a scope CONTAINING 'ci' is not the ci scope
    v = classify(_claim("fix(circus): tame the lion"),
                 DiffFacts(files=(".github/workflows/ci.yml",), is_empty=False))
    assert v.witness is Witness.DATA_WITNESSED
    # a stray yml outside the closed CI-config set is not a CI witness
    v = classify(_claim("fix(ci): adjust the thresholds"),
                 DiffFacts(files=("config.yml",), is_empty=False))
    assert v.witness is Witness.DATA_WITNESSED
    assert v.ci_files == ()


def test_ci_config_set_covers_yaml_suffix_and_known_files():
    """`.yaml` under the workflows dir and the exact well-known non-GitHub files
    count; the match is the dir-prefix + suffix or an exact filename, never 'any yml'."""
    for path in (".github/workflows/release.yaml", ".gitlab-ci.yml",
                 ".circleci/config.yml"):
        v = classify(_claim("fix(ci): correct the trigger"),
                     DiffFacts(files=(path,), is_empty=False))
        assert v.verdict is Verdict.OK, f"{path} should witness a ci-scoped claim"
        assert v.ci_files == (path,)


def test_data_witness_dependency_manifests_and_lockfiles():
    """The pilot's dominant artifact class (96 of 99 raw flags): a concrete
    `fix:`/`feat:`-shaped claim whose entire diff is dependency manifests /
    lockfiles — the bump IS the claimed effect. Witnessed on the data rung."""
    cases = [
        ("Fix CVE-2026-12345: Update react-router to 7.15.0",
         ("package.json", "package-lock.json")),
        ("Upgrade LiteLLM to 1.84.1",
         ("poetry.lock", "pyproject.toml", "uv.lock")),
        ("drop pin of torch",
         ("requirements.txt", "requirements-dev.in", "docker/requirements-gpu.txt")),
    ]
    for subject, files in cases:
        v = classify(_claim(subject), DiffFacts(files=files, is_empty=False))
        assert v.verdict is Verdict.OK, subject
        assert v.witness is Witness.DATA_WITNESSED, subject
        assert v.data_files, subject
        assert v.source_files == (), subject


def test_data_witness_config_resources_that_are_the_feature():
    """Pilot sub-class 2: the data/config resource IS the feature — a
    model-settings YAML, a set of templates."""
    v = classify(_claim("feat: Add gpt-5.3 and gpt-5.4 model variants"),
                 DiffFacts(files=("config/model-settings.yml",), is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED
    v = classify(_claim("fix(resolver): use literal placeholders in branch naming"),
                 DiffFacts(files=("templates/branch.j2", "templates/pr.j2"),
                           is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED


def test_data_witness_site_config_with_doc_in_the_mix():
    """Pilot sub-class 3, both halves. The issue's own example (`table of
    contents`, `documentation`) is doc-SHAPED prose, so it classifies DOC and
    the touched files witness it; a site-feature claim with NO doc noun rides
    the data rung off the config file in the mix."""
    v = classify(_claim("feat: Add hierarchical table of contents to documentation homepage"),
                 DiffFacts(files=("_config.yml", "docs/index.md"), is_empty=False))
    assert v.claim_kind is ClaimKind.DOC
    assert v.verdict is Verdict.OK
    # no doc noun anywhere → CODE_EFFECT; the config file is the witness
    v = classify(_claim("feat: Add hierarchical navigation to the site homepage"),
                 DiffFacts(files=("_config.yml", "docs/index.md"), is_empty=False))
    assert v.claim_kind is ClaimKind.CODE_EFFECT
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED
    assert v.data_files == ("_config.yml",)


def test_doc_shaped_prose_claim_witnessed_by_doc_only_diff():
    """The pilot's doc-only residue: the subject's own words scope the effect
    to documentation — a doc filename in the title, a doc noun in the window,
    or a doc phrase — so a doc-only diff is the natural witness, not a
    contradiction. (De-identified shapes from the #81 re-sweep residue.)"""
    cases = [
        # a doc FILENAME named mid-title
        ("Remove duplicate lowercase skill.md files",
         ("skills/alpha/skill.md", "skills/beta/skill.md")),
        # a doc noun in the message window
        ("Add PR review bot triage guidelines",
         ("agents/skills/codereview-guide.md",)),
        # the doc phrase
        ("fix: Simplify table of contents Liquid syntax to avoid errors",
         ("docs/index.md",)),
        ("feat: Add page descriptions to table of contents with inline styling",
         ("docs/index.md",)),
    ]
    for subject, files in cases:
        v = classify(_claim(subject), DiffFacts(files=files, is_empty=False))
        assert v.claim_kind is ClaimKind.DOC, subject
        assert v.verdict is Verdict.OK, subject
    # the doc-shaped claim still fires on an EMPTY commit — the floor stands
    v = classify(_claim("Add PR review bot triage guidelines"),
                 DiffFacts(files=(), is_empty=True))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    # a dependency manifest wearing .txt is data, not a doc-shaping token
    assert classify_claim("drop pin of torch in requirements.txt") \
        is ClaimKind.CODE_EFFECT
    # "comment(s)" stays OUT of the window set — code comments live in source
    assert classify_claim("fix: strip comments in the lexer") \
        is ClaimKind.CODE_EFFECT


def test_test_results_phrase_reports_data_not_suite_work():
    """`add test results …` REPORTS run data; it does not claim suite work. It
    falls through to CODE_EFFECT, where the leaderboard YAML witnesses on the
    data rung — while a real test claim keeps the test-file requirement."""
    v = classify(_claim("add test results for gpt-oss-120b (high) to polyglot leaderboard"),
                 DiffFacts(files=("website/_data/polyglot_leaderboard.yml",),
                           is_empty=False))
    assert v.claim_kind is ClaimKind.CODE_EFFECT
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DATA_WITNESSED
    # the guard never relaxes a REAL test claim: no reporting noun → TEST
    assert classify_claim("add tests for the resolver") is ClaimKind.TEST
    v = classify(_claim("add tests for the resolver"),
                 DiffFacts(files=("website/_data/polyglot_leaderboard.yml",),
                           is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED


def test_inherited_subject_over_attribution_diff_still_fires():
    """The conservative floor the widenings must not erode: a subject REUSED
    from the commit that did the work, pasted onto a follow-up whose own diff
    is an attribution line in a README, is a claim THIS diff cannot witness —
    the inherited-claim shape (docs/229) is a catch, not an artifact. No doc
    noun, no data file, no source: it stays CLAIM_UNWITNESSED. (Synthetic
    fixture — the shape of a real pilot catch, subject de-identified.)"""
    v = classify(_claim("added parsers for COBOL"),
                 DiffFacts(files=("queries/grammars/LICENSES.md",),
                           is_empty=False))
    assert v.claim_kind is ClaimKind.CODE_EFFECT
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.witness is Witness.SUBJECT_ONLY


def test_data_rung_does_not_swallow_the_real_contradictions():
    """The widening is fire-reducing ONLY where data exists. The forgery floor
    stands: empty commits, doc-only diffs, and media-only diffs under a code
    claim still fire — there is nothing in them that could carry the effect."""
    v = classify(_claim("implement the cache"), DiffFacts(files=(), is_empty=True))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    v = classify(_claim("fix: resolve the auth race"),
                 DiffFacts(files=("README.md", "docs/notes.rst"), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    v = classify(_claim("fix: resolve the auth race"),
                 DiffFacts(files=("assets/logo.png",), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.data_files == ()


def test_skill_md_is_the_source_file_of_an_agent_system():
    """Issue #94 (66 of 88 corpus-sweep flags): SKILL.md prose is executed as
    instructions by an agent runtime — editing it changes behavior the way a
    .py edit does, so it witnesses on the FULL diff-witnessed rung. Exact
    basename only: a skills/README.md keeps its doc classification."""
    v = classify(_claim("fix(review): findings MUST go in comments array"),
                 DiffFacts(files=("packages/core/src/skills/bundled/review/SKILL.md",),
                           is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DIFF_WITNESSED
    assert v.source_files == ("packages/core/src/skills/bundled/review/SKILL.md",)
    # the sibling prose files deliberately stay OUT (under-matching) — but a
    # commit touching them ALONGSIDE the SKILL.md is witnessed by the SKILL.md
    v = classify(_claim("feat(skills): add triage skill for issue gatekeeping"),
                 DiffFacts(files=("skills/triage/SKILL.md",
                                  "skills/triage/references/workflow.md"),
                           is_empty=False))
    assert v.verdict is Verdict.OK
    assert v.witness is Witness.DIFF_WITNESSED
    # exact-name boundary: a skills README is still a doc, not source
    v = classify(_claim("fix: resolve the auth race condition"),
                 DiffFacts(files=("skills/README.md",), is_empty=False))
    assert v.verdict is Verdict.CLAIM_UNWITNESSED


def test_doc_scope_token_is_a_doc_claim():
    """`fix(docs): …` / `feat(readme): …` — the conventional-commit SCOPE names
    documentation, so a doc-only diff is the claim's natural location (the
    `_scope_is_ci` analogue; structural token equality, never a word-search)."""
    v = classify(_claim("fix(docs): fix mode count in approval-mode docs"),
                 DiffFacts(files=("docs/users/approval-mode.md",), is_empty=False))
    assert v.claim_kind is ClaimKind.DOC
    assert v.verdict is Verdict.OK
    # a scope merely CONTAINING the letters is not the doc scope
    assert classify_claim("fix(docstore): evict stale entries") is ClaimKind.CODE_EFFECT


def test_doc_head_outranks_a_testing_noun_in_the_title():
    """The 17546b5 over-fire: `docs(plans): the dos.testing conformance-suite +
    JudgeTester design` is a documentation commit ABOUT a testing module, not a
    test claim — the explicit docs TYPE wins before the test-noun window."""
    v = classify(_claim("docs(plans): the dos.testing conformance-suite + JudgeTester design"),
                 DiffFacts(files=("docs/306_conformance-plan.md",), is_empty=False))
    assert v.claim_kind is ClaimKind.DOC
    assert v.verdict is Verdict.OK
    # a doc-named scope wins the same way
    assert classify_claim("README: how the testing suite runs") is ClaimKind.DOC
    # but a NON-doc head with a leading test noun is still a test claim
    assert classify_claim("core: add unit tests for the engine") is ClaimKind.TEST
    assert classify_claim("tests pass on the new matrix") is ClaimKind.TEST


def test_sweep_summary_rate_and_breakdown():
    """The sweep folds verdicts into the drift rate (unwitnessed/checkable, abstains
    excluded) + a by-kind grid + the offender shas."""
    from dos.commit_audit import sweep_summary, ClaimVerdict
    vs = [
        ClaimVerdict("a", Verdict.OK, ClaimKind.CODE_EFFECT, Witness.DIFF_WITNESSED, ""),
        ClaimVerdict("b", Verdict.CLAIM_UNWITNESSED, ClaimKind.CODE_EFFECT, Witness.SUBJECT_ONLY, ""),
        ClaimVerdict("c", Verdict.ABSTAIN, ClaimKind.NONE, Witness.ABSTAIN, ""),
        ClaimVerdict("d", Verdict.OK, ClaimKind.DOC, Witness.DIFF_WITNESSED, ""),
    ]
    s = sweep_summary(vs)
    assert s["commits"] == 4
    assert s["checkable"] == 3        # a, b, d (c abstained, excluded)
    assert s["unwitnessed"] == 1
    assert abs(s["drift_rate"] - (1 / 3)) < 1e-9
    assert s["unwitnessed_shas"] == ["b"]
    assert s["by_kind"]["code_effect"] == {"unwitnessed": 1, "witnessed": 1, "abstain": 0}


def test_noclaim_markers_match_whole_words_not_substrings():
    """A no-claim marker must match a WHOLE word, never a substring — `unit` must
    not hit `nit`, `conversion` must not hit `version`, `lifestyle` not `style`."""
    assert classify_claim("add unit tests for x") is ClaimKind.TEST          # not 'nit'
    assert classify_claim("implement conversion tracking") is ClaimKind.CODE_EFFECT  # not 'version'
    assert classify_claim("add lifestyle widget") is ClaimKind.CODE_EFFECT   # not 'style'
    # but a real whole-word no-claim marker still abstains
    assert classify_claim("address review nits") is ClaimKind.NONE           # 'nits' whole word


def test_docs_ok_policy_silences_code_claim_on_doc_diff():
    pol = ca.ClaimPolicy(docs_satisfy_code_claim=True)
    v = classify(_claim("fix: tweak a comment"),
                 DiffFacts(files=("README.md",), is_empty=False), pol)
    assert v.verdict is Verdict.OK


def test_verdict_is_author_neutral():
    """Nothing in the verdict reads WHO authored the message — the same subject +
    diff yields the same verdict whether a human or an agent wrote it."""
    same_inputs = (_claim("implement feature X"), DiffFacts(files=(), is_empty=True))
    assert classify(*same_inputs).verdict is Verdict.CLAIM_UNWITNESSED
    # there is no author field on CommitClaim at all — the structural guarantee.
    assert not hasattr(CommitClaim("s", "subj"), "author")


# --- layer 2: git-backed reader --------------------------------------------


def _git_ok() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


gitmark = pytest.mark.skipif(not _git_ok(), reason="git not available")


def _init_repo(d: Path) -> None:
    def g(*a):
        subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (d / "src").mkdir()
    (d / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "src/app.py")
    g("commit", "-qm", "initial")


@gitmark
def test_reader_empty_commit_claiming_code(tmp_path: Path):
    _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty",
                    "-m", "implement the cache"], capture_output=True)
    v = ca.audit_commit("HEAD", root=tmp_path)
    assert v is not None
    assert v.verdict is Verdict.CLAIM_UNWITNESSED
    assert v.witness is Witness.SUBJECT_ONLY


@gitmark
def test_reader_real_code_change(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/app.py"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm",
                    "fix: correct the value"], capture_output=True)
    v = ca.audit_commit("HEAD", root=tmp_path)
    assert v is not None and v.verdict is Verdict.OK
    assert "src/app.py" in v.source_files


@gitmark
def test_reader_doc_only_fix_is_unwitnessed(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm",
                    "fix: resolve the race condition"], capture_output=True)
    v = ca.audit_commit("HEAD", root=tmp_path)
    assert v is not None and v.verdict is Verdict.CLAIM_UNWITNESSED


@gitmark
def test_reader_ci_scoped_workflows_only_commit_is_witnessed(tmp_path: Path):
    """The real 5b2b940/e5debd1 shape end-to-end: `fix(ci): …` touching only
    .github/workflows/ci.yml reads back as OK / diff-witnessed."""
    _init_repo(tmp_path)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".github/workflows/ci.yml"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm",
                    "fix(ci): set up uv in the test job"], capture_output=True)
    v = ca.audit_commit("HEAD", root=tmp_path)
    assert v is not None and v.verdict is Verdict.OK
    assert v.witness is Witness.DIFF_WITNESSED
    assert v.ci_files == (".github/workflows/ci.yml",)


@gitmark
def test_reader_bad_ref_returns_none(tmp_path: Path):
    _init_repo(tmp_path)
    assert ca.audit_commit("nonexistent-ref", root=tmp_path) is None


@gitmark
def test_audit_range_mixed(tmp_path: Path):
    _init_repo(tmp_path)
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                  capture_output=True, text=True)
    # an unwitnessed empty claim + a witnessed real change + an abstain
    g("commit", "-q", "--allow-empty", "-m", "implement the thing")
    (tmp_path / "src" / "app.py").write_text("x = 9\n", encoding="utf-8")
    g("add", "src/app.py"); g("commit", "-qm", "fix: correct the value")
    g("commit", "-q", "--allow-empty", "-m", "wip")
    verdicts = ca.audit_range("HEAD~3..HEAD", root=tmp_path)
    assert len(verdicts) == 3
    kinds = {v.verdict for v in verdicts}
    assert Verdict.CLAIM_UNWITNESSED in kinds
    assert Verdict.OK in kinds
    assert Verdict.ABSTAIN in kinds
