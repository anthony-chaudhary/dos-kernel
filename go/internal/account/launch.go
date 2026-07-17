package account

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const (
	AccountsFileEnv       = "CLAUDE_ACCOUNTS_FILE"
	DefaultAccountsFile   = "~/.claude/accounts.yaml"
	EnvClaudeConfigDir    = "CLAUDE_CONFIG_DIR"
	EnvClaudeOAuthToken   = "CLAUDE_CODE_OAUTH_TOKEN"
	credsFilename         = ".credentials.json"
	tokenFilename         = ".oauth-token"
	defaultRotationOrder  = "by_reset"
	defaultNearCapPresent = DefaultNearCapUtil
)

// Account is one roster seat: a name mapped to an isolated config directory.
// This is the disk-bearing twin of the Python switcher's Account dataclass.
type Account struct {
	Name          string
	ConfigDir     string
	ChromeProfile string
	Email         string
	Enabled       bool
}

// RotationPolicy is the roster's pick policy. Only NearCapUtil is decision-bearing
// today; Order is preserved for byte-shaped parity with the Python roster object.
type RotationPolicy struct {
	Order       string
	NearCapUtil float64
}

// DefaultRotationPolicy returns the Python switcher's default policy.
func DefaultRotationPolicy() RotationPolicy {
	return RotationPolicy{Order: defaultRotationOrder, NearCapUtil: defaultNearCapPresent}
}

// LaunchOptions are the inputs to the Go launch resolver slice. Probes are keyed
// by account name because the on-disk refresher cache is a per-account seam; a nil
// or missing probe preserves the switcher's fail-open behavior.
type LaunchOptions struct {
	RosterPath string
	SeatIndex  int
	Now        float64
	Environ    map[string]string
	Probes     map[string]*Probe
}

// LaunchEnv is the resolved account pick plus the child-process env overrides.
// Env is nil when the pick is not OK, e.g. every enrolled account is walled.
type LaunchEnv struct {
	Account           string
	HasAccount        bool
	Reason            string
	WaitSeconds       *int
	SoonestResetEpoch *int64
	Env               map[string]string
}

// OK mirrors Pick.OK and additionally requires an emitted env.
func (l LaunchEnv) OK() bool {
	return l.HasAccount && l.WaitSeconds == nil && l.Env != nil
}

func nowEpoch(now float64) float64 {
	if now != 0 {
		return now
	}
	return float64(time.Now().UnixNano()) / 1e9
}

// AccountsFilePath mirrors accounts_file_path: explicit arg, then env, then the
// home default.
func AccountsFilePath(explicit string, environ map[string]string) string {
	if strings.TrimSpace(explicit) != "" {
		return explicit
	}
	if environ != nil {
		if v := strings.TrimSpace(environ[AccountsFileEnv]); v != "" {
			return v
		}
	} else if v := strings.TrimSpace(os.Getenv(AccountsFileEnv)); v != "" {
		return v
	}
	return expandUser(DefaultAccountsFile)
}

// LoadRoster parses the active account roster. It is fail-open like Python:
// missing, unreadable, or malformed input returns an empty roster and defaults.
func LoadRoster(path string) ([]Account, RotationPolicy) {
	return LoadRosterWithEnv(path, nil)
}

// LoadRosterWithEnv is LoadRoster with an injectable environment for
// CLAUDE_ACCOUNTS_FILE.
func LoadRosterWithEnv(path string, environ map[string]string) ([]Account, RotationPolicy) {
	p := AccountsFilePath(path, environ)
	data, err := os.ReadFile(p)
	if err != nil {
		return nil, DefaultRotationPolicy()
	}
	accounts, policy, ok := parseRoster(string(data))
	if !ok {
		return nil, DefaultRotationPolicy()
	}
	return DedupeByIdentity(accounts), policy
}

