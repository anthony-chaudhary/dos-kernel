# The forgeable success signal — when the safety net grades itself

> **Issue:** #191  
> **Status:** design decision — keep the generic detector in workspace/driver
> policy; use existing evidence verdicts rather than add a new kernel syscall.

## 1. The pattern

A **forgeable success signal** is evidence emitted by the remediation whose
effect it is supposed to prove. A reaper says `reaped=0`, a cleaner says
`completed`, a retry loop says `relaunched=0`, or a suite says `green`; the
user-visible leak, stale row, dead process, or unexercised path remains.

The signal may be factually accurate about the mechanism. It is still not a
witness for the claimed effect. The producer can report only what it attempted
or counted inside its own boundary. It cannot establish that the external state
changed unless that state is independently read back.

The detector shape is therefore:

```text
claim:     remediation R changed effect E
present:   receipt minted by R about R's own execution or counter
missing:   independent observation of E after R
verdict:   UNWITNESSED_EFFECT
```

`success=true`, exit 0, and a near-zero counter do not raise the rung. The
minimum witness is a fresh observation of the effect surface: enumerate the
actual leaked windows, read the row from the database, probe the endpoint,
inspect the process table, or run a test whose failing pre-patch behavior proves
that the changed path was exercised.

## 2. Why repeated “fixes” converge on the wrong layer

A self-grading safety net creates a closed explanatory loop:

1. the symptom appears;
2. a nearby mechanism is changed;
3. that mechanism reports success;
4. the symptom is not independently sampled;
5. “fixed” enters memory as fact;
6. recurrence is treated as a new incident rather than falsification.

This is [docs/103](103_memory-is-an-unverified-agent.md) applied to operations:
the prior diagnosis is another narrator. It is also the throughline in
[docs/138](138_what-is-truth-the-throughline.md): truth comes from structure the
claimant could not author, not confidence or proximity.

The corrective loop has two independent folds:

- **Effect fold:** observe E before and after R from outside R's boundary.
- **Recurrence fold:** cluster repeated BLOCKED/DRAIN/STALLED causes rather than
  nudge each run. The shipped `dos-unstick` skill performs this second fold and
  routes one structural proposal per wedge through `dos decisions`.

Neither substitutes for the other. A recurring-wedge cluster identifies the
layer to inspect; the effect witness proves that the proposed remediation
actually changed it.

## 3. Existing DOS surfaces already express the rule

| Surface | Claim it distrusts | Independent structure |
|---|---|---|
| `dos verify` | “the phase shipped” | git ancestry and the workspace stamp grammar |
| `dos commit-audit` | “this commit adds/fixes/tests X” | the diff shape and touched artifacts |
| evidence verification | “the external effect happened” | an `fs_artifact`, `http_probe`, `os_process`, `provider_ledger`, or other configured source read at the effect boundary |
| `dos-unstick` | “another retry will clear the wedge” | clustered BLOCKED/DRAIN/STALLED run history plus an operator decision |

The operational pattern does not require a new meaning of truth. It requires
choosing the evidence source whose trust boundary contains E but excludes R.
For a process leak, [docs/95](95_os-level-evidence-and-the-proc-liveness-rung.md)
puts the process table above a worker heartbeat. For environment-sensitive
claims, [docs/115](115_the-under-what-axis-environment-and-version-provenance.md)
requires the environment fingerprint so a green result from a different runtime
cannot stand in for the live one.

Two named instances fit the same class:

- **#95 — vacuous-suite/test-run witness.** “Tests passed” is self-description
  when no test exercised the relevant path. The independent effect is a witness
  that the target behavior fails before the patch and passes after it, or an
  execution receipt tied to the touched path.
- **#115 — poison census/environment witness.** A success claim from one
  environment cannot prove another environment is healthy. The runtime or
  provider state must mint the provenance/read-back.

## 4. The concrete operational protocol

For any remediation with a success counter, record this card:

```yaml
claim: "orphan helper processes no longer survive shutdown"
remediator_receipt: "reaper completed; relaunched=0"   # advisory only
effect_source: "OS process census filtered by parent/session identity"
before: 4
action: "patched shutdown ownership boundary"
after: 0
negative_control: "unpatched revision reproduces survivors"
environment: "host/runtime fingerprint"
```

Admission rules:

1. **No effect source, no done claim.** Report `UNWITNESSED_EFFECT`, not success.
2. **Read after the action.** A pre-action census proves the wound, not the cure.
3. **Bind identity and environment.** Count the intended processes/rows/session,
   not a convenient neighboring population.
4. **Prefer a negative control.** Stash/revert → fail, restore → pass distinguishes
   causal coverage from a permanently green checker.
5. **Sweep siblings.** Once the wrong ownership/lifecycle layer is identified,
   inspect other resources governed by that layer.
6. **Audit the commit separately.** `dos commit-audit` can corroborate that the
   diff matches the code claim; it cannot replace the runtime effect read-back.

## 5. Decision: no generic kernel rung

Do **not** add a universal `dos remediation-verify` syscall. The kernel cannot
infer the effect boundary from a remediator's counter, and pretending it can
would recreate the same forgeability one layer lower. “Cleaner success” may
mean a file disappeared, a DB row changed, a window closed, a deploy became
reachable, or a counterparty accepted a message. The independent reader and
identity key are host facts.

Keep the detector as **workspace/driver policy** expressed through DOS's existing
pure-verdict shape:

```python
classify(
    evidence={"remediator_receipt": receipt, "effect_readback": observation},
    policy={"required_effect_source": "os_process", "identity": session_id},
)
```

The deterministic floor is one-sided: absence, staleness, wrong provenance, or
identity mismatch can only refuse (`UNWITNESSED_EFFECT`). A driver may add a
stronger domain-specific check, but it may not promote the remediator's own
receipt into an independent witness.

A follow-on deserves kernel work only if several domains converge on a stable,
data-only evidence envelope that the current evidence verifier cannot express.
Until then, a generic verb would add vocabulary without adding ground truth.

## 6. Done means the symptom was read back

A remediation is done only when all three artifacts exist:

1. the diff/ship artifact (`verify` / `commit-audit`);
2. the independent post-action effect observation;
3. provenance tying that observation to the intended identity and environment.

The safety net's own green counter remains useful telemetry. It is never the
receipt that closes the incident.
