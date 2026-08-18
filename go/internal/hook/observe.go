package hook

// observe.go — the durable per-invocation observation record (docs/276 Part 2) plus
// the typed "record this verdict" helpers each decider feeds (Part 1's front door).
//
// A hook is a one-shot process: the in-process registry (metrics.go) dies with it.
// So each invocation ALSO appends ONE schema-tagged JSONL line to the workspace's
// observation log — the WAL discipline (lane_journal) applied to telemetry. The many
// one-shot binaries of a fleet all append to per-workspace logs; `dos-hook stats`
// folds them. This is the cross-process aggregate substrate the registry alone
// cannot be.
//
// FAIL-SOFT, ADVISORY (docs/99): a write fault NEVER changes the emitted dialect or
// the exit code — telemetry about a decision is strictly downstream of the decision.
// Same best-effort recover() + canonical pyJSONDumpsWAL byte-grammar + fsync as the
// existing journal writers, so a torn tail is "didn't happen," never a corruption
// that derails a turn.
//
// GATING: the in-process counters always run (free). Only the durable append is
// gated — DOS_HOOK_METRICS=0 opts OUT (symmetry with DOS_HOOK_NATIVE=0). A --debug
// run always logs regardless (you asked for the trace). The default is ON: an
// operator who installs the binary wants to see what it did.

import (
	"os"
	"path/filepath"
)

const (
	obsSchemaFamily  = "hook-observation"
	obsSchemaVersion = 1
	obsDirname       = "metrics"
	obsLogBasename   = "observations.jsonl"
)

// Observation is the full forensic projection of one hook invocation — the same
// fields the in-process counters carry, but as ONE durable record so the verdict is
// reconstructable per-call (not only in aggregate). Only the verb + ts + outcome are
// always present; the verb-specific fields are filled by the matching recorder and
// omitted (zero) otherwise, so a record stays small and self-describing.
type Observation struct {
	Verb       string // pretool|posttool|marker|stop|stats
	Outcome    string // a short verb-specific tag: deny|warn|passthrough|allow|refuse|block|let|delegate|…
	ExitCode   int    // 0 OWNED / 3 DELEGATE
	LatencyMs  float64
	RunID      string // CID_RUN_ID join key (the correlation spine), "" when unset
	CallID     string // host tool-use/call identifier
	SessionID  string // host session/thread identifier
	Workspace  string // resolved workspace that owns the observation store
	Profile    string // active host profile (CODEX_HOME when present)
	PhaseState string // succeeded|failed|skipped|disabled

	// pretool
	Rung        string
	ReasonClass string
	Dialect     string
	TreeKnown   *bool

	// posttool
	StreamState string

	// marker
	MarkerCount int
	MaxMarkers  int

	// stop / verify
	ClaimsSeen   int
	VerifySource string
	BlockedPlan  string
	BlockedPhase string

	// fail-safe
	PanicRecovered bool
}

// obsLogPath is `.dos/metrics/observations.jsonl` under the served workspace, or ""
// if the workspace is empty (no root → nowhere to write; degrade to no-log). Rides
// the same .dos layout as the streams/markers accumulators.
func obsLogPath(workspace string) string {
	if workspace == "" {
		return ""
	}
	return filepath.Join(workspace, ".dos", obsDirname, obsLogBasename)
}

// metricsDurableEnabled reports whether the durable append should run: ON by default,
// OFF only on the explicit DOS_HOOK_METRICS=0 opt-out, ALWAYS on under --debug
// (debug != nil), since a trace run is asking to see everything.
func metricsDurableEnabled(debug bool) bool {
	if debug {
		return true
	}
	return os.Getenv("DOS_HOOK_METRICS") != "0"
}

