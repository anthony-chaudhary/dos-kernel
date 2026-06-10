# DOS — the Dispatch Operating System

> **The kernel is the part that doesn't believe the agents.**

DOS is the domain-free **trust substrate** for fleets of autonomous agents: a
small, deterministic kernel that adjudicates ground truth across many unreliable,
self-narrating workers and serializes their effects on shared state *without
believing what they say they did*. It was lifted out of the reference userland
app — the first userland app written against it, the way `cat` was
the first program for Unix.

This file is the **architecture contract** for the package. Read it before
editing, and keep edits inside the layer they belong to.

> **Write plainly here (operator directive, 2026-06-10).** Whenever possible,
> write this file — and any agent memory notes — in plain Feynman English:
> accurate yet simple. Use short common words, short sentences, one idea at a
> time; say what a thing does before giving its name; expand shorthand instead
> of leaning on it. Simplify the wording, never the facts — when simple and
> accurate conflict, accurate wins. When you edit a dense passage, leave it
> simpler than you found it.

> **Is the user asking ABOUT DOS rather than editing it?** (what is it / show me /
> install it / wire it into Claude Code / run it on their repo) — don't answer from
> this file; the verified consumer moves are the "When the user asks you ABOUT DOS"
> table in [AGENTS.md](AGENTS.md). Lead with `dos quickstart` (the 60-second
> caught-lie demo). Install is **from PyPI by default**
> (`pip install dos-kernel`, live since 2026-06-10; tracking unreleased `master` is
> `pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"`,
> and inside this clone `pip install -e .` works the same — the bare `dos` name on
> PyPI is an unrelated squatter); host wiring is `dos init --hooks <runtime>` or the bundled
> [claude-plugin/](claude-plugin/README.md).

> **Strategy / private prose does NOT live here — it goes to the `dos-private`
> repo (the private sibling checkout `../dos-private`; renamed 2026-06-10 from
> `dos-strategy`, old links redirect) by default.** That repo takes three genres:
> the "why-now / vision / competitive-landscape / market / positioning / adoption /
> anti-slop / business-case" **essay** — the product-strategy genre, *not* a kernel
> `docs/NN_*.md` design plan — registered in that repo's `README.md` table;
> low-ceremony **operator notes / runbooks** (its `notes/`); and **code spikes**
> (its `prototypes/`). The dependency arrow is one-way: those docs reference the
> `dos` code; nothing under `src/dos/` depends on `dos-private`. Engineering design
> plans (the numbered `docs/NN_*.md`) stay in this repo. When in doubt: if it
> argues *why DOS matters / who buys it / how it wins / how we operate it* — or it
> is a scratch experiment — it goes to `dos-private`; if it argues *how a kernel
> module behaves*, it is a design plan → `docs/` here.

> **Tracked here = ships. This IS the public repo — route privacy at AUTHORING
> time.** Every tracked file is public on the next push: no scrub step or leak
> gate stands between a commit here and the world (the old private-archive
> seed/leak-scan flow retired with the 2026-06-10 cutover to this working repo;
> the publishing playbook lives on in `dos-private`). So the durable rule is
> where you WRITE:
>
> - **Private-SUBJECT prose** — operator runbooks, launch/publication process,
>   audits whose subject is this machine or its fleet — is born in `dos-private`
>   (`notes/` is the low-ceremony tier), never here. A `docs/reports/*.md`
>   authored here must read as a public document about the public code.
> - **Never write a dev-machine absolute path, hostname, or personal identifier**
>   into a tracked file — including JSON-escaped forms inside logs/fixtures (leaks
>   shipped at escaping depth 4). Write paths sibling-relative; synthetic fixture
>   paths use neutral roots.
> - **Cross-link, don't duplicate**: a kernel doc with strategy implications names
>   the `dos-private` doc by filename; the private doc registers in that repo's
>   `README.md` table (essays — notes and prototypes skip the table) and links
>   back by `docs/NN` name. The dependency arrow stays one-way.

## The layering — keep these apart

The whole design is one rule: **mechanism is the kernel; policy is a driver;
the phased-plan workflow is host concern, not kernel.** Four layers, strictly
one-directional (each may import the layer above it, never below):

