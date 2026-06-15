# docs/353 — the provider-ledger rung: the THIRD_PARTY band

> **Status:** shipped. Driver `src/dos/drivers/provider_ledger.py`, registered in
> `dos.evidence_sources`, wired into `dos attest --ledger`, 26 driver tests green.
> (The CODE is the ship, witnessed by tests + `dos commit-audit`; this 1-file doc
> reads NOT_SHIPPED via `none` on `dos verify docs/353 P1` — an evidence-horizon
> honesty, exactly like docs/349 P1.)

## The one sentence

The witness-ladder benchmark named **content_diff** (docs/349, the OS_RECORDED
"the value is *right*" band) as the largest unbuilt witness, then **provider_ledger**
(the THIRD_PARTY "external effect on a different principal's ledger" band) as the
next one — this builds it.

## Where it sits on the ladder

docs/261's growth table decomposes the floor abstain band into named, buildable
witnesses (each a DOS **driver**, an `EvidenceSource` outside the kernel):

| witness driver | rung | abstain class it converts | exists? |
|---|---|---|---|
| git-ancestry presence (`verify`) | W2 / floor-plus | "a file changed" | yes |
| env DB-hash / assertion engine | W3 `OS_RECORDED` | state-invariant (money/inventory) | yes |
| content-diff (docs/349) | W3 `OS_RECORDED` | "the value is *right*" | **yes (just shipped)** |
| **provider ledger** | W3 **`THIRD_PARTY`** | **external-effect (different principal)** | **yes (this doc)** |
| (irreducible) | — | judgment/quality/taste | never — JUDGE/HUMAN |

`provider_ledger` is the band content_diff structurally **cannot** reach. A committed
blob is git-authored — content_diff tops out where the agent's own repo tops out. But
"I charged the customer" is recorded on the *payment processor's* books, "I sent the
SMS" on the *gateway's*, "I provisioned the VM" on the *cloud's*. The agent under
adjudication authored none of those rows. That is exactly the docs/85 §1
accountability climb: **mutable third-party state on infrastructure the agent does
not control** is a strictly more accountable referent than anything in the agent's
own tree. The witness-ladder harness already encodes this — it maps
`provider_ledger → Accountability.THIRD_PARTY` (`benchmark/witness_ladder/harness.py`,
`_WANT_MIN_RUNG`), one rung above content_diff's OS_RECORDED, and tags 9 of its 100
synthetic tasks `provider_ledger`. The benchmark PROVED the band converts at the
THIRD_PARTY rung; this driver realizes it. (Same division of labor as content_diff:
the benchmark models the band, the driver is the real ship — no harness edit.)

## The witness mechanism — a keyed lookup against a different-principal ledger

The agent claims it made an external effect under a **reference** (`ch_1a2b`, an SMS
sid, an instance-id). The driver looks that reference up in a ledger and checks the
claimed fields are present and equal. The reference is the un-forgeable hook: the
agent NAMES the row; the host's captured ledger either has that row with those fields
or it does not. The agent cannot make a row it did not create resolve to its claimed
reference.

This is genuinely new value over content_diff, not a re-skin: content_diff does
whole-blob byte/digest equality; provider_ledger does a **keyed lookup by reference
into a structured record + a field-subset match** — the shape an "external effect on
a different principal's ledger" actually takes.

### Subject grammar — `'<ref>@<ledger>#<expect>'`

- `<ref>` — the provider reference the agent claims (the row key into the ledger).
- `<ledger>` — where the record lives (the evidence operand):
  - `<path>` / `<path>@<sha>` → a JSON/JSONL ledger read from a **committed blob**
    (git-authored → `OS_RECORDED`; the host captured the provider's webhook/API dump
    and committed it). `@WORKTREE` → the live file → forgeable floor (actor==witness).
  - `source:<name>:<subj>` → an abstract delegation to a host-wired provider poller
    exposing `ledger_bytes(subject)`, at the rung **it** declares (a live charge
    lookup → genuine `THIRD_PARTY`).
