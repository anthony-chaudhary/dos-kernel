// Package account ports the account-switcher's PURE ranking core to Go — the
// agent-blind seat picker (docs/386 §1) under the docs/385 strongly-typed mandate.
//
// docs/386 §6 queued exactly this: the switcher's pure ranking core
// (`pick_account` / `serving_pool` / `allocate_seats` — the preference order, the
// Hamilton apportionment, the headroom weighting, the serving/walled
// classification) ported to Go behind the parity harness, on the same
// PORT → SOAK → FLIP ratchet the hook deciders rode (docs/100/124/125). The auth
// glue (env/token/enroll), the roster I/O, and the disk/probe reads stay Python —
// genuine interconnects (docs/385 §5 seam #4). So the port is the DECISION only:
// it takes already-FOLDED per-account facts (the disk + live-probe reads the Python
// `account_state` does at the I/O boundary, supplied here as data) and reproduces
// the verdict the Python switcher returns, byte-for-byte on every detail string.
//
// The boundary is the same one docs/124 sharpened for the hook path: Go is a pure
// decider over the decision-bearing fields; the only formatted prose is the
// utilization percentage (`%.0f%%`, the `:.0%` twin that agrees cross-engine —
// docs/124 §1.1). Static, stdlib-only, no I/O — every fact is injected.
package account

import (
	"fmt"
	"sort"
)

// Kernel ranking constants — twins of the Python switcher's module constants.
const (
	// DefaultNearCapUtil — an account at/over this utilization is "near cap":
	// avoided when a lower-utilization serving account exists. (`_DEFAULT_NEAR_CAP_UTIL`)
	DefaultNearCapUtil = 0.9
	// noHintWaitSeconds — the all-walled-no-reset backoff floor. (`_NO_HINT_WAIT_SECONDS`)
	noHintWaitSeconds = 1800
	// minHeadroom — the sliver of weight a saturated serving window still gets so the
	// allocator never hands it literally zero seats while it is the only thing serving.
	minHeadroom = 0.05
)

// Account-state kinds — the per-account fold the picker ranks over. Twins of the
// `ACCT_*` constants in account_switcher.
const (
	KindNeedsEnroll = "needs_enroll"
	KindDisabled    = "disabled"
	KindServing     = "serving"
	KindNearCap     = "near_cap"
	KindWalled      = "walled"
)

// Probe is the FOLDED live rate-limit signal for one account (the host's injected
// `ProbeLike`). A nil *Probe is the fail-open "no live signal" case — assume serving.
type Probe struct {
	Allowed      bool
	Utilization  float64
	ResetAtEpoch *int64 // nil ⇒ no reset hint
	Status       string
}

// Facts is the disk-free ground truth the Python `account_state` reads from disk,
// supplied here as data so the ranking is pure. CredsPresent/TokenExpired/HasToken
// are the folded results of `_token_expired` + `read_account_token`; Probe is the
// folded live signal.
type Facts struct {
	Name         string
	Enabled      bool
	CredsPresent bool   // .credentials.json present WITH a usable access token
	TokenExpired bool   // that access token's expiry is in the past
	HasToken     bool   // a stored setup-token (.oauth-token) is present
	Probe        *Probe // nil ⇒ fail-open (no live probe)
}

// State is the folded verdict for one account — the twin of `AccountState`.
// Utilization/ResetAtEpoch are nil when the Python field is None.
type State struct {
	Name         string
	Kind         string
	CredsPresent bool
	TokenExpired bool
	ProbeStatus  string
	Utilization  *float64
	ResetAtEpoch *int64
	Detail       string
}

// Pickable reports whether the picker may route work to this account right now.
func (s State) Pickable() bool { return s.Kind == KindServing || s.Kind == KindNearCap }

