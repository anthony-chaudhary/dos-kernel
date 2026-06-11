# The `dos` CLI — the long-form prose moved out of `cli.py`

> `src/dos/cli.py` is the umbrella CLI — a layer-3 *thin shell* (see
> [CLAUDE.md](../CLAUDE.md)) that grew a third of its bulk as inline design
> prose. This file is where that prose now lives: every function docstring
> longer than a screen and every multi-line comment block was moved here and
> replaced in the code by a one-line summary plus a `docs/CLI.md § <name>`
> pointer. The code keeps the *what*; this file keeps the *why* and the
> `docs/NN` lineage.
>
> Conventions: a **§ heading below is the function name** for command
> docstrings, or the **first line of the comment block** for section notes —
> grep either to land in both places. Runtime-facing help text (the module
> docstring behind `dos --help`, the `description=`/`epilog=` argparse
> literals) stays in the code: it is user-visible output, not commentary.

## Command & helper docstrings (by function)

### § `_resolve_driver_config`

Resolve a host policy driver BY CONVENTION — no hardcoded host name.

The generic loader behind `dos --driver <name>`: imports `dos.drivers.<name>`
and calls its `<name>_config(workspace)` factory, returning the built
`SubstrateConfig`. This is what lets a new host be added as a single module
under `src/dos/drivers/` without the CLI (a layer-3 helper) ever learning its
name — the same one-way arrow the kernel obeys. `--job` is just the back-compat
spelling of `--driver job`; `dos.drivers.workshop` is the reference template.

Raises a plain `ValueError` (the CLI maps it to a usage error / exit 2) when
the driver does not exist or exposes no `<name>_config`. A driver name is a
single module token by convention, so a dotted/path-y name (`foo.bar`,
`../evil`) is rejected up front as "unknown" — this both avoids a path-traversal
surface AND sidesteps the `ModuleNotFoundError.name`-aliasing that would
otherwise let such a name escape the guard as a raw traceback. For a real
single-token driver, a `ModuleNotFoundError` is treated as "unknown driver"
ONLY when it names the driver's own (missing) module — a `ModuleNotFoundError`
from a driver's BROKEN INTERNAL import (a missing third-party dep) is re-raised
so a genuine bug is never masked as "no such driver."

### § `_apply_workspace`

Install the workspace the user pointed at as the process-active config.

Also folds in any policy the workspace DECLARED in its `dos.toml` — the
no-code hackability path. Four tables are read back; a missing/empty table
always leaves the built-in base, so every existing command is byte-unchanged
for a workspace that declared none (the shared additive-degradation rule):

  * `[reasons]` — extra block reasons, layered (ADDITIVE) onto the base
    registry.
  * `[stamp]`   — this repo's ship-subject grammar, OVERRIDING the base
    convention (declaring `subject_dirs` means "these are MY dirs"). This is
    the seam that makes `verify` work against a foreign repo's commit
    convention; without this readback `cfg.stamp` was stuck at the job
    default and the declared `[stamp]` table silently no-op'd (SCV1).
  * `[lanes]`   — this repo's lane taxonomy, REPLACING the base taxonomy
    wholesale (declaring lanes means "these are MY lanes"). Without this
    readback a foreign repo's `[lanes]` silently no-op'd and `arbitrate` saw
    only `main`/`global` (WCR Phase 1).
  * `[paths]`   — this repo's state layout, OVERRIDING only the fields it
    names (e.g. `plans_glob = "planning/*.md"`), inheriting the rest. Lets a
    host whose plans/state live off the job-shaped path say so in data
    (WCR Phase 2).

The resolution order (WCR Phase 3a) THIS function encodes, highest precedence
first: these `dos.toml` tables › the `--driver <name>` host policy pack (which
`--job` is the back-compat alias for, `--driver job`) › the `default_config`
generic. A `dos.toml [lanes]` therefore wins over `--driver`. The `--driver`
NAME is resolved to a `SubstrateConfig` HERE (`_resolve_driver_config`, by
convention) and passed to `load_workspace_config` as an already-built `base=`,
so `config` never learns a host name — the one-way arrow holds.

Note on the in-code rung: a CLI command always rebuilds from scratch here, so
a `set_active(...)` installed before running a `dos` SUBCOMMAND is deliberately
NOT preserved — the workspace the user pointed `--workspace`/cwd at is
authoritative for the CLI surface. The "explicit `SubstrateConfig` in code"
rung applies to DIRECT LIBRARY callers who pass `cfg=` into a syscall
(`oracle.is_shipped(cfg=…)`, `arbiter.arbitrate(config=…)`), never through a
`dos` subcommand. (Don't re-add a "honor a pre-installed active config" branch
here without a sentinel distinguishing an explicit install from the lazy
env-default — otherwise a stale process-global would silently shadow the
pointed-at workspace.)

Two deliberate asymmetries to keep straight:
  * reasons are ADDITIVE, lanes/paths/stamp are REPLACE/OVERRIDE.
  * lanes/paths default GENERIC and a host replaces them (declaring more is
    safe); stamp defaults STRICT (job grammar) and a host loosens it knowingly
    (permissive is the dangerous direction — false-positive ships). Same
    mechanism, opposite safe defaults, for principled reasons.

The four-table readback itself now lives in ONE shared implementation,
`config.load_workspace_config` (so the CLI and the MCP server can't drift —
they carried byte-identical copies of this loop). This function is the CLI's
thin adapter over it: build from the pointed-at workspace, then `set_active`.
The default stderr `warn` sink reproduces the prior byte-for-byte warning.

### § `_maybe_observe`

Record one `VerdictEvent` to the verdict journal IFF observation is armed.

Called by the verdict verbs right as they emit (docs/262 P2). FAIL-SOFT twice
over: it no-ops unless `_observe_enabled`, and the underlying
`verdict_journal.record` swallows every write error — so wiring this into a
syscall can never change that syscall's exit code or crash it. `run_id`/`lane`/
`subject` are pulled from `args` when not passed explicitly (the common verbs
carry `--run-id`/`--lane`); `detail` defaults to the verdict's own to_dict()
minus its prose `reason` (the byte-clean evidence counts, never narration).

### § `_ensure_home_if_persisting`

Lazily scaffold `.dos/` for the active workspace — the first-write hook.

Called by PERSISTING handlers only (`dos lease`, and `dos arbitrate --force`
on a captured override), AFTER `_apply_workspace`. Read-only handlers
(`verify`/`man`/`doctor`/`decisions`/`judge`/`journal`) never call it, which
is what makes `dos verify` in a stranger's repo write nothing (docs/75 §6.5).
`dos init` deliberately does NOT call it — `.dos/` is created by the first
real emission, not by scaffolding `dos.toml`.

### § `_resolve_output_name`

Which renderer a command should use, reconciling `--output` and `--json`.

`--output <name>` (default `"text"`) is the RND selector. The pre-RND
`--json` flag is kept for back-compat and maps to the built-in `json`
renderer — BUT only when `--output` was not given explicitly, so a future
`--output terse --json` resolves to the explicit `--output`. (No command
accepts both meaningfully; this just fixes the precedence deterministically.)

### § `_render_to_stdout`

Resolve the selected renderer, call `method(obj)`, print the string.

