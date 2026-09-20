package hook

// metrics.go — the observability spine for the native hook fast-path (docs/276).
//
// The Go kernel is a PURE, fast decider: every invocation produces a rich verdict
// (a Decision, a verifyVerdict, a markerVerdict, a Stop block/let-stop, an exit
// code, a possible recovered panic). Before this file NONE of it was counted and
// NONE of it was surfaced — the only observability was off-by-default --debug
// stderr that nobody aggregated. This adds the counting half; observe.go adds the
// durable per-invocation record + the surfacing fold.
//
// TWO disciplines hold the line:
//
//  1. "I/O at the boundary, data to the pure core." The deciders stay pure. A
//     counter increment is a side-band the dispatcher performs at the edge, AFTER
//     the verdict is already decided — strictly downstream of an already-decided
//     verdict, exactly like a dialect is downstream OUTPUT. No gated decision byte
//     changes (the docs/124 parity contract holds; the parity corpus never sees a
//     counter).
//
//  2. The closed-vocabulary rule. Every counter name is a const in this file, so
//     the metric surface is enumerable and testable — the same "closed-set-as-data"
//     discipline the kernel uses for reason classes. A typo cannot mint a phantom
//     dimension because callers use the typed helpers, not raw strings.
//
// PERFORMANCE: the registry is a fixed map built ONCE at package init, indexed by a
// pre-interned string key; an increment is a single atomic.AddInt64 on an
// already-resolved *int64 — nanoseconds, never milliseconds. A hook is a one-shot
// process, so there is no scrape endpoint (nothing long-lived to scrape); the
// cross-process surface is the durable observation log (observe.go), folded by
// `dos-hook stats`.

import (
	"sort"
	"sync"
	"sync/atomic"
)

// Metric is the closed counter vocabulary. Every name the kernel counts is a member
// here — the enumerable surface. A counter is identified by its base Metric plus an
// optional dimension label (verb, decision tag, reason class, …); the registry keys
// on the joined "base|label" string, all interned at init.
type Metric string

const (
	// ---- invocation (every verb) ----
	MInvocations    Metric = "invocations"     // dim: verb
	MExit           Metric = "exit"            // dim: verb:code  (0 OWNED / 3 DELEGATE)
	MPanicRecovered Metric = "panic_recovered" // dim: verb — the fail-safe fired (a Go crash that would silently exit 0)
	MDelegate       Metric = "delegate"        // dim: verb:why  (native declined → Python ||)

	// ---- pretool (the PRE admission decider) ----
	MPretoolDecision   Metric = "pretool_decision"    // dim: deny|warn|passthrough
	MPretoolRung       Metric = "pretool_rung"        // dim: admission|provenance|none
	MPretoolReasonCls  Metric = "pretool_reason_cls"  // dim: SELF_MODIFY|LANE_COLLISION|… (or "none")
	MPretoolTreeKnown  Metric = "pretool_tree_known"  // dim: true|false
	MPretoolDialect    Metric = "pretool_dialect"     // dim: claude-code|gemini|cursor|…
	MPretoolEffectKind Metric = "pretool_effect_kind" // dim: none|spawn|coordination|capability

	// ---- posttool (the tool-stream repeat/stall warn) ----
	MPosttoolVerdict Metric = "posttool_verdict"      // dim: PROCEEDING|REPEATING|STALLED|…
	MPosttoolWarn    Metric = "posttool_warn_emitted" // dim: true|false

	// ---- marker (the keep-alive wait-marker budget) ----
	MMarkerAllow   Metric = "marker_allow"    // budget remained → one more marker held the turn
	MMarkerRefuse  Metric = "marker_refuse"   // budget spent → let the loop stop
	MMarkerUnarmed Metric = "marker_unarmed"  // no loop signal → ordinary turn, budget did not arm
	MMarkerCountAt Metric = "marker_count_at" // SUM of the at-decision marker count (÷ allow+refuse = mean depth)

	// ---- stop (verify-on-stop: refuse a false done) ----
	MStopBlock   Metric = "stop_block"   // a confident NOT_SHIPPED claim → blocked the stop
	MStopLet     Metric = "stop_let"     // dim: no-claims|all-verified
	MStopClaims  Metric = "stop_claims"  // SUM of claims seen (÷ invocations = mean)
	MStopFailure Metric = "stop_failure" // dim: source — a claim that failed verify (grep-subject|none|…)

	// ---- verify (the truth syscall's grep rung, as exercised by stop) ----
	MVerifyShipped    Metric = "verify_shipped"     // dim: source
	MVerifyNotShipped Metric = "verify_not_shipped" // dim: source
	MVerifyAbstain    Metric = "verify_abstain"     // supported=false → delegate to Python

	// ---- latency (per-verb wall time, summed for a mean + a fixed bucket histogram) ----
	MLatencyNanos Metric = "latency_ns"     // dim: verb — SUM of elapsed ns
	MLatencyCount Metric = "latency_count"  // dim: verb — N (÷ into the sum for a mean)
	MLatencyBkt   Metric = "latency_bucket" // dim: verb:bucket — count in a fixed le-bucket
)

