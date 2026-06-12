package hook

// bench_test.go — the FIRST benchmarks in the dos Go module (docs/265). The whole
// reason this binary exists is performance: the per-tool-call hook hot path pays
// ~0.3–0.8 s of Python interpreter cold-start on EVERY call, and a static Go binary
// is meant to erase it (<30 ms, claimed ~10 ms). docs/125's claim was asserted in
// comments and pinned for CORRECTNESS by the parity corpus, but never MEASURED.
//
// These benchmarks measure the in-process decision cost — the work the binary does
// AFTER the OS has loaded it — split into three layers so the end-to-end process
// number (measured separately, out of band, against `python -m dos.cli`) can be
// attributed:
//
//   1. PURE decision (Decide / classifyStream / waitMarkerBudget): the verdict math,
//      no JSON, no disk. This is the floor — what the kernel's "pure classify"
//      discipline buys.
//   2. PARSE + decide (json.Unmarshal of a real CC event, then Decide): adds the
//      per-call deserialization the binary always pays.
//   3. BOUNDARY decision (DecidePretool against a real temp workspace): adds the WAL
//      read + the 11-file self-modify stat probe — the only disk the pretool path
//      touches. This is the honest in-process wall-clock of one native pretool call.
//
// If layer 3 is sub-millisecond, then the entire per-call budget on the native path
// is process spawn + this — and the whole ~0.3–0.8 s Python figure is interpreter
// cold-start, which the binary erases by construction. The end-to-end harness
// (scripts/bench_hook_e2e.py) confirms that at the process boundary.
//
// Run (dodging the hot-tree GOCACHE lock, see the memory note):
//   export GOCACHE=/c/work/dos/go/.cache_bench
//   go test -run '^$' -bench . -benchmem ./internal/hook/
// or compile-then-run:
//   go test -c -o /tmp/hook.test.exe ./internal/hook/
//   cd internal/hook && /tmp/hook.test.exe -test.run '^$' -test.bench . -test.benchmem

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// ---- Layer 1: the PURE decision (no JSON, no disk) ----------------------------

// benchInputsFull is the conservative full runtime set (every self-modify file
// present), so the SELF_MODIFY rung is fully armed — the worst case for the
// request-absolute predicate's path-prefix scan.
var benchInputsFull = Inputs{RuntimeFiles: dispatchRuntimeFiles}

// The realistic verdict spectrum — one representative event per outcome the PRE
// decider can reach. Built once; the benchmark only times Decide over them.
var (
	benchEvtReadPass    = eventFor("Read", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	benchEvtDocPass     = eventFor("Edit", "/work/workspace", map[string]any{"file_path": "docs/notes.md"})
	benchEvtSelfModify  = eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	benchEvtBashCollide = eventFor("Bash", "/work/workspace", map[string]any{"command": "rm src/dos/_tree.py"})
	benchEvtUnknownWarn = eventFor("Bash", "/work/workspace", map[string]any{"command": "make build"})
)

// benchLeaseSrc is a single held `src/**` lease — the contended-lane case that
// drives the disjointness scorer (collision deny) and the unknown-tree warn.
var benchLeaseSrc = []lease{{lane: "src", tree: []string{"src/**"}}}

func BenchmarkDecide_ReadPassthrough(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = Decide(benchEvtReadPass, benchInputsFull)
	}
}

func BenchmarkDecide_DisjointDocPassthrough(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = Decide(benchEvtDocPass, benchInputsFull)
	}
}

// The self-modify deny is the most expensive PURE decision: it renders the deny
// dialect (a pyJSONDumps of the full hookSpecificOutput map). Times decide + render.
func BenchmarkDecide_SelfModifyDeny(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		d := Decide(benchEvtSelfModify, benchInputsFull)
		_ = d.Render()
	}
}

func BenchmarkDecide_CollisionDeny(b *testing.B) {
	in := Inputs{LiveLeases: benchLeaseSrc, RuntimeFiles: dispatchRuntimeFiles}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		d := Decide(benchEvtBashCollide, in)
		_ = d.Render()
	}
}

func BenchmarkDecide_UnknownTreeWarn(b *testing.B) {
	in := Inputs{LiveLeases: benchLeaseSrc, RuntimeFiles: dispatchRuntimeFiles}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		d := Decide(benchEvtUnknownWarn, in)
		_ = d.Render()
	}
}

// ---- Layer 2: JSON parse + decide (the per-call deserialization) --------------

