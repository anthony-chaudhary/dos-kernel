package hook

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
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
type phaseTimer struct {
	last   time.Time
	phases map[string]float64
}

func newPhaseTimer() *phaseTimer { return &phaseTimer{last: time.Now(), phases: map[string]float64{}} }
func (t *phaseTimer) mark(name string) {
	now := time.Now()
	t.phases[name] += float64(now.Sub(t.last).Microseconds()) / 1000
	t.last = now
}
func (t *phaseTimer) observation(obs Observation) Observation {
	obs.PhaseMS = t.phases
	return obs
}

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
	timing := newPhaseTimer()
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

	timing.mark("parse")
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
	callShape, callShapeErr := ReadCallShape(workspace)
	if callShapeErr != nil {
		d := callShapeConfigDeny(ev, callShapeErr)
		dbg("call_shape config error: %v", callShapeErr)
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

	in := Inputs{
		LiveLeases:   LiveLeasesFromWAL(journalPath),
		RuntimeFiles: ExistingRuntimeFiles(workspace),
		CallShape:    callShape,
		// docs/296 — read the operator's armed SELF_MODIFY window at the boundary
		// (fail-closed; nil when disarmed/absent) and stamp the clock, so the pure
		// decider can dispose a SELF_MODIFY deny to an allow-with-note while the
		// window is open. This is the parity fix: the native fast-path now honors an
		// armed `dos override arm` window byte-identically to the Python path.
		OverrideFacts: ReadOverride(workspace),
		Now:           time.Now().UTC(),
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
	// issue #188 — is this a dispatched subagent whose lineage ties it to a live
	// lease holder AND whose write is CONTAINED in that ancestor's tree? Computed here
	// at the boundary (it reads the env + the folded leases) and frozen into the pure
	// decider, the same I/O-at-the-edge discipline as OperatorSession above.
	in.SubagentInLane = subagentInLane(ev, in.LiveLeases)
	timing.mark("inputs")
	d := Decide(ev, in)
	timing.mark("evaluate")
	dbg("rung=%s decision=%s reason_class=%s dialect=%s", d.Rung, d.DecisionTag, d.ReasonClass, dialect)

	// Count the verdict's dimensions in-process (the durable record + latency + exit
	// are the dispatcher's). Build the observability projection off the same Decision.
	recordPretool(d, dialect)
	treeKnown := d.TreeKnown
	stdout := d.RenderAs(dialect)
	timing.mark("serialize")
	return PretoolResult{
		Handled:     true,
		Stdout:      stdout,
		Decision:    d,
		JournalPath: journalPath,
		Event:       ev,
		Obs: timing.observation(Observation{
			Outcome:     d.DecisionTag,
			Rung:        d.Rung,
			ReasonClass: d.ReasonClass,
			Dialect:     nonEmpty(dialect, "claude-code"),
			TreeKnown:   &treeKnown,
		}),
	}
}

// subagentInLane resolves issue #188 at the boundary: is THIS call a dispatched
// subagent whose lineage (`CID_PARENT_ID` / `CID_ROOT_ID`, NOT its own `CID_RUN_ID`)
// ties it to the holder of a live lease, AND whose write footprint is CONTAINED in that
// ancestor's tree? A subagent inherits its parent's lineage but mints its own
// `CID_RUN_ID`, so without this it reads as a sibling collision against the parent's
// held lane. Returns true ONLY for a mutating call with a KNOWN, non-empty, CONTAINED
// write tree — an ESCAPE (a write outside the ancestor's tree) returns false so the
// hard deny stands. Twin of `pretool_sensor`'s lineage self-lease resolution + the
// gate-OFF containment guard. PURE over the env + the already-folded leases.
func subagentInLane(ev *Event, leases []lease) bool {
	if !ev.isMutatingTool() {
		return false
	}
	tree, known := ev.treeFromEvent()
	if !known || len(tree) == 0 {
		return false // unknown / empty footprint — never softened on this rung
	}
	own := os.Getenv("CID_RUN_ID")
	if v := os.Getenv("DISPATCH_RUN_ID"); own == "" {
		own = v
	}
	// The ancestor identity set: parent + root ids, excluding this run's OWN id (an
	// own-lease write is the apply-gate's / disjointness's concern, not this rung).
	ancestors := map[string]bool{}
	for _, name := range []string{"CID_PARENT_ID", "CID_ROOT_ID"} {
		if v := os.Getenv(name); v != "" && v != own {
			ancestors[v] = true
		}
	}
	if len(ancestors) == 0 {
		return false
	}
	for _, lz := range leases {
		if lz.runID != "" && ancestors[lz.runID] && writeContainedIn(tree, lz.tree) {
			return true
		}
	}
	return false
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
	timing := newPhaseTimer()
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

	timing.mark("parse")
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

	timing.mark("inputs")
	prior := readStream(streamPath)
	stepIndex := len(prior)
	allSteps := append(append([]streamStep(nil), prior...), step)
	verdict := classifyStream(allSteps)
	timing.mark("evaluate")

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
		return PosttoolResult{Handled: true, Obs: timing.observation(Observation{Outcome: "passthrough", StreamState: verdict.state})}
	}
	// Transcode the canonical CC warn dict into the host's grammar (docs/268). A
	// posttool WARN is advisory (never blocks), so for gemini/antigravity/cursor it
	// re-surfaces the same fact in the shape each host feeds back to the model.
	out := transcodeCC(payload, dialect)
	if out == nil {
		recordPosttool(verdict.state, false)
		return PosttoolResult{Handled: true, Obs: timing.observation(Observation{Outcome: "passthrough", StreamState: verdict.state})}
	}
	recordPosttool(verdict.state, true)
	stdout := pyJSONDumps(out)
	timing.mark("serialize")
	return PosttoolResult{Handled: true, Stdout: stdout, Obs: timing.observation(Observation{Outcome: "warn", StreamState: verdict.state})}
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
	if s, ok := top["tool_use_id"].(string); ok {
		e.ToolUseID = s
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
