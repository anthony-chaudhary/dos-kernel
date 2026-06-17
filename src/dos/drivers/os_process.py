"""dos.drivers.os_process — the process/port-liveness witness (docs/384).

The "OS stuff" rung of the witness spectrum: an agent's claim "the service I started
is running / the port is accepting connections" witnessed by **the operating system**,
not the agent's narration. The OS authors the process-table entry and the TCP
handshake result; a dead process cannot keep a socket listening, and the agent cannot
make `connect()` complete without something actually accepting on the port. So this
source is `OS_RECORDED` — the non-forgeable rung — and its ATTESTED facts can grant
belief under `evidence.believe_under_floor`.

Why this is distinct from `os_acceptance`
=========================================

`os_acceptance` runs a COMMAND and reads its exit code (a `pgrep`/`ss` invocation would
work, but the witness is then the exit code of a shell the host wired). This source
reads the OS state DIRECTLY — the process table (via the PID-reuse-defended,
foreign-host-blind `dos.proc_delta` probe the liveness verdict already trusts) and the
TCP stack (a stdlib socket connect). It is the clean first-class form of "is it up?",
with a typed REFUTE (confidently gone / connection refused) distinct from "could not
tell".

The subject grammar — what to witness
=====================================

  * `port:<host>:<port>`  — a TCP connect; connected → ATTESTED (something is
        listening), refused → REFUTED (nothing is), timeout/error → NO_SIGNAL.
  * `port:<port>`         — host defaults to 127.0.0.1.
  * `listening:<port>`    — an alias for `port:127.0.0.1:<port>`.
  * `pid:<n>`             — the OS process table via `proc_delta.probe`: alive →
        ATTESTED, confidently gone → REFUTED, cannot-tell → NO_SIGNAL.

The honest caveat (the host's wiring responsibility)
====================================================

As with `os_acceptance`/`http_probe`, the witness is sound only when the port/pid names
a surface the agent does not control — a real deployed service, not a dummy listener
the agent started to fake the signal. The kernel reads the OS's answer; choosing a
surface the agent does not author is the host's job (the `os_acceptance` contract).

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (a socket connect, a process
probe). Stdlib + the kernel's own `proc_delta` (stdlib + ctypes) only — no new
dependency. It implements the `evidence.EvidenceSource` Protocol (class-level
`name`/`accountability`; a boundary `gather` whose ONE probe lives here) so it drops
straight into `gather_evidence`, the belief fold, and `dos witness os_process
<subject>`. It imports the kernel; the kernel never imports it (the `drivers/__init__`
rule). Advisory: it reports a read-back; it takes no lease and mutates nothing.
"""

from __future__ import annotations

import argparse
import json
import socket

# Imports the kernel — never the other way round (the driver rule).
from dos import proc_delta
from dos.evidence import Accountability, EvidenceFacts, believe_under_floor

# Cap a socket connect so a black-holed host (a firewall that drops, not refuses) can't
# stall an evidence-gather — the `os_acceptance._DEFAULT_TIMEOUT_S` discipline. A connect
# is fast; a short cap keeps a hung probe from wedging the gather.
_DEFAULT_TIMEOUT_S = 5


