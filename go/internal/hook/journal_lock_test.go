package hook

import (
	"bufio"
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestNativeAppendWaitsForPythonCompaction(t *testing.T) {
	python, err := exec.LookPath("python")
	if err != nil {
		t.Skip("Python required for cross-runtime journal lock witness")
	}
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	wal := filepath.Join(t.TempDir(), "lane-journal.jsonl")
	program := `import os,sys
from dos import config,lane_journal,lane_lease
wal=sys.argv[1]
os.environ['DISPATCH_LANE_JOURNAL_PATH']=wal
os.environ['DISPATCH_LANE_LEASE_LOCK_PATH']=wal+'.lease.lock'
lane_journal.append({'op':'ACQUIRE','lane':'keep','loop_ts':'old','tree':['keep/**']})
original=lane_journal.compact
def pause(entries):
    print('FOLDING',flush=True)
    sys.stdin.readline()
    return original(entries)
lane_journal.compact=pause
lane_lease.compact_journal(config.default_config(os.path.dirname(wal)))
`
	cmd := exec.Command(python, "-c", program, wal)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "src"))
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { stdin.Close(); cmd.Process.Kill(); cmd.Wait() }()
	scanner := bufio.NewScanner(stdout)
	if !scanner.Scan() || scanner.Text() != "FOLDING" {
		t.Fatalf("Python compactor did not reach fold: %s", stderr.String())
	}
	done := make(chan struct{})
	go func() {
		AppendEnforceRecord(wal, &Event{}, Decision{DecisionTag: "warn", Rung: "admission"})
		close(done)
	}()
	select {
	case <-done:
		t.Fatal("native append bypassed Python compaction lock")
	case <-time.After(100 * time.Millisecond):
	}
	stdin.Close()
	if err := cmd.Wait(); err != nil {
		t.Fatalf("Python compact: %v: %s", err, stderr.String())
	}
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("native append did not resume after compaction")
	}
	entries := readJournal(wal)
	if len(entries) != 2 || asStr(entries[0]["op"]) != "CHECKPOINT" || asStr(entries[1]["op"]) != "ENFORCE" {
		t.Fatalf("lost checkpoint or native append: %#v", entries)
	}
	if len(replayJournalFull(entries)) != 1 || asInt(entries[1]["seq"]) < 2 {
		t.Fatal("compaction lost live lease or reused sequence")
	}
}
