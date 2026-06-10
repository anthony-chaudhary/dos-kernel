package hook

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

// The surfacing fold: counts by every dimension, latency percentiles, JSON/human
// render, and the tolerant read (torn line / wrong family / non-OBSERVE skipped).

// writeObsLog seeds an observation log with raw JSONL lines under a temp workspace.
func writeObsLog(t *testing.T, lines ...string) string {
	t.Helper()
	ws := t.TempDir()
	path := obsLogPath(ws)
	if dir := dirOf(path); dir != "" {
		_ = os.MkdirAll(dir, 0o755)
	}
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

func obsLine(m map[string]any) string {
	m["op"] = "OBSERVE"
	m["schema"] = map[string]any{"family": obsSchemaFamily, "version": obsSchemaVersion}
	b, _ := json.Marshal(m)
	return string(b)
}

func TestFoldCountsByDimension(t *testing.T) {
	ws := writeObsLog(t,
		obsLine(map[string]any{"verb": "pretool", "outcome": "deny", "exit": 0, "rung": "admission", "reason_class": "SELF_MODIFY", "dialect": "claude-code", "latency_ms": 5.0}),
		obsLine(map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "rung": "none", "dialect": "claude-code", "latency_ms": 1.0}),
		obsLine(map[string]any{"verb": "stop", "outcome": "block", "exit": 0, "claims_seen": 1, "latency_ms": 3.0}),
		obsLine(map[string]any{"verb": "marker", "outcome": "refuse", "exit": 0, "latency_ms": 2.0}),
		obsLine(map[string]any{"verb": "marker", "outcome": "allow", "exit": 0, "latency_ms": 2.0}),
	)
	agg := foldObservations(obsLogPath(ws))
	if agg.Total != 5 {
		t.Fatalf("total = %d, want 5", agg.Total)
	}
	if agg.ByVerb["pretool"] != 2 || agg.ByVerb["marker"] != 2 || agg.ByVerb["stop"] != 1 {
		t.Fatalf("by_verb wrong: %v", agg.ByVerb)
	}
	if agg.ByReason["SELF_MODIFY"] != 1 {
		t.Fatalf("by_reason_class wrong: %v", agg.ByReason)
	}
	if agg.StopBlocks != 1 {
		t.Fatalf("stop_blocks = %d, want 1", agg.StopBlocks)
	}
	if agg.MarkerRefuse != 1 || agg.MarkerAllow != 1 {
		t.Fatalf("marker tallies wrong: refuse=%d allow=%d", agg.MarkerRefuse, agg.MarkerAllow)
	}
}

// The pretool intervention rate — the "percent of overall tool calls touched"
// headline. One pretool record = one tool call; a delegate is excluded from the
// intervened count (Python decided it); other verbs never enter the denominator.
func TestPretoolInterventionRate(t *testing.T) {
	ws := writeObsLog(t,
		obsLine(map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "latency_ms": 1.0}),
		obsLine(map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "latency_ms": 1.0}),
		obsLine(map[string]any{"verb": "pretool", "outcome": "deny", "exit": 2, "latency_ms": 1.0}),
		obsLine(map[string]any{"verb": "pretool", "outcome": "delegate", "exit": 0, "latency_ms": 1.0}),
		obsLine(map[string]any{"verb": "stop", "outcome": "block", "exit": 2, "latency_ms": 1.0}), // not a tool call
	)
	agg := foldObservations(obsLogPath(ws))
	if agg.PretoolCalls != 4 || agg.PretoolPassed != 2 || agg.PretoolDelegated != 1 {
		t.Fatalf("pretool tallies wrong: calls=%d passed=%d delegated=%d",
			agg.PretoolCalls, agg.PretoolPassed, agg.PretoolDelegated)
	}
	if agg.pretoolIntervened() != 1 {
		t.Fatalf("intervened = %d, want 1 (the deny; the delegate is Python's verdict)",
			agg.pretoolIntervened())
	}
	human := renderStatsHuman(agg, "ws", "p")
	if !strings.Contains(human, "4 adjudicated") ||
		!strings.Contains(human, "2 passed untouched (50.0%)") ||
		!strings.Contains(human, "1 intervened (25.0%)") {
		t.Fatalf("human render missing the rate line:\n%s", human)
	}
	out := renderStatsJSON(agg)
	var o map[string]any
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("stats JSON not valid: %v\n%s", err, out)
	}
	if o["pretool_calls"].(float64) != 4 ||
		o["pretool_intervened"].(float64) != 1 ||
		o["pretool_intervened_pct"].(float64) != 25 {
		t.Fatalf("JSON rate fields wrong: %s", out)
	}
}

