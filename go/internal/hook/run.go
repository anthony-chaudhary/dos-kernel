package hook

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

// PretoolResult is the native pretool outcome the CLI dispatcher acts on. The
// native path OWNS every pretool outcome (the GHF1 fix that removed the
// delegate-for-deny stdin hazard):
//
//   - PASSTHROUGH => Stdout empty, nothing to journal.
//   - DENY / WARN => Stdout carries the exact CC dialect to emit, and the native
//     binary ALSO writes the durable OP_ENFORCE journal record itself (the Go port
//     of cli._journal_pretool_outcome) — so a deny is fast AND durably recorded
//     with zero Python. `JournalPath` is the resolved WAL path the dispatcher
//     passes to AppendEnforceRecord; `Decision` is the full outcome that record is
//     built from.
//
// Handled is always true now (the native decider serves every pretool case); the
// field is kept for the dispatcher's symmetry and a future case that genuinely must
// delegate. Any decline-to-passthrough failure mode (no stdin, bad JSON, not a PRE
// event) is an empty passthrough — nothing to emit or journal.
type PretoolResult struct {
	Handled     bool
	Stdout      string
	Decision    Decision
	JournalPath string
	Event       *Event      // the parsed event, for AppendEnforceRecord (nil on passthrough faults)
	Obs         Observation // the observability projection (docs/276) the dispatcher folds into the durable record
}

// DecidePretool runs the native PRE decider over a buffered event and returns the
// full outcome the dispatcher emits + journals. Zero Python on every pretool path.
//
// `dialect` selects the host envelope the Stdout is rendered into (docs/268): the
// decider computes the verdict vendor-blind (the CC dict in `Decision.Dialect`, kept
// for the durable journal), then `transcodeCC` renders the Stdout into the host's
// grammar — so `--dialect gemini` against a SELF_MODIFY deny emits Gemini's
// {"decision":"deny"} instead of CC bytes the host ignores (the fail-OPEN this fixes).
// An empty/`claude-code` dialect is byte-identical to before.
func DecidePretool(stdinBytes []byte, workspaceFlag, dialect string, debug io.Writer) PretoolResult {
	dbg := func(format string, a ...any) {
		if debug != nil {
			fmt.Fprintf(debug, "[dos-hook pretool] "+format+"\n", a...)
		}
	}

	if len(stdinBytes) == 0 {
		dbg("no stdin — emitting nothing")
		return PretoolResult{Handled: true}
	}
	var top map[string]any
	if err := json.Unmarshal(stdinBytes, &top); err != nil || top == nil {
		dbg("no/invalid stdin event — emitting nothing")
		return PretoolResult{Handled: true}
	}

	ev := parseEvent(top)
	if !ev.isPreEvent() {
		dbg("not a PreToolUse event — passthrough")
		return PretoolResult{Handled: true}
	}

	wsArg := workspaceFlag
	if wsArg == "" && ev.Cwd != "" {
		wsArg = ev.Cwd
	}
	workspace := ResolveWorkspace(wsArg)
	journalPath := JournalPath(workspace)

	in := Inputs{
		LiveLeases:   LiveLeasesFromWAL(journalPath),
		RuntimeFiles: ExistingRuntimeFiles(workspace),
		// An interactive operator turn carries NONE of the loop-context envs the
		// dispatcher/cron set (DOS_LOOP / CID_RUN_ID / DISPATCH_LOOP_TS). Their
		// absence is the same loop-vs-interactive signal the marker/stop verbs already
		// use. When this is an operator session, Decide softens a contention-only
		// disjointness DENY to a WARN (the human owns their blast radius); a loop keeps
		// the hard deny.
		OperatorSession: os.Getenv("DOS_LOOP") == "" &&
			os.Getenv("CID_RUN_ID") == "" &&
			os.Getenv("DISPATCH_LOOP_TS") == "",
	}
	d := Decide(ev, in)
	dbg("rung=%s decision=%s reason_class=%s dialect=%s", d.Rung, d.DecisionTag, d.ReasonClass, dialect)

	// Count the verdict's dimensions in-process (the durable record + latency + exit
	// are the dispatcher's). Build the observability projection off the same Decision.
	recordPretool(d, dialect)
	treeKnown := d.TreeKnown
	return PretoolResult{
		Handled:     true,
		Stdout:      d.RenderAs(dialect),
		Decision:    d,
		JournalPath: journalPath,
		Event:       ev,
		Obs: Observation{
			Outcome:     d.DecisionTag,
			Rung:        d.Rung,
			ReasonClass: d.ReasonClass,
			Dialect:     nonEmpty(dialect, "claude-code"),
			TreeKnown:   &treeKnown,
		},
	}
}


// PosttoolResult is the native posttool outcome — Stdout is the WARN dialect to
// emit (empty for ADVANCING / any decline). PostToolUse can never block, so there
// is no exit-code lever; it always exits 0. Handled is always true (the native path
// owns every posttool outcome: it reads+appends the stream and emits the warn).
//
// Obs carries the observability projection (docs/276) the dispatcher folds into the
// durable record — the stream verdict state + whether a warn fired. Zero-valued on a
// decline (no stream to classify), which records as a passthrough observation.
type PosttoolResult struct {
	Handled bool
	Stdout  string
	Obs     Observation
}

