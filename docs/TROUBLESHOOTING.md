# Troubleshooting — the day-1 stumbles

This is the "is this normal? what do I do?" page for the bumps a newcomer hits in
the first hour. Each entry names the symptom, why it happens, and the one move
that fixes it. For the architecture, see
[CLAUDE.md](../CLAUDE.md) and [docs/ARCHITECTURE.md](ARCHITECTURE.md); for the
full surface, run `dos start-here`.

---

## `import dos` fails after `pip install dos` (or `dos` does something weird)

**Symptom.** You ran `pip install dos`, then `dos` is missing, errors, or behaves
like a different tool.

**Why.** The PyPI **distribution** name is `dos-kernel`, not `dos`. A bare
`pip install dos` pulls an unrelated package that squats the name. The *import*
name and the CLI are still `dos` — only the install/pin name differs.

**Fix.**

```bash
pip uninstall dos          # remove the squatter if you grabbed it
pip install dos-kernel     # the real kernel (core dep: PyYAML only)
```

Confirm you have the right one — `dos doctor` prints the distribution fact on its
`distribution` line, and `dos doctor --json` carries `"distribution":
"dos-kernel"`. Canonical statement:
[docs/guide/wire-it-in.md](guide/wire-it-in.md) and
[SECURITY.md](../SECURITY.md) "Supply chain". We never probe-import to "detect"
the squatter — the import name `dos` is ours; the guard is informational.

---

## `dos init --hooks auto` says it detected nothing

**Symptom.** `dos init --hooks auto .` fails loud with a list of runtime names
instead of wiring your agent host.

**Why.** `auto` (docs/303) probes which agent-runtime config dirs already exist
here (`.claude/`, `.cursor/`, `.codex/`, `.gemini/`, `.agents/`) plus the shell
env. On a fresh machine, or a host that keeps its config elsewhere, it finds
none — and it refuses rather than guess (the refuse-don't-guess floor).

**Fix.** Name the runtime yourself — the failure message lists the valid ones:

```bash
dos init --hooks claude-code .   # .claude/settings.json
dos init --hooks cursor .        # .cursor/hooks.json
dos init --hooks codex .         # .codex/config.toml
dos init --hooks gemini .        # .gemini/settings.json
```

The block is merged into any existing config — your other hooks/keys are
preserved. See `dos init --help` for the full host list and what each writes.

---

## `dos verify … ` says `NOT_SHIPPED (via none)` — did I get the phase wrong?

**Symptom.** `dos verify PLAN PHASE` answers `NOT_SHIPPED (via none)` and you
expected it to be shipped.

**Why — and why this is honest.** `via none` means DOS found **no positive
evidence** that the phase shipped: no run-registry row, and no commit whose
subject matches the ship-stamp grammar for `(PLAN, PHASE)`. It is the truth
syscall declining to invent a verdict — `source=none` can never masquerade as a
strong answer (that distinction is the whole point). It does **not** mean you
named the phase wrong; it means the artifact isn't in git yet.

**Fix.** Check what's actually there:

```bash
git log --oneline | grep -i PHASE      # is there a commit claiming this phase?
dos doctor --workspace .               # what stamp grammar does this workspace use?
```

If the work really landed but under a subject the grammar doesn't recognize,
re-stamp the commit subject to match (`dos doctor` shows the grammar) — never
teach the oracle to believe a `> **Status:**` sentence. The rung that answered is
explained in `dos verify --help`.

---

## A lane shows as held but nothing is running (a "phantom lease")

**Symptom.** `dos top` / `dos arbitrate` reports a lane held, but no live process
owns it.

**Why.** A lease is a durable record (a WAL write-back). If a loop died without
releasing — or a test wrote a lease into the real `.dos/` index instead of a temp
one — the record outlives its owner. (The kernel's own suite guards against this
by redirecting every test's `DISPATCH_HOME` to a tmp dir; see
[tests/conftest.py](../tests/conftest.py).)

**Fix.** Confirm liveness, then release the stale lease:

```bash
dos lease-lane live --lane LANE    # is anything actually holding it?
dos lease-lane release --lane LANE # release a confirmed-dead lease
dos top                            # re-check the board
```

If a held lane is a *live* sibling loop, that's not a phantom — racing it is the
collision the arbiter exists to prevent. Wait for it to release.

---

## Still stuck?

- `dos start-here` — the task → verb router.
- `dos <verb> --help` — every verb carries a "USE THIS WHEN" body.
- `dos doctor --check` — surfaces dead policy / config drift in your `dos.toml`.
- Open an issue: <https://github.com/anthony-chaudhary/dos-kernel/issues>.