| Layer | What it is | Where | May import |
|---|---|---|---|
| **1. Kernel** (mechanism) | The syscalls, pure: `verify()`, `refuse()`, `arbitrate()`, `liveness()`, `resume`, `spawn/reap`. Closed verdict vocabulary, ship oracle, structured-refusal enum, lease arbiter, liveness verdict, resume/intent-ledger, correlation spine. Every verdict is a PURE `classify(evidence, policy)`; I/O is gathered at the CLI boundary, never inside the verdict. **No host names, no plan schema, no I/O policy.** Per-module detail (the `docs/NN` lineage + the byte-clean argument for each leaf) lives in the cold tier, **`docs/ARCHITECTURE.md`** — read it before editing a leaf. | `src/dos/*.py` — every module not claimed by rows 2a/2b (`config.py`, `reasons.py`, `stamp.py`), row 3's shells, or `drivers/` (row 4). **The roster is the directory listing — deliberately NOT enumerated here: a hand-kept list rots** (two audits, 2026-06-08 and 2026-06-10, found this cell naming ~45 of ~150 shipped modules). Orientation by cohesion cluster (a sample, not a roster): the temporal-verdict family (`liveness`/`tool_stream`/`productivity`/`efficiency`/`noop_streak` + its `marker_sensor` boundary I/O); the recovery family (`resume`/`intent_ledger`/`durable_schema`); the picker substrate (`enumerate`/`pickable`/`cooldown`/`reconcile`); the seam-protocol families (`judges`/`overlap_policy`/`notify`/`hook_dialect`); the witness family (`evidence`/`effect_witness`/`reward`, docs/230/234); the docs/189 CC-audit lifts (`breaker`/`exec_capability`/`hook_exit`/`config_lint`). `docs/ARCHITECTURE.md` carries the per-module `docs/NN` lineage for the leaves it covers but LAGS the newest modules — a newer leaf's lineage is the `docs/NN_*.md` plan its own module docstring names. | stdlib, `dos.config`, the seam-data modules (2b), and **sibling kernel modules** — the kernel is internally cohesive (the import edges are listed in `docs/ARCHITECTURE.md`). The enforced line is the next column's litmus — **no host, no I/O policy** — NOT "no sibling import." |
| **2a. Seam** (config) | `SubstrateConfig` — the single injected boundary. Workspace root (`.root`) + the *policy hooks* (lane taxonomy, path layout, **refusal vocabulary, ship-stamp grammar**) the kernel reads instead of hardcoding `REPO_ROOT`, **plus the discovered `WorkspaceFacts` (`.workspace`)**: facts gathered ONCE via build-time I/O (chiefly *which of the kernel's own runtime files exist under this root* → `is_kernel_repo`), cached as data so a **pure** verdict (`arbitrate`) can be workspace-aware without re-probing the disk — the "I/O at the boundary, data to the pure core" rule (cf. `git_delta`/`journal_delta` → `liveness.classify`), lifted to the config seam so the *workspace is a first-class object with discovered properties*, not a bare path. Ships a **generic** `main`/`global` default. | `src/dos/config.py` | stdlib + the seam-data modules (2b) |
| **2b. Seam data** (closed-sets-as-data) | The hackability registries the seam carries *as values*: the block-reason vocabulary (`ReasonSpec`/`ReasonRegistry`/`BASE_REASONS`) and the ship-stamp convention (`StampConvention`/`JOB_`/`GENERIC_STAMP_CONVENTION`). Pure stdlib leaves; declared per-workspace in `dos.toml`. This is the "closed enum → declared data" pattern (see `docs/HACKING.md`). | `src/dos/{reasons,stamp}.py` | stdlib only |
| **3. Helpers** | Thin shells over the kernel that carry **no policy of their own**: the umbrella CLI, the file-tree disjointness algebra, timeline assembly, the operator-decision queue + its TUI (the *what-needs-me* projection), and the live fleet-watchdog `dos top` + its TUI (the *what's-running-now* projection, behind the `[tui]` extra). Both projections are read-only — they read kernel state only (`decisions` over the four refusal sources; `dispatch_top` over `lane_journal.replay` + `liveness.classify` + the verdict envelopes + `git_delta`), acquire no lease, launch nothing, mutate no substrate. The `dos notify {decisions,top}` verb pipes either projection through the `notify` seam to a by-name transport (default `null` = render only); host-cadence-free (a fleet drives it with `/loop`/cron — no daemon in the kernel). | `src/dos/{cli,_tree,timeline}.py` + the read-only projection pairs (`decisions`/`dispatch_top`/`plan_board` and their `*_tui` shells) — same rule as row 1: the disk listing is the roster, this is orientation | layers 1–2 |
| **4. Drivers** (policy + adjudicators) | The layer outside the kernel boundary, in **three kinds**: (a) a *host repo's policy pack* — which lanes exist, how they admit concurrency, where its plans/ship-state live (`job.py`); (b) *out-of-kernel adjudicators* — the **JUDGE rung** (ORACLE → JUDGE → HUMAN), a non-deterministic adjudicator ruling on the residue the oracle ABSTAINED on (`llm_judge.py`), hedged by four disciplines: deterministic-first, advisory-only, fail-to-abstain, abstention-first (`docs/86_*`, seam `dos.judges`); and (c) *out-of-kernel transports* — the notification spine's delivery side, where a `Notifier` names a vendor as code (`notify_slack.py`, behind the `[notify-slack]` extra, registered via `dos.notifiers`). Each has the surface the kernel forbids (provider/I/O/non-determinism/network). Adding a host or adjudicator or transport = a module here (or a `dos.judges`/`dos.predicates`/`dos.renderers`/`dos.notifiers` plugin), **never touching the kernel.** | `src/dos/drivers/<host>.py` (e.g. `job.py`; `llm_judge.py:LlmJudge` the `dos.judges` occupant; `notify_slack.py:SlackNotifier` the `dos.notifiers` occupant) | layers 1–2 |

**Four things live OUTSIDE the four layers — each operates *on* the package, never
*inside* it (the dependency arrow is one-way: they `import dos` / call the `dos`
CLI; nothing under `src/dos/` imports them).** Treat each as its own workstream — an
edit to one is never an edit to the substrate:

- **Release & dev tooling** — the release scripts (`scripts/release_*.py`) and the
  `/release` + `/stable-release` skills (`.claude/skills/`) cut/gate versions. Not
  shipped. (Litmus: "no `scripts/` in the kernel," below.)
- **The MCP server** (`src/dos_mcp/`, `docs/80_*`) — a `FastMCP` server exposing the
  syscalls as MCP tools (JSON over stdio, zero Python coupling), the lowest-friction
  **adoption surface**. *Is* shipped, but as a **separate top-level package**
  (deliberately `dos_mcp`, not `dos.mcp` — folding it under `dos` would force a
  server framework into the near-stdlib kernel); the `mcp` dependency lives only in
  the `[mcp]` extra, so the kernel's own dependency set stays PyYAML-only. (Litmus:
  "kernel never imports the MCP server," below.)
- **The generic skill pack** (SKP, `docs/74_*`) — the domain-free `SKILL.md`
  screenplays under `src/dos/skills/` (one per reference workflow; the directory
  listing is the roster), shipped as package-DATA (not code — nothing
  imports them; they shell only `dos` verbs). The Axis-5 hackability surface: the
  *shape* "snapshot → `verify` → render → `gate` → take a lane → archive" is
  mechanism, every host specific is config data. (Litmus: "a shipped generic skill
  names no host," below.)
- **Phased-plan concepts** — `execution-state.yaml`, soft-claims, plan-meta
  frontmatter, a host's tuned `next-up`/`dispatch`/`replan` skills are the **host's
  workflow** (they live in the reference userland app). The kernel treats the plan
  registry as an *optional* `source`: `verify()` in a repo with no plan answers from
  git history alone (`source="none"`). **Do not couple the kernel to the phased-plan
  layer.** (Litmus: "`verify` needs no plan," below.)

### The litmus tests (each is enforced by a test or is trivially checkable)

- **Kernel imports no host.** No module under `src/dos/` (except `drivers/`) may
  name `job`, `apply`, `tailor`, or any host-specific lane. The generic default
  in `config.py` is `main`/`global`.
- **A driver is the only place policy lives.** `JOB_LANE_TAXONOMY` and
  `job_config` live in `dos.drivers.job`, re-exported from `dos.config` /
  `dos` only for backward compatibility. New host policy → a new `drivers/`
  module, not an edit to `config.py`.
- **`verify` needs no plan.** `tests/test_verify_no_plan.py` proves the truth
  syscall runs against a plain git repo with no `docs/*-plan.md` and no registry.
- **The package never assumes it lives in the repo it serves.** Every path
  resolves against `SubstrateConfig.root` (the workspace root: explicit arg ›
  `DISPATCH_WORKSPACE` › cwd, via `resolve_workspace_root`), never `__file__`.
  (`SubstrateConfig.workspace` is the distinct *facts* field — see the seam row.)
- **The kernel never imports its own tooling.** No module under `src/dos/` may
  import from `scripts/`. The release scripts and `.claude/skills/` consume the
  package (they `import dos` / call the `dos` CLI); the package is unaware they
  exist. Grep-checkable: `import .*scripts` / `from scripts` must not appear under
  `src/dos/`. (Release scripts *do* anchor on `git rev-parse --show-toplevel`, not
  `__file__` — they ship with the repo they release, so the git top-level is the
  honest root.)
- **The kernel never imports the MCP server.** No module under `src/dos/` may
  import `dos_mcp`. The server consumes the package (it `import dos`); the kernel
  is unaware it exists. Grep-checkable: `import dos_mcp` / `from dos_mcp` must not
  appear under `src/dos/`. `dos_mcp` resolves its served workspace via the same
  `SubstrateConfig` seam as everything else (explicit `workspace` arg ›
  `DISPATCH_WORKSPACE` › cwd), never `__file__`, and passes the built config
  EXPLICITLY into each syscall (`oracle.is_shipped(cfg=…)`,
  `arbiter.arbitrate(config=…)`) rather than mutating process-global active
  state — correct for a long-lived server fielding concurrent workspaces. Pinned
  by `tests/test_mcp_server.py`.
- **The kernel never imports a judge implementation.** The JUDGE rung is the one
  place a non-deterministic, provider-backed adjudicator is allowed — and it lives
  in a **driver**, never the kernel. The `dos.judges` seam (kernel) holds only the
  pure `Judge` protocol + `JudgeVerdict` + `run_judge` + resolver + the built-in
  `AbstainJudge`; every *ruling* judge (`drivers/llm_judge:LlmJudge`, any
  `dos.judges` plugin) is discovered by name at the call boundary, never imported by
  a kernel module. Grep-checkable: no module under `src/dos/` (except `drivers/`)
  may `import dos.drivers` / `from dos.drivers`. The discipline that makes an open
  adjudicator set safe is four-fold — deterministic-first, advisory-only,
  fail-to-abstain (`run_judge` converts any raise/bad-return to `ABSTAIN`, never
  `AGREE`), abstention-first — and is the judge analogue of the predicate
  conjunctive-only rule. Pinned by `tests/test_judges.py` (+ `docs/86_*`).
- **A swappable overlap scorer can only refuse-MORE, never admit a collision.**
  The disjointness scorer is now pluggable (Axis 7, `dos.overlap_policies`,
  docs/113) — and because a policy returns a verdict that *includes admit*
  (unlike a predicate, which can only refuse), the safe direction is guaranteed
  STRUCTURALLY by a deterministic floor: `overlap_policy.admissible_under_floor`
  AND-s any policy under the unforgeable prefix-disjointness verdict
  (`admit ⟺ floor.admissible AND policy.admissible`). So a buggy/hostile/raising
  policy cannot admit a path-colliding pair — it degrades to the prefix floor
  (today's behavior), never looser. The default `prefix` policy under the floor
  reproduces `lane_overlap.overlap_verdict` byte-for-byte (the whole existing
  arbiter/overlap suite stays green). A model-backed scorer lives in a **driver**
  (the `dos.drivers` litmus above covers it); the kernel seam imports no ruling
  policy. Pinned by `tests/test_overlap_policy.py` (incl. an arbiter-level proof
  that a lying-admit policy cannot double-book a held lane through `arbitrate`).
- **The kernel names no vendor in code; a host dialect is a driver.** The hook
  output a runtime parses is vendor-shaped (Claude Code's `hookSpecificOutput`,
  Gemini's top-level `decision`, Cursor's `permission`, docs/217). The kernel seam
  `dos.hook_dialect` holds only the dialect-NEUTRAL `HookVerdict` + `parse_cc` + the
  `HookDialect` Protocol + the by-name `resolve_dialect` + the ONE unshadowable
  built-in `ClaudeCodeDialect` (the default — byte-for-byte what the sensors already
  emit, the `AbstainJudge` analogue). Every OTHER renderer (`CodexDialect` /
  `GeminiDialect` / `CursorDialect`) names its vendor as code, so it lives in a
  **driver** (`dos.drivers.hook_dialects`) and registers through the
  `dos.hook_dialects` entry-point group — the same kernel/driver split as
  `judges`/`llm_judge` and `overlap_policy`. Grep-checkable + AST-pinned: no
  non-driver kernel module may name a vendor as a code identifier (the sole
  allowance is the `claude-code` default in `hook_dialect.py`), so no kernel
  *adjudication* can branch on which vendor is acting — a dialect is OUTPUT chosen by
  `--dialect`, strictly downstream of an already-decided verdict. Pinned by
  `tests/test_vendor_agnostic_kernel.py` + `tests/test_hook_dialect.py`.
- **A shipped generic skill names no host.** No `SKILL.md` under
  `src/dos/skills/` may name a host directory (`docs/_plans`, `output/next-up`), a
  host lane (`apply`/`tailor`/`discovery`), or a host commit prefix (`docs/dispatch:`)
  — every host specific comes from `dos doctor --json` / `dos.toml`. The skill
  analogue of "kernel imports no host," and like that rule it is grep-checkable +
  pinned (`tests/test_skill_pack_*.py`). The skills are package-DATA, not code:
  `src/dos/skills/` has no `__init__.py` and nothing under `src/dos/*.py` imports
  it.

## The syscall ABI

| Syscall | Module | What it is |
|---|---|---|
| `verify()` | `dos.oracle`, `dos.phase_shipped` (grammar: `dos.stamp`) | the **truth syscall** — "did (plan,phase) actually ship?", registry-first, ancestry-checked, never from self-report. Works with **no** plan present. The grep rung's ship-subject grammar is `SubstrateConfig.stamp` (a `dos.stamp.StampConvention`), declarable per-workspace in `dos.toml` `[stamp]` — strict host-grammar by default, generic by opt-in. |
| `liveness()` | `dos.liveness` (evidence readers: `dos.git_delta`, `dos.journal_delta`) | the **temporal verdict** (docs/82) — "is the run *moving* (ADVANCING) or just spinning (SPINNING/STALLED)?", from the git/journal delta, never the agent's "making progress" self-report. `verify`'s in-flight sibling; works with no plan present. Phase 2 (the journal/heartbeat rung) shipped; loop-self-stop (P3) is not. Advisory. *(Detail: `docs/ARCHITECTURE.md`.)* |
| `productivity()` | `dos.productivity` | the **loop-economics verdict** (docs/218) — "is the run still *doing work*, or fading?" `liveness`'s lateral sibling re-aimed onto a **trend**: `classify(WorkHistory, policy) -> ProductivityVerdict` (PRODUCTIVE/DIMINISHING/STALLED) over per-step work deltas. The cleanest mechanism/policy split in the kernel (host names the work-unit + thresholds; kernel only compares magnitudes); timeless — makes **no I/O at all**. Advisory. CLI: `dos productivity --deltas …` (PRODUCTIVE 0 / DIMINISHING 3 / STALLED 4). |
| `efficiency()` | `dos.efficiency` | the **token-effectiveness verdict** (docs/263) — "did the tokens this run spent *buy work*?" `productivity`'s lateral sibling re-aimed from a trend onto a **ratio**: `classify(EfficiencyEvidence, policy) -> EfficiencyVerdict` (EFFICIENT/COSTLY/WASTEFUL) over `work / tokens`. Relates the work to its *price* — the question an operator means by "token effectiveness" (a run can be PRODUCTIVE yet burn 10× the tokens its work was worth). **Non-forgeable by construction**: both counts are env-authored (the work git/the test-runner witnessed; the tokens the provider billed), so a run cannot narrate its way to EFFICIENT (the docs/138 invariant). WASTEFUL (0 work, meaningful spend) is unit-independent and always-free; COSTLY is opt-in behind a host-armed `floor` (default 0.0 = disabled, so a unit mismatch never manufactures a false COSTLY). Timeless — makes **no I/O at all**. Advisory. CLI: `dos efficiency --work W --tokens N [--floor R]` (EFFICIENT 0 / COSTLY 3 / WASTEFUL 4). |
| `improve()` | `dos.improve` (composes `dos.efficiency` + `dos.breaker`; engine: `dos.drivers.self_improve`) | the **self-improving-loop keep-gate** (docs/280) — "may a self-improving work loop *keep* this candidate change?" The kernel leaf of the first recursive-self-improvement loop for DOS: `reward.admit` ([[234]]) re-aimed from a training-set admission to a commit-KEEP admission. `classify(CandidateEvidence, policy) -> KEEP / REVERT / ESCALATE` over four **env-authored** facts (the suite's exit on the candidate-only tree, the truth syscall's cleanliness, the metric before/after) + the carried breaker count. KEEP iff suite green AND truth clean AND a STRICT env-measured metric gain AND not WASTEFUL; a regression (red suite / dirty truth) is the non-negotiable conjunctive floor → REVERT; a safe no-op → REVERT; N non-keeps in a row → ESCALATE to a human (the RSI "human-judgment bottleneck" as a kernel rule). **Non-forgeable** (docs/138/234 at loop scale): the keep-bit reads zero loop-authored bytes — a `narrated` string is carried for the operator and parsed for nothing, so a loop *cannot write its way into the kept set*; the only path to KEEP is to actually move the metric. PURE, no I/O, names no host (the metric + the proposer are the host's, injected into the `self_improve` engine which does the worktree-isolated propose→gather→classify→actuate). Advisory. CLI: `dos improve --suite-passed --truth-clean --work W --baseline-work B [--max-reverts N]` (KEEP 0 / REVERT 3 / ESCALATE 4). |
| `breaker()` | `dos.breaker` | the **circuit-breaker primitive** (docs/223) — "this failure class keeps tripping; stop, and escalate the rung." A PURE two-counter state machine (`record_failure`/`record_success`/`classify -> CLOSED/OPEN`); trips on `consecutive` (sustained) OR `total` (flapping). An OPEN verdict names an `Escalation` rung (NONE/JUDGE/HUMAN). Advisory. CLI: `dos breaker --consecutive N --max-consecutive M` (CLOSED 0 / OPEN 3). |
| `exec_capability()` | `dos.exec_capability` | the **arbitrary-exec capability classifier** (docs/223b) — "does this command grant arbitrary code execution?" `classify_command -> GRANTS_ARBITRARY_EXEC/BOUNDED/EMPTY`, matching the INVOKED PROGRAM token against a closed set, NEVER a substring (`cat python.txt` is BOUNDED). A classifier leaf the `pretool_sensor` PEP consults, not an arbiter predicate. ADVISORY (BOUNDED ≠ a safety guarantee). CLI: `dos exec-capability --command "…"` (BOUNDED/EMPTY 0 / GRANTS_ARBITRARY_EXEC 3). |
| `hook_exit()` | `dos.hook_exit` | the **shell-hook exit-code classifier** (docs/226) — "a plain script exited N; which intervention is that?" `classify_exit -> OBSERVE/WARN/BLOCK/DEFER` (0 = proceed, 2 = BLOCK, other non-zero = WARN). The cheapest integration surface; fail-safe (unknown non-zero → WARN). Advisory. CLI: `dos hook-exit --code N` (PASS 0 / BLOCK 3 / WARN 4 / DEFER 5 / OBSERVE 6). |
| `resume` | `dos.resume`, `dos.intent_ledger` (durability: `dos.durable_schema`; evidence reader: `dos.resume_evidence`) | the **third ARIES phase** (docs/107) — "a run died/paused mid-flight; how far did the *fossils* say it got, and what is the residual?" `liveness`'s FORWARD sibling. `resume_plan -> RESUMABLE/COMPLETE/DIVERGED/UNRESUMABLE` over a `run_id`-keyed intent ledger; MINTS the re-entry SHA off the non-forgeable rung, PROPOSES (never executes) the re-dispatch. Phases 1–5 shipped; bench (P6) future. *(Detail: `docs/ARCHITECTURE.md`.)* |
| `reward()` | `dos.reward` (join: `dos.effect_witness` → `dos.evidence`) | the **reward-set admission verdict** (docs/230/234) — "may a fine-tune *train* on this trajectory?" The on-ramp that puts the deterministic floor *inside a training loop*: `admit(claim_present, readbacks) -> ACCEPT / REJECT_POISON / ABSTAIN / NO_CLAIM`. `effect_witness`'s lab-facing consumer — a self-judged sampler banks every "resolved" claim as a positive (training the policy to over-claim more); this purges the poison a non-forgeable witness REFUTES (the dispreferred DPO member). The **non-distillable label**: the accept bit is a pure function of the witness the agent authors zero bytes of, so no answer text can move it reject→accept (inherits `believe_under_floor`; a forgeable read-back is structurally ignored). PURE, no I/O, names no host (the claim extractor + witness are the host's). Advisory. CLI: `dos reward --claim --witness {confirm,refute,none} [--forgeable]` (ACCEPT 0 / REJECT_POISON 3 / ABSTAIN 4 / NO_CLAIM 5). |
| `refuse(reason_class)` | `dos.wedge_reason`, `dos.picker_oracle` (vocabulary: `dos.reasons`) | **structured refusal** — a closed reason vocabulary, simultaneously emittable, verifiable, refusable. The reason set is `SubstrateConfig.reasons` (a `dos.reasons.ReasonRegistry`, base `BASE_REASONS`), declarable per-workspace in `dos.toml` `[reasons]`. |
| `lease()` / `arbitrate()` | `dos.arbiter` | the **pure admission kernel** — `arbitrate(request, live_leases, config) -> decision`, state-in / decision-out, no I/O. |
| `spawn()` / `reap()` | `dos.run_id`, `dos.lane_journal` | the **correlation spine** (sortable, lineage-carrying run-ids) + the lease **write-ahead log**. |
| `pickable()` / `enumerate()` / `cooldown()` / `reconcile()` | `dos.pickable`, `dos.enumerate`, `dos.cooldown`, `dos.reconcile` (grammar: `dos.toml` `[enumerate]`/`[cooldown]`/`[lifecycle]`) | the **picker substrate** (docs/168 + docs/207) — the producers/gates that decide *is there anything pickable, why-not, have I tried it, and did the claim hold?* `enumerate` produces the `declared` phase set; `pickable` is the pre-dispatch gate (OFFERABLE / HELD); `cooldown` is the anti-churn fold over `OP_ATTEMPT` (CLEAR / RECENTLY_ATTEMPTED — the cross-run memory breaking the re-pick storm); `reconcile` is the quiet-completion join (VERIFIED / QUIET_INCOMPLETE / HONEST_OPEN, fail-closed on the claim). All PURE; reads at the CLI boundary. CLI: `dos pickable`/`enumerate`/`cooldown`/`reconcile`. *(Detail: `docs/ARCHITECTURE.md`.)* |
| `notify()` | `dos.notify` (transport: a `dos.notifiers` driver, e.g. `dos.drivers.notify_slack`) | the **notification spine** (docs/225) — "push *what needs a human* / *what's running* to where the operator is (Slack first)." NOT a verdict: the FOURTH pure-protocol + by-name-resolver seam, on the DELIVERY side. Two PURE adapters turn the `decisions`/`dispatch_top` projections into one transport-agnostic `Notification`; `send_safely` delivers FAIL-SOFT (a transport raise → a non-delivered result, never a crashed producer); the built-in `null` sink is the safe default. Advisory (docs/99): reads a projection → push; takes no lease, stops no run. CLI: `dos notify {decisions,top} [--notifier slack --channel NAME] [--dry-run] [--json]`. *(Detail: `docs/ARCHITECTURE.md`.)* |
| `lint()` | `dos.config_lint` (algebra: `dos._tree`) | the **config-integrity linter** (docs/227, G1 from docs/189) — "is there *dead policy* in this workspace's own declarations?" A PURE `lint(LaneTaxonomy, ReasonRegistry) -> tuple[Finding, ...]`, the `detectUnreachableRules` analogue. Finds a treeless lane, a concurrent∩exclusive contradiction, a dangling autopick/alias/`see_also` target, an order-sensitive roster, and the real unreachable-rule case — a concurrent lane whose region is a strict subset of another's (`LANE_REGION_SHADOWED`). SHADOW (subset → remove) vs OVERLAP (intersection → disjoin) is the load-bearing split. Typed `Finding` (closed `LintKind` + `Severity`). Advisory. CLI: `dos lint [--strict] [--json]` (0 clean / 1 error-or-warn), folded into `dos doctor --check`. *(Detail: `docs/ARCHITECTURE.md`.)* |

## Install & test

```bash
pip install -e ".[dev,mcp]" # editable + the test toolchain (a bare `-e .` is PyYAML-only — no pytest)
python -m pytest -q         # the full kernel suite (must stay green; ~4,100 tests, ~3–4 min — foreground, wait for the verdict)
dos doctor --workspace .    # report the active workspace + lane taxonomy
dos verify --workspace . PLAN PHASE   # the truth syscall (no plan needed)
```

## DOS on DOS — dogfood the kernel here

**This repo IS a DOS workspace, so adjudicate your work on it with the kernel
itself.** A `dos.toml` sits at the root and `dos doctor` reports
`workspace_facts.is_kernel_repo: true` (`kernel_runtime_files_present: 11`) —
DOS knows it is editing its own source tree. Don't take the kernel's behavior on
faith from this contract; **run the syscalls against this repo and read the
verdict.** The four moves below are the working ritual — they are the same
`arbitrate → edit → verify` loop a host's dispatch-loop runs, performed by hand:

```bash
# 1. doctor — what IS this workspace? (the seam, made visible)
dos doctor --workspace .
#   stamp convention   generic (any/no dir prefix)  [style=grep]
#   concurrent lanes   benchmark, claude-plugin, docs, examples, go, paper, scripts, spikes, src, tests, verify-action
#   exclusive lanes    global
#   is git workspace   yes      (workspace_facts.is_kernel_repo: true)

# 2. arbitrate — may I take this lane right now? (the admission kernel)
dos arbitrate --workspace . --lane docs
#   {"outcome": "acquire", "auto_picked": false, "lane": "docs",
#    "reason": "cluster lane 'docs' free — admitted.", "tree": ["docs/**"]}
dos arbitrate --workspace . --lane src
#   {"outcome": "acquire", "auto_picked": true, "lane": "benchmark", ...,
#    "reason": "auto-picked free cluster lane 'benchmark' (requested 'src' was
#               refused: lane 'src' would edit the orchestrator's own running
#               code (src/dos/arbiter.py…) — … (SELF_MODIFY) …)."}
# Two verdicts, one discipline. A free, admissible lane you NAME is granted
# directly — a kindless `--lane` is a soft hint the arbiter honors. `src` is
# different ON THIS REPO: its tree IS the kernel's own running code, so the
# SELF_MODIFY predicate refuses the hint and the arbiter redirects to a free
# disjoint lane, naming the REAL refusal in the parenthetical — never
# double-booking, and never narrating a false "busy" for a lane nothing held.
# A lane actually HELD by a live lease in the WAL refuses same-lane instead
# ("already held"). The lane taxonomy in dos.toml mirrors this repo's
# top-level dirs, so a docs-only edit may run concurrently with a tests edit;
# editing `**/*` is the exclusive `global` lane.

# 3. verify — did a phase ACTUALLY ship? (the truth syscall, never self-report)
dos verify --workspace . docs/292_readme-audience-gradient-plan P1
#   SHIPPED docs/292_readme-audience-gradient-plan P1 85a3bad (via grep-subject)
dos verify --workspace . docs/99_runtime-validation-and-the-actuation-boundary halt
#   NOT_SHIPPED ... (via none)   ← still in flight; git ancestry has not stamped it
# `source=grep`/`none` is the rung that answered. The verdict comes from git
# ancestry + ship-stamp grammar, NOT from "I'm done" — that is the whole point.
# ⚠ The oracle's evidence base is exactly the VISIBLE ancestry: this public repo
# was seeded fresh 2026-06-10 (history starts at the v0.22.0 commit), so a phase
# stamped before the seed (e.g. docs/82 liveness) answers NOT_SHIPPED via `none`
# — a conservative abstain, not a bug. A history rewrite amputates the grep
# rung's evidence; accept the abstain or re-stamp, never teach the oracle to
# believe a `> **Status:**` sentence instead.

# 4. man — what refusals/lanes does THIS workspace know? (self-describing registry)
dos man wedge          # the closed reason vocabulary → resolver kind
dos man lane           # the lane taxonomy + file trees

# 5. plan — does the plan's CLAIM match the oracle's VERDICT? (check the plan
#          OUTSIDE the loop — the loop must not self-certify against its own plan)
dos plan --once --workspace .          # fan the oracle over every declared phase
dos plan --once docs/292_readme-audience-gradient-plan P1  # or an explicit (plan,phase), no doc needed
#   each row pairs the plan's self-reported status with `oracle.is_shipped` — the
#   headline cell is the OVER-CLAIM (plan says SHIPPED, git says not). This is a
#   verify()-fan-out, NOT a plan reader: a human or supervisor runs it from
#   outside the agent loop, so an over-claiming loop is caught by ground truth, not
#   by re-reading its own narration. Read-only — stores nothing, takes no lease.
#
#   ⚠ ON THIS REPO the board is noisy in one specific, documented way. Since
#   docs/293 the workspace declares its plan dialect (`dos.toml [plan]`), so the
#   harvester DOES parse DOS's own prose plans — the old "(no plans declared)"
#   empty case is gone. But read the ⚠over-claim rows carefully: every plan
#   numbered at or below docs/184 was stamped BEFORE the 2026-06-10 fresh seed,
#   so its ship-stamps live in the amputated ancestry and the oracle answers
#   NOT_SHIPPED via `none` — the step-3 ⚠, now rendered at board scale. An
#   honest "shipped" claim meeting an amputated witness reads as ⚠over-claim:
#   that is "evidence horizon", not "caught lying". Accept the abstain or
#   re-stamp; never teach the oracle to believe the `> **Status:**` sentence. A
#   post-seed plan adjudicates cleanly (docs/293 itself reads ✓shipped off its
#   trailer stamps). Day to day this repo's claims live in COMMIT SUBJECTS, so
#   its working honesty witness is still step 6.

# 6. commit-audit — does each commit's SUBJECT claim match its own DIFF? (the
#          out-of-loop honesty witness THIS repo actually has — author-neutral,
#          plan-free; the docs/228 gate aimed at git instead of a tau2 DB-hash)
dos commit-audit --workspace . HEAD              # one commit: claim vs its diff
dos commit-audit --sweep --workspace . origin/master..HEAD   # the DRIFT RATE over a range
#   The subject is FORGEABLE (whoever wrote the message authored it); the files the
#   commit touched + whether its SHA is a git ancestor are NOT (the commit machinery
#   authored them). That byte-author≠claimant split is the same one docs/228's gate
#   rides — there the witness was the env DB-hash the agent authors 0 bytes of; here
#   it is the DIFF the message-writer authors 0 bytes of. `--sweep` reports the
#   DRIFT RATE (unwitnessed/checkable) + a by-claim-kind grid — "how honest are this
#   repo's commit messages?" — a FLIP off ground truth (docs/179), not a re-read of
#   the narration. It fires CLAIM_UNWITNESSED only where a concrete code/test claim
#   and a contradicting diff coexist, ABSTAINs on the rest, and grades the KIND of
#   change, never CORRECTness (the Wall-3 line). Read-only; the exit code is the
#   verdict (0 clean / 1 a drift found). Run it from OUTSIDE the loop that wrote the
#   commits — a fresh session inheriting "docs/NN shipped" off a forgeable subject is
#   the peer-B handoff (docs/229) the gate protects.
```

**Use this loop when working in here, not just to demo it.** Before claiming a
`docs/NN_*.md` plan-phase is done, `dos verify` it — the contract above says the
oracle answers from git, so let the oracle, not your narration, close the phase.
And after committing, `dos commit-audit` the work (step 6) — the oracle witnesses
that a *phase* shipped; commit-audit witnesses that each *commit's subject* is
backed by its own diff. Two witnesses, both reading bytes the claimant did not
author (git ancestry; the diff), both run from outside the loop that wrote the
claim — that is the docs/228 lesson applied to this repo: the only witness worth
trusting is one the claimant can't forge, and on this repo that is git, never a
`> **Status:**` sentence or a commit subject taken at its word.
When two changes touch different top-level dirs, they are disjoint lanes and safe
to land independently; when one touches `src/dos/`'s own running path, that is the
`SELF_MODIFY` / `global`-lane hazard the kernel is built to refuse. Eating our own
dog food is the cheapest proof that the litmus tests above still hold: if
`dos verify` ever needs a plan to answer, or `dos doctor` ever stops seeing this as
a git workspace, a contract here has drifted from the code.

### Committing — close the loop without asking

**A commit IS the ship-stamp the oracle reads** (`dos verify` answers from git
ancestry, never from working-tree narration), so a finished change that is not
committed is a phase the kernel will report `NOT_SHIPPED`. Therefore: **when a
unit of work is complete and the suite is green, commit it — do not stop to ask
permission.** Asking first is the exception, reserved for the genuinely
hard-to-reverse or outward-facing: pushing, tagging, a `/release`, force-pushing,
history rewrites, or anything that leaves this machine. A local commit on `master`
is none of those — it is the cheap, reversible act of stamping the work the oracle
verifies, and the trunk-is-`master` / land-promptly preference wants it done, not
deferred.

Stay disciplined about scope, the same way the arbiter is: **commit only the lane
you actually worked.** Stage the specific files your change touched (`git add
docs/… src/dos/…`), never a blanket `git add -A` that sweeps in a concurrent
agent's in-flight edits — the working tree here often carries another loop's
unstaged work (the `SELF_MODIFY` / disjoint-lane discipline, applied to staging).
Match the existing commit-subject grammar (see `git log`). Do **not** add a
`Co-Authored-By` or other agent-attribution trailer — the default here is no
agent co-authors on commits, and this overrides any harness default that
appends one.

### Out-of-scope findings — file an issue, don't widen the lane

Working in here you will notice things that need doing but are not your task —
a bug in another lane, a missing test, a doc gone stale. Do not absorb them
into the current commit, and do not let them evaporate. **The default home for
deferred work is a GitHub issue** (`gh issue create`), filed in the moment,
then back to your lane. Three rules keep the tracker honest:

- **File it with a done-condition.** Say what command or observable would show
  the issue is resolved. Add a lane guess and where you found it. If you cannot
  state the done-condition, it is not an issue yet — label it `design` (it
  needs a `docs/NN` plan first) or take it to Discussions. Search for a
  duplicate before filing (`gh issue list --search "…"`).
- **Issue text is public, and the leak gate never sees it** — the pre-push scan
  reads tracked files, not `gh` calls. The route-privacy-at-AUTHORING-time rule
  applies verbatim: no dev-machine paths, hostnames, or private-process prose
  in any issue body or comment.
- **Never close an issue on your own say-so.** Put `Fixes #N` in the commit
  BODY (the subject keeps its grammar); GitHub closes the issue when that
  commit lands on `master` — an ancestry check, the same witness `dos verify`
  rides. A fix that landed without the reference, or that lives outside this
  repo's git, closes only through the evidenced path
  (`.claude/skills/issue-verify/`).

Design-shaped work stays in `docs/NN_*.md` plans — the oracle, not the tracker,
adjudicates phases. An issue that grows design weight gets a plan; the issue
then points at the plan and stays open as the public tracking handle until the
shipping commit closes it. Triage labels: `ready` (done-condition present —
anyone may pick it), `design` (plan first), `human-only` (operator judgment;
the fleet skips it).

## Releasing (dev tooling — outside the kernel)

Two user-invokable skills cut and promote versions. They are **tooling that
operates on the package**, not part of it (see the layering note above — trunk is
`master`, version is single-sourced from `pyproject.toml`):

- **`/release`** (`.claude/skills/release/`) — cut a rolling `vX.Y.Z`: bump the two
  version markers (`pyproject.toml` + the `src/dos/__init__.py` fallback literal,
  kept in lockstep by `scripts/release_bump.py`), draft `docs/releases/vX.Y.Z.md`,
  commit, tag, push to `master`, create a GitHub release, and verify with
  `pytest -q` + `dos doctor`. Scoped-by-default. **No** Go binary / zip /
  screenshots / versioned-install snapshot (those are host-only ceremonies DOS
  doesn't have). Backed by `scripts/release_context.py` (one-shot git + drift +
  preview JSON).
- **`/stable-release`** (`.claude/skills/stable-release/`) — promote an
  already-shipped `vX.Y.Z` to `stable/<codename>` on a gate of *green kernel suite
  + clean truth syscall + soak window* (the host's apply-loop hero-metric gate has no
  DOS analogue, so it's re-grounded on what the substrate actually has). Writes an
  evidence file + a second annotated tag; mints no new version. Backed by
  `scripts/stable_release_context.py`.

Adding a richer gate row or a new release ceremony = editing a `scripts/*.py` or a
skill, **never** a `src/dos/` module. That separation is the point.

## How the reference userland app consumes it

The reference userland app's `scripts/{ship_oracle,wedge_reason,picker_oracle,dispatch_tokens,
dispatch_loop_decide,gate_classify,dispatch_timeline,fanout_preflight_context,
fanout_archive_lock,check_phase_shipped,run_id,lane_journal}.py` are **byte-thin
re-export shims** over `dos.*` (`from dos.X import *`). Its `pyproject.toml`
pins `dos-kernel` (the distribution name; `dos-kernel>=X.Y` or `==X.Y.Z`); dev
install is `pip install -e` against this repository. **Edit substrate LOGIC here, not in the host
shims** — the shims carry none. The host picks up changes via the editable install.
This is one pinned dependency, NOT a mono-repo fold (the host's own "Independent
Repository" rule is honored on both sides).

> **The distribution name is `dos-kernel`, NOT `dos`.** The bare `dos` name on
> PyPI is an unrelated package (`dos` 1.6.0, a Flask/OpenAPI helper) — a
> `pip install dos` / `dos>=X` pin resolves to that squatter and would even
> shadow `import dos`. The **import** name is still `dos` (`import dos`, the `dos`
> /`dos-mcp` console scripts); only the pip-install / dependency-pin name is
> `dos-kernel` (set by `[project].name`; the import is set independently by
> `packages.find` discovering the `dos` package). Install/pin **only** `dos-kernel`
> (or `pip install -e` against this repository for dev). `install.py` is safe by construction (it
> does `pip install -e .` and asserts the resolved path is inside this repo);
> docs that write `pip install dos-kernel[mcp]` mean "the `[mcp]` extra vs. the
> core install." See SECURITY.md "Supply chain".

## What is NOT yet ported (the heavy tier)

`fanout_state.py` lease core, `next_up_*` renderers, and the plan-meta schema
stay in the reference userland app — they are host workflow + heavy I/O, not kernel mechanism. The
`dos.arbiter` is the *extracted pure* admission kernel for new consumers; the host
still owns its own `arbitrate_lane`. See `docs/` for the next-stage plan.

> **Drifting downward (docs/97 Phase 1 has landed at the API).** The concurrency-class
> *claim-budget* — "at most N of kind K may hold a lease at once" — is no longer
> purely host-side: `arbiter.arbitrate(..., class_budgets={"priority": 3})`
> takes the budgets (`arbiter.py:159`), counts live leases per kind on the
> auto-pick walk and skips budget-exhausted candidates (`arbiter.py:329-348`),
> and returns the named `CLASS_BUDGET_EXHAUSTED` refuse (`arbiter.py:637-655`),
> pinned by `tests/test_arbiter.py`. So the kernel already owns the *admission
> logic*; the host supplies only the *value*. What is NOT yet reachable from the
> operator surface is the rest of docs/97: no `dos arbitrate --class-budget K=N`
> flag and no `[[concurrency_class]]` table in `dos.toml` (the budgets are a
> Python parameter only). Wiring that CLI/config seam — and folding the host
> soft-claim / `STALE_CLAIM` adjudication into the class registry — is the
> remaining lift, planned in `docs/97_concurrency-class-model-plan.md`. (Note the
> word *claim* is overloaded here: a lane lease is a region-CLAIM the arbiter
> adjudicates (docs/89); the intent ledger's `STEP_CLAIMED` is the agent's
> distrusted *self-report* of progress vs the git-`STEP_VERIFIED` fact (docs/107);
> the host `soft_claim`/`STALE_CLAIM` is a packet read against `execution-state.yaml`.
> Three different things — keep them apart.)

## Glossary — acronyms used in this contract

The terms that appear above without expansion. (Industry / competitive-landscape
acronyms — `AAA`, `AGT`, `OAuth`, `ID-JAG`, `AUROC` — are glossed in the strategy
repo's `README.md`, not here; this list is the *kernel-internal* vocabulary.)

**Architecture**

- **DOS** — Dispatch Operating System (this package).
- **ABI** — Application Binary Interface. "The syscall ABI" is the stable surface of
  kernel calls (`verify`/`liveness`/`resume`/`refuse`/`arbitrate`/`spawn`/`reap`) a
  consumer codes against — borrowed from the OS sense: the contract that doesn't
  change under you.
- **CLI / TUI** — Command-Line Interface / Terminal User Interface (the `dos` verbs;
  the `rich.live` screens behind the `[tui]` extra: `dos top`, `dos decisions`).
- **MCP** — Model Context Protocol (the `dos_mcp` server exposes the syscalls as MCP
  tools — JSON over stdio, no `import dos`; the lowest-friction adoption surface).
- **PDP / PEP** — Policy **Decision** Point / Policy **Enforcement** Point. DOS's
  kernel is a PDP (it *decides* a verdict) with no PEP (it does not *enforce* — it
  reports and proposes, never acts). The opt-in host PEP is `dos apply` (docs/126).

**Durability & recovery**

- **WAL** — Write-Ahead Log. The lease journal (`lane_journal`): an effect is logged
  *before* it is believed, so the record outlives the process that wrote it.
- **ARIES** — Algorithms for Recovery and Isolation Exploiting Semantics (the classic
  database crash-recovery algorithm: analysis → redo → undo). DOS's `resume` is "the
  ARIES third phase" — *continue* a run from its durable fossils rather than undo it.
- **CAS** — Compare-And-Swap (the value-keyed atomic steal in the archive-lock).
- **TOCTOU** — Time-Of-Check to Time-Of-Use (the archive-lock race that was closed).

**Trust & evidence**

- **TCB** — Trusted Computing Base (the minimal-TCB / reference-monitor doctrine the
  kernel descends from: keep the trusted part small and separated).
- **ORACLE → JUDGE → HUMAN** — the trust ladder: a deterministic verdict first
  (`dos.oracle`), a non-deterministic *advisory* adjudicator only on the residue
  (`dos.judges`, fail-to-abstain), a human only at the irreducible seed.
- **SKP** — Skill Pack (docs/74: the domain-free generic `SKILL.md` screenplays
  shipped as package-data under `src/dos/skills/`).

**Plan-codes** (internal shorthand for the job→dos extraction plans; see the
"Substrate roadmap" memory): **ISV** in-substrate-verify · **AOS** arbiter/oracle
seam · **DSM** durable-schema · **DLA** durable-lane · **CID** correlation-id ·
**LJ** lane-journal · **DSP** dispatch-spine (the Python→Go port, docs/100/124) ·
**DOM** the self-describing `man` surface. These are private to the planning notes,
not part of the shipped API.

## Provenance

The spine logic is a faithful lift-and-shift of the reference userland app's
`scripts/`; the decision logic is byte-faithful, the only changes are import paths
and the workspace/config seam.
