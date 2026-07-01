package liveness

import (
	"strings"
	"testing"
)

func intp(v int) *int    { return &v }
func boolp(v bool) *bool { return &v }

// TestLadderRungs is the direct litmus (the twin of tests/test_liveness.py) — it pins
// each rung of the verdict ladder independent of the parity corpus, so a regression in
// the classifier fails here even if the corpus were stale.
func TestLadderRungs(t *testing.T) {
	cases := []struct {
		name      string
		ev        Evidence
		policy    Policy
		want      Liveness
		reasonHas string
	}{
		{
			name:      "commit rung is ADVANCING (no-plan floor)",
			ev:        Evidence{RunStartedMs: 1000, NowMs: 600000, CommitsSinceStart: 3},
			policy:    DefaultPolicy,
			want:      Advancing,
			reasonHas: "3 commit(s) since the run's start SHA",
		},
		{
			name:      "journal event rung is ADVANCING with 0 commits",
			ev:        Evidence{RunStartedMs: 1000, NowMs: 600000, JournalEventsSince: 4},
			policy:    DefaultPolicy,
			want:      Advancing,
			reasonHas: "progress at the lease layer (0 commits)",
		},
		{
			name:      "fresh beat, young run is benign ADVANCING",
			ev:        Evidence{RunStartedMs: 1000, NowMs: 600000, LastHeartbeatAgeMs: intp(8000)},
			policy:    DefaultPolicy,
			want:      Advancing,
			reasonHas: "too young to judge spinning",
		},
		{
			name:      "fresh beat, past grace, not moving is SPINNING",
			ev:        Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(8000)},
			policy:    DefaultPolicy,
			want:      Spinning,
			reasonHas: "since start", // "…0 commits and 0 lane events since start — spinning"
		},
		{
			name:      "SPINNING names the burned tokens when present",
			ev:        Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(8000), TokensSpentSince: intp(1200)},
			policy:    DefaultPolicy,
			want:      Spinning,
			reasonHas: "(burned 1200 tokens while not moving)",
		},
		{
			name:      "proc demote: fresh beat but OS says gone is STALLED",
			ev:        Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(8000), ProcessAlive: boolp(false)},
			policy:    DefaultPolicy,
			want:      Stalled,
			reasonHas: "the unforgeable proc rung overrides a forgeable heartbeat; docs/95",
		},
		{
			name:      "never beat is STALLED",
			ev:        Evidence{RunStartedMs: 0, NowMs: 2_000_000},
			policy:    DefaultPolicy,
			want:      Stalled,
			reasonHas: "run is dead or hung (never beat)",
		},
		{
			name:      "stale beat is STALLED, not spinning",
			ev:        Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(1_000_000)},
			policy:    DefaultPolicy,
			want:      Stalled,
			reasonHas: "run is dead or hung, not spinning",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Classify(tc.ev, tc.policy)
			if got.Verdict != tc.want {
				t.Fatalf("verdict: want %q got %q (reason %q)", tc.want, got.Verdict, got.Reason)
			}
			if !strings.Contains(got.Reason, tc.reasonHas) {
				t.Fatalf("reason %q does not contain %q", got.Reason, tc.reasonHas)
			}
			if got.Evidence != tc.ev {
				t.Fatalf("evidence not echoed unchanged")
			}
		})
	}
}

// TestProcDemoteNeedsAlive pins that the unforgeable proc rung is DEMOTE-ONLY and gated
// on alive: a confident process_alive=False on a STALE heartbeat does not change the
// not-alive bottom verdict, and process_alive=True never promotes a dead run.
func TestProcDemoteNeedsAlive(t *testing.T) {
	// Stale beat + proc False: still STALLED via the bottom branch (demote requires alive).
	stale := Classify(
		Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(1_000_000), ProcessAlive: boolp(false)},
		DefaultPolicy)
	if stale.Verdict != Stalled || !strings.Contains(stale.Reason, "not spinning") {
		t.Fatalf("stale+procFalse: want STALLED bottom branch, got %q %q", stale.Verdict, stale.Reason)
	}
	// proc True does not rescue a never-beat run.
	dead := Classify(
		Evidence{RunStartedMs: 0, NowMs: 2_000_000, ProcessAlive: boolp(true)},
		DefaultPolicy)
	if dead.Verdict != Stalled {
		t.Fatalf("procTrue must not promote a dead run, got %q", dead.Verdict)
	}
}

// TestBoundaries pins the two window boundaries the corpus also carries, in-code.
func TestBoundaries(t *testing.T) {
	// age == spin_ms is alive (≤); with run_age ≥ grace → SPINNING.
	atSpin := Classify(
		Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(DefaultPolicy.SpinMs)},
		DefaultPolicy)
	if atSpin.Verdict != Spinning {
		t.Fatalf("age==spin should be alive→SPINNING, got %q", atSpin.Verdict)
	}
	// age == spin_ms + 1 is not alive → STALLED.
	overSpin := Classify(
		Evidence{RunStartedMs: 0, NowMs: 2_000_000, LastHeartbeatAgeMs: intp(DefaultPolicy.SpinMs + 1)},
		DefaultPolicy)
	if overSpin.Verdict != Stalled {
		t.Fatalf("age==spin+1 should be STALLED, got %q", overSpin.Verdict)
	}
	// run_age == grace_ms is old enough (not < grace) → SPINNING; grace-1 is young.
	atGrace := Classify(
		Evidence{RunStartedMs: 0, NowMs: DefaultPolicy.GraceMs, LastHeartbeatAgeMs: intp(8000)},
		DefaultPolicy)
	if atGrace.Verdict != Spinning {
		t.Fatalf("run_age==grace should be SPINNING, got %q", atGrace.Verdict)
	}
	youngByOne := Classify(
		Evidence{RunStartedMs: 0, NowMs: DefaultPolicy.GraceMs - 1, LastHeartbeatAgeMs: intp(8000)},
		DefaultPolicy)
	if youngByOne.Verdict != Advancing {
		t.Fatalf("run_age==grace-1 should be young ADVANCING, got %q", youngByOne.Verdict)
	}
}
