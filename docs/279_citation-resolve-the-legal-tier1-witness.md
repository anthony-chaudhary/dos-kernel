# 279 — `citation_resolve`: the legal Tier-1 witness (docs/277 §6 experiment #1)

> **Status:** SHIPPED (driver + benchmark + measured replay). This is the build of
> docs/277 §6 **experiment #1** — the legal citation-resolution demo the strategy arc
> (docs/213 §3) names as DOS's cleanest unguarded slot. Every external number is a
> June-2026 web check ([[feedback-date-observations-for-staleness]]); the *measured*
> DOS numbers here are over a **frozen local sample**, stated as such (§4). Companion
> to docs/265 (the non-git evidence-source seam this plugs into), docs/156 (the
> `derived_witness` primitive the quote-match rides), and docs/277 (the benchmark map).

---

## 0. The one line

A cited case either **resolves in a third-party reporter** — bytes the agent authored
zero of — or it does not. That is DOS's *cleanest* rung: not "is the legal argument
sound" (Tier 3 — abstain), but "does this citation **exist**, and does the quote
**match** what the resolved opinion says" (Tier 1 — witnessable). The catastrophic,
*sanctioned* legal-AI failure (the *Mata v. Avianca* class — fabricated citations,
$5,000 sanction, May 2023) lives entirely on this rung, and the field's own verdict
(Harvey-LAB, 2026) is that citation hallucination "is not captured by any benchmark."
This module is the witness that benchmark is missing.

**What it is NOT:** it does not make the argument correct. It witnesses **existence +
quote-fidelity**. A `J` here is a *caught-count* (fabricated cites flagged), never a
won case. Selling it as "DOS verifies legal correctness" is the docs/277 §7 over-claim.

---

## 1. The witness shape — `derived_witness` over a non-forgeable operand

A citation is a pair: **`(case-cite, quoted-holding)`**. The verdict is four-valued
(the `EvidenceStance` honest split, never a binary):

| Verdict | Meaning | `EvidenceFacts` stance |
|---|---|---|
| `RESOLVED_MATCH` | the cite resolves in the reporter AND the quoted holding appears in the opinion text | **ATTESTED** (THIRD_PARTY) |
| `RESOLVED_MISMATCH` | the cite resolves BUT the quote is not in the opinion (mis-quote / put-in-mouth) | **REFUTED** (THIRD_PARTY) |
| `UNRESOLVED` | no reporter cluster carries this citation (the fabrication — *Mata*) | **REFUTED** (THIRD_PARTY) |
| `ABSTAIN` | no corpus access (no token, timeout, rate-limited, network down) | **NO_SIGNAL** (the fail-safe floor) |

