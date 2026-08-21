// Command dos-hook is the native fast-path for the DOS Claude-Code hooks
// (docs/125 GHF). The plugin's hooks.json calls it on EVERY tool call; it serves
// the per-call hook decision in ~10 ms instead of the ~0.3–0.8 s a
// `python -m dos.cli hook …` invocation costs, and it is byte-identical to that
// Python verb on the gated decision projection (the docs/124 parity contract).
//
// The fallback discipline (docs/100) is realized at the SHELL, not inside this
// binary: the binary OWNS the common fast path and exits 0; for anything it does
// not (yet) serve natively it exits with the DELEGATE sentinel, and the hooks.json
// `dos-hook … || python -m dos.cli hook …` runs the always-available Python verb.
// Keeping the fallback at the shell (rather than spawning Python from Go) means the
// binary never manages a subprocess on the hot path — it is a pure, fast decider,
// and a missing binary (exit 127) ALSO triggers the same `||`, so a machine without
// the binary degrades to today's Python behavior with no wiring change.
//
// Exit codes (consumed by the hooks.json `||`):
//
//	0  — native outcome OWNED. Either a PASSTHROUGH (emit nothing) or a native
//	     deny/warn already EMITTED on stdout. The shell `||` does NOT run Python.
//	3  — DELEGATE. The native path declines (flag off, a non-native verb, a
//	     non-passthrough outcome whose durable OP_ENFORCE journal record is not yet
//	     ported — GHF2). Emits nothing; the shell `||` runs the Python verb, which
//	     re-decides identically and writes the record.
//
// A panic anywhere is recovered to exit 0 with nothing emitted (the hook fail-safe:
// a Go crash can NEVER break a turn). Note exit 3 is deliberately NOT the deny
// mechanism — DOS denies via the JSON `permissionDecision: deny` dialect on stdout
// at exit 0 (the CC contract), never via a process exit code.
//
//	dos-hook pretool   [--workspace DIR] [--debug]
//	dos-hook posttool  [--workspace DIR] [--session-id ID] [--debug]
//	dos-hook marker    [--workspace DIR] [--session-id ID] [--max-markers N] [--debug]
//	dos-hook stop      [--workspace DIR] [--last-turns N] [--strict] [--force] [--json] [--plan P --phase PH]
package main

import (
	"encoding/json"
	"io"
	"os"
	"strconv"
	"time"

	"github.com/anthony-chaudhary/dos-kernel/go/internal/hook"
)

