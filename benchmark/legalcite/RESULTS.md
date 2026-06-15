# legalcite — does DOS catch fabricated/mis-quoted legal citations?

<!-- dos-bench-stamp: kernel=0.18.0 sha=324d595 date=2026-06-09 -->

**docs/277 §6 experiment #1 · docs/279 · captured 2026-06-09 · $0 frozen-corpus replay**

> **The one-line result:** over a labeled set of **18 citations** (8 real, 10
> fabricated) resolved against a **third-party reporter** (CourtListener / Free Law
> Project — bytes the agent authored zero of), the shipped `citation_resolve`
> classifier achieved **100% DETECT recall** (10/10 fabrications flagged) at **0%
> FALSE-FIRE** (0/8 real cites wrongly flagged). The docs/277 §6 falsifiable
> prediction — a measurable detect slice at ~0% false-fire — **held**.

## What this measures (and what it does NOT)

This is a **Tier-1** witness: it answers *does the cited case exist in a reporter, and
does the quote match the resolved opinion?* It does **not** make the legal argument
correct (Tier 3 — abstain). The headline number `J=10` is a **caught-count** — ten
fabricated citations of the *Mata v. Avianca* class that a sound witness refused to
vouch for — **never a won case**. Selling this as "DOS verifies legal correctness"
would be the docs/277 §7 over-claim, and in this domain an over-claim is a liability.

## The denominator (stated, per the discipline)

