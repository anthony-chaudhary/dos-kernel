package hook

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// The default TTL backstop (minutes) for an admission-facing lease whose own
// `ttl_minutes` is missing / unparseable / non-positive. Mirrors the Python
// `lane_lease._DEFAULT_LIVE_TTL_MINUTES` (== lease_health.LeaseHealthPolicy.
// ttl_minutes, the job's historical LANE_LEASE_TTL) so a malformed/legacy ACQUIRE
// with no declared TTL still cannot be immortal.
const defaultLiveTTLMinutes = 50.0

// The heartbeat-freshness grace added on top of a lease's own `ttl_minutes` before
// the live-set read treats it as expired — the same `_LIVE_TTL_GRACE_MINUTES` the
// Python `lane_lease` filter uses, so a lease merely a beat-or-two late (a busy-but-
// healthy worker's eventual-consistency window) is never elided; only one gone quiet
// well past its own declared TTL.
const liveTTLGraceMinutes = 5.0

// State-mutating WAL ops — `dos.lane_journal._STATE_MUTATING_OPS`. Only these fold
// into the live-lease set; REFUSE/HALT/ENFORCE/ATTEMPT/_CORRUPT are recorded but
// ignored for state.
var stateMutatingOps = map[string]struct{}{
	"ACQUIRE": {}, "RELEASE": {}, "HEARTBEAT": {},
	"SCAVENGE": {}, "RECONCILE": {}, "ADOPT": {},
}

// readJournal returns every journal entry in append order — port of
// `dos.lane_journal.read_all`. A torn TRAILING line (crash mid-append) is
// tolerated (dropped); an earlier corrupt line is kept as a `_CORRUPT` sentinel
// (replay ignores it for state, exactly like Python). A missing/unreadable file
// yields no entries (the fail-safe: a WAL read fault never denies a real call —
// it degrades to "no leases").
func readJournal(path string) []map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil // absent or unreadable -> no entries (fail-safe)
	}
	// splitlines() semantics: split on \n, drop a trailing empty line.
	text := strings.ReplaceAll(string(data), "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")
	// Drop a single trailing empty element from a final newline.
	for len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
		break
	}
	var out []map[string]any
	for i, line := range lines {
		s := strings.TrimSpace(line)
		if s == "" {
			continue
		}
		var obj map[string]any
		if err := json.Unmarshal([]byte(s), &obj); err != nil {
			// Tolerate ONLY a torn final line; an earlier corrupt line -> sentinel.
			if i == len(lines)-1 {
				break
			}
			out = append(out, map[string]any{"op": "_CORRUPT", "_raw": s, "_line": i})
			continue
		}
		if obj != nil {
			out = append(out, obj)
		}
	}
	return out
}

// leaseIdentity is the true lease identity (loop_ts, lane) —
// `dos.lane_journal._lease_identity`.
func leaseIdentity(rec map[string]any) [2]string {
	return [2]string{asStr(rec["loop_ts"]), asStr(rec["lane"])}
}

