# 348 — DOS for the courts themselves: the inbound-filing auditor

> **Status:** DESIGN + POSITIONING note. It adds **no new mechanism and no new
> measured number** — it reuses the legal-citation witness DOS already ships
> ([docs/279](279_citation-resolve-the-legal-tier1-witness.md), the
> `citation_resolve` driver, the frozen `benchmark/legalcite/` sample) and asks
> one question the prior legal docs did not: *who is sitting in the auditor's
> seat?* Every prior legal artifact puts the **filer** there. This doc puts the
> **court** there — and shows why that single swap is what makes the rung sound
> instead of self-asserted. Companion to [docs/212](212_dos-in-non-coding-domains-the-world-witness-axis.md)
> (the non-agent-consumer thesis this is a worked instance of),
> [docs/246](246_dos-attest-the-portable-signed-receipt.md) (the signed receipt
> a clerk would attach), and [docs/138](138_what-is-truth-the-throughline.md)
> (the over-claim discipline, which in this domain is a legal liability, not just
> a habit).

---

## 0. The one line

A court is the **one consumer for which `byte-author ≠ claimant` is guaranteed
by the institution**, not chosen by the producer. When a law firm runs a
citation check on its own brief, the firm picked the split and can decline it.
When the **court** runs the same check on a filing submitted by an *opposing*
party, the split is structural: the court did not write the filing, and the
reporter index it checks against was authored by neither side. That is the exact
soundness condition the kernel exists to enforce — finally met by an institution
instead of asserted by the party under scrutiny. So the legal-citation witness
DOS already ships is, **read from the bench's side, an inbound-filing auditor**.
This doc is the design for that reading.

---

## 1. Two seats, one witness

The witness is unchanged. What changes is who holds the auditor's seat.

| | **Filer seat** (docs/279 framing) | **Court seat** (this doc) |
|---|---|---|
| Who runs the check | the party that wrote the filing | the court that received it |
| The `byte-author ≠ claimant` split | **self-imposed** — the firm chose to check | **institutional** — the court never authored the filing |
| Can it be declined? | yes — the firm can skip it or ignore the verdict | no — the court runs its own intake, on every filing |
| Is the claimant adversarial to the auditor? | no (same party) | **yes** (opposing party, or a party the court must not trust) |
| What it returns | the same four-valued verdict | the same four-valued verdict |

Same driver (`src/dos/drivers/citation_resolve.py`), same honest four-valued
verdict — `RESOLVED_MATCH` / `RESOLVED_MISMATCH` / `UNRESOLVED` / `ABSTAIN`
([docs/279](279_citation-resolve-the-legal-tier1-witness.md) §1). The kernel is
untouched. The *only* edit to the world is which chair the auditor sits in — and
that edit is the whole point, because soundness was never a property of the
check; it was a property of **who runs it relative to who made the claim**. The
filer running it is consistency (a party agreeing with itself). The court
running it is grounding (a party that did not author the bytes confirming them
against a third party that also did not). DOS has always said that distinction
is the difference between a believed self-report and a witnessed fact
([docs/192](192_the-world-state-witness-ladder-and-the-w2-w3-gap.md), the W3*
gold-provenance axis). The court is where the distinction
stops being a design preference and becomes the structure of the room.

---

## 2. The wedge — the certification is a self-report today

As of mid-2026 at least **25 federal district courts** carry standing orders or
local rules requiring an attorney to certify, on every filing, whether
generative AI was used and — if it was — that **"all legal citations reference
actual, non-fictitious cases."** (Judge Starr, N.D. Tex., issued the first
widely-copied order; a Connecticut judge sanctioned a solo practitioner $500 for
a brief packed with fabricated AI citations.) These are a direct institutional
response to the *Mata v. Avianca* failure class — fake cases cited to a federal
court, a $5,000 sanction, May 2023.

Here is the gap the wedge sits in. **Today that certification is a signature.**
The lawyer signs that they checked; the court must *believe* the signature. In
DOS's vocabulary the certification is a **W0 narration** — generation #2 of bytes
(the signature) about generation #1 (the brief), authored entirely by the party
making the claim. It is precisely the thing the kernel refuses to take on faith.

DOS's contribution is not a new rule — the courts already wrote the rule. It is
to turn the court's own already-mandated certification **from a believed
signature into a verdict the court itself can re-run** against bytes neither it
nor the filer authored. The standing order says "certify the cases are real";
DOS lets the clerk *check* that the cases are real, with a witness the filer
cannot have forged.

