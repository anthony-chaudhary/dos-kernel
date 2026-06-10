"""commit-audit — does a commit's *claim* match what its *diff* actually did?

The kernel's whole thesis is **byte-author ≠ claimant**: the words in a commit
*subject* are authored by whoever wrote the message (forgeable); the *files the
commit touched* are authored by the commit machinery itself (not forgeable). That
split has nothing to do with whether a **human** or an **agent** wrote the
message — so this verdict is **author-neutral.** A person who writes
`fix: resolve the auth race` in a commit that touches only `README.md` has made a
claim their own diff cannot witness, exactly as an agent's `--allow-empty
"phase shipped"` does (docs/206 §5 E3, `benchmark/fleet_horizon/forge.py`).

Unlike `oracle.is_shipped`, this needs **no plan, no phase, no DOS vocabulary** —
it reads a single commit and grades the *relationship between the claim's KIND and
the diff's SHAPE*. That makes it the universal, zero-config form of the floor: a
`git log` audit / pre-commit lint / CI gate any git project can run.

It is PURE (`classify(CommitClaim, DiffFacts, policy) -> ClaimVerdict`): the git
read happens at the caller boundary (the `dos commit-audit` CLI / `audit_commit`
reader below), exactly like `liveness.classify` over `git_delta` — the arbiter
discipline. And it is **advisory**: it reports a rung, it does not block. A
doc-only "fix" is *sometimes* legitimate (a comment fix); the verdict says
"the claim rests on the message text, not the diff," and the consumer decides.

## What it can witness soundly (the non-forgeable facts)

1. **EMPTY_CLAIM** — the subject uses an *effect verb* ("fix/add/implement/
   remove/optimize/…") but the commit touched **zero source files** (it is empty,
   or touches only docs/config/generated/binary paths). The message claims a code
   change; the diff shows none. Sound: "touched 0 source files" is a fact about
   the commit, not the author's word.
2. **TEST_CLAIM_NO_TEST** — the subject claims tests ("add tests", "tests pass",
   "fix the failing test") but the diff touches **no test file**, or **net-deletes**
   test lines (the delete-the-assertion shape). Sound from the diff alone.
3. **the forgeability RUNG of every commit** — `diff-witnessed` (the diff touches
   source the subject plausibly refers to) vs `subject-only` (the claim rests on
   message text alone). The generalization of `oracle._grade_grep_source` to "does
   the diff corroborate the subject."

A claim that STRUCTURALLY scopes itself is witnessed by its own location kind:
`docs: …` by a doc file, and a CI-scoped claim (`ci: …` / `fix(ci): …`, the
conventional-commit type/scope) by a canonical CI-config path
(`.github/workflows/*.yml` & friends — `_is_ci_config`). The scope names where
the claimed effect LIVES, so the natural diff is corroboration, not contradiction
— without this, every honest `fix(ci):` touching only its workflow read as
"code claim, no source" (the 5b2b940/e5debd1 over-fire). The suppression needs
the CONJUNCTION (ci-shaped claim AND ci-config diff); either alone changes
nothing, so the widening is monotone fire-reducing.

## What it CANNOT witness — and so ABSTAINS on (state it; do not pretend)

- **Correctness.** A real fix to the *wrong* bug touches source and passes here.
  This grades *did the diff do the KIND of thing claimed*, never *was it right*
  (Wall 3, `project-dos-wall-presence-not-goal`).
- **Issue resolution.** "closes #42" needs an issue→files map this module does not
  have; it never claims to verify the *content* of an issue reference.
- **A subject with no checkable claim** ("wip", "misc", "address review") →
  `NO_CLAIM`, abstain. Most commits are honest and uncheckable; the verdict only
  *fires* where a concrete claim and a contradicting diff coexist.

The conservative direction is load-bearing: a false WARN on a legitimate doc-only
fix is annoying, so the verb taxonomy is deliberately tight and the source/non-
source split errs toward calling things source (fewer false EMPTY_CLAIM fires).
"""
from __future__ import annotations

import dataclasses
import re
import subprocess
from enum import Enum
from pathlib import Path

_GIT_TIMEOUT_S = 15


class ClaimKind(Enum):
    """The KIND of claim a commit subject makes — what we can check the diff against."""
    CODE_EFFECT = "code_effect"   # fix/add/implement/remove/refactor/optimize/…
    TEST = "test"                 # add/fix tests, "tests pass/green"
    DOC = "doc"                   # docs/comment/typo/readme — a doc claim
    NONE = "none"                 # wip/misc/merge/bump — no checkable behavioral claim