// pctOf never divides by zero — and a log with no pretool records renders no rate
// line at all (a denominator of other verbs would be dishonest).
func TestPretoolRateAbsentWithoutPretoolRecords(t *testing.T) {
	if got := pctOf(1, 0); got != "0.0%" {
		t.Fatalf("pctOf(1,0) = %q, want 0.0%%", got)
	}
	ws := writeObsLog(t,
		obsLine(map[string]any{"verb": "stop", "outcome": "block", "exit": 2, "latency_ms": 1.0}),
	)
	agg := foldObservations(obsLogPath(ws))
	human := renderStatsHuman(agg, "ws", "p")
	if strings.Contains(human, "tool calls") {
		t.Fatalf("rate line should be absent with zero pretool records:\n%s", human)
	}
}

// A torn/corrupt line and a wrong-family / non-OBSERVE record are skipped, never
// fatal — the durable_schema + tolerant-read discipline.
func TestFoldTolerantOfBadLines(t *testing.T) {
	good := obsLine(map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "latency_ms": 1.0})
	wrongFamily := `{"op":"OBSERVE","schema":{"family":"tool-stream","version":1},"verb":"x","outcome":"y"}`
	tooNew := `{"op":"OBSERVE","schema":{"family":"hook-observation","version":999},"verb":"x","outcome":"y"}`
	notObserve := `{"op":"STEP","schema":{"family":"hook-observation","version":1},"verb":"x"}`
	torn := `{"verb":"pretool","outco`
	ws := writeObsLog(t, good, wrongFamily, tooNew, notObserve, torn, "", good)

	agg := foldObservations(obsLogPath(ws))
	if agg.Total != 2 {
		t.Fatalf("only the 2 good OBSERVE lines should count, got total=%d", agg.Total)
	}
}

// Latency stats: n/mean/p50/p95/max over a known sample set.
func TestFoldLatencyStats(t *testing.T) {
	var lines []string
	for _, ms := range []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10} {
		lines = append(lines, obsLine(map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "latency_ms": ms}))
	}
	ws := writeObsLog(t, lines...)
	agg := foldObservations(obsLogPath(ws))
	st := agg.LatencyByVerb["pretool"]
	if st.N != 10 {
		t.Fatalf("n = %d, want 10", st.N)
	}
	if st.Mean != 5.5 {
		t.Fatalf("mean = %v, want 5.5", st.Mean)
	}
	if st.Max != 10 {
		t.Fatalf("max = %v, want 10", st.Max)
	}
	// nearest-rank p50 over 1..10 = rank ceil(0.5*10)=5 → value 5; p95 = rank ceil(9.5)=10 → 10.
	if st.P50 != 5 {
		t.Fatalf("p50 = %v, want 5", st.P50)
	}
	if st.P95 != 10 {
		t.Fatalf("p95 = %v, want 10", st.P95)
	}
}

// obsLineAt is obsLine with an explicit ts (for the --since window test).
func obsLineAt(ts string, m map[string]any) string {
	m["ts"] = ts
	return obsLine(m)
}

// The --since window: records before the cutoff are excluded; an unparseable ts is
// excluded WHEN a window is set; with no window everything counts (including the
// undatable record).
func TestFoldWindowFilter(t *testing.T) {
	ws := writeObsLog(t,
		obsLineAt("2020-01-01T00:00:00Z", map[string]any{"verb": "pretool", "outcome": "passthrough", "exit": 0, "latency_ms": 1.0}), // ancient
		obsLineAt("2999-01-01T00:00:00Z", map[string]any{"verb": "stop", "outcome": "block", "exit": 0, "latency_ms": 1.0}),          // future
		obsLine(map[string]any{"verb": "marker", "outcome": "allow", "exit": 0, "latency_ms": 1.0}),                                  // dated "now" by the writer
	)
	path := obsLogPath(ws)

	// No window → all 3 (including the "now"-dated one).
	if all := foldObservationsSince(path, time.Time{}); all.Total != 3 {
		t.Fatalf("no-window total = %d, want 3", all.Total)
	}
	// Cutoff in 2500 → only the 2999 record survives.
	cut, _ := time.Parse(time.RFC3339, "2500-01-01T00:00:00Z")
	windowed := foldObservationsSince(path, cut)
	if windowed.Total != 1 {
		t.Fatalf("windowed total = %d, want 1 (only the future record)", windowed.Total)
	}
	if windowed.ByVerb["stop"] != 1 {
		t.Fatalf("windowed should keep the stop record: %v", windowed.ByVerb)
	}
}

