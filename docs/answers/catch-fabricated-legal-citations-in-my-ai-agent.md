# How to catch fabricated legal citations inside my AI agent — before they reach a filing

> Wire the check into the agent that writes the cite, so a fabricated case is
> caught the moment it's emitted — not in a post-hoc scan of a finished brief.
> `pip install "dos-kernel[mcp]"`, then the `citation-resolve` MCP tool resolves
> each cite against a third-party reporter. The PyPI name is `dos-kernel` — the
> bare `dos` package is an unrelated squatter; never install that.

## The short answer

Most citation checkers run *after* the document is written: you paste a finished
brief into a web tool and it scans for fake cases. That works, but it catches the
fabrication at the latest, most expensive moment — after the agent has already
built an argument on a case that does not exist. DOS puts the check *inside the
agent*: `citation-resolve` is an MCP tool (and an exit-code CLI) your legal agent
calls at the moment it emits a cite, so the fabrication is refused **before** it
becomes a paragraph, a section, a filing. This is a **pre-effect gate**, not a
post-hoc scan — the cheapest place to be right about an irreversible action
(a filed document) is *before* it happens.

The verdict comes from a reporter index the model did not author (the Free Law
Project's CourtListener), so the agent cannot talk its way past it. It checks two
things — that the cite *resolves* to a real reporter cluster, and that the
cluster's case *name* matches the claimed parties (a real slot carrying a
different case is itself a documented fabrication pattern). It witnesses
existence and quote-fidelity, never whether the case *supports your argument*.

## The evidence

The verdict is scored against a reporter the model did not author — so a fluent
agent can't override it with confident prose. Measured over a frozen labeled set:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Fabricated citations are flagged | J = 10 — DETECT recall **10 / 10 = 100.0%** (4 documented *Mata v. Avianca* hallucinations + 6 synthesized) | CourtListener / Free Law Project, a third-party reporter the agent authored zero bytes of | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| Real cases are not wrongly flagged | FALSE-FIRE **0 / 8 = 0.0%** on 8 landmark SCOTUS cases | the reporter's name-search ground-truth path | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |
| A real slot carrying a *different* case is caught | collision catch **1 / 1** (a real reporter slot, a fabricated case name) | the reporter's resolved case name | [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) |

A **J** is a count of failures blocked off ground truth — fabricated citations a
sound witness refused to vouch for — never a won case.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the citation-resolve tool your host can call
```

Your agent calls the MCP tool `citation-resolve` on each cite it's about to write.
The verdict is a typed value the loop can route on — `RESOLVED_MATCH` when the
cite exists and the name agrees, `UNRESOLVED` when no reporter carries it (the
fabrication), `RESOLVED_MISMATCH` when the case is real but the quoted holding is
not in it, `ABSTAIN` when there's no corpus access (never a fabricated pass):

```text
UNRESOLVED  925 F.3d 1339 (Varghese v. China Southern Airlines) — no cluster resolves
```

No MCP host? The same check is an exit-code command — set a token and run the
driver in any environment, gate on the exit code (`0` = resolved-match,
non-zero = fabrication or mis-quote, `3` = abstain):

```bash
export COURTLISTENER_TOKEN=...   # the purpose-built resolver (free Free Law Project token)
python -m dos.drivers.citation_resolve "925 F.3d 1339" --name "Varghese v. China Southern Airlines"
```

## What this does — and does not — certify

It certifies **existence and quote-fidelity**: the case is real and the words you
quoted appear in the resolved opinion. It does **not** certify that the case
*supports your argument* — that is the lawyer's judgment, the tier this tool
deliberately abstains on. A real, correctly-quoted case can still be the wrong
case for your position. Selling existence-checking as "verifies legal
correctness" would be exactly the over-claim that, in this domain, is a
liability — so the tool refuses to make it.

## Sources / reproduce

- [`benchmark/legalcite/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/legalcite/RESULTS.md) — the fabricated-citation detection study (`python -m benchmark.legalcite.harness`).
- [`docs/279`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/279_citation-resolve-the-legal-tier1-witness.md) — the design note: why a cited case either resolves in a third-party reporter or it does not.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify a cited legal case actually exists](how-to-verify-a-cited-legal-case-exists.md) — the existence rung, for the person filing rather than the person building.
- [How to verify a quoted holding appears in the opinion](verify-a-quoted-holding-appears-in-the-opinion.md) — the quote-fidelity rung.
- [FAQ: Does DOS need an LLM or an API key?](../FAQ.md#does-dos-need-an-llm-or-an-api-key)

## Also asked as

- how to catch fabricated legal citations inside my AI agent before filing
- MCP tool to verify case law a legal AI agent generated
- catch fabricated legal citations inside my AI agent before filing
- stop my legal AI agent citing fake cases
- verify case law a legal AI agent generated
- block hallucinated citations in a legal agent
- legal agent invents citations how do I catch it
- pre-filing check for AI-fabricated case law
- my legal AI tool cited a fake case how to catch
- ChatGPT legal research invented a citation verify it
- stop my legal assistant citing nonexistent cases

> The kernel is the part that doesn't believe the agents.