class Witness(Enum):
    """How the diff stands relative to the claim."""
    DIFF_WITNESSED = "diff-witnessed"   # the diff corroborates the claim (non-forgeable)
    SUBJECT_ONLY = "subject-only"       # the claim rests on message text alone (forgeable)
    ABSTAIN = "abstain"                 # no checkable claim, or no evidence either way


class Verdict(Enum):
    OK = "OK"                     # claim witnessed by the diff (or honestly doc-only)
    CLAIM_UNWITNESSED = "CLAIM_UNWITNESSED"   # a concrete claim the diff contradicts
    ABSTAIN = "ABSTAIN"           # no checkable claim


# Effect verbs that assert a *code* change. Tight on purpose (false WARNs are the
# cost). Matched at the START of the subject (conventional-commit style) OR as a
# leading word, case-insensitive. `refactor`/`rename`/`move` are CODE_EFFECT too
# but are guarded specially below (they legitimately touch many files / can be
# pure-move with no net line change).
_CODE_VERBS = (
    "fix", "fixes", "fixed", "add", "adds", "added", "implement", "implements",
    "implemented", "remove", "removes", "removed", "delete", "optimize",
    "optimise", "refactor", "rename", "move", "support", "handle", "resolve",
    "resolves", "patch", "correct", "introduce", "enable", "disable", "drop",
    "migrate", "upgrade", "downgrade", "wire", "hook",
)
# Verbs/markers that claim TESTS specifically.
_TEST_MARKERS = (
    "test", "tests", "testing", "unit test", "unit tests", "coverage",
    "assertion", "assertions", "spec", "specs",
)
_TEST_PASS_PHRASES = (
    "tests pass", "test pass", "tests green", "all tests", "passing tests",
    "tests passing", "green build", "fix the test", "fix failing test",
    "fix the failing test", "fixes the test",
)
# Doc-only claim markers (a subject that HONESTLY scopes itself to docs).
_DOC_MARKERS = (
    "doc", "docs", "documentation", "readme", "comment", "comments", "typo",
    "changelog", "wording", "rephrase", "clarify",
)
# Subjects with no checkable behavioral claim → NO_CLAIM (abstain).
_NOCLAIM_MARKERS = (
    "wip", "misc", "cleanup", "chore", "bump", "version", "release", "merge",
    "revert", "format", "lint", "whitespace", "style", "address review",
    "review feedback", "nit", "nits", "rename variable",
)

# Source-file suffixes — the universal "this is code" set. Erring toward INCLUSION
# (more suffixes = fewer false EMPTY_CLAIM fires on a real code change in an exotic
# language). A commit that touches one of these counts as touching source.
_SOURCE_SUFFIXES = (
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".hh", ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".sh", ".bash", ".zsh",
    ".ps1", ".psm1", ".psd1", ".bat", ".cmd",
    ".pl", ".pm", ".lua", ".r", ".jl", ".dart", ".ex", ".exs", ".erl", ".clj",
    ".cljs", ".hs", ".ml", ".fs", ".vb", ".sql", ".proto", ".tf", ".gradle",
    ".cmake", ".mk", ".vue", ".svelte", ".elm", ".nim", ".zig", ".v",
)
# Non-source: docs, config, data, binaries, lockfiles. A commit touching ONLY
# these (and no _SOURCE_SUFFIXES file) has not touched code.
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc", ".org")
_BINARY_OR_DATA_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".ico", ".woff", ".woff2",
    ".ttf", ".zip", ".tar", ".gz", ".tgz", ".whl", ".egg", ".so", ".dll",
    ".dylib", ".exe", ".o", ".a", ".jar", ".class", ".bin", ".lock", ".sum",
    ".csv", ".tsv", ".parquet",
)

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__)(/|$)|"
    r"(^|/)(test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[A-Za-z0-9]+$",
    re.IGNORECASE,
)

# CI-config locations — the canonical places CI behavior is DECLARED. A ci-scoped
# claim (see `_scope_is_ci`) is witnessed by a diff touching one of these, the way
# a doc-scoped claim is witnessed by a doc file. CLOSED set, matched structurally
# (the workflows dir prefix + a yaml suffix, or an exact well-known filename) —
# never a substring, never "any .yml" (a stray config.yml is not CI).
_CI_WORKFLOW_DIR = ".github/workflows/"
_CI_CONFIG_FILES = (
    ".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "appveyor.yml",
    ".appveyor.yml", ".circleci/config.yml", "bitbucket-pipelines.yml",
)


