# `edu-rig` — driver bring-up on QEMU's `edu` device (ring-0 archetype)

An anonymized PCI-driver repo plus **one emulated rig**: a QEMU instance with
the documented `edu` teaching device (PCI `1234:11e8` — MMIO registers, an
interrupt line, a DMA engine). No real vendor's hardware, no physical device:
everything here runs against QEMU or static fixtures.

```text
driver/         the PCI driver C source      →  lane: driver    (concurrent)
tests/          the driver's test harness    →  lane: tests     (concurrent)
docs/           bring-up notes               →  lane: docs      (concurrent)
rig/            the rig's on-disk footprint  →  lane: qemu-rig  (EQUIPMENT — one taker at a time)
```

Three things this workspace is built to demonstrate:

```bash
# 1) The rig is an EQUIPMENT lane — one QEMU + one serial console is physically
#    unshareable. A second taker is refused same-lane while a disjoint driver
#    edit runs concurrently:
dos arbitrate --workspace . --lane qemu-rig \
  --leases '[{"lane":"qemu-rig","lane_kind":"cluster","tree":["rig/**"]}]'

# 2) The witness is the read-back — "the interrupt fires" adjudicated from the
#    /proc/interrupts delta the agent authored zero bytes of, never from the
#    agent's narration:
dos attest --workspace . --claim irq:11:edu \
  --before rig/interrupts_before.json --after rig/interrupts_after.json \
  --key-file rig/attest-demo.key

# 3) The ring-0 capability set is config data — insmod is this workspace's
#    arbitrary-exec entry point, a plain proc read stays BOUNDED:
dos exec-capability --workspace . --extra insmod,modprobe,dd,flashrom,setpci \
  --command "insmod edu_drv.ko"
```

The `rig/` files are **synthetic stand-ins** for what a live rig harness
captures: `serial.log` (the QEMU serial console), `dmesg.txt` (the guest's
kernel log after `insmod`), and the two `interrupts_*.json` snapshots (the
`/proc/interrupts` counters before/after the claimed interrupt test, reduced to
the `{key: value}` shape the state-diff witness reads). `attest-demo.key` is a
demo HMAC signing key for the walkthrough — **not a secret**.

> The deliberately-engineered lie in the fixtures: `dmesg.txt` shows a clean
> probe (`edu: probe ok, irq 11`), but the `irq:11:edu` counter is `0` in BOTH
> snapshots — the driver loaded and *narrated* a working interrupt path, and the
> rig says no interrupt was ever delivered. The walkthrough catches it.

Full walkthrough: [`../../playbooks/08_driver-bringup-qemu-edu.md`](../../playbooks/08_driver-bringup-qemu-edu.md).
