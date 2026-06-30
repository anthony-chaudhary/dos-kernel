package hook

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"
)

var callShapePolicyKeys = map[string]struct{}{
	"forbidden_command_prefixes": {},
	"forbidden_arg_patterns":     {},
	"forbidden_path_globs":       {},
}

// ReadCallShape reads the workspace's declared [call_shape] policy from dos.toml.
// It is boundary I/O for DecidePretool: absent dos.toml or absent [call_shape] means
// an empty ruleset; a present but malformed [call_shape] returns an error so the hook
// can fail closed instead of silently disabling a declared ban.
func ReadCallShape(workspace string) (CallShapeRuleset, error) {
	if workspace == "" {
		return CallShapeRuleset{}, nil
	}
	return readCallShapeFromToml(filepath.Join(workspace, "dos.toml"))
}

func readCallShapeFromToml(path string) (CallShapeRuleset, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return CallShapeRuleset{}, nil
		}
		return CallShapeRuleset{}, fmt.Errorf("read dos.toml: %w", err)
	}
	raw = bytes.TrimPrefix(raw, utf8BOM)
	if !utf8.Valid(raw) {
		return CallShapeRuleset{}, fmt.Errorf("dos.toml is not UTF-8")
	}

	p := callShapeTomlParser{
		lanes: map[string]*callShapePolicyDraft{},
	}
	if err := p.parse(string(raw)); err != nil {
		return CallShapeRuleset{}, err
	}
	if !p.seenCallShape {
		return CallShapeRuleset{}, nil
	}
	perLane := make(map[string]CallShapePolicy, len(p.lanes))
	for lane, draft := range p.lanes {
		perLane[lane] = draft.policy
	}
	return CallShapeRuleset{WorkspaceWide: p.wide.policy, PerLane: perLane}, nil
}

type callShapeTomlParser struct {
	section       string
	lane          string
	seenCallShape bool
	wide          callShapePolicyDraft
	lanes         map[string]*callShapePolicyDraft
	pending       *callShapePendingValue
}

type callShapePendingValue struct {
	where string
	key   string
	value string
	line  int
}

type callShapePolicyDraft struct {
	policy CallShapePolicy
	keys   map[string]struct{}
}

func (p *callShapeTomlParser) parse(text string) error {
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	for i, raw := range lines {
		lineNo := i + 1
		line := strings.TrimSpace(callShapeStripInlineComment(raw))
		if p.pending != nil {
			if line != "" {
				p.pending.value += "\n" + line
			}
			if callShapeArrayComplete(p.pending.value) {
				if err := p.apply(p.pending.where, p.pending.key, p.pending.value, p.pending.line); err != nil {
					return err
				}
				p.pending = nil
			}
			continue
		}
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "[") {
			name, ok, err := callShapeTableName(line)
			if err != nil {
				return fmt.Errorf("dos.toml line %d: %w", lineNo, err)
			}
			if !ok {
				p.section = "other"
				p.lane = ""
				continue
			}
			if name == "call_shape" {
				p.section = "wide"
				p.lane = ""
				p.seenCallShape = true
				continue
			}
			if lane, ok, err := callShapeLaneName(name); err != nil {
				return fmt.Errorf("dos.toml line %d: %w", lineNo, err)
			} else if ok {
				p.section = "lane"
				p.lane = lane
				p.seenCallShape = true
				if p.lanes[lane] == nil {
					p.lanes[lane] = &callShapePolicyDraft{}
				}
				continue
			}
			p.section = "other"
			p.lane = ""
			continue
		}
		if p.section != "wide" && p.section != "lane" {
			continue
		}
		eq := strings.IndexByte(line, '=')
		if eq < 0 {
			return fmt.Errorf("dos.toml line %d: %s expected key = value", lineNo, p.where())
		}
		key := strings.TrimSpace(line[:eq])
		value := strings.TrimSpace(line[eq+1:])
		if _, ok := callShapePolicyKeys[key]; !ok {
			return fmt.Errorf("dos.toml line %d: unknown %s key(s): %s (allowed: forbidden_arg_patterns, forbidden_command_prefixes, forbidden_path_globs)", lineNo, p.where(), key)
		}
		if strings.HasPrefix(value, "[") && !callShapeArrayComplete(value) {
			p.pending = &callShapePendingValue{where: p.where(), key: key, value: value, line: lineNo}
			continue
		}
		if err := p.apply(p.where(), key, value, lineNo); err != nil {
			return err
		}
	}
	if p.pending != nil {
		return fmt.Errorf("dos.toml line %d: %s %s array is unterminated", p.pending.line, p.pending.where, p.pending.key)
	}
	return nil
}

