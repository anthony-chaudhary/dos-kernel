// Package liveness ports the liveness verdict's PURE classifier to Go — the
// temporal distrust syscall (docs/82): *is the agent actually moving, or just
// spinning?* It is the docs/124 §3 Phase-1 / docs/385 TP2 port: the CLEANEST
// decider in the kernel, chosen to prove the parity harness on the safest
// surface before the arbiter's float hazard or the oracle's RE2 debt are in play.
//
// docs/124 §1.4 / A.4 audited it: liveness has ZERO of the three cross-language
// hazard classes — no lookbehind regex, no float-in-output (the reason
// interpolates the INTEGER commit/age counts only), no set-into-output. So unlike
// the arbiter (whose `reason` prose is excluded from the byte gate, docs/124 §2),
// liveness's reason is FULLY byte-matchable, and the parity corpus gates the whole
// verdict + reason. That strictly-stronger pin is exactly what makes it the right
// first TP2 port.
//
// The boundary is the one docs/82 / docs/100 fixed: classify() is PURE — no git,
// no journal, no clock. `now_ms` and every `*_ms` arrive as already-gathered
// integers on the Evidence (the caller's `dos liveness` evidence-gather reads the
// clock, counts commits, folds the journal at the I/O boundary — those reads stay
// Python, docs/385 §5 seam #4). Static, stdlib-only, no I/O: every fact injected.
//
// The Python twin is src/dos/liveness.py; this port is pinned to it byte-for-byte
// by parity/corpus.jsonl (the differential gate, parity_test.go). The reason
// strings below MUST match the Python f-strings character-for-character,
// including three non-ASCII codepoints carried as literal UTF-8 (this source is
// UTF-8): U+2014 em-dash, U+2264 "≤", U+2265 "≥". The corpus comparison decodes
// runes on both sides, so they never re-encode (docs/124 §5 rule 7 — pinned
// encoding, no escaping divergence); a mojibake'd byte would fail the gate loudly.
package liveness

import "fmt"

// Liveness is the typed verdict — three states, mutually exclusive. String-valued
// so it round-trips through a CLI stdout token without a lookup table (the twin of
// the Python `Liveness` str-enum).
type Liveness string

const (
	// Advancing — ground-truth state moved since the run started (or no liveness
	// problem to flag yet: the young-and-alive benign case).
	Advancing Liveness = "ADVANCING"
	// Spinning — the run is alive (heartbeat fresh) but state is NOT moving.
	Spinning Liveness = "SPINNING"
	// Stalled — no fresh heartbeat, no commits — dead/hung, not spinning.
	Stalled Liveness = "STALLED"
)

// Policy is the windows that separate ADVANCING/SPINNING/STALLED — policy, not
// mechanism (the twin of `LivenessPolicy`). Defaults: 30 min grace, 15 min spin.
type Policy struct {
	// GraceMs — minimum run-age an alive-but-idle run must reach before it can be
	// accused of SPINNING (the false-positive guard for a young run).
	GraceMs int
	// SpinMs — heartbeat-freshness bound proving the run is alive; older/absent ⇒
	// not demonstrably alive → STALLED.
	SpinMs int
}

// DefaultPolicy mirrors the Python `DEFAULT_POLICY`: 30-minute grace, 15-minute
// spin window.
var DefaultPolicy = Policy{GraceMs: 30 * 60 * 1000, SpinMs: 15 * 60 * 1000}

// Evidence is everything classify() needs, gathered by the CALLER before the call
// (the twin of `ProgressEvidence`). No git, no journal, no clock inside the
// verdict. The optional rungs are pointers: a nil pointer is the Python `None`.
type Evidence struct {
	RunStartedMs       int   // epoch-ms the run began (age framing only)
	NowMs              int   // wall-clock epoch-ms, injected at the boundary
	CommitsSinceStart  int   // authoritative forward delta; ≥1 ⇒ ADVANCING
	JournalEventsSince int   // lease-*work* events since start; ≥1 ⇒ ADVANCING
	LastHeartbeatAgeMs *int  // now − newest beat; nil = never beat (or no journal rung)
	TokensSpentSince   *int  // OPTIONAL waste signal; echoed, never an input to the ladder
	ProcessAlive       *bool // OPTIONAL unforgeable OS rung; demote-only (False ⇒ STALLED)
}

// Verdict is the single result classify() returns, with the evidence echoed back
// (the twin of `LivenessVerdict`). `Reason` is the one-line operator-facing
// summary — byte-matchable across engines (integer interpolation only).
type Verdict struct {
	Verdict  Liveness
	Reason   string
	Evidence Evidence
}

