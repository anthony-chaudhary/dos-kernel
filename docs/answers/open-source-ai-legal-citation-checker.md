# Is there an open-source alternative to paid AI legal citation checkers?

> Yes — resolve each citation against CourtListener for free, from the command
> line or as an MCP tool. `pip install "dos-kernel[mcp]"`, then `citation-resolve`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that. (Run `dos doctor` to list the tools your install exposes.)

## The short answer

The commercial citation-checkers are closed, paid, and a black box. The open
alternative is small and auditable: resolve the citation string against the Free
Law Project's CourtListener — a public reporter index whose bytes neither you nor
the model authored. DOS ships this as the `citation-resolve` MCP tool. It checks
that the citation resolves to a real reporter cluster, that the cluster's case
*name* matches the claimed parties (a real slot with a different case is a known
fabrication pattern), and, where the opinion text is available, that the quoted
holding is actually in it. MIT-licensed, no per-check fee, and you can read
exactly how the verdict is reached.

## The evidence

Scored against the third-party reporter, not the model's confidence. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated citations are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** | CourtListener / Free Law Project, a third-party reporter | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| Real cases are not wrongly flagged | FALSE-FIRE **0 / 8 = 0.0%** on 8 landmark SCOTUS cases | the reporter's name-search ground-truth path | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — ten fabricated
citations a sound witness refused to vouch for — never a won case.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the citation-resolve tool is available
```

The tool returns a typed verdict you can act on — `RESOLVED_MATCH`, `UNRESOLVED`,
or `RESOLVED_MISMATCH` (exists but the quote doesn't match):

```text
RESOLVED_MATCH  410 U.S. 113 (Roe v. Wade) — cluster resolves, name agrees
```

## What this does — and does not — certify

It certifies **existence and quote-fidelity** against a public reporter — for
free, auditably. It does not certify the case supports your argument (that is
legal judgment, the tier it abstains on), and it depends on CourtListener's
coverage. Within that scope it is a real, no-cost substitute for the existence
check the paid tools charge for.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated-citation detection study.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify a cited legal case actually exists before filing](how-to-verify-a-cited-legal-case-exists.md) — the same tool, framed as the workflow.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

> The kernel is the part that doesn't believe the agents.
