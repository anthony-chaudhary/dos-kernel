package hook

// stats.go — `dos-hook stats`, the surfacing fold (docs/276 Part 3).
//
// The read-only projection half of the observability spine: it folds the durable
// observation log (.dos/metrics/observations.jsonl, written by observe.go) into an
// aggregate an operator reads — the sibling of Python's `dos top` / `dos decisions`,
// aimed at the binary's OWN behavior. It takes no lease, launches nothing, mutates
// no state — a pure fold over the durable log, exactly the read-only-projection
// contract the kernel's other surfaces honor.
//
// Two render forms: a compact human table (default) and --json (the same aggregate
// as a machine object). The fold is tolerant the same way every kernel reader is —
// a torn/corrupt line is skipped ("didn't happen"), a wrong-family or too-new record
// is skipped (the durable_schema gate), so a half-written tail never derails the
// report.

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"
)

// StatsResult is the native stats outcome: Stdout is the rendered report (human or
// JSON), always exit 0 (a read-only fold never fails a turn). Handled is always true.
type StatsResult struct {
	Handled bool
	Stdout  string
}

// statsAgg is the folded aggregate — every dimension the observation log carries,
// counted. Maps are rendered sorted for byte-stability.
type statsAgg struct {
	Total      int            `json:"total_observations"`
	ByVerb     map[string]int `json:"by_verb"`
	ByOutcome  map[string]int `json:"by_outcome"`
	ByExit     map[string]int `json:"by_exit"`
	ByRung     map[string]int `json:"by_rung,omitempty"`
	ByReason   map[string]int `json:"by_reason_class,omitempty"`
	ByDialect  map[string]int `json:"by_dialect,omitempty"`
	ByStream   map[string]int `json:"by_stream_state,omitempty"`
	ByVerifySrc map[string]int `json:"by_verify_source,omitempty"`
	Delegates  int            `json:"delegates"`
	Panics     int            `json:"panics_recovered"`
	StopBlocks int            `json:"stop_blocks"`
	MarkerRefuse int          `json:"marker_refuse"`
	MarkerAllow  int          `json:"marker_allow"`

	// The pretool intervention rate — "what percent of tool calls did the kernel
	// touch?" One pretool record = one tool call adjudicated, so this verb is the
	// honest denominator (posttool/stop/marker firings are not tool-call admissions).
	// A `delegate` outcome is excluded from the intervened count: the Python
	// fallback decided that call, and this log never saw its verdict.
	PretoolCalls     int `json:"pretool_calls"`
	PretoolPassed    int `json:"pretool_passed"`
	PretoolDelegated int `json:"pretool_delegated"`

	// latency, per verb: mean + p50/p95 over the observed latency_ms samples.
	LatencyByVerb map[string]latStat `json:"latency_ms_by_verb,omitempty"`

	// internal sample buckets for percentile (not serialized directly)
	latSamples map[string][]float64
}

type latStat struct {
	N    int     `json:"n"`
	Mean float64 `json:"mean"`
	P50  float64 `json:"p50"`
	P95  float64 `json:"p95"`
	Max  float64 `json:"max"`
}

// DecideStats reads the workspace's observation log and renders the aggregate. The
// log read is the only I/O (at the boundary); the fold + render are pure. `asJSON`
// selects the machine form; `since` (e.g. "1h", "30m", "" = all) windows the fold to
// records at or after now-since. Any failure (no log yet, unreadable, an unparseable
// duration) renders an all-time aggregate — never an error, a read-only surface
// degrades to "show what we have."
func DecideStats(workspaceFlag string, asJSON bool, since string, debug io.Writer) StatsResult {
	workspace := ResolveWorkspace(workspaceFlag)
	path := obsLogPath(workspace)
	cutoff := sinceCutoff(since) // zero time = no window
	agg := foldObservationsSince(path, cutoff)
	if asJSON {
		return StatsResult{Handled: true, Stdout: renderStatsJSON(agg)}
	}
	return StatsResult{Handled: true, Stdout: renderStatsHuman(agg, workspace, path)}
}

// sinceCutoff parses a --since duration into an absolute cutoff (now - dur). An empty
// or unparseable value returns the zero Time (no window). The clock lives HERE, at
// the boundary (a read-only surface), never in a verdict.
func sinceCutoff(since string) time.Time {
	since = strings.TrimSpace(since)
	if since == "" {
		return time.Time{}
	}
	d, err := time.ParseDuration(since)
	if err != nil || d <= 0 {
		return time.Time{}
	}
	return time.Now().UTC().Add(-d)
}

