//go:build windows

package hook

import (
	"os"
	"syscall"
	"unsafe"
)

var journalLockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")

func tryJournalFileLock(f *os.File) (bool, error) {
	var overlapped syscall.Overlapped
	ok, _, err := journalLockFileEx.Call(f.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(&overlapped)))
	if ok != 0 {
		return true, nil
	}
	if err == syscall.Errno(33) { // ERROR_LOCK_VIOLATION
		return false, nil
	}
	return false, err
}