The operand whose bytes the agent did not author is the **reporter's citation index**
(Free Law Project's CourtListener). Resolution is `THIRD_PARTY` accountability
(docs/117): the agent under adjudication cannot author a cluster bearing a given
citation string in a public reporter database. So a `RESOLVED_MATCH` is eligible to
grant belief under `believe_under_floor`; an `ABSTAIN` never fabricates one.

The quote-match is the docs/156 `derived_witness` shape: a **declared op**
(`quote-substring-match`) over a **non-forgeable operand** (the resolved opinion text).
The op is committed up front, never reverse-searched to fit — a brute-force "does the
quote appear *somewhere* in *any* opinion" search would be the agent-selection that
forges the rung, and the helper refuses to express it.

---

## 2. The resolver — what is actually free, and the honest corpus choice

CourtListener (Free Law Project) is the free, public US-caselaw API. Two endpoints,
two very different fitness-for-purpose (June-2026 probe):

- **`/api/rest/v4/citation-lookup/`** — the **purpose-built** normalized-citation
  resolver (POST a citation string → matched cluster or `status: 404`). It is the
  *right* resolver. But it **requires a Token** and is rate-limited (5/min, 50/hr,
  125/day). Not usable for an unauthenticated, reproducible batch.
- **`/api/rest/v4/search/?q="<cite>"&type=o`** — **unauthenticated**, returns JSON.
  But it is a **full-text relevance search, not a citation index**: a phrase-quoted
  query for `"347 U.S. 483"` (Brown v. Board) returned `count=0` while
  `"576 U.S. 644"` (Obergefell) resolved cleanly. Its recall on real cites is
  **unreliable** — which would breach the docs/277 false-fire floor if used as the
  ground truth.

**The honest consequence (docs/277 §6 cheap-kill, stated, not narrated):** the live
`/search/` rung is **too noisy to headline a recall number** — a real cite that fails
to surface is a false-fire, and a noisy resolver is worse than none (docs/143
precision discipline). So:

1. The **measured benchmark** scores against a **frozen local sample**
   (`benchmark/legalcite/dataset.py`) whose per-cite ground truth is fixed by careful
   assembly (real cites confirmed present by exact citation-array match via the
   reliable name path; fabrications confirmed absent — the documented *Mata* cites +
   synthesized perturbations). **This is a frozen-sample measurement and is labeled as
   such everywhere it is reported.**
2. The **live driver** prefers `/citation-lookup/` **when a `COURTLISTENER_TOKEN` is
   present** (the purpose-built resolver), falls back to `/search/` exact-citation-array
   match otherwise, and **ABSTAINs** (NO_SIGNAL) on no-token / timeout / rate-limit /
   network error — never a fabricated RESOLVED.

What a live corpus (an authenticated token, or a frozen reporter dump) would add over
the frozen sample is **scale** (thousands of cites) and **live recency** — not a
different *mechanism*. The mechanism is proven on the sample; the number is a sample
number. We do not narrate that "it would work" at scale — we report the sample.

---

## 3. The collision trap — resolution alone is insufficient

A *Mata* fabrication, `92 F.3d 1074` (Hyatt v. N. Cent. Airlines), **resolves** in the
reporter — but to *Grilli v. Metropolitan Life*, a **different real case**. The
fabricator reused a real reporter slot with a wrong case name. So **citation-string
resolution alone would rubber-stamp this fabrication.** The resolver therefore also
checks **case-name agreement** between the claimed name and the resolved cluster's
name (a normalized token-overlap test): a resolved cluster whose name does not match
the claimed party names is `UNRESOLVED` (the citation, *as claimed*, does not exist),
not `RESOLVED`. This is the second non-forgeable operand — the cluster's `caseName`,
also authored by Free Law Project.

---

## 4. The measured result

See `benchmark/legalcite/RESULTS.md` for the committed numbers over the stated
denominator. Headline shape (frozen sample):

- **DETECT recall** = fraction of fabricated cites flagged (`UNRESOLVED`/`MISMATCH`).
- **FALSE-FIRE rate** = fraction of real, matching cites wrongly flagged. The
  falsifiable prediction (docs/277 §6): **0% false-fire** on the frozen sample by
  construction — a real cite whose ground-truth is "present + name-matches" must never
  be flagged. If the sample's real cites fail to resolve, the floor is breached and the
  result says so.

### 4.1 The LIVE-at-scale arm (the frozen→live conversion) — shipped, run is operator-gated

The §2 "what a live corpus would add" — *scale + recency, not a different mechanism* —
is now **built and provable offline**, so the only thing between the frozen number and
a real-world one is a (free) token a human supplies:

- **`benchmark/legalcite/corpus.py`** scales the labeled set from n=18 to **n≈166**:
  the documented *Mata* fabrications + ~110 deterministic synth perturbations (a real
  cite's volume/page moved to a plausible non-existent neighbour — auditable by
  inspection, never an invented "real" case) + ~50 publicly-auditable landmark real
  cites. Grow the REAL set further from **live** ground truth via the operator
  `--harvest` path (the `snapshot.py` pattern), never a hand-asserted cite.
- **`benchmark/legalcite/live_corpus.py`** is the batch runner: it calls the shipped
  `resolve()` per cite against the live `/citation-lookup/` endpoint, with rate-limit
  pacing, resumable checkpointing, and a **three-way DETECT / FALSE-FIRE / ABSTAIN**
  accounting (ABSTAIN excluded from both rate denominators — "could not tell" is never
  a pass nor a flag). A `--transport recorded` mode runs the *same code path* over
  committed reporter bytes with zero network, so the runner is fully tested offline
  (`tests/test_legalcite_live.py`): parity with the frozen verdicts, the §3 collision
  caught through the live path, the ABSTAIN column, and checkpoint/resume.
- **Why the live run is operator-gated, not self-run:** the live arm needs a
  `COURTLISTENER_TOKEN` only a human holds, and a self-certified "live number" without
  the token would be a fabricated measurement — the exact thing this witness exists to
  refuse. So `RESULTS.md`'s LIVE section ships stamped **PENDING-OPERATOR-RUN**; the
  one command that fills it is in `benchmark/legalcite/RUNBOOK.md`. The mechanism is
  shipped; the number lands when the token-holder runs it.

This is the docs/277 §6 #1 experiment moved from **DESIGNED** to **MEASURABLE-at-scale
in one credentialed command** — the cheapest, highest-credibility step from a
frozen-sample proof toward a real-world validation.

---

## 5. Layering — kernel untouched

`drivers/citation_resolve.py` is a **driver** (docs/265): it imports the kernel
(`dos.evidence`), the kernel never imports it (`drivers/__init__` rule). It has the
surface the kernel forbids — network I/O against a third party — exactly as
`ci_status` / `llm_judge` do. The `dos.evidence_sources` entry point registers
`CitationResolveSource` by name (`citation_resolve`). **No `src/dos/*` change is
needed** — the seam was already shipped (docs/265); this is the second occupant of it,
the move-(B) "new artifact oracle for a non-git surface" the seam was built for.

The audit artifact the new AI-disclosure rules demand is the
**`(via citation-resolved)`** stamp: a verdict that every cite in a brief resolved +
quote-matched against a third-party reporter, produced by a witness the filing agent
authored zero bytes of.
