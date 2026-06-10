"""dos.drivers.os_acceptance — the acceptance-verb witness (the OS is the byte-author).

docs/121 §3.1 #4 and docs/117 §5 name the *acceptance verb* as the cheapest
non-forgeable witness a deployment can have: **the kernel runs a command and reads
the OS exit code.** It is the first concrete `dos.evidence_sources` backend — the
population proof that turns the `dos.evidence` seam from one built-in (`null`) into a
witness that can actually move `verify` toward belief.

Why the exit code is evidence and a pasted "it passed" is not
=============================================================

The whole `dos.evidence` thesis is "a witness is only evidence when the byte-author
is not the judged agent." When an agent runs a test and *tells you* it passed, the
agent authored every byte that reached you — the docs/103 self-report, the forgeable
floor (`AGENT_AUTHORED`). But when the **kernel** launches the process and the
**operating system** records the exit status, the agent under adjudication cannot
forge a `returncode == 0`: it did not run the process and does not author the OS's
record of how it ended. So this source is tagged `OS_RECORDED` — the non-forgeable
rung — and its ATTESTED facts are eligible to grant belief under
`evidence.believe_under_floor` (a pasted-receipt source, `AGENT_AUTHORED`, never is).

The mapping from exit status to stance is the honest, conservative one:

  * exit 0                     → **ATTESTED**  (the effect's acceptance check passed)
  * exit non-zero (clean run)  → **REFUTED**   (the check ran and said no — a
                                positive disconfirmation, stronger than "no signal")
  * could not run the command  → **NO_SIGNAL** (binary missing, timeout, OS error,
                                no command given — abstain, never a fabricated pass)

The same fail-safe-never-fail-open posture as `ci_status._run_gh`: every failure mode
degrades to an unreachable `no_signal`, never a raise, never an ATTESTED.

Shape & layering
================

A driver, outside the kernel boundary — it has the surface the kernel forbids
(spawning a process). It implements the `evidence.EvidenceSource` Protocol:
`name`/`accountability` class attributes + a boundary `gather(subject, config)` whose
ONE subprocess lives here, mirroring `ci_status.gather` / `git_delta`. It imports the
kernel; the kernel never imports it (the `drivers/__init__` rule, the existing
`no dos.drivers import` litmus covers it). Advisory: it reports an attestation; it
never refuses a lease or mutates state — a host CONSULTS it (a `dos verify` belief
fold, a RED row in `dos decisions`), it does not actuate.

The `subject` IS the command
============================

For this source the opaque `subject` correlation handle is *the acceptance command
itself* — the shell-free argv to run (passed as a single string, split with
`shlex`). "Witness that effect E happened" becomes "run the command that checks E and
read its exit code." The command is the host's to choose (`pytest -q`, `curl -fsS
https://… -o /dev/null` for an HTTP-200 re-GET of an idempotent effect, a
provider-CLI status probe); the kernel only runs it and reads the OS's verdict. A
host wires which command witnesses which effect; this driver supplies the
runs-it-and-reads-the-exit-code mechanism.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess

# Imports the kernel — never the other way round (the driver rule).
from dos import config as _config
from dos.evidence import Accountability, EvidenceFacts, believe_under_floor

# Cap the run so a hung acceptance command can't stall an evidence-gather — the
# `ci_status._GH_TIMEOUT_S` / `git_delta._GIT_TIMEOUT_S` discipline. A touch generous
# because an acceptance check (a test run, an HTTP probe) can legitimately take a
# little while; a host that needs a different cap passes `timeout_s`.
_DEFAULT_TIMEOUT_S = 120


class OsAcceptanceEvidenceSource:
    """An `evidence.EvidenceSource`: run a command, read the OS exit code, witness it.

    `name`/`accountability` are CLASS-LEVEL and fixed — this source is always
    `OS_RECORDED` (it has no honest path to a higher or lower rung; the exit code is
    the OS's record, full stop). The `subject` handed to `gather` is the acceptance
    command to run (see the module docstring). `config` is accepted for Protocol
    conformance and is unused here (the command is self-contained); a richer source
    could read a per-effect command map out of `dos.toml [evidence]` via `config`.
    """

    name = "os_acceptance"
    accountability = Accountability.OS_RECORDED

    def __init__(self, *, timeout_s: int = _DEFAULT_TIMEOUT_S, cwd: str | None = None) -> None:
        self._timeout_s = timeout_s
        self._cwd = cwd

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Run `subject` as a command and map its exit status to an EvidenceFacts.

        Boundary I/O — the ONE subprocess lives here (the `ci_status.gather` rule);
        the returned facts are pure data `believe_under_floor` consumes. Never raises:
        every failure mode degrades to an unreachable `no_signal`, so a missing binary
        / timeout / OS error can never be mistaken for either an attestation or a
        refutation. Wrapped by `evidence.gather_evidence` at the call site for the
        belt-and-braces fail-safe, but defensive here too (a driver should not lean on
        its wrapper to be safe).
        """
        cmd = (subject or "").strip()
        if not cmd:
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail="no acceptance command given — nothing to witness",
            )
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as e:  # unbalanced quotes etc. — not runnable, so no signal
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail=f"un-parseable acceptance command ({e}) — no signal",
            )
        if not argv:
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail="empty acceptance command after parsing — no signal",
            )
        try:
            p = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_s,
                cwd=self._cwd,
                # docs/295 — never leak the caller's stdin into the acceptance
                # command (an acceptance run is a witness, not an interactive
                # session; inheriting a server's transport pipe wedges it).
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail=f"command not found: {argv[0]!r} — no signal",
            )
        except subprocess.TimeoutExpired:
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail=f"acceptance command timed out after {self._timeout_s}s — no signal",
            )
        except OSError as e:  # pragma: no cover - environment-dependent
            return EvidenceFacts.no_signal(
                self.name,
                self.accountability,
                subject,
                detail=f"acceptance command failed to start ({e.__class__.__name__}) — no signal",
            )

        rc = p.returncode
        # The OS authored `rc`. exit 0 → the acceptance check passed (ATTESTED);
        # non-zero from a clean run → the check ran and said no (REFUTED); both are
        # the OS's record, not the agent's narration.
        if rc == 0:
            return EvidenceFacts.attest(
                self.name,
                self.accountability,
                subject,
                detail=f"`{argv[0]}` exited 0 — acceptance check passed",
            )
        return EvidenceFacts.refute(
            self.name,
            self.accountability,
            subject,
            detail=f"`{argv[0]}` exited {rc} — acceptance check failed (OS-recorded)",
        )


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.os_acceptance "<command>"` witnesses an effect.
# Folds the single source through `believe_under_floor` so the operator sees the
# belief verdict, not just the raw stance — i.e. that an OS_RECORDED attestation
# DOES grant belief (whereas a forgeable-floor one would not).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.os_acceptance",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("command", help="the acceptance command to run (its exit code is the witness)")
    ap.add_argument("--workspace", default=None,
                    help="workspace root, used to resolve the run cwd (default: $DISPATCH_WORKSPACE or cwd)")
    ap.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT_S,
                    help=f"seconds before the command is abandoned as NO_SIGNAL (default: {_DEFAULT_TIMEOUT_S})")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    cfg = _config.default_config(args.workspace)
    source = OsAcceptanceEvidenceSource(timeout_s=args.timeout, cwd=str(cfg.paths.root))
    # Use the kernel's fail-safe wrapper, exactly as a real consumer would.
    from dos.evidence import gather_evidence

    facts = gather_evidence(source, args.command, cfg)
    belief = believe_under_floor((facts,))

    if args.json:
        print(json.dumps({"facts": facts.to_dict(), "belief": belief.to_dict()}, indent=2))
    else:
        print(f"COMMAND   {args.command}")
        print(f"SOURCE    {facts.source_name} ({facts.accountability.value})")
        print(f"STANCE    {facts.stance.value}")
        print(f"WHY       {facts.detail}")
        print(f"BELIEVE   {belief.believe}   (refuted={belief.refuted})")
        print(f"VERDICT   {belief.reason}")

    # Exit-code map mirrors `dos verify` / `ci_status`: a believed attestation is 0,
    # a refutation is 1 (the effect did not happen), no-signal is 3 (a human's call).
    if belief.refuted:
        return 1
    if belief.believe:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