// Pick is the switcher's decision — the twin of `Pick`. Account is "" / HasAccount
// false when no account is chosen; WaitSeconds/SoonestResetEpoch are non-nil only on
// the all-walled path.
type Pick struct {
	Account           string
	HasAccount        bool
	Reason            string
	WaitSeconds       *int
	SoonestResetEpoch *int64
}

// OK mirrors `Pick.ok`: an account was chosen AND no wait is required.
func (p Pick) OK() bool { return p.HasAccount && p.WaitSeconds == nil }

// pct renders a utilization as the `:.0%` twin (`%.0f%%` over value*100). docs/124
// §1.1: the only ratio in this surface, and it agrees cross-engine.
func pct(util float64) string { return fmt.Sprintf("%.0f%%", util*100) }

// utilOr returns the state's utilization, or dflt when it is nil (the Python
// `x.utilization if x.utilization is not None else <dflt>` pattern).
func utilOr(s State, dflt float64) float64 {
	if s.Utilization != nil {
		return *s.Utilization
	}
	return dflt
}

// Classify folds one account's facts into a State — the pure twin of `account_state`
// (the disk/probe I/O is already done; this is the classification only). nearCapUtil
// is the policy threshold above which a serving account is "near cap".
func Classify(f Facts, nearCapUtil float64) State {
	if !f.Enabled {
		return State{Name: f.Name, Kind: KindDisabled, Detail: "disabled in roster"}
	}
	var via string
	switch {
	case f.HasToken:
		via = "setup-token"
	case f.CredsPresent && !f.TokenExpired:
		via = "login"
	case f.CredsPresent && f.TokenExpired:
		return State{
			Name: f.Name, Kind: KindNeedsEnroll, CredsPresent: true, TokenExpired: true,
			Detail: "oauth token expired — re-enroll/refresh",
		}
	default:
		return State{
			Name: f.Name, Kind: KindNeedsEnroll,
			Detail: "no token / .credentials.json — run enroll",
		}
	}
	// Enrolled — consult the (injected) live probe. nil ⇒ fail-open serving.
	if f.Probe == nil {
		return State{
			Name: f.Name, Kind: KindServing, CredsPresent: true,
			Detail: fmt.Sprintf("enrolled via %s (no live probe — fail-open)", via),
		}
	}
	util := f.Probe.Utilization
	if !f.Probe.Allowed {
		u := util
		return State{
			Name: f.Name, Kind: KindWalled, CredsPresent: true, ProbeStatus: f.Probe.Status,
			Utilization: &u, ResetAtEpoch: f.Probe.ResetAtEpoch,
			Detail: fmt.Sprintf("live-probe rejected (util %s)", pct(util)),
		}
	}
	kind := KindServing
	if util >= nearCapUtil {
		kind = KindNearCap
	}
	u := util
	return State{
		Name: f.Name, Kind: kind, CredsPresent: true, ProbeStatus: f.Probe.Status,
		Utilization: &u, ResetAtEpoch: f.Probe.ResetAtEpoch,
		Detail: fmt.Sprintf("serving (util %s)", pct(util)),
	}
}

// ClassifyAll folds a roster of facts into states, in roster order.
func ClassifyAll(facts []Facts, nearCapUtil float64) []State {
	out := make([]State, len(facts))
	for i, f := range facts {
		out[i] = Classify(f, nearCapUtil)
	}
	return out
}

// hasReset mirrors Python's `if s.reset_at_epoch` truthiness: nil OR 0 is "no reset".
func hasReset(s State) bool { return s.ResetAtEpoch != nil && *s.ResetAtEpoch != 0 }

// defaultWaitForWalled is the pure fallback wait — the twin of
// `_default_wait_for_walled`. A nil/0 reset ⇒ the no-hint floor; an elapsed reset ⇒ 0.
func defaultWaitForWalled(reset *int64, now float64) int {
	if reset == nil || *reset == 0 {
		return noHintWaitSeconds
	}
	delta := int(float64(*reset) - now)
	if delta <= 0 {
		return 0
	}
	return delta
}

