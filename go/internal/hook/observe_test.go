package hook

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The durable observation record: schema tag, additive-optional fields, the
// DOS_HOOK_METRICS opt-out, and a fail-soft empty-workspace skip.

func TestRecordObservationWritesSchemaTaggedLine(t *testing.T) {
	ws := t.TempDir()
	tk := true
	recordObservation(ws, false, Observation{
		Verb: "pretool", Outcome: "deny", ExitCode: 0, LatencyMs: 1.5,
		Rung: "admission", ReasonClass: "SELF_MODIFY", Dialect: "claude-code", TreeKnown: &tk,
		CallID: "call-7", SessionID: "thread-3", Workspace: ws, Profile: "profile-a", PhaseState: "succeeded",
		PhaseMS: map[string]float64{"parse": 1.25, "inputs": 2.5, "evaluate": 0.75, "serialize": 0.5},
	})
	line := readSingleObs(t, ws)
	var o map[string]any
	if err := json.Unmarshal([]byte(line), &o); err != nil {
		t.Fatalf("observation line is not valid JSON: %v\n%s", err, line)
	}
	if o["op"] != "OBSERVE" {
		t.Fatalf("op = %v, want OBSERVE", o["op"])
	}
	sch, _ := o["schema"].(map[string]any)
	if sch == nil || sch["family"] != obsSchemaFamily {
		t.Fatalf("schema family wrong: %v", o["schema"])
	}
	for _, k := range []string{"verb", "outcome", "rung", "reason_class", "dialect", "tree_known", "latency_ms", "call_id", "session_id", "workspace", "profile", "phase_state", "phase_ms"} {
		if _, ok := o[k]; !ok {
			t.Errorf("observation missing expected field %q", k)
		}
	}
	if o["call_id"] != "call-7" || o["phase_state"] != "succeeded" {
		t.Fatalf("lifecycle identity missing: %v", o)
	}
	phases, ok := o["phase_ms"].(map[string]any)
	if !ok || phases["parse"] != 1.25 || phases["inputs"] != 2.5 || phases["evaluate"] != 0.75 || phases["serialize"] != 0.5 {
		t.Fatalf("phase_ms = %#v", o["phase_ms"])
	}
	if o["reason_class"] != "SELF_MODIFY" {
		t.Fatalf("reason_class = %v", o["reason_class"])
	}
}

// Additive-optional fields are written ONLY when set — a bare record stays small.
func TestRecordObservationOmitsUnsetFields(t *testing.T) {
	ws := t.TempDir()
	recordObservation(ws, false, Observation{Verb: "marker", Outcome: "unarmed", ExitCode: 0})
	line := readSingleObs(t, ws)
	var o map[string]any
	_ = json.Unmarshal([]byte(line), &o)
	for _, k := range []string{"rung", "reason_class", "dialect", "verify_source", "blocked_plan", "claims_seen", "stream_state", "call_id", "session_id", "workspace", "profile", "phase_state", "phase_ms"} {
		if _, ok := o[k]; ok {
			t.Errorf("bare record should omit %q, but it is present: %v", k, o[k])
		}
	}
}

// DOS_HOOK_METRICS=0 suppresses the durable append (the in-process counters are
// unaffected — they are tested elsewhere). A --debug run logs regardless.
func TestRecordObservationOptOut(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("DOS_HOOK_METRICS", "0")
	recordObservation(ws, false, Observation{Verb: "pretool", Outcome: "passthrough"})
	if _, err := os.Stat(obsLogPath(ws)); !os.IsNotExist(err) {
		t.Fatalf("DOS_HOOK_METRICS=0 should suppress the log, but it exists")
	}
	// --debug overrides the opt-out.
	recordObservation(ws, true, Observation{Verb: "pretool", Outcome: "passthrough"})
	if _, err := os.Stat(obsLogPath(ws)); err != nil {
		t.Fatalf("--debug should log despite the opt-out: %v", err)
	}
}

// An empty workspace (no root) is a no-op, never a panic or a stray write.
func TestRecordObservationEmptyWorkspaceIsNoop(t *testing.T) {
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("empty workspace should not panic: %v", r)
		}
	}()
	recordObservation("", false, Observation{Verb: "stop", Outcome: "no-claims"})
}

// Two records append (the log accumulates; it is a WAL, not a replace).
func TestRecordObservationAppends(t *testing.T) {
	ws := t.TempDir()
	recordObservation(ws, false, Observation{Verb: "pretool", Outcome: "passthrough"})
	recordObservation(ws, false, Observation{Verb: "stop", Outcome: "block", ClaimsSeen: 1})
	data, err := os.ReadFile(obsLogPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	lines := nonBlankLines(string(data))
	if len(lines) != 2 {
		t.Fatalf("want 2 appended records, got %d:\n%s", len(lines), data)
	}
}

func readSingleObs(t *testing.T, ws string) string {
	t.Helper()
	data, err := os.ReadFile(obsLogPath(ws))
	if err != nil {
		t.Fatalf("reading observation log: %v", err)
	}
	lines := nonBlankLines(string(data))
	if len(lines) != 1 {
		t.Fatalf("want exactly 1 observation, got %d:\n%s", len(lines), data)
	}
	return lines[0]
}

func nonBlankLines(s string) []string {
	var out []string
	for _, l := range strings.Split(strings.ReplaceAll(s, "\r\n", "\n"), "\n") {
		if strings.TrimSpace(l) != "" {
			out = append(out, l)
		}
	}
	return out
}

// sanity: the path layout is .dos/metrics/observations.jsonl under the workspace.
func TestObsLogPathLayout(t *testing.T) {
	got := obsLogPath("/tmp/ws")
	want := filepath.Join("/tmp/ws", ".dos", "metrics", "observations.jsonl")
	if got != want {
		t.Fatalf("obsLogPath = %q, want %q", got, want)
	}
	if obsLogPath("") != "" {
		t.Fatalf("empty workspace should yield empty path")
	}
}
