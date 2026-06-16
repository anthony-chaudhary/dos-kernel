# 278 — Integrating DOS with Hermes / OpenClaw: the missing lock manager for agent swarms

> **Status:** Phase 0 (audit) + Phase 1 (working example) + Phase 2 (the `hermes`
> hook-dialect driver) shipped. The audit's security figures are now web-verified
> and cited (2026-06-09); the example is pinned by `tests/test_hermes_integration_example.py`
> against the live CLI; the `HermesDialect` renderer is grounded in Hermes' real
> `pre_tool_call` shell-hook shape and pinned by the dialect + fail-open suites.
> Phase 3 (an MCP-injection variant) remains deferred — the centerpiece the
> operator asked for is the arbiter-coordination demo, and it stands alone. The
> Hermes YAML `install_spec` HAS NOW LANDED (`ConfigFormat.YAML` + `merge_yaml`
> in `hook_install.py` + `hermes_install_spec` in the driver), so `dos init
> --hooks hermes` wires `cli-config.yaml` directly — see "The `install_spec` now
> ships" below.

## The two-pronged thesis

DOS adds value to Hermes / OpenClaw on **two independent axes**, and the second
one matters *even for a single agent*:

1. **Coordination (multi-agent).** Hermes and OpenClaw run *fleets* of autonomous
   agents that touch *shared state*, and neither ships a lock manager for that
   state — which is precisely the hole DOS's arbiter (`dos arbitrate`) fills.
2. **Safety admission + verification (even single-agent).** Both runtimes ship
   with privileged tool access (shell, code-exec, file-ops, browser) and **weak
   default security**, and run *autonomously* — so an unsafe action (a destructive
   `bash -c`, an attempt to disable its own sandbox, a prompt-injected exfil) can
   fire with no gate. DOS supplies the missing **pre-act gate** (`exec_capability`
   + the PRE hook PEP + `SELF_MODIFY`) that refuses the unsafe action *before* it
   runs, and the missing **verifier** (`verify` / `commit-audit`) that refuses to
   *believe* the agent's "I did it safely" self-report. None of this needs a
   second agent.

DOS does not compete with these runtimes; it sits *under* them as the trust
substrate the kernel of this repo already is — "the part that doesn't believe the
agents." The two cheapest points of contact are the admission syscall
(`dos arbitrate`, for axis 1) and the PRE hook + `exec_capability` classifier (for
axis 2) — and both reduce to the same kernel discipline: *adjudicate the act
against ground truth instead of trusting the agent's narration of it.*

## What Hermes and OpenClaw actually are (audit, 2026-06-09)

Both are persistent, general-purpose **autonomous agent runtimes** that run on
your machine, reach you through your messaging channels, and handle recurring
workflows. The 2026 landscape:

- **Hermes Agent** (Nous Research, MIT, Feb 2026) — a Python framework for
  building autonomous agents on function-calling models; skills are created
  *autonomously by the agent* and compound across sessions.