func (p *callShapeTomlParser) where() string {
	if p.section == "lane" {
		return "[call_shape." + p.lane + "]"
	}
	return "[call_shape]"
}

func (p *callShapeTomlParser) draft() *callShapePolicyDraft {
	if p.section == "lane" {
		if p.lanes[p.lane] == nil {
			p.lanes[p.lane] = &callShapePolicyDraft{}
		}
		return p.lanes[p.lane]
	}
	return &p.wide
}

func (p *callShapeTomlParser) apply(where, key, value string, line int) error {
	if err := p.draft().set(key, value, where); err != nil {
		return fmt.Errorf("dos.toml line %d: %w", line, err)
	}
	return nil
}

func (d *callShapePolicyDraft) set(key, value, where string) error {
	if d.keys == nil {
		d.keys = map[string]struct{}{}
	}
	if _, dup := d.keys[key]; dup {
		return fmt.Errorf("duplicate %s key %s", where, key)
	}
	d.keys[key] = struct{}{}
	switch key {
	case "forbidden_command_prefixes":
		prefixes, err := coerceCallShapePrefixes(value, key)
		if err != nil {
			return err
		}
		d.policy.ForbiddenCommandPrefixes = prefixes
	case "forbidden_arg_patterns":
		patterns, err := coerceCallShapeStringList(value, key)
		if err != nil {
			return err
		}
		d.policy.ForbiddenArgPatterns = patterns
	case "forbidden_path_globs":
		globs, err := coerceCallShapeStringList(value, key)
		if err != nil {
			return err
		}
		d.policy.ForbiddenPathGlobs = globs
	}
	return nil
}

func coerceCallShapePrefixes(raw, key string) ([][]string, error) {
	vals, err := parseCallShapeArray(raw)
	if err != nil {
		return nil, fmt.Errorf("[call_shape] %s must be a list: %w", key, err)
	}
	out := make([][]string, 0, len(vals))
	for _, entry := range vals {
		var toks []string
		switch entry.kind {
		case "string":
			for _, tok := range strings.Fields(entry.text) {
				if strings.TrimSpace(tok) != "" {
					toks = append(toks, strings.ToLower(tok))
				}
			}
		case "array":
			for _, item := range entry.items {
				s := strings.TrimSpace(item.asString())
				if s != "" {
					toks = append(toks, strings.ToLower(s))
				}
			}
		default:
			return nil, fmt.Errorf("[call_shape] %s entries must be strings or lists, got %s", key, entry.typeName())
		}
		if len(toks) > 0 {
			out = append(out, toks)
		}
	}
	return out, nil
}

func coerceCallShapeStringList(raw, key string) ([]string, error) {
	vals, err := parseCallShapeArray(raw)
	if err != nil {
		return nil, fmt.Errorf("[call_shape] %s must be a list: %w", key, err)
	}
	out := make([]string, 0, len(vals))
	for _, entry := range vals {
		if entry.kind != "string" {
			return nil, fmt.Errorf("[call_shape] %s entries must be strings, got %s", key, entry.typeName())
		}
		s := entry.text
		if key == "forbidden_path_globs" {
			s = strings.TrimSpace(s)
		}
		if s != "" {
			out = append(out, s)
		}
	}
	return out, nil
}

