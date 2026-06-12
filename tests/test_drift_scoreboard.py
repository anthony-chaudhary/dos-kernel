"""Tests for the drift-rate scoreboard sweep (`scripts/drift_scoreboard.py`,
docs/307, issue #66).

This is DOS dev tooling, not a kernel module — it `import dos` and lives under
`scripts/`, the same one-way arrow as `trajectory_audit.py`. The suite pins
the parts that carry judgement:

  * the attribution matcher's POSITIVE space (bot-account emails, toolchain
    emails, the `(aider)` name form, `Co-authored-by:` trailers, generator
    footers) and its NEGATIVE space — a human named Claude/Claudette never
    matches (under-matching is the conservative direction);
  * the aggregate fold's arithmetic (pooled rate, median, by-kind pooling);
  * the ANONYMIZATION INVARIANT — the publishable artifacts (the fold output
    and the rendered markdown) carry no repo name, no URL, and no commit SHA,
    even when the per-repo inputs are full of them (v1 is aggregate-only;
    the issue-#66 ethics floor, structural, not policy);
  * the end-to-end shape on a synthetic local corpus: an agent-attributed
    over-claim (`--allow-empty` "fix:") counts unwitnessed, an honest
    agent-attributed fix counts witnessed, a human commit is not audited.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

# Import the script-under-test by path (it is not an installed package).
_HELPER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "drift_scoreboard.py"
_spec = importlib.util.spec_from_file_location("drift_scoreboard", _HELPER_PATH)
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)


# ---------------------------------------------------------------------------
# The attribution matcher — positive space.
# ---------------------------------------------------------------------------


def test_devin_bot_email_matches():
    assert ds.match_identity(
        "devin-ai-integration[bot]",
        "158243242+devin-ai-integration[bot]@users.noreply.github.com",
    ) == "devin"


def test_bot_email_without_numeric_prefix_matches():
    assert ds.match_identity(
        "anything", "claude[bot]@users.noreply.github.com") == "claude"


def test_bot_name_form_alone_matches():
    assert ds.match_identity("copilot-swe-agent[bot]", "") == "copilot"


def test_copilot_agent_email_suffix_matches():
    assert ds.match_identity(
        "Copilot", "198982749+Copilot@users.noreply.github.com") == "copilot"


def test_claude_code_coauthor_identity_matches():
    assert ds.match_identity("Claude", "noreply@anthropic.com") == "claude"


def test_aider_name_suffix_matches():
    assert ds.match_identity(
        "Paul Example (aider)", "paul@example.com") == "aider"


def test_aider_domain_matches():
    assert ds.match_identity(
        "aider (gpt-4o)", "noreply@aider.chat") == "aider"


def test_openhands_toolchain_email_matches():
    assert ds.match_identity("openhands", "openhands@all-hands.dev") == "openhands"


def test_qwen_coder_toolchain_email_matches():
    assert ds.match_identity("Qwen-Coder", "qwen-coder@alibabacloud.com") == "qwen"


def test_roomote_bot_matches():
    assert ds.match_identity(
        "roomote[bot]",
        "219738659+roomote[bot]@users.noreply.github.com") == "roo"


def test_codex_conjunctive_identity_matches():
    assert ds.match_identity("Codex", "noreply@openai.com") == "codex"


def test_codex_email_alone_does_not_match():
    # the conjunctive rule: the generic-looking address without the name is OUT
    assert ds.match_identity("Jane Human", "noreply@openai.com") is None


def test_coauthored_by_trailer_matches():
    body = ("Make the thing.\n\n"
            "Co-Authored-By: Claude <noreply@anthropic.com>\n")
    assert ds.classify_attribution(
        "Jane Human", "jane@example.com", "Jane Human", "jane@example.com",
        body) == "claude"


def test_generator_footer_matches():
    body = "Do it.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    assert ds.classify_attribution(
        "Jane Human", "jane@example.com", "Jane Human", "jane@example.com",
        body) == "claude"


# ---------------------------------------------------------------------------
# The attribution matcher — negative space (under-matching is load-bearing).
# ---------------------------------------------------------------------------


def test_human_named_claude_does_not_match():
    assert ds.match_identity("Claude", "claude@example.com") is None


def test_human_named_claudette_does_not_match():
    assert ds.match_identity("Claudette Smith", "claudette@gmail.com") is None


def test_aider_substring_in_a_real_name_does_not_match():
    assert ds.match_identity("Aiderman", "a@example.com") is None


def test_plain_human_commit_does_not_match():
    assert ds.classify_attribution(
        "Jane Human", "jane@example.com", "Jane Human", "jane@example.com",
        "fix the parser\n") is None


def test_unrelated_bot_does_not_match():
    # dependabot is a bot but not an agent toolchain — stays OUT (closed set).
    assert ds.match_identity(
        "dependabot[bot]",
        "49699333+dependabot[bot]@users.noreply.github.com") is None


# ---------------------------------------------------------------------------
# Corpus parsing.
# ---------------------------------------------------------------------------


def test_parse_corpus_skips_comments_and_blanks():
    text = ("# the corpus\n"
            "https://github.com/example/one.git\n"
            "\n"
            "C:/repos/two   # a local path\n")
    assert ds.parse_corpus(text) == [
        "https://github.com/example/one.git", "C:/repos/two"]


# ---------------------------------------------------------------------------
# The aggregate fold + render — arithmetic and the anonymization invariant.
# ---------------------------------------------------------------------------


def _fake_repo(name: str, *, checkable: int, unwitnessed: int,
               abstained: int = 3, markers: dict | None = None) -> dict:
    witnessed = checkable - unwitnessed
    return {
        "repo": f"https://github.com/acme/{name}.git",
        "commits_scanned": 100,
        "attributed_commits": checkable + abstained,
        "markers": markers or {"devin": checkable + abstained},
        "summary": {
            "commits": checkable + abstained,
            "checkable": checkable,
            "abstained": abstained,
            "witnessed": witnessed,
            "unwitnessed": unwitnessed,
            "drift_rate": unwitnessed / checkable if checkable else 0.0,
            "by_kind": {"code_effect": {"unwitnessed": unwitnessed,
                                        "witnessed": witnessed,
                                        "abstain": 0}},
            "unwitnessed_shas": ["deadbeefcafe1234"] * unwitnessed,
        },
    }


def test_fold_aggregate_arithmetic():
    agg = ds.fold_aggregate([
        _fake_repo("one", checkable=10, unwitnessed=1),
        _fake_repo("two", checkable=10, unwitnessed=3),
    ])
    assert agg["repos"] == 2
    assert agg["checkable"] == 20
    assert agg["unwitnessed"] == 4
    assert agg["drift_rate_pooled"] == 4 / 20
    assert agg["drift_rate_median_per_repo"] == (0.1 + 0.3) / 2
    assert agg["per_repo_drift_rates"] == [0.1, 0.3]
    assert agg["by_kind"]["code_effect"]["unwitnessed"] == 4
    assert agg["by_marker"]["devin"] == 26


def test_fold_aggregate_handles_zero_checkable():
    agg = ds.fold_aggregate([_fake_repo("one", checkable=0, unwitnessed=0)])
    assert agg["drift_rate_pooled"] == 0.0
    assert agg["repos_with_checkable_claims"] == 0


def test_aggregate_is_identity_stripped():
    """THE ethics-floor pin: nothing that names a repo survives the fold —
    not the name, not the URL, not a commit SHA (SHAs are searchable)."""
    repos = [
        _fake_repo("supersecret-project", checkable=10, unwitnessed=2),
        _fake_repo("other-repo", checkable=5, unwitnessed=1),
    ]
    agg = ds.fold_aggregate(repos)
    blob = json.dumps(agg).lower()
    for leak in ("supersecret", "other-repo", "github.com", "acme",
                 "deadbeef", "sha", "repo:"):
        assert leak not in blob, f"aggregate leaked identity via {leak!r}"
    md = ds.render_markdown(agg, "2026-06-12").lower()
    for leak in ("supersecret", "other-repo", "acme", "deadbeef"):
        assert leak not in md, f"report leaked identity via {leak!r}"


def test_render_markdown_carries_the_rate_and_the_advisory_line():
    agg = ds.fold_aggregate([_fake_repo("one", checkable=10, unwitnessed=2)])
    md = ds.render_markdown(agg, "2026-06-12")
    assert "Pooled drift rate: 20.0%" in md
    assert "never a correctness or malice grade" in md
    assert "aggregate-only" in md


# ---------------------------------------------------------------------------
# End-to-end on a synthetic local corpus (the P1 done-condition).
# ---------------------------------------------------------------------------

_DEVIN = ["-c", "user.name=devin-ai-integration[bot]",
          "-c", "user.email=158243242+devin-ai-integration[bot]@users.noreply.github.com"]
_HUMAN = ["-c", "user.name=Jane Human", "-c", "user.email=jane@example.com"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _make_synthetic_repo(root: Path) -> Path:
    repo = root / "synthetic-corpus-repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, *_HUMAN, "commit", "--quiet",
         "-m", "fix: human baseline change")
    # honest agent commit: a code claim witnessed by a source diff
    (repo / "mod.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, *_DEVIN, "commit", "--quiet", "-m", "fix: handle the empty case")
    # over-claim: a code claim over an EMPTY commit (the forgery shape)
    _git(repo, *_DEVIN, "commit", "--quiet", "--allow-empty",
         "-m", "fix: resolve the cache race")
    return repo


def test_end_to_end_sweep_and_artifacts(tmp_path):
    repo = _make_synthetic_repo(tmp_path)
    corpus = tmp_path / "corpus.txt"
    corpus.write_text(f"# fixture\n{repo.as_posix()}\n", encoding="utf-8")
    out = tmp_path / "out"

    rc = ds.main(["--corpus", str(corpus), "--out", str(out),
                  "--stamp", "2026-06-12"])
    assert rc == 0

    agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
    assert agg["repos"] == 1
    assert agg["audited_commits"] == 2          # the human commit is not audited
    assert agg["checkable"] == 2
    assert agg["witnessed"] == 1
    assert agg["unwitnessed"] == 1
    assert agg["drift_rate_pooled"] == 0.5
    assert agg["by_marker"] == {"devin": 2}

    # the per-repo artifact (operator-only) DOES carry identity ...
    per_repo = json.loads(
        (out / "per-repo" / "synthetic-corpus-repo.json").read_text(
            encoding="utf-8"))
    assert per_repo["repo"].endswith("synthetic-corpus-repo")
    assert len(per_repo["summary"]["unwitnessed_shas"]) == 1

    # ... and the publishable artifacts carry NONE of it.
    bad_sha = per_repo["summary"]["unwitnessed_shas"][0]
    agg_blob = (out / "aggregate.json").read_text(encoding="utf-8").lower()
    md_blob = (out / "aggregate.md").read_text(encoding="utf-8").lower()
    for blob in (agg_blob, md_blob):
        assert "synthetic-corpus-repo" not in blob
        assert bad_sha.lower() not in blob
