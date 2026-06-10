"""dos.drivers.ci_status — the CI/Checks oracle (a driver, the move-B reference).

docs/85 §2 names three ways to "extend the verifiable surface," and only one is a
kernel change. This module is the canonical instance of **move (B): a new artifact
oracle for the non-git surface**. `verify()` reads the git fossil — existence +
ancestry + subject grammar — and docs/84 §3.3 is blunt that a clean `verify()`
means *shipped*, not *correct*: git confirms the bytes are reachable, never that
they compile or pass tests. The single biggest *complete → correct* jump is a
**behavioral oracle**: "the build is green at this commit." That signal does not
live in git; it lives in a CI system. So it cannot be a kernel verb (it fails
gate 3 — *domain-free* — because it speaks a specific provider's Checks API), and
it must not grow a provider branch inside `dos.oracle`. It lives **here, in a
driver**, exactly as `drivers/llm_judge` does, and for the same structural reason:
it has the surface the kernel forbids (network I/O against a third party).

Where it sits on the docs/84 §4 rung-ladder — *above* every git rung, because its
referent is more accountable than a commit subject the agent typed:

    non-git oracle (build/test/CI green)   ← THIS module's verdict; strongest "complete ≈ correct"
      registry stamp ⋈ git ancestry        ← dos.oracle source="registry"
        distinctive file-path overlap       ← dos.oracle grep rung, file backstop
          direct-ship subject match         ← dos.oracle grep rung, subject
            source="none" / via=""          ← git history alone / could not confirm

The accountability spectrum (docs/85 §1) is the whole reason this is worth a rung:
a CI conclusion is **mutable third-party state on infrastructure the agent does not
control** — GitHub ran the workflow, recorded the conclusion, and the agent under
adjudication cannot retroactively forge a `check_run.conclusion == "success"` on a
public commit without compromising the CI system itself. That is a strictly more
accountable referent than the commit subject the same agent authored. It is NOT the
top of the spectrum — a CI system the agent *administers* (it can edit the workflow,
disable a required check, or re-run with a patched config) is only as honest as the
branch protection around it — which is exactly why this stays a **driver oracle the
host wires**, with the strength of the signal a property of the host's CI setup, not
a kernel guarantee. The kernel ships the socket; the host decides how accountable
the thing they plug in is.

The shape is the kernel's own, lifted from two templates already in the tree:

  * the **boundary reader** `gather()` mirrors `dos.git_delta`: the subprocess
    (`gh api …`) happens HERE, at the caller boundary, and every failure mode (no
    `gh`, unauthenticated, network error, timeout, unknown SHA, malformed JSON)
    degrades to an honest `NO_SIGNAL` evidence object — never a crash, never a
    propagated exception. A repo with no CI wired gets "no signal," the truthful
    floor, exactly as `verify` degrades to `source="none"`.
  * the **pure classifier** `classify(CiEvidence, CiPolicy) -> CiVerdict` is in the
    `dos.verdict` typed-verdict family (the `classify(Evidence, Policy) -> Verdict`
    ABI that `liveness`/`scope` share): a closed-enum verdict, a frozen caller-
    gathered evidence dataclass, a frozen policy with a `dos.toml`-shaped seam, an
    operator-facing `reason`, and a `to_dict()` for the JSON/MCP/decisions surface.
    `classify()` makes NO I/O — it reads the already-gathered check tallies, so the
    whole verdict is replay-testable on frozen fixtures, the family discipline.

And it obeys the three judge-driver disciplines (docs/87) — it is the deterministic
cousin of `llm_judge`, so the same fences apply:

  * **Advisory.** It reports a verdict; it never refuses a lease, reverts a commit,
    or mutates a registry. A host MAY consult it (a `CiPredicate` over the arbiter's
    conjunctive admission seam, or a RED row in the `dos decisions` queue) — but the
    CI verdict and the admission decision stay different syscalls, the line
    `liveness`/SPINNING and `scope`/SCOPE_CREEP both hold.
  * **Fail-safe, never fail-open.** With no provider reachable the verdict is
    `NO_SIGNAL` (route to a human), and a CI system mid-run is `PENDING` (not yet
    answerable) — never a fabricated GREEN. The conservative direction, the
    `run_judge` fail-to-abstain discipline restated for a deterministic reader: an
    absent oracle degrades to "ask a human," never to a rubber-stamp.
  * **One-way import.** It imports the kernel; the kernel never imports it
    (`drivers/__init__` rule, pinned by `tests/test_kernel_no_driver_import`).

"Use this pipeline ourselves" (the dog-food hook): `gather()` defaults its `repo`
to this project's own GitHub remote, so `python -m dos.drivers.ci_status <sha>`
adjudicates DOS's *own* CI run (`.github/workflows/ci.yml`) for a commit — the
substrate consulting the same green-build fossil it asks its users to trust. The
`/release` and `/stable-release` gates (which today shell `pytest` locally) can
consult this oracle instead, so "the suite is green" becomes a *verified* claim
against the third-party CI record rather than a local self-report.
"""