// exitDelegate is the sentinel that tells the hooks.json `||` to run the Python
// verb. Any non-zero, non-2 code works (CC treats it as non-blocking); 3 is chosen
// to be distinct from a generic shell error (1) and from the deny code 2 (which
// DOS never uses — it denies via stdout JSON).
const exitDelegate = 3

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) (code int) {
	// The observability spine (docs/276). The dispatcher owns the wall clock, the
	// per-verb invocation + exit counters, the panic-recovered count, and the durable
	// per-call observation — all stamped UNIFORMLY here so every verb (and every
	// decline/delegate path inside it) is counted exactly once, with no per-case
	// repetition. The decider fills obs.Outcome + its verb-specific fields; this
	// finalizer adds verb/exit/latency/run_id and persists.
	start := time.Now()
	var setupFinished time.Time
	var verb string
	var obs hook.Observation
	var obsWorkspace string
	var debug bool
	panicked := false

	// The outermost fail-safe: a panic degrades to "emit nothing, exit 0" — a Go
	// crash must never break a turn, and must never DELEGATE either (a delegate on
	// a crash would silently double the work; exit 0 with nothing is the honest
	// safe floor, matching cli.py's emit-nothing/exit-0 wrapper). A recovered panic
	// is itself a counted, durably-recorded observation (the fail-safe firing is one
	// of the most important things to surface).
	defer func() {
		if r := recover(); r != nil {
			_, _ = io.WriteString(stderr, "[dos-hook] recovered panic — failing safe (exit 0)\n")
			code = 0
			panicked = true
			obs.Outcome = "panic-recovered"
			obs.PanicRecovered = true
			if verb != "" {
				hook.RecordPanicRecovered(verb)
			}
		}
		finalizeObservation(verb, obsWorkspace, start, setupFinished, code, debug, panicked, &obs)
	}()

	if len(args) == 0 {
		_, _ = io.WriteString(stderr, "usage: dos-hook <pretool|posttool|marker|stop|stats> [--workspace DIR] [--session-id ID] [--max-markers N] [--debug]\n")
		return 0
	}

	verb = args[0]
	workspace, sessionID, maxMarkers, dialect, debugFlag, loopFlag := parseFlags(args[1:])
	debug = debugFlag
	obsWorkspace = hook.ResolveWorkspace(workspace)
	var dbgW io.Writer
	if debug {
		dbgW = stderr
	}

	// The flag gates the native path. GHF4 flips the default to NATIVE-ON: if you are
	// invoking the `dos-hook` binary at all (the plugin's hooks.json does), you want
	// the fast path — so native is the default and `DOS_HOOK_NATIVE=0` is the explicit
	// opt-OUT (delegate everything to Python, today's behavior byte-for-byte via the
	// shell `||`). This is the docs/125 GHF4 "flag becomes default-on where the binary
	// is present" — realized as default-on whenever the binary runs. (GHF1–GHF3 used
	// opt-IN `=1`; both `1` and unset now select native, only `0` opts out.)
	setupFinished = time.Now()

	if os.Getenv("DOS_HOOK_NATIVE") == "0" {
		if debug {
			_, _ = io.WriteString(stderr, "[dos-hook] DOS_HOOK_NATIVE=0 — delegating to Python (opt-out)\n")
		}
		obs.Outcome = "delegate"
		hook.RecordDelegate(verb, "native-off")
		return exitDelegate
	}

	switch verb {
	case "stats":
		// The surfacing fold (docs/276 Part 3) — a read-only projection over the durable
		// observation log. Takes no lease, launches nothing, mutates no state. Renders the
		// aggregate (human or --json). Always exit 0. NOT itself logged as an observation
		// (a read-only fold must not grow the log it folds — it would count its own reads).
		asJSON, since := parseStatsFlags(args[1:])
		res := hook.DecideStats(workspace, asJSON, since, dbgW)
		if res.Stdout != "" {
			_, _ = io.WriteString(stdout, res.Stdout+"\n")
		}
		obs.Outcome = "stats" // marks this verb as not-durably-logged in finalizeObservation
		return 0
	case "pretool":
		// Reading stdin here is safe because the native path now OWNS every pretool
		// outcome (passthrough emits nothing; deny/warn emit the dialect AND write
		// the durable OP_ENFORCE record themselves) — it never delegates, so it never
		// has to leave stdin intact for a downstream Python verb. (Delegating after
		// consuming stdin is exactly the bug the GHF1 native-journal port fixed.)
		stdinBytes, _ := io.ReadAll(stdin)
		res := hook.DecidePretool(stdinBytes, workspace, dialect, dbgW)
		stampLifecycleIdentity(&res.Obs, stdinBytes, obsWorkspace)
		obs = res.Obs
		if res.Stdout != "" {
			_, _ = io.WriteString(stdout, res.Stdout+"\n")
		}
		// Journal a non-passthrough outcome as a durable OP_ENFORCE record (the Go
		// port of cli._journal_pretool_outcome). Best-effort: a write fault never
		// changes the emitted dialect (the deny still stands).
		if res.Event != nil && res.Decision.DecisionTag != "passthrough" {
			hook.AppendEnforceRecord(res.JournalPath, res.Event, res.Decision)
		}
		return 0
	case "posttool":
		// Native (GHF2). PostToolUse can never block, so the native path owns every
		// outcome (emit the WARN dialect or nothing) and always exits 0 — it never
		// delegates, so consuming stdin here is safe.
		stdinBytes, _ := io.ReadAll(stdin)
		res := hook.DecidePosttool(stdinBytes, workspace, sessionID, dialect, dbgW)
		stampLifecycleIdentity(&res.Obs, stdinBytes, obsWorkspace)
		obs = res.Obs
		if res.Stdout != "" {
			_, _ = io.WriteString(stdout, res.Stdout+"\n")
		}
		return 0
	case "marker":
		// Native (GHF5 — the keep-alive wait-marker budget). The marker path OWNS its
		// outcome and NEVER delegates: it reads the session's durable marker tally to
		// decide, and delegating after that read would let the Python `||` fallback ALSO
		// run and append a SECOND marker record (double-counting the budget). So like
		// pretool/posttool it reads stdin, owns the outcome (emit the block dialect that
		// holds the turn open, or nothing = allow stop), and always exits 0. The marker
		// record write lives inside DecideMarker (the boundary I/O), so there is nothing
		// left for Python to do.
		stdinBytes, _ := io.ReadAll(stdin)
		// Arm the budget only inside a keep-alive loop (docs/274): the --loop flag, OR a
		// loop-scoping env (DOS_LOOP, or a non-empty CID_RUN_ID the dispatcher sets).
		// Without any of these a Stop is treated as an ordinary finished turn (allow stop),
		// so an unscoped binding can never force keep-alive turns on every interactive turn.
		runID := os.Getenv("CID_RUN_ID")
		loop := loopFlag || os.Getenv("DOS_LOOP") != "" || runID != ""
		res := hook.DecideMarker(stdinBytes, workspace, sessionID, maxMarkers, runID, loop, dbgW)
		obs = res.Obs
		if res.Stdout != "" {
			_, _ = io.WriteString(stdout, res.Stdout+"\n")
		}
		return 0
	case "stop":
		// Native (docs/125 native stop): the verify-on-stop binding. The native
		// decider OWNS the zero-flag default-dialect path (extract claims, verify each
		// against git's direct grep rung, block on a NOT_SHIPPED confident claim) and
		// DELEGATES (exit 3 → the `||` Python) on any advanced flag or whenever verify
		// ABSTAINS (a non-generic convention / an unported rung might fire). The
		// stop-specific flags (--last-turns/--strict/--force/--json/--plan/--phase) are
		// scanned here from the raw args (kept OUT of the shared parseFlags so this
		// edit stays inside the stop case — the disjoint-lane discipline).
		opts := parseStopFlags(args[1:])
		opts.WorkspaceFlag = workspace
		// An advanced flag → delegate WITHOUT reading stdin, so the `||` Python gets
		// clean stdin (the GHF1 stdin-hazard discipline: never consume stdin on a path
		// that will delegate a non-passthrough Python decision).
		if opts.JSON || opts.Strict || opts.Force || opts.Plan != "" || opts.Phase != "" {
			if debug {
				_, _ = io.WriteString(stderr, "[dos-hook] stop: advanced flag set — delegating to Python\n")
			}
			obs.Outcome = "delegate"
			hook.RecordDelegate("stop", "advanced-flag")
			return exitDelegate
		}
		// Native default path: read stdin and decide. If the decider declines (verify
		// abstained), DELEGATE. The delegate has consumed stdin, but an abstaining stop
		// emits NOTHING, so the `||` Python re-reads the event CC re-supplies to its arm
		// — at worst a re-decide, never a double-deny (the pretool hazard does not apply
		// to a path whose decline emits nothing).
		stdinBytes, _ := io.ReadAll(stdin)
		res := hook.DecideStop(stdinBytes, opts, nil, dbgW)
		obs = res.Obs
		if !res.Handled {
			if debug {
				_, _ = io.WriteString(stderr, "[dos-hook] stop: native decider declined — delegating to Python\n")
			}
			hook.RecordDelegate("stop", "verify-abstain")
			return exitDelegate
		}
		if res.Stdout != "" {
			_, _ = io.WriteString(stdout, res.Stdout+"\n")
		}
		return 0
	default:
		// An unknown verb: delegate (the Python CLI will report it), never break.
		obs.Outcome = "delegate"
		hook.RecordDelegate(verb, "unknown-verb")
		return exitDelegate
	}
}

