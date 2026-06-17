package account

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// parityCase mirrors one line of parity/corpus.jsonl — the hermetic differential case
// the Python oracle (gen_corpus.py) produced by driving the REAL account_switcher. The
// Go test replays each case through the native ranking core with the SAME folded facts
// and asserts it reproduces every recorded decision + detail string (value-exact).
type parityCase struct {
	Name        string                  `json:"name"`
	NearCapUtil float64                 `json:"near_cap_util"`
	Now         float64                 `json:"now"`
	Accounts    []corpusFacts           `json:"accounts"`
	States      []corpusState           `json:"states"`
	Pick        corpusPick              `json:"pick"`
	ServingPool []string                `json:"serving_pool"`
	Allocate    map[string][]string     `json:"allocate"`
	Spread      map[string]corpusSpread `json:"spread"`
}

type corpusProbe struct {
	Allowed      bool    `json:"allowed"`
	Utilization  float64 `json:"utilization"`
	ResetAtEpoch *int64  `json:"reset_at_epoch"`
	Status       string  `json:"status"`
}

type corpusFacts struct {
	Name         string       `json:"name"`
	Enabled      bool         `json:"enabled"`
	CredsPresent bool         `json:"creds_present"`
	TokenExpired bool         `json:"token_expired"`
	HasToken     bool         `json:"has_token"`
	Probe        *corpusProbe `json:"probe"`
}

type corpusState struct {
	Name         string   `json:"name"`
	Kind         string   `json:"kind"`
	CredsPresent bool     `json:"creds_present"`
	TokenExpired bool     `json:"token_expired"`
	ProbeStatus  string   `json:"probe_status"`
	Utilization  *float64 `json:"utilization"`
	ResetAtEpoch *int64   `json:"reset_at_epoch"`
	Detail       string   `json:"detail"`
}

type corpusPick struct {
	Account           *string `json:"account"`
	Reason            string  `json:"reason"`
	WaitSeconds       *int    `json:"wait_seconds"`
	SoonestResetEpoch *int64  `json:"soonest_reset_epoch"`
	Ok                bool    `json:"ok"`
}

type corpusSpread struct {
	Account *string `json:"account"`
	Reason  string  `json:"reason"`
}

func loadCorpus(t *testing.T) []parityCase {
	t.Helper()
	path := filepath.Join("parity", "corpus.jsonl")
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open corpus %s: %v (run `python go/internal/account/parity/gen_corpus.py > %s`)", path, err, path)
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

func factsFromCorpus(c parityCase) []Facts {
	out := make([]Facts, len(c.Accounts))
	for i, a := range c.Accounts {
		f := Facts{
			Name:         a.Name,
			Enabled:      a.Enabled,
			CredsPresent: a.CredsPresent,
			TokenExpired: a.TokenExpired,
			HasToken:     a.HasToken,
		}
		if a.Probe != nil {
			f.Probe = &Probe{
				Allowed:      a.Probe.Allowed,
				Utilization:  a.Probe.Utilization,
				ResetAtEpoch: a.Probe.ResetAtEpoch,
				Status:       a.Probe.Status,
			}
		}
		out[i] = f
	}
	return out
}

func strOrEmpty(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func floatPtrEq(a, b *float64) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func int64PtrEq(a, b *int64) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func intPtrEq(a, b *int) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func sliceEq(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// TestParityCorpus is the differential parity gate (docs/386 §6) and — under the
// docs/385 PORT→FLIP — the canonical pin: the Go ranking core IS the spec for the
// account-pick/serving-pool/allocate-seats deciders, and this assertion defines the
// corpus. For every case it folds the recorded facts and asserts the native core
// reproduces account_state, pick_account, serving_pool, allocate_seats, and
// pick_account_spread value-for-value, including every detail string.
func TestParityCorpus(t *testing.T) {
	for _, c := range loadCorpus(t) {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			facts := factsFromCorpus(c)
			states := ClassifyAll(facts, c.NearCapUtil)

			// account_state classification per account.
			if len(states) != len(c.States) {
				t.Fatalf("state count: go=%d py=%d", len(states), len(c.States))
			}
			for i, got := range states {
				want := c.States[i]
				if got.Name != want.Name || got.Kind != want.Kind ||
					got.CredsPresent != want.CredsPresent || got.TokenExpired != want.TokenExpired ||
					got.ProbeStatus != want.ProbeStatus || got.Detail != want.Detail ||
					!floatPtrEq(got.Utilization, want.Utilization) ||
					!int64PtrEq(got.ResetAtEpoch, want.ResetAtEpoch) {
					t.Fatalf("STATE DRIFT [%d] %q:\n  py: %+v\n  go: %+v", i, want.Name, want, got)
				}
			}

			// pick_account.
			pick := PickAccount(states, c.NearCapUtil, c.Now)
			if pick.Account != strOrEmpty(c.Pick.Account) || pick.Reason != c.Pick.Reason ||
				!intPtrEq(pick.WaitSeconds, c.Pick.WaitSeconds) ||
				!int64PtrEq(pick.SoonestResetEpoch, c.Pick.SoonestResetEpoch) ||
				pick.OK() != c.Pick.Ok {
				t.Fatalf("PICK DRIFT %q:\n  py: acct=%q reason=%q wait=%v reset=%v ok=%v\n  go: acct=%q reason=%q wait=%v reset=%v ok=%v",
					c.Name, strOrEmpty(c.Pick.Account), c.Pick.Reason, c.Pick.WaitSeconds, c.Pick.SoonestResetEpoch, c.Pick.Ok,
					pick.Account, pick.Reason, pick.WaitSeconds, pick.SoonestResetEpoch, pick.OK())
			}

			// serving_pool.
			if pool := ServingPool(states); !sliceEq(pool, c.ServingPool) {
				t.Fatalf("POOL DRIFT %q: py=%v go=%v", c.Name, c.ServingPool, pool)
			}

			// allocate_seats per recorded n.
			for nStr, want := range c.Allocate {
				n := 0
				for _, ch := range nStr {
					n = n*10 + int(ch-'0')
				}
				if got := AllocateSeats(states, n); !sliceEq(got, want) {
					t.Fatalf("ALLOCATE DRIFT %q n=%d: py=%v go=%v", c.Name, n, want, got)
				}
			}

			// pick_account_spread per recorded seat index.
			for seatStr, want := range c.Spread {
				seat := 0
				for _, ch := range seatStr {
					seat = seat*10 + int(ch-'0')
				}
				ps := PickAccountSpread(states, seat, c.NearCapUtil, c.Now)
				if ps.Account != strOrEmpty(want.Account) || ps.Reason != want.Reason {
					t.Fatalf("SPREAD DRIFT %q seat=%d:\n  py: acct=%q reason=%q\n  go: acct=%q reason=%q",
						c.Name, seat, strOrEmpty(want.Account), want.Reason, ps.Account, ps.Reason)
				}
			}
		})
	}
}
