# Cookbook — wiring DOS into a pipeline

> Recipes for making DOS a **gate** in real plumbing: CI, pre-commit, and an
> agent host (MCP). The common thread is that **the syscall's exit code is the
> verdict**, so DOS drops into any shell-driven gate with no glue.

The exit-code contracts these recipes rely on (all verified against the shipped
CLI):

| Command | Exit codes |
|---|---|
| `dos verify P PH` | `0` shipped · `1` not shipped |
| `dos doctor --check` | `0` clean · `1` a finding fired |
| `dos arbitrate ...` | `0` acquire · `1` refuse |
| `dos gate PACKET` | `0` LIVE · `3` DRAIN · `4` STALE-STAMP · `5` BLOCKED · `6` RACE · `2` contract error |

---

## Recipe 1 — a GitHub Actions ship-gate

Fail a PR that **claims** to close a phase that didn't actually ship. The PR
author puts the phase in the title/body (e.g. `Closes AUTH AUTH2`); the gate
checks it against git history.

```yaml
# .github/workflows/dos-ship-gate.yml
name: DOS ship gate
on: [pull_request]

jobs:
  verify-claimed-ship:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # verify needs full history for the ancestry check

      - name: Install DOS
        # The dist name is `dos-kernel` (the bare PyPI name `dos` is an unrelated
        # package). Pin a version with `dos-kernel==X.Y.Z` for reproducible CI.
        run: pip install dos-kernel

      - name: Confirm the stamp grammar matches this repo
        run: dos doctor --workspace . --check       # exit 1 → misconfigured [stamp]

      - name: Verify the claimed phase actually shipped
        run: |
          # parse "Closes <SERIES> <PHASE>" out of the PR body (example)
          SERIES=AUTH; PHASE=AUTH2
          if dos verify --workspace . "$SERIES" "$PHASE"; then
            echo "✓ $SERIES $PHASE is shipped in history"
          else
            echo "::error::PR claims $SERIES $PHASE shipped, but no commit attributes it"
            exit 1
          fi
```

The `--check` step is worth keeping: it fails the build if someone edits
`dos.toml`'s `[stamp]` into a grammar that no longer matches the repo's real
ship commits — catching the misconfiguration before it silently makes `verify`
answer `via none`.

## Recipe 1b — the same gates on GitLab CI (one include line)

The GitLab population never sees a GitHub Action. The shipped template
[`gitlab-ci/dos-verify.gitlab-ci.yml`](../../gitlab-ci/dos-verify.gitlab-ci.yml)
is the `verify-action/` twin: a `dos-verify` job that audits an MR's commits
(claim vs diff, `dos commit-audit --sweep` over
`$CI_MERGE_REQUEST_DIFF_BASE_SHA..HEAD`) and optionally requires a stamped
phase (`dos verify`, set `DOS_PLAN`/`DOS_PHASE`). The exit code is the verdict.

```yaml
# .gitlab-ci.yml
include:
  - remote: 'https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/gitlab-ci/dos-verify.gitlab-ci.yml'
    # pin a release tag instead of master for reproducible CI
```

Make the `dos-verify` job required (pipeline must succeed) and GitLab enforces
what the kernel decides. Knobs are CI variables: `DOS_VERSION` (pip pin),
`DOS_FAIL_ON: none` (observe-only), `DOS_WORKSPACE`.

**Prefer a searchable, semver-pinned form?** The same gate ships as a GitLab
[CI/CD Catalog component](../../gitlab-ci/templates/dos-verify.yml) —
discoverable in the catalog UI and pinned by version (`@1.0.0`) with a typed
`inputs:` interface instead of bare CI variables. See
[`gitlab-ci/README.md`](../../gitlab-ci/README.md) for both forms; the component
is published from a GitLab mirror project (the catalog lives on GitLab; this
source-of-truth repo is on GitHub).