// registry is the process-global counter store. Built once; an increment is a single
// atomic add on a resolved *int64 (the map itself is never written after init under
// the steady path — a never-before-seen key takes the slow mutex branch ONCE then is
// cached). Read back in full by Snapshot for the durable record / the stats fold.
type registry struct {
	mu sync.Mutex
	m  map[string]*int64
}

var reg = &registry{m: make(map[string]*int64)}

// counterKey joins a base metric and its dimension label into the interned registry
// key. An empty label is the bare base (e.g. marker_allow has no dimension).
func counterKey(base Metric, label string) string {
	if label == "" {
		return string(base)
	}
	return string(base) + "|" + label
}

// cell resolves (creating once) the *int64 for a key. The hot path hits the
// already-present branch lock-free after the first touch; the mutex only guards the
// rare first-insert of a key. Correct for a one-shot process (and safe under the
// goroutine-free hook flow regardless).
func (r *registry) cell(key string) *int64 {
	r.mu.Lock()
	p, ok := r.m[key]
	if !ok {
		p = new(int64)
		r.m[key] = p
	}
	r.mu.Unlock()
	return p
}

// Count increments base{label} by 1. The typed front door — callers pass a Metric
// const + a dimension, never a raw string, so a typo cannot mint a phantom metric.
func Count(base Metric, label string) {
	atomic.AddInt64(reg.cell(counterKey(base, label)), 1)
}

// Add increments base{label} by n (for SUM accumulators like latency_ns / claims /
// marker_count_at, where the mean is sum ÷ count at fold time).
func Add(base Metric, label string, n int64) {
	atomic.AddInt64(reg.cell(counterKey(base, label)), n)
}

// CountN increments base (no dimension) by 1 — the bare-counter convenience.
func CountN(base Metric) { Count(base, "") }

// latencyBuckets are the fixed le-upper-bounds (nanoseconds) for the histogram. The
// native path's whole point is sub-30 ms (docs/270 measured ~10 ms), so the buckets
// cluster below that and have a tail for a cold/contended outlier. A sample lands in
// the FIRST bucket whose bound it is <=; anything above the last bound lands in
// "+Inf". Stable, sorted, label-rendered once.
var latencyBuckets = []struct {
	bound int64
	label string
}{
	{1_000_000, "1ms"},
	{5_000_000, "5ms"},
	{10_000_000, "10ms"},
	{25_000_000, "25ms"},
	{50_000_000, "50ms"},
	{100_000_000, "100ms"},
	{250_000_000, "250ms"},
	{1_000_000_000, "1s"},
}

// observeLatency records one per-verb timing into the sum, the count, AND the
// histogram bucket — the three together give a mean and a distribution at fold time.
func observeLatency(verb string, elapsedNanos int64) {
	if elapsedNanos < 0 {
		elapsedNanos = 0
	}
	Add(MLatencyNanos, verb, elapsedNanos)
	Count(MLatencyCount, verb)
	Count(MLatencyBkt, verb+":"+latencyBucketLabel(elapsedNanos))
}

// latencyBucketLabel returns the le-bucket label a sample falls in (the first bound
// it is <=, else "+Inf").
func latencyBucketLabel(n int64) string {
	for _, b := range latencyBuckets {
		if n <= b.bound {
			return b.label
		}
	}
	return "+Inf"
}

// Snapshot returns a stable, sorted copy of every counter (key → value) — the read
// side the durable observation record and the `stats` fold consume. Sorting makes
// the rendered/serialized form deterministic (the byte-stable surface).
func Snapshot() []CounterSample {
	reg.mu.Lock()
	out := make([]CounterSample, 0, len(reg.m))
	for k, p := range reg.m {
		out = append(out, CounterSample{Key: k, Value: atomic.LoadInt64(p)})
	}
	reg.mu.Unlock()
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}

// CounterSample is one (interned-key, value) pair from Snapshot.
type CounterSample struct {
	Key   string
	Value int64
}

// resetMetricsForTest clears the registry — test-only, so each test sees a clean
// registry without process isolation. Not used by any non-test path.
func resetMetricsForTest() {
	reg.mu.Lock()
	reg.m = make(map[string]*int64)
	reg.mu.Unlock()
}