- **OpenClaw** (Peter Steinberger; ~247k GitHub stars by Mar 2026) — a real,
  mainstream autonomous agent runtime, **not** a stand-in name: its lineage is
  Clawd → Clawdbot → Moltbot → **OpenClaw** (renamed off "Molt" after an Anthropic
  trademark complaint). Ships web-search / file-ops / code-exec / browser / Docker
  out of the box, reaches the user over messaging, and runs a "heartbeat"
  scheduled-polling loop (the **unattended** operation mode). Skills are static
  `SKILL.md` directories installed from **ClawHub**. (Sources: [Wikipedia —
  OpenClaw](https://en.wikipedia.org/wiki/OpenClaw); [Censys, "OpenClaw in the
  Wild," 2026-01-31](https://censys.com/blog/openclaw-in-the-wild-mapping-the-public-exposure-of-a-viral-ai-assistant/).)
- **SwarmClaw** (an OpenClaw-lineage self-hosted runtime, Node/TS) — the most
  explicit about the fleet shape: *"delegation, orchestrators, subagents, durable
  jobs, checkpointing, background execution"* and *"parallel joins"* over a
  **shared task board + shared memory documents**.

The decisive audit finding, from SwarmClaw's own docs:

> Multi-agent is first-class (*"temporary bounded runs for one agent or many …
> parallel joins"*). Shared state is first-class (durable documents,
> project-scoped memory, a delegation board). **But there is no documented
> transaction / locking / leasing API for that shared state** — extension is via
> *MCP tool injection* and *staged task-approval policies*, neither of which
> serializes two agents' concurrent writes to the same object.

That is the gap. A swarm of agents writing the same memory document, the same
task record, or the same external resource (a calendar slot, a row, a file) has
exactly the **TOCTOU / lost-update** problem DOS's lane arbiter was built to
refuse — and the runtimes punt it to the user.

## Why the arbiter, and not detection, is the lead

This repo's own research (the "fleet angle" memory; docs/233/245) already
established the load-bearing point: for a *single* agent, re-running `verify(x)`
is a trivial one-liner and DOS's defensive lift is ~0. **Concurrency is what
changes the verdict** — two agents that each individually "succeed" can still
corrupt shared state because their success was adjudicated independently, with no
referee between them. That referee is `arbitrate`. So for a swarm runtime the
pitch is not "DOS catches your agent lying" (true, but a harder sell); it is
**"DOS stops two of your swarm agents from silently clobbering each other,"**
which is a structural guarantee the runtime cannot provide itself and the user
feels immediately.

The benchmark precedent on this exact half-plane is already in the repo:
- docs/233 — coordination payoff RAN LIVE: naive agents corrupt a DB, the arbiter
  (region = `reservations/<id>`) prevents it → **J = 6/8 off the DB-hash**.
- docs/245 F2 — NATURAL collisions, arbiter prevented J = 4/6.
- docs/245 F4 — the closed-form `payoff = C(K,2)·shared·clobber·F^D`: **0 at
  K = 1, ~173–505 at K = 32**. The value is superlinear in fleet size — which is
  the regime a swarm runtime lives in.

## The single-agent safety prong — why it matters here

The fleet-angle research above is about *coordination*. But Hermes and OpenClaw
have a second, orthogonal problem that bites a **lone** agent, and it is the one
the operator flagged: **unsafe actions and missing verification.** The 2026
record on these runtimes is blunt, and — unlike the rest of this doc — every
figure below is anchored to a primary or strong-secondary source (fact-checked
2026-06-09; the exposure/skill counts are date-sensitive and snapshotted as such):

- **Security is opt-in, not built-in.** As of Jan 2026 OpenClaw ships with
  sandboxing and the `exec` (shell) tool *off / opt-in by default*, and its
  flexible config system *"allows users to disable critical security controls,
  including plugin signature verification and execution sandboxing"* (Taming
  OpenClaw §4.1; [OpenClaw docs — Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)).
  Cisco's blunt summary: *"Security for OpenClaw is an option, but it is not built
  in."* So the hazard is not "a sandbox you can turn off" — it is **deep,
  privileged tool access (shell, file-ops, code-exec, browser, calendar/email)
  with the guard rails off unless you opt in**, often run **unattended** via the
  heartbeat loop. ([Cisco, "Personal AI Agents like OpenClaw Are a Security
  Nightmare," 2026-01-28](https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare).)
- **The two primary attack vectors are prompt injection and the malicious-skill
  supply chain.** Indirect prompt injection — adversarial text in a fetched page or
  tool result — is *"the most pervasive threat during the input phase"* (Taming
  OpenClaw), and The Register confirmed the platform *"is vulnerable to indirect
  prompt injection, allowing an attacker to backdoor a user's machine"* ([2026-02-05](https://www.theregister.com/2026/02/05/openclaw_skills_marketplace_leaky_security/)).
  In *volume*, the malicious-skill channel was at least as damaging (the ClawHavoc
  campaign, Atomic-macOS-Stealer-via-skill). An agent does not need to be
  multi-agent to `rm -rf` your home dir because a webpage — or an installed skill —
  told it to.
- **Ecosystem rot, with real numbers:**
  - *~26% of ~31,000 community skills contained at least one vulnerability* — Cisco
    AI Defense (Chang & Narajala), corroborated independently by an academic audit
    (Liu et al., *"Agent Skills in the Wild,"* 2026) cited at the same figure.
  - *824 malicious skills out of ~10,700 on ClawHub* as of 2026-02-16 (Koi
    Security; Bitdefender independently put it at ~900, ≈20% of the registry). The
    earliest hard count was *341 / 2,857* on 2026-02-03 (the ClawHavoc campaign,
    [eSecurityPlanet](https://www.esecurityplanet.com/threats/hundreds-of-malicious-skills-found-in-openclaws-clawhub/)).
  - *21,639 OpenClaw instances exposed on the open internet* as of 2026-01-31
    (Censys, default port TCP/18789 — *"from ~1,000 to over 21,000 in under a
    week"*). Later scans diverge by method (SecurityScorecard ~135k peak; Censys
    63,070 app-layer on 2026-03-31) — so the count is large and growing; the
    21k figure is the dated Jan-31 snapshot.
  - Dedicated 2026 security papers exist and are real arXiv preprints: **"Taming
    OpenClaw"** ([arXiv:2603.11619](https://arxiv.org/abs/2603.11619), Tsinghua /
    Ant Group) and **"ClawKeeper"** ([arXiv:2603.24414](https://arxiv.org/abs/2603.24414),
    a runtime "Watcher" middleware that halts high-risk actions) — among several
    others (PRISM, SkillProbe, ClawWorm).

DOS does not solve prompt injection (nothing fully does). What it provides is the
**deterministic pre-act gate and post-act verifier these runtimes lack** — three
kernel primitives, each already shipped and tested, each consumable by a single
agent with no fleet:

| Hazard (single agent) | DOS primitive | Mechanism |
|---|---|---|
| A destructive / arbitrary-exec command (`bash -c 'rm -rf …'`, `npx <fetched>`, `ssh …`, `sudo …`) | **`exec_capability`** (`dos exec-capability --command "…"`) | Classifies the *invoked program SHAPE* against a closed arbitrary-exec set (lifted from CC's `dangerousPatterns.ts`). `GRANTS_ARBITRARY_EXEC` → exit 3. **SHAPE not word**: `cat python.txt` is BOUNDED, so it does not false-positive on filenames. The PRE hook attaches this as a deny/warn signal *before* the command runs. |
| The agent editing its **own running code / sandbox config** to widen its powers | **`SELF_MODIFY`** admission predicate (in `arbitrate`) | A lane request whose file-tree touches the kernel's / runtime's own path is refused structurally — the exact move "disable execution sandboxing" makes is the move the predicate exists to block. |
| The agent **claiming** it did the work / did it safely, when it did not | **`verify` / `commit-audit`** | The truth syscall answers from git ancestry + ship-stamp grammar, never the agent's "done." `commit-audit` checks a commit's *subject* against its own *diff*. The witness is bytes the agent did not author (docs/138). |
| A plain shell guard wanting to say "block this" with just an exit code | **`hook_exit`** (`dos hook-exit --code N`) | The cheapest integration: exit 2 → BLOCK, other non-zero → WARN (fail-safe). Any script a runtime already runs in its tool lifecycle becomes a DOS gate. |

The discipline is the docs/143 lesson: a capability *observation* is **advisory by
default** — `exec_capability` REPORTS `GRANTS_ARBITRARY_EXEC`; the consumer (the
PRE hook, or a host driver) decides whether that is a WARN or a hard DENY, because
spurious disruption is the expensive mistake. A runtime that wants a hard block on
arbitrary-exec for an *unattended* agent flips it to DENY; an interactive one keeps
it a WARN. That choice is the host's, never the kernel's.

**Why this is a real add even for one agent:** the runtimes' own answer here is
*"sandbox it / approve each step / trust the skill,"* and the audit shows all three
fail in practice (sandbox can be disabled; approval fatigue; 26% of skills are
buggy). A deterministic, forgeable-proof classifier that fires on the *shape of the
act* — not on the agent's say-so, not on a skill's reputation — is a different and
sturdier layer. It is the same kernel thesis ("don't believe the agent") pointed at
the *safety* of one act instead of the *truth* of one claim.

## The three integration surfaces (all audited; surface 1 is the centerpiece)

DOS exposes three points a foreign runtime can wire into, in increasing
invasiveness. The kernel/driver split (CLAUDE.md) holds on all three: **nothing
under `src/dos/` learns the word "hermes."**

### Surface 1 — the admission CLI (`dos arbitrate`) — THE CENTERPIECE

The lowest-coupling, highest-value contact. Any runtime that can shell out (or
call the `dos_arbitrate` MCP tool) asks one question before it touches shared
state:

```bash
dos arbitrate --workspace <repo> --lane reservations/42 --kind keyword \
    --tree "reservations/42/**" --leases "<live-leases-json>"
# exit 0 + {"outcome":"acquire",...}  → safe to proceed, you hold the region
# exit 1 + {"outcome":"refuse",...}   → another agent holds it; back off / auto-pick
```

The mechanism (audited in `arbiter.py`):
- `arbitrate()` is a **pure** `state-in → decision-out` function. The CLI gathers
  `live_leases` at the boundary (via `lane_journal.replay(read_all())`), passes
  them in, and the verdict never touches disk itself.
- A **region** is a set of repo-relative globs (`reservations/42/**`). Two
  requests collide iff their trees are not prefix-disjoint (the unforgeable floor
  under any pluggable overlap policy — `overlap_policy.admissible_under_floor`).
- On collision the arbiter does not just refuse; for a *cluster* request it
  **auto-picks a free disjoint lane** and hands it back, so a swarm agent that
  asked for a busy region is redirected to productive work instead of spinning.
- The lease is durably recorded in the **WAL** (`.dos/lane_journal.jsonl`,
  append-only, `fsync`'d per write), so the lock survives the process that took
  it — the crash-safety a swarm of ephemeral subagents needs.

The two-agent race, concretely (the demo in `examples/hermes_integration/`
exercises exactly this):

```
Agent-A: arbitrate(region=reservations/42) → ACQUIRE   (WAL: ACQUIRE seq=1)
Agent-B: arbitrate(region=reservations/42) → REFUSE    (region held by A)
Agent-A: …writes reservations/42…  RELEASE             (WAL: RELEASE seq=2)
Agent-B: arbitrate(region=reservations/42) → ACQUIRE   (now free)
⇒ the two writes are SERIALIZED; zero lost updates.
```

### Surface 2 — MCP tool injection (`dos-mcp` over stdio)

OpenClaw-family runtimes' **documented #1 extension mechanism** is *"connect any
MCP server (stdio/SSE/HTTP) and inject its tools into agents."* DOS ships exactly
that: `dos-mcp` (console script, `[mcp]` extra) exposes 8 tools over stdio —
`dos_arbitrate`, `dos_verify`, `dos_commit_audit`, `dos_status`,
`dos_refuse_reasons`, `dos_check_reason`, `dos_doctor`, `dos_recall`. Each takes a
`workspace` arg per call (correct for a long-lived server fielding concurrent
workspaces) and returns the kernel verdict's own `to_dict()` + an `interpretation`
hint. So a Hermes/OpenClaw agent gets `dos_arbitrate` as a *native tool* with zero
Python coupling. (Audited in `src/dos_mcp/server.py`; the same arbiter, reached
over JSON-RPC instead of a subprocess.)

### Surface 3 — hook/dialect intercept (`dos hook … --dialect hermes`) — SHIPPED

The enforcement (PEP) surface: wire `dos hook pretool|stop` into the runtime's
tool-call lifecycle so DOS can **DENY** a bad tool call or a false-done. DOS already
speaks five vendor dialects (Claude Code default + Codex/Gemini/Cursor/Antigravity
drivers); the **`hermes` dialect is now the sixth** (`HermesDialect` in
`drivers/hook_dialects.py`, registered through the `dos.hook_dialects` entry-point —
a pure driver, zero kernel change, the vendor-blindness litmus holds).

The decisive grounding finding (web-verified 2026-06-09 against each runtime's own
hook docs + GitHub) is what made this buildable *honestly* — and is why only
**Hermes** got a dialect:

- **Hermes** has a real, shipped `pre_tool_call` **shell** hook that emits JSON on
  stdout: a hook BLOCKS by printing `{"decision": "block", "reason": …}` (ALLOW is
  `{}`). That is a genuine "renderer → stdout bytes" surface — exactly the DOS
  dialect model — so `HermesDialect` renders that shape and is pinned by golden-byte
  tests + the exhaustive fail-open matrix (a DENY carries a host-honored blocking
  signal at *every* moment) + an end-to-end `dos hook stop --dialect hermes` test.
  ([Hermes hooks docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks).)
- **OpenClaw**'s real interception hook (`before_tool_call`, [shipped 2026-02-03](https://github.com/openclaw/openclaw/issues/5943))
  is an **in-process TypeScript return value** (`{ block: true, blockReason }`), NOT
  stdout JSON — so a `--dialect openclaw` that prints bytes would have *no consumer*.
  Its look-alike exit-2 `PreToolUse` shell protocol was [proposed and closed
  NOT-PLANNED](https://github.com/openclaw/openclaw/issues/60943). DOS therefore
  ships **no** OpenClaw dialect: an honest renderer there is a TS adapter, not a
  stdout dialect.
- **SwarmClaw** documents **no** first-class pre-tool veto hook at all (only
  task-stage approval + human-loop + MCP injection) — so, likewise, no dialect.

One caveat kept deliberately honest: Hermes' shell hook has no non-blocking "add
context" channel, so a DOS **WARN** degrades to a non-blocking pass there (the
corrective is dropped) — surfaced in the renderer's docstring rather than smuggled
onto a field Hermes does not read.

**The `install_spec` now ships** (the YAML lift that was deferred): `hook_install.py`
gained `ConfigFormat.YAML` + `merge_yaml`/`wired_events_yaml` (PyYAML is already the
kernel's one dep — no new dep), and the driver gained `hermes_install_spec`. So
`dos init --hooks hermes .` writes exactly the `cli-config.yaml` below, idempotently,
preserving the user's keys and per-entry `timeout`. Hermes documents no stop/agent-end
shell hook, so DOS wires only `pre_tool_call` / `post_tool_call` (the honest-coverage
rule, docs/294). The same file can still be **hand-wired** if you prefer — in
`cli-config.yaml`:

```yaml
hooks:
  pre_tool_call:
    - command: "dos hook pretool --workspace . --dialect hermes"
      timeout: 30
```

Hermes pipes the tool event to that command on stdin and reads the
`{"decision":"block","reason":…}` (or empty) it prints — the same bytes the
golden tests pin.

## The end-to-end working example (`examples/hermes_integration/`)

A self-contained, **offline**, copy-me demo that proves **both** prongs with
controlled A/B arms:

- **`hermes_adapter.py`** — the integration boundary, the heart of the example:
  the ~2 small functions a Hermes/OpenClaw integrator drops into their
  tool-execution path. `guard_action(command)` shells `dos exec-capability` and
  returns an allow/deny *safety* verdict (axis 2); `claim_region(region, leases)`
  shells `dos arbitrate` and returns an acquire/refuse *coordination* verdict
  (axis 1). No `import dos` — it speaks the CLI exactly as a foreign runtime must.
- **`shared_resource.py`** — a tiny stand-in for the shared state a swarm fights
  over: a JSON "reservations" store with a deliberately racy read-modify-write
  (read the slot, think, write it back) — the lost-update bug in miniature.
- **`swarm_agent.py`** — a mock Hermes/OpenClaw worker. It (a) may be handed a
  *tool command to run* (some safe, some the injected `bash -c 'rm -rf …'` hazard)
  and (b) wants to *book a slot* in the shared store. Two modes: `naive` (runs the
  command and writes directly — the runtime's status quo) and `guarded` (gates the
  command through `guard_action` and the write through `claim_region`).
- **`run_safety_demo.py`** — axis 2, single agent: feeds the agent a mix of safe
  and unsafe tool commands, runs the naive arm (every command executes → the
  destructive one fires) and the guarded arm (`exec_capability` DENIES the
  arbitrary-exec ones *before* they run). Headline: *unsafe commands executed:
  naive = N, guarded = 0*, plus the SHAPE-not-word proof (`cat python.txt` is
  allowed).
- **`run_coord_demo.py`** — axis 1, K concurrent agents racing for the **same**
  slot. The coordination is **not simulated**: each guarded agent calls
  `dos lease-lane acquire` against a throwaway DOS workspace, which arbitrates AND
  journals the grant to the real WAL atomically (under DOS's archive-lock); a
  concurrent agent replays that journal, sees the region held, and is refused.
  Naive arm → K−1 lost updates; guarded arm → 0. Headline: *lost updates: naive =
  K−1, guarded = 0*, measured off the resource's own final state. Verified robust
  across repeated runs (a different agent wins each time — a genuine race) and at
  K ∈ {1, 4, 6, 8}.
- **`run_demo.py`** — runs both, prints the combined scoreboard.
- **`README.md`** — the "why + how to wire it into *your* runtime" guide, with the
  copy-me adapter and the K=1 falsifier called out.

Both demos' claims are checkable and non-forgeable: the safety arm counts commands
that *actually executed* (a side-effect file the command writes), and the coord arm
counts lost updates by *re-reading the shared store's final contents* — so an agent
cannot narrate its way to "0." That is the repo's own discipline (witness ≠
claimant, docs/138) applied to the example.

## What this is NOT (scope discipline)

- **Not a kernel change.** The example + audit land in `examples/` (a concurrent
  lane) and `docs/`; the arbiter is consumed exactly as-is. The one code addition —
  the `HermesDialect` renderer — lands in a **driver**
  (`src/dos/drivers/hook_dialects.py`), the one layer where naming a vendor as code
  is *allowed* (the same place `GeminiDialect`/`CursorDialect` live). So the
  vendor-blindness litmus still holds: no **non-driver** kernel module names
  "hermes" (`tests/test_vendor_agnostic_kernel.py` stays green), and no kernel
  *adjudication* branches on which runtime is acting — a dialect is OUTPUT chosen by
  `--dialect`, strictly downstream of an already-decided verdict.
- **Not a single-agent pitch.** At K = 1 the demo shows 0 collisions in *both*
  arms (the benchmark's own falsifier — Wall §1). The value appears only at K ≥ 2,
  and grows with K. The README says so plainly.
- **Not detection.** This is the coordination half-plane (referee *between* agents),
  the sibling of the detection work (referee *over* one agent's claims).

## Phasing

- **Phase 0 — audit (DONE; figures web-verified 2026-06-09).** The three surfaces
  mapped to exact CLIs / tool names / JSON shapes / exit codes; the security
  characterization re-grounded against primary sources (Cisco AI Defense, Censys,
  Koi Security, the Taming-OpenClaw / ClawKeeper arXiv papers) with the date-sensitive
  exposure/skill counts snapshotted.
- **Phase 1 — working example (DONE; pinned).** `examples/hermes_integration/`,
  offline, A/B-controlled, collision count off the resource's own state — now
  guarded by `tests/test_hermes_integration_example.py`, which runs the adapter +
  both demo arms through the real `dos` CLI and asserts the verdicts, so a CLI
  contract drift reddens instead of silently no-op'ing the example.
- **Phase 2 — `hermes` hook dialect (DONE).** `HermesDialect` in
  `drivers/hook_dialects.py` + the `dos.hook_dialects` entry-point, grounded in
  Hermes' real `pre_tool_call` shell-hook deny shape (`{"decision":"block",…}`) and
  pinned by the dialect golden-byte + fail-open + CLI suites. OpenClaw/SwarmClaw get
  **no** dialect by design (their hooks are not stdout-JSON surfaces; see Surface 3).
- **Phase 2.5 — `hermes` YAML `install_spec` (DONE, the deferred lift).**
  `hook_install.py` gained `ConfigFormat.YAML` + `merge_yaml`/`wired_events_yaml`
  (PyYAML is the kernel's one dep — no new dep), and the driver gained
  `hermes_install_spec`. So `dos init --hooks hermes` wires `cli-config.yaml`
  (`pre_tool_call` / `post_tool_call`, flat entries, no stop — Hermes documents none)
  directly, idempotently, preserving the user's keys + per-entry `timeout`. The Go
  fast-path also gained a `renderHermes` transcode + parity case, so a Go-decided
  `--dialect hermes` deny emits the flat `{"decision":"block"}` Hermes reads instead
  of silently degrading to the CC envelope (the docs/268 fail-open, closed).
- **Phase 3 — MCP-injection variant (DEFERRED).** The same demo driven through
  `dos-mcp` over stdio instead of a subprocess, to match the runtimes'
  documented #1 extension path.

## Provenance / cross-refs

- The fleet-angle thesis: the "Lead with the FLEET angle" + "Canonical
  fleet-pitch wording" memories; docs/170.
- The coordination payoff, measured: docs/233, docs/245 (F2/F4).
- The arbiter mechanism: `src/dos/arbiter.py`, `src/dos/lane_journal.py`,
  CLAUDE.md "DOS on DOS" §2.
- The MCP surface: `src/dos_mcp/server.py`, docs/80.
- The dialect/driver pattern: `src/dos/drivers/hook_dialects.py`, docs/217/268.