The single output seam every decided-object command routes through (RND).
Returns 0 on success, non-zero if `--output` named an unknown renderer — in
which case it prints a loud, actionable error to stderr (the completeness
posture: a typo'd `--output` never silently falls back to text). The caller
maps a non-zero return onto its own failure exit code.

`colorize`, when given, is a `(rendered_line) -> str` wrap applied ONLY to the
built-in `text` renderer's output and ONLY when the caller has decided a human
is watching (see `_color_enabled`). It is presentation polish layered at the
boundary, never in the renderer — so the byte-faithful `text`/`json` forms
(and any plugin) are untouched, and a pipe/JSON consumer always gets exact
bytes. A non-text/plugin renderer is never colorized (its bytes are its
contract).

### § `_emit_with_explanation`

Emit a verdict WITH the opt-in `--explain` next-action interpretation.

The companion to `_render_to_stdout` for the `--explain` path. It is a
SEPARATE path precisely because the built-in `text`/`json` renderers are
byte-faithful by contract (tests/test_render.py pins them) — they must NOT
grow an `interpretation` key. So `--explain` (off by default) is the one
place the emitted shape intentionally diverges from the renderer's bytes:

  * machine form (`--json` / `--output json`) → the verdict's own `to_dict()`
    with an added `interpretation` field, as compact sorted JSON (or
    `--pretty` indented). This MATCHES the MCP tool's return shape exactly.
  * human form (default `text`) → the normal rendered line (colorized if a
    human is watching), then the interpretation on a trailing dimmed line.

A non-built-in `--output <plugin>` with `--explain` is unusual (a plugin's
bytes are its own contract); we render the plugin's line and still append the
interpretation line, never injecting into the plugin's structure.

Returns `exit_code` on success (the caller's shipped/refuse code), or 2 if a
named `--output` renderer was unknown — the same contract `_render_to_stdout`
has, so `--explain` never changes a command's exit semantics.

### § `_render_one`

Call `renderer.method(obj)`, falling back to the built-in text form when
the renderer doesn't implement the surface OR its method raises (RND).

Two fallbacks, both to the canonical built-in `text` form (never a different
plugin):

  * MISSING surface — a plugin renderer (e.g. the example `TerseRenderer`)
    may implement only the surfaces it cares about; the optional surfaces
    (`render_timeline` / `render_man` / `render_decisions`) fall back when
    absent, so `--output terse` on a man page renders readable text rather
    than `AttributeError` (Phase 3a).
  * RAISING surface — a buggy third-party renderer whose method throws must
    not crash the whole command with a traceback. The design invariant is
    "the worst a buggy renderer can do is produce ugly text" (it can never
    mis-verify a ship or mis-admit a lease — rendering is downstream of the
    decision, which has already been made and is unaffected). So a raise is
    caught, a one-line stderr note names the culprit, and we fall back to the
    built-in text form. We never fall back text→text into the same except
    (the built-in forms don't raise on well-formed kernel objects); if the
    TEXT fallback itself somehow raises, that is a genuine kernel bug and is
    allowed to propagate.

### § `_color_enabled`

True iff we should emit ANSI for a human at an interactive terminal.

Honored, in order: an explicit `--output`/`--json` (machine form — never
colored); then `NO_COLOR` (https://no-color.org — any value disables, and per
the convention it takes precedence over a force-on); then `DOS_COLOR=always`/
`never` override; then `stdout.isatty()`. The default is "color only when a
human is watching," so pipes, redirects, CI logs, and JSON consumers all
receive the byte-faithful renderer output.

### § `_color_verdict_line`

Wrap a rendered verdict line so the SHIPPED/NOT_SHIPPED mark and the
`(via <rung>)` rung read as the tell — additive only, byte-identical content.

A SHIPPED verdict goes green; a NOT_SHIPPED goes red. The rung suffix is the
real signal, so it gets its own badge color GRADED BY FORGEABILITY (docs/118):

  * a NON-forgeable rung — `registry` (a `mark done` write) or `grep-artifact`
    (the file-path/diff rung: a commit cannot fake which files it touched) —
    is GREEN: a real artefact moved.
  * a FORGEABLE rung — `grep-subject` (the ship rests only on the commit
    SUBJECT/BODY the agent itself authored, satisfiable by `git commit
    --allow-empty`) — is YELLOW: shipped, but on evidence the agent controls.
  * `none` (no evidence at all) is a bold-red `(via none)` — the "your
    dashboard said done; git proved nothing" caption, in one glance.
  * bare `grep` (an un-graded fallback) keeps the old green.

So a supervisor scanning `dos verify` output sees at a glance when a SHIPPED
stands on the gameable rung (yellow) versus the diff (green) — the read-side
tell of the mediated-apply-gate. The function only inserts escape codes around
substrings already present; it never changes a character of the verdict text
(so a `| cat` still shows the literal `(via grep-subject)`).

### § `_render_init_config`

Build the scaffolded `dos.toml` text + a one-line summary of what it did.

Auto-derives a disjoint concurrent lane per top-level source dir (each owning
``<dir>/**``), plus an EXCLUSIVE ``global`` over the whole repo. A repo with
no source dirs falls back to the honest single-writer default: one exclusive
``main`` over ``**/*``, no concurrent lanes (so `doctor --check` stays clean —
an exclusive lane never enters the disjointness algebra).

### § `_resolve_driver_taxonomy`

The `LaneTaxonomy` of a named driver pack — the single source of truth.

Resolves `dos.drivers.<name>.<name>_config`, calls the factory (workspace-blind:
no root needed to read the taxonomy), and returns `config.lanes`. This is the
SAME by-convention contract `dos --driver <name>` uses, so a driver that the CLI
can load, quickstart can scaffold from — no separate registry, no duplicated
lane data. Raises ImportError/AttributeError/ValueError if the name is unknown
or malformed (the caller turns that into a friendly `--driver` error).

### § `_quickstart_driver_arbitration_beat`

Demo a driver's CONCURRENCY policy via the real `arbiter.arbitrate` kernel.

The truth-syscall beat shows verify(); this shows the admission half a stranger
can't otherwise see: two disjoint cluster lanes admit together, and an exclusive
lane refuses while a lease is live. Pure call (no I/O) over the driver's taxonomy
— same kernel `dos arbitrate` uses. Best-effort: the caller swallows any error so
the demo beat never breaks the verdict contract.

### § `_quickstart_fleet_act`

The fleet act of the DEFAULT quickstart — the admission half of the pitch.
(The caller's narration frames it wider than "fleet": two coding-agent tabs
open on one repo are the same two-writers-one-tree hazard.)

The verify contrast catches the LIE; this catches the COLLISION. Three calls
through the real `lane_lease.acquire` — the DURABLE verb (`dos lease-lane
acquire`), each grant journaled to the demo repo's lease WAL: agent A is
admitted onto `src`; agent B asks for the SAME region and is redirected to the
free disjoint `docs` (the clobber that never reached the files); agent C asks
when every lane is held and gets a typed refusal, not a silent overwrite.

Journaling for real — rather than threading three pure `arbiter.arbitrate`
calls through an in-memory lease list, as this act originally did — IS the
lesson, learned from a field replay: a reader who watched the old transcript's
`$ dos arbitrate --lane src` escalate (acquire → redirect → refuse) and then
typed that same command twice in their own repo got `acquire` twice (arbitrate
journals nothing; the demo's state lived in a list they couldn't see) and
reasonably read it as a double-booking. So the act now (a) narrates the verb
whose replay actually reproduces the escalation, (b) shows WHERE the state
lives (`$ dos lease-lane live` — the journal fold, printed from the real
`live_leases`), and (c) closes by stating the ask/hold split out loud:
`arbitrate` reads the journal but never writes it. Under `--keep` the journal
survives in the kept repo, so the escalation is literally re-runnable
(pinned by `test_quickstart_keep_dir_fleet_act_replays_from_the_journal`).

Every outcome/lane/reason printed is the decision object's own field, never a
canned string — the same honesty rule the verify beats follow. Best-effort:
the caller swallows any error so the demo beat never breaks the verdict
contract.

### § `_install_hooks`

Merge the DOS hooks into `dest_root/.claude/settings.json`.

Returns (wired, already) — the events newly wired and the events that already
had a `dos hook …` group (skipped, the idempotent path). Merges into an
existing settings.json (preserving the user's other hooks/keys); a malformed
existing file is reported by raising `ValueError` (the caller surfaces it),
never silently overwritten unless `force`. `force` re-adds the DOS group even
when one is already present (e.g. to repair a command string).

### § `_install_host_hooks`

Wire the DOS hooks into `host`'s config file under `dest_root`.

Returns `(spec, config_path, wired, already, proposed_text)` — the resolved
`HostHookSpec`, the absolute path written (or that WOULD be written under
`dry_run`), the events newly wired, the events that already had a DOS entry
(skipped — idempotent), and the full proposed config text. Raises `ValueError`
on an unknown host or a malformed existing config (the caller surfaces it;
`force` rescues the latter).

With `dry_run=True` the merge is computed exactly as for a real write, but
nothing is written and no parent dir is created — the caller prints
`proposed_text` so an operator can preview the settings.json/TOML merge before
committing it (the "dress rehearsal" the binary hook-wiring lacked). The merge
is the SAME pure `merge_json`/`merge_toml` either way, so the preview is exactly
what a subsequent real run produces.

### § `ExitMap`

A verb's verdict-token → exit-code map (the verdict IS the exit code).

`codes` is the verb's own verdict dict, VERBATIM — `.codes` is byte-identical
to the old `_FOO_EXIT_CODES` literal it replaces, so every existing call site
(`["contract_error"]` index, `.get(token, unknown)`) keeps working unchanged.
`unknown` (the never-success floor for a token the CLI hasn't caught up to)
and `contract_error` (the shared usage code, always 2) are carried ALONGSIDE
the dict so the conventions live on the object, not as two more loose literals
per verb — but they are NOT spliced into `.codes` (a multi-valued verb's map
historically omitted `contract_error`, and `.codes` preserves that exactly).

### § `emit`

Render a single-line verdict verb's tail and return its exit code.

The byte-faithful render-and-return shared by the one-line verdict verbs
(liveness / productivity / breaker / exec-capability / hook-exit): under
`--json` (or `--output json`) print the verdict's sorted `to_dict()`;
otherwise a single `"<token>  <reason>"` line; then map `token` to its code
(the `unknown` floor for a token this map omits). `token` is the headline
verdict string — `verdict.<field>.value` for most verbs, a precomputed
string for hook-exit (whose headline is not a bare enum value). The verdict
only needs a `.to_dict()` and a `.reason`, so this stays verb-agnostic.

### § `cmd_commit_audit`

Does a commit's CLAIM match what its DIFF actually did? Author-neutral.

The universal, plan-free form of the kernel's byte-author ≠ claimant floor:
a commit subject is forgeable (whoever wrote the message authored it), the
files it touched are not (the commit machinery did). Works on HUMAN commits
exactly as on agent commits — `fix: ...` touching only a README is the same
unwitnessed claim as an agent's `--allow-empty "shipped"`. Reads git at the
boundary; the verdict (`commit_audit.classify`) is pure. Advisory by default
in spirit (a doc-only fix is sometimes legitimate) but the exit code is a real
gate signal so it drops into pre-commit/CI with no glue.

### § `_gather_non_git_rung`

Gather the workspace's non-git oracle verdict for `sha` → a `NonGitRung`.

The docs/265 §2c boundary gather: the host declared `[verify] non_git_oracle`
(a `dos.evidence_sources` driver name), and the git rung confirmed a ship at
`sha`. We resolve that driver BY NAME (`_load_witness_driver` — the same
one-way-arrow bulkhead `dos attest` uses; cli.py is layer 3 and may not
`from dos.drivers import …`), call its `status_of(sha)` where the `gh api`
subprocess lives, and map the typed CI verdict to the kernel-local `NonGitRung`:

  * GREEN              → upgrade label ``"ci-green"`` + state GREEN (the oracle
                        upgrades the git ship's `source`)
  * RED                → state RED (the upgrade is WITHHELD + the ship flagged)
  * PENDING / NO_SIGNAL → that state, which `_apply_non_git_rung` passes through
                        as the git verdict unchanged (the safe degrade)

Returns None (→ the caller leaves the git verdict untouched) on ANY failure:
the driver missing, not exposing `status_of`, or raising. FAIL-SAFE — a broken
or unreachable oracle can only LEAVE the git answer as-is, never redden or
fabricate it (the `ci_status`/`run_judge` fail-to-abstain posture, restated at
the boundary). The `[ci] repo` knob is passed through if the workspace declared
one; otherwise the driver's own default repo answers (its dog-food remote).

The upgrade label is `"ci-green"` for the CI oracle (the plan's fixed first
label); a future non-CI driver mints its own through the same unchanged seam.

### § `_load_witness_driver`

Resolve a witness DRIVER (`os_acceptance` / `state_diff`) by name — never a
static import.

`dos attest` gathers its read-back from an out-of-kernel witness the same way
`dos watch` drives the watchdog and `dos memory` the recall driver. cli.py is
layer-3; the one-way arrow forbids it from `from dos.drivers import …`. So we resolve
`dos.drivers.<name>` by NAME via `importlib` here — the same bulkhead as
`_load_watchdog` / `_load_memory_recall`. Pinned by
`tests/test_vendor_agnostic_kernel.py::test_no_kernel_module_imports_a_driver`.

### § `_gather_attest_readback`

Gather the independent read-back at the boundary, returning (verdict, surface, err).

Mirrors the witness-driver CLIs: the host names a witness SURFACE the agent does not
control, the kernel re-reads it, and `witness_effect` joins it to the claim. Two
shipped surfaces (docs/246 §1.3):

  * `--accept-cmd CMD` → the `os_acceptance` witness (the OS authors the exit code;
    OS_RECORDED). The cheapest non-forgeable witness a deployment has.
  * `--before B.json --after A.json` → the `state_diff` witness (the store authors
    the delta; OS_RECORDED, or THIRD_PARTY with --third-party for a remote store).

Returns `(EffectWitnessVerdict, witness_surface, "")` on success, or
`(None, "", error_message)` on a usage/read fault the caller maps to contract-error.
Exactly one surface must be given. The witness drivers live OUTSIDE the kernel (they
have the I/O the kernel forbids); this boundary helper resolves them BY NAME (the
one-way-arrow bulkhead, `_load_witness_driver`), and the kernel `dos.attest` module
imports neither.

### § `cmd_attest`

Mint a portable, signed receipt over an effect-witness verdict (docs/246 Phase 1).

The non-participant surface over `effect_witness`: it gathers an INDEPENDENT
read-back at the boundary (the `os_acceptance` / `state_diff` witness), joins it to
the agent's claim via `witness_effect`, wraps the four-valued verdict in a
`dos.attest.Receipt`, and SIGNS it (HMAC-SHA256 in Phase 1) so a third party who was
not present verifies the certificate with the shared key alone (`dos verify-receipt`).

The signed payload carries the witness's AUTHOR and TIER, not just the verdict token
(docs/246 §2.1) — a `CONFIRMED` at OS_RECORDED is a different evidentiary object than
one at THIRD_PARTY, and the verifier reads the verdict together with the rung it
stands on. REFUTED is the load-bearing adverse certificate; UNWITNESSED stays a
distinct, non-adverse token (§2.3). The verdict is the engine's, untouched — this
writes no new decision logic, only packaging + a signature.

The exit code is the verdict the receipt carries (CONFIRMED 0 / REFUTED 1 /
UNWITNESSED|NO_CLAIM 3 / contract-error 2). `--json` emits the full signed receipt;
the text form prints the headline + the receipt fields.

### § `cmd_verify_receipt`

Check a portable receipt's signature — the third-party surface, NO loop access (docs/246).

The verifier was NOT present: given a `Receipt` (a JSON file / stdin) and the
shared/public half of the signing key, it re-derives the canonical bytes and checks
the signature, then renders the verdict WITH its tier. It verifies the SIGNATURE and
the TIER; it does NOT re-run the witness (a richer future verifier could — out of
scope here). The one place that fails LOUD: an invalid signature → INVALID, exit 1,
never a silent downgrade.

UNWITNESSED is rendered as a visibly distinct, NON-ADVERSE outcome (docs/246 §2.3) —
a valid receipt that simply could-not-tell is not the same as an adverse REFUTED.

### § `cmd_verify_result`

The fold-site result-state witness: did a subagent's terminal record DIE? (docs/197 §7(1)).

The keystone catch. An ultracode `Workflow` folds `agent()`'s self-authored
return as ground truth at the `${result}` interpolation, and 32% of real
subagents (docs/197 §2) return a HARNESS-synthesized terminal-error string there
that survives `.filter(Boolean)` and is banked as a finished finding. This verb
classifies a subagent TRANSCRIPT's terminal assistant record — keying on
`message.model == "<synthetic>"` (the unforgeable harness-authorship marker,
corroborated by top-level `isApiErrorMessage` + `stop_reason == "stop_sequence"`)
— and refuses to believe a harness-authored death. The catch reads a DIFFERENT
byte-author than the judged worker (`<synthetic>` = the CC harness, not the
subagent's model) — grounding, not consistency (the docs/138 invariant).

Reads the transcript from `--transcript PATH`, or — when absent — from a hook
event JSON on STDIN carrying a `transcript_path` (so it composes with a
SubagentStop wiring too). Exit code is the branch signal: 3 = DEAD (route to a
DEAD bucket, count in the denominator, do NOT fold), 0 = HEALTHY (or UNREADABLE,
the fail-safe floor — a read fault never fabricates a death). `--json` emits the
full verdict object (state, dead, class, api_status, refusal envelope).

ADVISORY (docs/197 §6.5): it REPORTS — it never re-runs a worker, never re-prompts
a synthesizer (the −9 pp DEFER-shaped derail). The safe action a workflow takes on
exit 3 is to route the dead child's OWN unit for re-dispatch.

### § `cmd_coverage`

The cheap, NON-GIT fan-out coverage fold: how many of N workers REALLY returned? (docs/197 §7(1)).

The coverage-classify pairing — the follow-up to `dos verify-result` (the §7(1)
keystone). An ultracode Workflow logs() coverage and throws it away, so the
synthesizer sees only the survivor list and a 4-of-7 fan-out is laundered as 7/7
(`failed = N − survivors.length` / `results.filter(Boolean)` cannot tell a
harness-synthesized death from a real negative). This verb folds the per-worker
DEAD/HEALTHY verdicts against the workflow-DECLARED count N into a coverage verdict
{FULL, UNDERFILLED, STARVED, OVERFILLED, EMPTY} whose `prompt_line` is the legible
sentence a workflow feeds INTO the synthesis prompt — so a sub-quorum fan-out can
no longer be laundered as full.

An HONEST AGGREGATOR (not a data-multiplier): it folds N already-adjudicated
`result_state` verdicts; it mints 0 new labels (the `fleet_roll` posture, docs/179).
The win is real but narrow: `declared` is independent of the survivor list (so the
laundering is structurally impossible) and it surfaces the discarded `unaccounted`
count.

Two input modes, mirroring verify-result's explicit-vs-stdin pattern:

  * HARNESS-GROUNDED — `--transcript PATH …` / `--transcripts-glob GLOB`: coverage
    itself runs `result_state.verify_transcript` per path, so the healthy/dead
    counts cannot be forged by the caller. `--json` stamps `grounded: true`.
  * CALLER-ASSERTED — `--states HEALTHY,SYNTHETIC,…`: the caller (a workflow that
    already ran `dos verify-result` per child) asserts the per-slot states. This
    is PROVENANCE-DEGRADED — the state tokens have no `<synthetic>` provenance;
    coverage cannot re-ground them. `--json` stamps `grounded: false` so a consumer
    knows the denominator was workflow-asserted, not harness-grounded.

`--declared N` is REQUIRED and is never inferred from the input length (that
independence IS the laundering fix). The EXIT CODE is the branch signal: 0 = FULL/
EMPTY (fold), 3 = degraded (inject the caveat, count the gap), 2 = contract error.
ADVISORY: it reports; it never re-runs a worker or judges a healthy return's
correctness (that is `effect_witness`, docs/197 §7(2)).

### § `_scope_gate_proposed_files`

Gather the PROPOSED (pre-effect) write-set the gate decides on.

The pre-effect analogue of `verdict_cli._git_diff_names` (which reads a
*committed* range for the post-hoc `classify`). Three sources, in precedence:

  --file F (repeatable) → the explicit list a caller already computed (the
      commit broker passes its `declared_paths(diff)` here — no git needed).
  --staged              → `git diff --cached --name-only`: the footprint of
      what is ABOUT to be committed (the natural edit-time gather).
  default (--base..--head) → `git diff --name-only <base>..<head>`, so the
      verb still works against a range for a dry-run / a CI check.

All git reads degrade to an empty set on any failure (the `git_delta`
fail-safe) — a gate over an empty footprint is the benign ALLOW (a write of
nothing escapes nothing), never a crash.

### § `cmd_liveness`

Classify whether a run is ADVANCING / SPINNING / STALLED (docs/82, LVN).

The temporal sibling of `dos verify`: `verify` distrusts a finished claim
("I shipped P"); this distrusts an in-flight one ("I'm making progress"). It
gathers ground-truth evidence at THIS boundary — decodes the run-start ms from
the run-id (`run_id.ts_ms_of`, the clock is free in the token), counts commits
since `--start-sha` (`git_delta.commits_since`), folds the lane journal for
this run's lease (`journal_delta.fold_since`), and reads the wall clock — then
hands it all to the PURE `liveness.classify`.

The journal rungs (LVN Phase 2) are scoped to THIS run's lease and require
identity: pass BOTH `--lane` and `--loop-ts` to count this lease's
state-mutating journal events toward ADVANCING and to derive its heartbeat
age. Without identity the journal cannot be attributed to this run, so those
rungs stay silent (events 0, no journal heartbeat) and the commit rung — plus
any `--last-heartbeat-age-ms` override — decides. `--last-heartbeat-age-ms` is
an OVERRIDE: when given it wins over the journal-derived age (a test/caller
that knows the heartbeat from a non-journal source, e.g. the live registry).

No-plan rail (the `dos verify` discipline): this runs against a plain git repo
with a run-id and a start SHA and nothing else — commits-since-start alone is a
sufficient ADVANCING/STALLED signal. Read-only: the journal read never creates
the journal (or its parent dir), and it never creates `.dos/`.

The verdict IS the exit code so a shell/loop can branch without re-parsing:
ADVANCING=0, SPINNING=3, STALLED=4 (distinct, and disjoint from argparse's
usage code 2 which a bad `--run-id` reserves).

### § `cmd_productivity`

Classify whether a run is PRODUCTIVE / DIMINISHING / STALLED (docs/218, PRD).

The loop-economics sibling of `dos liveness`: `liveness` asks whether ground-
truth state moved *at all* since the run started (a binary, lifetime question);
this asks whether the amount of work landed *per step* is collapsing (a
continuous question over a trend). A run can be ADVANCING (it committed) yet
DIMINISHING (each step does less and less) — the gap with no home in `liveness`.

Lifted from Claude Code's own loop (`tokenBudget.ts` `checkTokenBudget`): a run
is DIMINISHING when it has taken `--min-steps` steps AND its last two per-step
work deltas are BOTH under `--floor`. The multi-signal AND is what keeps one
quiet step from false-tripping the verdict.

The caller gathers the per-step work deltas at THIS boundary — tokens spent each
step, commits each step, bytes diffed — whatever *work unit* the host measures
(the kernel is unit-agnostic: it compares magnitudes, the host names the unit in
`dos.toml [productivity]`). They are passed OLDEST-first via `--deltas` (a comma
list) and frozen into the pure `productivity.classify`.

No-plan / no-telemetry rail (the strongest of any verdict): this needs NOTHING
but the deltas — no git, no journal, no clock, no plan. `classify` makes no I/O
at all (productivity is timeless — it reads a sequence, not ages). Read-only: it
creates no `.dos/` and touches no state.

The verdict IS the exit code so a babysitter loop can branch without re-parsing:
PRODUCTIVE=0, DIMINISHING=3, STALLED=4 (disjoint from argparse's usage code 2,
which a bad --deltas reserves).

### § `cmd_efficiency`

Classify whether a run's tokens were spent EFFICIENT / COSTLY / WASTEFUL (docs/263, EFF).

The token-economics sibling of `dos productivity`: `productivity` asks whether
the amount of work landed *per step* is collapsing (a trend over time); this
asks whether the work was worth its *price* (a ratio: work per token). A run
can be PRODUCTIVE (each step lands work) yet spend ten times the tokens that
work was worth — the gap with no home in `productivity`.

Reads two env-authored counts the caller already has — the work the environment
witnessed (commits / changed bytes / passed tests, the host's unit) and the
tokens the provider billed — and compares the ratio to a `--floor`. Both counts
are bytes the agent did not author (the docs/138 invariant), so a run cannot
narrate its way toward EFFICIENT.

The always-free verdict is WASTEFUL (meaningful spend bought 0 work —
unit-independent, fires with no floor). COSTLY is opt-in: it fires only when the
host arms a `--floor` that means something for its work unit (the default floor
is 0.0 = disabled, so a unit mismatch never manufactures a false COSTLY). Below
`--min-tokens` spend the run has barely started and the accusation is withheld
(EFFICIENT-benign — the `productivity.min_steps` guard, in spend).

No-plan / no-telemetry rail (the strongest of the verdict family, alongside
`productivity`): this needs NOTHING but the two counts — no git, no journal, no
clock, no plan. `classify` makes no I/O at all (efficiency is timeless — it
reads two numbers, not ages). Read-only: it creates no `.dos/` and touches no
state.

The verdict IS the exit code so a babysitter loop can branch without re-parsing:
EFFICIENT=0, COSTLY=3, WASTEFUL=4 (disjoint from argparse's usage code 2, which
a bad --work/--tokens reserves).

### § `cmd_improve`

Decide whether a self-improving loop may KEEP one candidate (docs/280, IMP).

The keep-gate of the first self-improving work loop for DOS. A self-improvement
loop is propose → verify → measure → keep-or-revert; its one fatal failure mode
is the loop grading its own homework (the agent that WROTE the change decides it
is better, so it learns to narrate improvement instead of making it). This verb
is the witness-gated keep-bit that closes that hole — `reward.admit` re-aimed at
"may this loop keep this commit?".

Reads four ENV-AUTHORED facts the driver gathered at its boundary, none authored
by the loop (the docs/138 invariant that makes the keep-bit non-forgeable):
`--suite-passed` (the test runner's exit on the candidate-only tree),
`--truth-clean` (`dos verify` / `dos commit-audit` over git ancestry), `--work`
(the env-measured improvement metric AFTER) and `--baseline-work` (the same
metric BEFORE). KEEP requires suite green AND truth clean AND a STRICT measured
gain (`work > baseline-work`) AND a non-WASTEFUL spend. Any other outcome is a
REVERT (regressed if the suite/truth floor failed; no-improvement if the metric
did not move). After `--max-reverts` non-keeps in a row the breaker OPENs and the
verdict is ESCALATE — surface to a human (the RSI bottleneck: hand "what matters
next" back rather than propose another candidate nothing accepts).

NON-FORGEABILITY (docs/234 at loop scale): `--narrated` text is carried for the
operator surface and parsed for NOTHING — it cannot move REVERT → KEEP. The only
path to KEEP is an actual env-measured gain; a loop cannot write its way into
the kept set.

No-plan / no-telemetry rail: needs only the gathered facts — no git, no journal,
no clock inside the verdict (it is pure; the driver does the I/O). The verdict IS
the exit code so a loop can branch without re-parsing: KEEP=0, REVERT=3,
ESCALATE=4 (disjoint from argparse's usage code 2).

### § `cmd_breaker`

Classify a circuit breaker's state: CLOSED / OPEN (docs/223, BRK).

The generic circuit-breaker primitive extracted from loop_decide's six
hand-coded breakers (UNCLEAR / OVERLOADED / DIRTY-ZERO / STALE-STAMP …). A
breaker counts failures of ONE class and OPENS when too many pile up — so the
caller stops hammering a broken path and escalates instead. The kernel knows
nothing about WHAT failed (it is handed counts, not an UNCLEAR token); the host
names the class, thresholds, and escalation in `dos.toml [breaker]`.

Two counters, lifted from Claude Code's `denialTracking.ts` (the same
consecutive-vs-total split): `--consecutive` (failures in a row — a SUSTAINED
outage, reset by a success) and `--total` (cumulative — a FLAPPING failure a
consecutive count misses, never reset). It OPENs on EITHER reaching its max.

The DOS addition (idea H3): an OPEN breaker names where to escalate — the trust
ladder ORACLE→JUDGE→HUMAN (`--on-trip none|judge|human`). "Don't keep refusing
identically — escalate the rung." Advisory: the verdict reports OPEN, it never
kills a process; the host decides what an escalation means.

This is the READ-ONLY peek (`classify`): given counts, is the circuit open? The
write path (`record_failure`/`record_success`, which return the next counts) is
the library API a loop threads through its own state — the CLI is the inspector.

No-plan rail: needs nothing but the counts — no git, no journal, no clock. The
verdict IS the exit code: CLOSED=0, OPEN=3, contract error 2 (a can-never-trip
policy, or a bad count).

### § `cmd_exec_capability`

Classify whether a command grants arbitrary code execution (docs/224, XCAP).

Idea B1 from the Claude Code audit: CC's dangerousPatterns.ts identifies which
commands hand the model ARBITRARY code execution — `python -c …`, `bash -c …`,
`npx …`, `ssh host …`, `sudo …` — because each escapes a narrower per-command
gate. This applies the docs/158 law "a capability is a SHAPE, not a word": it
matches the INVOKED PROGRAM (the first token, after stripping an `env`/`sudo`
wrapper and `VAR=value` prefixes; basename, lower-cased) against a closed
capability set — NEVER a substring of the command, so `cat python.txt` is
BOUNDED (it invokes `cat`), not a false python hit.

A pure classifier, NOT an admission gate. The verdict is ADVISORY — it reports
the capability; a consumer (`dos hook pretool`, the PRE PEP) decides what to do
(a WARN on the intervention ladder; a host driver may escalate). It never denies
on its own — the docs/143 −9 pp lesson (spurious disruption is the expensive
mistake). BOUNDED is "not in the declared exec set," NOT a safety guarantee.

The capability set defaults to CC's CROSS_PLATFORM_CODE_EXEC; `--extra prog,…`
(or `dos.toml [exec_capability]`) adds a host's own interpreters. No-plan rail:
needs only the command — no git, no journal, no clock. The verdict IS the exit
code: BOUNDED/EMPTY=0, GRANTS_ARBITRARY_EXEC=3, contract error 2.

### § `cmd_hook_exit`

Map a plain shell hook's exit code → an intervention verb (docs/226, HEX).

The cheapest integration surface (idea C3): a host has a shell script (a linter,
a policy probe, a smoke test) that signals its result the only way a plain
process can — an exit code. CC gives it meaning (`hooks.ts`): exit 0 = proceed,
exit 2 = blocking error, any other non-zero = a non-blocking warning. This maps
that terse signal into the DOS intervention vocabulary (OBSERVE/WARN/BLOCK/DEFER)
so a script too simple to emit JSON still rides the intervention ladder.

The default map is CC's convention; `--map CODE=VERB,…` (or `dos.toml
[hook_exit]`) declares a host's own (e.g. `3=DEFER`). An unanticipated non-zero
code falls to WARN — the fail-safe direction (inform, never silently pass, never
spuriously block; the docs/143 −9 pp posture).

The script's exit code is authored by the SCRIPT, not the judged agent (the
actor-witness split, docs/117) — so this routes a deterministic JUDGE's verdict
into the kernel's vocabulary. ADVISORY: it RECOMMENDS an intervention; the host
(or `enforce.run_handler`, which consumes an `Intervention`) decides.

No-plan rail: needs only the code — no git, no journal, no clock. The verb's OWN
exit code reflects the rung so a shell wrapper can branch without parsing: PASS=0,
BLOCK=3, WARN=4, DEFER=5, OBSERVE=6, contract error 2.

### § `cmd_answer_shape`

Classify an output's SHAPE: ANSWER_SHAPED / NON_ANSWER / INDETERMINATE (docs/156 §4, ASH).

The picker/grounding-boundary closure of the grounded-but-not-an-answer gap.
A numeric grounding gate can pass every fact in an output and still let through
a structurally-disqualified "answer" — an empty stub, a leaked chain-of-thought
log, a bare refusal pasted as content. This is the missing pre-ship floor:

    ship  ⟺  grounded  AND  answer_shape ≠ NON_ANSWER

THE HONESTY BOUNDARY: this judges SHAPE, never CORRECTNESS or RELEVANCE. It
answers the mechanically-checkable "is this the KIND of thing that could be an
answer?", NOT the semantic "is this a GOOD answer?" — that is the Tier-3 gestalt
the kernel deliberately ABSTAINS on (a JUDGE/HUMAN's call). So ANSWER_SHAPED
means "shaped like an answer," explicitly NOT "correct"; on anything shape can't
decide, the verdict is INDETERMINATE (the abstain floor), never a false
ANSWER_SHAPED.

The candidate output is gathered at THIS boundary — `--text` (or `--text -` /
`--file -` to read stdin, the natural way to pipe a large drafted answer in).
The markers are POLICY, not hardcode: the kernel ships a generic cross-domain
default (a leaked reasoning block, "let me think", a tool-call dump, a bare "I
cannot"); `--non-answer RE,…` adds a host's own disqualifiers ON TOP (never
replacing the default), and `--min-chars N` sets the viability floor. `--markers
RE,…` opts a strict host into positive-evidence-required (a non-trivial text
matching none of them is INDETERMINATE, not ANSWER_SHAPED).

No-plan rail: needs only the text + policy — no git, no journal, no clock, no
model. `classify` is pure and NEVER raises (a bad host regex degrades to "not
matched", the fail-safe UNDER-disqualify direction). ADVISORY: it reports a
shape; a consumer (an assembly policy) decides whether to withhold. Read-only.

The verdict IS the exit code so an assembly gate can branch without re-parsing:
ANSWER_SHAPED=0 (shippable on shape grounds), NON_ANSWER=3, INDETERMINATE=4
(disjoint from argparse's usage code 2, which a malformed flag reserves).

### § `cmd_reward`

May a fine-tune TRAIN on this trajectory? The reward-set admission verdict (docs/230/234).

The on-ramp that puts DOS *inside a training loop*. A self-judged RLVR/RFT sampler
banks every "resolved/done" trajectory as a positive reward label — which trains
the policy to over-claim more (it is rewarded for confidently narrating a success
it did not achieve). This is the witness-gated filter: a claim enters the positive
set ONLY if a NON-FORGEABLE witness confirms it (ACCEPT), a refuted claim is the
poison a naive sampler banks (REJECT_POISON — purged, and dispreferred in a DPO
pair), and a claim no accountable witness reached ABSTAINS (never minted positive).

THE NON-DISTILLABLE LABEL (docs/234): the accept bit is a pure function of the
witness the agent authors zero bytes of. The CLI surfaces the two independently-
authored facts the verdict joins:

  * `--claim` / `--no-claim` — the host extractor's bit: did this trajectory make a
    checkable claim? (Your extractor — tau2's confident-write detector, a CI job's
    "the PR says FIXED", a tool-log's "a mutating call was issued" — decides this at
    the boundary; the kernel never parses domain text.)
  * `--witness {confirm,refute,none}` — what a NON-FORGEABLE read-back (`OS_RECORDED`
    / `THIRD_PARTY`: the env DB-hash, an OS exit code, a provider ledger) saw about
    the claimed effect. `none` = no accountable witness reached it.

The floor made visible: `--forgeable` puts the witness on the `AGENT_AUTHORED` rung
(the agent re-read its OWN surface) — it is recorded but STRUCTURALLY ignored, so
even a `--witness confirm --forgeable` cannot ACCEPT. That is the proof that the
policy cannot write its way into the positive set, runnable at $0.

PURE, no-plan: needs only the two flags — no git, no journal, no clock, no model.
The verdict's OWN exit code reflects the rung so a dataset-build loop can branch:
ACCEPT=0, REJECT_POISON=3, ABSTAIN=4, NO_CLAIM=5, contract error 2.

### § `cmd_test_witness`

Does this NEW test actually witness this change? The test-witness verdict (docs/288).

Reverse-classical testing as a kernel rung. "I added a test for this" is one of
the highest-frequency work claims an agent makes, and a test that passes on BOTH
trees keeps the suite green while witnessing nothing — `improve()`'s suite-green
floor cannot tell a witnessing test from a decorative one. The deterministic
kill: run the new test against the tree WITHOUT the change; it must fail. This
verb joins the two runner outcomes the caller gathered at the boundary:

  * `--baseline {pass,fail,error,not-run}` — the runner's outcome for THE TEST
    on the tree WITHOUT the change (a worktree at the merge-base / HEAD-before).
  * `--candidate {pass,fail,error,not-run}` — the outcome on the tree WITH it.

`fail` is assert-level red (the test ran and rejected the old behavior — the
strong witness); `error` is structural red (it could not even load on the
baseline — still discriminating, flagged `assert_level: false`).

The floor made visible: `--forgeable` puts the outcomes on the `AGENT_AUTHORED`
rung (the agent NARRATED "it failed before and passes now") — the verdict
ABSTAINS, so a narration can never mint a witness. The `reward --forgeable`
proof, restated for the test-claim seam, runnable at $0.

PURE, no-plan: the kernel adjudicates outcomes; it does not run pytest (the
two-tree gather is a CI step's / host driver's, docs/288 §6). ADVISORY —
DISCRIMINATES proves the test discriminates the TREES, never that it asserts
the intended BEHAVIOR (that residue is a JUDGE/HUMAN's). The verdict's OWN
exit code: DISCRIMINATES=0, VACUOUS=3, UNSATISFIED=4, REGRESSIVE=5,
ABSTAIN=6, contract error 2.

### § `cmd_resume`

Replay a run's intent ledger, re-verify progress against ancestry, PROPOSE the continuation (docs/107).

The third ARIES phase (analysis → redo → CONTINUE), the forward dual of docs/94's
walk-back. It is ADVISORY by construction (the §8 non-goal / docs/99 floor):
there is no `dos resume` that EXECUTES — this `--plan` prints the residual + the
non-forgeable re-entry SHA + the proposed re-dispatch command, and exits. A
driver or the operator enacts it; the kernel mints the resume point and proposes,
it never re-spawns and never re-runs the work.

The boundary evidence-gather (the `dos liveness` shape): read the run's
`intent.jsonl` (`intent_ledger.read_all` → `replay`), ask git which claimed SHAs
are in ancestry (`resume_evidence.gather_ancestry`), then hand both to the PURE
`resume.resume_plan`. On RESUMABLE it idempotently records a `RESUME_PROPOSED`
on the run's ledger (the §5-req-4 idempotence: two supervisors racing to resume
one dead run converge on a single proposal) — UNLESS `--no-record` (the
inspect-only path) or the run already has a proposal.

Never trusts the dead run about its own progress: every `STEP_CLAIMED` is
re-verified against the fossil at read time (`103`'s recalled-memory posture),
"done" is re-derived from ancestry, and a forgeable verify (subject-grep) is not
a safe anchor (§5).

### § `cmd_rewind`

`dos rewind` — the conversation-rewind verdict (docs/164 F1.5). Read-only/advisory.

The boundary evidence-gather (the `cmd_resume` shape): replay the run's intent
ledger for the minted `SuspendCheckpoint`, read its transcript turns off disk
(hashing each — the kernel authors the anchor's identity), build the `FireVerdict`
from the ground-truth stop the operator asserts (`--fire`), then hand all three to
the PURE `rewind.rewind_plan`. PRINTS the plan (which turn to truncate to, which
dead-end turns to drop, the byte-clean no-good note) — and proposes the truncation
as DATA; the kernel never mutates the transcript (the §8 non-goal / docs/99 floor).

### § `cmd_complete`

Is the WHOLE declared job verifiably done? — the live completion verdict (docs/117).

The read-only "is this run actually done?" probe, run by a human or a supervisor
*outside* the loop (the `dos plan` / `dos resume` check-from-outside discipline,
applied to completion). It answers the forward question the running loop never
asks: not "did this pass run?" but "is the residual empty *now*?".

Same boundary evidence-gather as `dos resume` (they share the residual logic): read
the run's `intent.jsonl` (`intent_ledger.read_all` → `replay`), ask git which
claimed SHAs are in ancestry + re-adjudicate the verified steps
(`resume_evidence.gather_ancestry`), then hand both to the PURE
`completion.classify`. NEVER trusts the run about its own progress — a `STEP_CLAIMED`
git cannot confirm stays in the residual (inherited from `resume`, docs/107 §5).

READ-ONLY: unlike `dos resume` it records nothing (there is no proposal to mint —
completion is a question, not a continuation). Advisory: it mints the belief "done /
not done / can't tell"; stopping the loop is the loop's act (docs/99 / docs/117 §8).

### § `_run_region`

The held-lease region (path globs) for `run_id`, or () if it holds no lease.

Boundary I/O + pure fold, the `_journal_delta` shape: read the lane journal
at THIS boundary (`cfg.paths.lane_journal`, passed EXPLICITLY — never the
process-global path), fold it to the live-lease set (`lane_journal.replay`),
then pick the lease this run holds. The match key is the **spine join**: an
ACQUIRE stamps `run_id` on the nested lease (docs/118 S / docs/137), so a lease
is attributable to the run that took it. We read `tree` (the arbiter-granted
globs) off the matching lease.

Fail-closed (docs/120 §3): a missing/unreadable journal → (), a run holding no
lease → (), and an OLD ACQUIRE that never stamped `run_id` simply won't match
(so region=() — a valid "not attributable" fact, never a raise, never a guess).
A run holds at most one lease per (loop_ts, lane), so the first match suffices.

### § `cmd_arg_provenance`

The argument-provenance verdict: did the model MINT an id/FK arg, or RESOLVE it?
(docs/143 R1 — the EnterpriseOps-Gym survivor binding.)

A pure fold over a mutating tool call's args + the env-authored bytes the agent already
saw (prior tool RESULTS + the task text), answering the one byte-AUTHOR question DOS's
floor honestly underwrites: did each id-shaped argument value appear in env-authored
output, or was it invented? It NEVER asks whether the args are *correct* (the docs/143
§5a mirror-verifier trap). All inputs are gathered HERE at the boundary; `classify_call`
is pure.

Inputs (the env corpus is built only of env-authored bytes — there is no way to feed a
model turn in):
  --tool          the tool name (its write-verb stem decides mutating vs read).
  --args          the call's arguments as a JSON object.
  --prior         a prior tool RESULT, as a JSON/text blob. Repeatable; each is one
                  TOOL_RESULT env blob. (Or --prior-file to read newline-delimited
                  blobs from a file.)
  --task-text     the task prompt bytes (one TASK_TEXT env blob).
  --read          mark the call non-mutating (a read is never gated; it SOURCES
                  provenance). Overrides the write-verb heuristic.

Exit code IS the verdict so a wrapper can branch: believe (no minted arg) = 0,
UNSUPPORTED (≥1 minted id) = 3.

### § `cmd_status`

Fold a run's four shipped verdicts into one fail-closed digest (docs/120 Phase 2).

The boundary-gather for `dos.status.status_digest` (the pure fold, Phase 1). It
GATHERS — it computes no new verdict:

  1. liveness  — the `cmd_liveness` git+journal evidence-gather → `liveness.classify`.
  2. progress  — `intent_ledger.replay(read_all(run_id))` → `.verified` (the minted
                 rung; `claimed` is read by NOTHING here — the §3 invariant).
  3. region    — the run's held-lease globs via `_run_region` (the spine join), or ().
  4. resume    — CONDITIONAL: only once the run is *stopped*. The stopped predicate
                 is automatic — `LedgerState.suspended` (it parked) OR the liveness
                 verdict is STALLED (dead/hung) — overridable with `--stopped` /
                 `--live`. While live, resume stays None (and the expensive
                 `gather_ancestry` re-adjudication is skipped — docs/142 §3.4).

The `--start-sha` source (docs/142 §3.3): the explicit flag wins; else the run's
declared `start_sha` off the intent ledger (so the operator needn't supply it twice);
else "" (a conservative 0-commit liveness floor — a valid, if cautious, fact).

Fail-closed everywhere (docs/120 §3): a missing ledger yields a zero `ProgressView`
(declared 0 / verified 0), never a raise; no lease → region=(); a run that declared
no adjudicable intent is still a valid fact. The `--json` shape carries NO `claimed`
key — the load-bearing property of the surface. The verdict IS the exit code (the
liveness scheme), so a loop can branch on a run's headline state without re-parsing.

### § `_journal_delta`

Fold the lane journal for this run's lease (the LVN Phase-2 journal rung).

Boundary I/O + pure fold, the `_git_delta_count` shape: the file read
(`lane_journal.read_all`) happens HERE at the boundary, against the served
workspace's journal path passed EXPLICITLY (`cfg.paths.lane_journal`, never
the process-global `_journal_path()` — the MCP/explicit-config discipline);
the already-materialized entry list is handed to the pure
`journal_delta.fold_since`. `read_all` returns `[]` for a missing journal
WITHOUT creating it (the read-only / no-plan rail), and any surprise OSError
degrades to an empty delta — `dos liveness` never crashes on a bad journal,
the same every-failure→empty stance `git_delta` takes.

### § `_epoch_ms_iso`

Absolute epoch-ms of an ISO timestamp; None on any unparseable/missing input.

The lane journal stamps `acquired_at`/`heartbeat_at` at second-resolution UTC
(`%Y-%m-%dT%H:%M:%SZ`); `decisions`/`picker_oracle` parse the same shape with
`datetime.fromisoformat` after normalising a trailing `Z` to `+00:00`. We
mirror that kernel idiom here at the boundary (the verdict never parses a
clock). Robust by contract: ANY parse failure degrades to None and never
raises — a bad lease stamp must not crash the supervisor's evidence-gather.
A naive (tz-less) stamp is pinned to UTC so `timestamp()` does not shift by
the host's local offset.

### § `_supervise_evidence`

Gather the per-tick `supervise.SuperviseEvidence` for a workspace's roster.

The SUP boundary (docs/99): build the ordered lane roster, replay the lane
journal into the live-lease set, run ONE `liveness.classify` per held lease
(evidence gathered HERE, never inside the verdict — the `arbitrate` rule),
and freeze a `LaneLiveness` per declared lane. Re-used by both `dos loop` and
the long-lived supervisor driver. Every read is wrapped so a missing/corrupt
journal degrades to an empty live-set rather than crashing — the `dos liveness`
/ `_journal_delta` every-failure→empty stance, applied to the whole gather.

  cfg           — the active `SubstrateConfig` (served workspace + lane taxonomy).
  target        — desired live-worker population (echoed onto the evidence).
  now_ms        — wall-clock epoch-ms, read at the caller boundary (injectable).
  pending_lanes — lanes with a spawn in flight whose ACQUIRE has not yet landed
                  (the double-spawn race belt); empty for the stateless CLI,
                  populated by the driver from its launched-set.
  policy        — the active `SupervisePolicy` (optional). When it declares a
                  `max_concurrency` budget (docs/283), this boundary marks the
                  workspace's `autopick` lanes `repeatable` (fungible handles the
                  budget rides) AND — on a DYNAMIC-CLAIM workspace that declares
                  NO concurrent/autopick lanes (the job model: `concurrent=[]`,
                  disjointness enforced per-pick at acquire time) — synthesises a
                  single repeatable bare auto-pick handle so the budget has a lane
                  to ride. Without a `max_concurrency` policy this is a no-op and
                  the roster is byte-for-byte today's.

The heartbeat age that decides a held worker's SPINNING-vs-STALLED comes from
the JOURNAL append-ts (`jd.newest_heartbeat_age_ms`), with the lease's
self-reported `heartbeat_at` only as a last-resort fallback — the LVN Phase-2
integrity property (`journal_delta`): the beat must be the entry's own append
stamp, not the copy-prone self-report `liveness` exists to distrust. This is
the same trusted-journal-primary precedence `cmd_liveness` uses.

### § `cmd_loop`

The SUPERVISOR (docs/99, SUP): keep N dispatch-loops alive across the roster.

DOS's init / PID-1 for a fleet of workers. It counts the held lane leases
against the target population and emits a per-tick PLAN to close the gap:
SPAWN the free lanes, REAP the dead (STALLED) ones, FLAG the spinning ones
(advisory — a spinner is never auto-reaped; acting on a spin is open
research), HOLD the advancing ones. It is `liveness`'s population-level
sibling. The population policy (target + whether a spinner counts as up +
whether to reap the dead) comes from `dos.toml [supervise]`; `--target`
overrides just the target for a one-off run.

EMIT-ONLY at this boundary — exactly like `dos liveness` reports without
killing a process. `cmd_loop` gathers evidence (replay the lane journal,
classify each held lease's liveness, read the clock — all HERE, never inside
the pure `supervise.supervise` verdict), then PRINTS the plan: the worker
launch command lines for each SPAWN (the emit-and-exit discipline). It NEVER
`Popen`s a worker and NEVER writes the journal — that is the supervisor
DRIVER's job (the only layer where subprocess + journal-write + policy live).

The clock is read ONCE at this boundary, injectable via `--now-ms` for
deterministic runs/tests (the `cmd_liveness` idiom). `--watch` re-emits the
plan every `--interval` seconds until Ctrl-C; it is the only place this verb
reads the clock repeatedly, and it STILL only emits — no spawn, no write.

Exit code is 0 always: unlike `dos liveness` (whose verdict IS the exit code),
the supervisor's output is an effect PLAN carried in stdout, not a verdict a
shell branches on. A caller acts on the printed/JSON plan; the process exit
only signals that the tick ran.

### § `_load_watchdog`

Resolve the watchdog DRIVER by name at the call boundary — never a static import.

`dos watch` is the only CLI verb that drives an out-of-kernel actor (the
watchdog), the way `dos judge` drives the LLM judge. The kernel's one-way arrow
forbids a kernel module (cli.py is layer-3) from *importing* a driver — a driver
imports the kernel, never the reverse. So we resolve `dos.drivers.watchdog` by
NAME via `importlib` here (the same mechanism `_resolve_driver_config` uses for
host policy packs, and the `dos.judges` seam uses for adjudicators), rather than
`from dos.drivers import watchdog`. The distinction is real, not a lint-dodge:
a static import makes the driver a compile/package-time dependency of the kernel;
name-resolution at the boundary keeps it a runtime-optional one (the kernel
imports and packages without it; the verb fails gracefully if it is absent).
Pinned by `tests/test_vendor_agnostic_kernel.py::test_no_kernel_module_imports_a_driver`.

### § `_parse_track_spec`

Parse a `--track run_id[:start_sha[:lane[:loop_ts[:handle]]]]` spec.

Colon-separated, positional, trailing fields optional — the run_id is the only
required field. Returns a `watchdog.TrackedRun` (budget/command filled by the
caller from the shared `--budget-ms` / `--command`). A spec with no run_id is a
contract error (the run cannot be timed). The `watchdog` module is passed in
(resolved once by the caller via `_load_watchdog`) so this helper names no driver.

### § `_load_memory_recall`

Resolve the memory-recall DRIVER by name — never a static import.

`dos memory` drives an out-of-kernel actor (the recall driver, docs/103) the
same way `dos watch` drives the watchdog and `dos judge` the LLM judge. cli.py
is layer-3; the one-way arrow forbids it from `from dos.drivers import
memory_recall`. So we resolve `dos.drivers.memory_recall` by NAME via
`importlib` here — the same bulkhead as `_load_watchdog`. Pinned by
`tests/test_vendor_agnostic_kernel.py::test_no_kernel_module_imports_a_driver`.

### § `cmd_watch`

The WATCHDOG (docs/101): poll `liveness` per tracked run + propose halts.

Builds the tracked-run set from `--track` specs (repeatable) and/or `--discover`
(fold the live-lease set), then either does ONE tick (default) or runs the
cadence loop (`--max-ticks` / `--interval`). On a SPINNING / hung-past-budget
run it records an `OP_HALT` and echoes the proposed stop command (via
`lane_lease.halt` — records intent, NEVER signals). The clock is read at this
boundary, injectable via `--now-ms`. Exit 0 (an effect record, not a verdict).

### § `cmd_hook_stop`

A `Stop`/`SubagentStop` hook: refuse to let an agent stop on a false done.

Reads the host's hook event JSON on STDIN ({transcript_path, cwd,
stop_hook_active, …}), extracts the (plan, phase) the agent CLAIMED it shipped
(dos.claim_extract), and verifies each against git. If any CONFIDENT claim is
NOT_SHIPPED, prints the EXACT Claude-Code Stop dialect

    {"decision": "block", "reason": "<the verdict, fed back as the next task>"}

so CC declines to stop and feeds the reason back to the model — the structural
`@verify`. Otherwise prints nothing and exits 0 (let the agent stop).

This is the load-bearing fix vs the historical no-op (docs/165 §2): a Stop hook
that printed `{"ok": false, …}` was SILENTLY IGNORED by real Claude Code — CC
honors only top-level `decision`/`reason` (NOT `hookSpecificOutput`, NOT an `ok`
field), so the agent stopped anyway and the dogfood hook never caught a lie
against a live CC instance. The default stdout is now the dialect CC actually
parses; the rich `{"ok": …, "results": …}` object is preserved ONLY behind
`--json` (a machine-readable surface for tooling/tests/non-CC hosts, never the
bytes CC reads).

Anti-loop guard (the `stop_hook_active` discipline CC documents): when the event
carries `stop_hook_active: true`, CC is ALREADY in a forced-continuation from a
prior Stop block — this hook has had its one push-back. We then let the agent
stop (emit no block) rather than trap it in an infinite no-stop loop. `--force`
overrides the guard for a host that manages the loop itself.

Every failure mode degrades to "let it stop" (exit 0, no block): a missing
transcript, unparseable stdin, an extractor abstention, an already-active stop.
The hook can refuse a *false* done; it never blocks a *true* one or crashes the
host turn — the fail-safe direction. By default it acts only on CONFIDENT claims
(the marker / frontmatter rungs); `--strict` also blocks on a heuristic
NOT_SHIPPED.

### § `_emit_help_digest`

Print a once-per-session "this session DOS caught N things" digest to STDERR.

The session-bookend half of the operator observability (the in-flow nudge's
sibling): when an agent is genuinely allowed to stop, surface a compact rollup of
what DOS caught this session — so the operator gets a closing summary even if they
never saw an in-flow nudge.

STDERR, never stdout: the Stop stdout is the dialect CC parses ({"decision":…} or
nothing) — a digest there would be a malformed Stop output (the load-bearing
no-op-avoidance discipline the other hooks follow). CC surfaces stderr to the
operator without treating it as a decision.

Fires AT MOST ONCE per session, guarded by a `.dos/help-digest/<sid>` stamp so the
several Stop-hook firings of one session don't repeat it. Skips entirely when the
session caught nothing (no point in a "caught 0 things" bookend) and on any fault
(observability is best-effort, never blocks the stop or crashes the host turn).

### § `cmd_hook_marker`

A `Stop` hook: refuse a keep-alive wait-marker once its budget is spent (loop_decide §wait-marker).

The PRE-HOC runtime lever for the wait-marker budget — the live binding of the
pure `loop_decide.wait_marker_budget`, persisted across the many short-lived
hook processes of one session by `dos.marker_sensor`'s `.dos/markers/<sid>.jsonl`
tally (the `posttool_sensor` accumulator idiom). It closes the
[[project-dos-poll-loop-antipattern]] hole: a `/loop`-style dispatch loop that
holds its turn open by emitting `claude -p` keep-alive markers burns a FULL
cache-replay assistant turn per marker for zero forward work (session 4b4ff97c:
252 markers / ~$7.80, 91% of the run's cache_read). The `keepalive_poll`
telemetry flag NAMES that spend post-hoc at ≥5 markers; this is its pre-hoc
decision-surface sibling — it refuses the marker BEFORE it is emitted.

POLARITY — the INVERSE of `cmd_hook_stop`, stated sharply so the two never blur.
A keep-alive marker is the loop CHOOSING NOT TO STOP (blocking its own Stop to
keep waiting). So this hook reads the session's running marker count, asks the
pure budget, and:

  * budget REMAINS  (`wait_marker_budget(...).allow`) → the loop may emit another
    marker → records the marker and prints the CC Stop dialect that HOLDS THE
    TURN OPEN — {"decision": "block", "reason": "<budget N/MAX — turn held open>"}
    — exit 0 (CC's "keep working" signal). The marker is now a durable fact.
  * budget EXHAUSTED (`.allow` False)                  → stop polling → prints
    NOTHING (an empty Stop output is CC's "allow stop") so the loop ends its turn
    and waits on the real Bash `<task-notification>`, which fires on the child's
    true exit regardless. The count is NOT incremented past the cap (a refused
    marker was not emitted).

`cmd_hook_stop` blocks a *false done* (claimed-ship vs git); this blocks a
*premature stop ONLY while the marker budget is unspent*, then gets out of the
way. Two Stop hooks, opposite triggers — do not conflate. (A host can wire BOTH:
they compose — `cmd_hook_stop` first to refuse a false done, then this to bound
the keep-alive polling of a true wait.)

DOS stays a PDP (docs/99): the block is a PROPOSAL the runtime consumes. Every
failure mode degrades to "emit nothing, exit 0" = let the agent stop — a missing
stdin, unparseable JSON, an unusable session_id, an accumulator I/O error. The
hook can refuse to keep a loop polling past its budget; it never traps a loop
open on its own inability to read or write. `--debug` prints diagnostics to
STDERR only — never polluting the stdout contract CC parses (the load-bearing
no-op-avoidance discipline, same as the other hooks).

### § `cmd_hook_posttool`

A `PostToolUse` hook: re-surface a repeated ENV value as non-blocking context (docs/173 §4).

The IN-FLIGHT half of `dos.tool_stream` (whose pure verdict already ships and is
green). Reads the host's PostToolUse event JSON on STDIN ({session_id,
tool_name, tool_input, tool_response/tool_output, cwd, …}), turns it into a
`StreamStep` (the agent-authored args digest + the ENV-authored result digest),
appends it to the session's accumulating stream, replays the whole session, and
classifies the trailing run with `cfg.stream_policy`. On REPEATING/STALLED it
prints the host's WARN dialect — by default the Claude-Code shape

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "additionalContext": "<re-surfaced value>"}}

so CC feeds the unchanged env value back into the next turn (the docs/144
turn-preserving re-surface). `--dialect {gemini,cursor,codex,antigravity}` re-renders the same
verdict into another runtime's envelope (docs/217); the default `claude-code` is
byte-for-byte the shape above. On ADVANCING — or ANY failure mode (no stdin, bad
JSON, no `tool_name`, no `session_id`, an accumulator I/O error) — it prints
NOTHING and exits 0. PostToolUse fires AFTER the tool ran, so it CANNOT block:
this is structurally advisory (docs/99), it can only ADD context, never cut the
turn. The fail-safe direction matches `cmd_hook_stop`: never block a real
workflow on the sensor's own inability to read or write.

The output object is EXCLUSIVELY the CC dialect (or empty). `--debug` prints
diagnostics to STDERR only — it never pollutes the stdout contract real CC parses
(the load-bearing fix vs the `dos hook stop` no-op: emit the dialect CC honors,
and ONLY that).

### § `cmd_hook_pretool`

A `PreToolUse` hook: DENY a structurally-refused tool call BEFORE it runs (docs/191).

The PRE moment — the unique cell where a DOS verdict is both SOUND and backed by real
deny-power (the actuation×evidence crossing, docs/191 §0). Reads the host's PreToolUse
event JSON on STDIN ({session_id, tool_name, tool_input, cwd, …} — and CRUCIALLY no
`tool_response`, the structural PRE marker), runs the two PRE-sound rungs
(`pretool_sensor.decide`), and emits the host's PreToolUse dialect — by default the
Claude-Code shape (`--dialect {gemini,cursor,codex,antigravity}` re-renders the same verdict into
another runtime's envelope, docs/217; the default `claude-code` is byte-for-byte below):

  DENY:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": "…"}}
  WARN:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "additionalContext": "…"}}   (no permissionDecision)

so CC withholds the call on a deny (the docs/126 mediated-write moment) or merely adds
context on a warn. On a passthrough — or ANY failure mode (no stdin, bad JSON, no
`tool_name`, a result-key present = mis-routed PostToolUse event, an I/O error) — it
prints NOTHING and exits 0 (let CC's own permission flow proceed). This is the
fail-to-PASSTHROUGH direction: DOS never denies a real call on the sensor's own inability
to read, matching `cmd_hook_posttool`.

DOS stays a PDP: the deny is an `EffectProposal{dispatch_call=False}` the kernel
COMPUTES; the CC runtime is the PEP that acts. The default Rung-B handler is `observe`
(--handler), so a default install emits ZERO behavioral deny — a deny there requires a
wired ruling handler. Rung A (SELF_MODIFY / lane collision) is the structural gate and
denies on its own typed refusal (operator-visible, `--force`-overridable upstream). On
any non-passthrough outcome an `OP_ENFORCE` lane-journal record is appended (docs/189
§C4) so the deny is a durable, joinable WAL fact, not self-report.

Every DECIDED call — passthrough included — also appends one `hook-observation`
record (docs/297 P3; `dos.hook_observation`, fail-soft, `DOS_HOOK_METRICS=0` opts
out): the Python verbs are the second conforming writer of the kernel-owned
telemetry family the native binary already writes, and a pretool record IS the
denominator `dos helped` folds into its intervention rate. The native-served
path writes nothing here (the binary records its own observation); a call the
binary DELEGATED and Python then decided writes its real verdict — the record
the docs/297 rule pairs with the binary's `delegate` handoff row, so a mixed
log counts each call once. The other hook verbs (`posttool`/`stop`/`marker`)
append the same family for their own decided outcomes; none of them enter the
tool-call denominator.

`--debug` prints diagnostics to STDERR only — never polluting the stdout dialect CC
parses (the load-bearing no-op-avoidance discipline).

### § `_append_additional_context`

Append `text` to a CC hook dialect's `additionalContext` (additive, pure).

Returns a NEW dict so the caller's dialect is never mutated in place. If the
dialect already carries an `additionalContext` (the corrective fact from a WARN /
a denied-with-context BLOCK), the nudge is appended after a blank line so both
survive; otherwise it becomes the field. Touches nothing else — `permissionDecision`
and every other key pass through unchanged.

### § `_operator_help_nudge`

The one-line "DOS has caught N things this session" nudge, or "" (pure-ish).

Folds the lane WAL (the OP_ENFORCE stream just appended to) scoped to THIS
session, and — on the 1st + every 5th behavior-changing help — returns the
operator nudge line; otherwise "". The count is read back from the env-authored
journal (never agent narration), keeping a single source of truth: the nudge can
never report a help the kernel did not bank. A non-help outcome (this very call
was observe-only) returns "" — only BLOCK/WARN/DEFER advance the count.

The one boundary read is the WAL we control; any fault is caught by the caller
(observability is best-effort, never an enforcement input).

### § `cmd_judge`

Adjudicate a no-pick / WEDGE verdict — deterministically.

`dos judge wedge <run_ts>` reuses `picker_oracle._classify_one`: it reads the
run's self-reported verdict envelope and cross-checks the stated `reason_class`
against on-disk state (is the colliding claim really stale? is the soak window
really open? is the backlog really empty?), then emits a PROVABLE verdict —
`oracle_disagrees` (the picker-bug signal) or a believed cause.

This is the kernel's judge, and it is **deterministic by construction** — it
has zero LLM-provider surface (the DSP "Bulkhead" axiom: the verdict spine
never grows a provider branch). An LLM adjudicator for the UNCLASSIFIED rows
the oracle can only abstain on lives OUTSIDE the kernel, in
`dos.drivers.llm_judge`; when the cause is UNCLASSIFIED this command prints an
ESCALATE line pointing there (no flag needed — the escalation is automatic).
The exit code is the verdict: 0 = oracle agrees / abstains (no picker bug),
1 = oracle DISAGREES (a provable picker bug worth routing to /replan).

### § `_load_judge_cases`

Load labelled adjudication cases from a JSONL file → [(Claim, truth)].

One JSON object per line (blank lines / `#`-comments skipped):

    {"claim_text": "...", "stated_reason": "...",
     "evidence": ["git: ...", "..."], "truth": true, "subject": "RID-1"}

`claim_text` and `truth` are required; `stated_reason`/`evidence`/`subject`
default empty. `truth` is the researcher's GROUND TRUTH (the claim really is /
is not believable) — the eval is only as honest as these labels, so they should
be derived from artifacts (a real `git log`), not from any judge. Raises
ValueError with the offending line number on a malformed row (fail-loud, like
`dos gate`'s contract-error path), never a silent skip.

### § `cmd_judge_eval`

Score a JUDGE-rung adjudicator against labelled claims — the research instrument.

`dos judge-eval --judge <name> --cases cases.jsonl` resolves the named judge
(built-in `abstain`, the shipped `llm`, or any `dos.judges` plugin), runs it
over the labelled cases via `dos.judge_eval.score`, and prints the confusion
grid + the rates an oversight researcher cares about — chiefly the
**false-clear rate** (when this judge says "believable," how often is it wrong),
plus decisive-accuracy, abstention rate, and cost/claim.

This is the bring-your-own-adjudicator measurement surface (HACKING Axis 6):
implement `dos.judges.Judge`, register it under `dos.judges`, point this at your
labelled set, and get a number. The exit code is the verdict on the judge: 0 if
it cleared no false claims (false_clear == 0), 1 if it false-cleared at least one
(the dangerous cell is non-empty) — so a CI gate can fail on any leak.

### § `_load_overlap_cases`

Load labelled concurrent-pair outcomes from a JSONL file → [OverlapCase].

One JSON object per line (blank lines / `#`-comments skipped):

    {"tree_a": ["src/api/**"], "tree_b": ["src/api/x.py"],
     "collided": true, "label": "api-vs-api"}

`tree_a`, `tree_b`, and `collided` are required; `label` defaults empty.
`collided` is the researcher's GROUND TRUTH — whether running these two trees
concurrently ACTUALLY corrupted shared state (a merge conflict, a detonation),
derived from artifacts, never from a scorer (the eval is only as honest as
these labels). Raises ValueError with the offending line number on a malformed
row (fail-loud, like `dos judge-eval`), never a silent skip.

### § `cmd_overlap_eval`

Score a disjointness SCORER against labelled concurrent-pair outcomes (Axis 7).

`dos overlap-eval --policy <name> --cases cases.jsonl` resolves the named overlap
policy (built-in `prefix`, or any `dos.overlap_policies` plugin), runs each pair
through it UNDER THE DETERMINISTIC FLOOR (the exact path the arbiter takes), and
prints the 2×2 confusion grid + the rates a concurrency researcher cares about —
chiefly the **false-admit rate** (when this scorer says "safe to run together,"
how often did the pair actually collide), plus safe-concurrency-forgone, the
collision-leak rate (`docs/90 §2`'s detonations-missed), and the raw admit-rate.

This is the bring-your-own-scorer measurement surface (HACKING Axis 7, `docs/113`):
implement `dos.overlap_policy.OverlapPolicy`, register it under
`dos.overlap_policies`, point this at your labelled corpus, and get a number that
lets you BEAT the `1/3` ratio with evidence. The exit code is the verdict on the
scorer: 0 if it admitted no real collision (false_admit == 0), 1 if the dangerous
cell is non-empty — so a CI gate fails on any leak. (Against the prefix floor the
cell is 0 for prefix-colliding pairs by construction; it is informative under a
looser `[overlap] ratio_max` or a stricter floor — see `dos.overlap_eval`.)

### § `_verdict_from_dict`

Rehydrate a `dos.arg_provenance.ProvenanceVerdict` from a JSON dict.

The dict shape is `ProvenanceVerdict.to_dict()` — `{believe, unsupported, args:[...],
reason}`, each arg an `ArgProvenance.to_dict()`. Only the fields the intervention path
reads are required to be faithful (`believe`, `args[].stance`, `args[].components_checked`,
`args[].components_unmatched`); the rest default. Pure rebuild — no detector re-run, so
the case carries the EXACT recorded verdict.

### § `_verdict_from_compact`

Synthesize the MINIMAL `ProvenanceVerdict` whose `assess_confidence` is `confidence`.

The hand-authorable on-ramp for `_load_intervention_cases`: a case file should be writable
by a researcher without hand-rolling a full `ProvenanceVerdict.to_dict()` per row. The
confidence is NOT stored on the case (the eval derives it), so the loader must build a
verdict whose REAL `intervention.assess_confidence` SHAPE yields the stated rung — the
scored action then flows through the exact `choose_intervention` path the consumer's PEP
takes (no hand-labelled confidence to drift). The shapes are taken verbatim from
`assess_confidence`'s contract:

  * "HIGH" — one UNSUPPORTED arg, `components_checked == components_unmatched == (value,)`
             (a single data-bearing component that is itself unmatched = whole-value-absent
             scalar mint).
  * "LOW"  — one UNSUPPORTED arg, `components_checked` has >1 component with exactly one
             unmatched (a composite we cannot prove whole-value absence on).
  * "NONE" — `believe=True`, no args, no unsupported (nothing was minted).

`unsupported` names the minted arg(s) (first is used for the synthetic arg; empty → "arg").
Raises ValueError (carrying `line_ref`) on an unknown confidence token (fail-loud).

### § `_load_intervention_cases`

Load labelled intervention cases from a JSONL file → [InterventionCase].

One JSON object per line (blank lines / `#`-comments skipped). The verdict is supplied
in ONE of two shapes — a compact hand-authorable form (preferred for a seed corpus) or a
full recorded verdict:

  * COMPACT (preferred): a `confidence` token (HIGH/LOW/NONE) + an `unsupported` name
    list; the loader SYNTHESIZES the minimal `ProvenanceVerdict` whose real
    `assess_confidence` yields that rung (via `_verdict_from_compact`), so the scored
    action still flows through the live `choose_intervention` path — the confidence is
    never a stored label that can drift.

        {"confidence": "HIGH", "unsupported": ["group_id"], "believe": false,
         "truly_minted": true, "mattered_to_score": false,
         "recovered_if_blocked": true, "recovered_if_deferred": false, "label": "..."}

  * FULL: a `verdict` object (a `ProvenanceVerdict.to_dict()` dict) carrying the exact
    recorded detector output.

        {"verdict": {"believe": false, "unsupported": ["parent"], "args": [...]},
         "truly_minted": true, "mattered_to_score": true,
         "recovered_if_blocked": true, "recovered_if_deferred": false}

Always-required: `truly_minted`, `mattered_to_score`, `recovered_if_blocked`,
`recovered_if_deferred`; plus EITHER `confidence` OR `verdict`. `label` defaults empty.
The bool labels are the researcher's GROUND TRUTH from EXECUTED replay arms (a turn-
preserving arm and a turn-spending arm), derived from artifacts + the hidden verifier
set — NEVER from the detector (the eval is only as honest as its labels, the
`judge-eval`/`overlap-eval` discipline). Raises ValueError with the offending line number
on a malformed row (fail-loud, never a silent skip).

### § `cmd_intervention_eval`

Score an intervention POLICY by its NET TASK DELTA — not verdict accuracy (docs/143 §13.2).

`dos intervention-eval --cases cases.jsonl [--high BLOCK --low WARN --ceiling BLOCK]`
runs each replayed verdict through `intervention.choose_intervention` under the chosen
policy (the SAME path the consumer's PEP takes), then tabulates the net-task-delta ledger
— the instrument the live −9 pp proved is decisive. The detector's precision/recall is a
DIFFERENT eval (`dos arg-provenance`); this one answers "did ACTING on the verdict help
or hurt the run?"

The headline is `net_task_delta` (comparable to the live −9 pp / +11 pp). The dangerous
cell is `wasted_disruption_rate` (when this policy disrupts, how often is it spent on a
catch that did not matter — the source of the −9 pp). Exit code: **1 iff the policy is a
net regression** (`net_harmful` — net delta < 0), 0 otherwise — the `overlap-eval` CI-gate
analogue, so a disruptive policy fails CI.

### § `_load_stream_cases`

Load labelled stall-reader cases from a JSONL file → [StreamCase] (docs/145 §9).

One JSON object per line (blank lines / `#`-comments skipped). The tool stream is supplied
in ONE of two shapes — a compact `repeat` shorthand (preferred for a seed corpus) or a full
`steps` list:

  * COMPACT (preferred): `repeat` = N identical `(tool, args_digest, result_digest)` steps —
    the canonical stall fixture. Optional `tool`/`args`/`result` set the repeated triple
    (defaults `tool`/`a`/`r`).

        {"repeat": 4, "tool": "get_incident", "actually_stuck": true,
         "legit_polling": false, "recovered_if_fired": true, "label": "..."}

  * FULL: a `steps` list, each `[tool_name, args_digest, result_digest]` (result may be null
    for a no-result step).

        {"steps": [["get_incident","a","r"], ["get_incident","a","r"], ["get_incident","a","r"]],
         "actually_stuck": true, "legit_polling": false, "recovered_if_fired": true}

Always-required: `actually_stuck`, `legit_polling`, `recovered_if_fired` (bools — the
researcher's GROUND TRUTH from replay, NEVER from the reader, the `intervention-eval` /
`overlap-eval` honesty discipline); plus EITHER `repeat` OR `steps`. `label` defaults empty.
Raises ValueError with the offending line number on a malformed row (fail-loud).

### § `cmd_tool_stream_eval`

Score a stall-reader POLICY by its NET RECOVERY — does firing recover more than it wastes?

`dos tool-stream-eval --cases cases.jsonl [--repeat-n 3 --stall-n 5 --ignore-tools poll,wait]`
runs each replayed tool stream through `tool_stream.classify_stream` under the chosen policy
(the SAME path the consumer's reader takes), then tabulates the recovery ledger (docs/145 §9)
— the loop-economics axis's friendliness instrument, the `intervention-eval` / `overlap-eval`
sibling.

The headline is `recovered_rate` (of actually-stuck streams, the fraction fired-on AND
recovered). The dangerous cell is `false_resurface_rate` (of legitimately-polling streams,
the fraction also fired on — the §3 honest hole made measurable). Exit code: **0 iff the
policy is net-positive** (recovers more stuck streams than it false-fires on pollers), 1
otherwise — the `intervention-eval` CI-gate analogue, inverted to the friendly direction.

The policy windows default to the active `dos.toml [tool_stream]` (or the generic 3/5); the
CLI flags OVERRIDE them so a researcher can sweep `repeat_n`/`stall_n`/`ignore_tools` against
a fixed case corpus to calibrate the thresholds from data.

### § `_load_precursor_cases`

Load labelled `PrecursorCase`s from a JSONL file (one JSON object per line).

Each row is a replayed mutating call + its prior stream + the researcher's ground truth:

    {"tool": "create_change", "prior": ["get_change", "list_users"],
     "precursor_required": true, "precursor_actually_fired": false,
     "mattered_to_score": true, "label": "..."}

Always-required: `tool` (the mutating call), `prior` (a list of prior tool names — the
env-authored call stream), `precursor_required` + `precursor_actually_fired` (bools — the
researcher's GROUND TRUTH from the policy PROSE + replay, NEVER from the grammar under test,
the `tool-stream-eval` / `intervention-eval` honesty discipline). Optional: `is_mutating`
(default true), `mattered_to_score` (default false), `label`. Raises ValueError with the
offending line number on a malformed row (fail-loud).

### § `cmd_precursor_gate_eval`

Score a precursor GRAMMAR by its RECALL vs WASTE — does it catch real skips cleanly?

`dos precursor-gate-eval --cases cases.jsonl` runs each replayed (mutating call, stream)
through `precursor_gate.classify_call` under the active `[precursor]` grammar (the SAME path
the consumer's gate takes), then tabulates the recall ledger (docs/147 §9.2) — the
precursor-presence axis's friendliness instrument, the `tool-stream-eval` / `intervention-eval`
sibling.

The headline is `missed_precursor_recall` (of real prerequisite-skips, the fraction the gate
fired REFUTED on — the grammar-coverage instrument). The dangerous cell is `false_refute_rate`
(of correctly-sequenced calls whose precursor fired under an unlisted alias, the fraction
wrongly REFUTED — grow `[precursor.aliases]` to shrink it). Exit code: **0 iff the grammar is
net-positive** (catches more real skips than it false-REFUTES), 1 otherwise — the
`tool-stream-eval` CI-gate analogue.

The grammar is the active `dos.toml [precursor]` (or the empty grammar, which fires on
nothing — so run this in a workspace whose `dos.toml` declares the grammar under test).

### § `_ensure_initialized`

Best-effort: scaffold a `dos.toml` if this workspace has none. Non-fatal.

`dos top` is meant to work in a **brand-new repo** the operator just `cd`'d
into. If no `dos.toml` exists we write the same scaffold `dos init` writes
(`_render_init_config`, the per-repo `[lanes]` table auto-derived from the
top-level layout) so the first run leaves a real config behind — the "rerun
init if it has not been run before" behavior. It is BEST-EFFORT: a read-only
or unwritable workspace just renders against the in-memory generic default
(the screen still works), so a failure here never blocks the view.
Returns True if a config exists/was written, False if the repo stays bare.

### § `cmd_dispatch_top`

`dos top` — the live fleet watchdog: lanes, leases, recent verdicts, commits.

A read-only `top(1)` for a DOS fleet (the live-ops sibling of `dos decisions`).
Default: the auto-refreshing `rich` screen where stdout is a tty and the `[tui]`
extra is installed; otherwise one plain-text frame (the always-available floor).
`--once` forces a single frame (CI / pipe), `--json` emits the machine-readable
snapshot, `--interval N` sets the live refresh cadence.

Works in a brand-new repo: if no `dos.toml` exists it best-effort scaffolds one
(like `dos init`) before rendering, then reads the kernel's own lease world
(`lane_journal`) — never a host's `fanout_state`/`execution-state.yaml`. With
zero leases the screen shows the lane roster (all FREE) plus the git-activity
strip, so it is useful from the first commit.

### § `cmd_plan`

`dos plan` — the work-terrain board: every phase, the plan's claim vs the oracle.

The third read-only projection (the work-terrain sibling of `dos top` /
`dos decisions`). A plan view is a **verify()-fan-out, not a plan reader**: it asks a
declared `plan_source` for candidate `(plan, phase)` rows, then rules on each with
`oracle.is_shipped` — the truth syscall, never the plan's stamp. The headline cell is
the divergence flag, where the plan CLAIMS shipped but the oracle says not.

Default: the auto-refreshing `rich` screen where stdout is a tty and the `[tui]` extra
is installed; otherwise one plain-text frame (the always-available floor). `--once`
forces a single frame (CI / pipe), `--json` emits the machine-readable snapshot,
`--interval N` sets the live cadence, `--source NAME` selects a `dos.plan_sources`
plugin, and positional phases (`dos plan IF IF4.1 AUTH P2 …` — plan/phase pairs) fan
the oracle over an explicit list with no plan doc at all (the purest, no-schema mode).

Works in a brand-new repo: best-effort scaffolds a `dos.toml` (like `dos init`), then
reads the declared plan glob. With no plans the screen shows "(no plans)" plus the
git-activity strip — useful from the first commit, exactly like `dos top`.

### § `cmd_decisions`

List the pending operator decisions, or drill into one.

Default (no `show`, a tty, curses available): the interactive TUI. The TUI
degrades to the plain list when curses is unavailable or stdout is not a tty,
so `--no-tui` is rarely needed — it just forces the plain path explicitly.

`--all` surfaces ORACLE/JUDGE-resolvable rows too; the default shows only the
HUMAN-resolvable queue ("what needs me"). `show <#>` prints one decision's
full detail (the same projection the TUI detail pane renders).

### § `cmd_notify`

`dos notify {decisions,top}` — push a read-only projection to a transport.

The delivery side of the two projections `dos decisions` / `dos top` render to
the local screen. It calls the SAME readers, pipes the result through a pure
adapter into one transport-agnostic `Notification`, resolves a notifier by name
(`--notifier`, default `null` — a safe no-op that just prints the payload), and
sends it. A real transport (e.g. `slack`) lives in a driver and is discovered by
name; the kernel imports none.

Safe by default: pushing to an external service is outward-facing, so with no
real `--notifier` (or under `--dry-run`) NOTHING leaves the machine — the verb
renders the `Notification` and reports what it WOULD send. `--json` emits the
`Notification` + the `NotifyResult`. Read-only on DOS state (the
`decisions`/`top` posture): it takes no lease, stops no run; a LIVENESS-halt
field carries the paste-to-stop command but never enacts it.

### § `cmd_trace`

Walk one run across every DOS surface, joined by its run_id (docs/137).

A read-only projection (like `dos decisions` / `dos top` / `dos plan`): it
joins the spine (`run.json` + lineage), the intent ledger (claimed-vs-verified
steps + the residual), the WAL (lanes this run held / was refused — now
joinable since the ACQUIRE carries `run_id`, docs/118 Size S), and git (commits
since the run's start SHA). Stores nothing, takes no lease, adjudicates nothing
new — the believed (claimed) column shown beside the adjudicated (verified) one,
a lease with no run_id surfaced as `(unattributed)`, never guessed by time.

Exit code: 0 if any surface was found for the run_id, 1 if none.

### § `cmd_observe`

Project the verdict journal: the kernel's own adjudication stream (docs/262).

A read-only projection (the `decisions`/`trace`/`top` posture): it reads the
verdict WAL only — every verdict the kernel recorded (`verify`/`liveness`/
`efficiency`/…), correlated by `run_id` — folds it into per-dimension counts,
and renders. Takes no lease, mints no belief, adjudicates nothing new.

Default: the fleet-wide rollup (counts per syscall × verdict). `--run RID` shows
one run's verdict history (the `trace` join). `--syscall NAME` filters to one
dimension; `--by DIM` folds on verdict/run_id/lane/source instead of syscall.
`--tail N` shows the last N raw events. `--json` is machine-readable (the
trajectory-audit's kernel-native data source).

Exit code: 0 always when the journal is readable (an empty journal is a valid,
honest "nothing recorded yet", not an error); 1 only if a non-trailing corrupt
line was found (an integrity breach worth a non-zero so a cron can alert).

### § `cmd_helped`

`dos helped` — surface how many things DOS productively caught for the operator.

The operator-facing projection over the enforcement stream the lane WAL already
carries (every OP_ENFORCE record, docs/189 §C4): it folds the BLOCK/WARN/DEFER
interventions into a "DOS has caught N things" rollup — by typed reason class, by
tool — so the person running the fleet can SEE the substrate is working, not just
trust that it is. The read-only `observe`/`decisions`/`trace` posture: it reads the
journal only, takes no lease, mints no belief, adjudicates nothing new.

Default: the whole-workspace rollup, with each reason class glossed in plain
English inline. `--explain` drills down — per reason class, its meaning plus a
few concrete examples (WHICH file was blocked, by which tool, and the kernel's
own one-line reason), so "blocked 610" becomes "blocked 610 edits to the
kernel's own running code, e.g. src/dos/arbiter.py". `--session SID` scopes to
one session's helps (the OP_ENFORCE `holder` is the session id); `--since TS`
keeps only records at/after an ISO timestamp. `--json` is machine-readable.

The rate (docs/297, issue #24): when the workspace has a hook observation log
(`.dos/metrics/observations.jsonl`, the kernel-owned `hook-observation` family
— the native binary and the Python hook verbs are both conforming writers), the
rollup adds the denominator the absolute counts lack: "of N tool calls
adjudicated by the hooks, X passed untouched and Y were intervened on". Both
sides of that ratio come from the ONE observation log, never the lane journal —
the two records have different windows and scopes, so the rate line names its
source and shares no number with the catch counts above it (like-for-like is
structural: `hook_observation.intervention_rate` admits observation records
only, and a `delegate` handoff leaves the denominator — its real verdict is the
deciding runtime's own record). A workspace with no log renders the rate-less
output byte-identically; `--session` suppresses the rate (observation records
carry no session key); `--json` gains an additive `tool_calls` object only when
the log exists.

Byte-clean (docs/138): every field counted — the rung, the reason class, the
tool, the withheld bit — is env-authored (the kernel wrote the record downstream
of an already-decided verdict). No agent narration enters the count, so a run
cannot self-report its way to a bigger "helped" number.

Exit code 0 always when the journal is readable (an empty journal is an honest
"DOS has only been observing", not an error).

### § `cmd_export`

`dos export --to NAME` — drain the verdict journal outward to a backend.

The delivery side of `dos observe`. Where `observe` FOLDS the verdict stream for a
human to read, `export` SHIPS it — every `VerdictEvent`, structure intact — to where
an operator's dashboards/alerts live (a JSONL file a log shipper tails, a StatsD
endpoint, an OTLP collector). It reads the verdict journal via the existing
`verdict_journal.read_all`/`tail`, slices to events AFTER `--since <seq>`, resolves a
transport by name (`--to`, default `null` — a safe no-op that reports what WOULD
ship), and drains it through `export_safely`.

Safe by default + outward-facing-aware: with no real `--to` (or under `--dry-run`)
NOTHING leaves the machine. Read-only on DOS state (the `observe`/`decisions`/`trace`
posture): it takes no lease, mints no belief, adjudicates nothing new. The drain
cursor IS the journal's own monotonic `seq` — so `--since <cursor>` is a resumable
forward offset a `/loop`/cron carries forward; no daemon, no parallel state.

`--tail N` caps to the last N journal events before the `--since` slice (cheap when
only the recent tail matters). `--json` emits `{result, exporter, shipped, since}`
for piping (a `/loop` reads `result.cursor` to advance `--since` next tick).

Cursor persistence (docs/266 Phase 4): `--since auto` reads the persisted offset from
`.dos/export-cursor.<transport>`, drains the new tail, and writes the new high-water
mark back — so a repeated `dos export --since auto` on a `/loop`/cron tick threads
itself, no manual `--since` number. `--follow` is a BOUNDED foreground convenience
(poll → drain → persist → sleep, terminating on `--follow-max` iterations); the
kernel ships no daemon — the host owns the cadence (the `dos notify`/`dos top`
posture).

Exit code: 0 on a clean drain (including the null/dry-run no-op and an empty
journal); 1 only when a REAL transport was asked to ship and did not (`exported==0`
with events pending), so a cron can alert on a broken collector.

### § `cmd_memory`

Re-verify an agent-memory store at recall time (docs/103).

`dos memory recall <name>` re-checks ONE memory's claims against ground truth
(git + the working tree) and prints its closed verdict (RECALL_FRESH / STALE /
UNVERIFIABLE) — the antidote to a frozen self-report injected as fact. `dos
memory verify` sweeps the whole store, STALE first. Both are READ-ONLY by
default; `--route` (sweep only) cross-posts non-FRESH verdicts to `dos
decisions` via an OP_REFUSE (needs the host to have declared RECALL_* in
dos.toml [reasons]). The driver is resolved BY NAME (the bulkhead); the store
is the host's memory dir (`--store`, or the documented Claude Code layout).

### § `_runtime_hook_status`

For each known runtime, which DOS hook events are wired in `root`. READ-ONLY.

The boundary reader behind the `dos doctor` "runtime hooks" line (docs/221):
reads each host's config file under the workspace and asks the pure
`hook_install.wired_events_*` which DOS events are present. Returns
`[(host, [events…]), …]` for every known host (empty list = configured-but-not-
wired or no file). Never writes — a doctor probe creates no config. Defensive: a
host whose config is malformed/unreadable contributes an empty list, not a crash.

### § `cmd_lint`

`dos lint` — the config-integrity rail as a focused verb (docs/227, G1).

The lane+reason half of `dos doctor --check`, surfaced on its own so CI can
gate a `dos.toml` change on it without the rest of the doctor report. Pure
delegation: gather `cfg.lanes` + `cfg.reasons` at this boundary, decide in the
`config_lint` leaf, render the typed findings. The verdict IS the exit code (the
syscall-CLI contract): 0 clean, 1 on any error/warn finding (`--strict` narrows
the gate to error-only; `info` never gates either way — docs/227 §4).

### § `_exit_code_contract`

The documented verdict-IS-the-exit-code table, keyed by verb (item 1).

Surfaced in `dos doctor --json` so an agent DISCOVERS the exit semantics of
every verdict-bearing verb instead of reverse-engineering them by running the
binary and watching `$?`. Each row is DERIVED from the verb's own `ExitMap`
(`_VERIFY_EXITS`, `_LIVENESS_EXITS`, …) — the SAME object the handler returns
from — so the published contract and the running behaviour are one source and
can never drift.

Each verb maps a verdict token → its exit code. `contract_error: 2` is the
shared usage/contract code (a bad `--run-id`, malformed `--leases`/`--picks`,
an unknown `--output`); it never collides with a real verdict because the
multi-valued verbs start their non-success codes at 3. A token the running
kernel emits but the map omits exits on the verb's `unknown` floor (non-zero,
never read as success) — published here too for any verb that has one.

### § `cmd_exit_codes`

Print the verdict-IS-the-exit-code contract, all verbs or one (the on-ramp item).

The contract already lives in `dos doctor --json` under `exit_codes`, but a CI
author wiring `if dos verify …; then` should not have to run `doctor`, pipe it
through `jq`, and learn the JSON shape just to look up "what does exit 3 mean
for `liveness`?". This is the `dos man` move for the exit table: a discoverable
CLI surface over the SAME `_exit_code_contract()` the handlers are derived from,
so the printed table and the running behaviour can never drift.

`dos exit-codes` lists every verdict-bearing verb; `dos exit-codes VERB` filters
to one. `--json` emits the machine form (the same object `doctor` carries). An
unknown VERB is a usage error (exit 2); a clean listing is 0.

### § `cmd_quickstart`

Run the DOS money-moment end to end in a throwaway repo (the 60-second on-ramp).

The README's "Try it in 5 minutes" is a 12-line copy-paste; this is the one
command that *runs* it — and it runs it as a STORY a newcomer who has never
heard of a "phase" can follow: an agent claims it shipped two named things
("the login endpoint (AUTH1) and the password reset (AUTH2)"), the demo
scaffolds a fresh workspace and makes the one real commit that stamps `AUTH1`,
then asks the truth syscall about both claims — the backed one → SHIPPED, the
one nothing landed for → NOT_SHIPPED. The contrast IS DOS: the verdict comes
from git ancestry, never the agent's story. The exit-code line ("0/1 IS the
verdict") lands at the same beat, so the CI relevance is visible at the money
moment, not buried in reference docs.

Part two (default mode) is the multi-writer act — the admission half a
single-agent demo can't show, framed for the widest audience that actually
hits it (a 20-agent fleet OR just two coding-agent tabs open on one repo):
three `dos lease-lane acquire` calls journaled against the demo repo's lease
WAL admit agent A onto `src`, redirect agent B off the busy region onto the
disjoint `docs` (the collision that never reached the files), and refuse agent
C when every lane is held — then a `lease-lane live` beat shows the journal
that made those verdicts differ, and the coda states the ask/hold split
(`arbitrate` decides; `lease-lane acquire` holds). See § `_quickstart_fleet_act`
for why the act journals for real. Lie-catching plus collision-refereeing is
the whole pitch in one command.

The transcript is paced as a two-part story (`--- Part 1 — catch the false
"done" ---` / `--- Part 2 — two agents, one repo ---`) so a first read has a
spine to follow, and the default-mode epilogue leads with the hands-on replay
(`dos quickstart --keep dos-demo` keeps the repo + its lease journal, so every
narrated command re-runs against it with `--workspace dos-demo`).

The closing is an adoption ROUTER, not a fleet-only on-ramp: one
line each for the ways people actually run agents — a hook-capable runtime
(`dos init --hooks <runtime>`), an MCP host (`dos-mcp` + `dos_verify`), a CI
step (branch on the exit code), and a fleet on one repo (`dos init` +
`lease-lane acquire`) — so a newcomer who is not a fleet operator still leaves
with the one move that applies to them. Pinned by
`test_quickstart_default_routes_the_wider_audience`.

Every verdict line is produced by the SAME `oracle.is_shipped` + renderer the
real `dos verify` uses (not a canned string), so what the operator sees here is
exactly what they'll see against their own repo. By default it works in a temp
dir and cleans up; `--keep DIR` scaffolds in DIR and leaves it for the user to
poke at (including the two still-held demo leases the epilogue points at).
Read-only intent on the user's machine beyond the throwaway repo.

The story itself — the AUTH plan/phase tokens, the two named features, the one
real commit subject — is the CANONICAL EXAMPLE, declared once in
`dos._demo_story` and interpolated here rather than re-spelled. Every other
surface that tells it (the README parts, `docs/QUICKSTART.md`, the
`examples/demo/` scripts and figures, `examples/plans/example-plan.md`, the
fleet-framework fixture, the CI smoke step) carries a genre-local copy, and
`tests/test_canonical_example_lockstep.py` scans the tracked tree to pin every
copy to the same subject and feature↔phase bindings. To CHANGE the story, edit
`dos._demo_story`, update the lockstep test's twin literals, and let its scan
list every copy left to fix.

Exit 0 when the demo ran and the two verdicts came out as expected (SHIPPED /
NOT_SHIPPED); 2 if git is unavailable or a step failed (a contract/environment
error, never a silent half-run).

### § `_admission_predicate_names`

The names of the active admission predicates, in conjunction order (ADM 3c).

Built-ins first (`disjointness`, `self-modify`), then any discovered
`dos.predicates` plugin (sorted by entry-point name) — exactly the order the
arbiter runs them. Best-effort: a discovery fault degrades to the built-in
names rather than crashing `dos doctor` (the report must never block on a
broken third-party plugin). Discovery's own stderr notes are suppressed here
(doctor is a report, not the arbitration path) by handing it a null sink.

### § `_judge_names`

The names of the resolvable judges (Axis 6), built-in then discovered.

Built-in `abstain` first, then any discovered `dos.judges` plugin (sorted by
entry-point name) — the order `active_judges` composes. Best-effort: a
discovery fault degrades to just the built-in `abstain` rather than crashing
`dos doctor`; discovery's stderr notes are suppressed (doctor is a report, not
an adjudication path) by handing it a null sink.

### § `_evidence_source_names`

The names of the resolvable evidence sources (Axis 8, docs/121/265), built-in
then discovered.

Built-in `null` first, then any `dos.evidence_sources` plugin (sorted by
entry-point name) — the order `active_evidence_sources` composes. These are the
non-git witnesses a host can wire `verify` to consult (`[verify] non_git_oracle`).
Best-effort: a discovery fault degrades to just the built-in `null` rather than
crashing `dos doctor`; discovery's stderr notes are suppressed (doctor is a
report, not an adjudication path) by handing it a null sink.

### § `_enforce_handler_names`

The names of the resolvable enforcement handlers (docs/189 §A1), built-in then
discovered.

Built-in `observe` first, then any discovered `dos.enforce_handlers` plugin
(sorted by entry-point name) — the order `active_handlers` composes. Best-effort:
a discovery fault degrades to just the built-in `observe` rather than crashing
`dos doctor`; discovery's stderr notes are suppressed (doctor is a report, not an
actuation path) by handing it a null sink.

### § `_overlap_policy_names`

The names of the resolvable overlap policies (Axis 7), built-in then discovered.

Built-in `prefix` first, then any discovered `dos.overlap_policies` plugin
(sorted by entry-point name) — the order `active_overlap_policy_names` composes.
Best-effort: a discovery fault degrades to just the built-in `prefix` rather
than crashing `dos doctor`; discovery's stderr notes are suppressed (doctor is a
report, not an arbitration path) by handing it a null sink.

### § `_state_health_findings`

The state-file health rail: is the workspace's execution-state file bloated?

Closes the gap that `dos doctor` reported the execution-state *path* but never
its *health*. Gathers the file's on-disk size + a best-effort top-level
section row-count at the I/O boundary, then delegates the judgement to the pure
`state_health.classify_state_file` (the `_stamp_coverage_finding` /
`config_lint.lint` shape — gather here, decide in the leaf).

Generic by construction: it knows nothing host-specific. It flags the two debts
any large append-mostly state file accrues — total-byte bloat and an oversized
cold section — using the package default policy (`GENERIC_STATE_FILE_POLICY`).
The *deferred-obligation* rung (docs/133) needs declared obligations a host
supplies as data and is intentionally NOT wired here yet; this rail is the size
half, which needs no host declaration. A missing/unreadable file ⇒ no finding
(the file simply doesn't exist on a fresh workspace — not a health problem).

### § `_verifiability_headline`

The always-honest cold-open: in one line, can DOS check this repo's claims?

The one sentence that is CORRECT on every repo — the reference kernel, a
foreign Conventional-Commits repo, an empty repo. It answers the only question
a stranger has in the first five seconds ("does my repo even speak the language
a referee needs?") from REAL evidence — the repo's own recent commit subjects —
not a pitch:

  * "verifiable now: N of your last M commits name a unit of work `dos verify`
    can check (via the <grammar> grammar)."   — the grep rung will land.
  * "no verifiable ships: none of your last M commits name a unit of work — no
    referee can check what your agents claim until they do (see HACKING.md)."
    — the honest cold-open on a repo that stamps nothing, the Conventional-
    Commits majority. This is the `dos doctor`-as-on-ramp that never cries
    wolf: it tells the truth about coverage instead of `verify`-ing a phase and
    false-accusing on `via none`.

Pure-ish: the only I/O is reading recent subjects (best-effort; a non-git or
empty repo yields none → the "no commits to read" form). The JUDGEMENT is the
two pure stamp predicates (`ship_shaped_under_generic` / the active
convention's `recognizes_direct_ship`), so the count is exactly what `verify`'s
grep rung would recognise — no second, drifting heuristic.

### § `_describe_stamp`

One-line description of a `StampConvention` for `dos doctor` (SCV 3b).

Names the grammar `verify`'s grep rung will recognise against this repo:
  * ``job (docs|go|…)``     — the reference job convention (its exact dir set),
  * ``generic (any/no dir)``— no `subject_dirs`: a bare `<SERIES>: <PHASE>` /
                              `<SERIES><PHASE>:` ships (the foreign-repo shape),
  * ``<a|b|c>``             — a workspace's own declared `subject_dirs`.
Falls back gracefully if handed something without `subject_dirs`.

### § `cmd_reap`

Reap per-project `.dos/` scratch to the workspace's `[retention]` caps.

DRY-RUN by default (reports what WOULD be deleted, deletes nothing); `--apply`
performs the deletions. `--journal` additionally compacts the WAL when it
exceeds the size/age threshold (`retention.should_compact`). Every dropped
identifier is printed — the docs/106 §3.4 no-silent-caps rule. Only `--apply`
writes, so the dry-run path takes no lease and ensures no home.

### § `cmd_gate`

Classify a /next-up packet into one typed gate verdict (the empty-packet gate).

Two input modes, exactly one required:

  * a positional PACKET path — the `.dispositions-<tag>.json` sidecar the
    renderer emits next to a packet (schema ``oc3-dispositions-v1``). File
    mode also honours a sibling `.race-<tag>.json` envelope: if this render
    lost a candidates-cache lock race, the verdict is RACE regardless of the
    on-disk dispositions (the wrong-scope packet must not be read as a real
    DRAIN/BLOCKED). This is the surface `dos-dispatch` / `dos-dispatch-loop`
    gate their empty case through.
  * ``--picks-json`` — an inline JSON list of per-pick disposition dicts (the
    same shape the sidecar's ``dispositions`` array carries). Lets a skill
    gate a packet it just rendered in-memory without writing a file. No RACE
    precedence on this path (there is no sidecar to race against).

The verdict IS the exit code (see ``_GATE_EXIT_CODES``): LIVE=0, DRAIN=3,
STALE-STAMP=4, BLOCKED=5, RACE=6 — distinct so a shell can branch, and all
disjoint from the contract-error code (2). A missing/malformed sidecar or a
bad picks list is a CONTRACT error (the producer is out of contract with this
gate), surfaced on stderr with exit 2 — never a silent fall-through to DRAIN.

### § `cmd_pickable`

Decide whether a declared unit is offerable to a worker right now (docs/168 §2).

Reads the unit's declared STATE at this boundary (the host pre-gathers it as a
JSON object — `--state '{"plan_class":"DRAFT", …}'`), runs the pure
`pickable.classify`, and emits the typed `Pickability` verdict. The recognised
state keys (each defaulting to falsy when absent — degrade-never-crash) are:
``shipped``, ``in_flight``, ``soft_claimed_elsewhere``, ``stale_claim``,
``plan_class`` (``"DRAFT"`` → DRAFT_CLASS), ``operator_gated``, ``soak_open``,
``dependency_unmet``, ``cooldown_until_ms``, ``unparseable``.

The verdict IS the exit code (see ``_PICKABLE_EXIT_CODES``): OFFERABLE=0, and a
HELD verdict exits with a PER-HoldReason code so a skill branches on which hold
— invariant holds 10..13, curable holds 20..25, all disjoint from contract-
error (2). ``--json`` carries the full typed object.

### § `cmd_enumerate`

Enumerate the unit ids a plan-doc declares, in document order (docs/168 §1).

Reads the plan-doc at PLAN_DOC, layers the per-plan ``--series`` (plan-meta
``id``/``phase_prefix``) onto the workspace ``[enumerate]`` grammar, and runs
the pure `enumerate_units`. Emits the `Enumeration`: the unit universe, the
shipped/remaining partition, and any typed `DriftNote`s (a list↔table mismatch
or an unparseable region) — never a raise, never a silently-empty universe.

Exit STATUS: clean enumeration = 0; a DriftNote present = 3 (so a skill
notices the doc disagrees with itself); an empty universe = 4. A missing/
unreadable file is a contract error (2).

### § `cmd_cooldown`

Decide whether a unit is in a per-pick cooldown window now (docs/207 §3).

Reads the unit's `OP_ATTEMPT` history from the lane journal at this boundary
(each attempt's ms-epoch stamp is derived from the journal `ts`), runs the pure
`cooldown.cooldown_verdict` under the workspace `[cooldown]` policy, and emits
the typed `Cooldown` verdict. Outcome-aware: a SHIPPED most-recent attempt is
moot (CLEAR); a DRAINED/BLOCKED attempt inside the window is RECENTLY_ATTEMPTED.

The verdict IS the exit code: CLEAR=0, RECENTLY_ATTEMPTED=3 — so a loop's
pick-selection skips a cooled unit (and `dos cooldown UNIT && pick UNIT` reads
naturally). ``--attempts`` accepts an inline JSON list of attempt records for
testing/replay (bypassing the journal read).

### § `cmd_pick_priority`

Decide a unit's pick freshness and emit its sort_key (docs/254).

Reads the unit's `OP_ATTEMPT` history from the lane journal at this boundary
(the SAME source `dos cooldown` reads — each attempt's ms-epoch stamp derived
from the journal `ts`), reduces it to an `AttemptSummary` (attempted iff any row
is this unit's; last_attempt_ms = the newest such row), runs the pure
`pick_priority.classify`, and emits the typed verdict.

The verdict IS the exit code: NEVER_ATTEMPTED=0 (fresh — pick before any
already-tried unit), ATTEMPTED=3. The `sort_key` (in `--json`) is what a picker
appends to its own (priority, status, …) key so freshness breaks ties WITHIN a
tier and nowhere else. ``--attempts`` accepts an inline JSON list of attempt
records for testing/replay (bypassing the journal read).

### § `cmd_reconcile`

Reconcile a unit's CLAIM against the ORACLE's ground-truth verdict (docs/168 §3).

Fail-closed on the claim: the agent's word never removes work; only ground
truth does. The oracle verdict is gathered at this boundary — either computed
from git ancestry via PLAN + PHASE (`oracle.is_shipped`, the real `verify`
rung), or supplied directly with ``--oracle-shipped`` for replay/test. The
claim is ``--claimed-done`` (the agent's self-report).

The verdict IS the exit code: VERIFIED=0 (oracle confirms shipped — leaves the
residual), QUIET_INCOMPLETE=3 (claimed done BUT oracle says not — KEPT + flagged,
the dangerous case), HONEST_OPEN=4 (not claimed + not shipped — honest open
work). DETECT-and-KEEP, never a mutation.

### § `_add_workspace_flags`

Attach the workspace-selection flags (`--workspace`/`--driver`/`--job`).

These now live in TWO places: on the top-level `dos` parser (the GLOBAL
placement, `subcommand=False`) AND on each subparser (`subcommand=True`, the
default). That is what makes BOTH spellings work — `dos --workspace . verify`
and `dos verify --workspace .` — which fixes the agent-hostile wart where only
the trailing-flag spelling resolved. The mechanism is the `default`:

  * GLOBAL placement → a real default (`None` / `False`), so the attribute
    always exists on `args` even when neither placement sets it.
  * SUBCOMMAND placement → `argparse.SUPPRESS`, so the child flag sets the
    attribute ONLY when the user actually passes it. Without this, the
    child's plain default would CLOBBER a value the global placement parsed
    first (argparse applies a subparser's defaults unconditionally, AFTER the
    parent has parsed), silently dropping `dos --workspace . verify` back to
    cwd. With SUPPRESS the global value survives when the child is absent, and
    the child wins when both are given (last-parsed wins) — the precedence the
    operator chose.

Every handler reads these via `getattr(args, "...", <fallback>)`, so a
SUPPRESS'd-and-unset attribute degrades to the same fallback it always had —
no handler change is needed.

### § `_add_explain_flag`

The `--explain` agent affordance (off by default).

Appends a one-line next-action interpretation of the verdict — the SAME hint
the MCP tools return, from the shared `dos.interpret`. Off by default so the
byte-faithful `text`/`json` output is unchanged for every existing consumer;
an agent that wants the gloss ("treat as NOT done; don't trust the claim")
opts in. With `--json`, the hint is added as an `interpretation` field inside
the object (matching the MCP tool shape); with text, it prints on a trailing
line.

### § `_observe_enabled`

Is verdict-journal recording armed for this invocation? (docs/262 Phase 2)

OPT-IN, two ways: a `--observe` flag on the verb, or `DISPATCH_OBSERVE=1` in the
environment (so a fleet's `/loop` arms it ONCE and every verdict verb lands a
record without per-call plumbing). Default OFF — a bare `dos verify` must stay
side-effect-free (a truth syscall does not silently start writing a log). The
env value is truthy on `1`/`true`/`yes`/`on` (case-insensitive).

### § `_detect_source_dirs`

The repo's top-level source directories, sorted, noise-filtered, capped.

A "source dir" is any immediate subdirectory that is not a dotdir and not in
`_INIT_NOISE_DIRS`. Returns at most `_INIT_LANE_MAX` names (sorted, so the
selection is deterministic) — these become the auto-derived concurrent lanes.

### § `_render_driver_config`

Serialize a driver's `LaneTaxonomy` into a `dos.toml` the workspace reads back.

So the throwaway repo's ON-DISK config (what `--keep` leaves behind, and what
precedence reads) and the driver's IN-PROCESS taxonomy agree — the whole point
of the reachability fix: a stranger gets a dos.toml that actually USES the
reference driver, not a generic auto-derived one that would shadow it.

### § `_install_skills`

Copy the named generic skills into `dest_root/.claude/skills/<name>/SKILL.md`.

Idempotent: an existing local copy is NOT clobbered without `force` (a host may
have diverged it — the seed-not-binding rule). Returns (written, skipped,
unknown). Pure-ish: only the file copy touches disk; the selection is data.

### § `contract`

The published `dos doctor --json` row: the verb's verdicts, plus
`contract_error` and the `unknown` floor where each is a distinct code.

A two-valued verb already lists `contract_error` and has no separate
`unknown` floor (`unknown == contract_error`), so its row is exactly
`{verdicts, contract_error}` — matching the historical table byte-for-byte.

### § `_load_attest_key`

Read the HMAC signing key at the boundary: --key-file › $DOS_ATTEST_KEY.

The key is signing material, so it is read HERE (boundary I/O), never inside the
pure `dos.attest` module. A `--key-file` is read as raw bytes (a binary key is
fine); the env var is taken as its UTF-8 bytes. Returns None when neither is set —
the caller turns that into the contract-error exit (a signed receipt needs a key;
we never silently emit an unsigned one when the user asked to sign).

### § `_git_delta_count`

Commits since `start_sha` on the served workspace (the LVN git rung).

Thin boundary wrapper over the shared `git_delta` reader so `dos liveness`
and `dos.timeline` read the commit-delta through the SAME code (LVN-1b: LVN
must not re-implement the timeline's git rung). Passes the served root
EXPLICITLY (never process-global), the MCP-server discipline.

### § `_heartbeat_age_ms`

`now_ms − epoch_ms(ts)`, clamped at 0; None when `ts` is unparseable.

The LAST-RESORT fallback for the newest-beat age `liveness.classify` reads
(`last_heartbeat_age_ms`), used only when the journal fold yields no credible
beat (see `_supervise_evidence`). None means "no credible beat" — the lease
carried no parseable heartbeat/acquire stamp.

### § `cmd_guard`

Frame a headless agent launch with the DOS wiring, then exec the host.

`dos guard [opts] -- <host-cmd> …` injects `--mcp-config` (the DOS MCP
server) and optionally a `--settings` Stop hook, then execs the host command.
Pure plan-building lives in `dos.guard`; this verb does the one impure thing
(exec) — or, under `--print-config`, dumps the plan and launches nothing.

### § `_journal_pretool_outcome`

Append an OP_ENFORCE record for a non-passthrough PRE outcome (docs/189 §C4).

Builds the forensic body from the decide() outcome and writes it via the existing
`lane_journal.enforce_entry` builder (OP_ENFORCE is NOT in `_STATE_MUTATING_OPS`, so
`replay` ignores it for lease state — it only adds history). `tool` is the host tool
name, `holder` the session id, so a `trace`/`resume` can answer "which call was denied,
by which rung, and why" from the spine rather than the agent's narration.

### § `_parse_explicit_phases`

Pair a flat ``[plan, phase, plan, phase, …]`` token stream into PlanRows, or None.

Returns ``None`` when no positional phases were given (so `snapshot` falls through to
the declared source — the normal path). An odd trailing token becomes a plan with an
empty phase. The explicit rows carry `claimed_status=""` (UNKNOWN — the operator named
a phase, not a claim), so the board shows the bare oracle verdict for each.

### § `_verifiability_facts`

The machine form of `_verifiability_headline` for `dos doctor --json`.

The same two pure stamp predicates over the last 50 subjects, as counts a
skill/CI can branch on without parsing the prose headline: `commits_read`,
`ship_shaped`, `recognized`, and the active `grammar` label. Best-effort —
a non-git/empty repo reports zeros.

### § `_add_output_flag`

The RND `--output <name>` selector (default `text`).

Resolved through `dos.render.resolve_renderer`: the built-in `text`/`json`
forms plus any renderer a workspace registers under the `dos.renderers`
entry-point group. An unknown name fails loud with the known list. Default
`None` so `_resolve_output_name` can fall back to the legacy `--json` flag
when `--output` is omitted (keeping pre-RND callers byte-identical).

## Inline section notes (by first line)

### § The provenance-rung tell — color at the CLI boundary, NOT in the renderer.

*(in `module level`)*

The provenance-rung tell — color at the CLI boundary, NOT in the renderer.
`verify`'s most-differentiated idea is the evidence-GRADE suffix `(via <rung>)`:
`via grep`/`via registry` is a SHIP backed by a real artefact; `via none` is
"the agent claimed it, git proved nothing." That distinction is the screenshot
— the red/green of this category. But the `text` renderer is byte-faithful by
contract (render.py docstring + tests/test_render.py pin it character-for-
character), so the color CANNOT live in `render_verdict`. It lives HERE, as a
pure presentation wrap applied only when stdout is an interactive TTY and the
operator did not pick an explicit `--output`/`--json` (a pipe/redirect/JSON
consumer gets the exact bytes, unchanged). The literal `(via none)` token
survives inside the colored span, so `dos verify | grep 'via none'` still works.

### § init

*(in `module level`)*

init
The shared header + tail of the scaffolded `dos.toml`. The `[lanes]` table in
between is GENERATED from the repo's top-level layout (see `_render_init_config`)
so the default is actually usable for concurrent work, not a dead whole-repo lane.

### § docs/134 §6 / docs/165 — the runtime-binding on-ramp. `dos init --with-hooks`

*(in `module level`)*

docs/134 §6 / docs/165 — the runtime-binding on-ramp. `dos init --with-hooks`
writes the three DOS hooks into the workspace's `.claude/settings.json` so a
Claude-Code launch BINDS the verdict to the runtime with no hand-editing:
  Stop        → `dos hook stop`     (refuse to stop on an unverified claim)
  PostToolUse → `dos hook posttool` (re-surface a stalled tool stream, advisory)
  PreToolUse  → `dos hook pretool`  (DENY a structurally-refused call before it runs)
All three are the SHIPPED verbs that emit the exact CC dialect (the Stop no-op is
fixed, docs/165 §2). The block is MERGED into any existing settings.json, never
clobbering a user's own hooks — idempotent (re-running adds nothing already there).

### § docs/221 — `dos init --hooks <host>` is the CROSS-VENDOR generalization of

*(in `module level`)*

docs/221 — `dos init --hooks <host>` is the CROSS-VENDOR generalization of
`--with-hooks`: it wires the three shipped DOS hooks into the config file of ANY
of the four supported runtimes (Claude Code / Cursor / Codex / Gemini), each with
its own file path, format (JSON or TOML), and event-name vocabulary. The per-host
facts + the PURE merge live in `dos.hook_install`; this is the I/O BOUNDARY that
reads → merges → writes (the "I/O at the boundary, data to the pure core" rule).
`--with-hooks` is exactly `--hooks claude-code` (kept for backward-compat; its
command stays byte-identical, so the existing test_init_hooks.py suite is green).

### § `init` is workspace-INSENSITIVE for config READBACK (it scaffolds the very

*(in `cmd_init`)*

`init` is workspace-INSENSITIVE for config READBACK (it scaffolds the very
`dos.toml` the rest of the CLI reads, so there is nothing to read yet) — but
the WORKSPACE SELECTOR still names WHERE to scaffold. So when `--workspace W`
is given (globally or trailing), the positional DIR resolves RELATIVE TO W:
`dos --workspace /repo init` → /repo, `dos --workspace /repo init sub` →
/repo/sub. Without it, DIR is cwd-relative as before (backward-compatible).
An ABSOLUTE DIR is honored verbatim (it is unambiguous, so the workspace
base is not joined). This closes the foot-gun where `dos --workspace /x init`
silently scaffolded under cwd while appearing to honor the selector.

### § The verdict-IS-the-exit-code contract — one source per verb.

*(in `module level`)*

The verdict-IS-the-exit-code contract — one source per verb.
Every verdict-bearing verb maps its verdict tokens → exit codes. `ExitMap` is
the SINGLE SOURCE for one verb: the handler returns from it (`.code_for(token)`
/ `["contract_error"]`), and `_exit_code_contract()` DERIVES the published
`dos doctor --json` table from it (`.contract()`) — so the documented agent and
the running binary can never disagree, and the three facts that used to be
three independent literals per verb (`_FOO_EXIT_CODES`, `_FOO_EXIT_UNKNOWN`,
`_FOO_EXIT_CONTRACT_ERROR`) are now one object that cannot drift from itself.

`contract_error` is the shared usage code (a bad `--run-id`, malformed
`--leases`/`--picks`, an unknown `--output`); it is ALWAYS 2 — disjoint from
every real verdict because the multi-valued verbs start their non-success codes
at 3. It is a field on the map (defaulting to 2) so the convention lives in one
place; `contract()` always emits it. `unknown` is the floor for a token the
running kernel emits but the CLI hasn't caught up to: non-zero, distinct from
the known verdicts and from `contract_error`, so it is never read as success.

### § commit-audit  (the author-NEUTRAL claim-vs-diff verdict for ANY git project)

*(in `module level`)*

commit-audit  (the author-NEUTRAL claim-vs-diff verdict for ANY git project)
Exit codes a pre-commit hook / CI gate branches on (the "exit code IS the
verdict" convention the CI cookbook relies on):
  0 = clean — every checkable claim is witnessed by its diff (and abstains are
      fine). Also the code under --warn-only regardless of findings (advisory).
  1 = at least one commit makes a claim its own diff cannot witness
      (CLAIM_UNWITNESSED) — the gate-fail signal.
  2 = contract error (the ref/range could not be read; not a git repo).

### § docs/265 — the non-git evidence rung. When THIS workspace wired a non-git

*(in `cmd_verify`)*

docs/265 — the non-git evidence rung. When THIS workspace wired a non-git
oracle (`[verify] non_git_oracle = "ci_status"`) AND the git rung confirmed a
ship with a resolvable SHA, gather that driver's verdict for the SHA at the
boundary (the `gh api` subprocess lives in the driver, resolved BY NAME — the
one-way-arrow bulkhead, never a static import) and fold it conjunctively:
GREEN upgrades `source` to `ci-green`, RED withholds + flags, NO_SIGNAL/PENDING
pass through. It is applied ONLY to a `shipped=True` verdict, so it can never
manufacture a ship (the §1 safety invariant); an unwired workspace skips this
entirely and `verify` is byte-identical to git-only. A `--no-ci` flag forces it
off for one call (a fast path when the operator doesn't want the network probe).

### § Output goes through the renderer seam (RND). `--output` selects the named

*(in `cmd_verify`)*

Output goes through the renderer seam (RND). `--output` selects the named
renderer; the legacy `--json` flag still maps to the built-in `json`
renderer so existing callers are byte-unchanged. The default `text`
renderer reproduces the old `cmd_verify` line character-for-character.
When a human is at a TTY (no `--output`/`--json`, no NO_COLOR), the rung is
colored AT THE BOUNDARY — green for a real-artefact ship, red for `via none`
— so the evidence grade is the screenshot's tell. The bytes are unchanged.

### § `--explain` is the opt-in agent affordance: append the same one-line

*(in `cmd_verify`)*

`--explain` is the opt-in agent affordance: append the same one-line
next-action interpretation the MCP `dos_verify` tool returns (from the
SHARED `dos.interpret`, so the two surfaces can't drift). It is OFF by
default, so the byte-faithful `text`/`json` renderer output is unchanged
for every existing consumer — only an explicit `--explain` adds the field.
The JSON form carries it INSIDE the object (the renderer is byte-faithful by
contract and must not, so the explain path emits the dict itself); the text
form prints it as a trailing line.
The exit code comes FROM `_VERIFY_EXIT_CODES` (not a bare literal) so the
`dos doctor --json` `exit_codes` table and the running binary read the SAME
map — the single-source anti-drift claim is then structural, not coincidence.

### § attest  (the portable, signed receipt over an effect-witness verdict, docs/246)

*(in `module level`)*

attest  (the portable, signed receipt over an effect-witness verdict, docs/246)
Exit-code map mirrors `dos verify` / the witness drivers (state_diff /
os_acceptance): the verdict the RECEIPT CARRIES is the exit code, so a CI gate /
a counterparty's script branches on the certificate without re-reading its body.
  0 = CONFIRMED (a non-forgeable witness saw the effect present)
  1 = REFUTED   (a non-forgeable witness saw it ABSENT — the load-bearing adverse
                 certificate; exit 1 = "the effect did not happen", same as the drivers)
  3 = UNWITNESSED / NO_CLAIM (could-not-tell, or nothing checkable — a human's call,
      the same code liveness/resume use for a non-pass verdict)
  2 = contract error (no signing key, an unreadable witness surface, bad args).

### § verify-receipt  (the third-party check of a portable receipt, docs/246 Phase 2)

*(in `module level`)*

verify-receipt  (the third-party check of a portable receipt, docs/246 Phase 2)
The exit code is the certificate's standing:
  0 = VALID   (the signature matches the canonical payload — the stamp holds)
  1 = INVALID (a tampered field / wrong key / forged signature — fails LOUD, never a
      silent downgrade to "unsigned but probably fine"; docs/246 §5)
  2 = contract error (no signing key, an unreadable/malformed receipt, bad args).

### § verify-result  (the fold-site result-state witness, docs/197 §7(1))

*(in `module level`)*

verify-result  (the fold-site result-state witness, docs/197 §7(1))
Exit codes a workflow stage branches on at the `.filter(Boolean)` fold:
  0 = HEALTHY (or UNREADABLE — the fail-safe floor: a read fault is NOT a death,
      so it does NOT drop a result; the operator sees it via the verdict/--json).
  3 = DEAD (SYNTHETIC harness-authored terminal, or EMPTY) — route to a DEAD
      bucket, count in the denominator, refuse to fold. (3 = the same "act on this"
      code `dos resume`/`liveness` use for a non-pass verdict.)
  2 = contract error (no transcript path given / unusable args).

### § coverage  (the cheap, non-git fan-out coverage fold, docs/197 §7(1))

*(in `module level`)*

coverage  (the cheap, non-git fan-out coverage fold, docs/197 §7(1))
Exit codes a workflow stage branches on AFTER the parallel()/pipeline() barrier:
  0 = FULL (or EMPTY — nothing fanned out, not an error): fold all, no caveat.
  3 = degraded coverage (UNDERFILLED / STARVED / OVERFILLED): the fold is a
      sub-quorum (or anomalous); the workflow MUST inject the prompt_line caveat
      and count the gap in the denominator. (3 = the same "the result you'd fold is
      degraded" signal as verify-result's DEAD, so a stage can branch identically.)
  2 = contract error (no --declared, an un-coercible state token, or nothing to fold).

### § `--leases` is a JSON array of live-lease dicts. A malformed value is operator

*(in `cmd_arbitrate`)*

`--leases` is a JSON array of live-lease dicts. A malformed value is operator
error, not a kernel fault — report it cleanly and exit on the contract-error
code (2, the same code a bad `--run-id` / unknown renderer uses) instead of
dumping a JSONDecodeError traceback.

DEFAULT = the live WAL set, not []. The pure `arbitrate` is state-in/decision-out
and never reads the disk itself; the live leases must be gathered at THIS
boundary (the `git_delta`/`journal_delta` → verdict rule) and passed in. Folding
the lane-journal WAL — the exact set `dos lease-lane live` reconstructs and
`dos lease-lane acquire` writes to — means `dos arbitrate --lane L` SEES a lease a
sibling already holds and refuses/redirects, instead of arbitrating against an
empty world and silently double-booking. Reading the live set is still pure
(no lock, torn-tail-tolerant, []-on-missing per `lane_lease.live_leases`) and
`arbitrate` still PERSISTS nothing — the purity boundary is unmoved.
  - flag ABSENT  → load the live WAL (the real, collision-aware default).
  - --leases '[]' (or any explicit JSON) → an OVERRIDE: arbitrate against the
    set the caller names, the pure/testing path that asserts the world's state.

### § ADM Phase 3 — resolve the FULL admission conjunction at the CALL BOUNDARY

*(in `cmd_arbitrate`)*

ADM Phase 3 — resolve the FULL admission conjunction at the CALL BOUNDARY
(built-in disjointness/self-modify + any discovered `dos.predicates`
plugin) and pass it in, the same place this command discovers a renderer.
Discovery is I/O (it reads installed entry-point metadata), so it happens
HERE, not inside the pure `arbitrate`. We pass `config=cfg` so the SELF_MODIFY
guard reads the CACHED `cfg.workspace` facts gathered at `_apply_workspace`
time — a foreign repo's `**/*` lane can't edit a `src/dos/` file that isn't
there, so it must not refuse self-modify; the only boundary I/O left is the
plugin entry-point discovery. (Without `config=cfg` the guard falls back to
the conservative full static set and over-refuses every foreign-repo lane.)

### § `--explain` (off by default): append the same GO/STOP next-action hint the

*(in `cmd_arbitrate`)*

`--explain` (off by default): append the same GO/STOP next-action hint the
MCP `dos_arbitrate` tool returns, from the SHARED `dos.interpret`. Off by
default so the byte-faithful default emission below is unchanged. Arbitrate's
"text" form IS compact JSON (it has no human line), so the explain JSON path
carries the field inside the object exactly like the machine form; `--pretty`
is honored by `_emit_with_explanation` itself.

### § scope-gate  (docs/102 §5 — the BINDING pre-effect scope gate: may this

*(in `module level`)*

scope-gate  (docs/102 §5 — the BINDING pre-effect scope gate: may this
PROPOSED write land inside the lane it claims? Refuse-the-write BEFORE the
effect, not detect-after the commit. The verdict IS the exit code so a hook /
broker / shell can branch on it before applying a patch.)
allowed → 0 (the write is contained, let it land). A refusal is non-zero and
distinct per underlying verdict so a caller can tell a partial overrun
(SCOPE_CREEP) from a total miss / undeclared-lane (WRONG_TARGET); both start at
5/6 (matching verdict_cli's _SCOPE_EXIT) so they never collide with argparse's
usage code (2).
scope-gate keys on a (decision, reason) TUPLE, and an unrecognized pair falls to
the contract code (2) — so `unknown` IS the contract code here. Not published in
`_exit_code_contract()` (the gate is a same-process broker call, not a syscall).

### § The lane tree comes from the workspace's declared lanes (the SAME source the

*(in `cmd_scope_gate`)*

The lane tree comes from the workspace's declared lanes (the SAME source the
arbiter and the post-hoc `scope` verb resolve from). The fallback is
deliberately ASYMMETRIC on whether a lane was NAMED:
  * --lane ""  (no lane named) → the GENERIC `**/*` tree: there is genuinely
    no lane to bind against (the no-plan floor — a workspace that declared no
    lanes has no scope to violate).
  * --lane X declared   → its tree.
  * --lane X UNDECLARED  → an empty tree, NOT the generic floor. A named lane
    the workspace does not declare is an UNKNOWN blast radius, so `gate`
    yields WRONG_TARGET → REFUSE. Falling back to `**/*` here would silently
    allow any write against a typo'd / stale lane name — the exact under-
    declaration hole the binding gate exists to close. (`tree_for` returns
    `()` for an undeclared lane, which is what drives the conservative refuse.)

### § liveness  (the temporal verdict — is the run moving, or spinning?)

*(in `module level`)*

liveness  (the temporal verdict — is the run moving, or spinning?)
The verdict IS the exit code (same idiom as `_GATE_EXIT_CODES`) so a loop's
shell can branch on liveness without re-parsing stdout. ADVANCING is 0 (the
success case: the run is moving / no problem). SPINNING/STALLED start at 3 so
they never collide with argparse's usage code (2), which a malformed `--run-id`
reserves as a contract error.

### § productivity  (the loop-economics verdict — is the run still doing work?)

*(in `module level`)*

productivity  (the loop-economics verdict — is the run still doing work?)
`liveness`'s lateral sibling (docs/218): liveness asks "did state move at all?"
off a single since-start count; productivity asks "is the work-per-step RATE
fading?" off a trend. Same verdict-is-exit-code idiom: PRODUCTIVE is 0 (still
doing work / no problem), DIMINISHING/STALLED start at 3 so they never collide
with argparse's usage code (2) which a malformed --deltas reserves.

### § efficiency  (the token-effectiveness verdict — did the tokens buy work?)

*(in `module level`)*

efficiency  (the token-effectiveness verdict — did the tokens buy work?)
`productivity`'s lateral sibling (docs/263): productivity reads a TREND of
per-step work deltas ("is the work-per-step rate fading?"); efficiency reads a
RATIO ("did the tokens buy work?" = work per token). Same verdict-is-exit-code
idiom: EFFICIENT is 0 (the spend bought its work / no problem), COSTLY/WASTEFUL
start at 3 so they never collide with argparse's usage code (2) which a malformed
--work/--tokens reserves.

### § improve  (the self-improving-loop keep-gate — may this loop KEEP this candidate?)

*(in `module level`)*

improve  (the self-improving-loop keep-gate — may this loop KEEP this candidate?)
The kernel leaf of the first self-improving work loop for DOS (docs/280):
`reward.admit` re-aimed from a training-set admission to a commit-KEEP admission,
with the green-suite floor of the apply gate (docs/126) and the circuit breaker
(docs/223). The verdict IS the exit code: KEEP is 0 (the candidate is a witnessed
improvement — keep it), REVERT/ESCALATE start at 3 so they never collide with
argparse's usage code (2) which a malformed --work/--baseline reserves.

### § breaker  (the circuit breaker — this keeps failing; stop, escalate the rung)

*(in `module level`)*

breaker  (the circuit breaker — this keeps failing; stop, escalate the rung)
The generic facility extracted from loop_decide's six hand-coded breakers
(docs/223, idea H2). The verdict IS the exit code: CLOSED is 0 (the path is
still usable), OPEN starts at 3 so it never collides with argparse's usage code
(2) which a bad --consecutive/--max-* reserves.

### § exec-capability  (does this command grant arbitrary code execution? a SHAPE)

*(in `module level`)*

exec-capability  (does this command grant arbitrary code execution? a SHAPE)
The arbitrary-exec capability classifier (docs/224, idea B1) — CC's
dangerousPatterns lifted as the docs/158 "a SHAPE not a word" law applied to
command auditing. The verdict IS the exit code: BOUNDED/EMPTY are 0 (no
arbitrary-exec capability flagged), GRANTS_ARBITRARY_EXEC starts at 3 so it never
collides with argparse's usage code (2). A capability OBSERVATION, advisory by
default — it never denies on its own (the consumer/host decides).

### § hook-exit  (a plain shell hook's exit code → an intervention verb)

*(in `module level`)*

hook-exit  (a plain shell hook's exit code → an intervention verb)
The exit-code classifier (docs/226, idea C3) — CC's hooks.ts convention (0 pass /
2 block / other-nonzero warn) lifted as a pure map a host wires for plain shell
hooks (no JSON). The verb's OWN exit code reflects the intervention rung so a
shell wrapper can branch: PASS=0, then a distinct non-zero per Intervention
(BLOCK=3, WARN=4, DEFER=5, OBSERVE=6). 2 stays the argparse usage code.

### § answer-shape  (is this output an ANSWER, or a structural non-answer?)

*(in `module level`)*

answer-shape  (is this output an ANSWER, or a structural non-answer?)
The grounded-but-not-an-answer verdict (docs/156 §4) — the gap the first real
third-party adoption surfaced: every shipped NUMBER grounded, yet the app shipped
a 5,780-char leaked chain-of-thought log as its "answer". The grounding gate
guarded the facts; nothing guarded that the output was an answer. ANSWER_SHAPED
is 0 (shippable on shape grounds); NON_ANSWER/INDETERMINATE start at 3 so they
never collide with argparse's usage code (2) which a bad flag reserves. NOTE the
asymmetry — ANSWER_SHAPED means "shaped like an answer," NOT "a correct answer";
the semantic question is a JUDGE/HUMAN's, and INDETERMINATE is the abstain floor.

### § reward  (may a training run TRAIN on this trajectory? — the lab on-ramp, docs/230/234)

*(in `module level`)*

reward  (may a training run TRAIN on this trajectory? — the lab on-ramp, docs/230/234)
The reward-set admission verdict (the on-ramp that puts DOS inside a training loop).
The verdict IS the exit code so a dataset-build script can branch without re-parsing:
ACCEPT=0 (enters the positive set), REJECT_POISON=3 (the over-claim a naive sampler
would bank — purged + dispreferred), ABSTAIN=4 (no accountable witness — never mint a
positive), NO_CLAIM=5 (not a candidate). 2 stays the argparse usage code.

### § test-witness  (does this NEW test actually witness this change? — docs/288, TWV)

*(in `module level`)*

test-witness  (does this NEW test actually witness this change? — docs/288, TWV)
FrontierCode's reverse-classical rule as a kernel rung: a test that passes on
the tree WITHOUT the change witnesses nothing (VACUOUS — the false-positive
shape a "tests added ✓" review banks). The verdict IS the exit code so a CI
step / keep-gate can branch without re-parsing: DISCRIMINATES=0 (red->green —
the only witness-minting verdict), VACUOUS=3, UNSATISFIED=4 (the change does
not satisfy its own test), REGRESSIVE=5 (the change breaks the test),
ABSTAIN=6 (narrated outcomes / a missing run). 2 stays the usage code.

### § resume  (the third ARIES phase: replay → re-verify → PROPOSE — docs/107)

*(in `module level`)*

resume  (the third ARIES phase: replay → re-verify → PROPOSE — docs/107)
The verdict IS the exit code so a shell/loop can branch without re-parsing:
RESUMABLE=0 (there is work to continue), COMPLETE=0 (nothing to do — done),
DIVERGED=3 (refuse — a human must decide), UNRESUMABLE=4 (nothing to ground a
resume on). A bad --run-id reserves the argparse contract code 2.

### § rewind  (the conversation-rewind verdict — docs/164 F1.5: backjump + no-good note)

*(in `module level`)*

rewind  (the conversation-rewind verdict — docs/164 F1.5: backjump + no-good note)
`resume`'s CONVERSATION-axis sibling. `resume` rewinds GIT state (HEAD → a re-entry
SHA); `rewind` rewinds the TRANSCRIPT (turns → a minted (turn_index, transcript_digest)
checkpoint over the SAME SUSPEND anchor). Read-only + ADVISORY (docs/99 floor): it
replays the ledger for the minted checkpoint, reads the run's transcript turns, and
PROPOSES a truncation — it NEVER truncates the transcript ("the host owns the
transcript", docs/164 P1.5). The verdict IS the exit code: REWIND=0 (a minted anchor +
a ground-truth stop → excise the dead-end turns), NO_REWIND=0 (the loop continues, no
stop signal), UNANCHORED=3 (a stop fired but no kernel-minted anchor matches → refuse to
rewind to a turn the kernel did not stamp). A bad --run-id is argparse 2.

### § The no-good note tokens: ONLY closed kernel verdict tokens whose STRUCTURED fields

*(in `cmd_rewind`)*

The no-good note tokens: ONLY closed kernel verdict tokens whose STRUCTURED fields
the bare CLI actually has — never prose. The `DIVERGED` token is fieldless, so the
CLI can mint it from the fire alone. The `TOOL_STREAM_REPEATING` token needs a
`(count, turn)` the bare CLI does not have (that comes from a real
`tool_stream.classify_stream` over the env results) — so for a THRASHING/STARVED
fire the CLI emits NO token rather than a token with blank fields. A richer driver
with the env bytes attaches the populated tokens + the F0 env excerpt; the bare CLI
carries only what it can fill honestly (the reason line still names the signal).

### § complete  (the live completion verdict — docs/117: the end of working-in-passes)

*(in `module level`)*

complete  (the live completion verdict — docs/117: the end of working-in-passes)
The forward dual of `dos resume`: same residual = declared − verified, asked
"is the WHOLE job done *now*?" instead of "where do I re-enter a dead run?".
The verdict IS the exit code so a loop can branch without re-parsing:
COMPLETE=0 (stop-on-done), INCOMPLETE=3 (re-dispatch the residual — work remains),
INDETERMINATE=4 (unsound fold / no intent — can't say). UNDERDECLARED reserves 5
for the Phase-4 ScopeSource rung (not emitted yet). A bad --run-id is argparse 2.

### § status  (the folded fact: one fail-closed digest of a run — docs/120 Phase 2)

*(in `module level`)*

status  (the folded fact: one fail-closed digest of a run — docs/120 Phase 2)
`dos status <run_id>` folds the FOUR already-shipped run verdicts — liveness
(is it moving?), ledger-verified progress (what actually shipped, never the
self-report), the held-lease region (which globs it owns), and the resume plan
(once stopped) — into ONE record (`dos.status.status_digest`, the pure fold).
This verb writes NO new verdict logic; it only GATHERS the four inputs at the
boundary (the `cmd_liveness`/`cmd_resume` evidence-gather pattern) and hands them
to the pure fold. The whole point of the surface (docs/120 §3): the digest has
NO `claimed` field by construction — a peer reading `--json` structurally cannot
pick up a self-report it is never handed. The verdict IS the exit code, REUSING
the liveness scheme (the digest's headline is "is this run moving"): ADVANCING=0,
SPINNING=3, STALLED=4; a future verdict is 5, a bad --run-id is the argparse 2.

### § ── read 4: resume — CONDITIONAL on the stopped predicate (docs/142 §3.4). ──

*(in `cmd_status`)*

── read 4: resume — CONDITIONAL on the stopped predicate (docs/142 §3.4). ──
The automatic predicate: the run voluntarily parked (ledger SUSPEND) OR liveness
says STALLED (dead/hung). --stopped / --live override it. We compute resume ONLY
when stopped — `gather_ancestry` is the expensive I/O re-adjudication, and a live
run has no resume verdict (it has not stopped to be resumed). It also requires
real intent: with no INTENT there is nothing to ground a residual on.

### § loop  (the supervisor: keep N dispatch-loops alive across the lane roster)

*(in `module level`)*

loop  (the supervisor: keep N dispatch-loops alive across the lane roster)
These helpers live at the boundary (SUP, docs/99) — the `dos loop` verb and the
drivers/supervisor.py watchdog BOTH import `_supervise_evidence` so the
evidence-gather is defined ONCE, exactly the `_git_delta_count`/`_journal_delta`
shape. The pure `supervise.supervise` verdict never parses a clock or reads a
file; all of that happens here.

### § 2. live leases — replay the WAL (read-only; a missing journal is []), keyed

*(in `_supervise_evidence`)*

2. live leases — replay the WAL (read-only; a missing journal is []), keyed
by lane for the per-lane lookup. NOTE: the journal's true lease identity is
(loop_ts, lane); keying by lane alone assumes single-holder-per-lane (the
disjoint-concurrent norm). If two live leases ever share a lane (a real
double-hold — the race the `pending_lanes` belt guards), we keep the NEWEST
by acquired_at so the shadowing is deterministic, not insertion-order-luck.

### § The DERIVED-CLAIM roster extension (docs/283), only when a budget is declared:

*(in `_supervise_evidence`)*

The DERIVED-CLAIM roster extension (docs/283), only when a budget is declared:
a dynamic-claim workspace's live workers hold leases on PER-PICK handles that
are NOT in the declared roster (the job model: `concurrent=[]`, the real lane
is the narrow claim the worker auto-picked). To count those workers as alive
against the budget, fold each live non-roster, non-exclusive lease lane into the
roster as a REPEATABLE held lane; and ALWAYS add ONE synthetic free repeatable
auto-pick handle so the budget has a fungible lane to spawn onto even from an
empty fleet. Without a budget this whole block is skipped — the roster is the
declared lanes only (byte-for-byte today's).

### § watch  (the push-model watchdog: poll liveness per-run + propose halts — docs/101)

*(in `module level`)*

watch  (the push-model watchdog: poll liveness per-run + propose halts — docs/101)
The PER-RUN-HEALTH sibling of `dos loop`'s population axis. `dos loop` keeps the
roster full; `dos watch` polls `liveness` for a NAMED set of runs on a cadence and,
on SPINNING / hung-past-budget, records an OP_HALT + proposes the stop command (the
auto-halt-record default). It answers the docs/99 §2.1 budget-late incident: an
independent poller whose clock keeps ticking no matter what the watched runs do.
Like `dos loop` it exits 0 (the output is an effect record, not a verdict); the
pure verdict is `dos.liveness.classify`, enacted by `dos.drivers.watchdog`.

### § lease-lane  (the lane-lease WRITE-BACK over the pure arbiter — docs/96)

*(in `module level`)*

lease-lane  (the lane-lease WRITE-BACK over the pure arbiter — docs/96)
`dos arbitrate` is the PURE verdict and deliberately does not persist its grant
(arbiter.py is built on being I/O-free). That is correct for a single process
that threads `live_leases` in memory, but an ephemeral multi-process
orchestrator (a harness `Workflow` whose parallel() branches are separate `dos`
calls) has no shared in-memory list — so without a durable write-back two
branches both ADMIT a colliding tree and the collision is only DETECTED later by
`verify`, never PREVENTED at contention. `dos lease-lane` is that write-back: it
runs the pure arbitrate and, on acquire, journals the grant to the lane-journal
WAL under a mutex, so a sibling branch reconstructs `live_leases` (`lease-lane
live`) and is correctly refused. The arbiter stays pure; durability lives here.

Like `dos arbitrate`, the verdict IS the exit code (0 = acquire, 1 = refuse) so
an orchestrator's shell branches without re-parsing stdout.

### § Record an INTENT to take this lane — the FIRST thing a launcher does, before

*(in `cmd_lease_lane`)*

Record an INTENT to take this lane — the FIRST thing a launcher does, before
preflight, so `dos top` sees the loop the instant it commits to a lane (the
SPAWN→ACQUIRE blind window the audit closed). It grants NO lease: the
OP_SPAWN is non-state-mutating, so the arbiter never admits against it and a
never-acquired intent strands no phantom hold. The eventual `acquire` is
what durably takes the region. Resolve the CID spine id at the boundary so a
SPAWN→ACQUIRE join is possible (same as acquire).

### § halt  (docs/99 — record a STOP DECISION for an in-flight run + propose the

*(in `module level`)*

halt  (docs/99 — record a STOP DECISION for an in-flight run + propose the
command; the effectful `reap`-family boundary verb. The kernel records and
proposes; it NEVER delivers the signal — that needs to know WHAT the handle
is, which is a driver's domain knowledge, not the kernel's. So this is NOT a
verdict-as-exit-code verb: like `loop`, the output is an effect record.)

### § --resumable (docs/107 §4) — the halt that stops a run *resumably* rather than

*(in `cmd_halt`)*

--resumable (docs/107 §4) — the halt that stops a run *resumably* rather than
hard. The HALT above still records the stop INTENT on the WAL; this ADDS a
SUSPEND to the named run's intent ledger, so the run becomes parked-and-
resumable (scavenge-immune, its residual retained by the reachability clause)
instead of a hard kill that loses its in-flight orientation. Requires a
--run-id (the ledger key); without one there is no ledger to suspend, so it
degrades to a plain HALT with a note. The recoverable analogue of the halt
the watchdog already proposes — "stop this" becomes "stop this resumably."

### § Default: the bytes the host parses. A block is the ONLY non-empty output;

*(in `_emit`)*

Default: the bytes the host parses. A block is the ONLY non-empty output;
letting the agent stop prints nothing (an empty/`{}` Stop output is the
host's "allow stop"). The default shape is Claude-Code's
{"decision":"block",…}; a `--dialect` re-renders the SAME block into the
named host's blocking envelope (Gemini's {"decision":"deny",…}, Cursor's
{"permission":"deny",…}) so a non-CC host honors it instead of discarding a
foreign shape (docs/268). A bad dialect name fails LOUD on stderr but still
emits the CC default — a stop refusal must never be silently dropped.

### § MOMENT.STOP, not PRE: a stop refusal fires on the host's stop/AfterAgent

*(in `_emit`)*

MOMENT.STOP, not PRE: a stop refusal fires on the host's stop/AfterAgent
event, whose blocking gate differs from the pre-tool gate. For Gemini,
AfterAgent honors {"decision":"block"} (isBlockingDecision()), NOT the
{"continue":false} that PRE renders (which only shouldStopExecution()
reads, on the tool path) — so a PRE-rendered stop refusal is a SILENT
FAIL-OPEN: the agent stops despite the block (docs/268, the stop-verb
sibling of the BeforeTool fix). STOP also stamps CC's hookEventName as
"Stop" rather than "PreToolUse".

### § 3b. The forward-delta RESET path (docs/259 §Follow-up 2). A host wires

*(in `cmd_hook_marker`)*

3b. The forward-delta RESET path (docs/259 §Follow-up 2). A host wires
    `dos hook marker --reset` on a forward-progress signal (SessionStart /
    UserPromptSubmit, or after a commit): a forward delta zeroes the no-op tally so
    the loop re-enters a wait phase with a fresh budget (the `tool_stream`
    ADVANCING analogue). A RESET is never a Stop-block — it emits NOTHING and
    exits 0 (it is not the loop choosing to keep waiting; it is progress). A write
    failure degrades to "no reset" (the count stays HIGHER → refuse-more, the
    conservative direction), never a crashed turn.

### § 3c. ⚠ The ARMING decision (docs/274 — the load-bearing fix), now a PURE call into

*(in `cmd_hook_marker`)*

3c. ⚠ The ARMING decision (docs/274 — the load-bearing fix), now a PURE call into
    `marker_gate.decide`. A `Stop` hook fires when Claude finishes ANY turn
    (interactive included), NOT only on a keep-alive poll turn; the budget's
    polarity assumes a Stop == "the loop is about to poll again," which is FALSE on
    a bare/global binding, so an unscoped budget blocks every ordinary turn and
    MANUFACTURES the very keep-alive waste it exists to cap (docs/274). So the
    budget arms only with positive evidence this Stop is a poll inside a loop — an
    explicit `--loop`, or a loop-sentinel env var (default `DOS_LOOP`/`CID_RUN_ID`,
    declarable in `dos.toml [marker] arm_on_env`) — and never re-blocks a stop
    Claude Code is ALREADY continuing (`stop_hook_active`, honored per
    `cfg.marker.respect_stop_hook_active`). Not armed → emit nothing, allow stop
    (the fail-safe direction): interactive dev = never armed; headless /loop = armed
    only inside the loop. The two guards live in `marker_gate` so they are one
    unit-tested function, not inline policy.

### § 4. Read the running no-op-turn count (ground-truth durable state, not a flag the

*(in `cmd_hook_marker`)*

4. Read the running no-op-turn count (ground-truth durable state, not a flag the
   model threads through) and ask the PURE budget — `noop_streak.classify`, the
   docs/259 §Follow-up 1 generalization of `wait_marker_budget` (byte-equal on the
   allow bit + carried count, pinned by test_noop_streak). The cap is `--max-markers`
   when explicitly passed, else `cfg.marker.max_streak` (`dos.toml [marker]`), else
   the generic 4. Any read error → emit nothing (advisory fail-safe: never trap the
   loop open on a sensor read failure).

### § Budget remains → hold the turn open one more marker. Record the marker FIRST

*(in `cmd_hook_marker`)*

Budget remains → hold the turn open one more marker. Record the marker FIRST
(so the count is durable even if the print is lost), then emit the block dialect
CC honors. A write failure degrades to "allow stop" — never block on a tally we
could not persist (which would let the count desync and the loop spin). The
`budget_reason` (the pinned `wait-marker N/M — turn held open` wording) was built
above, shared with the --json surface.

### § 0. The native fast path (docs/286): if a per-platform wheel bundled the static

*(in `cmd_hook_posttool`)*

0. The native fast path (docs/286): if a per-platform wheel bundled the static
   dos-hook binary for this arch, it serves the POST decision (read+append the
   session stream, classify, emit the REPEATING/STALLED WARN) in ~10 ms vs the
   Python cold-start — byte-identical on the dialect + the stream record (docs/124
   parity). It consumes stdin + emits the dialect itself, so this MUST run before
   we read stdin below. Returns an int (owned) or None (no binary / opt-out) →
   fall through to the Python decider unchanged.

### § 5. Replay-then-classify-then-append-ONCE, so the durable record can carry the

*(in `cmd_hook_posttool`)*

5. Replay-then-classify-then-append-ONCE, so the durable record can carry the
   firing fact (docs/179 Phase 0). We read the prior stream, classify the
   would-be stream (prior + this step) to know the verdict for THIS step, then
   append the step ONCE — stamping run_id/step_index/verdict_state when it fired
   so a labeler (`dos.firing_label`) can join the firing to the run's git-minted
   ground truth later. Classifying over (prior + step) is identical to
   classifying the re-read stream (the step is the same bytes), so the verdict
   the agent sees is unchanged — this only makes the firing a durable fact, not a
   re-derived one. The accumulator I/O is wrapped fail-safe: ANY read/write error
   degrades to "emit nothing" (advisory — never block on a sensor I/O error).

### § The ONLY thing on stdout: the host's PostToolUse dialect. `warn_payload`

*(in `cmd_hook_posttool`)*

The ONLY thing on stdout: the host's PostToolUse dialect. `warn_payload`
returns the canonical Claude-Code dict; for a non-CC host we transcode it
through the selected `--dialect` renderer (docs/217). The default
(`claude-code`) round-trips to the SAME bytes — so the sibling `dos hook
stop` no-op lesson (emit the wrong dialect → silent no-op) is honored: this
path emits exactly the shape the SELECTED host honors, and nothing else.

### § 0. The native fast path (docs/286): if a per-platform wheel bundled the static (2)

*(in `cmd_hook_pretool`)*

0. The native fast path (docs/286): if a per-platform wheel bundled the static
   dos-hook binary for this arch, it serves the PRE decision in ~10 ms vs ~600 ms
   of Python cold-start — byte-identical on the dialect (the docs/124 parity
   contract). It consumes stdin + emits the dialect itself, so this MUST run
   before we read stdin below. Returns an int (it owned it) or None (no usable
   binary / DELEGATE / opt-out) → fall through to the Python decider unchanged.

### § 5b. The OPERATOR observability nudge (help_summary): on the 1st + every 5th

*(in `cmd_hook_pretool`)*

5b. The OPERATOR observability nudge (help_summary): on the 1st + every 5th
    behavior-changing help THIS session, fold the WAL and append a one-line "DOS
    has caught N things this session" to the dialect's additionalContext — so the
    person running the fleet learns, in their normal flow, that the substrate is
    working. Purely ADDITIVE: it never touches the deny/pass decision (`dialect`
    stays exactly what decide() returned), and any fault fails silent (the help
    count is observability, never an enforcement input). The count comes from the
    env-authored journal we just appended to — never from agent narration.

### § 6. The ONLY thing on stdout: the host's PRE dialect, or nothing. `decide()`

*(in `cmd_hook_pretool`)*

6. The ONLY thing on stdout: the host's PRE dialect, or nothing. `decide()`
   returns the canonical Claude-Code dict; for a non-CC host we transcode it
   through the selected `--dialect` renderer (docs/217). The default
   (`claude-code`) round-trips to the SAME bytes, so CC behavior is unchanged.
   A bad --dialect NAME fails LOUD on stderr but never denies a real call
   (fail-to-passthrough): we emit nothing rather than the wrong host's bytes.

### § Bound an unbounded WAL: fold the whole journal to a single CHECKPOINT

*(in `cmd_journal`)*

Bound an unbounded WAL: fold the whole journal to a single CHECKPOINT
snapshot of the live set and rewrite the file to it, crash-safely under
the lease mutex. The verdict is unchanged (replay over the compacted
journal is byte-identical) — this is purely a SIZE op, so it is the one
journal verb that WRITES, and only this branch ensures DOS_HOME (a
rewrite is an emission); tail/replay/seq above stay read-only.

### § `--explain` (off by default): append the same next-action gloss the MCP

*(in `cmd_man`)*

`--explain` (off by default): append the same next-action gloss the MCP
`dos_check_reason` tool returns, from the SHARED `dos.interpret`. A man
page is browsing a KNOWN reason (it is in the registry, or `spec` would
be None above), so the gloss is the safe-to-emit / blocks-or-advisory one.
We hand `interpret.check_reason` the FULL dict the MCP tool builds (not a
narrow `{known, refusal}`) so the two surfaces stay structurally in
lockstep: if the gloss ever grows a branch on `category`/`summary`, the
CLI man path can't silently diverge from MCP. JSON carries it INSIDE
`fields` (the `interpretation` convention); text gets a trailing line. Off
by default, so the byte-faithful man page is unchanged for every consumer.

### § --- bounded --follow loop (foreground convenience; NOT a daemon) ----------

*(in `cmd_export`)*

--- bounded --follow loop (foreground convenience; NOT a daemon) ----------
Poll → drain → persist → sleep, advancing the in-memory floor each tick so we
never re-ship. Terminates after `--follow-max` iterations (default 0 = until the
interrupt) — the host still owns the long-run cadence; this is a tail, not a
service. A KeyboardInterrupt exits cleanly (0). The cursor file is the durable
hand-off so the NEXT process resumes where this one stopped.

### § The config-integrity linter (docs/227, G1 from docs/189): one pure kernel

*(in `cmd_doctor`)*

The config-integrity linter (docs/227, G1 from docs/189): one pure kernel
leaf that finds DEAD POLICY in the lane taxonomy + reason registry — a lane
that can't be arbitrated (treeless), a contradiction (concurrent∩exclusive),
a dangling reference (autopick/alias target undeclared), a SHADOWED lane (its
region wholly inside another's → unreachable, the CC `detectUnreachableRules`
analogue), an overlapping concurrent roster (order-sensitive), and a dead
reason see_also cross-ref. Replaces the scattered `_treeless_lane_findings`
+ `_overlapping_concurrent_lane_findings` helpers (their logic now lives in
the tested leaf). Gather the config here, decide in the leaf — the
`_state_health_findings` shape.

### § SKP Phase 1a — `dos doctor --json`: the machine-readable workspace report a

*(in `cmd_doctor`)*

SKP Phase 1a — `dos doctor --json`: the machine-readable workspace report a
generic skill reads with one call to discover its layout (paths/lanes) and
ship grammar (stamp) instead of hardcoding `docs/_plans/` (the skill-pack's
WCR-on-ramp). Emits exactly the fields the text form prints, as one object;
the read-only discipline is unchanged (no `.dos/` is created — `active_home`
resolves without writing). When `--check` is also passed, the findings ride
along as a `findings` array AND set the exit code, preserving the rail.

### § Discovered FACTS about this workspace, gathered once at config-build

*(in `cmd_doctor`)*

Discovered FACTS about this workspace, gathered once at config-build
time (the third seam-value after lanes/paths — see
`config.WorkspaceFacts`). `is_kernel_repo` says whether DOS is
serving its OWN tree (in which case a whole-repo lease trips the
SELF_MODIFY guard); `kernel_runtime_files_present` is how many of the
kernel's runtime modules actually exist here (0 ⇒ foreign repo). This
is what makes the arbiter workspace-aware WITHOUT re-probing the disk
on every admission. None when facts were never gathered.

### § The environment print (Axis "under-what", docs/115): the

*(in `cmd_doctor`)*

The environment print (Axis "under-what", docs/115): the
content-addressed record of *under what* this config adjudicates —
kernel version + kernel git SHA + Python + OS/arch + declared tools.
`digest` is the EnvId a fossil carries and a future fleet pin compares.
The single most useful field is `kernel_sha`: a verdict from a stale
editable `.pth` pointing at a sibling worktree prints a DIFFERENT sha
here, so "which kernel actually ran" is a fact, not a guess. None when
the print was never gathered (the pure construction path).

### § Axis 8 (docs/121/265) — the non-git evidence sources resolvable as witnesses

*(in `cmd_doctor`)*

Axis 8 (docs/121/265) — the non-git evidence sources resolvable as witnesses
`verify` can consult beyond git (built-in `null` first, then discovered
`dos.evidence_sources` drivers). Marks the one THIS workspace has wired
`verify` to (the `[verify] non_git_oracle` config) with `*`, so an operator
sees both what is available AND what is actually consulted — the evidence
analogue of the active overlap policy. An unwired workspace shows none active
(git-only `verify`, the byte-identical default).

### § docs/189 §A1 — the enforcement handlers that CONSUME an intervention decision

*(in `cmd_doctor`)*

docs/189 §A1 — the enforcement handlers that CONSUME an intervention decision
and propose the effect (the PEP-adjacent seam). Built-in `observe` (the
unshadowable zero-disruption floor) first, then any discovered
`dos.enforce_handlers` driver. An operator sees which actuators can act on a
verdict here, the handler analogue of the active judges / predicates. The
built-in only ever proposes OBSERVE — escalation past it is opt-in (a driver).

### § Axis 7 — the disjointness SCORER the arbiter admits on (docs/113). Shows the

*(in `cmd_doctor`)*

Axis 7 — the disjointness SCORER the arbiter admits on (docs/113). Shows the
ACTIVE policy (the configured scorer), the resolvable set, and the active
soft-overlap tolerance — so an operator sees exactly how concurrency is
decided here, the overlap analogue of the active predicates / judges. The
active policy is marked with `*`; the deterministic prefix floor under it is
always in force regardless (a swappable scorer can only refuse-more).

### § Completeness rail (`--check`). Two independent rails accumulate findings

*(in `cmd_doctor`)*

Completeness rail (`--check`). Two independent rails accumulate findings
(computed above); ALL are reported, then a non-empty set makes `--check`
exit non-zero:
  * SCV (3c) — a DECLARED [stamp] grammar that matches none of the repo's
    own recent ship-shaped commits (a silent `via none` on real ships).
  * WCR (3b) — a lane named in concurrent/exclusive/autopick but absent
    from [lanes.trees]: a lane with no tree can't be arbitrated (the
    disjointness algebra has nothing to compare). The lane analogue of
    HACKING.md's "a reason emitted but not in the registry → fail."

### § 1. Scaffold the workspace. Two modes:

*(in `cmd_quickstart`)*

1. Scaffold the workspace. Two modes:
   - default: render the auto-derived generic dos.toml (matches `dos init`).
   - --driver NAME: scaffold under a named driver pack (e.g. workshop) so a
     stranger sees a REFERENCE driver's real lanes. We resolve the driver
     factory here and serialize ITS taxonomy into the dos.toml [lanes] so
     the on-disk config and the in-process config agree (and `--keep` leaves
     a workshop-configured repo behind). The throwaway repo has no competing
     dos.toml, so --driver is NOT shadowed here (unlike a real workspace).

### § NOTE (docs/227): the lane-integrity rails that used to live here as in-CLI helpers

*(in `module level`)*

NOTE (docs/227): the lane-integrity rails that used to live here as in-CLI helpers
— `_treeless_lane_findings` (treeless lanes) and `_overlapping_concurrent_lane_findings`
(the docs/210 roster-overlap smell) — were lifted DOWN into the pure `config_lint`
leaf (which `cmd_doctor` now calls, and the `dos lint` verb exposes). The CLI shell
carries no policy of its own (layer 3); those checks are mechanism and belong in the
kernel. `config_lint` ALSO adds the four with no prior home (contradiction, dangling
autopick/alias, dead reason see_also, strict-subset SHADOW). The overlap-pair logic
still has a second consumer in `supervise.overlapping_concurrent_lanes` (the spawn
planner); both stand on the same `_tree.lane_trees_disjoint` definition, so they
cannot drift.

### § gate  (the typed empty-packet verdict — gate_classify as a verb)

*(in `module level`)*

gate  (the typed empty-packet verdict — gate_classify as a verb)
The exit-code map (SKP Phase 1b). The verdict IS the exit code so a skill's
shell can branch on it without re-parsing stdout — and each typed verdict gets
a DISTINCT code, because the loop treats them very differently (a STALE-STAMP
self-heals, a BLOCKED is operator-gated, a RACE retries once). The codes start
at 3 so they never collide with argparse's usage code (2), which `dos gate`
reserves for a contract error (a malformed/missing sidecar, a bad picks list).
LIVE is 0 (the success case: the packet has dispatchable work).

### § `--explain` (off by default): append a one-line next-action hint from the

*(in `cmd_gate`)*

`--explain` (off by default): append a one-line next-action hint from the
`dos.interpret` seam (the same module the verify/arbitrate hints come from,
so the gloss style is consistent) — so an agent gating its empty case reads
the loop routing (CONTINUE / STOP / SELF-HEAL / RETRY) without re-deriving
`gate_policy`. (Gate has no MCP tool today, so unlike verify/arbitrate this is
a CLI-only surface; `interpret.gate` is parity-ready if a `dos_gate` MCP tool
is ever added.) JSON carries it INSIDE the object (the `interpretation`
convention `--explain` uses everywhere); text prints it on a trailing line.
Off by default, so the byte-faithful default emission below is unchanged.

### § `dos pickable` (docs/207 Phase 1) — expose the shipped pre-dispatch gate.

*(in `module level`)*

`dos pickable` (docs/207 Phase 1) — expose the shipped pre-dispatch gate.

The verdict IS the exit code, and a HELD verdict carries a PER-HoldReason code
so a skill branches on WHICH hold (DRAFT_CLASS→/promote, OPERATOR_GATED→
escalate, SOAK_OPEN→wait, …) without parsing. OFFERABLE=0; each hold reason a
distinct nonzero, all disjoint from the contract-error code (2).

### § `dos enumerate` (docs/207 Phase 2) — the phase-list producer surface.

*(in `module level`)*

`dos enumerate` (docs/207 Phase 2) — the phase-list producer surface.

Reads a plan-doc's bytes at this boundary, runs the pure `enumerate_units`, and
emits the `Enumeration` (units in doc order + the shipped/remaining partition +
typed DriftNotes). The exit code reports the enumeration STATUS: a clean
enumeration is 0; a drift note (a list↔table mismatch or an unparseable region)
is nonzero so a skill notices; an empty universe is its own code.
enumerate reports an enumeration STATUS, not a verdict token: CLEAN=0, a
list↔table/unparseable DriftNote=3, an empty universe=4. Held in an ExitMap so
`_exit_code_contract()` derives the row like every other verb; the named
constants below are projections the handler returns by name.

### § `dos cooldown` (docs/207 Phase 3) — the anti-churn read surface.

*(in `module level`)*

`dos cooldown` (docs/207 Phase 3) — the anti-churn read surface.

Folds a unit's OP_ATTEMPT history (from the lane journal, gathered at this
boundary) into the typed Cooldown verdict. The exit code IS the verdict:
CLEAR=0, RECENTLY_ATTEMPTED=3 — so a loop's pick-selection skips a cooled unit
without parsing.

### § `dos pick-priority` (docs/254) — the freshness sort-key read surface.

*(in `module level`)*

`dos pick-priority` (docs/254) — the freshness sort-key read surface.

Folds a unit's OP_ATTEMPT history (gathered at this boundary, same source as
`dos cooldown`) into the freshness verdict + the sort_key a picker appends to its
own (priority, status, …) key. The exit code IS the verdict: NEVER_ATTEMPTED=0
(fresh — pick first), ATTEMPTED=3 — so `dos pick-priority UNIT && pick UNIT` reads
naturally and a loop can branch on freshness without parsing.

### § `dos reconcile` (docs/207 Phase 4) — the quiet-completion gate.

*(in `module level`)*

`dos reconcile` (docs/207 Phase 4) — the quiet-completion gate.

Joins a unit's CLAIM against the ORACLE verdict. The exit code IS the verdict:
VERIFIED=0 (leaves the residual), QUIET_INCOMPLETE=3 (claimed-but-not-shipped,
the dangerous case), HONEST_OPEN=4 (honest open work). A QUIET_INCOMPLETE is
nonzero so a loop's archive step notices the claim the oracle refuted.

### § `USE THIS WHEN` help bodies (item 3). The MCP tool docstrings carry excellent

*(in `module level`)*

`USE THIS WHEN` help bodies (item 3). The MCP tool docstrings carry excellent
"reach for this when…" prose; the CLI's one-line `help=` only listed flags, so
an agent driving `dos` couldn't tell verify from arbitrate from liveness by
purpose. These `description=` bodies (shown on `dos <verb> --help`, with the
one-line `help=` kept for the top-level `dos --help` listing) port that prose so
the two surfaces teach the same thing. The exit-code line at the foot of each is
CLI-specific — the same contract `dos doctor --json`'s `exit_codes` publishes
(item 1) — so `--help` and the machine table agree.

### § attest (docs/246 Phase 1) — the portable, SIGNED receipt over an

*(in `build_parser`)*

attest (docs/246 Phase 1) — the portable, SIGNED receipt over an
effect-witness verdict: the NON-PARTICIPANT surface over the shipped
`effect_witness` engine. Gathers an independent read-back (os_acceptance /
state_diff witness) at the boundary, joins the claim, wraps the four-valued
verdict in a `dos.attest.Receipt`, and HMAC-signs it so a third party who was
not present verifies the certificate with the shared key alone. The verdict
is the engine's, untouched — packaging + a signature, not new decision logic.

### § improve (docs/280) — the keep-gate of the first self-improving work loop for

*(in `build_parser`)*

improve (docs/280) — the keep-gate of the first self-improving work loop for
DOS. `reward.admit` re-aimed from "may a fine-tune TRAIN on this?" to "may this
loop KEEP this candidate commit?": KEEP iff the suite is green AND the truth
syscall is clean AND the env-measured metric strictly improved (none of which
the loop authored). Bounded by a breaker that ESCALATEs to a human after N
non-keeps. PURE, no-plan — the driver gathers the facts; the kernel decides.

### § status (docs/120 Phase 2) — `dos status <run_id>`: the FOLDED FACT. One

*(in `build_parser`)*

status (docs/120 Phase 2) — `dos status <run_id>`: the FOLDED FACT. One
fail-closed digest of a run, gathering the four shipped verdicts (liveness /
ledger-verified progress / held-lease region / resume-once-stopped) into one
record (`dos.status.status_digest`, the pure fold). No new verdict — a
boundary-gather, the `liveness`/`resume` pattern. The `--json` shape carries
NO `claimed` key by construction (the §3 invariant: a peer cannot read a
self-report it is never handed). The verdict IS the exit code (the liveness
scheme: ADVANCING=0, SPINNING=3, STALLED=4).

### § halt — record a STOP DECISION for an in-flight run + propose the command

*(in `build_parser`)*

halt — record a STOP DECISION for an in-flight run + propose the command
(docs/99). The effectful `reap`-family boundary verb: it journals an
OP_HALT and echoes the host-supplied stop command for a driver/operator to
run. It NEVER delivers a signal — `--handle` is an OPAQUE identifier the
kernel records and interprets nothing about (domain-free; the kernel must
not learn what a process is). NOT verdict-as-exit-code: 0 = recorded.

### § export — the verdict-journal DRAIN (docs/266): ship the stream outward to an

*(in `build_parser`)*

export — the verdict-journal DRAIN (docs/266): ship the stream outward to an
observability backend (a JSONL file a shipper tails / StatsD / OTLP). The
delivery-side sibling of `observe`, as `notify` is to the decisions/top
projections. A transport driver (`file`/`statsd`/`otlp`) is discovered by name;
the default `null` exporter reports what WOULD ship and sends nothing (safe,
outward-facing-aware). Read-only on DOS state.

### § Cap on the number of auto-derived concurrent lanes. A repo with hundreds of

*(in `module level`)*

Cap on the number of auto-derived concurrent lanes. A repo with hundreds of
top-level dirs (a monorepo, an extracted vendor tree) should not scaffold a
hundred-lane taxonomy — beyond a handful the operator wants to curate it by
hand. The cap keeps the scaffold legible; the excess dirs simply stay under the
whole-repo `global` lane until the operator names them.

### § docs/207 Phase 7 — the skill on-ramp. `dos init --skills` copies the generic

*(in `module level`)*

docs/207 Phase 7 — the skill on-ramp. `dos init --skills` copies the generic
SKILL.md screenplays from the wheel's package-data into the workspace's
`.claude/skills/`, so a stranger runs ONE command and has editable local skills
to work on directly (not package-buried prose). The package-data is the SEED, not
a runtime binding — the folders→lanes one-time-scaffold pattern.

### § docs/207 Phase 7 — `dos init --skills [names…]` / `--all`: copy the generic

*(in `cmd_init`)*

docs/207 Phase 7 — `dos init --skills [names…]` / `--all`: copy the generic
SKILL.md screenplays into `<target>/.claude/skills/` as editable local files.
`--skills` with no names copies the core set; `--all` copies the full pack.
Runs whether or not the dos.toml is (re)scaffolded — a host can add skills to
an already-init'd workspace, and the config-exists guard below never blocks it.

### § The verdict-journal dimension this verb emits under (docs/262 P2). Optional —

*(in `module level`)*

The verdict-journal dimension this verb emits under (docs/262 P2). Optional —
set on the one-line verdict verbs (liveness/productivity/efficiency/breaker/
hook-exit) so `emit` can record a VerdictEvent when observation is armed; left
"" for maps that are not verdict dimensions (commit-audit, etc.), which then
simply don't auto-record.

### § Output through the renderer seam (RND Phase 2). The default `text`/`json`

*(in `cmd_arbitrate`)*

Output through the renderer seam (RND Phase 2). The default `text`/`json`
renderer both emit compact sorted JSON for a decision — byte-identical to
the old unconditional `json.dumps(..., sort_keys=True)`. `--pretty`
re-indents the rendered string when it is JSON (the built-in path); a
custom non-JSON renderer (`--output terse`) ignores pretty, as it should.

### § Resolved-decision capture (docs/75 §5.7): a `--force` that turned a refusal

*(in `cmd_arbitrate`)*

Resolved-decision capture (docs/75 §5.7): a `--force` that turned a refusal
into an acquire is an attributable HUMAN override worth recording. arbitrate
is PURE, so we cheaply re-run it WITHOUT force to confirm the non-forced call
would NOT have acquired (else there is nothing to "resolve"). This is the
only persisting write `dos arbitrate` makes; it ensures `.dos/` first.

### § Resolve the candidate text at the boundary: --file/--text, with "-" = stdin.

*(in `cmd_answer_shape`)*

Resolve the candidate text at the boundary: --file/--text, with "-" = stdin.
Exactly one source; --file wins if both are given (an explicit path is the
stronger intent). A missing --text AND --file is a contract error, not an
empty-string NON_ANSWER — "you forgot to say what to classify" is a usage
fault, distinct from "you classified the empty string".

### § The lane-divergence signal: the CALLER decides whether ground truth moved

*(in `cmd_resume`)*

The lane-divergence signal: the CALLER decides whether ground truth moved
past the resume point on the run's lane. Exposed as an explicit flag so the
advisory verdict stays honest about what it was told (a richer driver
computes it from the lane tree + commits-since-resume; the bare CLI lets the
operator assert it). Default False — no divergence assumed.

### § The DERIVED-CLAIM budget (docs/283): which roster lanes are fungible auto-pick

*(in `_supervise_evidence`)*

The DERIVED-CLAIM budget (docs/283): which roster lanes are fungible auto-pick
HANDLES the `max_concurrency` cap can ride (the disjointness of their per-pick
claims is enforced by the worker's own `arbitrate`, not a fixed tree here). A
declared `autopick` lane that is not exclusive is repeatable. (Computed always;
only CONSUMED when the policy sets a budget — a no-budget run ignores it.)

### § Acting-on-spin evidence (docs/90 §5): a SPINNING lane carries HOW LONG

*(in `_supervise_evidence`)*

Acting-on-spin evidence (docs/90 §5): a SPINNING lane carries HOW LONG
it has been spinning — exactly the heartbeat staleness that just made
`classify` say SPINNING. We REUSE `hb_age` (no new I/O, the arbiter
rule); only a SPINNING verdict gets the age, so an ADVANCING/None lane
carries None and can never trip the PROPOSE_HALT threshold (fail-quiet).

### § The standing population policy comes from `dos.toml [supervise]` (the

*(in `cmd_loop`)*

The standing population policy comes from `dos.toml [supervise]` (the
docs/99 seam) — target + count_spinning_as_alive + reap_stalled, declared
once and shared with the watchdog driver. An explicit `--target` overrides
only the target for THIS run (a one-off population), leaving the two
booleans at their declared values; absent, the config target stands.

### § `--target` and `--max-concurrency` each override just their field for THIS run

*(in `cmd_loop`)*

`--target` and `--max-concurrency` each override just their field for THIS run
(a one-off population / concurrency budget), leaving the rest at the declared
`[supervise]` values. `--max-concurrency` is the docs/283 derived-claim cap:
it lets the supervisor reach a target above the static disjoint-lane count by
riding a fungible auto-pick handle (the arbiter still gates each per-pick claim).

### § Acting-on-spin (docs/90 §5): a *proposed* halt of a live-but-stuck spinner.

*(in `_emit`)*

Acting-on-spin (docs/90 §5): a *proposed* halt of a live-but-stuck spinner.
ADVISORY — the supervisor proposes, the operator enacts with an explicit
`dos halt --handle <run> --lane <lane>` (the worker's run handle is the
operator's to supply; the kernel never kills a live worker — the docs/99
PDP-not-PEP floor). Distinct from `reap`, which frees a confirmed-dead lease.

### § Refresh a HELD lease — the writer that makes liveness SPINNING reachable

*(in `cmd_lease_lane`)*

Refresh a HELD lease — the writer that makes liveness SPINNING reachable
from real journal evidence (a fresh beat proves alive-now without
counting as progress). Beats only a currently-live lease (else writes
nothing): the load-bearing guard against a stray post-release beat
reading a dead run alive. `beat` is True iff a live lease was beaten.

### § Resolve the CID spine id at the BOUNDARY (docs/137): explicit flag, else the

*(in `cmd_lease_lane`)*

Resolve the CID spine id at the BOUNDARY (docs/137): explicit flag, else the
lineage env the loop already runs under, else none. Never minted here and
never read inside the pure arbiter — the lease just carries the id of the run
that took it, so a held lane is traceable back via `dos trace` (the WAL↔spine
join). Empty ⇒ a pre-join ACQUIRE, replayed unchanged.

### § The marker-grammar reason (the `wait-marker N/M — turn held open` wording the Go

*(in `cmd_hook_marker`)*

The marker-grammar reason (the `wait-marker N/M — turn held open` wording the Go
parity corpus + test_marker_sensor pin), NOT `noop_streak`'s "no-op streak …" prose
— the equivalence pin guarantees only the allow bit + carried count, never the
string. `decision.noop_turns` is `emitted` on a refuse, `emitted + 1` on an allow,
so this matches the count `wait_marker_budget` rendered in each case.

### § Project the man page over the ACTIVE workspace's ReasonRegistry — the

*(in `cmd_man`)*

Project the man page over the ACTIVE workspace's ReasonRegistry — the
built-in reasons PLUS any the workspace declared (dos.toml / extend()).
A custom reason therefore gets a real page through the same verb, which
is the hackability payoff: the manual is a render of the registry, never
a hand-authored doc (DOM Design-rule 1).

### § LIVE footer (DOM dynamic-footer rule): how many decisions are carrying

*(in `cmd_man`)*

LIVE footer (DOM dynamic-footer rule): how many decisions are carrying
this reason *right now*, queried at call-time — never baked in. This is
the instance↔type bridge: `dos man` defines the token, the count is the
live occupants from the decision queue. Best-effort: a queue read fault
must never break a man page (the manual never blocks).

### § Transport occupants take a subset of channel/url/token/dry-run/root; the

*(in `cmd_notify`)*

Transport occupants take a subset of channel/url/token/dry-run/root; the
null sink takes none. We build the SUPERSET, then filter it to each
constructor's accepted parameters (below, inside resolve) so a transport
is never handed a kwarg it doesn't accept — `slack` ignores url/token,
`webhook` ignores channel — without the CLI branching per driver.

### § export  (the verdict-journal DRAIN — ship the stream outward to observability;

*(in `module level`)*

export  (the verdict-journal DRAIN — ship the stream outward to observability;
         docs/266. The delivery-side sibling of `observe`, as `notify` is to the
         decisions/top projections.)

### § The always-honest verifiability cold-open (machine form): how many of

*(in `cmd_doctor`)*

The always-honest verifiability cold-open (machine form): how many of
the repo's recent commits `dos verify` can actually check, via the
same two pure stamp predicates the text headline uses. A skill/CI
reads this to decide "is this repo even verifiable?" without a verify
that false-accuses on `via none`.

### § ADM Phase 3c — the active admission predicates (built-in

*(in `cmd_doctor`)*

ADM Phase 3c — the active admission predicates (built-in
disjointness + self-modify, THEN any discovered `dos.predicates`
plugin), in conjunction order, so an operator/skill can see exactly
what gates this arbiter. The predicate analogue of "see the active
reason set." Best-effort: discovery never crashes the report.

### § Axis 7 (docs/113) — the disjointness scorer the arbiter admits on:

*(in `cmd_doctor`)*

Axis 7 (docs/113) — the disjointness scorer the arbiter admits on:
the active policy name, the resolvable set, and the active
soft-overlap tolerance. The deterministic prefix floor is always in
force under whatever policy is active (a swappable scorer can only
refuse-more), so a skill/CI can see how concurrency is decided here.

### § The always-honest cold-open (the iconicity on-ramp): one line, correct on

*(in `cmd_doctor`)*

The always-honest cold-open (the iconicity on-ramp): one line, correct on
EVERY repo, saying whether `dos verify` can check this repo's claims at all —
computed from the repo's own recent commits via the same two stamp predicates
the grep rung uses, so it never cries wolf the way a naive `verify` would on a
Conventional-Commits repo.

### § The environment print (docs/115): *under what* this kernel adjudicates. The

*(in `cmd_doctor`)*

The environment print (docs/115): *under what* this kernel adjudicates. The
digest is the EnvId a fossil carries; the kernel SHA is the stale-`.pth`
lie-detector — a verdict produced by a sibling worktree prints a different
SHA, so "which kernel ran" stops being a guess. Tools list is empty until a
workspace declares `[env] tools` (a later phase); kernel/Python/OS always print.

### § 4. The fleet act (default mode) — the admission half of the pitch. The

*(in `cmd_quickstart`)*

4. The fleet act (default mode) — the admission half of the pitch. The
   verify contrast catches the LIE; this catches the COLLISION: three
   calls through the real arbiter show admit / redirect-off-a-busy-
   region / refuse-when-saturated. Same honesty rule as the verify
   beats: every line is the kernel's own decision, not a canned string.

### § git leaves read-only files under .git/objects on Windows, which a

*(in `cmd_quickstart`)*

git leaves read-only files under .git/objects on Windows, which a
plain rmtree can't delete (and `ignore_errors=True` would silently
LEAVE the temp dir behind — a litter bug, not a clean run). Clear the
read-only bit on each undeletable entry and retry, so the throwaway
repo is actually removed on every platform.

### § GLOBAL workspace flags — accepted BEFORE the subcommand too, so

*(in `build_parser`)*

GLOBAL workspace flags — accepted BEFORE the subcommand too, so
`dos --workspace . verify …` works as well as `dos verify --workspace . …`.
The per-subcommand copies (added by `_add_workspace_flags` below) use a
SUPPRESS default so they only override this global value when actually
passed; see `_add_workspace_flags` for the precedence contract.

### § `metavar` collapses argparse's auto-generated {init,verify,…} wall (in the

*(in `build_parser`)*

`metavar` collapses argparse's auto-generated {init,verify,…} wall (in the
usage line AND the positional-args block) to a short hint, so the CURATED,
grouped command list in the description above is the single source a reader
scans. The full registered set is still reachable (each verb is its own
subparser; `dos <verb> --help` works), it's just not dumped as one flat blob.

### § docs/207 Phase 7 — copy the generic SKILL.md screenplays into the workspace's

*(in `build_parser`)*

docs/207 Phase 7 — copy the generic SKILL.md screenplays into the workspace's
.claude/skills/ as editable local files. `--skills` is a BARE flag (copies the
core set) so the north-star `dos init --skills /tmp/svc` reads `/tmp/svc` as
the positional DIR with no ambiguity (a value-taking flag would swallow it).
Named skills go through the repeatable `--skill NAME`; `--all` copies the pack.

### § docs/134 §6 / docs/165 / docs/221 — bind the verdict to an agent runtime by

*(in `build_parser`)*

docs/134 §6 / docs/165 / docs/221 — bind the verdict to an agent runtime by
wiring the three shipped DOS hooks into THAT host's own config file (merged,
never clobbering the user's own hooks). `--hooks <host>` is the cross-vendor
form (claude-code/cursor/codex/gemini/antigravity); `--with-hooks` is the back-compat alias
for `--hooks claude-code`. Works on an already-init'd workspace too.

### § verify-receipt (docs/246) — the third-party check, NO loop access. Re-derives

*(in `build_parser`)*

verify-receipt (docs/246) — the third-party check, NO loop access. Re-derives
the receipt's canonical bytes and checks the signature against the shared/public
key, then renders the carried verdict WITH its tier. The one place that fails
LOUD: an invalid signature → INVALID, never a silent downgrade. Phase 1 verifies
HMAC; the asymmetric (Ed25519) path is the Phase-2 [attest] extra.

### § productivity (docs/218) — liveness's lateral sibling. Where liveness reads a

*(in `build_parser`)*

productivity (docs/218) — liveness's lateral sibling. Where liveness reads a
single since-start count ("did state move?"), productivity reads a TREND of
per-step work deltas ("is the work-per-step rate fading?"). The diminishing-
returns gate lifted from Claude Code's session loop (tokenBudget.ts). PURE,
timeless (no clock), no-plan/no-telemetry — it needs only the deltas.

### § efficiency (docs/263) — productivity's lateral sibling. Where productivity reads

*(in `build_parser`)*

efficiency (docs/263) — productivity's lateral sibling. Where productivity reads
a TREND of per-step work deltas ("is the work-per-step rate fading?"), efficiency
reads a RATIO ("did the tokens buy work?" = work per token spent). The token-
economics completion of the loop-economics family. PURE, timeless (no clock),
no-plan/no-telemetry — it needs only the two env-authored counts.

### § breaker (docs/223) — the generic circuit-breaker extracted from loop_decide's

*(in `build_parser`)*

breaker (docs/223) — the generic circuit-breaker extracted from loop_decide's
six hand-coded breakers (idea H2). Counts failures of ONE class (consecutive +
total, the CC denialTracking split) and OPENs on either max; an OPEN verdict
names the escalation rung (ORACLE→JUDGE→HUMAN, idea H3). PURE, no-plan — the
CLI is the read-only peek; the write path (record_failure/_success) is library.

### § answer-shape (docs/156 §4) — the grounded-but-not-an-answer verdict. Catches a

*(in `build_parser`)*

answer-shape (docs/156 §4) — the grounded-but-not-an-answer verdict. Catches a
structurally-disqualified output (empty stub / leaked CoT log / tool dump / bare
refusal) that a numeric grounding gate misses — every fact grounds, yet it is
not an answer. PURE, advisory, no-plan. Judges SHAPE not correctness; the markers
are policy (a generic default + host overlays), the abstain floor is INDETERMINATE.

### § reward (docs/230/234) — the lab on-ramp: may a fine-tune TRAIN on this

*(in `build_parser`)*

reward (docs/230/234) — the lab on-ramp: may a fine-tune TRAIN on this
trajectory? The witness-gated reward-set admission filter (ACCEPT / REJECT_POISON
/ ABSTAIN / NO_CLAIM). The non-distillable label — only a non-forgeable witness
moves the accept bit; --forgeable demos the floor (a self-report is ignored).
PURE, no-plan. The verdict IS the exit code so a dataset-build loop can branch.

### § resume (docs/107) — the third ARIES phase. Replay a run's intent ledger,

*(in `build_parser`)*

resume (docs/107) — the third ARIES phase. Replay a run's intent ledger,
re-verify its progress against ancestry, and PROPOSE the continuation (the
residual + the non-forgeable re-entry SHA). ADVISORY — it prints, it never
executes (there is no `dos resume` that runs the work; §8 non-goal). The
verdict IS the exit code (RESUMABLE/COMPLETE=0, DIVERGED=3, UNRESUMABLE=4).

### § rewind (docs/164 F1.5) — resume's CONVERSATION-axis sibling. Replays the run's

*(in `build_parser`)*

rewind (docs/164 F1.5) — resume's CONVERSATION-axis sibling. Replays the run's
ledger for the minted (turn_index, transcript_digest) checkpoint, reads the
transcript turns, and PROPOSES a truncation back to the anchor + a byte-clean
no-good note — never truncates (the host owns the transcript). The verdict IS the
exit code (REWIND/NO_REWIND=0, UNANCHORED=3).

### § complete (docs/117) — the live completion verdict, the forward dual of resume.

*(in `build_parser`)*

complete (docs/117) — the live completion verdict, the forward dual of resume.
"Is the WHOLE declared job verifiably done *now*?" — residual = declared −
verified, asked forward (may-I-stop) not backward (where-do-I-re-enter).
READ-ONLY + advisory: it answers, it records nothing and stops no loop. The
verdict IS the exit code (COMPLETE=0, INCOMPLETE=3, INDETERMINATE=4).

### § SUP (docs/99) — `dos loop`: the supervisor. Count the held lane leases

*(in `build_parser`)*

SUP (docs/99) — `dos loop`: the supervisor. Count the held lane leases
against --target and EMIT a per-tick spawn/reap/flag plan (init / PID-1 for a
fleet of dispatch-loops). Emit-only — it prints the worker launch command
lines, it never Popens a worker or writes the journal (the driver does). The
pure verdict is `dos.supervise.supervise`; this boundary gathers its evidence.

### § scope-gate (docs/102 §5) — the BINDING pre-effect scope gate. Asks the same

*(in `build_parser`)*

scope-gate (docs/102 §5) — the BINDING pre-effect scope gate. Asks the same
containment question as `dos scope` (the post-hoc footprint verdict) but
BEFORE the write, returning ALLOW/REFUSE so an edit-time hook / a commit
broker can refuse an out-of-tree patch instead of detecting the clobber after
it lands. The decision IS the exit code (0 allow / 5 creep / 6 wrong-target).

### § intervention-eval — score an actuation POLICY by net task delta (docs/143 §13.2).

*(in `build_parser`)*

intervention-eval — score an actuation POLICY by net task delta (docs/143 §13.2).
The actuation twin of overlap-eval: the live -9pp proved a SOUND verdict can be
NET-HARMFUL if the intervention is too disruptive, so this scores "did acting help?"
not "was the verdict right?" (arg-provenance's eval answers that). Exit 1 if the
policy is a net regression (net_task_delta < 0) — a disruptive policy fails CI.

### § docs/227 (G1 from docs/189) — the config-integrity linter as a focused verb:

*(in `build_parser`)*

docs/227 (G1 from docs/189) — the config-integrity linter as a focused verb:
finds DEAD POLICY in the lane taxonomy + reason registry (a lane that can't be
arbitrated, a contradiction, a dangling reference, a SHADOWED lane, an
order-sensitive roster, a dead doc cross-ref). The lane+reason rail of
`doctor --check`, on its own so CI can gate a dos.toml change on it.