// ResolveLaunchEnv loads the roster, folds on-disk auth state, applies the Go
// picker, and emits the selected account's launch env.
func ResolveLaunchEnv(opts LaunchOptions) (LaunchEnv, error) {
	accounts, policy := LoadRosterWithEnv(opts.RosterPath, opts.Environ)
	now := nowEpoch(opts.Now)
	states := make([]State, len(accounts))
	for i, acct := range accounts {
		states[i] = Classify(accountFacts(acct, opts.Probes[acct.Name], now), policy.NearCapUtil)
	}
	pick := PickAccountSpread(states, opts.SeatIndex, policy.NearCapUtil, now)
	out := LaunchEnv{
		Account:           pick.Account,
		HasAccount:        pick.HasAccount,
		Reason:            pick.Reason,
		WaitSeconds:       pick.WaitSeconds,
		SoonestResetEpoch: pick.SoonestResetEpoch,
	}
	if !pick.OK() {
		return out, nil
	}
	acct, ok := findAccount(accounts, pick.Account)
	if !ok {
		return out, fmt.Errorf("selected account %q is not in roster", pick.Account)
	}
	env, err := EnvFor(acct, opts.Environ, now)
	if err != nil {
		return out, err
	}
	out.Env = env
	return out, nil
}

func findAccount(accounts []Account, name string) (Account, bool) {
	for _, acct := range accounts {
		if acct.Name == name {
			return acct, true
		}
	}
	return Account{}, false
}

func accountFacts(account Account, probe *Probe, now float64) Facts {
	present, expired := TokenExpired(AccountCredsPath(account), now)
	_, hasToken := ReadAccountToken(account)
	return Facts{
		Name:         account.Name,
		Enabled:      account.Enabled,
		CredsPresent: present,
		TokenExpired: expired,
		HasToken:     hasToken,
		Probe:        probe,
	}
}

// AccountCredsPath is the isolated credentials file for an account's login.
func AccountCredsPath(account Account) string {
	return filepath.Join(expandUser(account.ConfigDir), credsFilename)
}

// AccountTokenPath is the isolated setup-token store for an account.
func AccountTokenPath(account Account) string {
	return filepath.Join(expandUser(account.ConfigDir), tokenFilename)
}

// ReadAccountToken returns the stored setup-token, if present and non-empty.
func ReadAccountToken(account Account) (string, bool) {
	b, err := os.ReadFile(AccountTokenPath(account))
	if err != nil {
		return "", false
	}
	tok := strings.TrimSpace(string(b))
	if tok == "" {
		return "", false
	}
	return tok, true
}

// TokenExpired mirrors _token_expired: it never raises and treats present creds
// with an absent/unparseable expiry as usable.
func TokenExpired(credsPath string, now float64) (bool, bool) {
	info, err := os.Stat(credsPath)
	if err != nil || info.IsDir() {
		return false, false
	}
	b, err := os.ReadFile(credsPath)
	if err != nil {
		return false, false
	}
	var root map[string]any
	if err := json.Unmarshal(b, &root); err != nil {
		return false, false
	}
	oauth, ok := root["claudeAiOauth"].(map[string]any)
	if !ok {
		return false, false
	}
	token, ok := oauth["accessToken"].(string)
	if !ok || strings.TrimSpace(token) == "" {
		return false, false
	}
	exp, ok := numberAsFloat(oauth["expiresAt"])
	if ok && exp > 0 {
		return true, exp/1000.0 <= nowEpoch(now)
	}
	return true, false
}

// HasFreshLoginCreds reports whether .credentials.json is present and unexpired.
func HasFreshLoginCreds(account Account, now float64) bool {
	present, expired := TokenExpired(AccountCredsPath(account), now)
	return present && !expired
}