// foldObservations is the all-time fold (no window) — kept for callers/tests that do
// not window.
func foldObservations(path string) statsAgg {
	return foldObservationsSince(path, time.Time{})
}

// foldObservationsSince reads the JSONL log and accumulates the aggregate over
// records at or after `cutoff` (the zero Time = no window). Tolerant: a blank/torn/
// corrupt line is skipped; a non-OBSERVE or wrong-family or too-new record is skipped
// (the durable_schema gate, reusing schemaReadableFamily); a record whose `ts` is
// before the cutoff (or unparseable WHEN a window is set) is skipped.
func foldObservationsSince(path string, cutoff time.Time) statsAgg {
	agg := statsAgg{
		ByVerb: map[string]int{}, ByOutcome: map[string]int{}, ByExit: map[string]int{},
		ByRung: map[string]int{}, ByReason: map[string]int{}, ByDialect: map[string]int{},
		ByStream: map[string]int{}, ByVerifySrc: map[string]int{},
		LatencyByVerb: map[string]latStat{}, latSamples: map[string][]float64{},
	}
	if path == "" {
		return agg
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return agg
	}
	text := strings.ReplaceAll(string(data), "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	for _, line := range strings.Split(text, "\n") {
		s := strings.TrimSpace(line)
		if s == "" {
			continue
		}
		var o map[string]any
		if err := json.Unmarshal([]byte(s), &o); err != nil {
			continue // torn/corrupt — didn't happen
		}
		if !schemaReadableFamily(o, obsSchemaFamily, obsSchemaVersion) {
			continue
		}
		if op, _ := o["op"].(string); op != "OBSERVE" {
			continue
		}
		// Window filter: when a cutoff is set, skip a record older than it (or one
		// whose ts cannot be parsed — a windowed query must not count an undatable
		// record, the conservative direction). No window → count everything.
		if !cutoff.IsZero() {
			ts, ok := parseObsTS(strField(o, "ts"))
			if !ok || ts.Before(cutoff) {
				continue
			}
		}
		agg.Total++
		verb := strField(o, "verb")
		bump(agg.ByVerb, verb)
		bump(agg.ByOutcome, strField(o, "outcome"))
		bump(agg.ByExit, fmt.Sprintf("%d", intField(o, "exit")))
		bumpIf(agg.ByRung, strField(o, "rung"))
		bumpIf(agg.ByReason, strField(o, "reason_class"))
		bumpIf(agg.ByDialect, strField(o, "dialect"))
		bumpIf(agg.ByStream, strField(o, "stream_state"))
		bumpIf(agg.ByVerifySrc, strField(o, "verify_source"))
		if strField(o, "outcome") == "delegate" {
			agg.Delegates++
		}
		if b, ok := o["panic_recovered"].(bool); ok && b {
			agg.Panics++
		}
		if verb == "stop" && strField(o, "outcome") == "block" {
			agg.StopBlocks++
		}
		if verb == "pretool" {
			agg.PretoolCalls++
			switch strField(o, "outcome") {
			case "passthrough":
				agg.PretoolPassed++
			case "delegate":
				agg.PretoolDelegated++
			}
		}
		if verb == "marker" {
			switch strField(o, "outcome") {
			case "refuse":
				agg.MarkerRefuse++
			case "allow":
				agg.MarkerAllow++
			}
		}
		if lm, ok := floatField(o, "latency_ms"); ok && verb != "" {
			agg.latSamples[verb] = append(agg.latSamples[verb], lm)
		}
	}
	// Finalize per-verb latency stats from the collected samples.
	for verb, xs := range agg.latSamples {
		agg.LatencyByVerb[verb] = summarizeLatency(xs)
	}
	return agg
}

// summarizeLatency computes n/mean/p50/p95/max over a sample slice (nearest-rank
// percentile on the sorted copy — the simplest correct choice for a small N).
func summarizeLatency(xs []float64) latStat {
	if len(xs) == 0 {
		return latStat{}
	}
	sorted := append([]float64(nil), xs...)
	sort.Float64s(sorted)
	sum := 0.0
	for _, x := range sorted {
		sum += x
	}
	return latStat{
		N:    len(sorted),
		Mean: round2(sum / float64(len(sorted))),
		P50:  round2(percentileNearestRank(sorted, 50)),
		P95:  round2(percentileNearestRank(sorted, 95)),
		Max:  round2(sorted[len(sorted)-1]),
	}
}

