# How to verify a quoted holding actually appears in the cited opinion

> Existence isn't enough — check the quote against the resolved opinion text.
> `pip install "dos-kernel[mcp]"`, then the `citation-resolve` tool checks both.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

A model can cite a real case and still fabricate the *quote* — attributing a
holding to an opinion that never said it. Checking the citation exists is only
the first rung; the second is quote-fidelity: does the quoted holding actually
appear in the resolved opinion? DOS ships `citation-resolve` (an MCP tool; run
`dos doctor` to list what your install exposes) which resolves the citation
string against CourtListener and then, where the full opinion text is available,
checks the quoted holding against it. The verdict comes from the third-party
reporter's bytes, not from how authoritative the quote sounds. It witnesses
existence and quote-fidelity — never whether the holding helps your argument.

## The evidence

The check is scored against a reporter index the model did not author. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated citations (existence + name) are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** | CourtListener / Free Law Project, a third-party reporter | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| A real reporter slot carrying a *different* case is caught, not waved through | collision catch **1 / 1** (a real slot, a fabricated name) | the reporter's resolved case name | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — fabricated citations a
sound witness refused to vouch for — never a won case.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the citation-resolve tool is available
```

The tool's verdict distinguishes "the case exists and the quote matches" from
"the case exists but the quoted holding is not in it":

```text
RESOLVED_MISMATCH — cluster resolves, but the quoted holding is not in the opinion
```

## What this does — and does not — certify

It certifies **existence and quote-fidelity**: the case is real and the words you
quoted are in the resolved opinion. It does **not** certify that the holding
*supports your position* — that is legal judgment, the tier the tool abstains on.
A real, correctly-quoted case can still be the wrong case for your argument.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated/mis-quoted citation study.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify a cited legal case actually exists](how-to-verify-a-cited-legal-case-exists.md) — the existence rung this builds on.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- how to verify a quoted holding actually appears in the cited opinion
- verify a quoted holding actually appears in the opinion
- check that a quote is really in the cited case
- did the AI quote the opinion accurately or invent it
- confirm a holding quote matches the source opinion
- validate a legal quotation against the real text
- AI quoted a case is the quote actually there
- quote-fidelity check for AI legal citations

> The kernel is the part that doesn't believe the agents.