@dataclasses.dataclass(frozen=True)
class CommitClaim:
    """The claim side: what the commit SUBJECT asserts (author-authored, forgeable)."""
    sha: str
    subject: str


@dataclasses.dataclass(frozen=True)
class DiffFacts:
    """The witness side: what the commit's DIFF actually did (machinery-authored).

    `files` is the set of repo-relative paths the commit touched. `is_empty` is
    True for a `--allow-empty` commit (zero files). `test_lines_added` /
    `test_lines_removed` are the net test-file line deltas when known (−1/−1 means
    "not computed"; the verdict then falls back to test-file presence alone).
    """
    files: tuple[str, ...]
    is_empty: bool
    test_lines_added: int = -1
    test_lines_removed: int = -1


@dataclasses.dataclass(frozen=True)
class ClaimPolicy:
    """Knobs (all conservative by default). Kept tiny — the point is zero-config."""
    # Treat a commit touching only docs as a doc claim even if the verb is a code
    # verb? Off by default = we DO fire EMPTY_CLAIM on `fix: ...` touching only a
    # .md (the common "claimed code, edited a doc" case). A repo that commits code
    # fixes as markdown (rare) can turn this on to silence it.
    docs_satisfy_code_claim: bool = False


DEFAULT_POLICY = ClaimPolicy()


@dataclasses.dataclass(frozen=True)
class ClaimVerdict:
    """The graded relationship between a commit's claim and its diff."""
    sha: str
    verdict: Verdict
    claim_kind: ClaimKind
    witness: Witness
    reason: str
    # the files that made the witness call (touched source, tests, or CI config),
    # for an honest, inspectable verdict — never just a boolean.
    source_files: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    ci_files: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "verdict": self.verdict.value,
            "claim_kind": self.claim_kind.value,
            "witness": self.witness.value,
            "reason": self.reason,
            "source_files": list(self.source_files),
            "test_files": list(self.test_files),
            "ci_files": list(self.ci_files),
        }


# ---------------------------------------------------------------------------
# Pure helpers — classify the claim, classify the diff. No I/O.
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip()


def _is_source(path: str) -> bool:
    p = _norm(path).lower()
    if p.endswith(_SOURCE_SUFFIXES):
        return True
    if p.endswith(_DOC_SUFFIXES) or p.endswith(_BINARY_OR_DATA_SUFFIXES):
        return False
    # Unknown suffix / extensionless (Makefile, Dockerfile, a bare script): treat
    # as source. Erring toward source keeps EMPTY_CLAIM conservative.
    base = p.rsplit("/", 1)[-1]
    if base in ("makefile", "dockerfile", "rakefile", "gemfile", "procfile"):
        return True
    if base.startswith(".") and "." not in base[1:]:
        # A bare dotfile (.gitattributes, .gitignore, .flake8) is an
        # extensionless config file whose whole NAME looks like a suffix, so
        # the extensionless branch below misses it. It is behavior-bearing
        # repo plumbing, not a doc — same erring-toward-source rule (the
        # 04c740c over-fire: `fix(ci): pin the corpora LF` touching only
        # .gitattributes IS the fix it claims).
        return True
    if "." not in base:
        return True
    return False


def _is_test(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(_norm(path)))


def _is_ci_config(path: str) -> bool:
    p = _norm(path).lower()
    if p.startswith(_CI_WORKFLOW_DIR) and p.endswith((".yml", ".yaml")):
        return True
    return p in _CI_CONFIG_FILES