from __future__ import annotations

import argparse
import enum
import json
import subprocess
from dataclasses import dataclass
from typing import Optional

# Imports the kernel — never the other way round (the driver rule). `config` for the
# CLI's workspace seam; the evidence vocabulary for the `EvidenceSource` face
# (`CiStatusSource`, the `dos.evidence_sources` occupant). The verdict itself is
# self-contained.
from dos import config as _config
from dos.evidence import Accountability, EvidenceFacts

# The project's own remote — the dog-food default so `python -m dos.drivers.ci_status
# <sha>` with no --repo adjudicates DOS's own pipeline. A host wiring this for its own
# repo passes --repo / the `repo=` argument; this default is only a convenience for the
# substrate verifying itself.
DEFAULT_REPO = "anthony-chaudhary/dos-kernel"

# Cap the network call so a hung API can't stall an evidence-gather — the
# `git_delta._GIT_TIMEOUT_S` discipline, a touch longer for a network round-trip.
_GH_TIMEOUT_S = 20


class Ci(str, enum.Enum):
    """The typed CI verdict — four states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `liveness.Liveness`, `scope.Scope`,
    `gate_classify.Verdict`).

    The four-way split is deliberate and is the honest part: a binary green/red
    would have to *lie* about the two cases where there is no answer yet —
    in-flight (PENDING) and unwired/unreachable (NO_SIGNAL). Collapsing either into
    RED would manufacture a failure; collapsing either into GREEN would manufacture
    a pass. Both are kept distinct so the verdict never claims more than the
    evidence supports — the typed-verdict-over-binary-gate design law applied to a
    source that is legitimately sometimes silent.
    """

    GREEN = "GREEN"          # every required check concluded successfully
    RED = "RED"              # at least one required check failed/errored/was cancelled
    PENDING = "PENDING"      # checks exist but at least one is still queued/running (no failure yet)
    NO_SIGNAL = "NO_SIGNAL"  # no checks found, or the provider is unwired/unreachable — ask a human

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# GitHub check-run `conclusion` values that count as a hard failure. A check whose
# conclusion is none of these AND is not yet "completed" is still in flight
# (PENDING); a "neutral"/"skipped" conclusion is NOT a failure (a skipped optional
# job must not redden the verdict). This is the GitHub Checks vocabulary, named here
# because the verdict's meaning depends on it — the one place provider specifics are
# allowed (a driver, not the kernel).
_FAILING_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "action_required", "stale"})
_PASSING_CONCLUSIONS = frozenset({"success"})
# Conclusions that neither pass nor fail — they do not gate. A run made entirely of
# these (with none still in flight) is GREEN: nothing required failed.
_NEUTRAL_CONCLUSIONS = frozenset({"neutral", "skipped"})


@dataclass(frozen=True)
class CiPolicy:
    """The knobs that separate GREEN/RED/PENDING — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as
    `LivenessPolicy`/`ScopePolicy`. The defaults are GENERIC; a workspace declares
    its own in `dos.toml [ci]` read back through `SubstrateConfig`, the
    closed-config-as-data pattern (`[lanes]`/`[stamp]`/`[reasons]`/`[liveness]`/
    `[scope]`).

      required_checks   — when non-empty, ONLY check-runs whose name is in this set
                          gate the verdict; all others are advisory and ignored. The
                          mechanical analogue of GitHub branch-protection "required
                          status checks." Empty (default) = every check gates: the
                          strict, no-config floor (any failing check reddens).
      treat_pending_as  — what a still-running required check resolves to when you
                          need a binary answer downstream. Default keeps PENDING its
                          own state (the honest answer); a host that wants
                          "not-yet-green ⇒ block" can fold it. Kept as data so the
                          *verdict* never has to guess the host's risk posture.
    """

    required_checks: frozenset[str] = frozenset()
    treat_pending_as: Ci = Ci.PENDING

    def __post_init__(self) -> None:
        if self.treat_pending_as not in (Ci.PENDING, Ci.RED, Ci.NO_SIGNAL):
            raise ValueError("treat_pending_as must be PENDING, RED, or NO_SIGNAL")


