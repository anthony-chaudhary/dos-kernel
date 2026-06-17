# docs/384 — Widening the witness spectrum: the `dos witness` verb + new evidence kinds

> **One line.** DOS already knows how to *not believe* an agent about a git commit.
> This arc makes the same distrust reach the rest of the world an agent leaves
> effects on — a live endpoint, the OS, the filesystem, the rendered screen — by
> (1) making the whole witness population invocable through ONE verb and (2) adding
> first-class witness backends for surfaces the kernel could only reach awkwardly.

## The goal

"100× the types of things DOS can witness." Not everything is a git diff — an agent
also starts a server, ships a deploy, writes a file, renders a screen — and DOS
should be able to witness *those* effects with the same rule it uses for commits:
**a belief bit may only be set by bytes the judged agent did not author.**

## What we already had (do not rebuild it)

The witness machinery is already domain-free and pluggable — this arc *populates* it,
it does not reinvent it.

- **The accountability spectrum** (`dos/log_source.py`, re-exported by `dos/evidence.py`):
  three rungs, weakest→strongest — `AGENT_AUTHORED` (the forgeable floor; a JUDGE hint,
  never a verdict) · `OS_RECORDED` (a kernel-launched exit code, a git blob) · `THIRD_PARTY`
  (infra the agent cannot write — a CI verdict, a cloud trail, a server's own response).
  The tag is a *ceiling* on trust, declared as data on the source.
- **The `EvidenceSource` seam** (`dos/evidence.py`): a `Protocol` (`name`,
  `accountability`, `gather(subject, config) -> EvidenceFacts`), a fail-safe runner
  (`gather_evidence` — a source that raises or returns the wrong type degrades to
  NO_SIGNAL, never a fabricated attestation), the floor fold (`believe_under_floor` —
  *believe ⟺ a non-forgeable source attested*), and a by-name resolver over the
  `dos.evidence_sources` entry-point group.
- **The claim/read-back join** (`dos/effect_witness.py`): `witness_effect(claim,
  readbacks)` → CONFIRMED / REFUTED / UNWITNESSED / NO_CLAIM.
- **The coverage census** (`scripts/source_census.py`, docs/358): the operator goal
  "cover the industry-known data sources DOS can witness" turned into a *measured*
  number. Its load-bearing idea: **a witness is a SHAPE, not a per-vendor driver** —
  the universe collapses onto a small closed set of shapes, each proven by a registered
  backend; a source is COVERED only when its shape resolves to a backend actually
  registered in this tree (never a hardcoded yes).

## The gap this arc closes

Two real gaps, neither a hole in the *model* — both in its *reach*:

1. **The population was hard to invoke.** `dos verify` reads exactly one witness
   (git). `dos attest` joins a claim over a hard-coded `if/elif` of four surfaces.
   Every other registered witness (`ci_status`, `provider_ledger`, `citation_resolve`,
   `slack_approval`, …) was reachable only as `python -m dos.drivers.<x>` — no uniform,
   first-class operator surface. Adding a witness *kind* meant editing kernel CLI
   branches.
2. **Some everyday surfaces had only an awkward witness.** A live endpoint ("the
   deploy is up") was coverable only by routing `curl`'s exit code through
   `os_acceptance` (losing status/body granularity) or hand-writing a `read_state`
   reader for `state_diff`. The rendered screen (a screenshot) had no witness at all.

## What shipped in this arc

### 1. `dos witness <source> <subject>` — the unified, by-name witness verb

Resolves WHICHEVER `dos.evidence_sources` backend is named — built-in or third-party
plugin — through the by-name resolver, gathers it through the fail-safe wrapper, and
folds through `believe_under_floor`. The floor holds at the CLI surface: an
`AGENT_AUTHORED` source that ATTESTS is shown but cannot grant belief (exit 3).
`dos witness --list` shows the wired sources and each one's rung.

```
dos witness os_acceptance "pytest -q"                 # OS_RECORDED: exit-code witness
dos witness http_probe "https://app/health#status:200"# THIRD_PARTY: the deploy is live
dos witness --list                                    # the wired population + rungs
```

Exit map mirrors `verify`/`os_acceptance`: `0` believed · `1` refuted (an accountable
disconfirmation) · `3` abstain (NO_SIGNAL, or a forgeable-floor-only attest) · `2`
usage error. **The leverage:** a new witness kind becomes invocable with no kernel
edit — just an entry-point registration. Layer 3 (a CLI helper): names no host,
imports no driver statically (resolves by name at the boundary). Pinned by
`tests/test_cli_witness.py`.

### 2. `http_probe` — the live-endpoint witness ("the deploy shipped")

`evidence.py` names the effects the git rung is blind to: "a webhook delivered, a
payment made, … a deploy shipped." For a deploy the accountable witness is the
**server that answers the request**. The kernel makes the HTTP request; the server
authors the response — `THIRD_PARTY`, because the agent cannot make a remote service
it does not control return a healthy answer. A reached 2xx ATTESTS (grants belief); a
reached non-2xx REFUTES (the deploy is up but unhealthy — the silent-fail made
visible); an HTTP error status is a *reached* response mapped by the assertion; only a
genuinely unreachable endpoint degrades to NO_SIGNAL (never a fabricated refute on a
network blip). Stdlib-only (`urllib`). Subject grammar: `[METHOD ]URL[#assertion]`
(`status:NNN`/`status:Nxx` · `contains:<s>` · `sha256:<hex>`). Pinned by
`tests/test_http_probe.py` (monkeypatched `urlopen` — the suite never hits the net).

The honest caveat (named in the docstring, not silently assumed): the witness is sound
only when the URL targets a surface the agent does not control — the same "the host
wires the command" contract `os_acceptance` already lives under.

## The spectrum — the map (× = shipped, ○ = roadmap below)

| Surface read | Rung | Backend | Invocation |
|---|---|---|---|
| VCS commit / ancestry / blob | OS_RECORDED | in-kernel `oracle` | `dos verify` × |
| Commit subject vs its diff | OS_RECORDED | in-kernel `commit_audit` | `dos commit-audit` × |
| Command exit code | OS_RECORDED | `os_acceptance` | `dos witness os_acceptance` × |
| CI / third-party API verdict | THIRD_PARTY | `ci_status`, `citation_resolve` | `dos witness ci_status` × |
| External-effect ledger | THIRD_PARTY | `provider_ledger` | `dos witness provider_ledger` × |
| Persisted-store delta | OS/THIRD | `content_diff`, `state_diff` | `dos attest --before/--after` × |
| Human approval envelope | THIRD_PARTY | `slack_approval` | `dos witness slack_approval` × |
| **Live HTTP endpoint** | **THIRD_PARTY** | **`http_probe`** | **`dos witness http_probe`** × |
| Process / port liveness | OS_RECORDED | `os_process` | `dos witness os_process` ○ |
| Filesystem artifact | floor → OS (w/ gold) | `fs_artifact` | `dos witness fs_artifact` ○ |
| **Rendered screen / screenshot** | **OS_RECORDED** (kernel-captured) | **`visual_witness`** | **`dos witness visual_witness`** ○ |

## Roadmap — designed here, built next (each must ship its backend, or it stays SPEC)

The census honesty rule binds the roadmap: a new *source row* may be marked COVERED
only when its backend is registered and resolves. So each item below ships
**driver + registration + census row** together, or it is listed SPEC (out of the
covered headline) until it does.

### `os_process` — process / port liveness as a witnessed effect (OS_RECORDED)
"The service I deployed is running / listening." The kernel reads the OS process table
and/or opens a socket — the OS authors the fact; a dead process cannot keep a socket
listening. Reuses the existing `dos/proc_delta.py` probe (the PID-reuse-defended,
foreign-host-blind, stdlib+ctypes reader) as an `EvidenceSource`. Subject grammar:
`pid:<n>` · `port:<host:port>` · `listening:<port>`. ATTESTED iff alive/listening,
REFUTED iff confidently gone, NO_SIGNAL iff it cannot tell (the `proc_delta`
never-fabricate-True discipline). This is the "OS stuff" with a clean first-class
witness instead of a `pgrep`/`ss` exit code through `os_acceptance`.

### `fs_artifact` — the filesystem read-back (honest floor; OS_RECORDED with a gold)
"The build produced `dist/app.tar.gz`." A turn-time on-disk read is `actor==witness`
for content the agent could have written, so **existence alone is the forgeable
floor** (the agent can `touch` a file) — recorded, never believed. Soundness comes
from the **gold**: comparing the on-disk bytes to a `sha256:<hex>` an independent
source supplied is preimage-sound, so the comparison is expressed via
`evidence.derived_witness` and the rung is capped at `min(file-read, gold)` — exactly
the `content_diff` discipline. Subject grammar: `<path>` (existence, floor) ·
`<path>#sha256:<hex>` · `<path>#size:<n>`. The REFUTE direction is the value even at
the floor: "you claimed you built X — it is absent." (It is the awkward-but-covered
`persisted_state_diff` shape's *direct-on-disk* ergonomic form.)

### `visual_witness` — the rendered-screen / screenshot witness (the new shape)
The headline new TYPE the census does not yet enumerate. An agent's claim "the page
renders correctly / the chart was produced" is witnessed by **pixels the kernel
captured**, not the agent's narration. The kernel reads a captured image (PPM/PGM —
stdlib-parseable; a host's capture pipeline emits it), computes a **perceptual hash**
(dHash over a downscaled luminance grid), and compares it to a reference (a gold phash
or a reference image) within a Hamming-distance tolerance. `OS_RECORDED` when a
kernel-run capture authored the pixels (the `state_diff` posture: refuse
`AGENT_AUTHORED` — a witness over an agent-pasted screenshot is not a witness).

This is where the goal's "various spectrum and levels" becomes concrete: visual
evidence sits across **two rungs of adjudication**:
- **Deterministic (oracle rung):** a perceptual-hash distance under a tolerance is a
  pure, repeatable ATTEST/REFUTE — the kernel decides.
- **Perceptual / semantic (JUDGE rung):** "does this screenshot *show the error
  dialog*?" has no canonical hash — it routes to the `dos.judges` seam (advisory,
  fail-to-abstain). An agent-pasted screenshot, being `AGENT_AUTHORED`, can ONLY take
  this advisory path; it can never reach an oracle verdict.

The same effect (a rendered screen) is therefore witnessable at an oracle level (exact
capture vs reference) or, when the comparison is inherently fuzzy, at the advisory
judge level — never silently promoted from one to the other.

## Disciplines preserved (the litmus, restated for new witnesses)

- **Floor discipline.** Every new source declares a fixed `accountability`; only a
  non-forgeable rung can grant belief (`believe_under_floor`). A forgeable read-back is
  recorded and ignored, never the basis of a SHIPPED.
- **Fail-safe, never fail-open.** Every gather degrades to NO_SIGNAL on any failure —
  never a fabricated ATTEST or REFUTE.
- **Drivers do the I/O; the kernel imports no driver.** Each witness lives under
  `dos/drivers/`, registers via the `dos.evidence_sources` entry point, and is resolved
  by name at the boundary. The kernel ships only the seam + the `null` baseline.
- **Census honesty.** A new witnessable source is COVERED only when its backend is
  built and registered; otherwise it is SPEC (out of the headline) — the number never
  inflates on a promise. Residue (no sound witness — "did the human actually read the
  email", "is the prose good") stays out of the denominator, named not hidden.

## Status

- **Shipped:** the `dos witness` verb; the `http_probe` backend + registration; tests
  for both.
- **Next:** `os_process`, `fs_artifact`, `visual_witness` — each as driver +
  registration + census row + tests, in that-each-keeps-the-census-green order.
