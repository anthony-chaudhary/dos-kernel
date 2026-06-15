# 349 — The content-diff rung: the W2→W3 climb, shipped

> **The one-line claim.** docs/192 proved DOS's flagship non-forgeable
> ship-witness is a **W2 presence** check — it proves *a commit touched file X*
> (`git log --oneline -- <path>`), never *X's content is the value it should be*.
> The witness-ladder benchmark (docs/261) then ranked the unbuilt witnesses by how
> much abstain-band each would convert, and `content_diff` was the **largest unbuilt
> domain-free band** (13 of the roadmap's 69 buildable tasks, vs `provider_ledger`'s
> 9 which is inherently provider-specific). This note records that the content-diff
> rung is now **built** — a pure `EvidenceSource` driver that climbs the kernel's
> most-used witness from W2 to W3, with the gold-provenance soundness discipline of
> docs/192 §4 enforced *by construction* rather than by a host knob.

*Status: SHIPPED. `src/dos/drivers/content_diff.py` + `tests/test_content_diff.py`
(20 tests) + the `dos.evidence_sources` registration + the `dos attest --content`
boundary. No kernel edit — it rides the docs/265 socket. Adjudicate with
`dos verify --workspace . docs/349 P1`.*

## 1. What was missing (the gap docs/192 named, the band docs/261 measured)

`verify`'s strongest non-forgeable rung reads WHICH files a commit touched. The agent
authored the file *bytes* and chose the *commit*; only which-files-moved is
git-authored and tamper-evident. So the rung is conditionally sound at **W2**: "a real
commit touching the named cluster exists." It cannot catch *"the agent wrote the WRONG
content to the right file"* — the confident-wrong-mutation class (docs/177's 95/203).
Catching that needs a **content-vs-gold diff** the presence rung does not perform
(docs/192 §7.4).

The witness-ladder benchmark made the gap a number. Live run (2026-06-15):

```
buildable abstain band, by wanted-witness:
  state_invariant  27   (exists — tau2/agentdiff drivers)
  presence         20   (exists — verify)
  content_diff     13   ← largest UNBUILT domain-free band
  provider_ledger   9   (unbuilt, but provider-specific → low reuse)
  judge             7   (irreducible — punts to JUDGE/HUMAN)
```

`content_diff` is the highest-leverage build: it converts the largest unbuilt
domain-free band AND it is the W2→W3 climb on the kernel's *most-used* witness, so its
value multiplies across every `verify`/`attest` call rather than serving one provider.

## 2. The design — a derivation over two operands, capped at the weakest

A content-diff is a comparison, and docs/192 §4 proved it is sound only when BOTH
halves have byte-author ≠ judged-agent:

1. the **evidence blob** — read via `git cat-file blob <sha>:<path>`. git's store is
   content-addressed and tamper-evident, so the agent cannot make `<sha>:<path>`
   resolve to bytes other than the ones it committed. Always non-forgeable (a
   *working-tree* read is not — actor==witness — so it is capped at the floor).
2. the **gold** — the expected value. The load-bearing operand (docs/192 §4 W3*):
   env-invariant (A) and human-out-of-band (B) are sound; agent/plan-supplied (C) is
   forgeable (the agent grades its own homework); a verifier-agent (D) is advisory.

The implementation does **not** hard-code which gold forms it trusts (the operator's
steer: *"abstract third-party validation that may trust that boundary"*). It expresses
the comparison through the existing kernel primitive `evidence.derived_witness`
(`op="byte-eq"` / `"sha256-eq"`, never reverse-searched), which **caps the derived rung
at `min(operands)`**. A forgeable gold therefore caps the whole content-diff at
`AGENT_AUTHORED`, which `believe_under_floor` then structurally refuses to believe —
the "agent grades its own homework" hole closes *by construction*, not by a knob. This
is `derived_witness`'s reason for existing (the grounded-RAG laundering hole, docs/156),
re-aimed at content verification.

### The gold-ref grammar

| `<gold-ref>` form          | provenance (docs/192 §4)     | operand rung           |
|----------------------------|------------------------------|------------------------|
| `sha256:<hex>`             | (A) env-invariant checksum   | `OS_RECORDED`          |
| `source:<name>:<subject>`  | abstract host-wired validator | the rung it declares  |
| `inline:<value>` / `plan:` | (C) agent/plan-supplied      | `AGENT_AUTHORED` (floor) |

`sha256:` is sound because the agent cannot produce bytes whose digest equals a number
it did not choose. `source:` resolves a named `dos.evidence_sources` validator and
**trusts the rung that boundary declares** — the docs/93 move-B driver-oracle posture,
applied one level deeper (the gold is itself a pluggable, accountability-tagged source;
a validator exposing `gold_bytes(subject)` is compared byte-wise at its rung, else its
own attest/refute stance IS the verdict). `inline:`/`plan:` are present so the
**demotion is provable** — never to be believed.

## 3. The stance grammar (the W2→W3 value made visible)

  * blob == gold → **ATTESTED** at `min(blob, gold)` rung. Believed iff that rung is
    non-forgeable — the climb: "the content IS the right value."
  * blob != gold → **REFUTED** — a positive disconfirmation: "the file changed, but to
    the WRONG content." Distinct from "no signal", and the value a presence rung can
    never produce.
  * unreadable blob / unreachable gold / unparseable subject → **NO_SIGNAL** — abstain,
    never a fabricated REFUTE that would fail an honest commit.

## 4. Layering — pure driver, no kernel edit

The driver lives in `src/dos/drivers/`, imports the kernel, and the kernel never
imports it (the `drivers/__init__` litmus). All `git`/`hashlib`/`subprocess` I/O is
inside `gather`'s boundary (the `os_acceptance.gather` / `ci_status.gather` rule). It
reuses three shipped kernel primitives — `evidence.derived_witness` (the rung cap),
`EvidenceFacts.{attest,refute,no_signal}`, and `resolve_evidence_source` (for `source:`
golds) — and rides the already-wired docs/265 `dos.evidence_sources` socket. The
`oracle`/`evidence` modules never learn the string `content_diff`. No host name, no new
T1-runtime edit, the vendor-agnostic-kernel litmus stays green.

## 5. The honest frame

This rung witnesses **byte-identity to a fixed value**, not **semantic correctness**: a
match proves the content equals the gold the host pinned, never that the function is
right (Rice — docs/183; green-on-wrong-tests is still forgeable — docs/85). Its
soundness is exactly the gold's provenance: a `sha256:` invariant or a sound `source:`
validator climbs to W3 belief; a plan-supplied gold is recorded and shown but refused
belief. That is the W2→W3 climb done honestly — the value rises with the gold's
provenance, and bottoms out at the forgeable floor by construction, the same discipline
the witness-ladder benchmark measures the kernel by.

## 6. See also

- docs/192 — the world-state witness ladder; §3 (verify is W2 not W3), §4 (the W3*
  gold-provenance sub-ladder), §7.4 (the content-diff rung as the prescribed fix).
- docs/261 — the witness-ladder benchmark; the roadmap that ranks `content_diff` as the
  largest buildable domain-free band.
- docs/265 — the non-git evidence seam this rung plugs into (the socket, now occupied by
  a fifth witness).
- docs/156 — `derived_witness`, the rung-cap primitive that makes the gold-provenance
  discipline structural.
- `src/dos/drivers/state_diff.py` — the sibling read-back witness whose driver shape
  (instance accountability, `witness_effect` join) this follows.