**The one pitfall is `GIT_DEPTH`.** The audit reads git *ancestry*; GitLab's
default shallow clone (20–50 commits) amputates the evidence base, and an MR
with more commits than the depth audits against a hole. The template forces
`GIT_DEPTH: "0"` (the same reason the GitHub action checks out with
`fetch-depth: 0`) — if you override the job, keep it.

## Recipe 2 — verify a release manifest before tagging

Before cutting a release, confirm every phase the release notes claim is real.
One non-shipped phase fails the job.

```yaml
      - name: Verify release manifest
        run: |
          rc=0
          while read series phase; do
            if dos verify --workspace . "$series" "$phase"; then
              echo "  ✓ $series $phase"
            else
              echo "::error::release manifest claims $series $phase — NOT shipped"
              rc=1
            fi
          done < release-manifest.txt
          exit $rc
```

`release-manifest.txt` is just `<series> <phase>` per line. This is the
machine-checkable version of "is the changelog telling the truth?"

## Recipe 3 — a pre-commit hook

Stop a commit whose message *claims* a phase shipped when it isn't actually
attributed in history yet. (Catches "mark it done in the message, forget to do
the work" locally, before it reaches CI.)

```bash
#!/usr/bin/env bash
# .git/hooks/commit-msg   (chmod +x)
# Usage: a commit msg line "DONE: <SERIES> <PHASE>" is verified before the commit lands.
msg_file="$1"
claim=$(grep -oE '^DONE: [A-Za-z0-9]+ [A-Za-z0-9.]+' "$msg_file" || true)
[ -z "$claim" ] && exit 0           # no claim → nothing to check

read _ series phase <<< "$claim"
if dos verify --workspace . "$series" "$phase"; then
  echo "dos: $series $phase verified shipped — ok"
  exit 0
else
  echo "dos: refusing commit — it claims '$series $phase' DONE but no commit attributes it" >&2
  echo "     (drop the DONE: line, or actually ship the phase first)" >&2
  exit 1
fi
```

For the [`pre-commit`](https://pre-commit.com/) framework, the same as a local
hook:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: dos-doctor-check
        name: dos stamp-grammar check
        entry: dos doctor --workspace . --check
        language: system
        pass_filenames: false
```

## Recipe 3b — audit a commit's claim vs its diff (ANY git project, no plan)

The recipes above check a *declared* `(SERIES, PHASE)` claim — they need DOS's plan
vocabulary. `dos commit-audit` needs **none of that**: it grades whether a commit's
*subject* is witnessed by its own *diff*, on any repo, for **human or agent** commits
alike (a person's `fix: …` that touched only a README is the same unwitnessed claim
as an agent's `--allow-empty "shipped"`; docs/214). The exit code is the verdict:
`0` clean · `1` an unwitnessed claim found · `2` unreadable ref.

```bash
#!/usr/bin/env bash
# .git/hooks/post-commit   (chmod +x) — an advisory nudge after each commit
dos commit-audit --warn-only HEAD     # --warn-only never fails; just prints
```

Gate a PR's own commits in CI (fail a PR whose commits over-claim):

```yaml
      - name: Audit each commit's claim against its diff
        run: dos commit-audit "origin/${{ github.base_ref }}..HEAD"
        # exit 1 if any commit claims a code/test change its diff doesn't show
```

A one-shot audit over recent history (find the message-vs-diff drift already landed):

```bash
dos commit-audit HEAD~50..HEAD          # newest-first; ⚑ marks the unwitnessed
```

It grades *did the diff do the KIND of thing claimed*, never *was it correct* (run
the tests for that). It ABSTAINs on `wip`/`merge`/`bump` and anything with no concrete
claim, so it only fires where a real claim and a contradicting diff coexist.

## Recipe 4 — gate a dispatch in a shell loop

If you're orchestrating agents from a script (not the shipped skills), branch on
the gate verdict. The verdict *is* the exit code, so a `case` on `$?` is the
whole control flow:

```bash
dos gate --workspace . "$PACKET_SIDECAR"
case $? in
  0) echo "LIVE — dispatching";           launch_dispatch "$PACKET_SIDECAR" ;;
  3) echo "DRAIN — nothing to do";         archive_noop ;;
  4) echo "STALE-STAMP — reconciling";     run_replan_stamp ;;   # don't re-ship
  5) echo "BLOCKED — surfacing to operator"; notify_operator ;;
  6) echo "RACE — retrying snapshot once"; resnapshot ;;
  2) echo "contract error — bad packet" >&2; exit 2 ;;
  *) echo "unknown gate verdict" >&2;      exit 1 ;;
