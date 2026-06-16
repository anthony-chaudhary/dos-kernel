# OS-level evidence, and the process-liveness rung for `liveness()`

> **Running locally surfaces a whole shelf of "proof points" that *feel* like
> ground truth — file mtimes, system logs, the OS process table, CPU/RSS. Most of
> them are a self-report wearing an OS costume. Exactly one slice clears the
> kernel's forgeability bar cleanly, and it does not want a new syscall: it
> sharpens the alive/dead boundary `liveness()` already draws. This note sorts the
> local OS signals by the [`93`](93_verifying-live-non-git-sources.md) gate-2 test
> ("who can author this byte?"), then specs the one admissible signal — an OS
> process-liveness rung — as a `proc_delta.py` boundary reader feeding an optional
> field into `ProgressEvidence`, the [`git_delta`](../src/dos/git_delta.py) /
> [`journal_delta`](../src/dos/journal_delta.py) shape, classifier byte-unchanged.**

This is the *local* sequel to [`93`](93_verifying-live-non-git-sources.md). That
note placed live, **remote** sources (CI, infra logs, Slack, screenshare) on the
accountability spectrum and found that almost all of them are driver oracles, none
are kernel verbs. This note asks the question a *local* run raises — *what about
the OS the agent is running on? Its timestamps, its logs, its process table?* — and
runs the same single test on each. The answer has the same shape (the machinery
already generalizes; it is an evidence-sourcing question) but a different
conclusion: one local OS signal **is** domain-free, unforgeable, and mechanical, so
it lands not as a driver but as a new **rung inside an existing kernel verb**.

It is a theory + spec note in the family of [`82`](182_the-kernel-is-a-taxonomy-of-refusal.md),
[`84`](183_how-much-does-this-lean-on-git.md), [`85`](85_extending-the-verifiable-surface.md),
[`93`](93_verifying-live-non-git-sources.md). It specs code but does not ship it:
the deliverable is `src/dos/proc_delta.py` + an optional `ProgressEvidence` field +
a CLI gather wire + tests, all additive.

---

## 1. The instinct, and the trap

Run a fleet locally and the temptation is immediate: *we have the whole machine —
let's prove progress with more than git.* The OS stamps file modification times.
`systemd-journald` / the Windows Event Log record everything. `/proc` knows every
process's start time and CPU. Surely all of this is stronger evidence than an
agent's "I'm making progress"?

Some of it is. Most of it is the **exact disease the kernel exists to refuse**,
relabelled. The one test that sorts it ([`93 §2`](93_verifying-live-non-git-sources.md),
restated): **who can author this byte — the OS, or the agent being judged?**

The kernel already lives by this on the temporal axis.
[`journal_delta`](../src/dos/journal_delta.py) deliberately trusts an entry's *own
append `ts`* (which the kernel wrote) and **never** the self-reported `heartbeat_at`
(which a copy-prone foreign writer supplied) —
[`journal_delta.py:217`](../src/dos/journal_delta.py). That single line is the whole
philosophy: a timestamp is only evidence if the *party being judged did not write
it.* "OS-flavored" is not a synonym for "unforgeable."

---

## 2. The local OS signals, sorted by who authors the byte

The same word lands at opposite ends of the spectrum depending on who caused the
write — exactly as "logs" split down the middle in [`93 §3`](93_verifying-live-non-git-sources.md).

| Local OS signal | Who *causes* the byte | Gate 2 | Where it lands |
|---|---|---|---|
| **File mtime / `stat` ctime** on touched files | **The agent** — it's writing the files; `touch`, an editor, `os.utime`, even mtime-preserving copies are trivial. | ✗ **Forgeable** | A self-report in an OS costume. The mtime analogue of `heartbeat_at`. **Drop it** (or feed a judge). |
| **CPU% / RSS** sampled by DOS | The OS, but it answers the wrong question. | ~ proves *alive*, never *advancing* | A spinner pegs a core. Activity ≠ progress. At best a weak aliveness hint, dominated by the next row. |
| **OS process liveness** — is the run's PID still alive, and when did the OS say it started? | **The OS**, read by DOS — the agent cannot keep a PID alive after it crashes, nor backdate the kernel's process-creation time. | ✓ **Unforgeable, domain-free, mechanical** | A **kernel rung** inside `liveness()`. The subject of §3–4. |
| **OS audit logs** — `auditd` (Linux), Windows Security / Sysmon, journald for a process tree the agent can't write | **The OS**, root-gated, append-only; the agent can't rewrite without privilege. | ✓✓ Strongest local signal | A **driver oracle** for the *unbuilt* verbs (journal-integrity, acceptance). Heavy, platform-coupled, not the liveness rung. §6. |

