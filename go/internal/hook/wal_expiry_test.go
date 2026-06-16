package hook

// Phantom-lease self-heal parity (FQ-532 / docs/281 Defect 1): the Go WAL reader
// `liveLeasesFromWALAt` must drop a crashed worker's un-RELEASEd ACQUIRE once it
// ages past its TTL+grace, instead of the PRE-admission hook enforcing a phantom
// lane on every tool call until an external SCAVENGE lands. Mirrors the Python
// `tests/test_lane_lease_expiry.py` semantics with the same clock injection seam.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// writeAcquireWAL writes a one-record lane-journal holding a single live ACQUIRE
// with the given stamp/ttl, and returns its path (the disk LiveLeasesFromWAL reads).
func writeAcquireWAL(t *testing.T, lane, acquiredAt string, ttlMinutes float64) string {
	t.Helper()
	dir := t.TempDir()
	wal := filepath.Join(dir, "lane-journal.jsonl")
	rec := map[string]any{
		"op":      "ACQUIRE",
		"loop_ts": acquiredAt,
		"lane":    lane,
		"lease": map[string]any{
			"lane":        lane,
			"tree":        []string{lane + "/**"},
			"loop_ts":     acquiredAt,
			"acquired_at": acquiredAt,
			"ttl_minutes": ttlMinutes,
			"host_id":     "DESKTOP-TEST",
			"pid":         1,
		},
	}
	line, _ := json.Marshal(rec)
	if err := os.WriteFile(wal, append(line, '\n'), 0o644); err != nil {
		t.Fatalf("write wal: %v", err)
	}
	return wal
}

func stampMinutesAgo(now time.Time, mins float64) string {
	return now.Add(-time.Duration(mins * float64(time.Minute))).Format("2006-01-02T15:04:05Z")
}

// noHost is the thisHost arg for the pure TTL cases below: their lz carries no
// pid and no host_id, so the same-host proc rung (signal b) is inert and only the
// TTL/heartbeat backstop (signal a) decides — exactly what these cases pin.
const noHost = "DESKTOP-TEST"

func TestLeaseExpired_StaleByTTLIsExpired(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	// acquired 120 min ago, ttl 50 → 120 > 50+5 → expired
	lz := map[string]any{"acquired_at": stampMinutesAgo(now, 120), "ttl_minutes": 50.0}
	if !leaseExpired(lz, now, noHost) {
		t.Fatal("a lease 120m old with ttl 50 must be expired")
	}
}

func TestLeaseExpired_FreshIsKept(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	lz := map[string]any{"acquired_at": stampMinutesAgo(now, 1), "ttl_minutes": 50.0}
	if leaseExpired(lz, now, noHost) {
		t.Fatal("a 1m-old lease within ttl must NOT be expired")
	}
}

func TestLeaseExpired_NoStampIsKept(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	// No parseable stamp → cannot prove stale → kept (fail-safe).
	if leaseExpired(map[string]any{"ttl_minutes": 50.0}, now, noHost) {
		t.Fatal("a lease with no parseable stamp must be kept (cannot prove stale)")
	}
}

func TestLeaseExpired_DefaultTTLBackstop(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	// No ttl_minutes declared → default backstop (50). 120m old → expired.
	lz := map[string]any{"acquired_at": stampMinutesAgo(now, 120)}
	if !leaseExpired(lz, now, noHost) {
		t.Fatal("a lease with no ttl_minutes must age out by the default backstop")
	}
}

func TestLeaseExpired_HeartbeatWinsOverAcquiredAt(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	// Acquired 3h ago but heartbeating 1m ago → alive (heartbeat_at wins).
	lz := map[string]any{
		"acquired_at":  stampMinutesAgo(now, 180),
		"heartbeat_at": stampMinutesAgo(now, 1),
		"ttl_minutes":  50.0,
	}
	if leaseExpired(lz, now, noHost) {
		t.Fatal("a recently-heartbeating lease must be kept even with an old acquired_at")
	}
}

// ── signal (b): the same-host proc-liveness rung (docs/95, the new contribution) ──

func TestLeaseExpired_DeadLocalPidReclaimsStaleLease(t *testing.T) {
	// A heartbeat-stale lease (past grace, but WITHIN the TTL backstop) whose local
	// holder pid the OS confirms gone is reclaimed NOW — the phantom-lock window the
	// hook previously held for the full TTL. Uses an impossible pid (confidently
	// gone) on the local host. Linux-only (the rung is absent elsewhere).
	if runtime.GOOS != "linux" {
		t.Skip("proc-liveness rung engages only on linux")
	}
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	lz := map[string]any{
		// 10 min old: past the 5-min grace, but well within the 50-min TTL, so
		// signal (a) alone would KEEP it. Only signal (b) can reclaim it now.
		"heartbeat_at": stampMinutesAgo(now, 10),
		"ttl_minutes":  50.0,
		"host_id":      thisHostID(),
		"pid":          2000000000, // impossible → confidently gone
	}
	if !leaseExpired(lz, now, thisHostID()) {
		t.Fatal("a heartbeat-stale lease with a confirmed-dead LOCAL pid must reclaim now")
	}
}

func TestLeaseExpired_DeadPidDoesNotEvictFreshLease(t *testing.T) {
	// THE gate: a FRESH lease (beat within grace) is KEPT even if its pid is dead —
	// preserves the ephemeral-acquirer reservation (acquire subprocess journals then
	// exits; its pid is "dead" while the reservation is valid). A bare dead-pid
	// eviction here would double-book the region.
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	lz := map[string]any{
		"heartbeat_at": stampMinutesAgo(now, 1), // fresh → within grace
		"ttl_minutes":  50.0,
		"host_id":      thisHostID(),
		"pid":          2000000000, // dead, but the lease is fresh
	}
	if leaseExpired(lz, now, thisHostID()) {
		t.Fatal("a FRESH lease must be kept regardless of a dead pid (no double-book)")
	}
}

func TestLeaseExpired_ForeignHostPidNeverProbed(t *testing.T) {
	// A pid recorded on ANOTHER host is meaningless here — signal (b) must not fire;
	// the lease falls to the TTL backstop alone. 10 min old + ttl 50 → kept.
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	lz := map[string]any{
		"heartbeat_at": stampMinutesAgo(now, 10), // past grace, within TTL
		"ttl_minutes":  50.0,
		"host_id":      "some-other-box",
		"pid":          2000000000, // dead HERE, but it's not our host
	}
	if leaseExpired(lz, now, thisHostID()) {
		t.Fatal("a foreign-host lease must NOT be reclaimed by the local proc probe")
	}
}

func TestLiveLeasesFromWAL_DropsPhantom(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	wal := writeAcquireWAL(t, "apply", stampMinutesAgo(now, 180), 50.0)
	live := liveLeasesFromWALAt(wal, now)
	for _, l := range live {
		if l.lane == "apply" {
			t.Fatal("the contention read must drop the phantom 'apply' orphan")
		}
	}
}

func TestLiveLeasesFromWAL_KeepsFreshLease(t *testing.T) {
	now := time.Date(2026, 6, 9, 23, 0, 0, 0, time.UTC)
	wal := writeAcquireWAL(t, "apply", stampMinutesAgo(now, 1), 50.0)
	live := liveLeasesFromWALAt(wal, now)
	found := false
	for _, l := range live {
		if l.lane == "apply" {
			found = true
		}
	}
	if !found {
		t.Fatal("a fresh live lease must still gate (be present in the contention read)")
	}
}