// recordObservation appends one Observation to the workspace log — best-effort,
// fail-soft, schema-tagged, canonical-JSON, fsync'd. The caller has already decided
// + emitted; this only records. A nil/empty workspace or the opt-out simply skips.
func recordObservation(workspace string, debug bool, obs Observation) {
	defer func() { _ = recover() }() // a telemetry write fault never alters a verdict
	if !metricsDurableEnabled(debug) {
		return
	}
	path := obsLogPath(workspace)
	if path == "" {
		return
	}
	entry := obs.toEntry()
	line := pyJSONDumpsWAL(entry) + "\n"
	if dir := dirOf(path); dir != "" {
		_ = os.MkdirAll(dir, 0o755)
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	if _, err := f.WriteString(line); err == nil {
		_ = f.Sync()
	}
}

// toEntry renders the Observation into the schema-tagged map the WAL grammar
// serializes. Additive-optional fields are written ONLY when set (so a bare record
// stays small and the schema version never has to bump for an absent field) — the
// same convention the marker writer uses.
func (o Observation) toEntry() map[string]any {
	e := map[string]any{
		"schema":  map[string]any{"family": obsSchemaFamily, "version": obsSchemaVersion},
		"op":      "OBSERVE",
		"ts":      journalNowISO(),
		"verb":    o.Verb,
		"outcome": o.Outcome,
		"exit":    o.ExitCode,
	}
	// Latency is always meaningful (>= 0); round to microsecond-ish for stability.
	e["latency_ms"] = o.LatencyMs
	if o.RunID != "" {
		e["run_id"] = o.RunID
	}
	if o.CallID != "" {
		e["call_id"] = o.CallID
	}
	if o.SessionID != "" {
		e["session_id"] = o.SessionID
	}
	if o.Workspace != "" {
		e["workspace"] = o.Workspace
	}
	if o.Profile != "" {
		e["profile"] = o.Profile
	}
	if o.PhaseState != "" {
		e["phase_state"] = o.PhaseState
	}
	if o.Rung != "" {
		e["rung"] = o.Rung
	}
	if o.ReasonClass != "" {
		e["reason_class"] = o.ReasonClass
	}
	if o.Dialect != "" {
		e["dialect"] = o.Dialect
	}
	if o.TreeKnown != nil {
		e["tree_known"] = *o.TreeKnown
	}
	if o.StreamState != "" {
		e["stream_state"] = o.StreamState
	}
	if o.MarkerCount != 0 || o.MaxMarkers != 0 {
		e["marker_count"] = o.MarkerCount
		e["max_markers"] = o.MaxMarkers
	}
	if o.ClaimsSeen != 0 {
		e["claims_seen"] = o.ClaimsSeen
	}
	if o.VerifySource != "" {
		e["verify_source"] = o.VerifySource
	}
	if o.BlockedPlan != "" {
		e["blocked_plan"] = o.BlockedPlan
		e["blocked_phase"] = o.BlockedPhase
	}
	if o.PanicRecovered {
		e["panic_recovered"] = true
	}
	return e
}

// ---- the typed recorders: count the in-process dimensions for one verdict ----
//
// Each decider calls the matching recorder ONCE, after it has its result. The
// recorder increments the closed-vocabulary counters (metrics.go) for that verdict.
// The durable Observation is built + written by the dispatcher (cmd/dos-hook), which
// also owns the latency clock and the exit code — so these stay pure counting, no
// I/O, matching the "data to the pure core" rule (the I/O is recordObservation, at
// the edge).

func boolLabel(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

// NOTE: the per-VERB invocation counter (MInvocations), the per-verb exit code, the
// latency, and the durable Observation are all owned by the DISPATCHER
// (cmd/dos-hook), which sees every call uniformly (including the decline paths that
// reach no verdict) and owns the wall clock + exit code. The recorders below count
// only the VERDICT-specific dimensions — the data that exists only once a decider
// has a verdict. This split keeps the invocation count honest (one per call, no
// double-count) while the rich dimensions stay next to the verdict that produced them.

// recordPretool counts the PRE admission verdict's dimensions.
func recordPretool(d Decision, dialect string) {
	Count(MPretoolDecision, d.DecisionTag)
	Count(MPretoolRung, nonEmpty(d.Rung, "none"))
	Count(MPretoolReasonCls, nonEmpty(d.ReasonClass, "none"))
	Count(MPretoolTreeKnown, boolLabel(d.TreeKnown))
	Count(MPretoolDialect, nonEmpty(dialect, "claude-code"))
}

// recordPosttool counts the tool-stream verdict + whether a warn was emitted.
func recordPosttool(state string, warnEmitted bool) {
	Count(MPosttoolVerdict, nonEmpty(state, "UNKNOWN"))
	Count(MPosttoolWarn, boolLabel(warnEmitted))
}

// recordMarkerVerdict counts the keep-alive budget outcome + the at-decision depth.
// `armed` distinguishes a real budget decision from an unarmed ordinary turn.
func recordMarkerVerdict(armed, allow bool, countAtDecision int) {
	if !armed {
		CountN(MMarkerUnarmed)
		return
	}
	if allow {
		CountN(MMarkerAllow)
	} else {
		CountN(MMarkerRefuse)
	}
	Add(MMarkerCountAt, "", int64(countAtDecision))
}

// recordStop counts the verify-on-stop outcome. `outcome` is block|no-claims|
// all-verified|delegate; `claims` is the claim count seen; `failures` are the
// (source) of each failed claim (for the per-source breakdown).
func recordStop(outcome string, claims int, failureSources []string) {
	if claims > 0 {
		Add(MStopClaims, "", int64(claims))
	}
	switch outcome {
	case "block":
		CountN(MStopBlock)
		for _, s := range failureSources {
			Count(MStopFailure, nonEmpty(s, "none"))
		}
	case "delegate":
		Count(MVerifyAbstain, "")
	default: // no-claims | all-verified
		Count(MStopLet, outcome)
	}
}

// recordVerify counts one (plan,phase) verify rung outcome as exercised inside stop.
func recordVerify(supported, shipped bool, source string) {
	if !supported {
		Count(MVerifyAbstain, "")
		return
	}
	if shipped {
		Count(MVerifyShipped, nonEmpty(source, "none"))
	} else {
		Count(MVerifyNotShipped, nonEmpty(source, "none"))
	}
}

// recordExit counts the per-verb exit code + a delegate (with its reason) + a
// recovered panic. Called by the dispatcher once the code is known.
func recordExit(verb string, code int) {
	Count(MExit, verb+":"+itoa(code))
}

func recordDelegate(verb, why string) { Count(MDelegate, verb+":"+why) }

func recordPanicRecovered(verb string) { CountN(MPanicRecovered) }

// ---- the exported dispatcher front door (cmd/dos-hook is package main) ----
//
// The dispatcher owns the uniform per-call counters (one invocation + one exit per
// call, the latency histogram), the delegate/panic counts, and the durable write.
// These thin wrappers expose the unexported recorders to package main without
// widening the internal vocabulary — the closed metric set stays defined in one place.

// RecordInvocation counts one invocation of `verb` + its latency (sum/count/bucket).
func RecordInvocation(verb string, elapsedNanos int64) {
	Count(MInvocations, verb)
	observeLatency(verb, elapsedNanos)
}

// RecordExitCode counts the per-verb exit code (0 OWNED / 3 DELEGATE).
func RecordExitCode(verb string, code int) { recordExit(verb, code) }

// RecordDelegate counts a native decline → Python `||` fallback, with its reason.
func RecordDelegate(verb, why string) { recordDelegate(verb, why) }

// RecordPanicRecovered counts a fail-safe firing (a Go crash recovered to exit 0).
func RecordPanicRecovered(verb string) { recordPanicRecovered(verb) }

// RecordObservationDurable persists one per-call observation to the workspace log
// (best-effort, fail-soft, gated by DOS_HOOK_METRICS / --debug).
func RecordObservationDurable(workspace string, debug bool, obs Observation) {
	recordObservation(workspace, debug, obs)
}

func nonEmpty(s, def string) string {
	if s == "" {
		return def
	}
	return s
}
