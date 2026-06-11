# 297 — the `helped` denominator: the hook-observation log becomes a kernel-owned contract (issue #24)

> **Status:** ✅ **SHIPPED** (2026-06-10, same day) — P1–P3 landed in `9105438`
> (the leaf + the `dos helped` rate + the Python writer, with the issue's three
> done-condition clauses pinned in `tests/test_hook_observation.py`), P4 in
> `e99bc9d` (the Go fold alignment). `Fixes #24` rides the P1–P3 commit.
>
> **Field amendments (recorded while shipping, same day):**
>
> * **One phase per ship-stamp.** The trailer stamp grammar is fail-closed on a
>   multi-phase tail — `(docs/297 P1 P2 P3)` stamps nothing (the close paren
>   must hug the single phase token). The three phases were therefore closed by
>   their real follow-up artifacts, one stamp each: the `docs/ARCHITECTURE.md`
>   leaf entry (P1), the `docs/CLI.md § cmd_helped` rate detail (P2), and this
>   status + the `§ cmd_hook_pretool` writer detail (P3).
> * **Measured cost** (the performance check): the Python fallback hot path
>   pays ~1 ms per fsync'd append, riding a ~0.3–0.8 s interpreter start; the
>   native-served path is untouched. The `dos helped` read+fold measured
>   ~0.4 ms on a 50-record log, linear in log size — which makes the log's
>   UNBOUNDED growth the follow-up worth tracking: `retention` does not cover
>   `.dos/metrics/`, a gap born with docs/276 (filed as its own issue).

## The gap