// percentileNearestRank returns the p-th percentile by the nearest-rank method on an
// already-sorted slice: rank = ceil(p/100 * n), 1-indexed, clamped to [1,n].
func percentileNearestRank(sorted []float64, p int) float64 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	rank := (p*n + 99) / 100 // ceil(p/100 * n)
	if rank < 1 {
		rank = 1
	}
	if rank > n {
		rank = n
	}
	return sorted[rank-1]
}

func round2(f float64) float64 {
	return float64(int64(f*100+0.5)) / 100
}

// pretoolIntervened is the count of tool calls the hook actually changed (denied,
// warned, …): everything that neither passed through nor was delegated to Python.
func (a statsAgg) pretoolIntervened() int {
	return a.PretoolCalls - a.PretoolPassed - a.PretoolDelegated
}

// pctOf renders n as a percent of d ("3.2%"); a zero denominator renders "0.0%"
// (a read-only surface never divides by zero, it degrades).
func pctOf(n, d int) string {
	if d <= 0 {
		return "0.0%"
	}
	return fmt.Sprintf("%.1f%%", float64(n)*100/float64(d))
}

// renderStatsJSON marshals the aggregate as a machine object (byte-stable via the
// canonical pyJSONDumps after building a sorted plain map). We hand-build the map so
// the empty-map omission is honored consistently with the human form.
func renderStatsJSON(agg statsAgg) string {
	intervenedPct := 0.0
	if agg.PretoolCalls > 0 {
		intervenedPct = round2(float64(agg.pretoolIntervened()) * 100 / float64(agg.PretoolCalls))
	}
	m := map[string]any{
		"total_observations": agg.Total,
		"by_verb":            toAnyMap(agg.ByVerb),
		"by_outcome":         toAnyMap(agg.ByOutcome),
		"by_exit":            toAnyMap(agg.ByExit),
		"delegates":          agg.Delegates,
		"panics_recovered":   agg.Panics,
		"stop_blocks":        agg.StopBlocks,
		"marker_refuse":      agg.MarkerRefuse,
		"marker_allow":       agg.MarkerAllow,
		"pretool_calls":      agg.PretoolCalls,
		"pretool_passed":     agg.PretoolPassed,
		"pretool_intervened": agg.pretoolIntervened(),
		"pretool_intervened_pct": intervenedPct,
	}
	addAnyMapIf(m, "by_rung", agg.ByRung)
	addAnyMapIf(m, "by_reason_class", agg.ByReason)
	addAnyMapIf(m, "by_dialect", agg.ByDialect)
	addAnyMapIf(m, "by_stream_state", agg.ByStream)
	addAnyMapIf(m, "by_verify_source", agg.ByVerifySrc)
	if len(agg.LatencyByVerb) > 0 {
		lat := map[string]any{}
		for verb, st := range agg.LatencyByVerb {
			lat[verb] = map[string]any{
				"n": st.N, "mean": st.Mean, "p50": st.P50, "p95": st.P95, "max": st.Max,
			}
		}
		m["latency_ms_by_verb"] = lat
	}
	return pyJSONDumps(m)
}

