"""dos.drivers.http_probe — the live-endpoint read-back witness (docs/381).

`evidence.py`'s own docstring names the witnesses `verify()`'s git rung is blind to:
"an email sent, a webhook delivered, a payment made, a migration run, **a deploy
shipped**." For a deploy the accountable witness is the counterparty that received the
effect — here, **the server that answers the request**. This driver is that witness:
the KERNEL makes an HTTP request and reads the response the server authored.

Why a live HTTP response is a NON-FORGEABLE (THIRD_PARTY) witness
================================================================

The whole `dos.evidence` thesis is "a witness is only evidence when the byte-author is
not the judged agent." When an agent *tells you* "the deploy is live, it returns 200",
the agent authored every byte that reached you — the forgeable floor. But when the
kernel does `GET https://app.example.com/health` and a server the agent does not
control answers `200`, the **server** authored that status line. The agent cannot make
a remote service return a healthy response without the service actually being up — so
this source is tagged `THIRD_PARTY`, the strongest rung, and its ATTESTED facts are
eligible to grant belief under `evidence.believe_under_floor`.

The one honest caveat — the host's wiring responsibility
========================================================

The witness is only sound when the URL points at a surface the agent does NOT control
(a real deployment, a third-party API). If the host wires the probe at a server the
agent itself is running (`http://localhost:port` it just started), then actor==witness
and the THIRD_PARTY tag over-claims. This is exactly the `os_acceptance` contract — the
kernel runs whatever command/URL the HOST chose; choosing a surface the agent doesn't
author is the host's job, not something the kernel can adjudicate. The driver names the
caveat; it does not try to detect it (that is policy).

The subject grammar — `[METHOD ]URL[#<assertion>]`
==================================================

The opaque `subject` IS the probe. Forms:

  * `https://app/health`              — GET; ATTESTED iff the status is 2xx.
  * `GET https://app/health`          — an explicit method (GET/HEAD/POST/…).
  * `https://app/health#status:204`   — ATTESTED iff the status equals 204.
  * `https://app/health#status:2xx`   — ATTESTED iff the status is any 2xx (the default).
  * `https://app/x#contains:ok`       — reached + the body CONTAINS the substring.
  * `https://app/x#sha256:<hex>`      — reached + sha256(body) equals the gold digest.

Stance grammar (the honest, conservative mapping)
=================================================

  * the server responded AND the assertion holds  → **ATTESTED** (the effect is live)
  * the server responded AND the assertion FAILS  → **REFUTED**  (a positive
        disconfirmation — "you said it's up returning 200; it answered 503" — the
        silent-deploy-fail made visible, stronger than "could not tell")
  * could not reach the server (DNS / refused / timeout / TLS / a bad URL) → **NO_SIGNAL**
        (abstain — never a fabricated REFUTE that would falsely fail a healthy deploy
        on a transient network blip)

An HTTP *error* status (4xx/5xx) is a REACHED response — the server answered — so it is
mapped by the assertion (a `status:500` assertion ATTESTS on a 500), never confused with
unreachability.

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (a network request). Stdlib only
(`urllib.request`; no `requests` dependency — the kernel/driver import set stays thin).
It implements the `evidence.EvidenceSource` Protocol (class-level `name`/
`accountability`; a boundary `gather(subject, config)` whose ONE network call lives
here, the `os_acceptance.gather` / `ci_status.gather` rule) so it drops straight into
`gather_evidence`, the belief fold, and `dos witness http_probe <url>`. It imports the
kernel; the kernel never imports it (the `drivers/__init__` rule). Advisory: it reports
a read-back; it takes no lease and mutates nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request

# Imports the kernel — never the other way round (the driver rule).
from dos.evidence import Accountability, EvidenceFacts, believe_under_floor

# Cap the request so a hung endpoint can't stall an evidence-gather — the
# `os_acceptance._DEFAULT_TIMEOUT_S` / `git_delta._GIT_TIMEOUT_S` discipline. A probe
# should be fast; a generous-but-bounded default.
_DEFAULT_TIMEOUT_S = 15

# Cap the body read so a giant/streaming response can't exhaust memory while hashing or
# substring-scanning. 4 MiB is ample for a health-check body / JSON payload.
_MAX_BODY_BYTES = 4 * 1024 * 1024

# The HTTP methods we will issue. A closed set so a malformed subject token cannot turn
# into an arbitrary verb; default GET.
_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


class HttpProbeEvidenceSource:
    """An `evidence.EvidenceSource`: make an HTTP request, witness the server's response.

    `name`/`accountability` are CLASS-LEVEL and fixed — this source is always
    `THIRD_PARTY` (a remote server authored the response; the rung is a property of WHO
    answers, and the honest default is "a surface the agent does not control" — see the
    module docstring's caveat). The `subject` handed to `gather` is the probe
    (`[METHOD ]URL[#assertion]`). `config` is accepted for Protocol conformance and is
    unused (the subject is self-contained); a richer source could read a per-effect URL
    map out of `dos.toml [evidence]` via `config`.
    """

    name = "http_probe"
    accountability = Accountability.THIRD_PARTY

    def __init__(self, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, make the request, map the response to an EvidenceFacts.

        Boundary I/O — the ONE network call lives here (the `ci_status.gather` rule); the
        returned facts are pure data `believe_under_floor` consumes. Never raises: every
        failure mode degrades to an unreachable `no_signal`, so a refused connection /
        timeout / DNS error / bad URL can never be mistaken for an attestation OR a
        refutation. Wrapped by `evidence.gather_evidence` at the call site for the
        belt-and-braces fail-safe, but defensive here too (a driver does not lean on its
        wrapper to be safe — the `os_acceptance.gather` discipline)."""
        parsed = _parse_subject(subject)
        if parsed is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable probe subject — expected '[METHOD ]URL[#assertion]' "
                    "with an http(s) URL — nothing to witness"
                ),
            )
        method, url, assertion = parsed

        status, body, err = self._request(method, url)
        if err is not None:
            # Could not reach the server — abstain, never a fabricated refute.
            return EvidenceFacts.no_signal(
                self.name, self.accountability, url,
                detail=f"could not reach {url} ({err}) — no signal",
            )

        # The server responded — a reached read-back. Map it against the assertion.
        ok, why = _check_assertion(assertion, status, body)
        subj = f"{method} {url}"
        if ok:
            return EvidenceFacts.attest(
                self.name, self.accountability, subj,
                detail=f"{url} → HTTP {status}; {why} (server-authored response)",
            )
        return EvidenceFacts.refute(
            self.name, self.accountability, subj,
            detail=f"{url} → HTTP {status}; {why} (server-authored response disconfirms the effect)",
        )

    def _request(self, method: str, url: str) -> "tuple[int | None, bytes, str | None]":
        """Issue the request, returning `(status, body, error)`. `error` is None on a
        REACHED response (any status, including 4xx/5xx — the server answered); a non-None
        string means we could not reach the server at all (the NO_SIGNAL path)."""
        req = urllib.request.Request(url=url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 (host-chosen URL, the os_acceptance contract)
                status = getattr(resp, "status", None) or resp.getcode()
                body = resp.read(_MAX_BODY_BYTES) if method != "HEAD" else b""
                return int(status), body, None
        except urllib.error.HTTPError as e:
            # A 4xx/5xx IS a reached response — the server answered. Read its body (it is a
            # file-like) so a status/contains/sha256 assertion can be checked against it.
            try:
                body = e.read(_MAX_BODY_BYTES)
            except Exception:
                body = b""
            return int(e.code), body, None
        except (urllib.error.URLError, OSError, ValueError) as e:
            # URLError (DNS / refused / TLS), socket timeout (an OSError subclass), or a
            # malformed URL urlopen rejects → genuinely unreachable → NO_SIGNAL.
            reason = getattr(e, "reason", None) or e
            return None, b"", str(reason)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — the subject grammar + the assertion check.
# ---------------------------------------------------------------------------


def _parse_subject(subject: str) -> "tuple[str, str, str] | None":
    """`'[METHOD ]URL[#assertion]'` → `(method, url, assertion)`, or None if un-parseable.

    Method defaults to GET; only a token in `_METHODS` is treated as a method (so a URL
    with no scheme-leading verb is not mis-split). The URL must be http(s). The `#`
    splits the assertion (empty assertion → the default 2xx-status check). A fragment
    that is a real URL fragment is rare for a probe target and would be consumed as an
    assertion — a host that needs a literal `#` in the URL should percent-encode it."""
    s = (subject or "").strip()
    if not s:
        return None
    method = "GET"
    head, _, rest = s.partition(" ")
    if head.upper() in _METHODS and rest.strip():
        method = head.upper()
        s = rest.strip()
    url, _, assertion = s.partition("#")
    url = url.strip()
    assertion = assertion.strip()
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return None
    return method, url, assertion


def _check_assertion(assertion: str, status: "int | None", body: bytes) -> "tuple[bool, str]":
    """Return `(holds, why)` — does the reached response satisfy the assertion?

    PURE. The default (empty assertion) is "status is 2xx". `status:NNN` / `status:Nxx`
    pin a code or class; `contains:<s>` checks the body; `sha256:<hex>` checks the body
    digest. An un-parseable assertion is treated conservatively as a FAILED check (a
    typo'd assertion must not silently pass), with the reason naming the problem."""
    a = (assertion or "").strip()
    code = status if status is not None else 0

    if not a or a.lower() in ("status:2xx", "2xx"):
        ok = 200 <= code < 300
        return ok, f"status {'is' if ok else 'is NOT'} 2xx"

    kind, _, want = a.partition(":")
    kind = kind.lower().strip()
    want = want.strip()

    if kind == "status":
        w = want.lower()
        if w.endswith("xx") and len(w) == 3 and w[0].isdigit():
            lo = int(w[0]) * 100
            ok = lo <= code < lo + 100
            return ok, f"status {code} {'in' if ok else 'not in'} {w}"
        if want.isdigit():
            ok = code == int(want)
            return ok, f"status {code} {'==' if ok else '!='} {want}"
        return False, f"un-parseable status assertion {want!r}"

    if kind == "contains":
        if not want:
            return False, "empty contains: assertion"
        text = body.decode("utf-8", "replace")
        ok = want in text
        return ok, f"body {'contains' if ok else 'does NOT contain'} {want!r}"

    if kind == "sha256":
        hexd = want.lower()
        if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
            return False, f"malformed sha256 assertion {want!r} (need 64 hex chars)"
        actual = hashlib.sha256(body).hexdigest()
        ok = actual == hexd
        return ok, f"sha256(body) {'==' if ok else '!='} gold ({hexd[:12]}…)"

    return False, f"unknown assertion kind {kind!r} (expected status:/contains:/sha256:)"


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.http_probe '[METHOD ]URL[#assertion]'`
# (also reachable as `dos witness http_probe '<subject>'`).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.http_probe",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'[METHOD ]URL[#assertion]' — e.g. 'https://app/health' or "
             "'GET https://app/health#status:200' or 'https://app/x#contains:ok'",
    )
    ap.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT_S,
                    help=f"seconds before the request is abandoned as NO_SIGNAL (default: {_DEFAULT_TIMEOUT_S})")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    source = HttpProbeEvidenceSource(timeout_s=args.timeout)
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

    # Exit-code map mirrors `dos verify` / os_acceptance: a believed attestation is 0, a
    # refutation is 1 (the endpoint disconfirms the effect), no-signal is 3.
    if belief.refuted:
        return 1
    if belief.believe:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
