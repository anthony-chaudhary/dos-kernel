# Playbook 08 — driver bring-up on QEMU's `edu` device

> **Archetype:** a PCI-driver repo — driver C source (`driver/`), a test harness
> (`tests/`), bring-up notes (`docs/`) — plus **one emulated rig**: a QEMU
> instance with the documented `edu` teaching device (PCI `1234:11e8`: MMIO
> registers, an interrupt line, a DMA engine).
> **The DOS features:** an **equipment lane** (the rig admits one taker at a
> time while disjoint source lanes run concurrently), the **effect-witness
> join** (an interrupt claim adjudicated from `/proc/interrupts`, never from
> narration), and the **ring-0 capability set as config data** (`insmod` is an
> arbitrary-exec entry point the workspace declares).
> **Workspace:** [`../workspaces/edu-rig/`](../workspaces/edu-rig/).
> No real vendor's hardware is named and no physical device is required —
> everything runs against QEMU's `edu` device or static fixtures.

Low-level driver work is where DOS's three load-bearing properties peak at the
same time. **Self-report is least trustworthy**: driver correctness lives in
hardware state machines, timing, and interrupt ordering — none of it knowable
from the source text an agent pattern-matched from other chips. **The
claimant-independent witness is strongest**: a register read-back or an
interrupt counter is further from the agent's reach than any test runner.
**The cost of a believed lie is highest**: panics, silent corruption, DMA
holes. Nobody runs agent fleets on ring-0 code today — not because agents
cannot emit plausible C, but because without a trust substrate, believing them
is unthinkable. This playbook walks the substrate covering the case end to end.

---

## The shape

One rig, three source lanes. The rig's lane tree is its on-disk footprint —
the serial capture, the `dmesg` snapshot, the `/proc/interrupts` snapshots,
the QEMU pidfile:

```toml
[lanes]
concurrent = ["qemu-rig", "driver", "tests", "docs"]
exclusive  = []
autopick   = ["driver", "tests"]          # no "equivalent free rig" — see below

[lanes.trees]
qemu-rig = ["rig/**"]
driver   = ["driver/**"]
tests    = ["tests/**"]
docs     = ["docs/**"]

[exec_capability]                          # the ring-0 entry points, as data
extra = ["insmod", "modprobe", "dd", "flashrom", "setpci"]
```

```bash
cd examples/workspaces/edu-rig
dos doctor --workspace .
#   concurrent lanes    qemu-rig, driver, tests, docs
#   exclusive lanes     (none)
#   autopick ladder     driver, tests
```

## Step 1 — the rig is an equipment lane

One QEMU instance plus one serial console is one physically unshareable
resource. In DOS that is a lane that is **exclusive of itself** — one holder
at a time via the arbiter's same-lane refusal — while staying **concurrent
with disjoint work**. (It is deliberately *not* in the `exclusive` set: that
set means "run ALONE, block the whole portfolio" — right for a
`terraform apply`, wrong for a rig that must not stop a disjoint `driver/**`
edit. Same-lane refusal is what "exclusive" means for equipment.)

The first taker is admitted:

```bash
dos arbitrate --workspace . --lane qemu-rig --leases '[]'
```
```json
{"outcome": "acquire", "lane": "qemu-rig", "lane_kind": "cluster",
 "tree": ["rig/**"], "auto_picked": false, "free_clusters": [],
 "reason": "cluster lane 'qemu-rig' free — admitted.", "pick_count": null}
```
```text
exit code: 0
```

A second taker, while the first still holds the rig, is refused **same-lane**
— and the refusal is legible: it names the held lane and lists the lanes that
*are* free:

```bash
dos arbitrate --workspace . --lane qemu-rig \
  --leases '[{"lane":"qemu-rig","lane_kind":"cluster","tree":["rig/**"]}]'
```
```json
{"outcome": "refuse", "lane": "", "lane_kind": "", "tree": [],
 "auto_picked": false, "free_clusters": ["driver", "tests"],
 "reason": "lane 'qemu-rig' is already held by a live loop — pick a different
            --lane or wait.", "pick_count": null}
```
```text
exit code: 1
```

And a **disjoint source lane is admitted concurrently** — the held rig does
not serialize the rest of the repo. `driver/**` shares no prefix with
`rig/**`, so an agent editing the driver runs alongside the rig campaign:

```bash
dos arbitrate --workspace . --lane driver \
  --leases '[{"lane":"qemu-rig","lane_kind":"cluster","tree":["rig/**"]}]'
```
```json
{"outcome": "acquire", "lane": "driver", "lane_kind": "cluster",
 "tree": ["driver/**"], "auto_picked": false, "free_clusters": [],
 "reason": "cluster lane 'driver' free — admitted.", "pick_count": null}
```
```text
exit code: 0
```