// renderStatsHuman renders the compact operator table.
func renderStatsHuman(agg statsAgg, workspace, path string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "dos-hook stats — %s\n", workspace)
	if agg.Total == 0 {
		fmt.Fprintf(&b, "  (no observations yet at %s)\n", path)
		fmt.Fprintf(&b, "  the binary logs one record per hook call once it runs; set DOS_HOOK_METRICS=0 to disable.\n")
		return strings.TrimRight(b.String(), "\n")
	}
	fmt.Fprintf(&b, "  observations   %d\n", agg.Total)
	if agg.PretoolCalls > 0 {
		iv := agg.pretoolIntervened()
		fmt.Fprintf(&b, "  tool calls     %d adjudicated — %d passed untouched (%s), %d intervened (%s)\n",
			agg.PretoolCalls,
			agg.PretoolPassed, pctOf(agg.PretoolPassed, agg.PretoolCalls),
			iv, pctOf(iv, agg.PretoolCalls))
	}
	renderCountLine(&b, "  by verb       ", agg.ByVerb)
	renderCountLine(&b, "  by outcome    ", agg.ByOutcome)
	renderCountLine(&b, "  by exit code  ", agg.ByExit)
	if len(agg.ByRung) > 0 {
		renderCountLine(&b, "  pretool rung  ", agg.ByRung)
	}
	if len(agg.ByReason) > 0 {
		renderCountLine(&b, "  reason class  ", agg.ByReason)
	}
	if len(agg.ByDialect) > 0 {
		renderCountLine(&b, "  dialect       ", agg.ByDialect)
	}
	if len(agg.ByStream) > 0 {
		renderCountLine(&b, "  stream state  ", agg.ByStream)
	}
	if len(agg.ByVerifySrc) > 0 {
		renderCountLine(&b, "  verify source ", agg.ByVerifySrc)
	}
	// Headline rates an operator scans for.
	fmt.Fprintf(&b, "  delegates     %d  (native declined → Python)\n", agg.Delegates)
	fmt.Fprintf(&b, "  stop blocks   %d  (false-done refusals)\n", agg.StopBlocks)
	if agg.MarkerAllow+agg.MarkerRefuse > 0 {
		fmt.Fprintf(&b, "  marker budget %d allow / %d refuse\n", agg.MarkerAllow, agg.MarkerRefuse)
	}
	if agg.Panics > 0 {
		fmt.Fprintf(&b, "  ⚠ panics      %d  (fail-safe fired — a Go crash recovered to exit 0)\n", agg.Panics)
	}
	// Latency table.
	if len(agg.LatencyByVerb) > 0 {
		fmt.Fprintf(&b, "  latency (ms)  verb            n     mean    p50    p95    max\n")
		for _, verb := range sortedKeys(agg.LatencyByVerb) {
			st := agg.LatencyByVerb[verb]
			fmt.Fprintf(&b, "                %-14s %4d  %6.2f %6.2f %6.2f %6.2f\n",
				verb, st.N, st.Mean, st.P50, st.P95, st.Max)
		}
	}
	return strings.TrimRight(b.String(), "\n")
}

// renderCountLine renders "label  k=v  k=v …" with keys sorted, biggest-count first
// within the sorted-key ordering kept simple (sorted alphabetically for stability).
func renderCountLine(b *strings.Builder, label string, m map[string]int) {
	if len(m) == 0 {
		return
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s=%d", k, m[k]))
	}
	fmt.Fprintf(b, "%s%s\n", label, strings.Join(parts, "  "))
}

// ---- small field/aggregate helpers ----

// parseObsTS parses an observation `ts` — the journalNowISO format
// (2006-01-02T15:04:05Z, UTC, second resolution). Returns ok=false on a malformed
// stamp (so a windowed fold skips it).
func parseObsTS(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	t, err := time.Parse("2006-01-02T15:04:05Z", s)
	if err != nil {
		// Tolerate a fuller RFC3339 stamp too (a future writer might add sub-second).
		if t2, err2 := time.Parse(time.RFC3339, s); err2 == nil {
			return t2.UTC(), true
		}
		return time.Time{}, false
	}
	return t.UTC(), true
}

func strField(o map[string]any, k string) string {
	if s, ok := o[k].(string); ok {
		return s
	}
	return ""
}

func intField(o map[string]any, k string) int {
	switch x := o[k].(type) {
	case float64:
		return int(x)
	case int:
		return x
	}
	return 0
}

func floatField(o map[string]any, k string) (float64, bool) {
	switch x := o[k].(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	}
	return 0, false
}

func bump(m map[string]int, k string) {
	if k == "" {
		k = "(none)"
	}
	m[k]++
}

// bumpIf only counts a non-empty key (for the optional dimensions — an absent field
// must not inflate a "(none)" bucket the way a present-but-empty value would).
func bumpIf(m map[string]int, k string) {
	if k == "" {
		return
	}
	m[k]++
}

func toAnyMap(m map[string]int) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func addAnyMapIf(dst map[string]any, key string, m map[string]int) {
	if len(m) == 0 {
		return
	}
	dst[key] = toAnyMap(m)
}

func sortedKeys(m map[string]latStat) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