- `<expect>` — the expected fields `k=v,k2=v2` (the gold operand):
  - `inline:`/`plan:` → the agent grades its own homework → **AGENT_AUTHORED floor**.
  - `source:<name>:<subj>` → a host-pinned expectation (`expected_fields(subject)`),
    at the validator's rung.

## The soundness rule (the load-bearing part — lifted from content_diff one rung)

The verdict is a single `evidence.derived_witness` over **two operands** (the record
and the expectation) under a DECLARED op `"ledger-match"` — never reverse-searched
(a "does the row match SOME expectation?" search IS the agent-selection that forges a
rung, which `derived_witness` refuses to express). The derived rung is
`min(record-rung, expect-rung)`:

- host-captured committed (or `source:`) record **+** host-pinned expectation →
  `OS_RECORDED`/`THIRD_PARTY` → eligible to grant belief.
- an agent-supplied `inline:` expectation, **or** a `@WORKTREE` record the agent
  wrote this turn → capped at `AGENT_AUTHORED` → `believe_under_floor` refuses belief
  **by construction**. The "agent grades its own homework" hole closes structurally,
  not with a host knob.

Stances (the three-valued honest split):

- row found, every expected field matches → **ATTESTED** at `min(record, expect)`.
- row found, a field disagrees → **REFUTED** (a positive disconfirmation: "you
  claimed `ch_1a2b` charged 5000; the ledger says 4200"). A forgeable-operand REFUTE
  is recorded as a REFUTED *stance* but cannot redden `believe_under_floor` on its
  own (the symmetric floor rule — the agent could have authored the disagreement
  too); only a **non-forgeable** disagreement reddens. Both behaviors are pinned by
  tests.
- ref absent / ledger unreadable / unparseable subject → **NO_SIGNAL** (abstain).
  Absence-from-a-partial-ledger is *not* disconfirmation — an effect genuinely made
  may simply not be in THIS captured ledger, so we never fabricate a REFUTE that
  would fail an honest effect.

## Why it stays domain-free (the litmus)

No payment processor, gateway, or cloud is named anywhere in the driver or the
kernel — the `dos commit-audit` / vendor-agnostic-kernel litmus stays green. The
built-in concrete path is a host-captured ledger committed into git (vendor-free,
offline-testable); a *live* provider is reached only through the abstract `source:`
seam a **host** wires. A vendor name, if it ever appears, lives in that host's
plugin, never here — exactly the docs/93 move-B driver-oracle posture content_diff
and ci_status already take.

## Shape & layering

A `drivers/*.py` `EvidenceSource` (it does the I/O the kernel forbids: `git
cat-file`, reading a ledger, resolving a host validator). It imports the kernel
(`derived_witness`, `believe_under_floor`, `resolve_evidence_source`, `EvidenceFacts`,
`Accountability`); the kernel never imports it (the `drivers/__init__` one-way rule).
Class `accountability = THIRD_PARTY` is the CEILING; the per-call rung is capped below
by `derived_witness`. Advisory: it reports a verdict, it reverts nothing.

## Try it

```bash
# a host-captured ledger committed to git + an inline (forgeable) expectation:
#   ATTESTED, but at the floor — NOT believed (the agent chose the expectation).
python -m dos.drivers.provider_ledger 'ch_1a2b@receipts.jsonl@HEAD#inline:status=succeeded,amount=4200'

# a reference absent from the ledger → abstain (exit 3), never a fabricated refute.
python -m dos.drivers.provider_ledger 'ch_NOPE@receipts.jsonl@HEAD#inline:status=succeeded'

# the same, folded into a signed effect-receipt:
dos attest --claim 'charge ch_1a2b succeeded' \
  --ledger 'ch_1a2b@receipts.jsonl@HEAD#source:my_processor_poll:ch_1a2b'
```

The next growth band from the same roadmap is the irreducible **judge** slice —
which, by design, is *not* a driver: it punts to JUDGE/HUMAN (the principled floor
that bounds the buildable backlog so it is finite, not a treadmill).
