# External-repo conformance probe

Generated 2026-06-30 by `python scripts/external_repo_probe.py`.

This report keeps two questions separate:

- **Stamp verifiability:** can `dos verify` bind recent commits to a unit-of-work stamp under the active grammar?
- **Claim auditability:** can `dos commit-audit` classify a commit message claim and check it against the commit's own diff?

The committed scoreboard data measures the second rung. Run the live local-clone mode below to add the first rung.

## Aggregate

| measure | value |
|---|---|
| repos probed | 19 |
| commits audited by `commit-audit` | 2,834 |
| checkable commit claims | 1,739 (61.4%) |
| claims backed by their own diff | 1,734 (99.7% of checkable) |
| claims not backed by their own diff | 5 |
| abstained, no concrete claim | 1,095 (38.6%) |
| repos with live `doctor` verifiability | 0 |
| commits read by `doctor` | 0 |
| `dos verify`-verifiable commits | 0 (0.0%) |
| failure-mode counts | claim-diff-gap 1, claim-grammar-low-fit 2, stamp-grammar-not-measured 19 |

## Reading

A high backed rate means the claim-vs-diff auditor can check the commits that make concrete claims. It does **not** mean `dos verify` can close work units in that repo. The adoption wall is the gap between those rungs: a Conventional-Commits repo can have honest, backed diffs while `dos verify PLAN PHASE` still resolves through `none` because no commit names a unit of work.

No live `doctor` data is present in this committed offline run. That is intentional: the checked-in scoreboard artifacts are network-free `commit-audit` sweeps. To measure stamp grammar fit, run the same script over local clones.

## Repositories

| repo | source | commit claims checkable | backed | skipped | stamp-verifiable | modes |
|---|---|---:|---:|---:|---:|---|
| agno-agi/agno | scoreboard-sweep | 61.3% | 100.0% | 38.7% | n/a | stamp-grammar-not-measured |
| anthony-chaudhary/dos-kernel | scoreboard-sweep | 63.0% | 98.4% | 37.0% | n/a | stamp-grammar-not-measured, claim-diff-gap |
| assistant-ui/assistant-ui | scoreboard-sweep | 58.1% | 100.0% | 41.9% | n/a | stamp-grammar-not-measured |
| charmbracelet/crush | scoreboard-sweep | 52.1% | 100.0% | 47.9% | n/a | stamp-grammar-not-measured |
| crewAIInc/crewAI | scoreboard-sweep | 79.3% | 100.0% | 20.7% | n/a | stamp-grammar-not-measured |
| danny-avila/LibreChat | scoreboard-sweep | 63.2% | 100.0% | 36.8% | n/a | stamp-grammar-not-measured |
| exo-explore/exo | scoreboard-sweep | 66.3% | 100.0% | 33.7% | n/a | stamp-grammar-not-measured |
| farion1231/cc-switch | scoreboard-sweep | 71.4% | 100.0% | 28.6% | n/a | stamp-grammar-not-measured |
| getzep/graphiti | scoreboard-sweep | 52.0% | 100.0% | 48.0% | n/a | stamp-grammar-not-measured |
| JuliusBrussee/caveman | scoreboard-sweep | 75.4% | 100.0% | 24.6% | n/a | stamp-grammar-not-measured |
| kenn-io/roborev | scoreboard-sweep | 63.2% | 100.0% | 36.8% | n/a | stamp-grammar-not-measured |
| langchain-ai/langchain | scoreboard-sweep | 74.4% | 100.0% | 25.6% | n/a | stamp-grammar-not-measured |
| livekit/agents | scoreboard-sweep | 81.7% | 100.0% | 18.3% | n/a | stamp-grammar-not-measured |
| mem0ai/mem0 | scoreboard-sweep | 85.7% | 100.0% | 14.3% | n/a | stamp-grammar-not-measured |
| microsoft/autogen | scoreboard-sweep | 90.0% | 100.0% | 10.0% | n/a | stamp-grammar-not-measured |
| openai/codex | scoreboard-sweep | 45.1% | 100.0% | 54.9% | n/a | stamp-grammar-not-measured, claim-grammar-low-fit |
| OpenInterpreter/open-interpreter | scoreboard-sweep | 46.6% | 100.0% | 53.4% | n/a | stamp-grammar-not-measured, claim-grammar-low-fit |
| pydantic/pydantic-ai | scoreboard-sweep | 69.5% | 100.0% | 30.5% | n/a | stamp-grammar-not-measured |
| unslothai/unsloth | scoreboard-sweep | 78.6% | 100.0% | 21.4% | n/a | stamp-grammar-not-measured |

## Reproduce

Offline, from committed scoreboard data:

```bash
python scripts/external_repo_probe.py --from-scoreboard --out docs/scoreboard/external-repo-conformance.md --stamp 2026-06-30
python scripts/external_repo_probe.py --from-scoreboard --out docs/scoreboard/external-repo-conformance.md --stamp 2026-06-30 --check
```

Live, from local clones listed one per line:

```bash
python scripts/external_repo_probe.py --no-scoreboard --corpus local-clones.txt --range HEAD~200..HEAD --out _scratch/external-repo-conformance.md
```

Use the live path for the stamp-grammar question. Use the offline path for a deterministic regression check over the published scoreboard claim-audit corpus.