Zero kernel change was needed to express this — the lane taxonomy is already
pure policy data, which is the demonstration: **the seam holds for non-file
resources** (the generic equipment-lane case is #97).

> **Two deliberate choices in the `dos.toml`.** `autopick` omits `qemu-rig`:
> there is no "equivalent free rig", so an agent that needs the rig waits for
> the rig — it is never silently handed a source lane instead, and a bare
> auto-pick request is never handed the rig. And the requests above are
> **kindless** (`--lane qemu-rig`, no `--kind`): a kindless request on a held
> lane refuses same-lane, which is what an equipment taker wants. An explicit
> `--kind cluster` request would instead walk the autopick ladder looking for
> a substitute (see [playbook 02](02_polyglot-web-service.md)); with the
> ladder empty that branch today narrates a misleading "all lanes held"
> refusal — tracked as #118.

## Step 2 — the witness is the read-back

The docs/138 invariant at the hardware boundary: an agent claiming "the
interrupt fires" / "DMA works" is adjudicated from bytes it authored **zero**
of — the `/proc/interrupts` delta, the device-side checksum read back over
MMIO, the `dmesg` transcript — via the effect-witness join (`dos attest`,
exit code = the verdict: 0 CONFIRMED, 1 REFUTED, 3 UNWITNESSED).

The workspace ships synthetic stand-ins for what a live rig harness captures
(neutral paths under `rig/`): `serial.log` (the QEMU serial console),
`dmesg.txt` (the guest kernel log after `insmod`), and two
`interrupts_*.json` snapshots (the `/proc/interrupts` counters before/after
the claimed interrupt test). One lie is engineered in: `dmesg.txt` shows a
clean probe (`edu: probe ok, irq 11`), but the `irq:11:edu` counter is `0` in
**both** snapshots — the driver loaded, *narrated* a working interrupt path,
and the rig never delivered an interrupt.

**The honest claim, CONFIRMED.** The agent claims the probe succeeded and the
ident register read back. The witness is an acceptance command the kernel
runs — the OS authors the exit code (`OS_RECORDED`), the agent authors none
of it:

```bash
dos attest --workspace . --claim edu:probe \
  --narrated "edu device probed; MMIO ident reads back 0x010000ed" \
  --accept-cmd "grep -q 'edu: ident 0x010000ed' rig/dmesg.txt" \
  --key-file rig/attest-demo.key --timestamp 2026-06-12T00:00:00Z
```
```text
VERDICT   CONFIRMED   (believe=True refuted=False)
CLAIM     edu:probe
WITNESS   os_acceptance (OS_RECORDED) over grep -q 'edu: ident 0x010000ed' rig/dmesg.txt
ALGORITHM HMAC-SHA256
TIMESTAMP 2026-06-12T00:00:00Z
SIGNATURE 815f46d0bbd15a3289bc3e0b830569ac5b318778d42d4638f40dc312d85e95c4
REASON    CONFIRMED — non-forgeable witness re-read the world and effect 'edu:probe' is PRESENT: os_acceptance
```
```text
exit code: 0
```

**The false claim, REFUTED — the caught-lie moment.** The agent claims the
interrupt path works. The witness is the state-snapshot diff over the two
`/proc/interrupts` captures: the claimed effect key `irq:11:edu` must appear
in the delta (inserted or updated). The timer and keyboard counters moved;
the edu counter did not:

```bash
dos attest --workspace . --claim irq:11:edu \
  --narrated "interrupt path verified - IRQ 11 fires after the DMA completes" \
  --before rig/interrupts_before.json --after rig/interrupts_after.json \
  --key-file rig/attest-demo.key --timestamp 2026-06-12T00:00:00Z
```
```text
VERDICT   REFUTED   (believe=False refuted=True)
CLAIM     irq:11:edu
WITNESS   state_diff (OS_RECORDED) over rig/interrupts_before.json→rig/interrupts_after.json
ALGORITHM HMAC-SHA256
TIMESTAMP 2026-06-12T00:00:00Z
SIGNATURE 9ac92d1f6b8972f7f514e432e298351d00704cdfeba4b11184142cb06a31146d
REASON    REFUTED — non-forgeable witness re-read the world and effect 'irq:11:edu' is ABSENT: state_diff (a narrated success the world does not corroborate)
```
```text
exit code: 1
```

Claimed "IRQ fires"; witnessed 0 interrupts delivered. The claim was checked
against a surface the claimant cannot write, so the confident narration
changed nothing.

> **Where the snapshots come from on a live rig.** The harness — not the agent
> — reduces `/proc/interrupts` to the `{key: value}` shape before and after
> the claimed test, e.g. (illustrative):
> `awk '/edu|timer|i8042/ {gsub(":",""); print}'` piped into a tiny
> JSON-writer, run by the same supervisor that owns the QEMU process. The
> agent under adjudication authors zero bytes of either snapshot; an
> agent-authored snapshot is `AGENT_AUTHORED` — the forgeable floor — and the
> join structurally refuses to CONFIRM from it.

**The adverse certificate is portable.** `--json` emits the signed receipt; a
third party (an auditor, the team that owns the silicon bring-up) verifies it
with the shared key alone — no access to the agent, the loop, or this repo:

```bash
dos attest --workspace . --claim irq:11:edu \
  --narrated "interrupt path verified - IRQ 11 fires after the DMA completes" \
  --before rig/interrupts_before.json --after rig/interrupts_after.json \
  --key-file rig/attest-demo.key --timestamp 2026-06-12T00:00:00Z \
  --json > /tmp/edu-receipt.json
dos verify-receipt --receipt /tmp/edu-receipt.json --key-file rig/attest-demo.key
```
```text
VALID     VALID — HMAC signature matches the canonical payload (verdict REFUTED, tier OS_RECORDED)
CARRIES   REFUTED (tier OS_RECORDED)  ← ADVERSE: an accountable witness saw the effect ABSENT
```
```text
exit code: 0
```

> `rig/attest-demo.key` is a demo signing key shipped so the transcripts above
> reproduce byte-for-byte (fixed key + injected `--timestamp` ⇒ deterministic
> signatures). It is labeled what it is — **not a secret**. A real deployment
> generates its own and keeps it where the agent can't read it.

## Step 3 — the ring-0 capability set is config data

The default arbitrary-exec set covers interpreters, shells, and runners —
`python -c`, `bash -c`, `npx` — the ring-3 entry points. Module-load and
device-write tokens are the ring-0 analogues: `insmod edu_drv.ko` runs
arbitrary code in the kernel, escaping every narrower per-command gate. The
workspace declares them (the closed-config-as-data seam, docs/224), and the
classifier matches the **invoked program token**, never a substring:

```bash
dos exec-capability --workspace . \
  --extra insmod,modprobe,dd,flashrom,setpci \
  --command "insmod edu_drv.ko"
```
```text
GRANTS_ARBITRARY_EXEC  the command invokes 'insmod', an arbitrary-code-execution entry point — it can run any code, escaping a narrower per-command gate (GRANTS_ARBITRARY_EXEC)
```
```text
exit code: 3
```

```bash
dos exec-capability --workspace . \
  --extra insmod,modprobe,dd,flashrom,setpci \
  --command "cat /proc/interrupts"
```
```text
BOUNDED  the command invokes 'cat', not a known arbitrary-exec entry point — bounded (NOT a safety guarantee; only 'not in the declared exec set')
```
```text
exit code: 0
```

A plain proc read stays BOUNDED with the same declared set active — reading
the interrupt counters is how the *witness* works, and the classifier never
flags it. The verdict is ADVISORY (a capability observation, not a denial);
the consumer — `dos hook pretool`, a CI gate, a human — decides what a
GRANTS_ARBITRARY_EXEC at ring 0 requires (a rig lease held? a passing
suite first?).

> **Honesty note:** the workspace's `dos.toml` carries the
> `[exec_capability]` table as the documented durable declaration, but today
> the CLI reads only the `--extra` flag — the table is wired to nothing
> (#117). The transcripts above pass the set explicitly so they reproduce;
> when #117 lands, the flag drops away.

## Step 4 — the evidence ladder, honestly stated

Each rung below is env-authored and strictly stronger than the one before.
The fixture's `serial.log` covers the boot rung:

| Rung | The witness | Who authors it |
|---|---|---|
| compile exit | `cc` exit code on the driver tree | the toolchain |
| boot transcript | the QEMU serial console reaches `login:` | QEMU + the guest kernel |
| load + probe | `insmod` exit code, the `dmesg` delta | the guest kernel |
| device read-back | ident/status registers over MMIO, the `/proc/interrupts` delta, the DMA checksum the *device* computed | the emulated device |
| suite exit | the driver test harness exit code | the test runner |

```bash
dos attest --workspace . --claim rig:boot \
  --narrated "QEMU guest booted to a login prompt" \
  --accept-cmd "grep -q 'edu-rig login:' rig/serial.log" \
  --key-file rig/attest-demo.key --timestamp 2026-06-12T00:00:00Z
#   VERDICT   CONFIRMED   (believe=True refuted=False)
#   CLAIM     rig:boot
#   ...
#   exit code: 0
```

And the ceiling, stated plainly: **QEMU-green catches *lies* cheaply; it does
not certify *silicon*.** A REFUTED here is decisive — the claimed effect is
absent from a surface the claimant cannot write. A CONFIRMED here means the
effect is present *on the emulated device*: real hardware still differs in
timing, ordering, and errata, which is why the ladder ends at the rig and a
silicon qual run sits above it (the #97 equipment-lane case, where the witness
gets stronger as the work gets more physical). Every verdict in this playbook
is advisory — DOS reports and proposes; the host decides.

---

*Related: [playbook 02](02_polyglot-web-service.md) (concurrent disjoint
lanes), [playbook 05](05_infra-monorepo.md) (run-alone exclusive lanes — the
other meaning of "exclusive"), [playbook 06](06_debug-a-stuck-fleet.md)
(troubleshooting), and #97 (the generic hardware-in-the-loop equipment-lane
playbook this one instantiates).*
