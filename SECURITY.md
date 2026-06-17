# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report it privately via GitHub's **["Report a vulnerability"](https://github.com/anthony-chaudhary/dos-kernel/security/advisories/new)**
(Security → Advisories → Report a vulnerability) on this repository. If that is
unavailable, open a minimal public issue asking for a private contact channel —
without details — and a maintainer will follow up.

Please include:

- the version / commit you tested,
- a minimal reproduction (the smaller the better),
- the impact you believe it has, and
- any suggested fix if you have one.

This is a small project; expect an initial acknowledgement on a best-effort basis
rather than a guaranteed SLA. Coordinated disclosure is welcome — tell us your
intended disclosure timeline and we'll work to it.

## Supported versions

DOS is pre-1.0 and ships rolling `vX.Y.Z` releases from `master`, with promoted
`stable/<codename>` tags. Security fixes land on the latest `master` release first.
Until 1.0, only the most recent release is guaranteed to receive fixes.

## What DOS's threat model *is*

DOS exists to be the part of an agent system that **does not believe the agents**. Its
security-relevant value is exactly this adversarial stance toward *its own untrusted
workers*:

- **`verify()`** adjudicates "did this effect actually happen?" against artifacts
  (registry / disk / git ancestry) — **never** from a worker's self-report. A worker
  claiming success is a request for verification, not a result.
- **`refuse(reason_class)`** makes "I correctly declined to act" a typed, legible,
  first-class outcome rather than silence or prose.
- **`arbitrate()`** is a pure admission function over leases — admission control on
  conflicting effects to shared state, unit-testable with no live processes.

This is the same shape the agent-security literature converges on (cognitive/executive
separation; the validator that admits effects is structurally separate from the model
that proposes them). DOS is intended for **authorized** use: building safer fleets,
defensive verification, security testing of your own systems, research, and CTF/
educational contexts.

## What DOS is **not** — do not over-trust it

A trust substrate is only as good as the boundary it's given. Please understand the
limits before relying on DOS:

- **DOS is a referee, not a sandbox.** It adjudicates and serializes claimed effects;
  it does **not** itself confine a malicious process, enforce OS-level isolation, or
  stop code from doing what its capabilities allow. Pair it with real isolation
  (containers, VMs, worktrees, least-privilege credentials).
- **`verify()` is only as strong as its artifacts.** It can adjudicate effects that
  leave a checkable trace (a file changed, a registry entry, a merge ancestry). It
  cannot certify the *correctness of a judgment* that leaves no artifact. Don't read a
  green verdict as "this was the right thing to do" — only "this provably happened."
- **The state plane is git-native and operator-visible by design.** That is a feature
  (auditability), but it means substrate state is **not** a secret store. Never put
  credentials, tokens, or PII into `dos.toml`, lane journals, `execution-state`-style
  files, or any DOS-tracked state. Secrets belong in a real secrets manager and should
  be referenced, never stored.
- **Workspace config is trusted input.** `SubstrateConfig` / `dos.toml` (lanes, paths,
  reasons, stamp grammar) and the `dos.renderers` / driver entry points are treated as
  trusted policy from the workspace operator. Running DOS against a workspace whose
  `dos.toml` or installed entry-point plugins you do not control is equivalent to
  running untrusted configuration/code — don't.
- **Lease coordination is local-filesystem only.** The lane-lease WAL and its
  O_EXCL mutex live on one disk. Workers on separate machines that share only a
  git remote have no common serialization point for `arbitrate` — two hosts can
  each be granted the same lane simultaneously. The *verification* half (`verify`,
  `commit-audit`, `liveness`) travels across machines fine because it reads git
  history; the *admission* half does not. Do not rely on `dos arbitrate` for
  cross-machine mutual exclusion until a remote-lease driver exists. See
  [docs/366](docs/366_single-filesystem-lease-boundary.md).
- **It's pre-1.0.** Interfaces and guarantees can change.

## Dependencies

The kernel is deliberately near-stdlib — its only runtime dependency is **PyYAML**.
The MCP server surface (the `[mcp]` extra) adds the `mcp` framework and is the
one place a larger dependency surface is pulled in; it is optional and isolated to the
`[mcp]` extra. A smaller dependency surface is part of the security posture, not an
accident — please weigh that before proposing new core dependencies.

## Supply chain — the distribution name is `dos-kernel`, NOT `dos`

This project's PyPI **distribution** name is **`dos-kernel`**. The bare `dos` name
on PyPI belongs to an **unrelated** package (`dos` 1.6.0, a Flask/OpenAPI
documentation helper — last released 2020). That package also ships a top-level
`dos` module, so a `pip install dos` / `dos>=X` requirement not only pulls the
wrong project but would **shadow `import dos`**. Treat the bare name as a
name-collision/confusion hazard:

- **Install / depend on `dos-kernel`** — `pip install dos-kernel`,
  `pip install 'dos-kernel[mcp]'`, or a pin like `dos-kernel==X.Y.Z`. For local
  dev, `pip install -e .` from a checkout. **Never** depend on the bare `dos`
  index name.
- The **import** name is unchanged: `import dos`, and the console scripts are
  still `dos` / `dos-mcp`. `[project].name` (the dist) and
  `[tool.setuptools.packages.find]` (the import package) are set independently, so
  the dist rename leaves the import surface untouched.
- The runtime version lookup uses the dist name
  (`importlib.metadata.version("dos-kernel")` in `src/dos/__init__.py`) — looking
  up `"dos"` would miss our metadata and could read the squatter's version if it
  were installed.
- `install.py` is safe by construction — it runs `pip install -e .` against this
  checkout and then verifies the *resolved* `dos.__file__` lives inside the repo
  (`_resolved_dos`), so it can never silently pick up the squatter.
- A stale `src/dos.egg-info/` (from before the rename) re-introduces a `dos`-named
  dist on the path — delete any `*.egg-info` carrying `Name: dos` if you see it.

## Publication gate — the maintainer-side leak scan

Everything tracked in this repository is public the moment it is pushed. To keep
private material (developer-machine paths, hostnames, personal identifiers) from
ever landing here, maintainer clones run a leak scanner over the shippable tree
as a local, fail-closed pre-push gate. Two things about it are deliberate:

- **The scanner itself is not tracked here.** It enumerates the very patterns it
  forbids, so committing it would itself be the leak. It is maintained privately
  and synced into maintainer clones as an ignored file (see the
  `scripts/leak_scan.py` entry in `.gitignore`).
- **CI cooperates either way.** The `leak-scan` job in
  `.github/workflows/ci.yml` runs the scanner when the file is present (a
  maintainer clone) and skips with a note otherwise — a skipped leak-scan job on
  a contributor PR or fork is expected behavior, not a misconfiguration.

If you spot what looks like leaked private content in the published tree or its
history, please report it through the vulnerability channel at the top of this
file rather than opening a public issue.
