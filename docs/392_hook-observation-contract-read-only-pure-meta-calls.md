# 392 - Hook observation contract: read-only and pure-meta calls are not file-lane work

> **Status:** DESIGN SLICE for
> [issue #231](https://github.com/anthony-chaudhary/dos-kernel/issues/231).
> This publishes the contract that was missing: which calls DOS must observe for
> soundness, and how known read-only / pure-meta calls avoid empty-tree lane WARN
> noise. It does not implement matcher narrowing or replay by itself. Commit
> trailers for this slice should use `Refs #231`, not `Fixes #231`.

The bug is not "Read is noisy." The bug is that the hook path had only one
coordinate for a tool call: a file tree. A call that touches no files was encoded
as an empty or unknown tree and then compared to held lanes. That makes a pure
read or a meta/control-plane query look like an empty-tree work lane, so the
operator sees a low-signal warning against a held file lane.

The fix is a contract, not a larger skip list. A host may only narrow hook
matchers if the kernel first publishes the predicate that says which calls the
gate actually depends on. The safe direction is:

```text
known-empty file footprint  !=  unknown footprint
known-empty file footprint  !=  no effect
file-lane gate              ==  only known file writes and unknown write footprint
```

## The contract

Every hook event is classified on three independent axes before the file-lane
gate is allowed to speak:

| Axis | Values | Used by |
|---|---|---|
| File footprint | `KNOWN_PATHS`, `KNOWN_EMPTY`, `UNKNOWN` | lane arbitration, `SELF_MODIFY`, apply-gate |
| Non-file effect | `NONE`, `SPAWN`, `COORDINATION`, `CAPABILITY`, `SCHEDULER`, `EXTERNAL` | non-file blast-radius accounting |
| Result stream | `ENV_RESULT`, `NO_RESULT`, `UNKNOWN_RESULT` | PostToolUse `tool_stream` only |

The file-lane gate may emit a held-lane DENY/WARN only for `KNOWN_PATHS` or
`UNKNOWN`. It must not emit lane contention output for `KNOWN_EMPTY`. A
known-empty call can still be observed on the non-file or result-stream axes; it
just cannot collide with a file lane.

`UNKNOWN` is conservative. It means "the hook could not prove the file
footprint." It is not a synonym for empty. A shell command, MCP call, or foreign
tool with an unclassified footprint remains must-observe and may still warn or
deny. This is the line that preserves soundness for real writes.

## Minimal PreToolUse observation

The kernel must observe a PreToolUse call when any of these is true:

1. The call may write the repo file tree: direct file-write tools, patch tools,
   notebook writes, shell / PowerShell / arbitrary exec, and mutating MCP tools.
2. The file footprint is unknown.
3. The call has a non-file effect that DOS accounts for: spawn, coordination,
   capability, scheduler, or external side effect.
4. A declared `[call_shape]` policy can match the call's command, arguments, or
   write tree.
5. A provenance or apply-gate predicate reads this call's arguments at PRE.

A PreToolUse call may be matcher-skipped only when all of these are true:

1. Its file footprint is known-empty.
2. Its non-file effect is `NONE`.
3. No configured PreToolUse predicate reads its arguments.
4. The host has no stricter local policy for that tool.

That gives the operator a predicate, not a frozen vendor list. For a
Claude-shaped host, examples that are normally `KNOWN_EMPTY` on the file axis are
`Read`, `Grep`, `Glob`, `LS`, `NotebookRead`, `WebFetch`, `WebSearch`,
`CronList`, `TaskList`, `TaskGet`, `Monitor`, and `AskUserQuestion`. A host may
scope these out of PreToolUse admission only if it has mapped them to
`effect=NONE` and no active predicate reads their args.

Some tools are known-empty on the file axis but still not pure no-ops:
`Agent` / `Task` spawn work, task update verbs mutate coordination state,
`ToolSearch` changes the available capability set, and `CronCreate` /
`CronDelete` / `ScheduleWakeup` mutate scheduler state. They must not produce a
file-lane WARN, but they may still be observed or governed on the non-file axis
([docs/371](371_non-file-blast-radius-axis.md)).

## Minimal PostToolUse observation

PostToolUse cannot deny. It is not part of file-lane arbitration. Its only
sound kernel job today is `tool_stream`: append an env-authored result digest and
re-surface repeated identical results as context.

So the PostToolUse matcher is a detector-coverage choice, not an arbitration
soundness choice:

- Observe calls whose env-authored result can feed `tool_stream`: file reads,
  command output, MCP/tool output, task output, and other host result bytes.
- Skip pure meta calls with no result stream: prompts to the user, monitor
  handles with no result body, list/status calls that the operator does not want
  in `tool_stream`.
- Never run file-lane arbitration at POST. A PostToolUse event has already run;
  it can only add context.

Narrowing PostToolUse may reduce loop/stall detector coverage. It must not
change any PRE admission DENY/WARN result. That distinction belongs in the
installer output: "scoped out of POST" means "no stream signal for this tool,"
not "admission changed."

## Stop and marker hooks are outside this matcher

`Stop`, `SubagentStop`, and marker-budget hooks are not file-lane calls. They
remain wired by event name and are not made narrower by a PreToolUse/PostToolUse
tool matcher. A host config that narrows PRE/POST but drops STOP weakened the
verify-on-stop floor and fails this contract.

## Empty-tree WARN rule

The file-lane warning rule is:

```text
if footprint == KNOWN_EMPTY:
    file-lane verdict = PASS, with no operator output
elif footprint == KNOWN_PATHS:
    run admission / apply-gate / self-modify normally
elif footprint == UNKNOWN:
    observe and warn when a mutating footprint cannot be resolved
```

That means a read does not "warn less." It never enters the file-lane collision
question. The warning is reserved for a genuinely unresolved footprint where the
operator can act on the message: scope the call to a path, declare the tool
shape, or leave the broad matcher in place.

## Soundness rails

- **Unknown stays gated.** If a host cannot classify a tool, it must route it to
  DOS. A missing classifier degrades to today's broad hook behavior, never to a
  silent skip.
- **Known-empty is an evidence claim.** A tool enters `KNOWN_EMPTY` only by a
  kernel/driver mapping or a declared host policy. A name not in the mapping is
  not assumed read-only.
- **File quieting is not effect quieting.** `KNOWN_EMPTY` suppresses file-lane
  output only. Non-file effects remain visible on their own axis.
- **`[call_shape]` wins over matcher narrowing.** If a workspace declares
  forbidden command prefixes, arg substrings, or path globs, the matcher must
  still deliver any tool whose arguments can carry those bytes. If the installer
  cannot express that precisely, it keeps the broader matcher.
- **No POST decision can repair a skipped PRE decision.** A mutating call must be
  observed before it runs. PostToolUse is advisory only.
- **The host tool list is data.** The kernel publishes the predicate and the
  built-in baseline mappings it actually ships. New host tools are unknown until
  mapped by a driver or workspace policy.

## Implementation plan

### P0 - publish the contract

This document is P0. It is deliberately docs-only: it names the predicate, the
fail-safe direction, and the empty-tree WARN rule so matcher scoping has a
soundness target.

### P1 - publish a machine-readable observation manifest

Add a small kernel/driver data surface that exports the built-in hook
classification:

```text
tool_name -> file_footprint | effect_kind | post_stream
```

The surface can be a CLI JSON report, a stable Python helper, or both. The
important part is that installers read this data instead of hand-copying a
skip list. The default for an absent tool is `UNKNOWN` / must-observe.

### P2 - drive PreToolUse from the manifest

Refactor the existing read/no-footprint handling into the manifest-backed
predicate. Tests must pin:

- `Read` / `Grep` / known no-write shell reads produce no held-lane WARN.
- no-file effect calls produce no file-lane WARN but keep their effect tag.
- unrecognized tools remain must-observe and do not silently become read-only.
- `Write`, `Edit`, patch tools, write-capable shell commands, and mutating MCP
  tools keep the same DENY/WARN decisions as before.

### P3 - narrow generated hook matchers

Teach `dos init --hooks <host>` / `--hooks auto` to use the manifest when the
host supports a tool matcher. The installer must print the narrowed set and the
fallback:

- if the host supports a precise matcher, wire PRE to the must-observe set;
- if it does not, keep today's broad matcher;
- if a workspace policy makes the set wider, say so;
- never narrow STOP.

### P4 - replay the frozen trace

Before this issue can close, replay a trace containing:

- pure reads / grep / glob calls against a held lane;
- pure meta calls;
- no-file effect calls;
- a genuine same-lane write;
- a self-modify write;
- an unknown mutating shell/MCP shape;
- a PostToolUse repeated-result stream.

Acceptance is strict:

- every genuine gated write has the same DENY/WARN class before and after;
- known-empty read/meta calls emit no file-lane WARN;
- no-file effect calls are visible but not file-lane-gated;
- absence of the manifest or matcher support degrades to today's broad behavior.

## What this does not do

- It does not say read-only calls are worthless. Some reads are useful
  PostToolUse detector inputs.
- It does not make a hardcoded `Read|Grep` skip safe. The safe object is the
  predicate plus host mappings.
- It does not close [issue #198](https://github.com/anthony-chaudhary/dos-kernel/issues/198).
  That issue is the Bash self-modify read-vs-write classifier. This doc is the
  broader hook-observation contract it should obey.
- It does not close [issue #214](https://github.com/anthony-chaudhary/dos-kernel/issues/214).
  That issue is the native Go fast-path loading `[call_shape]` from `dos.toml`.
  This doc assumes whatever path serves PRE has the same policy inputs.

## References

- [191_tool-call-division-and-the-pretool-hook.md](191_tool-call-division-and-the-pretool-hook.md) - why PRE is the sound deny moment and POST is advisory.
- [217_the-cross-vendor-hook-dialect-seam.md](217_the-cross-vendor-hook-dialect-seam.md) - host dialects render one decided verdict into host bytes.
- [221_the-cross-vendor-hook-installer.md](221_the-cross-vendor-hook-installer.md) - generated host hook configs and matcher support.
- [224_the-exec-capability-classifier-a-shape-not-a-word.md](224_the-exec-capability-classifier-a-shape-not-a-word.md) - invoked-program shape discipline, not substring guessing.
- [364_tool-call-error-handling-as-a-first-class-primitive.md](364_tool-call-error-handling-as-a-first-class-primitive.md) - declared call shape and the PRE argument policy seam.
- [371_non-file-blast-radius-axis.md](371_non-file-blast-radius-axis.md) - the no-file effect axis that keeps known-empty file calls from becoming invisible.
- [125_go-hook-fastpath-build-plan.md](125_go-hook-fastpath-build-plan.md) and [270_go-hook-fastpath-benchmarks.md](270_go-hook-fastpath-benchmarks.md) - why unnecessary per-call hook spawns matter.
