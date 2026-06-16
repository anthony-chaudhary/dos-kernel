# 325 — the answer corpus: the citeable unit an answer engine quotes verbatim

> **The next searcher is a model, and the next reader of this repo may be a
> training run.** docs/299 shipped the *arrival* surfaces — the files an agent
> fetches when it lands here (`llms.txt`, `AGENTS.md`, the FAQ). Its
> out-of-scope section deliberately left the harder half unbuilt: the content
> that gets DOS *cited* and *ingested* before an agent ever arrives — in the
> corpora the next models train on (public GitHub, the crawlable web) and in the
> answers an engine gives when someone asks "how do I check an agent actually
> did the work?" This plan builds that half: a small corpus of evidentiary,
> quotable, individually-fetchable pages — one per high-intent query — each a
> self-contained unit a generative engine can lift and attribute.

*Status: SHIPPED — Phase A (the five core pages + index + the rot pin + the
discovery wiring) and Phase B (the embeddable answer-card) land with this plan.
As of 2026-06-12.*

## 0. The mechanism this rests on (why this, not more `llms.txt`)

Three findings sort the work, and the first kills the obvious move:

- **`llms.txt` is inert for ranking.** No major lab reads it in production; a
  published 90-day before/after over ten sites found eight unchanged. DOS
  already ships one (docs/299 P1). Adding more index files is not the lever.
- **The corpora are known.** Open pretraining mixes (e.g. Dolma) are ~three
  quarters crawled web, ~an eighth public code (the GitHub-derived sets), a
  sliver academic. The highest-weight surface a code repo *controls* is its own
  public GitHub tree — so the content has to live there as plain, crawlable
  markdown, not behind a site.
- **Answer engines reward evidence, not keywords.** The Princeton GEO study
  (KDD 2024, ~10k queries) found the citation-rate gains come from adding
  **statistics**, **quotations**, and **inline citations**; keyword stuffing
  did not move. So the unit that ranks is a page dense with *true, sourced*
  numbers and a quotable line — exactly the register
  [`docs/guide/for-researchers.md`](guide/for-researchers.md) already
  uses, promoted to a standalone, individually-citeable page.

The unit of work is therefore content, not kernel: this is a docs/distribution
plan in the genre of docs/299 and docs/304 — nothing under `src/dos/` is
touched.

## 1. The discipline: answer-shaped, sourced, pinned, honest

Four rules, three inherited from the rest of the repo:

- **Answer-shaped.** Each page's H1 *is* a query the way a searcher types it;
  the first body block is a self-contained answer (names `dos-kernel`, the
  command, the squatter warning) that survives being lifted out of context.
