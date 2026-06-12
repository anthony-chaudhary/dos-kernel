package hook

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEnforceRecordShape pins the OP_ENFORCE WAL record the native binary writes
// for a deny — the Go port of cli._journal_pretool_outcome + enforce_entry. The
// cross-engine byte-parity vs the Python writer is verified live (see the GHF1
// notes); this test pins the STRUCTURE so a future edit can't silently change the
// record shape (the field set, the sorted-key WAL line, the BLOCK/withheld flags).
func TestEnforceRecordShape(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "lane-journal.jsonl")
	t.Setenv("DISPATCH_LANE_JOURNAL_PATH", wal)
	t.Setenv("DISPATCH_LOOP_TS", "")
	t.Setenv("DISPATCH_HOST_ID", "")
	t.Setenv("CID_RUN_ID", "")

	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	e.SessionID = "sess-A"
	d := Decide(e, Inputs{RuntimeFiles: dispatchRuntimeFiles})
	if d.DecisionTag != "deny" {
		t.Fatalf("expected deny, got %q", d.DecisionTag)
	}
	AppendEnforceRecord(wal, e, d)

	raw, err := os.ReadFile(wal)
	if err != nil {
		t.Fatalf("WAL not written: %v", err)
	}
	line := strings.TrimSpace(string(raw))

	// The WAL line is ensure_ascii=False — the em-dash is RAW UTF-8, NOT the
	// escaped 6-char form the stdout dialect (ensure_ascii=True) uses.
	escapedEmDash := "\\u2014"      // a literal backslash + u2014 (the escaped form)
	rawEmDash := string('—')   // the actual em-dash rune (raw UTF-8 bytes)
	if strings.Contains(line, escapedEmDash) {
		t.Fatalf("WAL line must be ensure_ascii=False (raw em-dash), got the escaped form: %s", line)
	}
	if !strings.Contains(line, rawEmDash) {
		t.Fatalf("WAL line should carry the raw em-dash byte sequence: %s", line)
	}
	// Keys must be sorted (sort_keys=True) — dispatch_call is the first key.
	if !strings.HasPrefix(line, `{"dispatch_call": false, `) {
		t.Fatalf("WAL line not sorted-key / wrong leading field: %s", line)
	}
	for _, want := range []string{
		`"op": "ENFORCE"`,
		`"lane": "Edit"`,
		`"holder": "sess-A"`,
		`"tool": "Edit"`,
		`"intervention": "BLOCK"`,
		`"withheld": true`,
		`"dispatch_call": false`,
		`"reason_class": "SELF_MODIFY"`,
		`"rung": "admission"`,
		`"seq": 1`,
	} {
		if !strings.Contains(line, want) {
			t.Fatalf("WAL line missing %q:\n%s", want, line)
		}
	}
	// The typed token must be LIFTED to the top level (issue #14), not only nested
	// in the proposal body: the decisions queue's enforce-storm fold and the
	// cause-resolution readers key on the top-level `reason_class`. Two
	// occurrences = nested body + the lift (Python `enforce_entry` parity).
	if n := strings.Count(line, `"reason_class": "SELF_MODIFY"`); n < 2 {
		t.Fatalf("reason_class not lifted to the top level (found %d occurrence(s)):\n%s", n, line)
	}
}

// TestPassthroughWritesNoRecord pins that a passthrough writes NOTHING (a journal
// record is only for a non-passthrough outcome — the Python contract).
func TestPassthroughWritesNoRecord(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "lane-journal.jsonl")
	e := eventFor("Read", "/work/workspace", map[string]any{"file_path": "x.py"})
	d := Decide(e, Inputs{RuntimeFiles: dispatchRuntimeFiles})
	if d.DecisionTag != "passthrough" {
		t.Fatalf("expected passthrough, got %q", d.DecisionTag)
	}
	if d.DecisionTag != "passthrough" {
		AppendEnforceRecord(wal, e, d)
	}
	if _, err := os.Stat(wal); err == nil {
		t.Fatalf("passthrough must not write a WAL record, but %s exists", wal)
	}
}

// TestEnforceAppendRepairsTornTail pins the torn-tail repair (issue #62, parity
// with Python `lane_journal.append`): a WAL whose final line is a terminator-less
// fragment (a writer crashed mid-append) must NOT swallow the next record. The
// repair writes the new record on a fresh line, so the fragment becomes its own
// complete corrupt line (a `_CORRUPT` sentinel to readJournal) and the new
// ENFORCE stays readable — never one glued unparseable line holding both.
func TestEnforceAppendRepairsTornTail(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "lane-journal.jsonl")
	// A crash mid-append: a partial record, no trailing newline.
	if err := os.WriteFile(wal, []byte(`{"op": "ACQUIRE", "lane": "to`), 0o644); err != nil {
		t.Fatal(err)
	}
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dispatchRuntimeFiles})
	if d.DecisionTag != "deny" {
		t.Fatalf("expected deny, got %q", d.DecisionTag)
	}
	AppendEnforceRecord(wal, e, d)

	raw, err := os.ReadFile(wal)
	if err != nil {
		t.Fatalf("WAL not written: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected the fragment + the record on separate lines, got %d line(s):\n%s",
			len(lines), string(raw))
	}
	entries := readJournal(wal)
	if len(entries) != 2 {
		t.Fatalf("expected a _CORRUPT sentinel + the ENFORCE record, got %d entries", len(entries))
	}
	if op := asStr(entries[0]["op"]); op != "_CORRUPT" {
		t.Fatalf("fragment should surface as _CORRUPT, got %q", op)
	}
	if op := asStr(entries[1]["op"]); op != "ENFORCE" {
		t.Fatalf("the appended record must stay readable, got %q", op)
	}
}

// TestEnforceSeqIncrements pins that a second record gets seq=2 (next_seq reads the
// existing WAL) — the append-order invariant.
func TestEnforceSeqIncrements(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "lane-journal.jsonl")
	t.Setenv("DISPATCH_LANE_JOURNAL_PATH", wal)
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dispatchRuntimeFiles})
	AppendEnforceRecord(wal, e, d)
	AppendEnforceRecord(wal, e, d)
	raw, _ := os.ReadFile(wal)
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 WAL lines, got %d", len(lines))
	}
	if !strings.Contains(lines[0], `"seq": 1`) || !strings.Contains(lines[1], `"seq": 2`) {
		t.Fatalf("seq did not increment 1->2:\n%s\n%s", lines[0], lines[1])
	}
}
