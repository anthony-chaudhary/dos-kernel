# Notify Decisions Cadence

`dos notify decisions` is request-response. It sends the current operator queue
when something else calls it; the kernel does not run a daemon. The recurring
caller in this repo is `.github/workflows/notify-decisions.yml`.

The scheduled workflow runs every two hours and can also be started manually. In
a credential-less checkout it is a safe canary:

```bash
python scripts/notify_decisions_cadence.py --workspace .
```

That builds and runs:

```bash
dos notify decisions --workspace . --notifier null --top 5 --dry-run
```

The null notifier sends nothing and exits green, so the workflow log is the
dry-run witness. To make the same schedule deliver, set repository variables and
secrets for a real transport.

For the built-in webhook transport:

```text
Variable: DOS_NOTIFY_DECISIONS_NOTIFIER=webhook
Secret:   DOS_NOTIFY_DECISIONS_URL=https://...
Secret:   DOS_NOTIFY_DECISIONS_TOKEN=...        # optional bearer token
```

For Slack:

```text
Variable: DOS_NOTIFY_DECISIONS_NOTIFIER=slack
Variable: DOS_NOTIFY_DECISIONS_CHANNEL=#ops
Secret:   SLACK_BOT_TOKEN=...
```

Useful optional variables:

```text
DOS_NOTIFY_DECISIONS_DRY_RUN=true   # keep a real transport non-sending
DOS_NOTIFY_DECISIONS_TOP=10         # include more ranked rows
DOS_NOTIFY_DECISIONS_ALL=true       # include ORACLE/JUDGE rows too
```

Leave `DOS_NOTIFY_DECISIONS_JSON` unset in the scheduled job. The plain CLI path
keeps the cron contract: a real transport failure exits non-zero, while null and
dry-run paths stay green.