DEFAULT_POLICY = CiPolicy()


@dataclass(frozen=True)
class CheckRun:
    """One CI check-run, normalized from the provider's record (the unforgeable bit).

    `status` is GitHub's lifecycle (`queued`/`in_progress`/`completed`);
    `conclusion` is meaningful only once `status == "completed"`
    (`success`/`failure`/`neutral`/…). The agent under adjudication cannot author
    these for a public commit — they are written by the CI system. That is the gate-2
    (unforgeable) property the whole oracle stands on.
    """

    name: str
    status: str
    conclusion: Optional[str] = None


@dataclass(frozen=True)
class CiEvidence:
    """Everything `classify()` needs, gathered by the CALLER before the call.

    No network, no subprocess inside the verdict — the arbiter/`git_delta` rule.
    `gather()` (the boundary) runs `gh api` and normalizes the response into this
    frozen object; `classify()` receives it and is pure.

      sha          — the commit the checks belong to (echoed for the json/operator
                     surface; not an input to the ladder).
      repo         — `owner/name` the checks were read from (provenance for the
                     operator — *which* CI record answered).
      checks       — the normalized check-runs. An EMPTY tuple is the load-bearing
                     ambiguity: it means *either* "this commit genuinely has no CI"
                     *or* "we could not read the provider." `gather()` distinguishes
                     them by setting `reachable=False` on the latter, so the verdict
                     can say NO_SIGNAL for both but the `reason` tells the truth.
      reachable    — False when the provider call itself failed (no `gh`, unauthed,
                     network/timeout, bad JSON). With `reachable=False` the verdict is
                     always NO_SIGNAL regardless of `checks` — we observed nothing, so
                     we assert nothing (fail-safe, never fail-open).
      detail       — a one-line note from the gather (the error class on an
                     unreachable read, or "" on a clean read) — carried into the
                     verdict `reason` so an operator sees *why* there was no signal.
    """

    sha: str
    repo: str = ""
    checks: tuple[CheckRun, ...] = ()
    reachable: bool = True
    detail: str = ""


@dataclass(frozen=True)
class CiVerdict:
    """The single verdict `classify()` returns, with the evidence echoed back.

    `verdict` is the typed `Ci`. `reason` is a one-line operator-facing summary that
    NAMES the driving checks (legible distrust — not just RED but *which* check
    failed), the RND/Axis-4 renderer seam, identical to `liveness`'s "0 commits,
    heartbeat 8m fresh." `to_dict()` is the JSON shape for `--json` / MCP / the
    decisions queue.

    Conforms structurally to `dos.verdict.TypedVerdict` (a `str`-enum `verdict`, a
    `str` `reason`, a JSON-shaped `to_dict()`), so a future `dos.verdicts.register`
    could expose it uniformly — though as a *driver* oracle it stays host-wired, not
    a `dos <verb>` subcommand (it fails gate 3, domain-free).
    """

    verdict: Ci
    reason: str
    evidence: CiEvidence
    failing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    passing: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        ev = self.evidence
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "failing": list(self.failing),
            "pending": list(self.pending),
            "passing": list(self.passing),
            "evidence": {
                "sha": ev.sha,
                "repo": ev.repo,
                "reachable": ev.reachable,
                "detail": ev.detail,
                "checks": [
                    {"name": c.name, "status": c.status, "conclusion": c.conclusion}
                    for c in ev.checks
                ],
            },
        }