// stampLifecycleIdentity carries the host's stable join keys from the untouched
// hook envelope into the terminal observation. Codex tool_use_id is the rollout
// function_call call_id; preserving it makes parallel and out-of-order hooks
// independently joinable without timestamp heuristics.
func stampLifecycleIdentity(obs *hook.Observation, stdinBytes []byte, workspace string) {
	var envelope struct {
		ToolUseID string `json:"tool_use_id"`
		SessionID string `json:"session_id"`
	}
	if json.Unmarshal(stdinBytes, &envelope) != nil {
		return
	}
	obs.CallID = envelope.ToolUseID
	obs.SessionID = envelope.SessionID
	obs.Workspace = workspace
}

// finalizeObservation stamps the per-invocation fields the dispatcher owns (verb,
// exit, latency, run_id) onto the decider's partial observation, counts the uniform
// in-process dimensions (one invocation + one exit per call), and persists the
// durable record. Called from the single deferred finalizer so EVERY path — every
// verb, every decline, every delegate, a recovered panic — is counted exactly once.
//
// The `stats` verb is the one exclusion: it is a read-only fold over the log, so
// logging it would grow the very log it reads (and count its own reads). An empty
// verb (no args) is also skipped — there was no invocation to record.
func finalizeObservation(verb, workspace string, start, setupFinished time.Time, code int, debug, panicked bool, obs *hook.Observation) {
	if verb == "" || verb == "stats" {
		return
	}
	elapsed := time.Since(start)
	obs.Verb = verb
	obs.ExitCode = code
	obs.LatencyMs = float64(elapsed.Microseconds()) / 1000.0
	if !setupFinished.IsZero() {
		if obs.PhaseMS == nil {
			obs.PhaseMS = map[string]float64{}
		}
		obs.PhaseMS["setup"] = float64(setupFinished.Sub(start).Microseconds()) / 1000.0
		measured := 0.0
		for _, value := range obs.PhaseMS {
			measured += value
		}
		if residual := obs.LatencyMs - measured; residual > 0 {
			obs.PhaseMS["runtime"] = residual
		}
	}
	obs.RunID = os.Getenv("CID_RUN_ID")
	obs.Workspace = workspace
	obs.Profile = os.Getenv("CODEX_HOME")
	if panicked || code != 0 {
		obs.PhaseState = "failed"
	} else if obs.CallID == "" && (verb == "pretool" || verb == "posttool") {
		obs.PhaseState = "skipped"
	} else if verb == "pretool" || verb == "posttool" {
		obs.PhaseState = "succeeded"
	}

	// Uniform in-process counters: one invocation + one exit per call, plus the
	// per-verb latency histogram. (The verdict-specific dimensions were already
	// counted inside the decider.)
	hook.RecordInvocation(verb, elapsed.Nanoseconds())
	hook.RecordExitCode(verb, code)

	// Persist the durable per-call record (best-effort, fail-soft, gated).
	hook.RecordObservationDurable(workspace, debug, *obs)
}

