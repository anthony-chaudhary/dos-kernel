# Does ABA Opinion 512 require me to verify AI-generated citations — and how?

> The duty to verify is non-delegable: you can't hand it to the tool that wrote
> the cite. The checkable half — *does this case exist?* — you can automate
> against a source the AI didn't author. `pip install "dos-kernel[mcp]"`, then
> `citation-resolve`. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

ABA Formal Opinion 512 holds that a lawyer remains fully responsible for
AI-assisted work product and must independently verify its citations and
assertions — the duty does **not** transfer to the AI tool. Courts have applied
the same standard: the obligation to confirm a citation is real is the
attorney's, regardless of what produced it.

That duty has two halves. *Does the cited case exist, and is the quote really in
it?* is **checkable** — you can resolve the cite against a third-party reporter
the AI did not author. *Does the case support your argument?* is **judgment** —
irreducibly the lawyer's. DOS automates the first half and explicitly abstains on
the second. `citation-resolve` (an MCP tool and an exit-code CLI; run `dos doctor`
to list what your install exposes) resolves each cite against CourtListener (Free
Law Project), checks the resolved case *name* matches the parties you claimed, and
where the opinion text is available checks the quoted holding — producing a
`(via citation-resolved)` audit trail that the existence half of the duty was
discharged against a source you didn't write.

## The evidence

The checkable half is scored against the reporter's own bytes, not the AI's
confidence. Measured over a frozen labeled set:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated citations are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** | CourtListener / Free Law Project, a third-party reporter the agent authored zero bytes of | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| Real cases are not wrongly flagged | FALSE-FIRE **0 / 8 = 0.0%** on 8 landmark SCOTUS cases | the reporter's name-search ground-truth path | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — fabricated citations a
sound witness refused to vouch for — never a discharged duty or a won case.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the citation-resolve tool is available
```

A cite the AI invented returns `UNRESOLVED`; a real cite with a fabricated quote
returns `RESOLVED_MISMATCH` — both refused, each with a typed, auditable reason:

```text
UNRESOLVED  925 F.3d 1339 (Varghese v. China Southern Airlines) — no cluster resolves
```

## What this does — and does not — certify

It discharges the **checkable** half of the duty — existence and quote-fidelity,
scored against a source you didn't author — and produces an audit artifact that it
did. It does **not** discharge the **judgment** half: whether the case is good law,
whether it supports your position, whether the argument is sound. Those stay with
the attorney; the tool abstains on them by design. Presenting an existence-check as
"satisfies your professional-responsibility duty" would be the over-claim that, in
this domain, is itself a liability — so the tool is precise about which half it
covers.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated-citation detection study.
- [`docs/279`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/279_citation-resolve-the-legal-tier1-witness.md) — the design note and the `(via citation-resolved)` audit stamp.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify a cited legal case actually exists](how-to-verify-a-cited-legal-case-exists.md) — the existence rung.
- [How to avoid an AI-citation sanction](largest-ai-hallucination-sanction-how-to-avoid.md) — the failure class this duty guards against.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- does ABA Opinion 512 require me to verify AI-generated citations
- a lawyer's duty to verify AI-generated case law citations
- does ABA Opinion 512 require me to verify AI citations
- ABA 512 duty to check AI-generated citations
- lawyer's duty to verify AI case law under ABA 512
- what does ABA Formal Opinion 512 say about AI citations
- am I required to verify AI citations ABA guidance
- ABA 512 and AI citation verification duty
- ABA 512 duty to verify AI citations
- am I required to check AI-generated citations
- what ABA Opinion 512 says about AI case law
- lawyer obligation to verify AI citations

> The kernel is the part that doesn't believe the agents.