def classify(ev: CiEvidence, policy: CiPolicy = DEFAULT_POLICY) -> CiVerdict:
    """Classify one commit's CI status from already-gathered evidence. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "is the build
    green at this commit?"):

      1. NO_SIGNAL — the provider was unreachable, OR there are no (gating) checks
                     at all. We observed nothing we can stand on → route to a human.
                     Checked FIRST on the unreachable path so a failed read can never
                     be mistaken for a real verdict (fail-safe).
      2. RED       — at least one *gating* check concluded in `_FAILING_CONCLUSIONS`.
                     A failure dominates: one red required check reddens the commit
                     regardless of how many others passed.
      3. PENDING   — no failure, but at least one gating check is not yet completed
                     (queued/in_progress, or completed with no conclusion). The build
                     is not green *yet*; it is not red either. The honest middle.
      4. GREEN     — every gating check completed and none failed (all passing or
                     neutral/skipped). The build is green.

    The RED-dominates ordering is the conservative one: when checks disagree, the
    failure wins, because a believer must not be told "green" while a required check
    is red. PENDING over GREEN for the same reason — an unfinished check is not a
    pass.
    """
    # 1a. NO_SIGNAL (unreachable) — the provider call failed. We saw nothing, so we
    #     assert nothing: NO_SIGNAL with the gather's error class in the reason. This
    #     is the fail-safe rung — an unwired/unreachable CI never fabricates a verdict.
    if not ev.reachable:
        return CiVerdict(
            verdict=Ci.NO_SIGNAL,
            reason=(
                f"no CI signal for {ev.sha[:12] or '(no sha)'}"
                + (f" in {ev.repo}" if ev.repo else "")
                + (f" — {ev.detail}" if ev.detail else " — provider unreachable")
            ),
            evidence=ev,
        )

    # Select the gating subset: when the host declared required_checks, only those
    # gate; otherwise every check gates (the strict no-config floor).
    if policy.required_checks:
        gating = tuple(c for c in ev.checks if c.name in policy.required_checks)
    else:
        gating = ev.checks

    # 1b. NO_SIGNAL (no checks) — the commit has no gating CI to read. Distinct from
    #     unreachable (we DID read the provider; there just is nothing here), and the
    #     reason says so — the honest floor for a repo/commit with no CI, never a crash.
    if not gating:
        if ev.checks:  # there were checks, but none matched required_checks
            reason = (
                f"none of the {len(ev.checks)} check(s) on {ev.sha[:12]} match the "
                f"required set {sorted(policy.required_checks)} — no gating signal"
            )
        else:
            reason = (
                f"no CI checks found for {ev.sha[:12] or '(no sha)'}"
                + (f" in {ev.repo}" if ev.repo else "")
                + " — commit has no CI, or none has reported yet"
            )
        return CiVerdict(verdict=Ci.NO_SIGNAL, reason=reason, evidence=ev)

    failing = tuple(
        c.name for c in gating
        if c.status == "completed" and (c.conclusion or "") in _FAILING_CONCLUSIONS
    )
    # Not-yet-conclusive: still queued/running, or completed without a conclusion.
    pending = tuple(
        c.name for c in gating
        if c.status != "completed" or not c.conclusion
    )
    passing = tuple(
        c.name for c in gating
        if c.status == "completed"
        and (c.conclusion or "") in (_PASSING_CONCLUSIONS | _NEUTRAL_CONCLUSIONS)
    )

    # 2. RED — a failure dominates everything below it.
    if failing:
        return CiVerdict(
            verdict=Ci.RED,
            reason=(
                f"{len(failing)} check(s) failed at {ev.sha[:12]}: "
                f"{', '.join(failing[:5])}" + (" …" if len(failing) > 5 else "")
            ),
            evidence=ev,
            failing=failing,
            pending=pending,
            passing=passing,
        )

    # 3. PENDING — no failure, but something hasn't finished. Not green yet.
    if pending:
        return CiVerdict(
            verdict=Ci.PENDING,
            reason=(
                f"{len(pending)} check(s) still running at {ev.sha[:12]} "
                f"({len(passing)} passed, 0 failed so far): "
                f"{', '.join(pending[:5])}" + (" …" if len(pending) > 5 else "")
            ),
            evidence=ev,
            failing=failing,
            pending=pending,
            passing=passing,
        )

    # 4. GREEN — every gating check finished and none failed.
    return CiVerdict(
        verdict=Ci.GREEN,
        reason=(
            f"all {len(passing)} gating check(s) green at {ev.sha[:12]}"
            + (f" in {ev.repo}" if ev.repo else "")
        ),
        evidence=ev,
        failing=failing,
        pending=pending,
        passing=passing,
    )


# ---------------------------------------------------------------------------
# The boundary reader — the ONLY I/O path (mirrors dos.git_delta).
# ---------------------------------------------------------------------------