// parseStatsFlags scans the stats-verb args for --json and --since DURATION (kept
// tiny and local — the stats verb's only flags beyond the shared --workspace). An
// unparseable/absent --since is "" (no window), handled downstream.
func parseStatsFlags(args []string) (asJSON bool, since string) {
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--json":
			asJSON = true
		case "--since":
			if i+1 < len(args) {
				since = args[i+1]
				i++
			}
		default:
			if v, ok := cutPrefix(args[i], "--since="); ok {
				since = v
			}
		}
	}
	return asJSON, since
}

// parseFlags is a tiny argument scanner for the flags the hook verbs accept
// (--workspace, --session-id, --max-markers, --dialect, --debug). An unknown flag is
// ignored (never fatal): a hook must not die on an unexpected argument from a host's
// hooks.json. maxMarkers is 0 when unset, so the caller applies the verb's default
// (the `marker` verb defaults to 4 — the /dispatch-loop SKILL's per-run prose cap).
// dialect is "" when unset, so the caller emits the default Claude-Code envelope
// (transcodeCC treats ""/claude-code identically — byte-for-byte today's behavior).
func parseFlags(args []string) (workspace, sessionID string, maxMarkers int, dialect string, debug, loop bool) {
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--workspace", "-w":
			if i+1 < len(args) {
				workspace = args[i+1]
				i++
			}
		case "--session-id":
			if i+1 < len(args) {
				sessionID = args[i+1]
				i++
			}
		case "--max-markers":
			if i+1 < len(args) {
				if n, err := strconv.Atoi(args[i+1]); err == nil {
					maxMarkers = n
				}
				i++
			}
		case "--dialect":
			if i+1 < len(args) {
				dialect = args[i+1]
				i++
			}
		case "--debug":
			debug = true
		case "--loop":
			// Arms the marker (Stop) budget: assert this Stop is a keep-alive poll inside
			// a headless loop (docs/274). Ignored by the other verbs. Without it (and
			// without DOS_LOOP / CID_RUN_ID) the marker hook treats a Stop as an ordinary
			// finished turn and allows it.
			loop = true
		default:
			if v, ok := cutPrefix(args[i], "--workspace="); ok {
				workspace = v
			} else if v, ok := cutPrefix(args[i], "--session-id="); ok {
				sessionID = v
			} else if v, ok := cutPrefix(args[i], "--max-markers="); ok {
				if n, err := strconv.Atoi(v); err == nil {
					maxMarkers = n
				}
			} else if v, ok := cutPrefix(args[i], "--dialect="); ok {
				dialect = v
			}
		}
	}
	return workspace, sessionID, maxMarkers, dialect, debug, loop
}