// PickAccount chooses the next account to route work to — the twin of `pick_account`.
// Order: a SERVING account (roster order) › a NEAR_CAP account (lowest util) › the
// soonest-resetting WALLED account (with a wait, not ok) › none (the enroll gap).
func PickAccount(states []State, nearCapUtil float64, now float64) Pick {
	var serving, near, walled []State
	nNeedsEnroll := 0
	for _, s := range states {
		switch s.Kind {
		case KindServing:
			serving = append(serving, s)
		case KindNearCap:
			near = append(near, s)
		case KindWalled:
			walled = append(walled, s)
		case KindNeedsEnroll:
			nNeedsEnroll++
		}
	}

	if len(serving) > 0 {
		s := serving[0] // roster order is the operator's preference
		return Pick{Account: s.Name, HasAccount: true, Reason: "serving: " + s.Detail}
	}

	if len(near) > 0 {
		best := near[0]
		for _, s := range near[1:] { // first minimum wins (min() semantics)
			if utilOr(s, 1.0) < utilOr(best, 1.0) {
				best = s
			}
		}
		return Pick{
			Account: best.Name, HasAccount: true,
			Reason: fmt.Sprintf("near-cap fallback (no account below %s): %s",
				pct(nearCapUtil), best.Detail),
		}
	}

	if len(walled) > 0 {
		var chosen State
		var reset *int64
		var withReset []State
		for _, s := range walled {
			if hasReset(s) {
				withReset = append(withReset, s)
			}
		}
		if len(withReset) > 0 {
			chosen = withReset[0]
			for _, s := range withReset[1:] { // soonest reset, first minimum wins
				if *s.ResetAtEpoch < *chosen.ResetAtEpoch {
					chosen = s
				}
			}
			reset = chosen.ResetAtEpoch
		} else {
			chosen = walled[0]
			reset = nil
		}
		wait := defaultWaitForWalled(reset, now)
		return Pick{
			Account: chosen.Name, HasAccount: true,
			Reason:            fmt.Sprintf("all enrolled accounts walled — soonest reset is %s", chosen.Name),
			WaitSeconds:       &wait,
			SoonestResetEpoch: reset,
		}
	}

	reason := "no accounts in roster"
	if nNeedsEnroll > 0 {
		reason = fmt.Sprintf(
			"no enrolled account available (%d need enrollment) — enroll an account "+
				"once (`claude setup-token`, then store the token)", nNeedsEnroll)
	}
	return Pick{Reason: reason}
}

// servingPoolStates returns the pickable states in spread order — clean-serving head
// in roster order, near-cap tail lowest-util first. Twin of `_serving_pool_states`.
func servingPoolStates(states []State) []State {
	var serving, near []State
	for _, s := range states {
		switch s.Kind {
		case KindServing:
			serving = append(serving, s)
		case KindNearCap:
			near = append(near, s)
		}
	}
	sort.SliceStable(near, func(i, j int) bool {
		return utilOr(near[i], 1.0) < utilOr(near[j], 1.0)
	})
	out := make([]State, 0, len(serving)+len(near))
	out = append(out, serving...)
	out = append(out, near...)
	return out
}

// ServingPool is the serving accounts in spread order — the twin of `serving_pool`.
func ServingPool(states []State) []string {
	pool := servingPoolStates(states)
	out := make([]string, len(pool))
	for i, s := range pool {
		out[i] = s.Name
	}
	return out
}

