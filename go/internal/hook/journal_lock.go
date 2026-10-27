package hook

import (
	"os"
	"path/filepath"
	"time"
)

// Lock the persistent sibling file, not the journal inode replaced by compaction.
// Closing the descriptor also releases the lock after an interrupted writer.
func lockJournal(journalPath string) (func(), error) {
	path := journalPath + ".append.lock"
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(30 * time.Second)
	for {
		ok, err := tryJournalFileLock(f)
		if err != nil {
			f.Close()
			return nil, err
		}
		if ok {
			return func() { f.Close() }, nil
		}
		if !time.Now().Before(deadline) {
			f.Close()
			return nil, os.ErrDeadlineExceeded
		}
		time.Sleep(25 * time.Millisecond)
	}
}
