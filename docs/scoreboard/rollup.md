# Drift-rate scoreboard — published-set roll-up (as of 2026-06-15)

> Across the **16 published repos** on the scoreboard, AI agents wrote about **3%** of recent commits — and **99.8%** of the concrete claims those commits made are backed by the commit's own diff (the **3** exceptions, 0.2%, named on their pages).

Generated 2026-06-15 by `python scripts/scoreboard_rollup.py` over the committed per-repo data under `docs/scoreboard/<org>/<repo>/sweep.json` — **zero network, reproducible by anyone from the repo**. This is the living roll-up of the *named, published* set (the leaderboard on the [index](README.md)); the larger operator-run corpus snapshot is [report-2026-06.md](report-2026-06.md). Read the [methodology](methodology.md) first.

## The numbers

| | |
|---|---|
| published repos folded | 16 (every `<org>/<repo>/sweep.json` under `docs/scoreboard/`) |
| default-branch commits scanned | 53,396 |
| machine-attributed agent commits | 1,777 |
| AI-built share (attributed / scanned) | **3%** |
| made a concrete, checkable claim (the denominator) | **1,253** |
| claim backed by the commit's own diff | 1,250 |
| claim not backed (unwitnessed) | **3** |
| abstained (no checkable claim — excluded from the rate) | 727 |
| **pooled backed rate** (witnessed / checkable) | **99.8%** |
| pooled unwitnessed rate | 0.2% |
| per-repo backed-rate spread | 97.7% – 100.0% (15 of 16 repos: 100% backed) |

By claim kind (backed / not backed): code-effect 995 / 0 · test 39 / 0 · doc 216 / 3. The audited commits carry attribution markers from 8 toolchains: claude 1,260 · codex 240 · copilot 94 · crush 86 · devin 76 · cursor 16 · aider 4 · jules 1.

## The honest reading

This is not "agents lie X% of the time." It is: across the named, published repositories on the scoreboard — visible-attribution, active, popular projects — agent-authored commit subjects are overwhelmingly backed by their own diffs. The pooled backed rate is **99.8%**; the **3** unwitnessed claims (15 of 16 repos are 100% backed) are all on this auditor's own page, where each is explained as a deliberate empty re-stamp — a house convention, not an over-claim (see the self page and methodology §2). Selection caveats — visible attribution only, active/popular repos, English-grammar claims — are in methodology §4 and §7.

A message-vs-diff mismatch is **never** a correctness, honesty, or intent grade — only a note that a commit's words and its own diff disagree.

## Reproduce

```bash
python scripts/scoreboard_rollup.py            # regenerate this file
python scripts/scoreboard_rollup.py --check     # assert it matches the data
```

`--check` regenerates the report in memory and compares it to the committed `docs/scoreboard/rollup.md`, exiting non-zero on any drift — so the report can never silently disagree with the JSON it folds. To score your own repo in one command: `dos commit-audit --sweep --workspace . BASE..HEAD`.

> The kernel is the part that doesn't believe the agents.