def _leading_words(subject: str) -> list[str]:
    """The words that can LEAD a claim — at most one per meaningful segment. Pure.

    A commit subject puts the verb in one of two places:
      * the conventional-commit TYPE — `fix(parser): …` / `fix: …` → verb `fix`;
      * the REMAINDER after a scope/path prefix that has no verb of its own —
        `benchmark/foo: implement X` / `mcp: add Y` → verb `implement`/`add`. DOS's
        own `path/scope: <verb> …` grammar lives here, and the old parser missed it
        (it only read the pre-colon head), so every scoped subject false-ABSTAINed.

    Returns the FIRST word of the prefix AND the FIRST word of the remainder (when
    the subject is `<scope-shaped-prefix>: <rest>`), else just the subject's first
    word. Returning only the *leading* word of each segment — not a window — is what
    keeps the widening conservative: a verb BURIED later (`kernel: report the fix
    status` → leads are `kernel`, `report`; `fix` is not a lead) does NOT fire. Only
    a verb that genuinely begins the type or the scoped message is seen.
    """
    s = subject.strip().lower()
    leads: list[str] = []
    if ":" in s:
        head, rest = s.split(":", 1)
        if len(head) < 30 and " " not in head.strip():
            hw = re.findall(r"[a-z][a-z']*", head)
            rw = re.findall(r"[a-z][a-z']*", rest)
            if hw:
                leads.append(hw[0])
            if rw:
                leads.append(rw[0])
            return leads
    w = re.findall(r"[a-z][a-z']*", s)
    return w[:1]


def _phrase_window(subject: str, n: int = 8) -> str:
    """The lowercased first-n-words string, for multi-word marker matching
    (`unit test`, `tests pass`) that is not position-locked to a single lead."""
    s = subject.strip().lower()
    # drop a scope-shaped prefix so "tests pass" in the remainder is reached
    if ":" in s:
        head, rest = s.split(":", 1)
        if len(head) < 30 and " " not in head.strip():
            s = rest.strip()
    words = re.findall(r"[a-z][a-z']*", s)[:n]
    return " ".join(words)


def classify_claim(subject: str) -> ClaimKind:
    """What KIND of claim does this subject make? Pure, conservative.

    Order matters: an explicit no-claim marker wins (so `chore: bump deps` is
    NONE, not CODE_EFFECT on `bump`); then a TEST claim; then a DOC claim; then a
    CODE_EFFECT verb; else NONE.
    """
    s = subject.strip().lower()
    if not s:
        return ClaimKind.NONE
    # Merge/revert/bump/wip → no checkable behavioral claim. Match WHOLE WORDS, never
    # substrings: a substring match made "u**nit** tests" hit the `nit` marker (and
    # would make "con**version**" hit `version`, "life**style**" hit `style`). Build
    # the subject's word set; single-word markers match a word, multi-word markers
    # ("address review") still match as a phrase.
    word_set = set(re.findall(r"[a-z][a-z']*", s))
    single = {m for m in _NOCLAIM_MARKERS if " " not in m}
    multi = [m for m in _NOCLAIM_MARKERS if " " in m]
    if (word_set & single) or any(m in s for m in multi):
        return ClaimKind.NONE
    leads = _leading_words(s)          # ≤1 verb-candidate per segment (conservative)
    window = _phrase_window(s)         # the remainder, for multi-word marker phrases
    # TEST claim: a "tests pass" phrase, a test marker LEADING a segment, or a
    # test noun in the first few words of the message ("add tests", "write specs",
    # "unit test the engine") — checked BEFORE the code verb so `add tests` is a
    # TEST claim, not a generic CODE_EFFECT (they differ: the test path also gates
    # the delete-the-assertion shape).
    if any(p in s for p in _TEST_PASS_PHRASES):
        return ClaimKind.TEST
    if any(w in _TEST_MARKERS for w in leads):
        return ClaimKind.TEST
    window_words = window.split()
    if any(w in ("test", "tests", "testing", "spec", "specs", "coverage")
           for w in window_words[:3]):
        return ClaimKind.TEST
    # DOC claim: a doc marker LEADING a segment (honestly scoped to docs), OR the
    # scope-prefix itself NAMES a doc target (`CLAUDE.md: add a glossary`,
    # `README: …`, `docs/206: …`). A `<doc-file>: <action verb> …` subject is a
    # documentation edit even though the verb is `add`/`update` — scoping to a doc
    # file is itself the honest "this is docs" signal, so it must not read as an
    # unwitnessed CODE_EFFECT (the e2d5aa9/b2f58fb/f4a36aa real-history finding).
    if any(w in _DOC_MARKERS for w in leads) or _scope_is_doc(s):
        return ClaimKind.DOC
    # CODE_EFFECT: a code verb LEADING the type or the scoped message.
    if any(w in _CODE_VERBS for w in leads):
        return ClaimKind.CODE_EFFECT
    return ClaimKind.NONE


