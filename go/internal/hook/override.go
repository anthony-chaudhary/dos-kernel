package hook

// docs/296 — the operator-armed SELF_MODIFY override, ported to the Go fast-path.
//
// The Python PreToolUse path (`dos.override_facts` + `dos.pretool_sensor`) already
// reads the operator's hand-typed arm file and, while the window is open, converts a
// SELF_MODIFY deny into an ALLOW-with-note. The native binary served the SELF_MODIFY
// deny but did NOT consult the arm file — so an armed window was silently ignored on
// any host running the fast path. This file closes that parity gap: it is the byte-
// faithful Go twin of `override_facts.read_override` + `dispose` (+ the arm-path write
// perimeter `pretool_sensor` runs before admission).
//
// Two halves, the same house split as the Python module:
//   - ReadOverride — boundary I/O: parse the arm file under the workspace root into
//     OverrideFacts, or nil. FAIL-CLOSED on every branch (a missing/unreadable/UTF-16/
//     malformed/incomplete file can only fail to admit, never fail to deny).
//   - dispose — PURE: facts + the refused call's reason-class/targets + now → the
//     override note, or "" (no disposition; the deny stands). Only a SELF_MODIFY
//     refusal is ever converted.
//
// Stdlib-only, no dependency (the module is dependency-free by design); the 3 keys are
// hand-parsed with the same minimal TOML helpers `workspace.go` / `verify_convention.go`
// already use. Parity with the Python bytes is pinned by the hermetic corpus.

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// utf8BOM is the 3-byte UTF-8 byte-order mark a `utf-8-sig` arm file may carry
// (PowerShell `Set-Content -Encoding utf8` writes it). Trimmed before parsing so a
// BOM-prefixed but otherwise valid arm file still reads, mirroring Python's
// `read_text(encoding="utf-8-sig")`.
var utf8BOM = []byte{0xEF, 0xBB, 0xBF}

// armRelPath is the arm file's workspace-relative home — byte-identical to
// `override_facts.ARM_RELPATH`. Inside `.dos/` (the gitignored state dir) so an armed
// window can never be accidentally committed.
const armRelPath = ".dos/override/self-modify.toml"

// overridableReasonClass is the ONLY reason-class dispose may convert (the arm file is
// a self-modify instrument and must never wave through a collision/budget deny). Port
// of `override_facts._OVERRIDABLE_REASON_CLASS`.
const overridableReasonClass = selfModifyReason

// OverrideFacts is the operator's armed window, parsed — frozen evidence, not state.
// Port of `override_facts.OverrideFacts`.
type OverrideFacts struct {
	Until  time.Time // hard deadline; always tz-aware after parsing
	Reason string    // the operator's why — lands in the audit note
	Scope  []string  // normalized relative paths; empty = the whole T1 set
	Source string    // where it was read from (display only)
}

// normOverridePath is one normalized spelling for a workspace-relative path: posix
// slashes, no leading "./", casefolded — byte-faithful to `override_facts._norm`
// (the same fold the lane trees use, so a case-insensitive FS compare is stable).
func normOverridePath(p string) string {
	text := strings.TrimSpace(strings.ReplaceAll(p, "\\", "/"))
	for strings.HasPrefix(text, "./") {
		text = text[2:]
	}
	return strings.ToLower(strings.Trim(text, "/"))
}

// touchesArmPath reports whether any target IS (or is inside) the arm file's directory
// — the perimeter test the hook runs BEFORE admission (port of
// `override_facts.touches_arm_path`). An agent write anywhere under `.dos/override/` is
// refused outright and that refusal is never converted by dispose (a window must not
// extend itself).
func touchesArmPath(targets []string) bool {
	armDir := normOverridePath(armRelPath[:strings.LastIndexByte(armRelPath, '/')])
	armFile := normOverridePath(armRelPath)
	for _, t := range targets {
		n := normOverridePath(t)
		if n == "" {
			continue
		}
		if n == armFile || strings.HasSuffix(n, "/"+armFile) {
			return true
		}
		if n == armDir || strings.HasSuffix(n, "/"+armDir) || strings.Contains("/"+n+"/", "/"+armDir+"/") {
			return true
		}
	}
	return false
}