// replayJournalFull folds the decision sequence into the authoritative live-lease
// set, returning the FULL folded lease maps in first-acquired order — port of
// `dos.lane_journal.replay` (CHECKPOINT / ADOPT / HEARTBEAT semantics match Python
// exactly). This keeps the timestamp/ttl fields (`heartbeat_at` / `acquired_at` /
// `ttl_minutes`) the TTL-expiry filter needs; `replayJournal` projects this to the
// lane+tree []lease the disjointness check uses, and `LiveLeasesFromWAL` expires the
// stale before projecting.
func replayJournalFull(entries []map[string]any) []map[string]any {
	type key = [2]string
	live := map[key]map[string]any{}
	var order []key

	forget := func(k key) {
		delete(live, k)
		for i, o := range order {
			if o == k {
				order = append(order[:i], order[i+1:]...)
				break
			}
		}
	}

	for _, e := range entries {
		op := asStr(e["op"])
		if op == "CHECKPOINT" {
			live = map[key]map[string]any{}
			order = nil
			if payload, ok := e["leases"].([]any); ok {
				for _, raw := range payload {
					lz, ok := raw.(map[string]any)
					if !ok {
						continue
					}
					k := leaseIdentity(lz)
					if k[0] == "" && k[1] == "" {
						continue
					}
					if _, exists := live[k]; !exists {
						order = append(order, k)
					}
					live[k] = copyMap(lz)
				}
			}
			continue
		}
		if _, mut := stateMutatingOps[op]; !mut {
			continue // REFUSE/HALT/ENFORCE/ATTEMPT/_CORRUPT/unknown
		}
		k := leaseIdentity(e)
		if k[0] == "" && k[1] == "" {
			continue
		}
		switch op {
		case "ACQUIRE", "RECONCILE":
			var lz map[string]any
			if nested, ok := e["lease"].(map[string]any); ok {
				lz = copyMap(nested)
			} else {
				// Forward-compat: inline lease fields on the entry.
				lz = map[string]any{}
				for _, fld := range []string{
					"lane", "lane_kind", "tree", "loop_ts", "host_id",
					"pid", "acquired_at", "heartbeat_at", "ttl_minutes",
					"holder", "run_id",
				} {
					if v, ok := e[fld]; ok {
						lz[fld] = v
					}
				}
			}
			if _, exists := live[k]; !exists {
				order = append(order, k)
			}
			live[k] = lz
		case "RELEASE", "SCAVENGE":
			forget(k)
		case "HEARTBEAT":
			if cur, ok := live[k]; ok {
				hb := e["heartbeat_at"]
				if hb == nil {
					hb = e["ts"]
				}
				if hb != nil {
					cur["heartbeat_at"] = hb
				}
			}
		case "ADOPT":
			if cur, ok := live[k]; ok {
				for _, fld := range []string{"holder", "pid", "host_id"} {
					if v, ok := e[fld]; ok && v != nil {
						cur[fld] = v
					}
				}
				hb := e["heartbeat_at"]
				if hb == nil {
					hb = e["ts"]
				}
				if hb != nil {
					cur["heartbeat_at"] = hb
				}
			}
		}
	}

	out := make([]map[string]any, 0, len(order))
	for _, k := range order {
		if lz, ok := live[k]; ok {
			out = append(out, lz)
		}
	}
	return out
}

// replayJournal folds the decision sequence into the authoritative live-lease set,
// projected to the lane+tree []lease the disjointness check needs — port of
// `dos.lane_journal.replay`. Pure structural fold (no clock): every un-RELEASEd
// ACQUIRE is "live", regardless of age. The TTL/heartbeat expiry that self-heals a
// crashed worker's orphan is applied SEPARATELY at the WAL-read boundary
// (`LiveLeasesFromWAL`), so this stays the byte-for-byte structural parity of Python
// `replay` the corpus pins.
func replayJournal(entries []map[string]any) []lease {
	full := replayJournalFull(entries)
	out := make([]lease, 0, len(full))
	for _, lz := range full {
		out = append(out, lease{
			lane:  asStr(lz["lane"]),
			tree:  asStrSlice(lz["tree"]),
			runID: asStr(lz["run_id"]), // issue #188 — the lineage join key
		})
	}
	return out
}

// leaseExpired reports whether a folded lease is past its TTL/heartbeat window —
// the hard backstop port of the Python `lane_lease._lease_is_dead` staleness signal
// (signal (a)). A lease's newest credible stamp (`heartbeat_at`, else `acquired_at`)
// older than its own `ttl_minutes` (or the default backstop) plus a grace is
// confidently stale; a fresh/heartbeating lease never is. Unlike the Python filter
// the hook does NOT do the cross-process PID probe (signal (b)) — a hook has no
// business reading another box's process table, and the TTL backstop alone closes
// the immortal-orphan phantom (FQ-532): a crashed loop stops beating and ages out.
//
// FAIL-SAFE: a lease with NO parseable stamp is treated as NOT expired here (kept) —
// we cannot prove it stale, so it keeps its claim (the genuine-collision-preserving
// direction). This filter can only ever SHRINK the live set by dropping the provably
// stale, never admit a colliding live worker.
func leaseExpired(lz map[string]any, now time.Time) bool {
	stamp := asStr(lz["heartbeat_at"])
	if stamp == "" {
		stamp = asStr(lz["acquired_at"])
	}
	hb, ok := parseLeaseStamp(stamp)
	if !ok {
		return false // unparseable/absent stamp → cannot prove stale → keep
	}
	ttl := defaultLiveTTLMinutes
	if v, ok := asFloat(lz["ttl_minutes"]); ok && v > 0 {
		ttl = v
	}
	ageMin := now.Sub(hb).Minutes()
	return ageMin > (ttl + liveTTLGraceMinutes)
}

