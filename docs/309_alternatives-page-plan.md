# 309 — "DOS and the alternatives": own the comparison honestly, in FastAPI's register

> Anyone who finds DOS asks the same next question: "how is this different
> from X?" — where X is an eval platform, a framework guardrail, a durable
> workflow engine, a supply-chain attestor, or plain CI. Today the repo has no
> page that answers it, so each reader assembles their own answer from
> fragments. This plan ships one public page that answers it generously, the
> way FastAPI's alternatives page does (https://fastapi.tiangolo.com/alternatives/):
> for each neighbor, what it is excellent at, what DOS adds that it
> deliberately does not have (post-hoc verdicts computed from evidence the
> claimant didn't author), and — load-bearing for trust — when NOT to use DOS.
> Public handle: [issue #65](https://github.com/anthony-chaudhary/dos-kernel/issues/65).
> Framing source (private, by the cross-link rule): `dos-private`
> `notes/2026-06-12_next-steps-private-companions.md` §"Comparison page framing".

*Status: in flight 2026-06-12 — drafted same day as the plan; each phase
closes only when `dos verify` answers SHIPPED for it (P2's witness lives on
`gh-pages`, see its phase note). Operator sign-off on the final copy is the
gate before any push. (Numbered 309: 306–308 were taken by concurrent
plans — the same collision docs/310 renumbered around.)*

## 0. The editorial contract (what "generous" means, pinned)

The page lives or dies on tone, so the rules are part of the design:

1. **Generous register.** Every neighbor section opens with what that tool is
   genuinely good at, in its own vocabulary. Where DOS ships an integration
   for the neighbor (the docs/305 guardrail seats, the verify-action CI gate,
   the cookbook recipes), the page says so — "we sit in their seam" is the
   strongest possible compliment and the strongest possible differentiation,
   simultaneously.
2. **Every claim about a neighbor cites that project's primary docs.** Not a
   blog post, not a comparison site, not memory. If a claim cannot carry a
   primary-doc link, it does not go on the page. (Citations verified
   2026-06-12; each section names its sources inline.)
3. **One discriminator, repeated calmly.** The thing DOS adds in every
   section is the same thing: a verdict about *completed* agent work computed
   from evidence the agent did not author (git ancestry, exit codes, file
   trees, read-backs). Neighbors gate proposals, score traces, make execution
   durable, attest pipelines, or gate merges — different jobs, all real, none
   of them this one.
4. **"When NOT to use DOS" is mandatory and concrete.** A single trusted
   agent whose diffs a human reviews, behind good CI, does not need a
   referee — the page says so plainly. That honesty is the credibility the
   whole page is buying.
5. **No market talk.** No funding, no acquisitions, no category prognosis, no
   adoption pressure. The page compares mechanisms, full stop.

## 1. The five neighbor families (the issue's roster)

| Family | Representative | What it gates / reads | Primary-doc anchor |
|---|---|---|---|
| Hosted evals / observability | LangSmith-class | scores the run/trace the app emitted (LLM-as-judge, code evaluators, online evaluation) | docs.langchain.com/langsmith/evaluation-concepts |
| Framework guardrails | OpenAI Agents SDK, CrewAI | typed checkpoints on agent/task output; tripwires halt, guardrails retry | openai.github.io/openai-agents-python/guardrails/; docs.crewai.com/en/concepts/tasks |
| Durable execution | Temporal-class | event history; resume-after-failure; replay | docs.temporal.io/temporal |
| Supply-chain attestation | in-toto / witness | signed Statement/Predicate metadata about pipeline steps | github.com/in-toto/attestation; github.com/in-toto/witness |
| Plain CI + branch protection | GitHub required checks | env-authored gate on the merge path | docs.github.com "About protected branches" |

## Phase 1 — the docs twin, indexed and linked

`docs/ALTERNATIVES.md`: the full page under the editorial contract of §0,
covering the five families of §1, with the mandatory "when NOT to use DOS"
section and a closing composition map (which DOS surface meets which
neighbor). Wired into both indexes in the same phase, because the generated
files chain (`llms-full.txt` inlines `README.md`): a row in `llms.txt`
(non-Optional, so `scripts/build_llms_full.py` inlines the page), a bullet in
the Documentation list (`docs/readme/90_extending-and-docs.md`), and both
generated files rebuilt (`scripts/build_readme.py`,
`scripts/build_llms_full.py`; `tests/test_readme_assembly.py` +
`tests/test_llms_txt.py` + `tests/test_llms_full.py` stay green). Done when
the page exists, the README links it, the leak scanner passes it, and the
pins are green.

## Phase 2 — the Pages-site rendering

`alternatives.html` on the `gh-pages` branch (the site is a hand-kept
single-page genre — match `index.html`'s style, canonical URL, JSON-LD), plus
a `sitemap.xml` entry and a cross-link from `index.html`. **Witness note:**
this phase's commit lives on `gh-pages`, outside master's ancestry, so
`dos verify` on master answers NOT_SHIPPED via `none` for it by design — the
witness is `git log gh-pages --oneline` naming the stamp. Done when the page
exists on the branch and the sitemap lists it.

## Out of scope (deliberately)

- Per-neighbor benchmark numbers or head-to-head measurements — this page
  compares *jobs*, not performance; the benchmark harness has its own docs.
- Any neighbor not in the issue's roster (orchestrators, sandboxes, policy
  engines get a sentence in the composition map at most — each is a future
  section only if readers actually ask).
- Push/publication — operator signs off on the final copy first (the issue's
  done-condition), and pushes are owner-gated on this repo anyway.