// armPath is the arm file's absolute path under a workspace root.
func armPath(workspace string) string {
	return filepath.Join(workspace, filepath.FromSlash(armRelPath))
}

// ReadOverride parses the arm file under `workspace` into OverrideFacts, or nil
// (FAIL-CLOSED). Boundary I/O. nil on: missing/unreadable file, a non-UTF-8 file (e.g.
// a PowerShell `>` redirect's UTF-16+BOM — the documented #147 fail-closed case), TOML
// that does not parse, a missing/blank `reason`, a missing/invalid `until`, or a
// `scope` that is not a list of strings. A malformed override can only fail to admit.
// Byte-faithful port of `override_facts.read_override`.
func ReadOverride(workspace string) *OverrideFacts {
	if workspace == "" {
		return nil
	}
	raw, err := os.ReadFile(armPath(workspace))
	if err != nil {
		return nil
	}
	// Strip a UTF-8 BOM if present (a `utf-8-sig` file is valid; the Python reader
	// accepts it). A UTF-16 file (0xff 0xfe …) is NOT valid UTF-8 and the line scan
	// below finds none of the keys → nil (fail-closed), mirroring the Python
	// UnicodeDecodeError branch.
	if !isLikelyUTF8(raw) {
		return nil
	}
	text := string(bytes.TrimPrefix(raw, utf8BOM))

	var untilRaw, reasonRaw, scopeRaw string
	var sawScope bool
	for _, line := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		l := strings.TrimSpace(line)
		if l == "" || strings.HasPrefix(l, "#") || strings.HasPrefix(l, "[") {
			continue
		}
		eq := strings.IndexByte(l, '=')
		if eq < 0 {
			continue
		}
		key := strings.TrimSpace(l[:eq])
		val := strings.TrimSpace(l[eq+1:])
		switch key {
		case "until":
			untilRaw = val
		case "reason":
			reasonRaw = val
		case "scope":
			scopeRaw = val
			sawScope = true
		}
	}

	until := coerceUntil(untilRaw)
	reason := unquoteOrBare(reasonRaw)
	if until.IsZero() || strings.TrimSpace(reason) == "" {
		return nil
	}
	var scope []string
	if sawScope {
		// A `scope` present but not a parseable string list is a malformed override
		// → nil (the Python reader returns None when scope is not a list of strings).
		list, ok := parseTomlStringListStrict(scopeRaw)
		if !ok {
			return nil
		}
		for _, s := range list {
			if strings.TrimSpace(s) != "" {
				scope = append(scope, normOverridePath(s))
			}
		}
	}
	return &OverrideFacts{
		Until:  until,
		Reason: strings.TrimSpace(reason),
		Scope:  scope,
		Source: armPath(workspace),
	}
}

// isLikelyUTF8 rejects an obvious UTF-16/binary arm file (a NUL byte, or a UTF-16 BOM)
// — the cheap structural check that mirrors Python's UnicodeDecodeError fail-closed.
func isLikelyUTF8(b []byte) bool {
	if len(b) >= 2 && ((b[0] == 0xff && b[1] == 0xfe) || (b[0] == 0xfe && b[1] == 0xff)) {
		return false // UTF-16 LE/BE BOM
	}
	for _, c := range b {
		if c == 0x00 {
			return false
		}
	}
	return true
}

// coerceUntil parses the deadline, tz-aware, or the zero time (fail-closed). Accepts a
// bare TOML offset-datetime or a quoted ISO string; a NAIVE value is read as the
// operator's LOCAL wall clock and made aware — the friendly reading of a hand-typed
// time, mirroring `override_facts._coerce_until`.
func coerceUntil(val string) time.Time {
	s := strings.TrimSpace(val)
	if s == "" {
		return time.Time{}
	}
	// A quoted string value: strip the quotes (TOML basic/literal string).
	if len(s) >= 2 && ((s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'')) {
		s = s[1 : len(s)-1]
	}
	s = strings.TrimSpace(stripInlineComment(s))
	// Offset-aware first (the schema's bare form: 2026-…+00:00 / …Z).
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05Z07:00"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t
		}
	}
	// Naive (no offset): read as local wall clock, made aware.
	for _, layout := range []string{"2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02T15:04", "2006-01-02"} {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return t
		}
	}
	return time.Time{}
}

