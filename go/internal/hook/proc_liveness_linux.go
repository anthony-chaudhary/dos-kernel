//go:build linux

// proc_liveness_linux.go — the same-host OS process-liveness rung for the Go
// fast-path, the Linux half. Port of the Python `dos.proc_delta` probe (docs/95)
// down to the in-line hook that runs on every tool call.
//
// Why the hook needs this. `leaseExpired` reclaims a crashed worker's un-RELEASEd
// lease by TTL/heartbeat staleness alone (signal a). That means the in-line
// enforcer holds a phantom lock on the dead worker's lane for the FULL
// `ttl_minutes + grace` window (default 15+ min), refusing every other worker's
// write to it — while the Python `live_leases` would reclaim it the instant the
// OS confirms the holder PID is gone. This file is the missing signal (b): a
// SAME-HOST `/proc/<pid>` read that confidently detects a dead local PID, with
// field-22 starttime corroboration so a RECYCLED pid (the OS reassigned the
// number to a stranger) is not mistaken for the original holder.
//
// The disciplines (mirror proc_delta exactly):
//   - SAME-HOST ONLY. The caller (`leaseExpired`) probes only when the lease's
//     recorded host_id is local — a pid is meaningless on another box. This file
//     never sees host_id; it just reads THIS machine's /proc.
//   - NEVER fabricate dead. Every read failure (no /proc, race-deleted pid,
//     permission, malformed line) degrades to known=false ("cannot tell"), never
//     to a confident "gone" — so the caller keeps the lease (the
//     genuine-collision-preserving direction).
//   - starttime is identity, not liveness. A live pid whose starttime != the
//     recorded baseline is a DIFFERENT process; the original holder is therefore
//     gone, which the caller treats as confidently-dead (reuse → original dead).

package hook

import (
	"os"
	"strconv"
	"strings"
)

// procAlive reports whether `pid` is a live process on THIS host.
//
//	alive=true,  known=true  — /proc/<pid> exists; the process is up.
//	alive=false, known=true  — /proc/<pid> is confidently absent; the pid is gone.
//	known=false              — could not tell (pid<=0, or an unexpected stat error);
//	                           the caller must NOT treat this as dead.
func procAlive(pid int) (alive bool, known bool) {
	if pid <= 0 {
		return false, false
	}
	if _, err := os.Stat("/proc/" + strconv.Itoa(pid)); err == nil {
		return true, true
	} else if os.IsNotExist(err) {
		return false, true // confidently gone — the only value that may demote
	}
	return false, false // permission / unexpected — cannot tell, keep the lease
}

// procStarttime reads `/proc/<pid>/stat` field 22 (process start time, in clock
// ticks since boot) — the Linux process-identity byte fixed at creation and not
// inherited by a recycled pid. Returns ok=false on any failure (the rung simply
// does not corroborate identity, never blocks).
//
// Parse note (matches _starttime_linux in proc_delta): field 2 (comm) is wrapped
// in parens and MAY contain spaces or close-parens, so the only safe split is on
// the LAST ')'. Field 22 is then index 19 of the space-split remainder
// (the fields after comm are 3..N, and starttime is the 22nd overall).
func procStarttime(pid int) (int64, bool) {
	if pid <= 0 {
		return 0, false
	}
	data, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return 0, false
	}
	s := string(data)
	rparen := strings.LastIndexByte(s, ')')
	if rparen < 0 {
		return 0, false
	}
	rest := strings.Fields(s[rparen+1:])
	// rest[0] = state (field 3); starttime is field 22 → rest index 22-3 = 19.
	if len(rest) < 20 {
		return 0, false
	}
	v, err := strconv.ParseInt(rest[19], 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// procConfidentlyGone folds the two readers into the one verdict `leaseExpired`
// needs: is the original holder process PROVABLY gone on this host?
//
//	gone=true  — /proc says the pid is absent, OR the pid exists but its starttime
//	             disagrees with `recordedStart` (a recycled number → the original
//	             holder is gone; PID-reuse defense).
//	gone=false — the pid is up and (if a baseline was recorded) matches, OR we
//	             could not tell. NOT confidently gone → the caller keeps the lease.
//
// recordedStart<=0 means "no baseline recorded" — then a live pid is taken at
// face value (existence-only, the no-baseline degrade), exactly as proc_delta
// does when recorded_starttime is None.
func procConfidentlyGone(pid int, recordedStart int64) bool {
	alive, known := procAlive(pid)
	if !known {
		return false // cannot tell → not confidently gone
	}
	if !alive {
		return true // /proc says absent → gone
	}
	// The pid is up. Corroborate identity only when a baseline exists.
	if recordedStart > 0 {
		if st, ok := procStarttime(pid); ok && st != recordedStart {
			return true // a different process recycled the number → original gone
		}
	}
	return false
}
