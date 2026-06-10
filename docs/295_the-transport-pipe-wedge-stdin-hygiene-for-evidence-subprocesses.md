# 295 — the transport-pipe wedge: stdin hygiene for evidence subprocesses

> **A verdict reader that inherits its caller's stdin can be wedged by what that
> stdin IS.** Inside a spawned `dos-mcp` server, stdin is the live MCP
> JSON-RPC pipe — and a git child holding that pipe wedges on Windows, turning
> every git-backed MCP tool into a STALLED verdict on every host. The fix is
> one line of honesty per spawn site: an evidence subprocess reads no stdin,
> so it declares `stdin=subprocess.DEVNULL`.

*Status: SHIPPED. As of 2026-06-10.*

## 0. How it was found

While executing the end-to-end Trae proof (docs/294 §3a — drive a real MCP
client at `dos-mcp` exactly as Trae's `.trae/mcp.json` would), `dos_verify`
answered the docs/282 watchdog's STALLED degrade instead of a verdict — on a
fresh one-commit scratch repo where the CLI answers in 0.3s. The same call
through this machine's live Claude-Code plugin server STALLED too, against
every workspace; ~20 wedged `dos_mcp` server processes had accumulated
machine-wide. Pure tools (`dos_check_reason`) answered instantly; every
git-shelling tool stalled. The defect predates the Trae work and affected
every MCP host on Windows — the proof just finally LOOKED.

## 1. The diagnosis (each step evidence, not narration)

1. **py-spy of a wedged server**: the tool thread sat in
   `subprocess.run → communicate() → join`, with both `_readerthread`s blocked —
   git's output pipes never reached EOF.
2. **An instrumented server** (every `subprocess.run` logged + a faulthandler
   dump): the FIRST spawn — `git -C <kernel-src> rev-parse HEAD` from
   `env_print._kernel_sha`, on the `default_config` path — never returned
   within its 10s timeout; the older wedged servers showed the same shape at
   `phase_shipped._git_log`. So the wedge is the SPAWN CLASS, not one site.
3. **The timeout made it worse, not better.** On `TimeoutExpired`, CPython's
   `run()` kills the child then calls `communicate()` again WITH NO TIMEOUT to
   drain the pipes (subprocess.py:565) — and that drain blocked until the MCP
   client hung up. The docs/282 deadline was the only thing keeping the server
   answering at all (its STALLED degrade is exactly the "transport, not
   syscall" message it claims).
4. **The flip**: re-running the identical stall harness with one change —
   every `subprocess.run` given `stdin=subprocess.DEVNULL` — turned the wedge
   into `rc=0 in 0.02s` and a real `dos_verify` verdict end-to-end. Same
   machine, same server, same git, same workspace.

Root cause, stated plainly: `subprocess.run(..., capture_output=True)` with no
`stdin=` hands the child the PARENT's stdin. In a short-lived CLI that is the
operator's console — harmless, which is why the suite (in-process pytest) and
every shell invocation never saw it. In a long-lived stdio server it is the
live transport pipe (an anyio-owned async pipe under an active reader), and a
Git-for-Windows child holding it as its stdin wedges without exiting. Minimal
repros that DON'T have a live MCP transport on stdin (plain pipes, an idle
asyncio parent) do not reproduce it — the live pipe is the load-bearing
ingredient.

## 2. The fix — declare what the child reads

Every shipped spawn site now passes an explicit stdin:

- **Evidence readers** (`env_print._kernel_sha`/`_tool_version`,
  `phase_shipped._git_log`, `oracle`'s `git show`, `git_delta`,
  `commit_audit._git`, `resume_evidence`, `health`, `timeline`, `preflight`,
  `verdict_cli`, the `memory_recall`/`ci_status`/`os_acceptance` drivers, the
  `cli.py` git reads) → `stdin=subprocess.DEVNULL`. A verdict's evidence
  subprocess reads no stdin; now it says so.
- **Deliberate consumers** stay deliberate: the LLM/similarity judges pipe
  their prompt via `input=`; `hook_binary` relays `stdin=sys.stdin` by design
  (the hook's payload arrives there); `decisions_tui`'s notifier pipes
  `input=`. Unchanged.

Pinned by `tests/test_subprocess_stdin_hygiene.py`: an AST walk over
`src/dos/**` + `src/dos_mcp/**` asserting every `subprocess.*` spawn carries
`stdin=` or `input=` — silence (inherit-the-caller) is the only thing
forbidden, so a new spawn site cannot quietly reintroduce the wedge.

## 3. What this does NOT change

- **No verdict moved.** The change is I/O plumbing at existing boundary
  readers; every classify stays pure and byte-identical.
- **The docs/282 deadline stays.** It remains the right degrade for the stalls
  this fix cannot reach (a genuinely held git lock on a hot tree).
- **The Go fast-path is untouched** — it execs git with its own pipe handling
  and was never wired through a stdio transport this way.

## 4. Provenance

Found and fixed 2026-06-10 during the docs/294 Trae end-to-end proof. Evidence
gathered with py-spy dumps, a subprocess-logging server wrapper, and a
controlled `stdin=DEVNULL` A/B on the live stall; the CPython
kill-then-drain-forever behavior read from `subprocess.py` (3.13). The fix
sites were enumerated by grep over `subprocess.(run|Popen|check_output)` and
the rule is enforced by the new hygiene test, not by review vigilance.