def _run_gh(args: list[str]) -> tuple[Optional[str], str]:
    """Run `gh <args>` and return (stdout, "") on success, (None, error-class) else.

    The single guarded provider seam, the `llm_judge._call_provider` discipline:
    NEVER raises. Every failure mode — `gh` not installed, not authenticated, a
    non-zero exit (unknown SHA / no access), a network timeout — returns
    `(None, <short reason>)` so `gather()` degrades to an unreachable evidence
    object. This is the one place the GitHub CLI is touched.
    """
    try:
        p = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_TIMEOUT_S,
        )
    except FileNotFoundError:
        return None, "gh CLI not installed"
    except subprocess.TimeoutExpired:
        return None, f"gh timed out after {_GH_TIMEOUT_S}s"
    except OSError as e:  # pragma: no cover - environment-dependent
        return None, f"gh failed to start ({e.__class__.__name__})"
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        tail = err[-1] if err else f"exit {p.returncode}"
        # The two most common, most actionable failures get a clean label.
        low = " ".join(err).lower()
        if "not logged" in low or "authentication" in low or "gh auth login" in low:
            return None, "gh not authenticated (run `gh auth login`)"
        if "not found" in low or "404" in low:
            return None, "commit/repo not found (or no access)"
        return None, f"gh error: {tail[:120]}"
    return p.stdout, ""


