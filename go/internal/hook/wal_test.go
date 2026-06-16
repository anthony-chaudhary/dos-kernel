package hook

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// FQ-532 / docs/281 Defect 1: the PRE-admission hook folds the lane-journal WAL
// into a live-lease set with NO clock — so a crashed loop's un-RELEASEd ACQUIRE is
// an immortal phantom lane the hook enforces against forever. These tests pin the
// TTL/heartbeat expiry that self-heals an orphan out of the LIVE set the hook reads,
// while the structural `replayJournal` stays a clock-free, parity-faithful fold.

// writeWAL writes the given JSONL entries to a tmp lane-journal and returns the path.
func writeWAL(t *testing.T, entries []map[string]any) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "lane-journal.jsonl")
	f, err := os.Create(p)
	if err != nil {
		t.Fatalf("create WAL: %v", err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	for _, e := range entries {
		if err := enc.Encode(e); err != nil {
			t.Fatalf("encode WAL entry: %v", err)
		}
	}
	return p
}

// acquireEntry builds an ACQUIRE WAL entry with the given identity + stamps, in the
// nested-lease shape `lane_journal.acquire_entry` writes.
func acquireEntry(lane, acquiredAt, heartbeatAt string, ttlMinutes float64) map[string]any {
	lease := map[string]any{
		"lane":        lane,
		"tree":        []string{lane + "/**"},
		"loop_ts":     "2026-06-09T00:00:00Z",
		"acquired_at": acquiredAt,
		"ttl_minutes": ttlMinutes,
	}
	if heartbeatAt != "" {
		lease["heartbeat_at"] = heartbeatAt
	}
	return map[string]any{
		"op":      "ACQUIRE",
		"lane":    lane,
		"loop_ts": "2026-06-09T00:00:00Z",
		"lease":   lease,
	}
}

func TestLiveLeasesExpiresStaleOrphan(t *testing.T) {
	now := time.Date(2026, 6, 9, 12, 0, 0, 0, time.UTC)
	// One fresh lease (beat just now) and one orphan whose newest beat is hours old,
	// well past its 50-minute TTL + 5-minute grace.
	fresh := acquireEntry("src", "2026-06-09T11:59Z", "2026-06-09T11:59Z", 50)
	orphan := acquireEntry("docs", "2026-06-09T08:00Z", "2026-06-09T08:00Z", 50)
	wal := writeWAL(t, []map[string]any{fresh, orphan})

	live := liveLeasesFromWALAt(wal, now)
	if len(live) != 1 {
		t.Fatalf("expected 1 live lease after expiry, got %d: %+v", len(live), live)
	}
	if live[0].lane != "src" {
		t.Fatalf("expected the fresh 'src' lease to survive, got %q", live[0].lane)
	}
}

func TestLiveLeasesKeepsFreshLease(t *testing.T) {
	now := time.Date(2026, 6, 9, 12, 0, 0, 0, time.UTC)
	// A lease beaten 10 minutes ago — within 50min TTL + 5min grace → still live.
	fresh := acquireEntry("src", "2026-06-09T11:30Z", "2026-06-09T11:50Z", 50)
	wal := writeWAL(t, []map[string]any{fresh})

	live := liveLeasesFromWALAt(wal, now)
	if len(live) != 1 {
		t.Fatalf("a within-TTL lease must stay live; got %d leases", len(live))
	}
}

func TestLiveLeasesHeartbeatRefreshesFreshness(t *testing.T) {
	now := time.Date(2026, 6, 9, 12, 0, 0, 0, time.UTC)
	// Acquired hours ago (past TTL) but HEARTBEAT just now → the fold's replay sets
	// heartbeat_at to the beat, so the lease is fresh and must survive expiry.
	acq := acquireEntry("src", "2026-06-09T08:00Z", "2026-06-09T08:00Z", 50)
	beat := map[string]any{
		"op":           "HEARTBEAT",
		"lane":         "src",
		"loop_ts":      "2026-06-09T00:00:00Z",
		"heartbeat_at": "2026-06-09T11:59Z",
	}
	wal := writeWAL(t, []map[string]any{acq, beat})

	live := liveLeasesFromWALAt(wal, now)
	if len(live) != 1 {
		t.Fatalf("a freshly-heartbeaten lease must stay live; got %d leases", len(live))
	}
}

func TestLiveLeasesKeepsLeaseWithNoStamp(t *testing.T) {
	now := time.Date(2026, 6, 9, 12, 0, 0, 0, time.UTC)
	// A lease with NO parseable stamp: we cannot PROVE it stale, so the fail-safe
	// direction is to KEEP it (preserve a possibly-genuine claim). The cross-process
	// PID probe / external SCAVENGE handles a truly-dead no-stamp orphan; the hook
	// must never invent a collision but also must not drop an unprovable one here.
	noStamp := map[string]any{
		"op":      "ACQUIRE",
		"lane":    "src",
		"loop_ts": "2026-06-09T00:00:00Z",
		"lease": map[string]any{
			"lane":    "src",
			"tree":    []string{"src/**"},
			"loop_ts": "2026-06-09T00:00:00Z",
		},
	}
	wal := writeWAL(t, []map[string]any{noStamp})

	live := liveLeasesFromWALAt(wal, now)
	if len(live) != 1 {
		t.Fatalf("an unparseable-stamp lease must be KEPT (fail-safe); got %d", len(live))
	}
}

func TestLiveLeasesDefaultTTLForMissingTTLMinutes(t *testing.T) {
	now := time.Date(2026, 6, 9, 12, 0, 0, 0, time.UTC)
	// A lease declaring NO ttl_minutes, beaten 2 hours ago: the default 50-min
	// backstop (+grace) still expires it — a legacy/malformed ACQUIRE cannot be
	// immortal.
	stale := map[string]any{
		"op":      "ACQUIRE",
		"lane":    "src",
		"loop_ts": "2026-06-09T00:00:00Z",
		"lease": map[string]any{
			"lane":         "src",
			"tree":         []string{"src/**"},
			"loop_ts":      "2026-06-09T00:00:00Z",
			"acquired_at":  "2026-06-09T10:00Z",
			"heartbeat_at": "2026-06-09T10:00Z",
		},
	}
	wal := writeWAL(t, []map[string]any{stale})

	live := liveLeasesFromWALAt(wal, now)
	if len(live) != 0 {
		t.Fatalf("a 2h-old lease with no declared TTL must expire under the default backstop; got %d", len(live))
	}
}

func TestReplayJournalStaysClockFreeStructural(t *testing.T) {
	// The structural fold must NOT expire — it is the history-faithful, parity-pinned
	// reconstruction. An hours-old orphan stays in replayJournal's output; only the
	// LiveLeasesFromWAL boundary applies expiry.
	orphan := acquireEntry("docs", "2026-06-09T08:00Z", "2026-06-09T08:00Z", 50)
	got := replayJournal([]map[string]any{orphan})
	if len(got) != 1 {
		t.Fatalf("structural replayJournal must keep every un-RELEASEd ACQUIRE regardless of age; got %d", len(got))
	}
	if got[0].lane != "docs" {
		t.Fatalf("expected the structural fold to surface 'docs', got %q", got[0].lane)
	}
}

func TestParseLeaseStampMinuteAndSecond(t *testing.T) {
	// Both resolutions must parse — the host writes minute, a replay()-restored
	// heartbeat_at carries second. A second-resolution stamp that failed to parse
	// would make the TTL backstop silently skip (the immortal-by-TTL hole).
	if _, ok := parseLeaseStamp("2026-06-09T11:59Z"); !ok {
		t.Fatal("minute-resolution stamp must parse")
	}
	if _, ok := parseLeaseStamp("2026-06-09T11:59:30Z"); !ok {
		t.Fatal("second-resolution stamp must parse")
	}
	if _, ok := parseLeaseStamp("not-a-stamp"); ok {
		t.Fatal("a malformed stamp must NOT parse")
	}
}

// ── proc_starttime rides the structural fold (Py↔Go parity, docs/95 §4.2) ──

func TestReplayCarriesProcStarttime(t *testing.T) {
	// A lease minted with proc_starttime must replay WITH it — the PID-reuse
	// baseline the same-host probe corroborates against. Mirror of the Python
	// test_lane_journal round-trip; keeps the structural fold byte-parity.
	acq := map[string]any{
		"op": "ACQUIRE", "lane": "apply", "loop_ts": "t1",
		"lease": map[string]any{
			"lane": "apply", "loop_ts": "t1", "host_id": "host-a",
			"pid": 4242.0, "proc_starttime": 987654.0,
		},
	}
	got := replayJournalFull([]map[string]any{acq})
	if len(got) != 1 {
		t.Fatalf("expected 1 live lease, got %d", len(got))
	}
	if st, _ := asFloat(got[0]["proc_starttime"]); st != 987654 {
		t.Fatalf("proc_starttime must ride the fold; got %v", got[0]["proc_starttime"])
	}
}

func TestReplayInlineAcquireCarriesProcStarttime(t *testing.T) {
	// The forward-compat inline path (fields flattened, no nested "lease") must
	// also reconstruct proc_starttime from the known-keys list.
	inline := map[string]any{
		"op": "ACQUIRE", "lane": "apply", "lane_kind": "concurrent",
		"loop_ts": "t1", "host_id": "host-a", "pid": 4242.0,
		"acquired_at": "2026-06-01T14:00:03Z", "proc_starttime": 555.0,
	}
	got := replayJournalFull([]map[string]any{inline})
	if len(got) != 1 {
		t.Fatalf("expected 1 live lease, got %d", len(got))
	}
	if st, _ := asFloat(got[0]["proc_starttime"]); st != 555 {
		t.Fatalf("inline proc_starttime must reconstruct; got %v", got[0]["proc_starttime"])
	}
}

func TestAdoptChangingPidDropsStaleProcStarttime(t *testing.T) {
	// ADOPT to a new pid WITHOUT a new baseline must DROP the old proc_starttime —
	// never keep a stale stamp that would falsely accuse the new pid of being reused
	// (the safe degrade to existence-only). Mirror of the Python lane_journal ADOPT.
	acq := map[string]any{
		"op": "ACQUIRE", "lane": "apply", "loop_ts": "t1",
		"lease": map[string]any{
			"lane": "apply", "loop_ts": "t1", "host_id": "host-a",
			"pid": 4242.0, "proc_starttime": 111.0,
		},
	}
	adopt := map[string]any{
		"op": "ADOPT", "lane": "apply", "loop_ts": "t1",
		"holder": "host-b:99", "pid": 99.0, "host_id": "host-a",
		"heartbeat_at": "2026-06-01T15:00:00Z",
	}
	got := replayJournalFull([]map[string]any{acq, adopt})
	if len(got) != 1 {
		t.Fatalf("expected 1 live lease, got %d", len(got))
	}
	if asInt(got[0]["pid"]) != 99 {
		t.Fatalf("ADOPT must rewrite pid to 99; got %v", got[0]["pid"])
	}
	if _, present := got[0]["proc_starttime"]; present {
		t.Fatal("ADOPT changing the pid without a new baseline must DROP the stale proc_starttime")
	}
}

func TestAdoptWithNewBaselineRewritesProcStarttime(t *testing.T) {
	// ADOPT WITH a supplied baseline rewrites proc_starttime to the new holder's
	// stamp — so the reuse defense keeps corroborating the live pid.
	acq := map[string]any{
		"op": "ACQUIRE", "lane": "apply", "loop_ts": "t1",
		"lease": map[string]any{
			"lane": "apply", "loop_ts": "t1", "host_id": "host-a",
			"pid": 4242.0, "proc_starttime": 111.0,
		},
	}
	adopt := map[string]any{
		"op": "ADOPT", "lane": "apply", "loop_ts": "t1",
		"holder": "host-b:99", "pid": 99.0, "host_id": "host-a",
		"proc_starttime": 222.0, "heartbeat_at": "2026-06-01T15:00:00Z",
	}
	got := replayJournalFull([]map[string]any{acq, adopt})
	if len(got) != 1 {
		t.Fatalf("expected 1 live lease, got %d", len(got))
	}
	if st, _ := asFloat(got[0]["proc_starttime"]); st != 222 {
		t.Fatalf("ADOPT with a new baseline must rewrite proc_starttime to 222; got %v", got[0]["proc_starttime"])
	}
}
