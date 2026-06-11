# 299 — Agent discoverability (AEO): the surfaces an agent reads before it clones

> **The next searcher is an agent.** SEO optimized pages for human searchers;
> AEO (answer-engine optimization) optimizes them for the things that now do the
> searching: coding agents, answer engines, and LLMs with web tools. Those
> readers don't browse — they fetch a handful of well-known files (`llms.txt`,
> `AGENTS.md`, a README, registry metadata), match question-shaped queries
> against question-shaped content, and extract one self-contained answer. DOS's
> own buyers run agent fleets, and the installer acting on their behalf is very
> often an agent. So the repo should be exactly as legible to an agent
> *arriving* as it already is to one *working inside it* — and today it is not:
> there is no `llms.txt`, no question-shaped FAQ for an answer engine to lift,
> and the PyPI/GitHub metadata is missing terms an agent actually types.

*Status: IN FLIGHT. Opened 2026-06-11.*

## 0. What exists, what's missing

DOS already has the strongest *arrival* surfaces by accident of its dogfooding:

| Surface | State before this plan |
|---|---|
| `AGENTS.md` | shipped — orientation written FOR an agent, with the consumer-moves table |
| `README.md` | shipped — audience gradient (docs/292), absolute URLs, agent call-out box |
| MCP registry | shipped — `server.json`, registry live at v0.24.1 |
| GitHub topics / description | 15 topics, answer-shaped description, homepage → PyPI |
| `llms.txt` | **missing** — the one file the llms.txt convention says an agent fetches first |
| Question-shaped content | **missing** — no FAQ; the questions that should lead here ("how do I verify an AI agent actually did the work?") have no extractable answer |
| PyPI keywords / urls | partial — no `claude-code`, `model-context-protocol`, `agent-verification`; no FAQ/llms.txt urls |
| `CITATION.cff` | **missing** — the README has a citation block, but GitHub's "cite this repository" affordance (and scholarly agents) read the machine-readable file |

The unit of work is therefore three small, independently shippable artifacts,
none of which touches a kernel module. This is a docs/metadata plan — the same
genre as docs/292 — not a kernel design plan.

## 1. The discipline: answer-shaped, honest, pinned against rot

Three rules carry over from the rest of the repo:

- **Answer-shaped.** Every FAQ entry is one question phrased the way a searcher
  (human or agent) types it, followed by an answer that survives extraction:
  self-contained, names `dos-kernel` and the exact command, no "see above."
  This is the AEO analogue of the README's "exit code is the verdict" style.
- **Honest.** No keyword that isn't true. DOS is a referee, not an orchestrator
  or framework — the metadata must not claim otherwise, because the first agent
  that installs it on a false promise churns and the answer engines learn that.
- **Pinned.** Discoverability artifacts rot silently (a renamed doc leaves a
  dead link in `llms.txt` forever, and no human re-reads it). So `llms.txt`
  gets the same treatment as the README assembly: a test that resolves every
  repo link it carries and fails the suite when one dies.

## Phase 1 — `llms.txt`: the agent index at the root

The llms.txt convention (llmstxt.org): a root-level `/llms.txt` with an H1, a
blockquote summary, short prose, then H2 sections of `- [name](url): one-line
description` links — a curated index sized for an LLM context window, not a
sitemap. Ship:

- `llms.txt` at the repo root. H1 names the project and the PyPI distribution
  (`dos-kernel`); the blockquote carries the one-line answer (catch agents
  lying about what they shipped) + the install command + the squatter warning.
  Sections: **Start here** (README, AGENTS.md, QUICKSTART, INSTALL, FAQ),
  **The kernel** (ARCHITECTURE, CLAUDE.md contract, HACKING), **Integrations**
  (claude-plugin, MCP server, fleet-framework cookbook), **Optional** (paper,
  benchmark, releases). All `.md` targets as `raw.githubusercontent.com` URLs
  (an agent fetching them gets clean markdown), everything else as normal
  GitHub/PyPI URLs.
- `tests/test_llms_txt.py` — the rot pin: the file exists, starts with `# `,
  the first body element is a blockquote, every `blob/master/` or
  `raw.githubusercontent.com/...master/` path resolves to a tracked file in
  this repo, and no link target is a bare relative path (raw fetchers can't
  resolve those).

Done when: `llms.txt` is tracked at the root and `python -m pytest -q
tests/test_llms_txt.py` is green.

## Phase 2 — `docs/FAQ.md`: the question-shaped layer

The AEO content artifact: one page of H2 questions phrased as the queries that
should land here, each with an extractable answer.

- `docs/FAQ.md` — roughly a dozen entries: verify-an-agent's-claim, stop
  two-agents-colliding, detect a-spinning-loop, gate a "keep going until done"
  loop on evidence, what-is-dos-kernel, install (and the `dos` squatter),
  which agent hosts (Claude Code / Cursor / Codex / Gemini / Antigravity /
  Cowork), which frameworks (LangGraph / CrewAI / AutoGen / Agents SDKs),
  does-it-need-an-LLM (no — deterministic; the JUDGE rung is optional and
  advisory), does-it-need-special-repo-setup (no — plain git, zero config),
  is-it-an-orchestrator (no — a referee beside whatever you already run), and
  how-it-differs-from-evals/observability (runtime verdicts with exit codes
  you can gate on, not offline scores).
- Wire it in: a link from the README docs map (`docs/readme/90_*` part +
  rebuild via `scripts/build_readme.py` so `test_readme_assembly` stays green;
  absolute URL per the README rule) and from the `llms.txt` **Start here**
  section (P1 writes the link; this phase makes it resolve — land P1 and P2
  together or accept one red test window).

Done when: `docs/FAQ.md` is tracked, the README links it, and the readme
assembly + llms.txt tests are green.

## Phase 3 — metadata widening: PyPI, citation, topics

The registry surfaces an agent queries *instead of* the repo:

- `pyproject.toml`: add the missing honest keywords (`claude`, `claude-code`,
  `mcp-server`, `model-context-protocol`, `agent-verification`,
  `autonomous-agents`, `ai-agent-monitoring`) and two `[project.urls]` rows —
  `FAQ` and `llms.txt` — so the PyPI sidebar carries both. (Reaches PyPI on
  the next `/release`; that lag is fine.)
- `CITATION.cff` at the root, matching the README citation block — GitHub's
  "Cite this repository" affordance plus the scholarly-agent surface.
- GitHub topics: `gh repo edit --add-topic` for `autonomous-agents`, `agents`,
  `claude`, `hooks` (15 → 19, under the 20 cap; all true).

Done when: the keywords/urls are in `pyproject.toml`, `CITATION.cff` is
tracked, and `gh repo view --json repositoryTopics` shows the new topics.

## Out of scope (deliberately)

- **External submissions** — llms.txt directories, awesome lists, newsletter
  and showcase venues — are distribution *operations*, not repo artifacts;
  they are owner-gated and tracked outside this repo (the private playbook
  `notes/2026-06-11_newsletter-showcase-submission-playbook.md`).
- **A docs website / hosted llms-full.txt** — DOS has no website; the repo IS
  the site. If one appears, `llms.txt` moves to its root and gains an
  `llms-full.txt` sibling; until then the GitHub raw URL is the canonical
  fetch.
- **Keyword stuffing** — adding `agi`, `ai-safety`, or framework names DOS
  does not implement. The honesty rule above is the whole point of the
  product; the metadata doesn't get to violate it.