// AccountEnvOverrides mirrors account_env_overrides.
func AccountEnvOverrides(account Account, environ map[string]string, now float64) (map[string]string, error) {
	cfg := expandUser(account.ConfigDir)
	info, err := os.Stat(cfg)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf(
			"account %s: config_dir does not exist: %s - create it and log in once:  $env:CLAUDE_CONFIG_DIR = %q; claude (then /login with that account)",
			pySingleQuote(account.Name), cfg, cfg)
	}
	env := map[string]string{EnvClaudeConfigDir: cfg}
	if strings.TrimSpace(envLookup(environ, EnvClaudeOAuthToken)) == "" && !HasFreshLoginCreds(account, now) {
		if tok, ok := ReadAccountToken(account); ok {
			env[EnvClaudeOAuthToken] = tok
		}
	}
	return env, nil
}

// EnvFor mirrors env_for, including the top-level token splice after the lower
// level account_env_overrides call.
func EnvFor(account Account, environ map[string]string, now float64) (map[string]string, error) {
	env, err := AccountEnvOverrides(account, environ, now)
	if err != nil {
		return nil, err
	}
	if _, ok := env[EnvClaudeOAuthToken]; !ok && !HasFreshLoginCreds(account, now) {
		if tok, hasToken := ReadAccountToken(account); hasToken {
			env[EnvClaudeOAuthToken] = tok
		}
	}
	return env, nil
}

// DedupeByIdentity collapses proven duplicate roster seats to their first entry.
func DedupeByIdentity(accounts []Account) []Account {
	seen := map[string]bool{}
	out := make([]Account, 0, len(accounts))
	for _, acct := range accounts {
		key, ok := accountIdentityKey(acct)
		if ok {
			if seen[key] {
				continue
			}
			seen[key] = true
		}
		out = append(out, acct)
	}
	return out
}

func accountIdentityKey(account Account) (string, bool) {
	b, err := os.ReadFile(AccountCredsPath(account))
	if err == nil {
		var root map[string]any
		if json.Unmarshal(b, &root) == nil {
			if oauth, ok := root["claudeAiOauth"].(map[string]any); ok {
				if tok, ok := oauth["accessToken"].(string); ok && strings.TrimSpace(tok) != "" {
					return "cred:" + strings.TrimSpace(tok), true
				}
			}
		}
	}
	if tok, ok := ReadAccountToken(account); ok {
		return "oat:" + tok, true
	}
	return "", false
}

func envLookup(environ map[string]string, key string) string {
	if environ != nil {
		return environ[key]
	}
	return os.Getenv(key)
}

