# Playbook 09 — hardware-in-the-loop: shared lab equipment

> **Archetype:** a reliability lab where an agent fleet shares **finite,
> exclusive, expensive equipment** — burn-in chambers, device-qual rigs, EDA
> license seats, shared GPU boxes. The fixture: two burn-in benches (chamber A,
> chamber B), disjoint firmware/test source lanes, and a farm-wide power/thermal
> cap of two concurrent campaigns.
> **The DOS features:** an **equipment lane** (a bench admits one taker at a
> time while disjoint source lanes run concurrently), a **class budget** (at
> most N concurrent chambers — the (N+1)th is refused), and the **witness is the
> rig** (a "the soak passed" claim adjudicated from the instrument's own thermal
> capture, never the campaign log the agent's tooling wrote).
> **Workspace:** [`../workspaces/burn-in-farm/`](../workspaces/burn-in-farm/).
> No real vendor's equipment is named; everything runs against static fixtures.

This is the **generic** equipment case (#97). Playbook 08 instantiates the same
three properties on one concrete rig — a QEMU `edu` device for ring-0 driver
bring-up; this one strips the case to its general shape and adds the farm-wide
budget that playbook 08's single rig does not exercise.

Hardware-in-the-loop is where DOS's three load-bearing properties peak together.
**Self-report is least trustworthy**: a multi-hour soak campaign's outcome lives
in instrument state an agent cannot see from its source text. **The
claimant-independent witness is strongest**: a chamber controller's measured
temperature is further from the agent's reach than any test runner — the witness
gets *stronger* as the work gets more physical. **The cost of a believed lie is
highest**: a part shipped on a falsely-passed qual is a field return, not a
failed CI job. One generic playbook covers the whole population.

---

## The shape

Two benches, two source lanes, one farm-wide budget. A bench's lane tree is its
on-disk footprint — the campaign log and the instrument's thermal capture for
that chamber:

```toml
[lanes]
concurrent = ["bench-a", "bench-b", "firmware", "tests"]
exclusive  = []
autopick   = ["firmware", "tests"]        # no "equivalent free bench" — see below

[lanes.trees]
bench-a  = ["benches/bench-a/**"]
bench-b  = ["benches/bench-b/**"]
firmware = ["firmware/**"]
tests    = ["tests/**"]

[[concurrency_class]]                      # the farm-wide power/thermal cap, as data
name = "burn-in"
max_concurrent = 2
```

```bash
cd examples/workspaces/burn-in-farm
dos doctor --workspace .
#   concurrent lanes    bench-a, bench-b, firmware, tests
#   exclusive lanes     (none)
#   autopick ladder     firmware, tests
```

## Step 1 — a bench is an equipment lane

One chamber runs one campaign at a time. In DOS that is a lane **exclusive of
itself** — one holder via the arbiter's same-lane refusal — while staying
**concurrent with disjoint work**. (It is deliberately *not* in the `exclusive`
set: that set means "run ALONE, block the whole portfolio" — right for a
`terraform apply`, wrong for a bench that must not stop a disjoint `firmware/**`
edit. Same-lane refusal is what "exclusive" means for equipment.)

The first taker on chamber A is admitted:

```bash
dos arbitrate --workspace . --lane bench-a --leases '[]'
```
```json
{"auto_picked": false, "free_clusters": [], "lane": "bench-a",
 "lane_kind": "cluster", "outcome": "acquire", "pick_count": null,
 "reason": "cluster lane 'bench-a' free — admitted.",
 "tree": ["benches/bench-a/**"]}
```
```text
exit code: 0
```

A second taker, while the first still holds chamber A, is refused **same-lane**
— and the refusal is legible: it names the held lane and lists the lanes that
*are* free:

```bash
dos arbitrate --workspace . --lane bench-a \
  --leases '[{"lane":"bench-a","lane_kind":"cluster","tree":["benches/bench-a/**"]}]'
```
```json
{"auto_picked": false, "free_clusters": ["firmware", "tests"], "lane": "",
 "lane_kind": "", "outcome": "refuse", "pick_count": null,
 "reason": "lane 'bench-a' is already held by a live loop — pick a different
            --lane or wait.", "tree": []}
```
```text
exit code: 1
```

And a **disjoint second bench is admitted concurrently** — a held chamber does
not serialize the rest of the lab. `benches/bench-b/**` shares no prefix with
`benches/bench-a/**`, so a campaign on chamber B runs alongside chamber A:

```bash
dos arbitrate --workspace . --lane bench-b \
  --leases '[{"lane":"bench-a","lane_kind":"cluster","tree":["benches/bench-a/**"]}]'
```
```json
{"auto_picked": false, "free_clusters": [], "lane": "bench-b",
 "lane_kind": "cluster", "outcome": "acquire", "pick_count": null,
 "reason": "cluster lane 'bench-b' free — admitted.",
 "tree": ["benches/bench-b/**"]}
```
```text
exit code: 0
```

Zero kernel change was needed to express any of this — the lane taxonomy is
already pure policy data, which **is** the demonstration: the seam holds for
non-file resources.

> **Why `autopick` omits the benches.** There is no "equivalent free bench" in
> the sense the picker means — chamber A and chamber B are not interchangeable
> work surfaces (a campaign is pinned to the chamber its units are loaded into).
> So an agent that needs chamber A waits for chamber A; it is never silently
> handed chamber B or a source lane instead. The requests above are also
> **kindless** (`--lane bench-a`, no `--kind`): a kindless request on a held
> lane refuses same-lane, which is what an equipment taker wants.

## Step 2 — the farm-wide budget refuses the (N+1)th chamber

Per-bench exclusivity is not the whole story. The lab has power and thermal
headroom for only **two** campaigns at once, no matter how many chambers exist.
That cap is a **concurrency class budget** (docs/97): a bench lease declares
`concurrency_class = "burn-in"`, and the kernel refuses the grab that would push
the live count past `max_concurrent`.

> **Honesty note — this half runs through the picker path, not a `dos
> arbitrate` CLI line.** The `class_budgets` gate bites only inside the kernel's
> bare auto-pick walk over a host-supplied `auto_pick_order` ladder — the
> (lane, kind, tree) slot list a real host (or the picker driver) feeds the
> kernel. The generic `dos arbitrate` CLI produces no such ladder, so a class
> budget never fires from a copy-paste CLI line (#97's scoping finding;
> `tests/test_concurrency_class.py` pins exactly why). The runnable
> [`farm_budget_demo.py`](../workspaces/burn-in-farm/farm_budget_demo.py) **is**
> that host — 30 lines feeding the slot ladder and the `burn-in` budget into the
> same `arbiter.arbitrate(...)` the picker calls:

```bash
cd examples/workspaces/burn-in-farm
python farm_budget_demo.py
```
```text
GRAB 1: acquire  lane=chamber-1  auto-picked burn-in lane 'chamber-1' from priority ladder.
GRAB 2: acquire  lane=chamber-2  auto-picked burn-in lane 'chamber-2' from priority ladder.
GRAB 3: refuse             CLASS_BUDGET_EXHAUSTED: every auto-pick candidate belongs to a concurrency class already at its max_concurrent budget (burn-in (2/2)) — admitting one would exceed the budget. The work exists and the regions are fine; the class is simply full. Wait for a holder of that class to release (do NOT /replan — there is nothing to refill), or raise the class budget if the concurrency is genuinely safe.
```

Two chambers admit; the third is refused with `CLASS_BUDGET_EXHAUSTED` naming
`burn-in (2/2)`. The refusal is precise about the lever — **wait for a holder to
release** (the work exists and the regions are fine; the class is simply full),
explicitly *not* `/replan` (there is nothing to refill) and not a tree
collision (the chamber trees are pairwise-disjoint, so only the budget can
refuse). Raising the cap is a one-line `max_concurrent` edit *if* the extra
concurrency is genuinely safe for the lab's power budget.

> The picker message says "from priority ladder" — `priority` is the kernel's
> own reference label for the budget-walk, not this farm's class name. The
> binding verdict is the `burn-in (2/2)` in the reason.

## Step 3 — the witness is the rig, not the campaign log

The docs/138 invariant at the equipment boundary: an agent claiming "the soak
passed" is adjudicated from bytes it authored **zero** of — the chamber
controller's measured-temperature capture — via the effect-witness join (`dos
attest`, exit code = the verdict: 0 CONFIRMED, 1 REFUTED).

The fixture ships two surfaces for chamber A (neutral paths under
`benches/bench-a/`). `campaign.log` is the **forgeable** surface: the
tester-harness wrapper writes its `soak reached` / `PASS` lines, and an
over-eager or misconfigured harness can stamp `PASS` for a unit that never held
setpoint. `thermal_after.json` is the **non-forgeable** surface: the chamber
controller writes the measured max temperature directly. One lie is engineered
in — the campaign log shows `PASS unit-7`, but unit-7's measured max is `71C`
against a `125C` setpoint: the harness *narrated* a passing soak the chamber
never delivered.

**The honest claim, CONFIRMED.** unit-3 genuinely soaked; the witness is an
acceptance command the kernel runs — the OS authors the exit code, the agent
authors none of it:

```bash
dos attest --workspace . --claim soak:unit-3 \
  --narrated "unit-3 completed 168h soak at 125C" \
  --accept-cmd "python -c \"from pathlib import Path; import sys; sys.exit(0 if 'PASS unit-3' in Path('benches/bench-a/campaign.log').read_text(encoding='utf-8') else 1)\"" \
  --key-file benches/bench-a/attest-demo.key --timestamp 2026-06-14T00:00:00Z
```
```text
VERDICT   CONFIRMED   (believe=True refuted=False)
CLAIM     soak:unit-3
WITNESS   os_acceptance (OS_RECORDED) over python -c "from pathlib import Path; import sys; sys.exit(0 if 'PASS unit-3' in Path('benches/bench-a/campaign.log').read_text(encoding='utf-8') else 1)"
ALGORITHM HMAC-SHA256
TIMESTAMP 2026-06-14T00:00:00Z
SIGNATURE 835d42ab0193bab27adb43f59b90f7e46bd23385a028404559c751a6d560376c
REASON    CONFIRMED — non-forgeable witness re-read the world and effect 'soak:unit-3' is PRESENT: os_acceptance
```
```text
exit code: 0
```

**The false claim, REFUTED — the caught-lie moment.** unit-7 carries a `PASS` in
the campaign log, but the claim is checked against the **instrument capture**,
not the log: did the measured max reach the setpoint? It reached 71C, so the
acceptance command fails and the effect is ABSENT:

```bash
dos attest --workspace . --claim soak:unit-7 \
  --narrated "unit-7 completed 168h soak at 125C (PASS in the campaign log)" \
  --accept-cmd "python -c \"import json,sys; d=json.load(open('benches/bench-a/thermal_after.json')); sys.exit(0 if d['unit-7:maxC'] >= d['chamberA:setpointC'] else 1)\"" \
  --key-file benches/bench-a/attest-demo.key --timestamp 2026-06-14T00:00:00Z
```
```text
VERDICT   REFUTED   (believe=False refuted=True)
CLAIM     soak:unit-7
WITNESS   os_acceptance (OS_RECORDED) over python -c "import json,sys; d=json.load(open('benches/bench-a/thermal_after.json')); sys.exit(0 if d['unit-7:maxC'] >= d['chamberA:setpointC'] else 1)"
ALGORITHM HMAC-SHA256
TIMESTAMP 2026-06-14T00:00:00Z
SIGNATURE 24ebb7e725832d70b4f7e155ebb0c3ac8543454903ff7d8c2de3ce72f0737cde
REASON    REFUTED — non-forgeable witness re-read the world and effect 'soak:unit-7' is ABSENT: os_acceptance (a narrated success the world does not corroborate)
```
```text
exit code: 1
```

Claimed "168h soak at 125C"; the instrument witnessed a peak of 71C. The claim
was checked against a surface the claimant cannot write, so the confident `PASS`
in the log changed nothing.

> **`reconcile` vs `attest` — pick the right join.** #97's done-condition names
> "a `reconcile`/effect-witness pass". They are different joins: `dos attest`
> (above) is the **effect-witness** — it re-reads the *world* (an instrument
> capture, a state-snapshot delta) for the rig case. `dos reconcile` joins a
> claim against the **git oracle** (`is_shipped`, the same rung as `dos
> verify`) — right for "the firmware fix landed", wrong for "the chamber hit
> 125C", which git knows nothing about. The rig witness is `attest`.

> **The demo key.** `benches/bench-a/attest-demo.key` is a demo signing key
> shipped so the signatures above reproduce byte-for-byte (fixed key + injected
> `--timestamp` ⇒ deterministic). It is labeled what it is — **not a secret**. A
> real lab generates its own and keeps it where the agent can't read it.

## Where the captures come from on a live farm

The chamber controller — not the agent — writes the instrument capture. On a
real bench the harness that owns the controller reduces the telemetry to the
`{key: value}` shape (`unit-N:maxC`, `chamberA:setpointC`) before and after the
campaign; the agent under adjudication authors zero bytes of it. An
agent-authored capture is `AGENT_AUTHORED` — the forgeable floor — and the join
structurally refuses to CONFIRM from it (same rule as `commit-audit`: the report
is forgeable, the instrument output is not).

And the ceiling, stated plainly: a CONFIRMED here means the effect is present
*in the captured telemetry* — a controller that lies, a miscalibrated probe, or
a capture the agent *can* reach still sits above this floor. A REFUTED is
decisive (the claimed effect is absent from a surface the claimant cannot
write); a CONFIRMED is "no lie this witness can see", not "the silicon is good".
Every verdict is advisory — DOS reports and proposes; the lab decides what a
REFUTED soak gates (a re-run? a quarantined lot?).

---

*Related: [playbook 08](08_driver-bringup-qemu-edu.md) (the QEMU-`edu` instance
of these same three properties on a ring-0 driver), [playbook 02](02_polyglot-web-service.md)
(concurrent disjoint lanes), [playbook 05](05_infra-monorepo.md) (run-alone
exclusive lanes — the other meaning of "exclusive"). Related issues: #97 (this
playbook), #45 (interval/slot claim regions — kernelizing range leases, the
opposite of this playbook's "today's taxonomy already expresses it"), #117/#118
(the `[exec_capability]` table and the named-cluster autopick narration, both
out of this playbook's scope).*