// parseLeaseStamp parses a lane-journal ISO stamp at minute OR second resolution —
// port of `lease_health.parse_iso`. Accepting BOTH is load-bearing: the host writes
// the minute form (`2006-01-02T15:04Z`) and a `replay()`-restored `heartbeat_at`
// carries the second form (`2006-01-02T15:04:05Z`); a second-resolution stamp fed to
// a minute-only parser would fail and make the TTL backstop silently skip — the
// immortal-by-TTL hole. Minute is tried first (the hot path); second is the superset.
func parseLeaseStamp(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	for _, layout := range []string{"2006-01-02T15:04Z", "2006-01-02T15:04:05Z"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), true
		}
	}
	return time.Time{}, false
}

// asFloat coerces a decoded JSON number (`ttl_minutes` is a JSON number → float64
// after json.Unmarshal) to float64. Returns ok=false for a non-number.
func asFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}

// LiveLeasesFromWAL reads + folds the WAL at `journalPath` into the live leases the
// PRE admission check sees — the `pretool_sensor.live_leases_for` boundary I/O — then
// EXPIRES the provably-stale (`leaseExpired`) so a crashed worker's un-RELEASEd
// ACQUIRE self-heals out of the admission set the instant it ages past its TTL,
// instead of the hook enforcing a phantom lane on every tool call until an external
// SCAVENGE lands (FQ-532 / docs/281 Defect 1). The structural fold stays pure (so
// `dos journal replay` is byte-identical); expiry is a live-set-only filter layered
// here at the reader with the injected clock, mirroring the Python `lane_lease.
// live_leases` → `_expire_dead` boundary. Any fault degrades to no leases (safe).
func LiveLeasesFromWAL(journalPath string) []lease {
	return liveLeasesFromWALAt(journalPath, time.Now().UTC())
}

// liveLeasesFromWALAt is the clock-injected core (the seam a test pins): fold the WAL,
// drop the leases expired at `now`, project the survivors to lane+tree.
func liveLeasesFromWALAt(journalPath string, now time.Time) []lease {
	full := replayJournalFull(readJournal(journalPath))
	out := make([]lease, 0, len(full))
	for _, lz := range full {
		if leaseExpired(lz, now) {
			continue // crashed-orphan past TTL → self-heal out of the admission set
		}
		out = append(out, lease{
			lane:  asStr(lz["lane"]),
			tree:  asStrSlice(lz["tree"]),
			runID: asStr(lz["run_id"]), // issue #188 — the lineage join key
		})
	}
	return out
}

// ExistingRuntimeFiles returns the dispatchRuntimeFiles that actually exist under
// `workspace` — port of `dos.self_modify.existing_runtime_files`. This is what
// makes the SELF_MODIFY guard workspace-aware: against a foreign repo none resolve
// (-> () -> a "**/*" lane touches nothing -> admit); against the DOS repo all
// resolve. A falsy workspace yields the full static set (conservative).
func ExistingRuntimeFiles(workspace string) []string {
	if workspace == "" {
		return append([]string(nil), dispatchRuntimeFiles...)
	}
	var out []string
	for _, f := range dispatchRuntimeFiles {
		if _, err := os.Stat(filepath.Join(workspace, filepath.FromSlash(f))); err == nil {
			out = append(out, f)
		}
	}
	return out
}

// asStr coerces a decoded JSON value to a string the way `str(x or "")` would for
// the lane/loop_ts identity fields (nil -> "").
func asStr(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// asStrSlice coerces a decoded JSON "tree" value to []string. A non-list or a
// list with non-string elements degrades element-wise (a non-string element is
// dropped), matching `list(live_lease.get("tree") or [])` followed by the prefix
// normalization which treats only strings.
func asStrSlice(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, e := range arr {
		if s, ok := e.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func copyMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
