# docs/352 — skill_span: the wall the harness won't build

> **Status:** example shipped (`examples/skill_span/`), kernel verb deferred.
> The wall stands on shipped primitives (`dos.spend`, the `dos.posttool_sensor`
> fossil); it adds no kernel surface.

## The ask, and the two host facts that make the obvious approach lie

A real Claude-Code user wanted the exact **start time, end time, and token usage
of every individual skill run**. Two host facts block the obvious approach:

1. **PostToolUse fires on the DECISION to load a skill (~30–40ms), not on its
   execution.** A skill's real work happens across the *subsequent* tool calls and
   model turns of the same session — each firing its own PostToolUse. So the
   PostToolUse duration times the wrong event: the load, not the work.
2. **Token usage is only emitted at SessionEnd, aggregated** over the whole
   session — so it cannot be attributed to one skill.

These are not bugs to hook around. They are **provenance** facts: the host does not
author a clean per-skill `(duration, tokens)`, and any number you synthesize from a
point event is closer to a self-report than to telemetry.

## The DOS move: build the wall ourselves, from env-authored fossils

The kernel is the part that doesn't believe the agents. Here it is the part that
doesn't believe the **harness** either. If the host won't fence a skill span, we
fence it from the two fossils the environment *does* author:

- **the per-session stream log** `.dos/streams/<session_id>.jsonl`
  (`src/dos/posttool_sensor.py`) — one fsync'd, **timestamped** record per tool
  fire, in order. A skill run is the SPAN between two of these records, not a point.
- **the provider usage record** (`src/dos/spend.py` — `SpendBreakdown` /
  `parse_usage`) — the BILLED token aggregate, the one number an agent can no more
  shrink than inflate, normalized once at the boundary (the additive/inclusive
  wire-shape ambiguity — "the industry's double-count bug class" — resolved there).

The wall, precisely:

```
start fence  = the stream record where tool_name == "Skill"   (env-authored ts)
end fence    = the NEXT "Skill" record in the session, OR the final record
duration_ms  = end.ts - start.ts                  # from timestamps we HAVE
tokens       = usage_at(end) - usage_at(start)    # delta of the BILLED total
               ↳ only when a usage snapshot exists at BOTH fences; else REFUSED
```

## The load-bearing choice: refuse what no environment authored

A span with no usage snapshot at a fence does **not** get a guessed token count.
A proportional split of the session total by tool-fire count would be a number no
environment authored — exactly the self-report DOS exists to reject. Instead the
wall reports the real duration and leaves tokens honestly absent. **The refusal is
the wall.** This is `dos.efficiency`'s "withhold the accusation until there is
enough spend to judge" and `dos.posttool_sensor`'s "no result is never the same
result," applied to attribution.

### The three rungs (`SpanVerdict`)

| rung | condition | what it asserts |
|---|---|---|
| `ATTRIBUTED` | both fences present AND a usage snapshot at each | duration + a grounded `SpendBreakdown` token delta (input/output/cache/reasoning share) |
| `DURATION_ONLY` | fenced + timed, but a snapshot is missing at one/both fences | duration only; tokens **refused** |
| `UNATTRIBUTED` | the span can't be fenced (open final span; unparseable/backwards stamp) | neither number — the honest nothing |

A backwards / non-monotonic usage snapshot (end total below start) is a contract
error, **refused loudly** by `SpendBreakdown.of`'s own non-negativity validation —
never clamped to zero (the `dos.spend` "a silently-mended usage record is how
double-counts ship" discipline).

## The blind spots, named (not buried)

1. **`StreamStep` drops `ts`.** `dos.posttool_sensor.read_stream` replays the same
   fossil but projects to `StreamStep`, which carries only digests (it needs no
   timestamp for repeat-detection). The span wall needs `ts`, so `load_stream`
   reads the raw `.jsonl` for `ts` + `tool_name` directly — same fossil, a wider
   projection. The reuse boundary is legible, not papered over.
2. **The PostToolUse event does not today carry a per-call usage snapshot.** So in
   a live session, `DURATION_ONLY` is the realistic default until either a
   usage-bearing event lands or a SessionEnd→fence join is wired. The example
   proves the attribution MATH and the REFUSAL given snapshots; *capturing* the
   snapshots live is the host-integration follow-up.

## What shipped

- `examples/skill_span/skill_span.py` — `fence_spans` (pure: ordered records →
  spans) + the 3-rung `attribute` ladder standing on `dos.spend.SpendBreakdown`;
  `load_stream` (the raw-`.jsonl` read-side); `SCENARIOS` (the three rungs as
  fixtures); an `argparse` `main` with `--json` and `--stream PATH`.
- `examples/skill_span/test_skill_span.py` — the falsifiers, each red if the wall
  overstates: attributes-both-exactly, **no-snapshot-refuses-tokens-keeps-duration**
  (the honesty floor), open-span-is-unattributed, stands-on-the-real-spend-primitive,
  non-monotonic-usage-refused, no-skill-is-empty.

## The follow-up (a second real ask gates it)

A kernel verb `dos span` over `.dos/streams/<sid>.jsonl` is the natural next rung —
fence + difference at the CLI, exit-code per dominant rung, `--json` for a fleet
dashboard. It is deferred deliberately: the examples-only proof costs no kernel
surface, and a verb waits on a second real consumer ask so we build the right
shape, not a speculative one. The host-side capture of per-fence usage snapshots is
the other half — once an event carries usage, `ATTRIBUTED` becomes the live default.
