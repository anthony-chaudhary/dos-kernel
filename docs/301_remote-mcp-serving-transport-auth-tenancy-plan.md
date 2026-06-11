# 301 — Remote MCP serving: transport, auth, and tenancy for connector catalogs

> **The verdict doesn't change; the wire does.** A remote caller gets the same
> pure verdicts the stdio server already serves. What is genuinely new is two
> facts the local transport got for free: *who is calling* (the OS gave us that
> — whoever launched the process), and *which workspace they may ask about* (the
> launcher's cwd). Over HTTPS both must be proven. So this plan adds exactly two
> mechanisms — a token verdict and a workspace registry — and refuses to add a
> third (an authorization server), because issuing identity is not the referee's
> job any more than believing agents is.

> **Status:** designed (2026-06-11) — no phase shipped yet. This document is the
> design plan issue #43's done-condition names: the issue closes when this plan
> lands; Phases 1–4 below track the implementation. Lane: `src` (`src/dos_mcp/`
> only) + `docs`. The kernel (`src/dos/`) is untouched by every phase.

---

## 0. The problem

`dos_mcp` serves **stdio only**: a local process, one launching client, the
workspace defaulting to the server's own cwd (docs/80). Every connector catalog
that would let a hosted agent platform call DOS tools requires the opposite
shape — a **remote** MCP server over HTTPS with real auth. Two concrete
catalogs, same requirements (verified from their published docs, 2026-06-11,
in issue #43):

- **Anthropic's connector directory** — remote MCP servers, OAuth.
- **Harvey's MCP Connector Library** (legal-agent platform) — OAuth 2.1 with
  PKCE S256, RFC 8414 authorization-server metadata, RFC 9728
  protected-resource discovery, HTTPS; RFC 7591 dynamic client registration
  recommended.

Without a remote serving mode, DOS verdicts are unreachable from any hosted
agent platform — which is where most non-CLI agent fleets run. The flagship
consumer is already queued: issue #42's `dos_citation_resolve` tool is aimed at
exactly these platforms, and it rides whatever surface this plan builds.

## 1. The facts the design rides on (web-grounded 2026-06-11)

From the MCP authorization specification (revision 2025-06-18), which **is**
the connector catalogs' shared baseline:

1. **The MCP server is an OAuth 2.1 *resource server*, never the authorization
   server.** The AS is a separate role; its implementation is explicitly out of
   the spec's scope. The split is the spec's architecture, not our invention.
2. **The resource-server side has a small, closed obligation set:** serve an
   RFC 9728 protected-resource metadata document whose `authorization_servers`
   field names at least one AS; answer an unauthenticated request with **401 +
   `WWW-Authenticate`** carrying the resource-metadata URL; validate every
   bearer token; and **bind tokens to audience** — accept only tokens issued
   for this server's canonical resource URI (RFC 8707), rejecting everything
   else. Token passthrough is explicitly forbidden.
3. **Everything else lands on the AS and the client:** RFC 8414 metadata, RFC
   7591 dynamic client registration, PKCE (S256) — all MUST/SHOULD obligations
   of the AS and the MCP *client*, not the resource server. Harvey's checklist
   items beyond HTTPS + RFC 9728 are checks against the AS the server points at.
4. **The transport already exists in our dependency.** The `mcp` SDK the
   `[mcp]` extra pins speaks streamable HTTP natively (`FastMCP.run(transport=
   "streamable-http")`, verified against the installed SDK, v1.27.1). The
   transport half really is mechanical, as the issue suspected.

## 2. The four design decisions

These answer the open questions issue #43 poses, in its order.

### Decision 1 — Token issuance: front an external AS; never embed one

`dos_mcp` becomes an OAuth 2.1 **resource server only**. The operator names
their authorization server (any RFC 8414-compliant provider — a hosted IdP, an
org SSO, an off-the-shelf OSS AS) in the serve config; `dos_mcp` publishes it
via RFC 9728 and validates the tokens it issues. DOS ships **no** authorization
server, no login page, no client registry, no token mint.

This is the spec's own architecture (§1.1), but it is also the kernel doctrine
applied to auth. Validating a token is a **deterministic verdict**: signature
against published keys, issuer against an allowlist, audience against our own
URI, expiry against the clock — facts in, ACCEPT/REJECT out, the same
`classify(evidence, policy)` shape as every kernel syscall. *Issuing* identity
is the opposite kind of thing: interactive, user-facing, policy-laden — the
JUDGE/HUMAN side of the ladder. Embedding an AS would graft the largest
possible trust surface onto the package whose whole point is a minimal TCB.
The refusal is the feature.

### Decision 2 — Tenancy: a closed workspace registry; names, never paths

The stdio server lets the caller pass `workspace` as a **filesystem path** —
correct when the caller and the filesystem belong to the same operator. A
remote caller must never name server filesystem paths. The remote mode replaces
the open path with a **closed registry, declared as data** (the
`reasons`/`stamp`/`lanes` pattern, applied to tenancy):

- The serve config declares the registry: `name → workspace root`, a closed
  set the operator authors. Nothing outside the registry is servable, ever.
- The access token carries the principal's **workspace grants** — scope tokens
  of the form `dos:ws:<name>` (the configurable default; an operator whose AS
  emits grants under a different claim names that claim in the serve config).
- A remote tool call passes `workspace=<name>`; the server resolves it
  registry-first and grant-checked. Unknown name → typed refusal. Known name
  without the grant → 403. Path-shaped input (anything with a separator) →
  typed refusal, never resolved. A principal with exactly one grant gets it as
  the default when `workspace` is omitted.
- Per-call explicit config is already the server's discipline (the
  `tests/test_mcp_server.py` pinned pattern: build a `SubstrateConfig` per
  call, mutate no process-global). Tenancy is that same discipline with the
  *resolution* hardened: the registry decides what a name means; the token
  decides who may use it.

### Decision 3 — Dependency posture: a new `[mcp-remote]` extra; the core stays PyYAML-only

Token validation needs real crypto (JWS signature verification against the
AS's JWKS) — that cannot ride stdlib and must not ride the core install. So:

- A new extra, **`[mcp-remote]`**: the `mcp` SDK plus one JWT/JWKS validation
  library (candidate chosen at Phase 2 by dependency-cone audit; smallest
  wins). The core install stays PyYAML-only; `[mcp]` keeps meaning "stdio MCP,
  nothing more."
- All auth-shaped imports live behind the remote serve path and are guarded the
  way the `mcp` import already is: an `[mcp]`-only install runs the stdio
  server byte-identically and never imports an auth module; asking for the
  remote mode without the extra fails loud with the install hint.

### Decision 4 — Remote tool surface: an explicit allowlist, read-only by default

Every tool the server exposes today is already read-only by construction ("an
MCP tool should decide, not persist" — docs/80). The remote mode pins that as
**policy data**, not habit:

- The serve config carries a tool **allowlist**; the default is the verdict
  tools: `dos_verify`, `dos_commit_audit`, `dos_arbitrate` (pure adjudication —
  it never persists on the MCP surface), `dos_refuse_reasons`,
  `dos_check_reason`, `dos_doctor`, `dos_status`.
- **`dos_recall` is excluded by default**: it reads the operator machine's
  agent-memory store — local-trust context, not a tenant's resource. An
  operator may allowlist it deliberately.
- Anything lease-mutating that ever joins the MCP surface is stdio-only unless
  explicitly allowlisted — the safe direction is structural: remote exposure
  is opt-in per tool, so a future write-shaped tool cannot leak into remote
  reach by default.

## 3. The phases

## Phase 1 — the transport: a streamable-HTTP serve mode

`dos-mcp --transport streamable-http --host 127.0.0.1 --port <N>` (env twins
for container use). Stdio remains the default and is **byte-identical** to
today — no flag, no behavior change, the existing suite green untouched.

One fail-closed rule ships with the transport, before any auth exists: an
HTTP bind to a **non-loopback** interface is refused unless Phase 2 auth is
configured. Loopback-only is the sole authless mode (a dev/test convenience —
the same trust shape as stdio: same machine, same operator). The refusal is
loud and names the gap; there is no "I'll add auth later" open bind.

Pinned by: a test that builds the HTTP app object; the loopback guard test
(non-loopback + no auth → refuse); the existing stdio suite as the
no-regression witness.

## Phase 2 — the auth: the resource-server obligations, exactly

The closed obligation set from §1.2, and nothing beyond it:

- Serve the RFC 9728 document at `/.well-known/oauth-protected-resource`:
  `resource` = the server's canonical URI (serve config), `authorization_servers`
  = the operator's AS list (serve config).
- Unauthenticated / failed requests → **401 + `WWW-Authenticate`** carrying
  `resource_metadata` per RFC 9728 §5.1; insufficient grants → 403.
- Bearer validation as a **pure classifier with I/O at the boundary** (the
  `git_delta → liveness.classify` rule, applied to auth): the boundary fetches
  and caches the AS's JWKS; the pure part takes (token claims, signature
  verdict, clock, policy) and answers ACCEPT / REJECT-with-reason. Checks:
  signature, issuer allowlist, `exp`/`nbf`, and **audience binding** to the
  canonical resource URI (RFC 8707) — a token minted for any other resource is
  rejected regardless of who signed it. Fail-to-reject: any parse/fetch/verify
  error is a 401, never a served verdict.
- The `[mcp-remote]` extra lands here (Decision 3), with the dependency-cone
  audit recorded in the phase's commit.

Pinned by: forged-signature, expired, wrong-issuer, and wrong-audience tokens
each → 401; a grantless valid token → 403; the metadata document and the
`WWW-Authenticate` header shape each pinned byte-level.

## Phase 3 — the tenancy: the registry, the grants, the allowlist

Decisions 2 and 4 made real: the serve-config schema (workspace registry, grant
claim/scope shape, tool allowlist), name-only resolution on every remote tool
call, default-deny everywhere, the path-shaped-input refusal as a typed
refusal. The per-call explicit-`SubstrateConfig` pattern is reused verbatim —
the registry only changes *what a workspace argument is allowed to mean*.

Pinned by: cross-tenant deny (a valid token for ws A calling ws B → 403),
unknown-name deny, path-input refusal, single-grant default resolution, and
an allowlist test (a non-allowlisted tool is absent from the remote tool
list, not merely erroring).

## Phase 4 — the deployment recipe and the connector checklist

The issue's eventual proof: a documented deployment passing a
connector-catalog-style checklist.

- A deployment doc + example config under `examples/`: `dos-mcp` behind a
  TLS-terminating reverse proxy (the standard posture; the Python process
  never holds certificates), plus a container example.
- A **curl-runnable checklist** mapping each catalog requirement to a command
  and its expected observation: HTTPS (the proxy's cert), the RFC 9728
  document present and naming the AS, the AS's RFC 8414 document reachable and
  advertising `code_challenge_methods_supported` ⊇ `S256`, a tokenless call
  answering 401 with `resource_metadata`, a wrong-audience token answering
  401. RFC 7591 is checked on the AS (a `registration_endpoint` in its
  metadata) and documented as the AS-selection criterion it is.

Pinned by: the checklist itself — each row is a command anyone can run against
a deployment; the doc ships only when a real deployment has produced the
expected observation for every row.

## 4. Non-goals (each is a refusal with a reason)

- **No authorization server** — Decision 1. No login, no client registration
  endpoint,
  no token mint, no token storage. RFC 7591 is the AS's surface; we select for
  it, we do not implement it.
- **No kernel edits.** Zero bytes under `src/dos/` change in any phase. The
  kernel never learns HTTP exists; the one-way arrow (`dos_mcp` imports `dos`,
  never the reverse) holds in both directions, as the existing litmus pins.
- **No remote lease persistence.** `dos_arbitrate` stays a pure adjudication
  remotely, exactly as it is on stdio today.
- **No session state beyond caches.** Streamable HTTP is per-request; the
  server holds a JWKS cache and nothing else. Scale-out is the proxy's job.
- **No new default behavior.** Every phase leaves the stdio path
  byte-identical; remote serving is opt-in flag + opt-in extra + opt-in
  config, all three.

## 5. The litmus tests (checkable, like the contract's)

- **Stdio is byte-identical.** The pre-plan stdio suite passes unmodified at
  every phase; no auth module is imported on the stdio path (AST/grep-checkable).
- **Authless means loopback.** A non-loopback bind without configured auth is
  a refusal, pinned by test.
- **Fail-to-reject.** No code path turns a token error into a served verdict —
  the auth analogue of `run_judge`'s fail-to-abstain.
- **Names, never paths, remotely.** No remote code path hands a caller-supplied
  string to the filesystem; resolution is registry-only, pinned by test.
- **The core dependency set is untouched.** `pip install dos-kernel` resolves
  exactly what it resolves today; everything new is inside `[mcp-remote]`.

## 6. Relations

- **Issue #43** — the tracking handle this plan closes (its done-condition:
  this document landing; these phases tracking the implementation).
- **Issue #42** — `dos_citation_resolve` joins the same server and inherits the
  remote surface (allowlist row + grant check) for free once both land.
- **docs/80** — the MCP server's design fence (consumer of `dos`, separate
  top-level package); this plan extends the server inside that fence.
- **docs/275 / docs/282** — the per-call explicit-config and tool-deadline
  disciplines; both apply unchanged on the HTTP transport.
- **docs/217** — the dialect-seam precedent: normalize the vendor-shaped wire
  at the boundary, keep the inside pure. Phase 2's token boundary is that
  pattern aimed at auth instead of hook output.