This is also the cleanest instance of the
[docs/212](212_dos-in-non-coding-domains-the-world-witness-axis.md) §4
**pre-action gate for an irreversible effect**. A filing entering the docket is
hard to revert — it misleads opposing parties and a possibly pro-se litigant,
wastes court time, and erodes trust in the record (the Connecticut court's own
words). In code, DOS's measured value is detect-and-halt *after* a commit. At a
court's intake desk, the value migrates **before** the filing is accepted, where
being right is cheapest — exactly the domain-distinctive direction docs/212
named and could not find a high-stakes home for. The intake desk is that home.

---

## 3. What carries over unchanged

Nothing in the kernel moves. The court seat is a new *occupant* of machinery
that already shipped:

- **The driver.** `citation_resolve` is already a driver (network I/O against
  CourtListener / Free Law Project lives there, never in the kernel — the
  `drivers/__init__` rule). The court uses it as-is.
- **The four-valued verdict.** The honest split is what a court needs: a binary
  valid/invalid would have to lie in the two cases where there is no clean
  answer — a real case mis-quoted (`RESOLVED_MISMATCH`) and no corpus access
  (`ABSTAIN`).
- **The name-collision guard** ([docs/279](279_citation-resolve-the-legal-tier1-witness.md)
  §3). A *Mata* fabrication can land on a **real reporter slot under a wrong
  case name** (`92 F.3d 1074` resolves — but to *Grilli v. Metropolitan Life*,
  not the claimed *Hyatt*). Citation-string resolution alone would rubber-stamp
  it. The driver checks two operands, both authored by Free Law Project: the
  slot carries the cite **and** the cluster's name agrees with the claimed
  parties. A court needs this guard more than anyone, because the adversary it
  is auditing has every incentive to dress a fabrication in a real-looking slot.
- **`ABSTAIN`-on-no-corpus.** The fail-safe floor: no token, a timeout, a
  rate-limit, a network error — every one degrades to "could not tell," never to
  a fabricated `RESOLVED`. §4 explains why this floor is not just safe but
  *constitutionally required* in the court seat.

The court is the [docs/212](212_dos-in-non-coding-domains-the-world-witness-axis.md)
§5.2 **non-agent consumer arriving for real.** DOS's hardest unsolved problem is
value-capture: a verdict handed back to the *same* agent washes to ~0 on a
capable model, because the agent can route around its own check. The court
cannot route around the court. It is the consumer that does not grade its own
homework — which is the half of the conversion-gap problem code struggles with,
solved here by the structure of the institution rather than by a cleverer gate.

---

## 4. What a court-intake profile would add (design, unbuilt)

These are **host/driver concerns** — a court-intake profile, never a kernel
edit. Naming them as design, not as shipped.

- **Batch over a whole filing, not one cite.** A brief carries many
  `(cite, claimed-name, quoted-holding)` triples. The court-intake profile
  extracts each and resolves it, producing a per-filing **intake report**: N
  resolved-and-matched, M unresolved, K abstained. The denominator is honest by
  construction — the report counts what was **checkable** (a parseable cite
  string), and says so; it never reports coverage it did not have. A filing
  whose citations are images, or in a citation format the extractor could not
  parse, yields abstains, not a clean bill.

- **`ABSTAIN` is load-bearing — it is due process, not a footnote.** A court may
  not strike a party's filing because the *court's* corpus was down, its token
  expired, or the reporter was unreachable. An `ABSTAIN` in the intake report
  must read as **"the court could not verify this — route to a human,"** and may
  never read as "fabricated." This is the existing fail-safe floor restated as a
  judicial requirement: the burden of an auditor's blind spot falls on the
  auditor, never on the audited. DOS's refusal to ever fail *open* (fabricate a
  RESOLVED) and its refusal to ever fail *loud* (call an unverifiable cite fake)
  are the same property the court needs from both directions.

- **`dos attest` as the portable receipt** ([docs/246](246_dos-attest-the-portable-signed-receipt.md)).
  The intake report is a verdict the clerk can **sign and attach to the
  docket** — the `(via citation-resolved)` stamp from
  [docs/279](279_citation-resolve-the-legal-tier1-witness.md) §5, now authored
  by the court rather than the filer. A signed receipt that says "every cite in
  this filing resolved against a third-party reporter on this date, these K
  abstained pending human review" is an audit artifact the new disclosure rules
  effectively demand and currently have no mechanism to produce.

