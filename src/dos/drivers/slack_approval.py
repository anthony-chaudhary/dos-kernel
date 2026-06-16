"""dos.drivers.slack_approval — the Slack approval-envelope oracle (a driver, move-B).

docs/93 §3 ranks the live, non-git sources on the accountability spectrum and names
the Slack approval-envelope as the natural fossil for the ONE claim git can never
leave: **"an accountable human approved this."** It is the next driver after
`ci_status` on that ranking (CI #1, infra logs #2, Slack approval #3), built here on
the SAME move-B template — a boundary reader that pulls a third-party record, a pure
classifier that renders a typed verdict.

The envelope, never the content (the load-bearing distinction, docs/93 §3)
==========================================================================
"Agent posts 'deployed to prod'" is a self-report with a webhook — drop it. The
trustworthy fossil is the **envelope**: a message (or a reaction) was posted by user
U at time T in a channel C, attested by Slack's API, NOT by the agent. The agent
under adjudication cannot impersonate U or forge Slack's record of who-reacted-when
on a workspace it does not administer. So this oracle adjudicates exactly one
claim — *an accountable human approved this* — and NEVER "the thing the message
describes is true." The content of the message is never read into the verdict; only
WHO approved and WHEN.

Where it sits on the docs/84 §4 rung-ladder — `THIRD_PARTY`, the top band, same as
`ci_status`: the approval record is mutable state on infrastructure the agent does
not control. It is only as honest as the workspace's own controls (an agent that
ADMINISTERS the Slack workspace could post as another user / fake a reaction), which
is exactly why it stays a **driver the host wires**, not a kernel guarantee — the
kernel ships the socket; the host decides how accountable the approval venue is.

The claim model (domain-free over the envelope)
===============================================
A "subject" is the thing awaiting sign-off — a release tag, a change id, a phase.
The host supplies a set of **approver identities that count** (`required_approvers`,
the mechanical analogue of a CODEOWNERS / branch-protection reviewer set) and the
boundary reader returns the approval EVENTS it found for that subject: each an
`(approver, ts)` pair Slack authored. The verdict:

    APPROVED       — >= `min_approvals` distinct REQUIRED approvers signed off
    INSUFFICIENT   — some approvals, but fewer than required (or by non-required
                     users only) — a positive "not enough sign-off", distinct from
                     "nobody looked"
    NO_APPROVAL    — the venue was reachable and there is no approval on record
    NO_SIGNAL      — the venue was unwired/unreachable (no token, network, bad JSON)
                     — abstain, never a fabricated APPROVED (fail-safe, never -open)

The shape is the kernel's own (lifted field-for-field from `ci_status`):
  * the **boundary reader** `gather()` mirrors `ci_status.gather` / `git_delta`: the
    one provider call (a Slack Web API GET via `curl`, or a host-injected reader)
    happens HERE, and every failure mode (no token, network/timeout, non-2xx,
    malformed JSON) degrades to an honest unreachable evidence object — never a
    crash, never a propagated exception.
  * the **pure classifier** `classify(SlackEvidence, SlackPolicy) -> SlackVerdict`
    is in the `dos.verdict` ABI: a closed-enum verdict, frozen caller-gathered
    evidence, a frozen `dos.toml [slack_approval]`-shaped policy, an operator-facing
    `reason` naming the approvers, and a `to_dict()`. No I/O inside, so the whole
    verdict is replay-testable on frozen fixtures.

And it obeys the three judge-driver disciplines (docs/87), the `ci_status` fences:
  * **Advisory.** It reports a verdict; it never refuses a lease or mutates state.
  * **Fail-safe, never fail-open.** An unreachable venue is NO_SIGNAL (ask a human),
    never a fabricated APPROVED.
  * **One-way import.** It imports the kernel; the kernel never imports it
    (`drivers/__init__` rule; pinned by `test_kernel_does_not_import_this_driver`).
"""

from __future__ import annotations

import argparse
import enum
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

# Imports the kernel — never the reverse (the driver rule). `config` for the CLI's
# workspace seam; the evidence vocabulary for the `EvidenceSource` face.
from dos import config as _config
from dos.evidence import Accountability, EvidenceFacts

