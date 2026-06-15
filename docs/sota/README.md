# DOS SOTA scanner

A small recurring sweep of the field for work that bears on DOS — agent
verification, multi-agent trust, process-reward training, self-improving loops.
It is run by [`scripts/sota_scan.py`](../../scripts/sota_scan.py).

The point: DOS competes in a fast-moving space, and until now we found the
relevant papers, repos, and threads by hand (docs/344 named arXiv ids one at a
time, then that knowledge was gone). This scanner makes the state-of-the-art a
**tracked series** instead of a fresh hunt every time.

## What each cycle does

1. Fetch the newest items across the sources below.
2. Drop everything already in the ledger (`seen.jsonl`).
3. Append the NEW items and write a dated digest (`digest-<date>.md`).
4. Print a GitHub-issue body listing only the new items, each with a one-line
   suggested DOS action. With `--open-issue`, post it — **after** the leak gate
   passes.

## Sources, and the one honesty wall

A plain Python script can fetch some sources by itself. It **cannot** run the
`WebSearch` tool — that is the agent's capability, not the shell's. So the work
is split, and the split is honest:

| Source | Who fetches it | How |
| --- | --- | --- |
| GitHub repos | the script | `gh search repos`, star-ranked |
| arXiv | the script | the Atom API (cs.AI / cs.SE / cs.LG / cs.MA) |
| Reddit | the script | the `.rss` search feed |
| Hacker News | the script | the Algolia API |
| **Web search** | **the agent** | `WebSearch`, fed back via `--add-manual` |

The script never claims to have done a web search it cannot do. A web item
enters the ledger only when the agent gathers it and runs:

```bash
python scripts/sota_scan.py --add-manual --source web \
  --id "<url>" --title "<title>" --topic "<topic>"
```

This is the same LIVE-vs-claimed wall `aeo_tracker.py` draws: a tracked item was
actually fetched, or explicitly added — never a promise the script can't keep.

## Files here

- `seen.jsonl` — the append-only dedup ledger. One line = one seen item
  (`source`, `id`, `title`, `url`, `score`, `date`, `topic`, `scanned`). LF on
  every OS, so a row written locally and one written in CI compare byte-for-byte.
- `digest-<date>.md` — one digest per scan day, grouped by source, NEW items
  only, each with a suggested action.

Every digest row is a **lead to triage, not a verified fact** — verify before
acting (the kernel's own rule: don't believe the report, check the effect).

## The roll-up — the clean "most important items" view

A full scan can surface dozens of items. The roll-up is the human view: the most
important items, **ranked**, with the long tail collapsed to a count.

```bash
python scripts/sota_scan.py --rollup            # latest scan's items, ranked
python scripts/sota_scan.py --rollup --top 12   # show more before the tail count
python scripts/sota_scan.py --rollup --all      # rank the whole ledger, not just the latest scan
```

Importance is a small, defensible heuristic over data we already have — a
per-source base weight (research and agent-surfaced items lead), a log-damped
popularity term (a 5000★ repo outranks a 50★ one without one mega-repo swamping
the list), and a freshness nudge for items from the latest scan day. It is a
**relevance hint for ordering, never a claim of fact** — the same rule as the
suggested actions.

### Single key: `r`

On Windows, `scripts/r.ps1` is the one-keystroke roll-up. Dot-source it once and
then just press `r`:

```powershell
. .\scripts\r.ps1     # defines the function `r` for this session (dot + space)
r                     # ← the single key — prints the roll-up
r -Top 12             # same knobs as --rollup
r -All
```

To get `r` in every new shell, add that dot-source line (with this file's full
path) to your `$PROFILE`. Run directly without defining the function with
`pwsh scripts/r.ps1`.

## Running it

```bash
python scripts/sota_scan.py --scan                 # fetch, dedup, write digest, print issue body
python scripts/sota_scan.py --scan --open-issue    # ...and post the issue (leak-gated)
python scripts/sota_scan.py --rollup               # the clean ranked roll-up (single key `r`)
python scripts/sota_scan.py --status               # per-source counts over the ledger
python scripts/sota_scan.py --scan --stamp 2026-06-15   # pin the date (deterministic)
```

Deterministic + offline by construction: the only non-deterministic input (the
date) is injectable via `--stamp`, and every network fetch degrades to nothing
when its source is unreachable — a dead source skips, the cycle never crashes.

## The cycle

The scan is meant to run on a cadence (e.g. weekly). In a Claude Code session a
session-scoped cron fires it; for an unattended cloud cadence, wire a
`schedule:`-triggered GitHub Actions job that runs `--scan --open-issue` (a
durable follow-up to the local cron).
