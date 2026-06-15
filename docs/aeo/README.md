# AEO/SEO surface tracking

This directory is the **tracked record** of DOS's answer-engine-optimization /
search-discovery surface over time — the progress on "make DOS discoverable,
especially by other agents," recorded instead of eyeballed.

Two tools, one discipline:

- [`scripts/discoverability_inventory.py`](../../scripts/discoverability_inventory.py)
  counts the surfaces an arriving agent finds DOS through **right now**, read
  from the repo's own ground truth (arrival queries, arrival files, wireable
  hosts, integration tiers, live registries, scoreboard pages). A surface is
  counted LIVE only when its evidence is in this tree; a filed-but-unmerged
  submission is GATED, never folded into the LIVE headline.

- [`scripts/aeo_tracker.py`](../../scripts/aeo_tracker.py) is the **tracking**
  half: it snapshots that headline into [`aeo_ledger.jsonl`](aeo_ledger.jsonl)
  (append-only, one row per snapshot) and reports the delta against the prior
  row. The inventory promises "the delta is the progress, measured" — this is
  what records that delta, so the progress is a series, not a manual diff.

## The honesty rule, drawn across time

The tracker carries the kernel's one rule into the AEO surface: **track the
witnessed effect, never the self-report.** The gated, regression-checked counts
(`witnessed`) are LIVE / in-tree surfaces — a page that exists, a manifest that
ships, a registry whose evidence is a tracked file. A *claim* — "we rank #1",
"they merged our PR" — is never a tracked number. GATED/submitted counts are
recorded in the row's `advisory` field and are **excluded from the regression
gate**, so the tracked progress can never inflate on a promise a third party
hasn't honored. That is the same LIVE-vs-GATED wall the inventory draws,
extended into a time series.

## Usage

```bash
python scripts/aeo_tracker.py --snapshot   # record one row + print the delta
python scripts/aeo_tracker.py --status     # latest snapshot + trend since first
python scripts/aeo_tracker.py --check      # exit 1 if a witnessed surface dropped
```

`--snapshot` is idempotent on an unchanged tree (it logs change, not heartbeat;
use `--force` to record anyway). `--stamp YYYY-MM-DD` injects the date so a
snapshot is deterministic and byte-comparable across hosts. Take a snapshot
after any distribution change — a new answer page, a registry going live, a new
host wired — and the ledger row is the receipt.

`--check` is a rot pin for CI: if the latest snapshot regressed a witnessed
surface against the prior one (a page deleted, a registry delisted, a tier
dropped), it fails loudly — the same discipline as the inventory's `--check`
and the `llms.txt` link test.