| Set | n | Provenance |
|---|---|---|
| **Real cites** (must NOT flag) | 8 | Landmark SCOTUS cases (Obergefell, Roe, Miranda, Plessy, Marbury, Lawrence, Bush v. Gore, Citizens United), each confirmed present in the reporter via the name-search ground-truth path |
| **Fabricated cites** (must flag) | 10 | **4 documented *Mata v. Avianca*** hallucinations (Varghese 925 F.3d 1339, Zaunbrecher 772 F.3d 1278, Hyatt 92 F.3d 1074, Gen. Wire Spring 556 F.2d 713) + **6 synthesized** perturbations (a real cite's volume/page/reporter nudged to a plausible non-existent neighbour) |
| — of which **collisions** | 1 | `92 F.3d 1074` is a *real reporter slot* that resolves to a **different** case (Grilli v. Metropolitan Life) — a fabricated name on a real slot (docs/279 §3) |

## The numbers

| Metric | Value |
|---|---|
| **DETECT recall** (fabrications flagged) | **10 / 10 = 100.0%** |
| **FALSE-FIRE rate** (real cites wrongly flagged) | **0 / 8 = 0.0%** |
| **collision catch** (real-slot/fake-name) | **1 / 1** |

Reproduce: `python -m benchmark.legalcite.harness` (the false-fire floor is the
harness's own exit gate — a breach exits non-zero).

## This is a FROZEN-SAMPLE measurement — said plainly

Per docs/279 §2, the **measured number is over a frozen local sample**, not a live
batch, and that is a deliberate, honest choice:

1. **The free, unauthenticated resolver is too noisy to be live ground truth.**
   CourtListener's `/search/` endpoint is a full-text *relevance* search, not a
   citation index: a bare-cite query for `347 U.S. 483` (Brown v. Board) and
   `567 U.S. 519` (NFIB v. Sebelius) **failed to surface the canonical cluster on the
   top page** — they were dropped from the sample for exactly this reason (and that
   drop is itself recorded here, not hidden). Using live `/search/` as the ground
   truth would have **breached the false-fire floor** (4/6 landmark cases MISS),
   which is the docs/277 cheap-kill: *a noisy resolver is worse than none.*
2. **So the ground truth is established reliably (name-search → true citation array),
   frozen as the third-party bytes, and the classifier scored against that.** The
   frozen clusters (`frozen_corpus.json`) are what `classify()` runs over — the
   verdict is a pure function of bytes Free Law Project authored, replayable forever
   with no network and no rate-limit.

**What a live corpus would add:** *scale* (thousands of cites, not 18) and *recency* —
**not a different mechanism.** The purpose-built `/citation-lookup/` endpoint (token,
rate-limited 5/min) is the reliable live resolver; the driver uses it automatically
when `COURTLISTENER_TOKEN` is set, falling back to `/search/` and **abstaining**
(NO_SIGNAL) on no-token/timeout/rate-limit — never a fabricated RESOLVED. We do **not**
narrate that "it would scale" — we report the sample (n=18) and name the path to more.

## LIVE at-scale (operator-run) — PENDING-OPERATOR-RUN

> **Status: PENDING.** The mechanism for the at-scale live run is **shipped and
> tested offline** — `corpus.py` (n≈166 labeled cites: 52 publicly-auditable real +
> 4 documented *Mata* + ~110 generated synth perturbations) and `live_corpus.py`
> (the batch runner that calls the shipped `resolve()` against the live
> `/citation-lookup/` endpoint, with rate-limit pacing, resumable checkpointing, and
> a three-way DETECT / FALSE-FIRE / **ABSTAIN** accounting). What is missing is a
> **credential only a human holds** — a free `COURTLISTENER_TOKEN`. An agent must
> not self-certify a live number it could not run, so this block stays PENDING until
> a token-holder fills it in. **No number is claimed here that was not measured.**

The offline proof that the live code path is correct (same path, recorded reporter
bytes, zero network/token):

```text
$ python -m benchmark.legalcite.live_corpus --transport recorded
  REAL cites:          52  (8 reachable, 44 abstained)
  FABRICATED cites:   114  (114 reachable, 0 abstained)
  DETECT recall    = 114/114 = 100.0%   (fabrications flagged, over REACHABLE)
  FALSE-FIRE rate  = 0/8 = 0.0%         (real cites wrongly flagged, over REACHABLE)
  ABSTAIN          = 44                 (no recorded bytes offline — excluded from both rates)
  ✓ FALSE-FIRE FLOOR HELD (0% over reachable)
```

The 44 offline ABSTAINs are honest: with no recorded bytes for the extra landmark
cites, the runner says "could not tell" rather than vouch — exactly what a missing
token does live. **With a token, those 44 become reachable and the denominators grow.**

To produce the real-world number, run the one command in
[`RUNBOOK.md`](RUNBOOK.md) and replace this block with the headline (template):

```text
LIVE — captured <DATE>, CourtListener /citation-lookup/ (token), kernel <SHA>
  REAL cites reachable:        <n>   ABSTAIN: <n>
  FABRICATED cites reachable:  <n>   ABSTAIN: <n>
  DETECT recall   = <d>/<n> = <pct>%   (over REACHABLE)
  FALSE-FIRE rate = <f>/<n> = <pct>%   (over REACHABLE)
```

State ABSTAIN separately; never fold it into either rate; never report an unrun number.

## One bug found and fixed during the run (the honest part)

The first replay showed a **12.5% false-fire** — Obergefell (the one real cite carrying
a quote) was flagged `RESOLVED_MISMATCH`. Diagnosis: the quote rung was refuting on the
quote's absence from a **search snippet** (the opening ~400 chars), but the holding
sentence is on a later page. **Refuting on partial evidence is unsound** (docs/277
precision discipline). Fix (`citation_resolve.py`, `ResolvedCluster.text_is_full`): the
quote rung may only REFUTE a mis-quote against the **full opinion**; against a snippet
it ABSTAINs on the quote and stands on existence alone. After the fix: 0% false-fire,
and `RESOLVED_MISMATCH` still fires correctly on full-text mis-quotes (pinned by
`tests/test_citation_resolve.py::test_quote_mismatch_only_on_full_text`).

## The mechanism (why the recall is non-forgeable)

A fabricated citation is caught two ways, **both** reading bytes the agent did not
author:

1. **No reporter carries the cite** → `UNRESOLVED` (the pure-invention *Mata* case:
   `925 F.3d 1339` returns nothing). The agent cannot author a cluster bearing a given
   citation string in a public reporter database.
2. **The cite resolves to a DIFFERENT case than claimed** → `UNRESOLVED` (the collision:
   `92 F.3d 1074` is a real slot, but it is Grilli, not the claimed Hyatt). The
   cluster's *case name* — also a Free Law Project byte — is the second operand.

Mapped onto the docs/265 evidence seam: a fabrication is a **non-forgeable REFUTED**
(`THIRD_PARTY`) that can **redden** a `verify` of "I cited this case"; a real match is an
**ATTESTED** that grants belief under `believe_under_floor`; no corpus access is a
**NO_SIGNAL** that never fabricates either. The audit artifact the new AI-disclosure
rules demand is the **`(via citation-resolved)`** stamp — produced by a witness the
filing agent authored zero bytes of.

## Caveats (the docs/277 §7 discipline, applied here)

1. **n=18 is a sample, not a population.** The recall is a *sample* recall; scale is the
   live-corpus arm's job.
2. **A `J` is a blocked-count, never a downstream ΔB.** This measures *what it caught*,
   not whether a downstream outcome improved (the certifying attorney's filing, the
   sanction avoided). ΔB needs a live loop with a consumer that can't re-verify
   ([[project-dos-keystone-deltaB-needs-validation]]).
3. **The Tier-3 ceiling is hard.** DOS witnesses existence + quote-fidelity and abstains
   on whether the cite *supports the argument* — that is a gestalt question.
4. **Two landmark cases were dropped** (Brown, NFIB) because the free resolver could not
   reliably surface them — an honest corpus-gap, recorded, not hidden. A token resolver
   would not have this gap.
