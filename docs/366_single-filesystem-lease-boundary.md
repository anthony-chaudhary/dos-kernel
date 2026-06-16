# docs/366 — Single-filesystem lease boundary: scope honesty + remote-lease design

**Status:** DESIGN — phase 1 (scope honesty) shippable; phase 2 (remote authority) deferred.
**Closes:** issue #196
**Author:** kernel team, 2026-06-16

---

## 1. The wound

DOS markets to "fleets of autonomous agents." That is true for the *verification*
half: `verify`, `commit-audit`, and `liveness` all read git history, which travels
across machines freely.

It is not yet true for the *admission* half. The lease WAL (`lane_journal`, an
append-only file) lives on one disk. The O_EXCL mutex that serializes grant
reads is a local `fcntl` lock. The result: two workers on different machines can
each call `dos arbitrate` and both win the same lane, because neither sees the
other's grant.

docs/301 already says "No remote lease persistence." The gap between that line
and the "fleet" framing in the README and docs is the wound this note addresses.

---

## 2. Scope honesty — what ships now

Three surfaces get an explicit boundary statement (the shippable slice):

- **README** (`docs/readme/20_fleet.md`): a one-sentence scope note in the
  adoption table.
- **SECURITY.md**: a bullet under "What DOS is not": lease coordination is
  local-filesystem only; a fleet spanning multiple hosts shares no admission
  serialization point.
- **docs/ARCHITECTURE.md**: a parenthetical on the `lease()` / `arbitrate()`
  syscall row.
- **`dos doctor`**: when the workspace has a git remote (implying the repo is
  shared across machines), doctor prints a one-line note:
  `lease scope   local filesystem only — workers on other machines share no serialization point`.
  This is advisory (exit 0), never a failure.

Test (`tests/test_single_filesystem_lease_boundary.py`) asserts:
- the boundary strings are present in SECURITY.md and ARCHITECTURE.md;
- `dos doctor` prints the lease-scope note when the workspace has a git remote;
- `dos doctor` prints nothing about lease scope when there is no git remote.

---

## 3. Cross-machine coordination design

### 3a. What `verify` gets for free

`verify` reads git. Git is already replicated. Any worker on any host with a
clone can ask "did this commit land?" and get the right answer. This half of the
fleet promise already works across machines. No design needed here.

### 3b. What `arbitrate` needs

`arbitrate` is a pure function: it takes the live-lease list and returns a
decision. What it does NOT do is persist that decision somewhere a worker on
another host can see. Closing the gap means putting the lease list somewhere
both hosts can read and write atomically.

Three candidate authorities, in order of complexity:

**Option A — a git ref (CAS on the remote)**

Each worker races to push a new commit to a well-known ref (e.g.
`refs/dos/leases`) that contains the current live-lease list as a JSON blob.
Git's ref update is atomic at the remote: only one push wins on a
compare-and-swap. The loser fetches the new state and tries again.

Pros: no new service; reuses existing git infrastructure; the lease log is
already a git-native artifact.

Cons: round-trip to a remote on every acquire (50–200 ms for a remote push vs.
<1 ms for a local fsync); TTL enforcement needs a separate sweep; the "push"
model is at odds with high-frequency admission (e.g. 50 concurrent workers).
Suitable for low-frequency, long-lived leases (the typical DOS use case).

**Option B — a small networked service**

A minimal HTTP service (one process, one endpoint per workspace) holds the
live-lease list in memory and exposes `POST /acquire` / `POST /release`. The
kernel shells out to it (or a new driver wraps the HTTP call). Consistency is
trivial (single-writer process). Fencing: the service holds the TTL clock and
evicts stale holders on the next acquire.

Pros: fast; simple consistency model.

Cons: now you need to run and supervise a service; a service crash loses
in-flight lease state (mitigated by TTL); a new operational component.

**Option C — a remote lock (S3 conditional put, Redis SETNX, etc.)**

Use a lease primitive from an existing shared store the fleet operator already
runs. The DOS driver shells the acquire/release to a named lock on that store.

Pros: reuses existing infrastructure; proven consistency primitives.

Cons: adds a provider dependency; the kernel may not name the provider (only a
driver can); the integration contract must be specified so any compliant driver
works.

### 3c. Consistency model

All three options must satisfy the same invariant: **at most one holder per
lane, cross-host, at any moment**. The mechanism:

1. Read the current live-lease list (atomically, from the authority).
2. Call `arbitrate(request, live_leases, config)` — still pure.
3. If ADMIT: write the grant back (atomically — the CAS step that makes it
   safe). Fail the write if the state changed since step 1.
4. If the write fails (lost the race): retry from step 1.

This is the classic optimistic-concurrency read-modify-write. The pure
`arbitrate` kernel stays untouched; only the I/O shell changes.

### 3d. Fencing stale holders (the docs/114 §A2 pattern, cross-host)

A worker can die mid-lease. Without fencing, the lane stays locked forever.

The current TTL mechanism (local, in `lane_lease.py`) already handles this for
in-process expiry. Cross-host, the same concept applies:

- Every granted lease carries a TTL timestamp.
- Before granting a new lease, the authority discards any grant whose TTL has
  expired.
- A still-live holder must heartbeat before TTL expires (the existing
  `keepalive` field in the WAL entry is the right anchor).

The fencing generation (docs/114 §A2) maps to: a lease grant carries a
monotonic generation number. The authority rejects any release or heartbeat that
names a stale generation (the holder lost the lease to TTL eviction and must
re-acquire). This prevents a slow worker from releasing a lease it no longer
holds.

No new kernel primitives are needed. The generation counter is an opaque integer
in the lease record the authority issues; the kernel never interprets it, only
echoes it back on release.

---

## 4. Should DOS own this, or defer to the host?

The honest answer is **defer for now, but own the seam**.

Owning the full remote-lease service would make DOS a networked infrastructure
component. That is a different kind of project — one with availability SLAs,
operational runbooks, and a dependency on a transport that does not exist in the
kernel today.

The right move in the near term:

1. **Document the integration contract** (the seam): what a remote-lease driver
   must implement so any compliant backend (git-ref, HTTP, Redis, S3) can plug
   in without touching the kernel.
2. **Ship Option A (git-ref) as the first remote driver** — it requires no new
   service, and the typical DOS fleet runs against a shared git remote already.
   It is slow but correct, and "slow" is acceptable for long-lived lane leases
   (minutes, not milliseconds).
3. **Keep the kernel pure**: `arbitrate` stays state-in / decision-out. The
   remote-lease driver is a `drivers/` module, exactly as all other host-policy
   code is.

The integration contract for a remote-lease driver:
```
acquire(lane, run_id, ttl_seconds, config) -> LeasGrant | REFUSED
release(lease_grant, config) -> None
heartbeat(lease_grant, config) -> LeasGrant | EVICTED
live(config) -> list[Lease]
```
The driver shells `arbitrate` against its own `live()` result and CAS-commits on
ADMIT. The kernel's `lane_lease.py` acquires against the local WAL today; a
future `drivers/remote_lease.py` acquires against the remote authority instead,
with the same calling convention.

---

## 5. Done-condition

Phase 1 (scope honesty) closes issue #196:
- boundary strings present in SECURITY.md, ARCHITECTURE.md, and README;
- `dos doctor` notes the single-filesystem scope when a git remote is present;
- test asserts all of the above and is green.

Phase 2 (remote authority) is a new issue to be filed with `design` + `ready`
labels when the seam contract above is ratified.