func expandUser(path string) string {
	if path == "~" {
		if home, err := os.UserHomeDir(); err == nil {
			return home
		}
		return path
	}
	if strings.HasPrefix(path, "~/") || strings.HasPrefix(path, `~\`) {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, path[2:])
		}
	}
	return path
}

func numberAsFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case float32:
		return float64(x), true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case json.Number:
		f, err := x.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}

type scalar struct {
	value  string
	quoted bool
}

func parseRoster(text string) ([]Account, RotationPolicy, bool) {
	policy := DefaultRotationPolicy()
	var accounts []Account
	var current *Account
	section := ""
	for _, raw := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		if strings.TrimSpace(raw) == "" || strings.HasPrefix(strings.TrimSpace(raw), "#") {
			continue
		}
		indent := len(raw) - len(strings.TrimLeft(raw, " \t"))
		line := strings.TrimSpace(raw)
		if indent == 0 {
			key, val, ok := splitKeyValue(line)
			if !ok {
				return nil, DefaultRotationPolicy(), false
			}
			switch key {
			case "accounts":
				if val.value != "" && val.value != "[]" {
					return nil, DefaultRotationPolicy(), false
				}
				section = "accounts"
			case "rotation":
				if val.value != "" && val.value != "{}" {
					return nil, DefaultRotationPolicy(), false
				}
				section = "rotation"
			default:
				section = ""
			}
			continue
		}
		switch section {
		case "accounts":
			if strings.HasPrefix(line, "- ") {
				acct := Account{Enabled: true}
				accounts = append(accounts, acct)
				current = &accounts[len(accounts)-1]
				rest := strings.TrimSpace(strings.TrimPrefix(line, "- "))
				if rest != "" {
					key, val, ok := splitKeyValue(rest)
					if !ok {
						return nil, DefaultRotationPolicy(), false
					}
					assignAccount(current, key, val)
				}
				continue
			}
			if current == nil {
				return nil, DefaultRotationPolicy(), false
			}
			key, val, ok := splitKeyValue(line)
			if !ok {
				return nil, DefaultRotationPolicy(), false
			}
			assignAccount(current, key, val)
		case "rotation":
			key, val, ok := splitKeyValue(line)
			if !ok {
				return nil, DefaultRotationPolicy(), false
			}
			switch key {
			case "order":
				if val.value != "" {
					policy.Order = val.value
				}
			case "near_cap_util":
				if f, err := strconv.ParseFloat(val.value, 64); err == nil {
					policy.NearCapUtil = f
				} else {
					policy.NearCapUtil = DefaultNearCapUtil
				}
			}
		}
	}
	out := make([]Account, 0, len(accounts))
	for _, acct := range accounts {
		acct.Name = strings.TrimSpace(acct.Name)
		acct.ConfigDir = strings.TrimSpace(acct.ConfigDir)
		if acct.Name == "" || acct.ConfigDir == "" {
			continue
		}
		out = append(out, acct)
	}
	return out, policy, true
}

func assignAccount(acct *Account, key string, val scalar) {
	switch key {
	case "name":
		acct.Name = val.value
	case "config_dir":
		acct.ConfigDir = val.value
	case "chrome_profile":
		acct.ChromeProfile = val.value
	case "email":
		acct.Email = val.value
	case "enabled":
		acct.Enabled = parseEnabled(val)
	}
}

func parseEnabled(val scalar) bool {
	if val.quoted {
		return val.value != ""
	}
	switch strings.ToLower(strings.TrimSpace(val.value)) {
	case "", "true", "yes", "on", "1":
		return true
	case "false", "no", "off", "0":
		return false
	default:
		return true
	}
}

func splitKeyValue(line string) (string, scalar, bool) {
	cut := -1
	inSingle, inDouble := false, false
scan:
	for i, r := range line {
		switch r {
		case '\'':
			if !inDouble {
				inSingle = !inSingle
			}
		case '"':
			if !inSingle {
				inDouble = !inDouble
			}
		case ':':
			if !inSingle && !inDouble {
				cut = i
				break scan
			}
		}
	}
	if cut < 0 {
		return "", scalar{}, false
	}
	key := strings.TrimSpace(line[:cut])
	if key == "" {
		return "", scalar{}, false
	}
	val := parseScalar(line[cut+1:])
	return key, val, true
}

func parseScalar(raw string) scalar {
	raw = strings.TrimSpace(stripInlineComment(raw))
	if len(raw) >= 2 {
		if raw[0] == '\'' && raw[len(raw)-1] == '\'' {
			return scalar{value: strings.ReplaceAll(raw[1:len(raw)-1], "''", "'"), quoted: true}
		}
		if raw[0] == '"' && raw[len(raw)-1] == '"' {
			if unq, err := strconv.Unquote(raw); err == nil {
				return scalar{value: unq, quoted: true}
			}
			return scalar{value: raw[1 : len(raw)-1], quoted: true}
		}
	}
	return scalar{value: raw}
}

func stripInlineComment(raw string) string {
	inSingle, inDouble := false, false
	for i, r := range raw {
		switch r {
		case '\'':
			if !inDouble {
				inSingle = !inSingle
			}
		case '"':
			if !inSingle {
				inDouble = !inDouble
			}
		case '#':
			if !inSingle && !inDouble {
				return raw[:i]
			}
		}
	}
	return raw
}

func pySingleQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `\'`) + "'"
}