# Cap the network call so a hung API can't stall an evidence-gather — the
# `ci_status._GH_TIMEOUT_S` discipline.
_SLACK_TIMEOUT_S = 20
# The env var a host sets so the substrate can read its OWN approval channel; absent
# ⇒ the venue is "unwired" and every verdict is the honest NO_SIGNAL floor.
_TOKEN_ENV = "DOS_SLACK_TOKEN"


class Slack(str, enum.Enum):
    """The typed approval verdict — four states, mutually exclusive.

    `str`-valued so it round-trips through a CLI token / exit-code map (mirrors
    `ci_status.Ci`). The four-way split is the honest part: a binary
    approved/not-approved would have to lie about the two cases with no answer —
    not-enough-sign-off (INSUFFICIENT) and unwired/unreachable (NO_SIGNAL).
    """

    APPROVED = "APPROVED"          # >= min_approvals distinct required approvers signed off
    INSUFFICIENT = "INSUFFICIENT"  # some approvals, but fewer than required (or non-required only)
    NO_APPROVAL = "NO_APPROVAL"    # venue reachable, zero approvals on record
    NO_SIGNAL = "NO_SIGNAL"        # venue unwired/unreachable — ask a human

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class Approval:
    """One approval EVENT, normalized from Slack's record (the unforgeable bit).

    `approver` is the Slack user id/handle Slack attributes the event to; `ts` is
    Slack's timestamp. The agent under adjudication cannot author these for a
    workspace it does not administer — they are written by Slack. That is the
    gate-2 (unforgeable) property the oracle stands on. The message TEXT is
    deliberately NOT a field: only the envelope (who/when) is trustworthy.
    """

    approver: str
    ts: str = ""


@dataclass(frozen=True)
class SlackPolicy:
    """The knobs that separate APPROVED/INSUFFICIENT — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as `CiPolicy`. The
    defaults are GENERIC; a workspace declares its own in `dos.toml [slack_approval]`
    read back through `SubstrateConfig`.

      required_approvers — when non-empty, ONLY approvals by a user in this set
                           count (the CODEOWNERS / required-reviewer analogue). Empty
                           (default) = ANY distinct approver counts — the no-config
                           floor, still envelope-grounded (Slack authored the event).
      min_approvals      — how many distinct counting approvers are needed for
                           APPROVED. Default 1 (a single accountable sign-off).
    """

    required_approvers: frozenset[str] = field(default_factory=frozenset)
    min_approvals: int = 1

    def __post_init__(self) -> None:
        if self.min_approvals < 1:
            raise ValueError("min_approvals must be >= 1")


DEFAULT_POLICY = SlackPolicy()


@dataclass(frozen=True)
class SlackEvidence:
    """Everything `classify()` needs, gathered by the CALLER before the call.

    No network inside the verdict — the `ci_status`/`git_delta` rule. `gather()`
    runs the Slack API read and normalizes the response into this frozen object.

      subject     — the thing awaiting sign-off (echoed for the operator surface).
      channel     — the channel the approvals were read from (provenance).
      approvals   — the normalized approval events. EMPTY is the load-bearing
                    ambiguity: "reachable, nobody approved" vs "could not read" — the
                    two are distinguished by `reachable`.
      reachable   — False when the provider call itself failed (no token, network,
                    bad JSON). With `reachable=False` the verdict is always NO_SIGNAL.
      detail      — a one-line note carried into the verdict `reason`.
    """

    subject: str
    channel: str = ""
    approvals: tuple[Approval, ...] = ()
    reachable: bool = True
    detail: str = ""


@dataclass(frozen=True)
class SlackVerdict:
    """The single verdict `classify()` returns, with the evidence echoed back.

    Conforms structurally to `dos.verdict.TypedVerdict` (a `str`-enum `verdict`, a
    `str` `reason`, a JSON-shaped `to_dict()`), like `CiVerdict` — but stays a driver
    oracle (host-wired), not a `dos <verb>` (it fails gate 3, domain-free).
    """

    verdict: Slack
    reason: str
    evidence: SlackEvidence
    approvers: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        ev = self.evidence
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "approvers": list(self.approvers),
            "evidence": {
                "subject": ev.subject,
                "channel": ev.channel,
                "reachable": ev.reachable,
                "detail": ev.detail,
                "approvals": [{"approver": a.approver, "ts": a.ts} for a in ev.approvals],
            },
        }


