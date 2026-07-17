package liveness

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// parityCase mirrors one line of parity/corpus.jsonl — the hermetic differential case
// the Python oracle (gen_corpus.py) produced by driving the REAL dos.liveness.classify.
// The Go test rebuilds the SAME evidence from expect.Evidence and asserts the native
// classifier reproduces the recorded verdict + reason, byte-for-byte.
type parityCase struct {
	Name    string       `json:"name"`
	GraceMs int          `json:"grace_ms"`
	SpinMs  int          `json:"spin_ms"`
	Expect  corpusExpect `json:"expect"`
}

// corpusExpect is the Python LivenessVerdict.to_dict() shape.
type corpusExpect struct {
	Verdict  string         `json:"verdict"`
	Reason   string         `json:"reason"`
	Evidence corpusEvidence `json:"evidence"`
}

// corpusEvidence is the echoed ProgressEvidence — the Go test's INPUT (it reads the
// evidence back out and re-runs the classifier over it). Nullable rungs are pointers,
// so a JSON null decodes to nil (the Python None).
type corpusEvidence struct {
	RunStartedMs       int   `json:"run_started_ms"`
	NowMs              int   `json:"now_ms"`
	CommitsSinceStart  int   `json:"commits_since_start"`
	JournalEventsSince int   `json:"journal_events_since"`
	LastHeartbeatAgeMs *int  `json:"last_heartbeat_age_ms"`
	TokensSpentSince   *int  `json:"tokens_spent_since"`
	ProcessAlive       *bool `json:"process_alive"`
}

func (c corpusEvidence) toEvidence() Evidence {
	return Evidence{
		RunStartedMs:       c.RunStartedMs,
		NowMs:              c.NowMs,
		CommitsSinceStart:  c.CommitsSinceStart,
		JournalEventsSince: c.JournalEventsSince,
		LastHeartbeatAgeMs: c.LastHeartbeatAgeMs,
		TokensSpentSince:   c.TokensSpentSince,
		ProcessAlive:       c.ProcessAlive,
	}
}

func loadCorpus(t *testing.T) []parityCase {
	t.Helper()
	path := filepath.Join("parity", "corpus.jsonl")
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open corpus %s: %v (run `python go/internal/liveness/parity/gen_corpus.py > %s`)", path, err, path)
	}
	defer f.Close()
	var cases []parityCase
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var c parityCase
		if err := json.Unmarshal(line, &c); err != nil {
			t.Fatalf("corpus line unmarshal: %v", err)
		}
		cases = append(cases, c)
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("corpus scan: %v", err)
	}
	if len(cases) == 0 {
		t.Fatal("corpus is empty")
	}
	return cases
}

// TestParityCorpus is the differential parity gate (docs/124 §3 Phase 1) and — under
// the docs/385 PORT→SOAK→FLIP ratchet — the pin that keeps the Go liveness classifier
// byte-exact with its Python twin. For every case it rebuilds the recorded evidence,
// runs Classify with the recorded policy, and asserts the verdict + reason match the
// Python oracle's output. (Liveness's reason is fully byte-matchable — integer
// interpolation only, docs/124 §1.4 — so the whole verdict + reason is gated, not just
// the decision field.)
func TestParityCorpus(t *testing.T) {
	for _, c := range loadCorpus(t) {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			got := Classify(c.Expect.Evidence.toEvidence(), Policy{GraceMs: c.GraceMs, SpinMs: c.SpinMs})
			if string(got.Verdict) != c.Expect.Verdict {
				t.Fatalf("VERDICT DRIFT %q: py=%q go=%q", c.Name, c.Expect.Verdict, got.Verdict)
			}
			if got.Reason != c.Expect.Reason {
				t.Fatalf("REASON DRIFT %q:\n  py: %q\n  go: %q", c.Name, c.Expect.Reason, got.Reason)
			}
		})
	}
}