func cutPrefix(s, prefix string) (string, bool) {
	if len(s) >= len(prefix) && s[:len(prefix)] == prefix {
		return s[len(prefix):], true
	}
	return "", false
}

// parseStopFlags scans the `stop`-verb-specific flags off the raw args, mirroring the
// argparse surface of cli's `dos hook stop` (--plan/--phase/--last-turns/--strict/
// --force/--json). It does NOT re-parse --workspace (the caller threads the shared
// parseFlags result into opts.WorkspaceFlag). LastTurns defaults to 1 (the cli
// default). An unknown flag is ignored (never fatal — a hook must not die on an
// unexpected argument). Kept separate from parseFlags so the native-stop change stays
// inside the stop case and does not alter the shared scanner's signature.
func parseStopFlags(args []string) hook.StopOptions {
	opts := hook.StopOptions{LastTurns: 1}
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--plan":
			if i+1 < len(args) {
				opts.Plan = args[i+1]
				i++
			}
		case "--phase":
			if i+1 < len(args) {
				opts.Phase = args[i+1]
				i++
			}
		case "--last-turns":
			if i+1 < len(args) {
				if n, err := strconv.Atoi(args[i+1]); err == nil {
					opts.LastTurns = n
				}
				i++
			}
		case "--strict":
			opts.Strict = true
		case "--force":
			opts.Force = true
		case "--json":
			opts.JSON = true
		default:
			if v, ok := cutPrefix(args[i], "--plan="); ok {
				opts.Plan = v
			} else if v, ok := cutPrefix(args[i], "--phase="); ok {
				opts.Phase = v
			} else if v, ok := cutPrefix(args[i], "--last-turns="); ok {
				if n, err := strconv.Atoi(v); err == nil {
					opts.LastTurns = n
				}
			}
		}
	}
	return opts
}