def classify(ev: SlackEvidence, policy: SlackPolicy = DEFAULT_POLICY) -> SlackVerdict:
    """Classify one subject's approval status from already-gathered evidence. PURE.

    Reads the ladder top to bottom:

      1. NO_SIGNAL    — the venue was unreachable. We observed nothing → route to a
                        human. Checked FIRST so a failed read can never read as a
                        verdict (fail-safe).
      2. count distinct COUNTING approvers (in required_approvers when set, else any).
      3. APPROVED     — >= min_approvals distinct counting approvers.
      4. INSUFFICIENT — at least one approval exists, but not enough counting ones.
      5. NO_APPROVAL  — reachable, zero approvals on record.
    """
    # 1. NO_SIGNAL (unreachable) — fail-safe; an unwired venue never fabricates.
    if not ev.reachable:
        return SlackVerdict(
            verdict=Slack.NO_SIGNAL,
            reason=(
                f"no approval signal for {ev.subject or '(no subject)'}"
                + (f" in {ev.channel}" if ev.channel else "")
                + (f" — {ev.detail}" if ev.detail else " — approval venue unreachable")
            ),
            evidence=ev,
        )

    # 2. The distinct counting approvers — a SET so two reactions by one user count
    #    once (the envelope is who, not how-many-times).
    if policy.required_approvers:
        counting = {a.approver for a in ev.approvals if a.approver in policy.required_approvers}
    else:
        counting = {a.approver for a in ev.approvals if a.approver}
    approvers = tuple(sorted(counting))

    # 5. NO_APPROVAL — reachable, nothing on record at all.
    if not ev.approvals:
        return SlackVerdict(
            verdict=Slack.NO_APPROVAL,
            reason=(
                f"no approval on record for {ev.subject}"
                + (f" in {ev.channel}" if ev.channel else "")
            ),
            evidence=ev,
        )

    # 3. APPROVED — enough distinct counting approvers signed off.
    if len(counting) >= policy.min_approvals:
        return SlackVerdict(
            verdict=Slack.APPROVED,
            reason=(
                f"{len(counting)} accountable approver(s) signed off on {ev.subject}: "
                f"{', '.join(approvers[:5])}" + (" …" if len(approvers) > 5 else "")
            ),
            evidence=ev,
            approvers=approvers,
        )

    # 4. INSUFFICIENT — approvals exist, but not enough that count.
    need = policy.min_approvals
    if policy.required_approvers and not counting:
        why = (
            f"{len(ev.approvals)} approval(s) on {ev.subject}, but none by a required "
            f"approver {sorted(policy.required_approvers)}"
        )
    else:
        why = (
            f"{len(counting)}/{need} required approval(s) for {ev.subject}: "
            f"{', '.join(approvers) or '(none counting)'}"
        )
    return SlackVerdict(
        verdict=Slack.INSUFFICIENT,
        reason=why,
        evidence=ev,
        approvers=approvers,
    )


# ---------------------------------------------------------------------------
# The boundary reader — the ONLY I/O path (mirrors ci_status.gather).
# ---------------------------------------------------------------------------


