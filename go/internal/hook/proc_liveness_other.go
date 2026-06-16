//go:build !linux

// proc_liveness_other.go — the non-Linux half of the proc-liveness rung.
//
// On any non-Linux platform the hook has no portable, dependency-free way to
// confidently probe a same-host pid the way `/proc/<pid>/stat` allows on Linux,
// so the rung simply DOES NOT ENGAGE: every reader degrades to "cannot tell"
// (known=false / ok=false / gone=false). This is the Python "unsupported
// platform → None" behavior — the verdict still answers from the TTL/heartbeat
// backstop (signal a), byte-identical to before this rung existed.
//
// The Windows dev box and any non-Linux CI compile and pass against these stubs;
// the live signal is a Linux-host property, which is exactly where the DOS fleet
// runs its workers.

package hook

// procAlive — cannot tell on a non-Linux host. See the Linux build file.
func procAlive(pid int) (alive bool, known bool) {
	return false, false
}

// procStarttime — no portable same-host identity byte off Linux.
func procStarttime(pid int) (int64, bool) {
	return 0, false
}

// procConfidentlyGone — never confidently gone off Linux (the rung is absent, so
// reclaim falls back to the TTL/heartbeat backstop alone).
func procConfidentlyGone(pid int, recordedStart int64) bool {
	return false
}
