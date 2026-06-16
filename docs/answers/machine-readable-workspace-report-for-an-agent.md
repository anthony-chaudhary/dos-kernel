# How does an agent read a workspace's layout instead of hardcoding it?
> One read-only command emits the layout as machine-readable JSON: `pip install dos-kernel`, then `dos doctor --json`. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

An arriving agent that hardcodes `docs/_plans/`, a fixed lane list, or a guessed
commit-subject grammar is asserting a layout it never checked — a self-report
about the world, not a reading of it. `dos doctor --json` is the cure: one
read-only call returns the workspace's real layout as one JSON object — where
plans live (the paths), the lane taxonomy `dos arbitrate` admits on, and the
ship-stamp grammar `dos verify` matches against — so the agent **discovers** all
of it instead of carrying an assumption. The fields are exactly the ones the
human-readable `dos doctor` prints, emitted as data.

This is the same DOS discipline applied to orientation rather than to a finished
claim: don't believe a layout you assumed; read the artifact — the workspace's
own `dos.toml` and discovered facts — that the agent did not author. The call
creates no `.dos/` and mutates nothing, so a generic skill can read a stranger's
repo and adapt to it without writing a byte.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| `dos doctor --json` emits the machine-readable workspace report an agent reads once to discover layout (paths/lanes) + ship grammar (stamp) instead of hardcoding it | one read-only call, exactly the fields the text form prints, no `.dos/` created | the workspace's own `dos.toml` + config-build-time discovered facts, not the agent's assumption | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The same `--json` report carries the per-verb `exit_codes` map, so an agent discovers the exit semantics of every verdict too | the verb→verdict-token→exit-code contract, published as data | the running binary's own contract table, not a doc the agent re-typed | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The "use it on my repo" move is `dos init . && dos doctor` — the workspace answers from its own git history and one `dos.toml` | works on a plain git repo; the one `dos.toml` is all the config | the repo's git history + declared config, never the agent's narration | [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) |

These describe the mechanism the files document; they are not a benchmarked
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json --workspace .
```

A trimmed, realistic shape of what comes back — paths, the lane taxonomy, the
stamp grammar, and the exit-code contract, all as one object:

```text
{
  "workspace": ".",
  "is_kernel_repo": false,
  "paths": { "plans_glob": "planning/*.md", "lane_journal": ".dos/lane.jsonl" },
  "lanes": { "concurrent": ["src", "docs"], "exclusive": ["global"] },
  "stamp": { "subject_dirs": ["planning"] },
  "exit_codes": { "verify": { "SHIPPED": 0, "NOT_SHIPPED": 1 } }
}
```

The agent reads `paths` instead of hardcoding where plans live, `lanes` instead
of guessing what `dos arbitrate` will admit, `stamp` instead of assuming a
commit-subject grammar, and `exit_codes` instead of memorizing a verb's exit
semantics. To set this up on a fresh repo first:

```bash
cd <your repo> && dos init . && dos doctor
```

## What this does — and does not — certify

`dos doctor --json` reports the workspace's **declared and discovered layout** —
it is a description, not a verdict on your work. It tells an agent where plans
live and which lanes exist; it does not check whether any phase shipped (that is
`dos verify`) or whether two agents would collide (that is `dos arbitrate`). It
is read-only by design: it resolves the active workspace without writing `.dos/`,
so reading a stranger's repo changes nothing in it. A layout it reports is only
as honest as the `dos.toml` that declared it — `doctor --check` flags a declared
lane with no tree or a stamp grammar that matches none of the repo's real ships,
but the plain `--json` report just renders what is there.

## Sources / reproduce

- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the `dos doctor --json` verb: paths, lane taxonomy, stamp grammar, and the per-verb `exit_codes` map, emitted as one read-only object.
- [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — `cd <repo> && dos init . && dos doctor` is the "use it on my repo" move.
- [The trust substrate for a fleet of autonomous agents](trust-substrate-for-a-fleet-of-autonomous-agents.md) — what the kernel this report describes actually is.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the lane taxonomy this report exposes is what `dos arbitrate` admits on.
- [How to verify an AI agent actually did the work](how-to-verify-an-ai-agent-actually-did-the-work.md) — the truth syscall whose stamp grammar this report publishes.
- [FAQ](../FAQ.md) — the short-form answers.

## Also asked as

- How does an agent discover a repo's layout without hardcoding paths?
- Is there a machine-readable workspace report a skill can read once?
- How do I get a JSON dump of a DOS workspace's lanes, paths, and stamp grammar?
- How does a generic agent skill adapt to an unfamiliar repo?
- What does `dos doctor --json` return?
- How can an agent find out which lanes `dos arbitrate` uses?
- Where do plans live in this repo — can the agent ask instead of guess?
- How do I make a skill that works on any repo instead of one hardcoded layout?

> The kernel is the part that doesn't believe the agents.