def _run_slack(method: str, params: dict[str, str], token: str) -> tuple[Optional[str], str]:
    """GET the Slack Web API `method` and return (stdout, "") on success, else
    (None, error-class). The single guarded provider seam — NEVER raises. Every
    failure (no `curl`, network timeout, non-2xx, missing token) returns
    `(None, <short reason>)` so `gather()` degrades to unreachable. The one place
    the Slack API is touched."""
    if not token:
        return None, f"no Slack token ({_TOKEN_ENV} unset) — approval venue unwired"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://slack.com/api/{method}" + (f"?{query}" if query else "")
    try:
        p = subprocess.run(
            ["curl", "-sS", "--max-time", str(_SLACK_TIMEOUT_S),
             "-H", f"Authorization: Bearer {token}", url],
            capture_output=True,
            text=True,
            check=False,
            timeout=_SLACK_TIMEOUT_S + 5,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except FileNotFoundError:
        return None, "curl not installed"
    except subprocess.TimeoutExpired:
        return None, f"slack API timed out after {_SLACK_TIMEOUT_S}s"
    except OSError as e:  # pragma: no cover - environment-dependent
        return None, f"curl failed to start ({e.__class__.__name__})"
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        tail = err[-1] if err else f"exit {p.returncode}"
        return None, f"slack read failed: {tail[:120]}"
    return p.stdout, ""


def _parse_approvals(raw: str, *, approve_marker: str = "") -> tuple[Optional[tuple[Approval, ...]], str]:
    """Parse a Slack `conversations.history`/`reactions` JSON into approval events.

    Returns (approvals, "") on a well-formed read — even when EMPTY (a reachable
    "nobody approved"). Returns (None, error) only when the response says the read
    itself failed (`ok: false`) or the JSON is malformed — those are unreachable.
    Tolerant: an unexpected shape with `ok: true` yields an empty tuple, not a crash.

    The envelope grammar (content-free): an approval is a message/reaction whose
    AUTHOR Slack attributes (`user`) and timestamp (`ts`) we record. When
    `approve_marker` is set, only messages whose text contains it count as an
    approval gesture — but the marker gates WHICH envelope is an approval; the
    trusted bytes remain who+when, never the free text.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, "malformed JSON from slack"
    if not isinstance(data, dict):
        return None, "unexpected slack response shape"
    if data.get("ok") is not True:
        why = str(data.get("error") or "unknown")[:80]
        return None, f"slack ok=false: {why}"
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return (), ""
    out: list[Approval] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        user = str(m.get("user") or "").strip()
        if not user:
            continue
        if approve_marker:
            text = str(m.get("text") or "")
            if approve_marker not in text:
                continue
        out.append(Approval(approver=user, ts=str(m.get("ts") or "").strip()))
    return tuple(out), ""


def gather(
    subject: str,
    *,
    channel: str = "",
    token: Optional[str] = None,
    approve_marker: str = "",
    _reader=None,
) -> SlackEvidence:
    """Read the approval events for `subject` in `channel` via the Slack API.

    Boundary I/O — the subprocess lives HERE; the returned `SlackEvidence` is pure
    data `classify()` consumes (the `ci_status`/`git_delta` discipline). Never
    raises: an unreachable venue returns `SlackEvidence(reachable=False,
    detail=<why>)`, which `classify()` maps to NO_SIGNAL.

    `_reader` is a host-injected boundary reader `(subject, channel) -> raw json str`
    — the same seam `state_diff` uses (`read_state`) so a host can wire a real Slack
    client, a mock, or a recorded transport without this module importing an SDK.
    When absent, the default `curl` path reads `conversations.history` for `channel`.
    """
    subject = (subject or "").strip()
    if not subject:
        return SlackEvidence(subject="", channel=channel, reachable=False,
                             detail="no subject given — nothing to read approvals for")
    if not channel:
        return SlackEvidence(subject=subject, channel="", reachable=False,
                             detail="no channel given — approval venue unwired")

    if _reader is not None:
        try:
            raw = _reader(subject, channel)
        except Exception as e:  # the host reader is untrusted — fail-safe
            return SlackEvidence(subject=subject, channel=channel, reachable=False,
                                 detail=f"host reader failed ({e.__class__.__name__})")
    else:
        tok = token if token is not None else os.environ.get(_TOKEN_ENV, "")
        raw, err = _run_slack("conversations.history", {"channel": channel, "limit": "200"}, tok)
        if raw is None:
            return SlackEvidence(subject=subject, channel=channel, reachable=False, detail=err)

    approvals, perr = _parse_approvals(raw, approve_marker=approve_marker)
    if approvals is None:
        return SlackEvidence(subject=subject, channel=channel, reachable=False, detail=perr)
    return SlackEvidence(subject=subject, channel=channel, approvals=approvals, reachable=True)


def approval_of(
    subject: str,
    *,
    channel: str = "",
    token: Optional[str] = None,
    approve_marker: str = "",
    policy: SlackPolicy = DEFAULT_POLICY,
    _reader=None,
) -> SlackVerdict:
    """Convenience: gather + classify in one call (the wired-host entry point)."""
    return classify(
        gather(subject, channel=channel, token=token, approve_marker=approve_marker, _reader=_reader),
        policy,
    )


# ---------------------------------------------------------------------------
# The EvidenceSource face — the `dos.evidence_sources` entry-point occupant.
# ---------------------------------------------------------------------------


class SlackApprovalSource:
    """An `evidence.EvidenceSource` over the Slack approval-envelope oracle.

    `THIRD_PARTY`-tagged: the approval record is mutable state on infrastructure the
    agent does not control. The `subject` IS the thing awaiting sign-off. `gather`
    runs `approval_of(subject)` at the boundary and maps the typed verdict to
    `EvidenceFacts`:

      * APPROVED                  → **ATTESTED**  (an accountable human signed off —
                                    a Slack-authored record the agent cannot forge)
      * NO_APPROVAL / INSUFFICIENT → **REFUTED**  (a positive "not approved (enough)",
                                    stronger than "no signal")
      * NO_SIGNAL                 → **NO_SIGNAL** (unwired/unreachable — abstain,
                                    never a fabricated APPROVED; the fail-safe floor)

    `accountability` is CLASS-LEVEL `THIRD_PARTY`, so an APPROVED attestation IS
    eligible to grant belief under `believe_under_floor`. The channel comes from
    `dos.toml [slack_approval] channel` via `config`; absent ⇒ the venue is unwired
    and every verdict is NO_SIGNAL (the honest floor). Never raises —
    `gather_evidence` wraps it fail-safe and `approval_of` degrades every failure to
    NO_SIGNAL on its own.
    """

    name = "slack_approval"
    accountability = Accountability.THIRD_PARTY

    def __init__(self, *, channel: str = "", policy: SlackPolicy = DEFAULT_POLICY) -> None:
        self._channel = channel
        self._policy = policy

    def _resolve_channel(self, config: object) -> str:
        if self._channel:
            return self._channel
        # Best-effort read of [slack_approval] channel off the substrate config; any
        # shape miss degrades to "" (⇒ unwired ⇒ NO_SIGNAL), never a crash.
        try:
            raw = getattr(config, "raw", None) or {}
            sec = raw.get("slack_approval") if isinstance(raw, dict) else None
            if isinstance(sec, dict):
                return str(sec.get("channel") or "")
        except Exception:
            pass
        return ""

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        subj = (subject or "").strip()
        if not subj:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject,
                detail="no subject given — nothing to read approvals for")
        channel = self._resolve_channel(config)
        verdict = approval_of(subj, channel=channel, policy=self._policy)
        if verdict.verdict is Slack.APPROVED:
            return EvidenceFacts.attest(
                self.name, self.accountability, subj, detail=verdict.reason)
        if verdict.verdict in (Slack.NO_APPROVAL, Slack.INSUFFICIENT):
            return EvidenceFacts.refute(
                self.name, self.accountability, subj, detail=verdict.reason)
        return EvidenceFacts.no_signal(
            self.name, self.accountability, subj, detail=verdict.reason)


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.slack_approval <subject> --channel C` (+ a mock seam).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.slack_approval",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("subject", nargs="?", default="",
                    help="the thing awaiting sign-off (a release tag, change id, phase)")
    ap.add_argument("--channel", default="", help="Slack channel id to read approvals from")
    ap.add_argument("--required", default="",
                    help="comma-separated required approver ids; only these count (default: any)")
    ap.add_argument("--min", type=int, default=1, help="distinct required approvals needed (default: 1)")
    ap.add_argument("--marker", default="",
                    help="text marker a message must contain to count as an approval gesture")
    ap.add_argument("--workspace", default=None, help="workspace root (default: $DISPATCH_WORKSPACE or cwd)")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    # Resolve a channel from config if not passed (the wired-host convenience).
    channel = args.channel
    if not channel:
        cfg = _config.default_config(args.workspace)
        src = SlackApprovalSource()
        channel = src._resolve_channel(cfg)

    policy = SlackPolicy(
        required_approvers=frozenset(s.strip() for s in args.required.split(",") if s.strip()),
        min_approvals=max(1, args.min),
    )
    verdict = approval_of(args.subject, channel=channel, approve_marker=args.marker, policy=policy)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, default=str))
    else:
        print(f"SUBJECT   {verdict.evidence.subject or '(none)'}")
        print(f"CHANNEL   {verdict.evidence.channel or '(none)'}")
        print(f"VERDICT   {verdict.verdict.value}")
        print(f"WHY       {verdict.reason}")
        if verdict.approvers:
            print("APPROVERS " + ", ".join(verdict.approvers))

    # Exit-code map mirrors `ci_status`: APPROVED=0, INSUFFICIENT=2, NO_APPROVAL=1,
    # NO_SIGNAL=3 — a gate can `&&` on it.
    return {
        Slack.APPROVED: 0, Slack.NO_APPROVAL: 1, Slack.INSUFFICIENT: 2, Slack.NO_SIGNAL: 3,
    }[verdict.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
