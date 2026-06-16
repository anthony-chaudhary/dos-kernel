# How a court can audit AI-generated citations in filings it receives

> Re-run the certification you already require: `pip install "dos-kernel[mcp]"`,
> then the `citation-resolve` tool checks each cited case against a third-party
> reporter — a witness neither the court nor the filer authored. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

The standing orders that ≥25 federal courts now carry make an attorney *certify*
that "all legal citations reference actual, non-fictitious cases." Today that
certification is a **signature** — a self-report the court has to take on faith.
You can re-run it as a verdict instead.

You did not write the filing in front of you. That is the whole point: the party
that submitted it is the one whose claim is under scrutiny, and a reporter index
(the Free Law Project's CourtListener) is a witness whose bytes **neither you
nor the filer control.** When a firm checks its own brief, it is agreeing with
itself. When the *court* checks a filing from a party it does not represent, the
auditor and the claimant are categorically different principals — which is the
condition that makes the check *sound* rather than self-asserted.

DOS ships `citation-resolve` (an MCP tool; run `dos doctor` to list what your
install exposes). For each cite it checks two things against the reporter: that
the citation **resolves** to a real cluster, and that the cluster's **case name
matches** the claimed parties (a real reporter slot carrying a *different* case
is itself a documented fabrication pattern — the *Mata v. Avianca* class). It
witnesses existence and quote-fidelity. It does **not** judge whether the case
supports the argument — that is the court's job, not the tool's.

## The evidence

The verdict comes from a third-party reporter, not from the signature on the
filing or how authoritative the cite looks. These numbers measure the **witness
mechanism** over a frozen labeled sample — they are the same numbers the
filer-side pages carry; the court contribution is *who runs the witness*, not a
new benchmark:

| Claim | Number | Witness (byte-author ≠ filer ≠ court) | Source |
|---|---|---|---|
| Fabricated citations are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** over a labeled set (4 documented *Mata v. Avianca* hallucinations + 6 synthesized) | CourtListener / Free Law Project, a third-party reporter neither party authored | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| Real cases are not wrongly flagged | FALSE-FIRE **0 / 8 = 0.0%** on 8 landmark SCOTUS cases | the reporter's name-search ground-truth path | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — fabricated citations a
sound witness refused to vouch for — never a won case and never a "courts caught
N" number. There is no court deployment to measure; inventing one would be the
over-claim this page is written to avoid.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the citation-resolve tool is available
```

The tool returns a typed verdict per cite — `RESOLVED_MATCH` when the case is
real and the name agrees, `UNRESOLVED` when no reporter carries it (or a real
slot carries a different case). The documented *Mata* fabrication, checked
against the live reporter with no token, returns:

```text
UNRESOLVED  925 F.3d 1339 (Varghese v. China Southern Airlines) — no cluster resolves
```

## What this does — and does not — certify

It certifies **existence and quote-fidelity**: the cited case is real and the
quoted holding appears in the resolved opinion. It does **not** certify that the
case *supports the party's argument* — that is the court's judgment, the tier
this tool deliberately abstains on. A clean citation report is not a ruling on
the merits.

Two boundaries a court must hold:

- **`ABSTAIN` is "review by a human," never "fabricated."** If the corpus is
  unreachable, the token expired, or the cite could not be parsed, the verdict
  is `ABSTAIN` — the tool refuses to call a cite fake when it could not check.
  A filing must never be struck because the *court's* auditor had a blind spot;
  the burden of that blind spot falls on the auditor, not the audited.
- **It flags fabrications, it does not decide the case.** Presenting an
  existence check as "verified the brief is legally correct" would invite a
  litigant to treat it as a finding on the merits. It is not. Existence +
  quote-fidelity, never correctness.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated-citation detection study.
- [`docs/348`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/348_dos-for-the-courts-the-inbound-filing-auditor.md) — the court-as-inbound-auditor design note (why the court seat makes the rung sound).
- [`docs/279`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/279_citation-resolve-the-legal-tier1-witness.md) — the witness mechanism and the name-collision guard.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface (`citation-resolve` and the others).
- [How to verify a cited legal case actually exists](how-to-verify-a-cited-legal-case-exists.md) — the filer-seat version of the same witness.
- [Verify a quoted holding appears in the opinion](verify-a-quoted-holding-appears-in-the-opinion.md) — the quote-fidelity rung.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- how a court can audit AI-generated citations in filings
- court-side check for AI citations in submitted briefs
- audit inbound filings for fabricated AI citations
- how do courts verify citations in filings they receive
- screen filings for hallucinated case law
- a court's process to catch AI-invented citations

> The kernel is the part that doesn't believe the agents.