The line through that table is sharp and worth stating as a law:

> **Anything the agent writes is not evidence *about* the agent, no matter how
> OS-flavored the wrapper.** File mtimes are the seductive trap — the OS stamped
> them, so they *feel* like ground truth, but the agent caused every write, so
> they are exactly as trustworthy as "I'm making progress." This is the
> `heartbeat_at`-vs-`ts` distinction ([`journal_delta.py:217`](../src/dos/journal_delta.py))
> generalized to the filesystem.

Two signals survive: **OS process liveness** (light, domain-free → a kernel rung)
and **OS audit logs** (heavy, provider-shaped → a driver). The rest is belief.

---

## 3. Why process-liveness is a *rung*, not a verb

Run process-liveness through the [`85 §2`](85_extending-the-verifiable-surface.md)
four-gate test:

1. **A claim about ground-truth state?** Yes — "this run's process is still alive"
   is a fact about the world, not taste.
2. **Unforgeable by the agent?** Yes — a crashed agent cannot fake a running PID;
   the OS process-creation time cannot be backdated by the process itself.
3. **Domain-free?** Yes — "is PID P alive" is the same question on every host, no
   provider, no schema. (The *reader* is platform-specific — `/proc` vs
   `OpenProcess` — but that is a boundary-I/O detail, the same way `git_delta`
   shells a specific binary; the *signal* is universal.)
4. **Mechanical / typed?** Yes — alive is a boolean; it collapses into the existing
   `Liveness` enum.

It passes all four — which would suggest a kernel verb. But it is **not a new
verb**, because it answers *the same question* `liveness()` already answers, only
with a better sensor. `liveness()`'s alive/dead boundary
([`liveness.py:266`](../src/dos/liveness.py)) is decided today by
`last_heartbeat_age_ms` — and a heartbeat is *something the agent's harness emits*.
A crashed agent whose last heartbeat is still fresh reads SPINNING-or-ADVANCING
when it should read STALLED. An OS process probe is a **strictly better aliveness
signal for the same verdict**: the agent cannot beat a heartbeat for a process the
OS knows is gone.

So the move is the [`76`](76_flexible-goals-and-verification.md) one — *the give
lives in which-signals, not the adjudication*. We add a more accountable signal to
the **alive/dead rung** of an existing verdict; the verdict's enum, its ladder, and
its meaning do not change. This is the kernel's preferred kind of extension: new
evidence enters as a rung, the verdict stays mechanical.

### The accountability ladder for "is this run alive", after this rung

```
strongest  OS says the PID is alive            ← proc_delta — the agent can't fake a live PID
     │     journal heartbeat (kernel-written ts)  ← journal_delta — kernel wrote the ts, agent triggered the beat
weakest    --last-heartbeat-age-ms (passed in)    ← a number the caller supplied
```

Each rung is more accountable than the one below: the OS owns the top, the kernel
owns the middle (it writes the `ts` even though the agent triggers the beat), the
caller owns the bottom. `proc_delta` adds the missing top rung.

---

## 4. The spec — `proc_delta.py`, the third boundary reader

`proc_delta` is `git_delta`'s and `journal_delta`'s sibling: **boundary I/O at the
CLI, a pre-gathered fact into the pure classifier.** The classifier
([`liveness.classify`](../src/dos/liveness.py)) stays byte-pure and
replay-testable; only *where one input comes from* changes — the exact Phase-2
move ([`82`](182_the-kernel-is-a-taxonomy-of-refusal.md)) that `journal_delta` made
for the heartbeat.

### 4.1 The PID is already in the evidence

The load-bearing find that makes this buildable on existing data: the **lane
journal already records `pid` and `host_id`** per lease entry
([`lane_journal.py:267-268`](../src/dos/lane_journal.py)). So the PID to probe is
available from the same `(loop_ts, lane)`-scoped lease the journal rung already
folds — no new field on the run-id, no new write path. This means the proc rung
**inherits the journal rung's identity discipline for free**: it engages only when
`--lane` + `--loop-ts` are given (the operator's "require identity always" call,
[`journal_delta.py:37-41`](../src/dos/journal_delta.py)), because without identity
we cannot know *which* PID is this run's. A `host_id` mismatch (the journal entry
was written on a different host) makes the probe meaningless — `proc_delta` must
treat a foreign-host lease as "rung absent," never probe a local PID that
coincidentally matches a number from another machine.