def _scope_is_doc(subject: str) -> bool:
    """True iff a scope-shaped prefix (`<prefix>: …`) names a documentation target —
    a doc suffix (`.md`/`.rst`/…), a known doc file (`CLAUDE.md`/`README`/`LICENSE`),
    or a `docs/` path. Pure."""
    s = subject.strip().lower()
    if ":" not in s:
        return False
    head = s.split(":", 1)[0].strip()
    if " " in head or len(head) >= 40:
        return False
    if head.endswith(_DOC_SUFFIXES):
        return True
    if head.startswith(("docs/", "doc/")):
        return True
    base = head.rsplit("/", 1)[-1]
    return base in ("readme", "claude.md", "claude", "license", "contributing",
                    "changelog", "authors", "notice")


_CC_HEAD_RE = re.compile(r"^([a-z][a-z0-9_-]*)(?:\(([^)]*)\))?!?$")


def _scope_is_ci(subject: str) -> bool:
    """True iff the conventional-commit head names CI as its TYPE or SCOPE —
    `ci: …`, `ci(test): …`, `fix(ci): …`. STRUCTURAL token equality on the parsed
    head, never a word-search: "fix the CI cache" in prose does not engage, nor
    does a scope merely containing the letters (`fix(circus):` is not CI). Pure."""
    s = subject.strip().lower()
    if ":" not in s:
        return False
    m = _CC_HEAD_RE.match(s.split(":", 1)[0].strip())
    if not m:
        return False
    return m.group(1) == "ci" or m.group(2) == "ci"


def _is_pure_move_or_rename(subject: str) -> bool:
    """A rename/move legitimately touches many files with little net line change —
    do not fire EMPTY_CLAIM/UNWITNESSED on it from line deltas alone."""
    words = _leading_words(subject)
    return any(w in ("rename", "move", "moved", "renamed") for w in words)


def classify(claim: CommitClaim, diff: DiffFacts,
             policy: ClaimPolicy = DEFAULT_POLICY) -> ClaimVerdict:
    """THE verdict: does the commit's claim match its diff? Pure.

    Author-neutral, plan-free. Fires `CLAIM_UNWITNESSED` only where a concrete
    claim and a contradicting diff coexist; `ABSTAIN`s on everything else (most
    commits). The witness rung (`diff-witnessed`/`subject-only`) is reported on
    every commit that makes a code/test claim, so even an `OK` carries its rung.
    """
    kind = classify_claim(claim.subject)
    source_files = tuple(f for f in diff.files if _is_source(f))
    test_files = tuple(f for f in diff.files if _is_test(f))
    ci_files = tuple(f for f in diff.files if _is_ci_config(f))
    # A ci-scoped claim is witnessed by the CI config itself — the scope names
    # where the claimed effect lives, so the workflows diff is corroboration, not
    # contradiction. CONJUNCTIVE on purpose: the shaped claim alone (a ci scope
    # over a README diff) or the path alone (a bare `fix:` over a workflows diff)
    # changes nothing, so this can only suppress an over-fire, never add one.
    ci_witnessed = bool(ci_files) and _scope_is_ci(claim.subject)

    if kind is ClaimKind.NONE:
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.ABSTAIN, claim_kind=kind,
            witness=Witness.ABSTAIN,
            reason="subject makes no checkable code/test claim",
            source_files=source_files, test_files=test_files, ci_files=ci_files)

    if kind is ClaimKind.DOC:
        # A doc claim is witnessed by ANY touched file (docs count); it never
        # over-claims code, so it is OK as long as it is not empty.
        if diff.is_empty:
            return ClaimVerdict(
                sha=claim.sha, verdict=Verdict.CLAIM_UNWITNESSED, claim_kind=kind,
                witness=Witness.SUBJECT_ONLY,
                reason="doc claim but the commit is empty (touched nothing)",
                source_files=source_files, test_files=test_files, ci_files=ci_files)
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.OK, claim_kind=kind,
            witness=Witness.DIFF_WITNESSED,
            reason="doc claim, the commit touches files (doc scope, no code over-claim)",
            source_files=source_files, test_files=test_files, ci_files=ci_files)

    if kind is ClaimKind.TEST:
        # A test claim must touch a test file. Net-deleting test lines while
        # claiming "tests pass/green" is the delete-the-assertion shape.
        net_test_delta_known = (diff.test_lines_added >= 0
                                and diff.test_lines_removed >= 0)
        net_deleted = (net_test_delta_known
                       and diff.test_lines_removed > diff.test_lines_added)
        if not test_files:
            return ClaimVerdict(
                sha=claim.sha, verdict=Verdict.CLAIM_UNWITNESSED, claim_kind=kind,
                witness=Witness.SUBJECT_ONLY,
                reason="claims tests but the diff touches no test file",
                source_files=source_files, test_files=test_files, ci_files=ci_files)
        if net_deleted and not _is_pure_move_or_rename(claim.subject):
            return ClaimVerdict(
                sha=claim.sha, verdict=Verdict.CLAIM_UNWITNESSED, claim_kind=kind,
                witness=Witness.SUBJECT_ONLY,
                reason=(f"claims tests but net-DELETES test lines "
                        f"(+{diff.test_lines_added}/-{diff.test_lines_removed}) "
                        "— the delete-the-assertion shape"),
                source_files=source_files, test_files=test_files, ci_files=ci_files)
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.OK, claim_kind=kind,
            witness=Witness.DIFF_WITNESSED,
            reason="test claim witnessed by a touched test file",
            source_files=source_files, test_files=test_files, ci_files=ci_files)

    # kind is CODE_EFFECT
    if source_files:
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.OK, claim_kind=kind,
            witness=Witness.DIFF_WITNESSED,
            reason="code-effect claim witnessed by a touched source file",
            source_files=source_files, test_files=test_files, ci_files=ci_files)
    if ci_witnessed:
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.OK, claim_kind=kind,
            witness=Witness.DIFF_WITNESSED,
            reason=("ci-scoped claim witnessed by the CI config it names "
                    "(the scope is where the claimed effect lives)"),
            source_files=source_files, test_files=test_files, ci_files=ci_files)
    # No source touched. Empty, or docs/config/binary only → the claim rests on the
    # subject text alone. (A pure rename can land here with renames git records as
    # add+delete pairs — but those ARE source files, so source_files is non-empty;
    # the move guard is for the line-delta path, not this presence check.)
    if diff.is_empty:
        why = "code-effect claim but the commit is EMPTY (touched no files)"
    elif policy.docs_satisfy_code_claim:
        return ClaimVerdict(
            sha=claim.sha, verdict=Verdict.OK, claim_kind=kind,
            witness=Witness.DIFF_WITNESSED,
            reason="code claim; docs_satisfy_code_claim policy accepts doc-only diff",
            source_files=source_files, test_files=test_files, ci_files=ci_files)
    else:
        touched = ", ".join(diff.files[:4]) or "(nothing)"
        why = (f"code-effect claim but the diff touches no SOURCE file "
               f"(only: {touched}) — the claim rests on the subject text")
    return ClaimVerdict(
        sha=claim.sha, verdict=Verdict.CLAIM_UNWITNESSED, claim_kind=kind,
        witness=Witness.SUBJECT_ONLY, reason=why,
        source_files=source_files, test_files=test_files, ci_files=ci_files)


