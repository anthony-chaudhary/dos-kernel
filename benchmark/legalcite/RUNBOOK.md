# legalcite LIVE arm — operator runbook

This is the one credentialed step that turns the legalcite benchmark from a
**frozen-sample** result (n=18, no network — `RESULTS.md` "frozen" section) into a
**real-world, at-scale** measurement against the live CourtListener (Free Law
Project) reporter — the `docs/277 §6 #1` experiment, MEASURED rather than DESIGNED.

Everything except this step is already built and tested offline. An agent cannot run
this itself: it needs a token only a human holds, and a self-certified "live number"
without the token would be a fabricated measurement. So this is yours to run.

## 0. Prove the runner first ($0, no token)

```bash
python -m benchmark.legalcite.live_corpus --transport recorded
# or through the standardized runner:
python -m benchmark._run run legalcite --arm live_recorded
```

This runs the **same live code path** over a committed recorded transport (the
frozen reporter bytes), so you see the three-way accounting and the false-fire exit
gate work with zero network. Expect `FALSE-FIRE 0%`, `DETECT 100%` over the reachable
subset, and the extra landmark cites reported as ABSTAIN (no recorded bytes offline).

## 1. Get a free CourtListener token

1. Make a free account at <https://www.courtlistener.com/>.
2. Copy your API token from <https://www.courtlistener.com/help/api/rest/>.
3. Export it (do **not** commit it):

```bash
export COURTLISTENER_TOKEN=...        # bash/zsh
$env:COURTLISTENER_TOKEN = "..."      # PowerShell
```

Check the gate is satisfied:

```bash
python -m benchmark._run preflight legalcite --arm live   # should say READY
```

## 2. Run the live corpus

The token endpoint is rate-limited (≈5/min, 50/hr, 125/day). The runner paces itself
(`--min-interval`) and checkpoints after every cite (`--out`), so a run that hits the
quota or is interrupted **resumes** without re-querying:

```bash
# ~166 cites at 13s/call ≈ 36 min, but the daily cap is 125 — so run in two passes:
python -m benchmark.legalcite.live_corpus \
    --out live_results.json --min-interval 13 --progress

# next day (or after a Ctrl-C), continue where it stopped:
python -m benchmark.legalcite.live_corpus \
    --out live_results.json --min-interval 13 --resume --progress
```

Knobs:
- `--n-synth N` — cap the generated synth fabrications (default: all ~110). Use a
  small N for a quick first pass within one day's quota.
- `--min-interval S` — seconds between calls. 13 keeps you under 5/min; raise it if
  you see rate-limit ABSTAINs piling up.
- `--json` — machine-readable result to stdout (the checkpoint `--out` file holds the
  per-cite rows either way).

## 3. Record the number (honestly)

`RESULTS.md` ships with a **LIVE section stamped PENDING-OPERATOR-RUN**. Replace that
block with the real run's headline:

```bash
python -m benchmark.legalcite.live_corpus --resume --out live_results.json --json \
    > live_summary.json
# then paste the DETECT recall / FALSE-FIRE / ABSTAIN counts + the date into RESULTS.md
```

State it as what it is: a **live, at-scale** DETECT recall + FALSE-FIRE rate over the
**reachable** cites, with the ABSTAIN count called out separately. Do **not** fold
ABSTAINs into either rate, and do **not** report a number you did not run. `J` (caught
count) is a caught-count, never a downstream outcome — see `RESULTS.md` caveats.

## What you are measuring (and not)

- **Measuring:** does the shipped `citation_resolve` driver, at scale, against the
  live third-party reporter, flag fabricated/mis-quoted cites (the *Mata* class) while
  never flagging a real one. Tier-1, non-forgeable (you authored none of the reporter
  bytes).
- **Not measuring:** whether any legal argument is correct (Tier 3 — the driver
  abstains), and not a downstream ΔB (a sanction avoided, a case won).

## Optional: refresh in CI

To keep the live number fresh without a local run, add `COURTLISTENER_TOKEN` as a
repo secret and a guarded CI job that runs the `live` arm only when the secret is
present (skips cleanly otherwise). That is the one outward-facing piece; wire it under
the `ci` lane when you want it.