### 4.2 The reader (boundary I/O, `git_delta`-shaped)

```python
# proc_delta.py — sketch, not final
def probe(pid: int, *, host_id: str, this_host: str) -> ProcLiveness:
    """Is `pid` a live process on THIS host? PURE of verdict logic; does the I/O.

    Returns a small NamedTuple: (alive: Optional[bool], detail: str). `alive` is
    None ("rung absent") for every degrade — foreign host, no pid, probe
    unsupported on this platform, permission denied, ANY OSError — never a raise,
    never a fabricated True. The git_delta "every failure mode → safe empty"
    stance, here "every failure mode → None (no signal)".
    """
```

Platform readers, each degrading to `None`:

- **POSIX** — `os.kill(pid, 0)` (signal 0 = existence/permission probe, kills
  nothing); `ProcessLookupError` → `alive=False`, `PermissionError` → the process
  exists but isn't ours (still informative: `alive=True` is *not* claimed — degrade
  to `None`, the conservative read). **SHIPPED:** on Linux, `starttime()` reads
  `/proc/<pid>/stat` field 22 (`starttime`) and `probe(recorded_starttime=…)`
  corroborates the run-start, catching PID reuse — a pid that EXISTS but whose live
  starttime ≠ the recorded baseline is a recycled number, so the probe returns
  `None` (refuses to read the stranger as alive) instead of a false `True`. The
  baseline is captured at lease-mint (`lane_lease`), rides the WAL on the nested
  lease + survives ADOPT (`lane_journal`), and is fed at the `dos liveness` /
  `dos pulse` boundary + the `_expire_dead` reclaim probe. Absent baseline ⇒
  existence-only, byte-identical to before.