# ---------------------------------------------------------------------------
# Sweep summary — the "how honest are this repo's commit messages?" rate. Pure.
# ---------------------------------------------------------------------------


def sweep_summary(verdicts: list[ClaimVerdict]) -> dict:
    """Fold a list of `ClaimVerdict`s into the drift RATE + a by-claim-kind grid.

    The headline `drift_rate` is `unwitnessed / checkable`, where *checkable* =
    commits that made a concrete code/test/doc claim (everything that did NOT
    ABSTAIN). Abstains are excluded from the denominator on purpose: a `wip`/`merge`
    commit makes no claim to be honest or dishonest about, so counting it would dilute
    the rate toward zero and hide real drift. We report both denominators so the
    reader sees how much of the history was checkable at all.

    This is a RATE over real history, not a per-commit gate — the docs/179 caution
    applies: it re-describes the corpus it is given (it mints no new label), so it is
    a description of THIS repo's commit hygiene, not a generalization.
    """
    total = len(verdicts)
    unwitnessed = [v for v in verdicts if v.verdict is Verdict.CLAIM_UNWITNESSED]
    witnessed = [v for v in verdicts if v.verdict is Verdict.OK]
    abstained = [v for v in verdicts if v.verdict is Verdict.ABSTAIN]
    checkable = len(unwitnessed) + len(witnessed)

    by_kind: dict[str, dict[str, int]] = {}
    for v in verdicts:
        k = v.claim_kind.value
        row = by_kind.setdefault(k, {"unwitnessed": 0, "witnessed": 0, "abstain": 0})
        if v.verdict is Verdict.CLAIM_UNWITNESSED:
            row["unwitnessed"] += 1
        elif v.verdict is Verdict.OK:
            row["witnessed"] += 1
        else:
            row["abstain"] += 1

    return {
        "commits": total,
        "checkable": checkable,           # made a concrete claim (the denominator)
        "abstained": len(abstained),      # no checkable claim
        "witnessed": len(witnessed),
        "unwitnessed": len(unwitnessed),
        "drift_rate": (len(unwitnessed) / checkable) if checkable else 0.0,
        "by_kind": by_kind,
        # the actual offenders, so the rate is inspectable, never just a number
        "unwitnessed_shas": [v.sha for v in unwitnessed],
    }