- **Sourced.** Every number on a page is one DOS has already earned, and every
  one carries an inline link to the in-repo file that proves it. A number with
  no resolvable source link does not go on a page — and that is *mechanically
  enforced* (Phase A's test resolves every source link). This is the GEO
  inline-citation lever and the repo's honesty rule, the same gate.
- **Pinned.** Discoverability artifacts rot silently. The pages get the same
  treatment as `llms.txt` and the README: a test that resolves every repo link
  they carry and fails the suite when one dies.
- **Honest.** No keyword or claim that isn't true. DOS is a **referee, not an
  orchestrator or framework** — that word appears only in the negative. And no
  *outcome* claim: a **J** is a count of failures blocked off ground truth,
  never a downstream delta — "blocked 10 real over-claims" is proven; "made the
  fleet 10% better" is a different sentence we don't write. Whether these pages
  rank or land in a corpus is the *goal*, never an assertion on a page.

## Phase A — the five core pages, the index, and the wiring

The corpus lives at `docs/answers/`, named with the incident-page slug
convention (`<verbatim-search-phrase>.md`), not the numbered-design-note
namespace. The five highest-intent queries, each owning one failure the kernel
already catches:

- `how-to-verify-an-ai-agent-actually-did-the-work.md` — `dos verify`.
- `how-to-stop-two-ai-agents-overwriting-each-other.md` — `dos arbitrate`.
- `how-to-detect-an-agent-loop-spinning-without-progress.md` — `dos liveness` /
  `productivity` / `efficiency`.
- `process-reward-model-training-data-that-cant-be-gamed.md` — `dos reward`,
  the non-distillable label.
- `do-ai-coding-agents-lie-about-what-they-shipped.md` — the category page that
  owns the distinctive phrasing.

Each page is hand-authored (the numbers must be entered by someone who checked
them) on one fixed skeleton: the query as H1; a liftable one-line answer
blockquote; a short answer; an **evidence table** (`Claim | Number | Witness
(byte-author ≠ claimant) | Source`) cloned from the researcher register, every
number sourced to a repo file; the one command with **real** CLI output (the
neutral `AUTH / AUTH1` scratch convention — never a real path or host); the
presence-not-correctness honesty rung; sources to reproduce; and one verbatim
quotable line. `docs/answers/README.md` indexes them (the
[`docs/incidents/README.md`](incidents/README.md) table discipline) and carries
the J-is-not-an-outcome caveat once for the whole corpus.

The rot pin is `tests/test_answers.py`, cloning the link-resolution logic of
[`tests/test_llms_txt.py`](../tests/test_llms_txt.py): for every page it checks
the shape (H1, blockquote, names `dos-kernel` and a `dos` command), resolves
**every** in-repo source link to a tracked file (the honesty gate), forbids a
local-machine path, and checks the index links every page and back.

The corpus wires into the existing discovery surfaces: one
`raw.githubusercontent.com` link to `docs/answers/README.md` in a non-Optional
section of [`llms.txt`](../llms.txt) (so
[`scripts/build_llms_full.py`](../scripts/build_llms_full.py) inlines the index
into `llms-full.txt` on rebuild), and one link from the README docs map
([`docs/guide/extending.md`](guide/extending.md),
absolute URL, rebuilt via `scripts/build_readme.py`). Each page back-links its
matching FAQ entry and incident page for topical authority.

Done when: the five pages + index are tracked, `tests/test_answers.py` is
green, `build_readme.py --check` and `build_llms_full.py --check` are clean, and
`dos verify docs/325_answer-corpus-distribution-plan PA` → SHIPPED.

## Phase B — the embeddable answer-card

The proliferation surface is mostly already shipped: the "verified by DOS" badge
([`docs/BADGE.md`](BADGE.md)), the vendored assets, the shields endpoints
(docs/112, docs/312), the scoreboard opt-in (docs/311). The one honest delta is
a copy-paste **answer-card** — a tiny markdown block (the badge, one quotable
line, a link back to the canonical answer page) that another repo can paste into
its README the way `docs/BADGE.md` already offers the badge. When a repo adopts
it, DOS's URL and vocabulary enter *that* repo's tree. It is framed as an
offering, on the `docs/BADGE.md` model ("asserts adoption, not a verdict") —
never as a claim that anyone has adopted it.

Done when: the answer-card block + cross-links are tracked (in `docs/BADGE.md`
and/or the answers index), pinned by `tests/test_answers.py`'s link checks, and
`dos verify docs/325_answer-corpus-distribution-plan PB` → SHIPPED.

## Out of scope (deliberately)

- **External submissions** — llms.txt directories, awesome-lists, marketplace
  and showcase listings — are distribution *operations*, owner-gated, and
  already tracked: issues
  [#102](https://github.com/anthony-chaudhary/dos-kernel/issues/102),
  [#77](https://github.com/anthony-chaudhary/dos-kernel/issues/77),
  [#54](https://github.com/anthony-chaudhary/dos-kernel/issues/54), and the
  private playbook docs/299 names. This plan does not duplicate them.
- **The arXiv posting** — the academic-corpus lever — is owner-gated (a
  first-time cs.* endorsement and the author's submission) and is out of scope
  here by operator decision; the bundle work, if revisited, lives in
  [`paper/arxiv/`](../paper/arxiv/README.md).
- **Keyword stuffing / outcome claims** — adding terms DOS does not implement,
  or asserting a page ranks or has been ingested. The honesty rule is the whole
  point of the product; the distribution layer does not get to violate it.