esac
```

Same pattern guards the arbiter before taking a lane:

```bash
if dos arbitrate --workspace . --lane "$LANE" --kind cluster --leases "$LIVE"; then
  echo "acquired $LANE — starting"
else
  echo "lane refused — waiting" ; exit 0      # NOT --force; a refuse is real
fi
```

## Recipe 5 — let an agent call the referee directly (MCP)

The highest-leverage integration for an **agentic** workflow: instead of your
agent shelling out, expose the syscalls as MCP tools so Claude (Desktop / Code),
Cursor, Cline, or an Agent-SDK app calls `verify` / `arbitrate` / the refusal
vocabulary natively.

```bash
# the server is an optional extra; the kernel stays near-stdlib. Dist name is
# `dos-kernel` (the bare `dos` on PyPI is unrelated):
pip install 'dos-kernel[mcp]'
```

```jsonc
// claude_desktop_config.json  (or .mcp.json for Claude Code)
{
  "mcpServers": {
    "dos": {
      "command": "dos-mcp",
      "env": { "DISPATCH_WORKSPACE": "/path/to/the/repo/it/should/serve" }
    }
  }
}
```

Now the agent has tools `dos_verify`, `dos_arbitrate`, `dos_refuse_reasons`,
`dos_check_reason`, `dos_doctor`, plus user-invokable prompts
(`/verify_a_claim`, `/can_i_take_this_lane`, `/refuse_with_a_reason`). Each
decision tool returns an `interpretation` line ("what this means for your next
action"), so the model acts on guidance, not a bare dict. Every tool honors the
served repo's `dos.toml`, so the lane taxonomy and stamp grammar flow straight
through. Full surface: [`src/dos_mcp/README.md`](../../src/dos_mcp/README.md).

> **Why this is the adoption beachhead.** It's the only integration with *zero*
> Python coupling — JSON over stdio. An agent that can call `dos_verify` stops
> accepting "I shipped it" on faith, which is the entire point of the kernel,
> with no code in your project at all.

## Recipe 6 — a non-zero-exit safety net in any pipeline

A one-liner you can drop anywhere to assert "this phase is shipped or fail":

```bash
dos verify --workspace . "$SERIES" "$PHASE" \
  || { echo "::error::$SERIES $PHASE not shipped"; exit 1; }
```

…and the inverse, "fail if a phase that should *not* be shipped somehow is" (a
guard against accidental early ships on a release branch):

```bash
dos verify --workspace . "$SERIES" "$PHASE" \
  && { echo "::error::$SERIES $PHASE shipped on a branch where it shouldn't be"; exit 1; }
```

---

## Notes

- **`fetch-depth: 0`** in CI checkouts — `verify`'s grep rung walks `git log` and
  ancestry-checks against `HEAD`; a shallow clone can hide the attributing commit.
- **Run `dos doctor --check` in CI** once, early. It's the cheapest insurance that
  your `[stamp]` grammar still matches how the repo stamps ships — a wrong grammar
  turns every `verify` into a false negative.
- **Never `--force` in automation.** A refuse/negative is information; forcing past
  it defeats the gate. `--force` is an operator action (see
  [playbook 05](05_infra-monorepo.md)).
- For the **code** equivalent of these gates (embedding rather than shelling),
  see [`cookbook-python-api.md`](cookbook-python-api.md).