`dos helped` reports absolute counts ("DOS has caught 118 things") with no
denominator. On the lane journal, every `OP_ENFORCE` record IS an intervention,
so the verb literally cannot say what share of tool calls the substrate
touched. The rate is the number an operator actually wants ("light touch or
nanny?"), and it already exists — but only on the plugin surface: since
`561c240`, `dos-hook stats` renders "3057 adjudicated — 96.9% passed untouched,
3.1% intervened" from the per-call observation log the Go binary writes
(`.dos/metrics/observations.jsonl`, schema family `hook-observation` v1).

Issue #24 names three options. **This plan picks Option B**: the observation
record family becomes a **kernel-owned durable contract** with (eventually) two
conforming writers — the Go binary, which already writes it, and the Python
hook verbs, which gain the same append. `dos helped` then reads a contract the
kernel itself defines, not a log "only the plugin's Go binary writes."

## Why B, in one paragraph each

**Why not A (kernel reads the plugin's log as-is).** Reading is cheap, but the
schema would stay defined in `go/internal/hook/observe.go` — outside the
kernel. The kernel would parse records whose shape it does not own, which is
the awareness arrow ("the kernel is unaware the plugin exists") bent into a
data dependency. Every other durable surface the kernel reads — the lane
journal, the intent ledger, the run records — is a family the kernel declares
via `dos.durable_schema`. The observation log should not be the one exception.

**Why not C (status quo).** The flagship "what did DOS do for me" verb cannot
answer the first follow-up question every operator asks. And workspaces wired
via `dos init --hooks` with no native binary (the sdist / off-matrix-arch path)
have NO per-call observability at all today — C leaves that hole open.

**Why B.** Ownership inverts the arrow instead of bending it. The kernel module
*defines* the family — constants, entry builder, tolerant reader, pure rate
fold — exactly as `durable_schema` already prescribes for every other durable
surface. The Go binary becomes what it already behaves as: a conforming writer
(its `pyJSONDumpsWAL` grammar byte-matches the Python journal writers by
design, so conformance is the established direction). The kernel never knows
*who* wrote the log; it reads its own contract. And once the Python hook verbs
write the same family, a no-binary workspace gets the denominator it currently
lacks entirely.

## The denominator rule (the issue's caveat, made structural)

Never join the lane-journal numerator onto the observation-log denominator —
their windows and scopes differ (observed live: journal 61 BLOCKs vs 38 pretool
denies, because the journal predates the log and includes non-pretool sources).
**Like-for-like only: both numbers from the same log.** The pure fold takes
observation records and nothing else; the lane journal has no path into it.

One refinement the two-writer world forces: **a `delegate` record leaves the
denominator.** The Go binary exits 3 ("let Python decide") on a call it does
not own, and logs `outcome=delegate`. Today that call's real verdict is
invisible, so `dos-hook stats` counts the delegate in its denominator. Once the
Python fallback writes the same family, the delegated call's real verdict
appears as a SECOND record — counting both would double-count the call. The
rule that is correct in all three writer worlds (binary-only, Python-only,
mixed) is:

* **denominator** (`adjudicated`) = pretool records with `outcome != delegate`
  — one record per call, written by whichever runtime actually decided it;
* **numerator** (`intervened`) = pretool records that neither passed through
  nor delegated (deny / warn — the rungs that touched the call).

A delegate record stays visible in `by_outcome` (it is a real handoff fact);
it just never counts as an adjudication, because the adjudication it points at
is another record.

## What does NOT change

* `dos helped` on a workspace with no observation log renders **byte-identical
  output** — the new line exists only when the log does (graceful absence, the
  `source="none"` posture).
* The helped headline (the lane-journal fold) keeps its meaning and its
  numbers. The rate line is additive, names its own source, and shares no
  number with the journal counts above it.
* The kernel still imports no plugin, names no vendor, and `help_summary`'s
  "no writer here" rule survives — the writer lives in the new leaf, called
  from the hook verbs (which already write the lane journal today).

## Phases

### P1 — `dos.hook_observation`: the family contract as a kernel leaf

New module `src/dos/hook_observation.py` (stdlib + `dos.durable_schema` +
`dos.config` only):

* the family constants (`hook-observation`, version 1) and the log path
  arithmetic (`.dos/metrics/observations.jsonl` under `cfg.paths.dot_dos` —
  the `streams_dir_for` idiom);
* `observation_entry(...)` — the PURE schema-tagged record builder (the
  `_step_entry` posture): always `verb`/`outcome`/`exit`/`latency_ms` + `ts`,
  verb-specific fields written only when set, byte-compatible with what the Go
  writer emits;
* `append(entry, path)` — the fail-soft writer (mkdir + append + fsync, the
  `lane_journal.append` grammar), gated by `DOS_HOOK_METRICS != "0"` exactly
  like the Go side; a write fault never raises into a hook verb;
* `read_observations(path)` — the tolerant reader (skip torn lines, skip
  wrong-family, refuse-by-skipping a too-new version via `durable_schema`);
* `intervention_rate(records) -> InterventionRate` — the PURE fold:
  `adjudicated` / `passed` / `intervened` / `delegated` + the percents, under
  the denominator rule above. Records in, value out, no disk.

Done when: the round-trip and the fold are pinned by `tests/test_hook_observation.py`,
including the delegate-exclusion arithmetic and a reader fed a Go-written line
verbatim (fixture bytes copied from a real log, neutral paths).

### P2 — `dos helped` renders the rate (the issue's done-condition)

* `cmd_helped` reads the observation log at the CLI boundary (one `read_observations`,
  fail-soft to "absent") and hands the fold's result into the renderers.
* `render_summary_text(..., rate=None)`: a `None` rate renders today's bytes;
  a present rate adds one self-contained line sourced wholly from the
  observation log, e.g.
  `of 3057 tool calls adjudicated by the hooks, 2962 passed untouched (96.9%) and 95 were intervened on (3.1%)`
  — plus an honest note that this is a different log/window than the catch
  counts above. `--json` gains an additive `adjudicated` object only when the
  log exists.
* Tests pin the three clauses of the issue's done-condition: (1) with a log,
  the honest "of N adjudicated calls" rate renders; (2) without one, the output
  is byte-identical to the pre-change rendering; (3) the rate never mixes
  numerator and denominator across logs — a workspace whose journal counts
  disagree with its observation log renders the rate from the observation log
  alone.

### P3 — the Python hook verbs become the second writer

`cmd_hook_pretool` / `cmd_hook_posttool` / `cmd_hook_stop` / `cmd_hook_marker`
append one observation per decided call (fail-soft, downstream of the emitted
dialect, the `_journal_pretool_outcome` posture). The native-served path
(`try_native_hook` returned an exit) writes nothing — the binary already did.
A delegated call (binary wrote `delegate`, Python decided) writes its real
verdict — which is exactly what the P1 denominator rule was shaped for.
`pretool` is the essential one (it IS the denominator); the other verbs ride
the same helper for parity. Done when: a no-binary workspace accumulates
pretool observations and `dos helped` renders its rate end-to-end.

### P4 — align the Go fold to the shared denominator rule

`go/internal/hook/stats.go`: `adjudicated = pretool_calls - pretool_delegated`;
the human line and the two percents move to the new denominator; the JSON adds
`pretool_adjudicated` (additive; the raw counts stay). `stats_test.go` updates
its pinned expectations. Without this, a mixed-writer workspace double-counts
every delegated call in `dos-hook stats` while `dos helped` counts it once.

## Litmus (unchanged contracts this plan must not break)

* No module under `src/dos/` imports `dos_mcp`, `dos.drivers`, or names a
  vendor — `hook_observation` is stdlib + seam only.
* `help_summary` keeps no writer; the writer is the new leaf, called from the
  CLI hook verbs at the boundary.
* Advisory throughout (docs/99): a telemetry write fault never changes an
  emitted dialect or an exit code.
* Byte-clean (docs/138): every counted field is env-authored — the hook wrote
  the record downstream of an already-decided verdict; no agent narration
  enters either the numerator or the denominator.
