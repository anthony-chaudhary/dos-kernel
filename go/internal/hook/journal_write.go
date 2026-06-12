package hook

import (
	"os"
	"strconv"
	"time"
)

// AppendEnforceRecord writes the OP_ENFORCE journal record for a non-passthrough
// PRE outcome — the Go port of `cli._journal_pretool_outcome` +
// `lane_journal.enforce_entry` + `lane_journal.append`. This is what lets the
// native binary OWN a deny/warn entirely (emit the dialect AND write the durable
// forensic record), so it never has to delegate to Python for the WAL write — the
// fix for the stdin-single-consumption problem the shell `||` fallback hit, and the
// full realization of GHF's "zero Python on the hot path" thesis (a deny is now
// fast too).
//
// This is BOUNDARY I/O (the clock + the file write live here, at the edge, never
// inside a verdict — the same rule Python's `append` follows: it is the I/O shell,
// the decision was already made purely). It is best-effort: any failure (unwritable
// dir, a torn read) is swallowed so a journal-write fault never changes the emitted
// dialect — the deny still stands, we just failed to record it (matching the Python
// path, where `_journal_pretool_outcome` is wrapped in a try/except that only logs).
//
// OP_ENFORCE is NOT a state-mutating op (`replay` ignores it for lease state), so
// appending it can never invent or lose a live lease — it only adds history. That
// is why writing it from the binary (outside the lane-lease mutex Python's ACQUIRE
// path uses) is safe: there is no lease-state race, only an append to the log.
func AppendEnforceRecord(journalPath string, ev *Event, d Decision) {
	defer func() { _ = recover() }() // best-effort: a write fault never alters the verdict

	if d.DecisionTag == "passthrough" {
		return
	}
	body := enforceBody(ev, d)
	entry := enforceEntry(ev, d, body)

	// Stamp seq + ts the way `append` does (seq = next_seq, ts = now), then write
	// one canonical-JSON line (sort_keys, ensure_ascii=False) + newline, O_APPEND.
	entry["seq"] = nextSeq(journalPath)
	entry["ts"] = journalNowISO()
	line := pyJSONDumpsWAL(entry) + "\n"

	if dir := dirOf(journalPath); dir != "" {
		_ = os.MkdirAll(dir, 0o755)
	}
	// O_RDWR (not O_WRONLY): the torn-tail repair below must read the last byte.
	f, err := os.OpenFile(journalPath, os.O_RDWR|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	// Torn-tail repair — parity with Python `lane_journal.append` (issue #62). A
	// writer that died mid-append leaves a terminator-less final line; a bare
	// O_APPEND would concatenate THIS record onto that fragment — one unparseable
	// line — so the fsync'd record below would be invisible to readJournal/replay
	// (a lost forensic ENFORCE here; on the Python ACQUIRE path, a falsely-free
	// lane). A leading newline gives the fragment its own line (readJournal keeps
	// it as a _CORRUPT sentinel) and this record stays readable. `\r` counts as a
	// terminator (readJournal folds CR into LF). Fail-soft: an unreadable tail
	// writes as before — the repair can never block the WAL.
	if st, err := f.Stat(); err == nil && st.Size() > 0 {
		buf := make([]byte, 1)
		if _, err := f.ReadAt(buf, st.Size()-1); err == nil && buf[0] != '\n' && buf[0] != '\r' {
			line = "\n" + line
		}
	}
	if _, err := f.WriteString(line); err != nil {
		return
	}
	_ = f.Sync() // fsync — durable before we return, matching Python's os.fsync
}

// enforceBody builds the proposal body — the Go port of the `body` dict in
// `cli._journal_pretool_outcome`. For the admission rung the outcome carries no
// `intervention`/`handler` keys, so they derive: intervention = BLOCK (deny) /
// WARN (warn); handler = rung.
func enforceBody(ev *Event, d Decision) map[string]any {
	intervention := "WARN"
	if d.DecisionTag == "deny" {
		intervention = "BLOCK"
	}
	dispatchCall := d.DecisionTag != "deny"
	rung := d.Rung
	reason := d.Reason
	if reason == "" {
		reason = d.ReasonClass
	}
	if reason == "" {
		reason = rung
	}
	return map[string]any{
		"intervention":  intervention,
		"dispatch_call": dispatchCall,
		"handler":       rung, // outcome has no separate handler at the admission rung
		"reason":        reason,
		"rung":          rung,
		"decision":      d.DecisionTag,
		"reason_class":  d.ReasonClass,
	}
}

// enforceEntry wraps the body into the full OP_ENFORCE entry — the Go port of
// `lane_journal.enforce_entry`, with the lane/owner/tool fields the CLI supplies in
// `_journal_pretool_outcome` (lane = tool_name, owner = session_id, tool =
// tool_name; the loop/host/run ids from the environment).
func enforceEntry(ev *Event, d Decision, body map[string]any) map[string]any {
	// Lift the rung + dispatch flag to the top level, exactly as enforce_entry does.
	rung, _ := body["intervention"].(string)
	dispatch, _ := body["dispatch_call"].(bool)
	tool := ev.ToolName
	lane := tool
	if lane == "" {
		lane = "tool"
	}
	return map[string]any{
		"op":            "ENFORCE",
		"lane":          lane,
		"loop_ts":       os.Getenv("DISPATCH_LOOP_TS"),
		"host_id":       envOrNil("DISPATCH_HOST_ID"),
		"run_id":        envOrNil("CID_RUN_ID"),
		"holder":        ev.SessionID,
		"tool":          tool,
		"intervention":  rung,
		"dispatch_call": dispatch,
		"withheld":      !dispatch,
		"handler":       strOr(body["handler"], ""),
		"reason":        strOr(body["reason"], ""),
		"proposal":      body,
	}
}

// nextSeq = max existing seq (and seq_watermark) + 1 — the Go port of
// `lane_journal.next_seq`. Reads the whole WAL (the same O(file) the Python path
// pays); a missing/unreadable WAL yields seq 1.
func nextSeq(journalPath string) int {
	mx := 0
	for _, e := range readJournal(journalPath) {
		mx = maxInt(mx, asInt(e["seq"]), asInt(e["seq_watermark"]))
	}
	return mx + 1
}

// journalNowISO is the second-resolution UTC stamp `lane_journal.journal_now_iso`
// writes. The clock lives HERE, at the boundary (the WAL write), never in a verdict.
func journalNowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05Z")
}

func envOrNil(name string) any {
	if v := os.Getenv(name); v != "" {
		return v
	}
	// Python passes os.environ.get(name, "") (default "") into run_id/host_id; an
	// absent env var becomes "" there, not None — match that (the entry carries "").
	return ""
}

func strOr(v any, def string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return def
}

func asInt(v any) int {
	switch x := v.(type) {
	case float64:
		return int(x)
	case int:
		return x
	case string:
		if n, err := strconv.Atoi(x); err == nil {
			return n
		}
	}
	return 0
}

func maxInt(xs ...int) int {
	m := 0
	for _, x := range xs {
		if x > m {
			m = x
		}
	}
	return m
}

func dirOf(path string) string {
	for i := len(path) - 1; i >= 0; i-- {
		if path[i] == '/' || path[i] == '\\' {
			return path[:i]
		}
	}
	return ""
}