# ---------------------------------------------------------------------------
# Boundary I/O — read a commit's subject + diff facts from git. NOT pure.
# ---------------------------------------------------------------------------
#
# The git reads happen HERE (the `git_delta`/`liveness` discipline); `classify`
# above never shells out. `root` is passed EXPLICITLY (never the process-global
# config) so a long-lived caller — the MCP server, a CI runner — gets the right
# tree. Every failure degrades to a safe value (an empty/abstaining read), never a
# crash: a non-git dir, a missing binary, a bad ref, a timeout.


def _git(root: Path | str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, check=False,
                           timeout=_GIT_TIMEOUT_S,
                           stdin=subprocess.DEVNULL)  # docs/295
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout


def read_commit(ref: str, *, root: Path | str) -> tuple[CommitClaim, DiffFacts] | None:
    """Read one commit's subject + diff facts from git at ``ref`` over ``root``.

    Returns ``(CommitClaim, DiffFacts)`` ready for `classify`, or ``None`` if the
    ref can't be read (non-git dir, bad ref, git missing). Uses ``--numstat`` over
    the first parent so a merge commit reports its own delta; binary files report
    ``-``/``-`` numstat and are counted as touched files but contribute no line
    delta.
    """
    rc, sha = _git(root, "rev-parse", "--short", ref)
    if rc != 0 or not sha.strip():
        return None
    sha = sha.strip()
    rc, subject = _git(root, "log", "-1", "--pretty=format:%s", ref)
    if rc != 0:
        return None
    subject = subject.strip()

    # numstat: "<added>\t<removed>\t<path>" per file; binary → "-\t-\t<path>".
    rc, numstat = _git(root, "show", "--numstat", "--format=", "--no-renames", ref)
    files: list[str] = []
    test_add = test_rem = 0
    have_test_delta = False
    if rc == 0:
        for line in numstat.splitlines():
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_s, removed_s, path = parts
            path = path.strip()
            if not path:
                continue
            files.append(path)
            if _is_test(path) and added_s != "-" and removed_s != "-":
                try:
                    test_add += int(added_s)
                    test_rem += int(removed_s)
                    have_test_delta = True
                except ValueError:
                    pass

    diff = DiffFacts(
        files=tuple(files),
        is_empty=(len(files) == 0),
        test_lines_added=(test_add if have_test_delta else -1),
        test_lines_removed=(test_rem if have_test_delta else -1),
    )
    return CommitClaim(sha=sha, subject=subject), diff


def audit_commit(ref: str = "HEAD", *, root: Path | str = ".",
                 policy: ClaimPolicy = DEFAULT_POLICY) -> ClaimVerdict | None:
    """Read commit ``ref`` and return its `ClaimVerdict`, or ``None`` if unreadable.

    The one-call convenience the CLI / a pre-commit hook / the MCP tool use.
    """
    got = read_commit(ref, root=root)
    if got is None:
        return None
    claim, diff = got
    return classify(claim, diff, policy)


def audit_range(rev_range: str, *, root: Path | str = ".",
                policy: ClaimPolicy = DEFAULT_POLICY,
                limit: int = 500) -> list[ClaimVerdict]:
    """Audit every commit in a `git log` range (e.g. ``origin/main..HEAD``).

    Returns newest-first `ClaimVerdict`s, capped at ``limit`` so a huge range
    can't hang an audit. Empty list on any read failure (the fail-safe floor).
    """
    rc, out = _git(root, "log", f"-{int(limit)}", "--pretty=format:%H", rev_range)
    if rc != 0:
        return []
    verdicts: list[ClaimVerdict] = []
    for sha in out.splitlines():
        sha = sha.strip()
        if not sha:
            continue
        v = audit_commit(sha, root=root, policy=policy)
        if v is not None:
            verdicts.append(v)
    return verdicts
