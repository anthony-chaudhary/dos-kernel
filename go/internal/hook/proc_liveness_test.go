package hook

// The same-host proc-liveness rung readers (docs/95) — the Go port of the Python
// proc_delta probe. The live signal is a Linux /proc property, so the
// Linux-specific assertions skip on other platforms (the rung is deliberately
// absent there); the never-fabricate-dead and no-raise properties hold everywhere.

import (
	"os"
	"runtime"
	"testing"
)

func TestProcAlive_SelfIsAliveOnLinux(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("procAlive engages only on linux")
	}
	alive, known := procAlive(os.Getpid())
	if !known || !alive {
		t.Fatalf("this process must read alive on linux; got alive=%v known=%v", alive, known)
	}
}

func TestProcAlive_ImpossiblePidIsConfidentlyGoneOnLinux(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("procAlive engages only on linux")
	}
	alive, known := procAlive(2000000000)
	if !known || alive {
		t.Fatalf("an impossible pid must read confidently gone on linux; got alive=%v known=%v", alive, known)
	}
}

func TestProcAlive_NonPositivePidIsUnknown(t *testing.T) {
	// pid<=0 (the TTL-only sentinel) is never probeable — known=false everywhere,
	// so it can never be "confidently gone" (which would wrongly demote a lease).
	for _, pid := range []int{0, -1, -42} {
		if _, known := procAlive(pid); known {
			t.Fatalf("pid %d must be unknown (not probeable)", pid)
		}
	}
}

func TestProcStarttime_SelfIsPositiveOnLinux(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("procStarttime reads /proc only on linux")
	}
	st, ok := procStarttime(os.Getpid())
	if !ok || st <= 0 {
		t.Fatalf("self starttime must be a positive int on linux; got %d ok=%v", st, ok)
	}
}

func TestProcConfidentlyGone_NeverFabricatesDeadForLiveSelf(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("rung engages only on linux")
	}
	// Self, no baseline → alive → NOT gone.
	if procConfidentlyGone(os.Getpid(), 0) {
		t.Fatal("a live self-pid with no baseline must not read as gone")
	}
	// Self WITH its true baseline → identity matches → NOT gone.
	st, _ := procStarttime(os.Getpid())
	if procConfidentlyGone(os.Getpid(), st) {
		t.Fatal("a live self-pid matching its recorded starttime must not read as gone")
	}
}

func TestProcConfidentlyGone_ReuseMismatchIsGoneOnLinux(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("rung engages only on linux")
	}
	// Self is alive, but the recorded baseline is WRONG → a different process
	// recycled the number → the original holder is confidently gone (PID-reuse).
	st, _ := procStarttime(os.Getpid())
	wrong := st + 1
	if !procConfidentlyGone(os.Getpid(), wrong) {
		t.Fatal("a live pid whose starttime != the recorded baseline must read as gone (reuse)")
	}
}

func TestProcConfidentlyGone_UnknownNeverGone(t *testing.T) {
	// A non-probeable pid is never confidently gone (fail-safe: keep the lease).
	if procConfidentlyGone(0, 0) || procConfidentlyGone(-1, 12345) {
		t.Fatal("a non-probeable pid must never read as confidently gone")
	}
}
