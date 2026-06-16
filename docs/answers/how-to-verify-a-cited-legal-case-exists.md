# How to verify a cited legal case actually exists before filing

> Resolve the citation against a third-party reporter before you rely on it:
> `pip install "dos-kernel[mcp]"`, then the `citation-resolve` tool checks the
> cite against CourtListener. The PyPI name is `dos-kernel` — the bare `dos`
> package is an unrelated squatter; never install that.

## The short answer

The *Mata v. Avianca* failure is an LLM inventing a real-looking citation —
`Varghese v. China Southern Airlines, 925 F.3d 1339` — that no reporter carries.
You catch it by resolving the citation string against a reporter index whose
bytes the model did not author: the Free Law Project's CourtListener. DOS ships
this as the `citation-resolve` MCP tool (run `dos doctor` to list the tools your
install exposes). It checks two things — that the citation *resolves* to a real
reporter cluster, and that the cluster's case *name* matches the claimed parties
(a real slot carrying a different case is itself a documented fabrication
pattern). It witnesses existence and quote-fidelity, never whether the legal
argument is sound.

## The evidence

The verdict comes from a third-party reporter, not from how plausible the cite
looks. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated citations are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** over a labeled set (4 documented *Mata v. Avianca* hallucinations + 6 synthesized) | CourtListener / Free Law Project, a third-party reporter the agent authored zero bytes of | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| Real cases are not wrongly flagged | FALSE-FIRE **0 / 8 = 0.0%** on 8 landmark SCOTUS cases | the reporter's name-search ground-truth path | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — here, ten fabricated
citations a sound witness refused to vouch for — never a won case.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the MCP tools your host can call
```

The `citation-resolve` tool returns a typed verdict — `RESOLVED_MATCH` when the
cite exists and the name agrees, `UNRESOLVED` when no reporter carries it (or a
real slot carries a different case):

```text
UNRESOLVED  925 F.3d 1339 (Varghese v. China Southern Airlines) — no cluster resolves
```

## What this does — and does not — certify

It certifies **existence and quote-fidelity**: the case is real and the quoted
holding appears in the resolved opinion. It does **not** certify that the case
*supports your argument* — that is the lawyer's judgment, the tier this tool
deliberately abstains on. Selling existence-checking as "verifies legal
correctness" would be the over-claim this benchmark is written to avoid.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated-citation detection study.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface (`citation-resolve` and the others).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Open-source alternative to paid AI legal citation checkers](open-source-ai-legal-citation-checker.md) — the same tool, framed as buy-vs-build.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- how to verify a cited legal case actually exists before filing
- verify a cited legal case actually exists before filing
- check that a case citation is real not fabricated
- does this court case the AI cited actually exist
- confirm a legal citation resolves to a real reporter
- AI cited a case how do I know it's not made up
- validate case law citations against a real database
- fact-check a legal citation before I file it
- is this case citation hallucinated
- is this case real or did the AI invent it
- look up whether a citation resolves to a real case
- check a case citation against a reporter database
- AI gave me a citation confirm it exists

> The kernel is the part that doesn't believe the agents.