class OsProcessEvidenceSource:
    """An `evidence.EvidenceSource`: witness process/port liveness from the OS directly.

    `name`/`accountability` are CLASS-LEVEL and fixed — this source is always
    `OS_RECORDED` (the OS authored the process-table entry / the TCP handshake result;
    it has no honest path to a higher or lower rung). The `subject` is the probe (see
    the module docstring). `config` is accepted for Protocol conformance and is unused.
    """

    name = "os_process"
    accountability = Accountability.OS_RECORDED

    def __init__(self, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, probe the OS, map the result to an EvidenceFacts.

        Boundary I/O — the ONE probe lives here. Never raises: every failure mode
        degrades to an unreachable `no_signal`, so a malformed subject / probe error can
        never be mistaken for an attestation OR a refutation (the `os_acceptance.gather`
        discipline)."""
        s = (subject or "").strip()
        if not s or ":" not in s:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable subject — expected 'port:<host>:<port>' / "
                    "'port:<port>' / 'listening:<port>' / 'pid:<n>' — nothing to witness"
                ),
            )
        kind, _, rest = s.partition(":")
        kind = kind.lower().strip()
        rest = rest.strip()

        if kind == "pid":
            return self._probe_pid(rest)
        if kind in ("port", "listening"):
            return self._probe_port(kind, rest)
        return EvidenceFacts.no_signal(
            self.name, self.accountability, s,
            detail=f"unknown probe kind {kind!r} — expected port:/listening:/pid: — no signal",
        )

    # -- the process-table rung (reuses the kernel's proc_delta probe) --------------

    def _probe_pid(self, rest: str) -> EvidenceFacts:
        try:
            pid = int(rest)
        except ValueError:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, f"pid:{rest}",
                detail=f"malformed pid {rest!r} — no signal",
            )
        # proc_delta.probe never raises: alive True / False / None (cannot tell). It is
        # the PID-reuse-defended, foreign-host-blind reader the liveness verdict trusts.
        live = proc_delta.probe(pid)
        subj = f"pid:{pid}"
        if live.alive is True:
            return EvidenceFacts.attest(
                self.name, self.accountability, subj,
                detail=f"{live.detail} (OS process table)",
            )
        if live.alive is False:
            return EvidenceFacts.refute(
                self.name, self.accountability, subj,
                detail=f"{live.detail} (OS process table) — the claimed process is gone",
            )
        return EvidenceFacts.no_signal(
            self.name, self.accountability, subj,
            detail=f"{live.detail} — cannot tell",
        )

    # -- the TCP rung (a stdlib socket connect) -------------------------------------

    def _probe_port(self, kind: str, rest: str) -> EvidenceFacts:
        host, port = _parse_hostport(kind, rest)
        if port is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, f"{kind}:{rest}",
                detail=f"malformed {kind} target {rest!r} (need a port number) — no signal",
            )
        subj = f"port:{host}:{port}"
        try:
            with socket.create_connection((host, port), timeout=self._timeout_s):
                pass
        except ConnectionRefusedError:
            # The OS sent an RST — nothing is listening. A positive disconfirmation.
            return EvidenceFacts.refute(
                self.name, self.accountability, subj,
                detail=f"connection to {host}:{port} REFUSED — nothing is listening (OS-recorded)",
            )
        except (socket.timeout, TimeoutError):
            # Dropped (a firewall black-holing) — we genuinely cannot tell whether a
            # service is up behind it, so NO_SIGNAL, never a fabricated refute.
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subj,
                detail=f"connection to {host}:{port} timed out after {self._timeout_s}s — cannot tell",
            )
        except OSError as e:
            # DNS failure, unreachable network, etc. — cannot tell.
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subj,
                detail=f"could not probe {host}:{port} ({e.__class__.__name__}) — cannot tell",
            )
        # The 3-way handshake completed — something is accepting on the port. The OS
        # authored this; the agent cannot complete a connect without a real listener.
        return EvidenceFacts.attest(
            self.name, self.accountability, subj,
            detail=f"connected to {host}:{port} — a process is listening (OS-recorded)",
        )


# ---------------------------------------------------------------------------
# Pure helper (no I/O) — the host:port grammar.
# ---------------------------------------------------------------------------


def _parse_hostport(kind: str, rest: str) -> "tuple[str, int | None]":
    """`port:<host>:<port>` / `port:<port>` / `listening:<port>` → `(host, port|None)`.

    `listening:` is always 127.0.0.1. `port:` with one field is 127.0.0.1; with two,
    the first is the host. A non-numeric / out-of-range port → `(host, None)` so the
    caller degrades to NO_SIGNAL (never a probe of port 0)."""
    if kind == "listening":
        host, raw_port = "127.0.0.1", rest
    elif ":" in rest:
        host, _, raw_port = rest.rpartition(":")
        host = host.strip() or "127.0.0.1"
    else:
        host, raw_port = "127.0.0.1", rest
    raw_port = raw_port.strip()
    try:
        port = int(raw_port)
    except ValueError:
        return host, None
    if not (0 < port < 65536):
        return host, None
    return host, port


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.os_process '<subject>'`
# (also reachable as `dos witness os_process '<subject>'`).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.os_process",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'port:<host>:<port>' / 'port:<port>' / 'listening:<port>' / 'pid:<n>'",
    )
    ap.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_S,
                    help=f"seconds before a connect is abandoned as NO_SIGNAL (default: {_DEFAULT_TIMEOUT_S})")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    source = OsProcessEvidenceSource(timeout_s=args.timeout)
    from dos.evidence import gather_evidence

    facts = gather_evidence(source, args.subject, None)
    belief = believe_under_floor((facts,))

    if args.json:
        print(json.dumps({"facts": facts.to_dict(), "belief": belief.to_dict()}, indent=2))
    else:
        print(f"PROBE     {args.subject}")
        print(f"SOURCE    {facts.source_name} ({facts.accountability.value})")
        print(f"STANCE    {facts.stance.value}   (reachable={facts.reachable})")
        print(f"WHY       {facts.detail}")
        print(f"BELIEVE   {belief.believe}   (refuted={belief.refuted})")

    if belief.refuted:
        return 1
    if belief.believe:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