- **Windows** — `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, …)` +
  `GetExitCodeProcess` (alive iff `STILL_ACTIVE`); **SHIPPED:** `starttime()` reads
  `GetProcessTimes` creation time as the win32 PID-reuse corroboration, the same
  demote-to-`None`-on-mismatch shape as Linux. (Use only stdlib + `ctypes`; the
  kernel's dep set stays PyYAML-only — [CLAUDE.md](../CLAUDE.md).)
- **Anywhere else / unsupported** — `None`. The rung simply does not engage; the
  verdict still answers from the commit + journal + caller rungs. This is the
  no-plan rail ([`test_verify_no_plan`](../tests/test_verify_no_plan.py) sibling):
  `dos liveness` must return a verdict on a bare host where the proc probe is
  unavailable, exactly as it does where there is no journal.

### 4.3 The evidence field (additive, optional)

One new optional field on `ProgressEvidence`
([`liveness.py:134`](../src/dos/liveness.py)), defaulting to the no-signal value so
every existing caller and fixture is byte-unchanged:

```python
process_alive: Optional[bool] = None   # OS process-liveness probe; None = rung absent
```

`None` is the load-bearing default: it means "we did not (or could not) probe," and
it must read identically to today's behavior. The field is **OPTIONAL**, exactly
like `tokens_spent_since` — a workspace that can't probe passes `None` and the
verdict is unaffected (the no-telemetry discipline,
[`liveness.py:161-167`](../src/dos/liveness.py)).

### 4.4 The classifier change — one rung, fail-safe direction

`process_alive` enters the **alive/dead boundary only**, and only in the *safe*
direction. Today:

```python
alive = age is not None and age <= policy.spin_ms      # liveness.py:267
```

The minimal, sound change — the OS verdict can **demote alive → dead but never
promote dead → alive**:

```python
# A definitive OS "not alive" overrides a fresh-looking heartbeat: a crashed run
# whose last beat is still inside spin_ms is STALLED, not SPINNING. But a positive
# OS probe does NOT manufacture aliveness on its own — it confirms, it never
# fabricates (PID reuse, a coincidental match). The conservative asymmetry is the
# whole point: new evidence may only make the verdict MORE skeptical of "alive".
if ev.process_alive is False:
    alive = False
elif ev.process_alive is True:
    alive = alive          # corroborates; does not override the heartbeat rung up
# ev.process_alive is None → rung absent, behavior byte-identical to today
```

The asymmetry is the soundness guarantee and mirrors `journal_delta`'s "every
out-of-window op fails toward SPINNING/STALLED, never invents ADVANCING"
([`journal_delta.py:51-57`](../src/dos/journal_delta.py)): the new signal can only
make the verdict *more* skeptical of life, never less. A `False` probe correctly
flips a fresh-heartbeat-but-crashed run from SPINNING to STALLED — closing the one
real gap this rung exists to close. A `True` probe is allowed to corroborate but
not to override, because PID reuse means "a live PID with this number" is weaker
than "the kernel saw a beat from this lease."

> **Note the rung it touches.** `process_alive` affects SPINNING-vs-STALLED, never
> ADVANCING-vs-the-rest. A running process is *not* progress (a spinner is a
> running process — that's the entire premise of SPINNING,
> [`liveness.py:44-48`](../src/dos/liveness.py)). The ADVANCING boundary stays the
> *forward delta* (commits / lease events) and only that. This rung makes STALLED
> honest; it must never make a spinner look like it's advancing.

### 4.5 The CLI wire

In [`cmd_liveness`](../src/dos/cli.py) (the existing evidence-gather boundary,
`cli.py:382`), after the journal fold: when identity is present, pull this lease's
`pid` + `host_id` from the folded journal, call `proc_delta.probe(...)`, and pass
the result as `process_alive`. The boundary already reads the journal for the
heartbeat rung — the PID comes from the same entries, no extra read. `--no-proc`
opt-out and a `[liveness] probe_process = false` `dos.toml` knob keep it disengageable
(some hosts will not want DOS poking the process table); default-on is fine because
the rung degrades to `None` wherever it can't run.

---

## 5. What stays out (the disciplines that make it sound)

1. **No file mtimes as a progress signal — write it down.** This is the rabbit hole
   someone *will* fall into. mtime is `heartbeat_at`, not `ts`: the agent authors
   every byte. It does not enter `ProgressEvidence` in any form. (If a host wants a
   filesystem-activity *hint*, it is a driver-fed judge input, never a kernel rung.)
2. **CPU/RSS is not a rung.** Proves a core is busy, which a spinner satisfies. At
   most a future advisory note; it never touches the verdict.
3. **The rung degrades to `None` everywhere it can't run** — unsupported platform,
   foreign host, no PID, permission denied, any OSError. Never a raise, never a
   fabricated `True`. The `git_delta` "every failure → safe empty" /
   `journal_delta` "fail toward STALLED" stance.
4. **It stays a verdict, never an enforcement.** Reading the OS process table to
   *report* STALLED is in-bounds; reading it to `kill -9` is the host's watchdog,
   not the kernel ([`liveness.py:53`](../src/dos/liveness.py): SPINNING/STALLED are
   ADVISORY). The kernel sells the signal; the driver acts on it.
5. **Asymmetric: demote-only.** `process_alive=False` can flip alive→dead;
   `process_alive=True` may corroborate but never override. New evidence only
   increases skepticism of "alive."

---

## 6. The heavy tier — OS audit logs, for the *unbuilt* verbs (not this rung)

OS audit logs (`auditd`, Windows Security / Sysmon, journald for a tree the agent
can't write) are the **strongest** local signal — append-only, root-gated, genuinely
unforgeable by an unprivileged agent. But they are *not* the liveness rung, for two
reasons: they are heavy + platform-coupled (a driver, not near-stdlib kernel code),
and their real value is answering questions `liveness` doesn't ask. They are the
natural evidence source for two **unbuilt** verbs on the
[`93`](93_verifying-live-non-git-sources.md) / distrust-map list:

- **journal-integrity** ("is the prior state sound?", gap #5) — an OS audit log is
  *the* canonical place to detect a journal heartbeat from a PID the OS says already
  died: the "don't trust your own pre-crash notes" rung, feeding orphan detection.
  The agent can't forge `auditd`.
- **acceptance** ("done means done", gap #3) — a *kernel-launched* process's
  OS-recorded exit code is structured, unforgeable evidence that "the tests ran",
  vs. the summary line `verify` currently believes. Discipline (from the map):
  structured envelope, **never re-do the work**.

Both follow [`93 §4`](93_verifying-live-non-git-sources.md) move B (a driver oracle
on the seam, one-way import, degrades honestly when the log is absent), the
`ci_status.py` template. They are out of scope here — flagged so this note's
boundary is explicit: **process-liveness → a kernel rung now (light, universal);
audit logs → driver oracles later (heavy, for journal-integrity/acceptance).**

---

## 7. Build order (additive, ~one focused session)

1. `src/dos/proc_delta.py` — the boundary reader + `ProcLiveness` NamedTuple +
   per-platform probe, every failure → `None`. `tests/test_proc_delta.py` pins:
   live-self-PID → `True`, an impossible PID → `False`, foreign `host_id` → `None`,
   monkeypatched-unsupported → `None`, no raise on any input.
2. `ProgressEvidence.process_alive: Optional[bool] = None` + the demote-only rung in
   `liveness.classify`. Extend `test_liveness` with the frozen-fixture cases:
   `False` flips fresh-heartbeat SPINNING → STALLED; `True` does not override the
   heartbeat rung; `None` is byte-identical to today (regression pin).
3. Wire `cmd_liveness` — pull `pid`/`host_id` from the identity-scoped journal
   entries already folded, probe, pass `process_alive`. `--no-proc` +
   `[liveness] probe_process` knob.
4. `--output json` already echoes evidence via `to_dict`
   ([`liveness.py:198`](../src/dos/liveness.py)); add `process_alive` to the dict so
   the operator sees *why* STALLED (legible distrust — the renderer seam).

No new verb, no new exit code, no kernel dependency. One optional field, one new
boundary reader, one demote-only branch — the smallest possible change that makes
STALLED honest about a crashed-but-recently-beating run.

> **Update — the PID-reuse rung is now SHIPPED (§4.2 fulfilled).** Steps 1–4 above
> landed earlier; this note's one remaining sketch — the `/proc/<pid>/stat`
> `starttime` corroboration that catches PID reuse — is now built. `proc_delta`
> grows a `starttime(pid)` boundary reader (Linux `/proc/<pid>/stat` field 22;
> win32 `GetProcessTimes`; any other platform/error → `None`) and `probe(…,
> recorded_starttime=…)`: a pid that exists but whose live starttime ≠ the recorded
> baseline is a recycled number → `None`, never a false `True`. The baseline is
> captured at lease mint (`lane_lease`), travels the WAL on the nested lease and
> through ADOPT (`lane_journal`, with the inline forward-compat key + a `pid`-change
> drop so a stale stamp never accuses a fresh pid), and is fed at the `dos liveness`
> / `dos pulse` boundaries and the `_expire_dead` reclaim probe. Absent baseline ⇒
> existence-only, byte-identical to before — the same `None`-degrades-everywhere
> discipline as the rest of the rung. This is "DOS into Linux" in the true sense:
> the kernel reading procfs's own process-identity byte to make its liveness verdict
> sound against the dominant long-running-host failure mode.

---

## References

*The machinery this extends:*
- [`src/dos/liveness.py`](../src/dos/liveness.py) — the verdict the proc rung
  sharpens; the alive/dead boundary (`:266`), the ADVISORY discipline (`:53`), the
  optional-field precedent (`tokens_spent_since`, `:161`).
- [`src/dos/journal_delta.py`](../src/dos/journal_delta.py) — the Phase-2 template
  this copies: boundary read → pure fold → optional evidence field; the
  `ts`-not-`heartbeat_at` law (`:217`); fail-toward-STALLED (`:51`); identity-required
  (`:37`).
- [`src/dos/git_delta.py`](../src/dos/git_delta.py) — the "every failure mode →
  safe empty, never a raise" boundary-reader stance `proc_delta` follows.
- [`src/dos/lane_journal.py`](../src/dos/lane_journal.py) — already records `pid` +
  `host_id` per lease (`:267`), the data that makes the proc probe buildable with no
  new write path.
- [`src/dos/verdict.py`](../src/dos/verdict.py) — the `classify(Evidence, Policy) ->
  Verdict` ABI; the proc rung adds Evidence, not a new Verdict.

*The frame:*
- [`93_verifying-live-non-git-sources.md`](93_verifying-live-non-git-sources.md) —
  the accountability spectrum + "who can author this byte?" gate-2 test this note
  applies to *local* OS signals; the "agent's own logs/screen = forgeable floor"
  placement file mtimes re-create.
- [`85_extending-the-verifiable-surface.md`](85_extending-the-verifiable-surface.md)
  — the four-gate test; the three homes (rung-in-a-verb is the same family as
  driver-oracle / judge, here landing on "deepen an existing verb").
- [`182_the-kernel-is-a-taxonomy-of-refusal.md`](182_the-kernel-is-a-taxonomy-of-refusal.md)
  — `liveness` as the temporal distrust verb; the Phase-2 "change where an input
  comes from, not the classifier" move this rung repeats.
- [`76_flexible-goals-and-verification.md`](76_flexible-goals-and-verification.md) —
  the give lives in which-signals, never the adjudication: the proc probe is a more
  accountable signal on a fixed verdict.