- **The scope fence (said twice on purpose).** It witnesses **existence +
  quote-fidelity** — Tier 1. It does **not** judge whether a real,
  correctly-quoted case actually *supports the argument it is cited for* — that
  is legal reasoning, Tier 3, where the tool **abstains by design.** A court
  using this to flag fabricated citations is using it correctly; a court (or a
  vendor) presenting it as "DOS verified the brief is legally sound" is the
  [docs/138](138_what-is-truth-the-throughline.md) over-claim — and in this
  domain that over-claim is a **liability**,
  because it would invite a litigant to treat a clean citation report as a
  ruling on the merits. The fence is stated here, and again on the answer page,
  and again in the report's own header. Existence + quote-fidelity, never
  correctness.

---

## 5. The honest caveats (the docs/138 discipline, mandatory here)

1. **No new measured number, by rule.** The measured results — DETECT recall
   **10/10** over a labeled set, **0/8** false-fire on landmark SCOTUS cases —
   are over the **frozen sample** (`benchmark/legalcite/RESULTS.md`) and belong
   to the **mechanism**. They are the same numbers the filer-side pages carry.
   This doc invents no "courts caught N" figure; there is no court deployment to
   measure, and manufacturing one would be the exact over-claim §4 fences. The
   court contribution is **who runs the witness**, not a new benchmark.

2. **Corpus reach is the real limit.** CourtListener covers US case law. The
   purpose-built `/citation-lookup/` resolver needs a token and is rate-limited;
   the unauthenticated `/search/` rung is noisier and falls to `ABSTAIN` more
   often ([docs/279](279_citation-resolve-the-legal-tier1-witness.md) §2). A
   serious court deployment therefore implies a **reliable corpus** — an
   authenticated token, or a local reporter mirror — and inherits that corpus's
   jurisdictional coverage. State-court and unpublished-opinion coverage is a
   corpus question, not a mechanism question, and a court evaluating this should
   evaluate the corpus first.

3. **This is design + positioning, not a live result.** The map says *where the
   value is* (the court seat makes the rung sound); it does not prove a court
   *will adopt it*. The project's own hardest lesson is that only a live loop
   changes an outcome. The cheapest datum that would move the question is §6.

---

## 6. The cheapest decision-relevant experiment

A **$0, offline court-intake replay** over the existing frozen sample, read as
the court would read it — no new corpus, no new number, no network.

Take the labeled cites in `benchmark/legalcite/` (the documented *Mata*
fabrications + the real landmark cases), run each through
`citation_resolve.classify` on the committed fixtures, and render the result as
a **court intake report**: *flagged* (UNRESOLVED / MISMATCH), *clean*
(RESOLVED_MATCH), *abstained* (no corpus). It reuses the verdict already proven
on the sample and simply re-projects it into the bench's seat — the same move
[docs/279](279_citation-resolve-the-legal-tier1-witness.md) §4 makes for the
filer, with the report framed for a clerk. It proves the transfer mechanically
(the report a court would see), costs nothing, and is the building block for a
runnable `examples/court_intake/` demo if the intake profile is later built.

If a *live* signal is wanted on top of the replay, the unauthenticated path
already works end-to-end with no token — `925 F.3d 1339` (the *Mata*
"Varghese v. China Southern Airlines") returns `UNRESOLVED` against the live
`/search/` endpoint today. That is the catch the court needs, demonstrable now;
the token only buys recall and scale, not a different mechanism.

---

## 7. Strategy pointer

Who buys this, how a court or a state administrative office of courts would
procure it, pricing, and the competitive framing against the incumbent citators
(Westlaw KeyCite, Lexis Shepard's) live in the private strategy repo
(`dos-private`), not here — the same split docs/212 follows. This doc is the
mechanism-and-positioning note; the market case is downstream of it.

---

*Part of the legal arc: [docs/279](279_citation-resolve-the-legal-tier1-witness.md)
(the witness), [docs/264](264_paper-citations-modular-plan.md) (the paper-citation
sibling), and the answer pages
[how-to-verify-a-cited-legal-case-exists](answers/how-to-verify-a-cited-legal-case-exists.md),
[verify-a-quoted-holding-appears-in-the-opinion](answers/verify-a-quoted-holding-appears-in-the-opinion.md),
[open-source-ai-legal-citation-checker](answers/open-source-ai-legal-citation-checker.md)
(the filer seat) and
[how-a-court-can-audit-ai-citations-in-filings](answers/how-a-court-can-audit-ai-citations-in-filings.md)
(the court seat).*