// A realistic CC PreToolUse event, marshaled to the bytes the binary reads off
// stdin. Self-modify so the decision also renders the deny dialect — the full
// parse→decide→render the heaviest pretool call performs in-process.
var benchPretoolStdin = func() []byte {
	ev := map[string]any{
		"hook_event_name": "PreToolUse",
		"session_id":      "bench-session",
		"cwd":             "/work/workspace",
		"tool_name":       "Edit",
		"tool_input":      map[string]any{"file_path": "src/dos/arbiter.py"},
	}
	out, _ := json.Marshal(ev)
	return out
}()

// BenchmarkParseAndDecide times json.Unmarshal of a real event + parseEvent + Decide
// + Render, with the runtime files injected (NO disk). This is the per-call CPU the
// binary pays minus the WAL read + the self-modify stat probe.
func BenchmarkParseAndDecide(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		var top map[string]any
		_ = json.Unmarshal(benchPretoolStdin, &top)
		ev := parseEvent(top)
		d := Decide(ev, benchInputsFull)
		_ = d.Render()
	}
}

// ---- Layer 3: the full boundary decision (WAL read + stat probe) --------------

// benchWorkspace builds a temp DOS-shaped workspace ONCE per benchmark: a `.dos/`
// dir with a lane-journal WAL holding `leaseCount` live ACQUIRE records, plus the
// 11 self-modify runtime files (empty) so the existence probe finds them all. This
// is the disk the pretool boundary actually touches. Returns the workspace root.
func benchWorkspace(b *testing.B, leaseCount int) string {
	b.Helper()
	root := b.TempDir()
	// The 11 runtime files the self-modify probe stats.
	for _, f := range dispatchRuntimeFiles {
		p := filepath.Join(root, filepath.FromSlash(f))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			b.Fatalf("mkdir %s: %v", p, err)
		}
		if err := os.WriteFile(p, []byte("# bench\n"), 0o644); err != nil {
			b.Fatalf("write %s: %v", p, err)
		}
	}
	// A lane-journal with leaseCount disjoint live leases (none colliding with the
	// benched Edit to src/dos/arbiter.py — so the call reaches the self-modify rung,
	// the realistic hot path on this repo).
	dosDir := filepath.Join(root, ".dos")
	if err := os.MkdirAll(dosDir, 0o755); err != nil {
		b.Fatalf("mkdir .dos: %v", err)
	}
	wal := filepath.Join(dosDir, "lane-journal.jsonl")
	f, err := os.Create(wal)
	if err != nil {
		b.Fatalf("create wal: %v", err)
	}
	for i := 0; i < leaseCount; i++ {
		rec := map[string]any{
			"op":      "ACQUIRE",
			"loop_ts": "2026-06-09T00:00:0" + strconv.Itoa(i%10) + "Z",
			"lane":    "lane" + strconv.Itoa(i),
			"lease": map[string]any{
				"lane":    "lane" + strconv.Itoa(i),
				"tree":    []string{"unrelated/dir" + strconv.Itoa(i) + "/**"},
				"loop_ts": "2026-06-09T00:00:0" + strconv.Itoa(i%10) + "Z",
			},
		}
		line, _ := json.Marshal(rec)
		if _, err := f.Write(append(line, '\n')); err != nil {
			b.Fatalf("write wal line: %v", err)
		}
	}
	_ = f.Close()
	return root
}

// benchPretoolStdinForWS rewrites the cwd in the event to the temp workspace so the
// path resolution + repo-relative logic runs against the real root.
func benchPretoolStdinForWS(b *testing.B, ws string) []byte {
	b.Helper()
	ev := map[string]any{
		"hook_event_name": "PreToolUse",
		"session_id":      "bench-session",
		"cwd":             ws,
		"tool_name":       "Edit",
		"tool_input":      map[string]any{"file_path": filepath.ToSlash(filepath.Join(ws, "src/dos/arbiter.py"))},
	}
	out, err := json.Marshal(ev)
	if err != nil {
		b.Fatalf("marshal event: %v", err)
	}
	return out
}

// BenchmarkDecidePretool_FullBoundary is the headline in-process number: one full
// native pretool call — parse the event, resolve the workspace, READ + FOLD the WAL,
// STAT the 11 runtime files, decide, render the deny dialect. Everything except the
// OS process spawn and the journal append (a deny does append; measured separately).
// This is what the binary costs once it is running; the end-to-end harness adds the
// spawn on top.
func BenchmarkDecidePretool_FullBoundary(b *testing.B) {
	ws := benchWorkspace(b, 4) // a typical small fleet: 4 live leases
	stdin := benchPretoolStdinForWS(b, ws)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		res := DecidePretool(stdin, ws, "", nil)
		if res.Stdout == "" {
			b.Fatal("expected a self-modify deny dialect on stdout")
		}
	}
}