// AllocateSeats assigns n seats across the serving pool weighted by each window's
// remaining headroom — the twin of `allocate_seats` (largest-remainder / Hamilton
// apportionment, ties by pool order; seats interleaved across windows by descending
// headroom). Returns the per-seat account names; nil for n<=0 or an empty pool.
func AllocateSeats(states []State, n int) []string {
	if n <= 0 {
		return nil
	}
	pool := servingPoolStates(states)
	if len(pool) == 0 {
		return nil
	}
	weights := make([]float64, len(pool))
	for i, s := range pool {
		w := 1.0 - utilOr(s, 0.0)
		if w < minHeadroom {
			w = minHeadroom
		}
		weights[i] = w
	}
	totalW := 0.0
	for _, w := range weights {
		totalW += w
	}
	if totalW <= 0 { // minHeadroom keeps this positive; mirror the Python guard anyway
		totalW = float64(len(pool))
		for i := range weights {
			weights[i] = 1.0
		}
	}
	exact := make([]float64, len(pool))
	floors := make([]int, len(pool))
	assigned := 0
	for i, w := range weights {
		exact[i] = float64(n) * w / totalW
		floors[i] = int(exact[i]) // truncation toward zero; exact >= 0
		assigned += floors[i]
	}
	remainder := n - assigned
	// Hand the leftover seats to the largest fractional parts (ties: weight, then
	// pool order). reverse=True over (frac, weight), stable ⇒ equal keys keep order.
	order := make([]int, len(pool))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(a, b int) bool {
		ia, ib := order[a], order[b]
		fa, fb := exact[ia]-float64(floors[ia]), exact[ib]-float64(floors[ib])
		if fa != fb {
			return fa > fb
		}
		return weights[ia] > weights[ib]
	})
	seats := make([]int, len(pool))
	copy(seats, floors)
	for k := 0; k < remainder; k++ {
		seats[order[k%len(order)]]++
	}
	// Interleave per-seat across windows by descending headroom (ties: pool order).
	byHeadroom := make([]int, len(pool))
	for i := range byHeadroom {
		byHeadroom[i] = i
	}
	sort.SliceStable(byHeadroom, func(a, b int) bool {
		return weights[byHeadroom[a]] > weights[byHeadroom[b]]
	})
	remaining := make([]int, len(pool))
	copy(remaining, seats)
	anyRemaining := func() bool {
		for _, r := range remaining {
			if r > 0 {
				return true
			}
		}
		return false
	}
	var out []string
	for len(out) < n && anyRemaining() {
		for _, i := range byHeadroom {
			if remaining[i] > 0 {
				out = append(out, pool[i].Name)
				remaining[i]--
				if len(out) >= n {
					break
				}
			}
		}
	}
	return out
}

// PickAccountSpread is `pick_account` spread across the serving pool by seatIndex —
// the twin of `pick_account_spread`. With ≤1 clean-serving window it defers to
// PickAccount (byte-identical walled/near-cap/empty verdicts); otherwise it maps
// seatIndex onto the headroom-ordered serving windows (ties: roster order).
func PickAccountSpread(states []State, seatIndex int, nearCapUtil float64, now float64) Pick {
	pool := servingPoolStates(states)
	var serving []State
	for _, s := range pool {
		if s.Kind == KindServing {
			serving = append(serving, s)
		}
	}
	if len(serving) <= 1 {
		return PickAccount(states, nearCapUtil, now)
	}
	headroom := func(s State) float64 {
		h := 1.0 - utilOr(s, 0.0)
		if h < minHeadroom {
			h = minHeadroom
		}
		return h
	}
	order := make([]int, len(serving))
	for i := range order {
		order[i] = i
	}
	// (headroom, -i) reverse=True ⇒ headroom DESC, then index ASC (roster order).
	sort.SliceStable(order, func(a, b int) bool {
		ia, ib := order[a], order[b]
		ha, hb := headroom(serving[ia]), headroom(serving[ib])
		if ha != hb {
			return ha > hb
		}
		return ia < ib
	})
	chosen := serving[order[seatIndex%len(order)]]
	return Pick{
		Account: chosen.Name, HasAccount: true,
		Reason: fmt.Sprintf("spread seat %d of %d serving: %s",
			seatIndex, len(order), chosen.Detail),
	}
}