// DecidePosttool runs the native PostToolUse decider — the Go port of
// cli.cmd_hook_posttool. It builds the StreamStep, appends it to the session's
// accumulating stream (the boundary I/O), replays + classifies the trailing run,
// and returns the WARN dialect on REPEATING/STALLED. Any failure mode (no stdin,
// bad JSON, no tool_name, no session_id, an accumulator I/O fault) is an empty
// advisory passthrough — never blocks, never errors a turn.
//
// Replay-then-classify-then-append-ONCE (the docs/179 firing-record order): read
// prior, step_index = len(prior), classify (prior + this step), append once
// stamping verdict_state/run_id when it fired. Classifying over (prior+step) is
// identical to re-reading the appended stream, so the verdict is unchanged — this
// only makes the firing a durable fact.
func DecidePosttool(stdinBytes []byte, workspaceFlag, sessionFlag, dialect string, debug io.Writer) PosttoolResult {
	dbg := func(format string, a ...any) {
		if debug != nil {
			fmt.Fprintf(debug, "[dos-hook posttool] "+format+"\n", a...)
		}
	}
	if len(stdinBytes) == 0 {
		return PosttoolResult{Handled: true}
	}
	var top map[string]any
	if err := json.Unmarshal(stdinBytes, &top); err != nil || top == nil {
		dbg("no/invalid stdin event — emitting nothing")
		return PosttoolResult{Handled: true}
	}

	step, ok := stepFromEvent(top)
	if !ok {
		dbg("event has no tool_name — nothing to record")
		return PosttoolResult{Handled: true}
	}

	// Session identity: --session-id flag › the event's session_id. No id → no
	// accumulator (an unkeyed stream cannot accumulate a per-session repeat run).
	sessionID := sessionFlag
	if sessionID == "" {
		if s, ok := top["session_id"].(string); ok {
			sessionID = s
		}
	}
	if strings.TrimSpace(sessionID) == "" {
		dbg("event has no session_id — no accumulator without an identity")
		return PosttoolResult{Handled: true}
	}

	// Workspace: --workspace › the event's cwd › cwd.
	wsArg := workspaceFlag
	if wsArg == "" {
		if c, ok := top["cwd"].(string); ok {
			wsArg = c
		}
	}
	workspace := ResolveWorkspace(wsArg)
	streamPath := streamPathFor(workspace, sessionID)
	if streamPath == "" {
		dbg("session_id sanitizes to empty — no accumulator")
		return PosttoolResult{Handled: true}
	}

	prior := readStream(streamPath)
	stepIndex := len(prior)
	allSteps := append(append([]streamStep(nil), prior...), step)
	verdict := classifyStream(allSteps)

	fired := verdict.state == "REPEATING" || verdict.state == "STALLED"
	runID := ""
	verdictState := ""
	if fired {
		runID = os.Getenv("CID_RUN_ID")
		verdictState = verdict.state
	}
	appendStep(streamPath, step, stepIndex, verdictState, runID)

	dbg("verdict=%s repeat_run=%d step_index=%d warn=%v dialect=%s", verdict.state, verdict.repeatRun, stepIndex, fired, dialect)
	payload := postWarnPayload(verdict)
	if payload == nil {
		recordPosttool(verdict.state, false)
		return PosttoolResult{Handled: true, Obs: Observation{Outcome: "passthrough", StreamState: verdict.state}}
	}
	// Transcode the canonical CC warn dict into the host's grammar (docs/268). A
	// posttool WARN is advisory (never blocks), so for gemini/antigravity/cursor it
	// re-surfaces the same fact in the shape each host feeds back to the model.
	out := transcodeCC(payload, dialect)
	if out == nil {
		recordPosttool(verdict.state, false)
		return PosttoolResult{Handled: true, Obs: Observation{Outcome: "passthrough", StreamState: verdict.state}}
	}
	recordPosttool(verdict.state, true)
	return PosttoolResult{Handled: true, Stdout: pyJSONDumps(out), Obs: Observation{Outcome: "warn", StreamState: verdict.state}}
}

// parseEvent decodes the typed fields off the top-level event map, retaining the
// raw map for the structural PRE guard.
func parseEvent(top map[string]any) *Event {
	e := &Event{raw: top}
	if s, ok := top["hook_event_name"].(string); ok {
		e.HookEventName = s
	}
	if s, ok := top["session_id"].(string); ok {
		e.SessionID = s
	}
	if s, ok := top["cwd"].(string); ok {
		e.Cwd = s
	}
	if s, ok := top["tool_name"].(string); ok {
		e.ToolName = s
	}
	if m, ok := top["tool_input"].(map[string]any); ok {
		e.ToolInput = m
	} else {
		e.ToolInput = map[string]any{}
	}
	return e
}
