package account

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type launchCase struct {
	Name      string                  `json:"name"`
	Now       float64                 `json:"now"`
	SeatIndex int                     `json:"seat_index"`
	Roster    string                  `json:"roster"`
	Files     []launchFile            `json:"files"`
	Probes    map[string]*launchProbe `json:"probes"`
	Environ   map[string]string       `json:"environ"`
	Expected  launchExpected          `json:"expected"`
}

type launchFile struct {
	Account string `json:"account"`
	Name    string `json:"name"`
	Content string `json:"content"`
}

type launchProbe struct {
	Allowed      bool    `json:"allowed"`
	Utilization  float64 `json:"utilization"`
	ResetAtEpoch *int64  `json:"reset_at_epoch"`
	Status       string  `json:"status"`
}

type launchExpected struct {
	Account           *string           `json:"account"`
	Reason            string            `json:"reason"`
	OK                bool              `json:"ok"`
	WaitSeconds       *int              `json:"wait_seconds"`
	SoonestResetEpoch *int64            `json:"soonest_reset_epoch"`
	Env               map[string]string `json:"env"`
}

func loadLaunchCorpus(t *testing.T) []launchCase {
	t.Helper()
	path := filepath.Join("parity", "corpus_launch.jsonl")
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open corpus %s: %v (run `python go/internal/account/parity/gen_launch_corpus.py > %s`)", path, err, path)
	}
	defer f.Close()
	var cases []launchCase
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		if len(sc.Bytes()) == 0 {
			continue
		}
		var c launchCase
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatalf("launch corpus unmarshal: %v", err)
		}
		cases = append(cases, c)
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("launch corpus scan: %v", err)
	}
	if len(cases) == 0 {
		t.Fatal("launch corpus is empty")
	}
	return cases
}

func probeMapFromLaunchCorpus(c launchCase) map[string]*Probe {
	out := make(map[string]*Probe, len(c.Probes))
	for name, p := range c.Probes {
		if p == nil {
			continue
		}
		out[name] = &Probe{
			Allowed:      p.Allowed,
			Utilization:  p.Utilization,
			ResetAtEpoch: p.ResetAtEpoch,
			Status:       p.Status,
		}
	}
	return out
}

func materializeLaunchCase(t *testing.T, c launchCase) (string, string) {
	t.Helper()
	root := filepath.ToSlash(t.TempDir())
	rosterText := strings.ReplaceAll(c.Roster, "$ROOT", root)
	roster := filepath.Join(root, "accounts.yaml")
	if err := os.WriteFile(roster, []byte(rosterText), 0o644); err != nil {
		t.Fatalf("write roster: %v", err)
	}
	for _, f := range c.Files {
		dir := filepath.Join(root, f.Account)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", dir, err)
		}
		path := filepath.Join(dir, f.Name)
		if err := os.WriteFile(path, []byte(f.Content), 0o644); err != nil {
			t.Fatalf("write %s: %v", path, err)
		}
	}
	return root, roster
}

func normalizeLaunchEnv(env map[string]string, root string) map[string]string {
	if env == nil {
		return nil
	}
	out := make(map[string]string, len(env))
	for k, v := range env {
		out[k] = strings.ReplaceAll(filepath.ToSlash(v), filepath.ToSlash(root), "$ROOT")
	}
	return out
}

func mapStringEq(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, av := range a {
		if b[k] != av {
			return false
		}
	}
	return true
}

// TestLaunchParityCorpus replays the Python-generated launch resolver corpus:
// roster loading + disk auth fold + account pick + env_for.
func TestLaunchParityCorpus(t *testing.T) {
	for _, c := range loadLaunchCorpus(t) {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			root, roster := materializeLaunchCase(t, c)
			got, err := ResolveLaunchEnv(LaunchOptions{
				RosterPath: roster,
				SeatIndex:  c.SeatIndex,
				Now:        c.Now,
				Environ:    c.Environ,
				Probes:     probeMapFromLaunchCorpus(c),
			})
			if err != nil {
				t.Fatalf("ResolveLaunchEnv: %v", err)
			}
			wantAccount := strOrEmpty(c.Expected.Account)
			if got.Account != wantAccount || got.Reason != c.Expected.Reason ||
				got.OK() != c.Expected.OK ||
				!intPtrEq(got.WaitSeconds, c.Expected.WaitSeconds) ||
				!int64PtrEq(got.SoonestResetEpoch, c.Expected.SoonestResetEpoch) {
				t.Fatalf("LAUNCH DRIFT %q:\n  py: acct=%q ok=%v reason=%q wait=%v reset=%v\n  go: acct=%q ok=%v reason=%q wait=%v reset=%v",
					c.Name, wantAccount, c.Expected.OK, c.Expected.Reason,
					c.Expected.WaitSeconds, c.Expected.SoonestResetEpoch,
					got.Account, got.OK(), got.Reason, got.WaitSeconds, got.SoonestResetEpoch)
			}
			gotEnv := normalizeLaunchEnv(got.Env, root)
			if !mapStringEq(gotEnv, c.Expected.Env) {
				t.Fatalf("ENV DRIFT %q:\n  py: %#v\n  go: %#v", c.Name, c.Expected.Env, gotEnv)
			}
		})
	}
}