func TestSinceCutoffParsing(t *testing.T) {
	if !sinceCutoff("").IsZero() {
		t.Fatalf("empty --since should yield the zero cutoff (no window)")
	}
	if !sinceCutoff("garbage").IsZero() {
		t.Fatalf("unparseable --since should degrade to no window")
	}
	if !sinceCutoff("-5m").IsZero() {
		t.Fatalf("non-positive --since should degrade to no window")
	}
	if sinceCutoff("1h").IsZero() {
		t.Fatalf("a valid --since should yield a non-zero cutoff")
	}
}

func TestParseObsTS(t *testing.T) {
	if _, ok := parseObsTS("2026-06-09T22:34:51Z"); !ok {
		t.Fatalf("journalNowISO stamp should parse")
	}
	if _, ok := parseObsTS("not-a-time"); ok {
		t.Fatalf("garbage stamp should not parse")
	}
	if _, ok := parseObsTS(""); ok {
		t.Fatalf("empty stamp should not parse")
	}
}

func TestPercentileNearestRank(t *testing.T) {
	sorted := []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
	cases := []struct {
		p    int
		want float64
	}{
		{0, 1}, {50, 5}, {90, 9}, {95, 10}, {100, 10},
	}
	for _, c := range cases {
		if got := percentileNearestRank(sorted, c.p); got != c.want {
			t.Errorf("p%d = %v, want %v", c.p, got, c.want)
		}
	}
	if percentileNearestRank(nil, 50) != 0 {
		t.Fatalf("empty slice percentile should be 0")
	}
}

// DecideStats on a missing log renders the empty form (zero observations), never an
// error — a read-only surface degrades to "nothing to show".
func TestDecideStatsEmptyLog(t *testing.T) {
	ws := t.TempDir()
	res := DecideStats(ws, false, "", nil)
	if !res.Handled {
		t.Fatalf("stats should always handle")
	}
	if !strings.Contains(res.Stdout, "no observations") {
		t.Fatalf("empty-log human render missing the no-observations note:\n%s", res.Stdout)
	}
	// JSON form of an empty log is a valid object with total 0.
	resJSON := DecideStats(ws, true, "", nil)
	var o map[string]any
	if err := json.Unmarshal([]byte(resJSON.Stdout), &o); err != nil {
		t.Fatalf("empty-log JSON not valid: %v\n%s", err, resJSON.Stdout)
	}
	if o["total_observations"].(float64) != 0 {
		t.Fatalf("empty-log total should be 0, got %v", o["total_observations"])
	}
}

// The JSON render is parseable and carries the headline aggregates.
func TestRenderStatsJSONRoundTrips(t *testing.T) {
	ws := writeObsLog(t,
		obsLine(map[string]any{"verb": "stop", "outcome": "block", "exit": 0, "claims_seen": 1, "latency_ms": 3.0}),
		obsLine(map[string]any{"verb": "stop", "outcome": "delegate", "exit": 3, "latency_ms": 1.0}),
	)
	agg := foldObservations(obsLogPath(ws))
	out := renderStatsJSON(agg)
	var o map[string]any
	if err := json.Unmarshal([]byte(out), &o); err != nil {
		t.Fatalf("stats JSON not valid: %v\n%s", err, out)
	}
	if o["total_observations"].(float64) != 2 {
		t.Fatalf("total = %v, want 2", o["total_observations"])
	}
	if o["stop_blocks"].(float64) != 1 {
		t.Fatalf("stop_blocks = %v, want 1", o["stop_blocks"])
	}
	if o["delegates"].(float64) != 1 {
		t.Fatalf("delegates = %v, want 1", o["delegates"])
	}
	byExit := o["by_exit"].(map[string]any)
	if byExit["3"].(float64) != 1 {
		t.Fatalf("by_exit[3] = %v, want 1", byExit["3"])
	}
}