def _parse_check_runs(raw: str) -> tuple[CheckRun, ...]:
    """Parse `gh api .../check-runs` JSON into normalized `CheckRun`s.

    Tolerant: malformed JSON or an unexpected shape yields `()` (the caller then
    reports NO_SIGNAL "no checks"), never a raise — the `git_delta` parse-defensively
    stance. The GitHub shape is `{"check_runs": [{"name", "status", "conclusion"}, …]}`.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    runs = data.get("check_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return ()
    out: list[CheckRun] = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        out.append(
            CheckRun(
                name=name,
                status=str(r.get("status") or "").strip(),
                conclusion=(str(r["conclusion"]).strip() if r.get("conclusion") else None),
            )
        )
    return tuple(out)


def gather(sha: str, *, repo: str = DEFAULT_REPO) -> CiEvidence:
    """Read the CI check-runs for `sha` in `repo` via `gh api`. Boundary I/O.

    The subprocess lives HERE; the returned `CiEvidence` is pure data the
    `classify()` verdict consumes (the `git_delta`/arbiter discipline). Defaults
    `repo` to this project's own remote so the substrate can adjudicate its OWN
    pipeline (`python -m dos.drivers.ci_status <sha>`).

    Never raises: an unreachable provider returns `CiEvidence(reachable=False,
    detail=<why>)`, which `classify()` maps to NO_SIGNAL. An empty but reachable
    read (`gh` worked, the commit has no checks) returns `reachable=True, checks=()`,
    which `classify()` also maps to NO_SIGNAL but with the honest "no CI here" reason.
    """
    if not sha:
        return CiEvidence(sha="", repo=repo, reachable=False, detail="no commit SHA given")
    stdout, err = _run_gh(["api", f"repos/{repo}/commits/{sha}/check-runs"])
    if stdout is None:
        return CiEvidence(sha=sha, repo=repo, reachable=False, detail=err)
    checks = _parse_check_runs(stdout)
    return CiEvidence(sha=sha, repo=repo, checks=checks, reachable=True)


def status_of(sha: str, *, repo: str = DEFAULT_REPO, policy: CiPolicy = DEFAULT_POLICY) -> CiVerdict:
    """Convenience: gather + classify in one call (the wired-host entry point).

    The natural call for a `/release` gate or a `CiPredicate` — gather the evidence
    at the boundary, classify it purely, return the typed verdict. Kept thin so the
    two halves (the reader, the verdict) stay independently testable.
    """
    return classify(gather(sha, repo=repo), policy)


# ---------------------------------------------------------------------------
# The EvidenceSource face — the `dos.evidence_sources` entry-point occupant.
# (docs/265 §4. The native verdict is `CiVerdict` with its four-way GREEN/RED/
# PENDING/NO_SIGNAL fidelity; the resolver/`active_evidence_sources` apparatus
# needs an `EvidenceSource` — name + accountability + gather(subject, config) —
# so this thin adapter maps the verdict onto the witness vocabulary the seam
# shares with `os_acceptance`/`paste_log`. `cmd_verify` calls `status_of` DIRECTLY
# for the richer four-way mapping (PENDING ≠ NO_SIGNAL there); this face is what a
# generic `evidence.gather_evidence` / `dos doctor` discovery consumes.)
# ---------------------------------------------------------------------------


class CiStatusSource:
    """An `evidence.EvidenceSource` over the CI/Checks oracle. `THIRD_PARTY`-tagged.

    The `subject` IS the commit SHA — "witness that the build is green at this
    commit" becomes "read the provider's check-runs for this SHA." `gather` runs
    `status_of(subject)` at the boundary (the one provider call lives in
    `_run_gh`, inside `gather`) and maps the typed CI verdict to `EvidenceFacts`:

      * GREEN              → **ATTESTED**  (every gating check concluded success —
                            a third-party record the agent cannot author)
      * RED                → **REFUTED**   (≥1 required check failed: a positive
                            disconfirmation, stronger than "no signal")
      * PENDING / NO_SIGNAL → **NO_SIGNAL** (not answerable yet, or unwired/unreachable
                            — abstain, never a fabricated GREEN; the fail-safe floor)

    `accountability` is CLASS-LEVEL and fixed `THIRD_PARTY`: a CI conclusion is
    mutable state on infrastructure the agent does not control (`ci_status`'s module
    docstring argues exactly why this is more accountable than a commit subject the
    agent typed). So a GREEN attestation IS eligible to grant belief under
    `believe_under_floor` — but only as the conjunctive upgrade docs/265 §1 fixes
    (`verify` never promotes a false git verdict on the strength of CI alone). Never
    raises — `gather_evidence` wraps it fail-safe, and `status_of` degrades every
    provider failure to NO_SIGNAL on its own. `config` is accepted for Protocol
    conformance; a richer source could read `[ci] repo`/`required` out of it (today
    the CLI/`cmd_verify` passes those at the boundary).
    """

    name = "ci_status"
    accountability = Accountability.THIRD_PARTY

    def __init__(self, *, repo: str = DEFAULT_REPO, policy: CiPolicy = DEFAULT_POLICY) -> None:
        self._repo = repo
        self._policy = policy

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        sha = (subject or "").strip()
        if not sha:
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail="no commit SHA given — nothing to read CI for",
            )
        verdict = status_of(sha, repo=self._repo, policy=self._policy)
        if verdict.verdict is Ci.GREEN:
            return EvidenceFacts.attest(
                self.name, self.accountability, sha, detail=verdict.reason)
        if verdict.verdict is Ci.RED:
            return EvidenceFacts.refute(
                self.name, self.accountability, sha, detail=verdict.reason)
        # PENDING / NO_SIGNAL — not answerable yet, or unwired/unreachable. Abstain
        # (the honest floor); never a fabricated attestation or refutation.
        return EvidenceFacts.no_signal(
            self.name, self.accountability, sha, detail=verdict.reason)


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.ci_status <sha>` adjudicates a pipeline run.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.ci_status",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("sha", nargs="?", default="HEAD",
                    help="commit SHA to read CI for (default: HEAD, resolved against the workspace)")
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help=f"owner/name to read checks from (default: {DEFAULT_REPO})")
    ap.add_argument("--workspace", default=None,
                    help="workspace root, used only to resolve HEAD (default: $DISPATCH_WORKSPACE or cwd)")
    ap.add_argument("--required", default="",
                    help="comma-separated required check names; only these gate (default: all gate)")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    # Resolve HEAD against the served workspace so `<sha>` may be a ref. Boundary I/O,
    # kept here in the CLI, never in the verdict. Degrades to the literal arg on any
    # failure (the verdict will then report NO_SIGNAL if it isn't a real SHA).
    sha = args.sha
    if sha == "HEAD" or not all(c in "0123456789abcdefABCDEF" for c in sha):
        cfg = _config.default_config(args.workspace)
        try:
            r = subprocess.run(
                ["git", "rev-parse", sha],
                cwd=str(cfg.paths.root), capture_output=True, text=True,
                check=False, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                sha = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

    policy = CiPolicy(
        required_checks=frozenset(
            s.strip() for s in args.required.split(",") if s.strip()
        )
    )
    verdict = status_of(sha, repo=args.repo, policy=policy)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, default=str))
    else:
        print(f"SHA       {verdict.evidence.sha[:12] or '(none)'}")
        print(f"REPO      {verdict.evidence.repo}")
        print(f"VERDICT   {verdict.verdict.value}")
        print(f"WHY       {verdict.reason}")
        if verdict.failing:
            print("FAILING   " + ", ".join(verdict.failing))
        if verdict.pending:
            print("PENDING   " + ", ".join(verdict.pending))
        if verdict.passing:
            print("PASSING   " + ", ".join(verdict.passing))

    # Exit-code map mirrors `dos verify` (SHIPPED=0/NOT=1): GREEN=0, everything that
    # is not a clean green is non-zero, so a CI gate can `&&` on it. RED=1 (failure),
    # PENDING=2 (not yet), NO_SIGNAL=3 (could not tell — a human's call).
    return {
        Ci.GREEN: 0, Ci.RED: 1, Ci.PENDING: 2, Ci.NO_SIGNAL: 3,
    }[verdict.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