func callShapeConfigDeny(e *Event, err error) Decision {
	_, treeKnown := e.treeFromEvent()
	reason := "workspace dos.toml declares a malformed [call_shape] policy (" +
		err.Error() + ") - refusing to fail open because a malformed forbidden-shape " +
		"ban must not silently disable enforcement (FORBIDDEN_CALL_SHAPE). Fix " +
		"dos.toml or remove [call_shape]."
	return Decision{
		Dialect:     denyPayload("DOS PRE-admission: "+reason, ""),
		Rung:        "admission",
		DecisionTag: "deny",
		ReasonClass: forbiddenCallShapeReason,
		Reason:      reason,
		TreeKnown:   treeKnown,
	}
}

func callShapeTableName(line string) (string, bool, error) {
	if strings.HasPrefix(line, "[[") {
		if strings.Contains(line, "call_shape") {
			return "", false, fmt.Errorf("array-of-table call_shape declarations are not supported")
		}
		return "", false, nil
	}
	if !strings.HasPrefix(line, "[") || !strings.HasSuffix(line, "]") {
		if strings.Contains(line, "call_shape") {
			return "", false, fmt.Errorf("malformed call_shape table header")
		}
		return "", false, nil
	}
	name := strings.TrimSpace(line[1 : len(line)-1])
	if name == "" {
		return "", false, nil
	}
	return name, true, nil
}

func callShapeLaneName(table string) (string, bool, error) {
	const prefix = "call_shape."
	if !strings.HasPrefix(table, prefix) {
		return "", false, nil
	}
	lane := strings.TrimSpace(table[len(prefix):])
	if lane == "" {
		return "", false, fmt.Errorf("empty [call_shape.<lane>] table name")
	}
	if (strings.HasPrefix(lane, "\"") && strings.HasSuffix(lane, "\"")) ||
		(strings.HasPrefix(lane, "'") && strings.HasSuffix(lane, "'")) {
		unquoted, err := callShapeUnquoteString(lane)
		if err != nil {
			return "", false, err
		}
		if strings.TrimSpace(unquoted) == "" {
			return "", false, fmt.Errorf("empty [call_shape.<lane>] table name")
		}
		return unquoted, true, nil
	}
	if strings.Contains(lane, ".") {
		return "", false, fmt.Errorf("nested [call_shape.%s] tables are not valid call-shape lanes; quote a literal dot in the lane name", lane)
	}
	return lane, true, nil
}

func callShapeStripInlineComment(line string) string {
	inQuote := byte(0)
	escape := false
	for i := 0; i < len(line); i++ {
		c := line[i]
		if inQuote != 0 {
			if inQuote == '"' && escape {
				escape = false
				continue
			}
			if inQuote == '"' && c == '\\' {
				escape = true
				continue
			}
			if c == inQuote {
				inQuote = 0
			}
			continue
		}
		if c == '"' || c == '\'' {
			inQuote = c
			continue
		}
		if c == '#' {
			return line[:i]
		}
	}
	return line
}

func callShapeArrayComplete(value string) bool {
	depth := 0
	inQuote := byte(0)
	escape := false
	for i := 0; i < len(value); i++ {
		c := value[i]
		if inQuote != 0 {
			if inQuote == '"' && escape {
				escape = false
				continue
			}
			if inQuote == '"' && c == '\\' {
				escape = true
				continue
			}
			if c == inQuote {
				inQuote = 0
			}
			continue
		}
		if c == '"' || c == '\'' {
			inQuote = c
			continue
		}
		switch c {
		case '[':
			depth++
		case ']':
			depth--
			if depth <= 0 {
				return true
			}
		}
	}
	return depth == 0
}

type callShapeTomlValue struct {
	kind  string
	text  string
	items []callShapeTomlValue
}

func (v callShapeTomlValue) asString() string {
	if v.kind == "array" {
		parts := make([]string, 0, len(v.items))
		for _, item := range v.items {
			parts = append(parts, item.asString())
		}
		return "[" + strings.Join(parts, ", ") + "]"
	}
	return v.text
}

func (v callShapeTomlValue) typeName() string {
	if v.kind == "bare" {
		switch strings.ToLower(v.text) {
		case "true", "false":
			return "bool"
		}
		if _, err := strconv.ParseInt(v.text, 10, 64); err == nil {
			return "int"
		}
	}
	if v.kind == "array" {
		return "list"
	}
	return v.kind
}