// BenchmarkDecidePretool_PassthroughBoundary is the COMMON case: an idle workspace
// (no lane held) where a read passes through (empty stdout), but still pays the WAL
// read (an empty/absent journal) + the 11-file stat probe. This is the per-call cost
// of the overwhelming majority of hook fires on a quiescent repo — the number that
// dominates a real single-agent session's total hook overhead.
//
// NOTE: this uses leaseCount=0 deliberately. With a live lease whose tree is known,
// a Read (empty-but-known requested tree) is REFUSED by the disjointness predicate's
// "empty requested tree vs a known lease → refuse" branch — a faithful port of
// admission.py:201, so a read CAN be denied while a lane is held. The common single-
// agent case is the idle repo, which is what this benches.
func BenchmarkDecidePretool_PassthroughBoundary(b *testing.B) {
	ws := benchWorkspace(b, 0) // idle: no held lane, so a read passes through
	ev := map[string]any{
		"hook_event_name": "PreToolUse",
		"session_id":      "bench-session",
		"cwd":             ws,
		"tool_name":       "Read",
		"tool_input":      map[string]any{"file_path": "src/dos/arbiter.py"},
	}
	stdin, _ := json.Marshal(ev)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		res := DecidePretool(stdin, ws, "", nil)
		if res.Stdout != "" {
			b.Fatalf("read should pass through on an idle repo, got %q", res.Stdout)
		}
	}
}

// ---- WAL replay scaling (how the fold grows with fleet size) ------------------

// benchEntries builds n live ACQUIRE entries in memory (the readJournal output
// shape) so replayJournal can be benched without disk. Disjoint trees, so all n
// stay live (the worst case for the fold — nothing is released).
func benchEntries(n int) []map[string]any {
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, map[string]any{
			"op":      "ACQUIRE",
			"loop_ts": "2026-06-09T00:00:00Z",
			"lane":    "lane" + strconv.Itoa(i),
			"lease": map[string]any{
				"lane":    "lane" + strconv.Itoa(i),
				"tree":    []string{"dir" + strconv.Itoa(i) + "/**"},
				"loop_ts": "2026-06-09T00:00:00Z",
			},
		})
	}
	return out
}

func benchReplay(b *testing.B, n int) {
	entries := benchEntries(n)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = replayJournal(entries)
	}
}

func BenchmarkReplayJournal_1(b *testing.B)   { benchReplay(b, 1) }
func BenchmarkReplayJournal_8(b *testing.B)   { benchReplay(b, 8) }
func BenchmarkReplayJournal_64(b *testing.B)  { benchReplay(b, 64) }
func BenchmarkReplayJournal_256(b *testing.B) { benchReplay(b, 256) }

// ---- PostToolUse stream classification (the repeat/stall verdict) -------------

// benchStream builds a trailing run of n identical steps (the REPEATING/STALLED
// shape) so classifyStream does its full trailing-run scan. The steps must carry a
// non-empty resultDigest, else key() reports ok=false and the run never forms (a
// no-result step breaks a run) — so this benches the real consecutive-identical scan.
func benchStream(n int) []streamStep {
	out := make([]streamStep, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, streamStep{
			toolName:     "Read",
			argsDigest:   "abc123def4567890",
			resultDigest: "0123456789abcdef",
		})
	}
	return out
}

func benchClassify(b *testing.B, n int) {
	steps := benchStream(n)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = classifyStream(steps)
	}
}

func BenchmarkClassifyStream_8(b *testing.B)   { benchClassify(b, 8) }
func BenchmarkClassifyStream_64(b *testing.B)  { benchClassify(b, 64) }

// ---- the marker budget (pure) -------------------------------------------------

func BenchmarkWaitMarkerBudget(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = waitMarkerBudget(2, 4) // budget remains -> builds the allow reason string
	}
}

// ---- the byte-exact serializer (on the deny path) -----------------------------

// The deny dialect map, marshaled the GHF byte-exact way (sort_keys + Python str
// escaping) — the most-allocating step of a deny. Times the serializer alone.
var benchDenyDialect = denyPayload(
	"DOS PRE-admission: lane 'Edit' would edit the orchestrator's own running code "+
		"(src/dos/arbiter.py) — refusing to let a live loop rewrite the kernel that is "+
		"adjudicating it (SELF_MODIFY). Pass --force only if you are deliberately editing "+
		"the kernel between loop runs.", "")

func BenchmarkPyJSONDumps_DenyDialect(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = pyJSONDumps(benchDenyDialect)
	}
}