// Classify classifies one run's liveness from already-gathered evidence. PURE —
// no I/O. Reads the ladder top to bottom, exactly as src/dos/liveness.py:classify:
//
//  1. ADVANCING — any forward delta (≥1 commit OR ≥1 state-mutating journal
//     event), OR the young-and-alive case (a fresh heartbeat on a run younger
//     than GraceMs).
//  2. SPINNING  — no forward delta, a heartbeat fresher than SpinMs (alive), AND
//     run-age ≥ GraceMs (old enough to judge).
//  3. STALLED   — no forward delta and not alive (newest heartbeat older than
//     SpinMs, or none at all).
func Classify(ev Evidence, policy Policy) Verdict {
	// 1a. ADVANCING (forward delta) — the authoritative rung and the no-plan
	//     floor: a commit since start answers in a plain git repo with no journal.
	//     Checked FIRST so a run that moved is never mislabelled on a stale-
	//     heartbeat technicality.
	if ev.CommitsSinceStart >= 1 {
		return Verdict{
			Verdict: Advancing,
			Reason: fmt.Sprintf(
				"%d commit(s) since the run's start SHA — ground-truth state moved",
				ev.CommitsSinceStart),
			Evidence: ev,
		}
	}
	if ev.JournalEventsSince >= 1 {
		return Verdict{
			Verdict: Advancing,
			Reason: fmt.Sprintf(
				"%d state-mutating lane-journal event(s) since start — progress at the lease layer (0 commits)",
				ev.JournalEventsSince),
			Evidence: ev,
		}
	}

	// No forward delta. Heartbeat freshness decides alive-vs-dead; run-age decides
	// whether an alive run is old enough to be called spinning.
	age := ev.LastHeartbeatAgeMs
	alive := age != nil && *age <= policy.SpinMs

	if alive {
		// 1b. STALLED (proc-rung demote, docs/95) — the heartbeat says alive, but
		//     the OS confidently reports the run's pid is gone. The unforgeable OS
		//     rung overrides the forgeable beat. Checked BEFORE the young-and-alive
		//     guard (a confidently-dead process is dead regardless of run-age).
		//     Only a confident False demotes; True/None fall through.
		if ev.ProcessAlive != nil && !*ev.ProcessAlive {
			return Verdict{
				Verdict: Stalled,
				Reason: fmt.Sprintf(
					"heartbeat %d ms old says alive, but the OS reports the run's process is gone — STALLED (the unforgeable proc rung overrides a forgeable heartbeat; docs/95)",
					*age),
				Evidence: ev,
			}
		}

		// 1c. ADVANCING (young-and-alive guard) — an alive run younger than GraceMs
		//     has not earned a SPINNING accusation; we withhold and report "no
		//     liveness problem yet" (ADVANCING is benign — it does NOT claim a
		//     commit landed; the reason says so).
		runAge := ev.NowMs - ev.RunStartedMs
		if runAge < policy.GraceMs {
			return Verdict{
				Verdict: Advancing,
				Reason: fmt.Sprintf(
					"alive (heartbeat %d ms old) and only %d ms into the run (< grace %d ms) — too young to judge spinning; no liveness problem yet (0 commits so far)",
					*age, runAge, policy.GraceMs),
				Evidence: ev,
			}
		}

		// 2. SPINNING — alive, old enough to judge, and not moving. The OPTIONAL
		//    waste signal (tokens) only makes the reason legible; it never moved
		//    the verdict. Absent ⇒ the reason is byte-identical to before the slot
		//    was fed.
		reason := fmt.Sprintf(
			"alive (heartbeat %d ms old ≤ spin window %d ms) and %d ms into the run (≥ grace %d ms) but 0 commits and 0 lane events since start — spinning",
			*age, policy.SpinMs, runAge, policy.GraceMs)
		if ev.TokensSpentSince != nil {
			reason += fmt.Sprintf(" (burned %d tokens while not moving)", *ev.TokensSpentSince)
		}
		return Verdict{Verdict: Spinning, Reason: reason, Evidence: ev}
	}

	// 3. STALLED — no forward delta and not alive: the newest heartbeat is older
	//    than SpinMs, or there is none at all (nil). Dead or hung, not a spin.
	var reason string
	if age == nil {
		reason = "no heartbeat and 0 commits since start — run is dead or hung (never beat)"
	} else {
		reason = fmt.Sprintf(
			"heartbeat %d ms old (> spin window %d ms) and 0 commits since start — run is dead or hung, not spinning",
			*age, policy.SpinMs)
	}
	return Verdict{Verdict: Stalled, Reason: reason, Evidence: ev}
}
