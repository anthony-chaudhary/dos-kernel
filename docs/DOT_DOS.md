# The `.dos` surface — what that directory is, and why it's the whole trick

> You ran `dos init` (or a DOS hook fired) and a `.dos/` directory appeared in
> your repo. This page answers the immediate questions — what is it, is it
> safe to delete, why isn't it in my git history — and then the bigger one
> those answers add up to: why the same kernel works under Claude Code,
> Cursor, Codex, Gemini, a CrewAI guardrail, an MCP client, GitHub Actions,
> GitLab CI, and bare Python, without caring which one is calling.

## The short answers

**What is it?** DOS's per-project state: every fossil the kernel writes while
adjudicating work in this repo. Leases and the lane write-ahead log, the
verdict journal, run archives, verdict envelopes, the hook observation log,
per-run tool-stream records, and a small schema-versioned identity card
(`project.json`).

**Is it safe to delete?** Yes. Everything under `.dos/` is re-derivable
emission, never source of truth about your code — the truth the oracle reads
is git itself. `dos reindex` rebuilds the cross-project indices. The one real
cost: observation *history* (what the hooks saw, per-run streams) is gone, so
trend views start fresh.

**Why isn't it in my history?** `.dos/.gitignore` ships self-ignoring (`*`
plus `!.gitignore`), so adopting DOS needs zero edits to your repo's own
`.gitignore` and the state never lands in a commit. The consequence to know:
`.dos/` is **per-clone**. A fresh clone starts with empty fossils; only the
evidence that lives in git — commits, ancestry, the stamps `dos verify`
reads — travels with the repository.

## The three surfaces

Everything DOS shares with a host is a repo-resident file with a declared
shape. Three surfaces, three directions:

| Direction | Surface | Committed? | What it carries |
|---|---|---|---|
| Policy **in** | `dos.toml` | yes — it's your declaration | which lanes exist and their file trees, the refusal vocabulary, the ship-stamp grammar, the plan dialect ([HACKING](HACKING.md)) |
| State **through** | `.dos/` | no — self-gitignored, per-clone | the WAL (`lane-journal.jsonl`), the verdict journal, `leases/`, `runs/`, `verdicts/`, `metrics/observations.jsonl`, `streams/`, `project.json` (`schema: 1`) |
| Verdicts **out** | git + `verdict.json` | git is the record; `verdict.json` is opt-in publishing | the ancestry and stamps `dos verify` answers from; the machine-readable self-grade + badge a repo can publish ([BADGE](BADGE.md)) |

(There is also `~/.dos`, the per-MACHINE tier: the cross-project registry and
aggregate indices. Nothing in your repo depends on it; `dos reindex` rebuilds
it from the per-project `.dos/` cards.)

## Why this is the whole trick

Here is the part worth saying plainly: **DOS is platform-agnostic because the
substrate travels with the work, not the worker.**

A "host" — Claude Code firing a hook, Cursor, a CrewAI guardrail, an MCP
client, a CI job, your own Python via [`dos.verified`](../examples/playbooks/cookbook-python-api.md) —
holds **zero state**. It is anything that can invoke `dos` (or the `dos-hook`
binary, or `import dos`) against a workspace root. The decision is computed
fresh each time from the three surfaces above: declared policy, durable
fossils, git evidence. So:

- **A new platform costs an adapter, never a redesign.** The verdict is
  decided once and rendered into whatever JSON shape the host parses
  ([docs/217](217_the-cross-vendor-hook-dialect-seam.md)); the state it was
  decided FROM didn't move.
- **Two platforms working the same repo get one referee with one memory.** A
  lane lease journaled while one agent works refuses the colliding lane no
  matter which host the second agent arrives through — same WAL, same
  arbiter. The observation log accumulates across all of them.
- **Your evidence is yours.** The eval and observability platforms anchor
  agent state in their cloud, which is why each speaks only to its own
  ecosystem. DOS anchors it in your repository — the one neutral ground every
  agent platform already shares. Switch hosts, mix hosts, or read the
  verdicts with `cat`: nothing is held anywhere else.

The layout itself is a contract, not a convention: every emission path in the
generic layout resolves under `.dos/`, pinned by the kernel's own suite
(`tests/test_state_home.py`), and the card and published verdict are
schema-versioned (`project.json` `schema: 1`; `verdict.json` v1) so a host
written against them knows exactly what it may rely on
([STABILITY](STABILITY.md)).

## What `.dos/` is NOT

- **Not a database you must protect.** Delete it freely; back up nothing.
- **Not synced state.** It does not follow the repo through clone/push. If
  you need a verdict to travel, that's what git stamps and `verdict.json`
  are for.
- **Not readable policy.** Nothing under `.dos/` changes what DOS decides
  about admissibility or truth — policy is `dos.toml`, evidence is git.
  (That's deliberate: an agent that can write `.dos/` files still can't
  write itself a friendlier verdict — the oracle reads ancestry, not
  fossils.)

## See also

- [QUICKSTART](QUICKSTART.md) — the 5-minute hello-world that creates one.
- [HACKING](HACKING.md) — everything `dos.toml` can declare.
- [The MCP / hooks surfaces](80_mcp-server-surface.md) and the
  [hook installer](221_the-cross-vendor-hook-installer.md) — the host
  adapters this page explains the thinness of.