// unquoteOrBare strips matching TOML quotes from a value (after dropping an inline
// comment outside the quotes); a bare value is returned trimmed.
func unquoteOrBare(val string) string {
	v := strings.TrimSpace(stripInlineComment(strings.TrimSpace(val)))
	if len(v) >= 2 && ((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
		return v[1 : len(v)-1]
	}
	return v
}

// parseTomlStringListStrict parses a `["a", "b"]` inline array. The bool is false when
// the value is not a bracketed list at all (so the caller can fail-close, matching the
// Python "scope is not a list → None"); an empty `[]` is (nil, true).
func parseTomlStringListStrict(val string) ([]string, bool) {
	v := strings.TrimSpace(val)
	if i := strings.LastIndexByte(v, ']'); i >= 0 {
		v = v[:i+1]
	}
	if !strings.HasPrefix(v, "[") || !strings.HasSuffix(v, "]") {
		return nil, false
	}
	inner := strings.TrimSpace(v[1 : len(v)-1])
	if inner == "" {
		return nil, true
	}
	var out []string
	for _, part := range strings.Split(inner, ",") {
		p := strings.Trim(strings.TrimSpace(part), `"'`)
		if p != "" {
			out = append(out, p)
		}
	}
	return out, true
}

// inOverrideScope reports whether a normalized target equals a scope entry or sits
// under one read as a directory — port of `override_facts._in_scope`.
func inOverrideScope(target string, scope []string) bool {
	n := normOverridePath(target)
	for _, s := range scope {
		if n == s || strings.HasPrefix(n, s+"/") {
			return true
		}
	}
	return false
}

// dispose is the PURE disposition: the override note, or "" (the deny stands). Converts
// iff ALL of: facts present; the refusal is SELF_MODIFY (never a collision/budget
// deny); now is inside the window; and — when the window is scoped — every target is
// provably inside the scope (a scoped window with no parseable targets stays denied).
// The returned note is byte-identical to `override_facts.dispose` so the emitted
// allow-with-note matches the Python path over the parity corpus.
func dispose(reasonClass string, targets []string, f *OverrideFacts, now time.Time) string {
	if f == nil {
		return ""
	}
	if reasonClass != overridableReasonClass {
		return ""
	}
	// now must be inside the window: now <= until (mirrors Python `here > until → None`).
	if now.After(f.Until) {
		return ""
	}
	if len(f.Scope) > 0 {
		if len(targets) == 0 {
			return ""
		}
		for _, t := range targets {
			if !inOverrideScope(t, f.Scope) {
				return ""
			}
		}
	}
	return "operator override armed until " + isoZ(f.Until) + " — admitting " +
		"supervised kernel edit: " + f.Reason + ". The SELF_MODIFY verdict itself " +
		"is unchanged; this is the operator's window (docs/296). " +
		"Disarm any time: dos override disarm"
}

// isoZ renders a deadline EXACTLY as Python's `datetime.isoformat()` does, because
// `dispose` interpolates `facts.until.isoformat()` verbatim and the note must be byte-
// identical across engines. Two divergences from Go's RFC3339 to correct:
//   - UTC: Python prints the numeric offset "+00:00", Go's RFC3339 prints "Z".
//   - fractional seconds: Python prints none when microsecond==0, else exactly 6
//     digits (".NNNNNN"); never the variable-width nanosecond form. The arm-file
//     schema is second-resolution, so 0 microseconds is the normal case → no fraction.
func isoZ(t time.Time) string {
	base := t.Format("2006-01-02T15:04:05")
	if us := t.Nanosecond() / 1000; us != 0 {
		// Python: 6-digit microseconds, zero-padded.
		frac := t.Format(".000000")
		base += frac
	}
	// Offset as Python isoformat: "+HH:MM" / "-HH:MM" (UTC → "+00:00", not "Z").
	_, off := t.Zone()
	sign := "+"
	if off < 0 {
		sign = "-"
		off = -off
	}
	hh := off / 3600
	mm := (off % 3600) / 60
	return base + sign + fmt.Sprintf("%02d:%02d", hh, mm)
}
