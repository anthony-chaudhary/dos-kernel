# docs/372 — the tool call as a system call: the adjudicated call layer

> **Status:** 📋 planned — design note, no code yet. This is the altitude argument
> behind a future call-path binding of the existing verdict verbs; it ships nothing
> on its own. The two buildable legs it motivates are
> [docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md)
> (pre-flight) and [docs/374](374_ten-x-stewards-one-invariant-per-steward.md) (stewards).
>
> Origin: operator thread 2026-06-16 — *"the concept of a tool call is a system call …
> tool calling should be local and dos and the different adjudication of that is a very
> critical part of tool calling."* Strategy framing lives in the private repo's
> `dispatch-os-tool-call-is-a-syscall.md`; this is the kernel-facing version.

## The gap

DOS verbs run *after* a call, on demand. `dos verify` reads git after an agent claims
"done"; `dos arbitrate` is asked whether two workers may run. The verdict logic is
sound and witness-grounded, but it attaches as **a gate you call**, not as **the door
the agent goes through**. Nothing in the kernel reasons about a tool call *as a typed
transition* on the model's privilege boundary, and nothing refuses a call *before it
fires*.

That timing is the whole cost. An agent's generation is cheap and reversible — text in
a buffer harms nothing. The instant a tool call fires it touches the world, the spend
is paid, and the result pollutes context whether the call succeeded or not. So the
boundary worth adjudicating is the tool call, and the moment worth adjudicating it is
*before* execution.

## The thesis

> Treat the tool call as the agent OS's system call: the one narrow, typed, versioned
> door where an untrusted-by-default process (the model) asks the trusted kernel (DOS)
> to touch the world. Then DOS's existing verdicts stop being a side-car and become the
> kernel's reference monitor on the call path.

The analogy is load-bearing, not decorative. A Unix process cannot touch a disk or a
socket on its own authority; it asks through the syscall table and the kernel decides.
The syscall is where every security, accounting, and isolation guarantee attaches. The
tool call is the same door for an agent — we have just been treating it as
`subprocess.run`.

## What each borrowed OS primitive maps to

| OS primitive | Agent-kernel analogue | DOS piece today |
|---|---|---|
| syscall table | typed, versioned **tool ABI** known at load time | (new) the declared tool surface |
| LSM / seccomp-bpf hook | **reference monitor** on the call path: allow/deny/transform/require-witness | `dos_arbitrate`, `dos_refuse_reasons`, `dos_check_reason` |
| pre-fault permission check | **mechanistic pre-flight** before the call commits | [docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md) |
| vDSO (userspace syscall) | **tool fast-path**: pure / cached-unchanged / table-answerable calls served in-binary, no round-trip | content-addressed cache + the ABI |
| MMU memory protection | **context as a protected address space**: a result enters context only if it passes admission | `headroom` as the page-in compressor |
| syscall error codes | the **closed refusal vocabulary** returned *before* the call when pre-flight refutes | `dos_refuse_reasons` |
| post-condition | **effect verification** after the call | `dos_verify` |
| concurrency control | the **arbiter** deciding which calls may run together | `dos_arbitrate` |

The shift is altitude, not mechanism: the same witness-grounded verdicts, moved onto
the call path and pulled before the call.

## The one invariant that carries over

DOS's floor — *the byte-author of the witness must not be the judged agent* — applies
unchanged. A syscall verdict comes from the typed ABI, the file tree, git, the clock,
or a schema — never from the model's narration of what the call will do. The reference
monitor reads evidence, not intent.

## Where the analogy stops (honest limit)

The model is not a *hostile* process; it is usually a cooperative collaborator that is
merely unreliable. seccomp exists to contain malice; the value here is distrust of
*self-report*, not containment of an attacker. Keep the mechanism (witness-grounded
adjudication before the call) and drop the threat-model theater. And a real syscall
table is closed, while tools register dynamically (MCP) — un-declared tools fall back to
the slow always-execute path, degraded but not broken. The full objection set is in the
private memo §7.

## Done condition (for this note)

This note is "done" when its two buildable legs have their own design docs — which they
do: [docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md)
and [docs/374](374_ten-x-stewards-one-invariant-per-steward.md). It commits no code and
closes no phase; it is cold-tier reference for *why* a call-path binding would exist.

## Related

- [docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md) — the pre-flight ladder (the buildable core of this note).
- [docs/374](374_ten-x-stewards-one-invariant-per-steward.md) — the steward population that gardens the fleet on the other side of the call.
- Private: `dispatch-os-tool-call-is-a-syscall.md`, `dispatch-os-the-fused-agent-kernel.md`.