type callShapeValueParser struct {
	s   string
	pos int
}

func parseCallShapeArray(raw string) ([]callShapeTomlValue, error) {
	p := &callShapeValueParser{s: strings.TrimSpace(raw)}
	v, err := p.parseValue()
	if err != nil {
		return nil, err
	}
	p.skipSpace()
	if p.pos != len(p.s) {
		return nil, fmt.Errorf("trailing bytes after value")
	}
	if v.kind != "array" {
		return nil, fmt.Errorf("got %s", v.typeName())
	}
	return v.items, nil
}

func (p *callShapeValueParser) parseValue() (callShapeTomlValue, error) {
	p.skipSpace()
	if p.pos >= len(p.s) {
		return callShapeTomlValue{}, fmt.Errorf("empty value")
	}
	switch p.s[p.pos] {
	case '[':
		return p.parseArray()
	case '"', '\'':
		text, err := p.parseString()
		if err != nil {
			return callShapeTomlValue{}, err
		}
		return callShapeTomlValue{kind: "string", text: text}, nil
	default:
		return p.parseBare()
	}
}

func (p *callShapeValueParser) parseArray() (callShapeTomlValue, error) {
	p.pos++ // [
	var items []callShapeTomlValue
	for {
		p.skipSpace()
		if p.pos >= len(p.s) {
			return callShapeTomlValue{}, fmt.Errorf("unterminated list")
		}
		if p.s[p.pos] == ']' {
			p.pos++
			return callShapeTomlValue{kind: "array", items: items}, nil
		}
		item, err := p.parseValue()
		if err != nil {
			return callShapeTomlValue{}, err
		}
		items = append(items, item)
		p.skipSpace()
		if p.pos >= len(p.s) {
			return callShapeTomlValue{}, fmt.Errorf("unterminated list")
		}
		switch p.s[p.pos] {
		case ',':
			p.pos++
			continue
		case ']':
			p.pos++
			return callShapeTomlValue{kind: "array", items: items}, nil
		default:
			return callShapeTomlValue{}, fmt.Errorf("expected comma or closing bracket")
		}
	}
}

func (p *callShapeValueParser) parseString() (string, error) {
	q := p.s[p.pos]
	start := p.pos
	p.pos++
	if q == '\'' {
		for p.pos < len(p.s) {
			if p.s[p.pos] == '\'' {
				out := p.s[start+1 : p.pos]
				p.pos++
				return out, nil
			}
			p.pos++
		}
		return "", fmt.Errorf("unterminated string")
	}
	escape := false
	for p.pos < len(p.s) {
		c := p.s[p.pos]
		if escape {
			escape = false
			p.pos++
			continue
		}
		if c == '\\' {
			escape = true
			p.pos++
			continue
		}
		if c == '"' {
			lit := p.s[start : p.pos+1]
			p.pos++
			return callShapeUnquoteString(lit)
		}
		p.pos++
	}
	return "", fmt.Errorf("unterminated string")
}

func (p *callShapeValueParser) parseBare() (callShapeTomlValue, error) {
	start := p.pos
	for p.pos < len(p.s) && p.s[p.pos] != ',' && p.s[p.pos] != ']' {
		p.pos++
	}
	text := strings.TrimSpace(p.s[start:p.pos])
	if text == "" {
		return callShapeTomlValue{}, fmt.Errorf("empty bare value")
	}
	return callShapeTomlValue{kind: "bare", text: text}, nil
}

func (p *callShapeValueParser) skipSpace() {
	for p.pos < len(p.s) {
		switch p.s[p.pos] {
		case ' ', '\t', '\n', '\r':
			p.pos++
		default:
			return
		}
	}
}

func callShapeUnquoteString(lit string) (string, error) {
	if strings.HasPrefix(lit, "'") && strings.HasSuffix(lit, "'") {
		return lit[1 : len(lit)-1], nil
	}
	out, err := strconv.Unquote(lit)
	if err != nil {
		return "", fmt.Errorf("invalid string %s", lit)
	}
	return out, nil
}
