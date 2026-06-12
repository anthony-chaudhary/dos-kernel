"""WKA — the work-kind account + the pure classifier (docs/310).

`work_account.classify_work` is the COMPOSITION sibling of the loop-economics
family: where `productivity` reads a trend and `efficiency` a ratio, WKA reads
a typed account of one iteration's work BY KIND — each counter env-witnessed at
the caller's boundary — and names the dominant kind. It is the fix for the
one-axis stats forcing ("did a pick ship?") that graded 67% of dispatch-loop
iterations as "drained" even when they landed commits, groomed plans, or
caught a false claim.

These tests pin:

  1. the precedence ladder (SHIPPED > CAUGHT > ADVANCED > GROOMED > SURFACED >
     IDLE) on frozen accounts;
  2. the non-forgeability rail — claims alone classify IDLE; only the oracle's
     verified count reaches SHIPPED;
  3. the over-claim gap (claimed − verified − caught), visible in every reason;
  4. the headline renderer agreeing with the classifier (one shared phrase
     source);
  5. the merge fold (loop-level totals; identity = the zero account);
  6. input validation (counts are non-negative ints).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dos.work_account import (
    WorkAccount,
    WorkKind,
    account_lead_token,
    classify_work,
    merge,
)


# ---------------------------------------------------------------------------
# 1. The precedence ladder, on frozen accounts.
# ---------------------------------------------------------------------------


def test_verified_ship_is_shipped():
    v = classify_work(WorkAccount(verified_ships=2, claimed_ships=2))
    assert v.kind is WorkKind.SHIPPED
    assert "2 picks shipped" in v.reason


def test_ship_outranks_everything():
    """One verified ship heads the verdict whatever else the account holds."""
    v = classify_work(
        WorkAccount(
            verified_ships=1,
            catches=3,
            advance_commits=9,
            grooms=4,
            unblocks=2,
            surfaced=5,
        )
    )
    assert v.kind is WorkKind.SHIPPED


def test_catch_outranks_advance():
    """A refused lie is operator-actionable; commits are routine."""
    v = classify_work(WorkAccount(catches=1, advance_commits=7))
    assert v.kind is WorkKind.CAUGHT
    assert "1 false claim caught" in v.reason


def test_advance_only_is_advanced():
    """Partial progress — commits with no phase closed — no longer reads zero."""
    v = classify_work(WorkAccount(advance_commits=4))
    assert v.kind is WorkKind.ADVANCED
    assert "4 commits advanced" in v.reason


def test_grooms_only_is_groomed():
    v = classify_work(WorkAccount(grooms=3))
    assert v.kind is WorkKind.GROOMED


def test_unblocks_alone_also_groomed():
    """An unblock is bookkeeping that moved durable state — the GROOMED rung."""
    v = classify_work(WorkAccount(unblocks=1))
    assert v.kind is WorkKind.GROOMED
    assert "1 unblock" in v.reason


def test_surfaced_only_is_surfaced():
    v = classify_work(WorkAccount(surfaced=2))
    assert v.kind is WorkKind.SURFACED
    assert "2 decisions surfaced" in v.reason


def test_zero_account_is_idle():
    v = classify_work(WorkAccount())
    assert v.kind is WorkKind.IDLE
    assert "no witnessed work" in v.reason


# ---------------------------------------------------------------------------
# 2. The non-forgeability rail — narration cannot climb the ladder.
# ---------------------------------------------------------------------------


def test_claims_alone_are_idle():
    """The strictness this leaf adds over today's picks_shipped headline: a
    claimed ship with no oracle answer is NOT shipped work — it is nothing,
    plus a visible unadjudicated over-claim."""
    v = classify_work(WorkAccount(claimed_ships=3))
    assert v.kind is WorkKind.IDLE
    assert "3 claimed ships unadjudicated" in v.reason


def test_claims_do_not_lift_a_groomed_iteration():
    v = classify_work(WorkAccount(claimed_ships=2, grooms=1))
    assert v.kind is WorkKind.GROOMED


# ---------------------------------------------------------------------------
# 3. The over-claim gap — visible, never believed.
# ---------------------------------------------------------------------------


def test_overclaim_is_claimed_minus_verified_minus_caught():
    acc = WorkAccount(verified_ships=1, claimed_ships=4, catches=1)
    assert acc.overclaim == 2
    v = classify_work(acc)
    assert v.kind is WorkKind.SHIPPED
    assert "2 claimed ships unadjudicated" in v.reason


def test_quiet_completion_is_not_an_overclaim():
    """Verified without a claim (a quiet completion) must not go negative."""
    acc = WorkAccount(verified_ships=2, claimed_ships=0)
    assert acc.overclaim == 0
    assert "unadjudicated" not in classify_work(acc).reason


def test_fully_adjudicated_claims_carry_no_tail():
    acc = WorkAccount(verified_ships=1, claimed_ships=2, catches=1)
    assert acc.overclaim == 0
    assert "unadjudicated" not in classify_work(acc).reason


# ---------------------------------------------------------------------------
# 4. The headline renderer — one phrase source with the classifier.
# ---------------------------------------------------------------------------


def test_lead_token_composes_in_precedence_order():
    acc = WorkAccount(verified_ships=1, catches=1, advance_commits=4)
    assert (
        account_lead_token(acc)
        == "1 pick shipped · 1 false claim caught · 4 commits advanced"
    )


def test_lead_token_singular_plural():
    assert account_lead_token(WorkAccount(verified_ships=1)) == "1 pick shipped"
    assert account_lead_token(WorkAccount(verified_ships=2)) == "2 picks shipped"


def test_lead_token_idle():
    """IDLE renders `idle` — the backlog word (drained) is the gate's axis,
    not this one's; the renderer must not reach for it."""
    assert account_lead_token(WorkAccount()) == "idle"
    assert account_lead_token(WorkAccount(claimed_ships=2)) == "idle"


def test_headline_agrees_with_reason():
    """The renderer and the classifier read the same phrase source — a
    non-IDLE reason leads with the headline verbatim."""
    acc = WorkAccount(catches=2, grooms=1, surfaced=1)
    v = classify_work(acc)
    assert v.reason.startswith(account_lead_token(acc))


# ---------------------------------------------------------------------------
# 5. The merge fold — loop-level totals.
# ---------------------------------------------------------------------------


def test_merge_sums_counterwise():
    total = merge(
        WorkAccount(verified_ships=1, advance_commits=2),
        WorkAccount(grooms=3, advance_commits=1),
        WorkAccount(catches=1, surfaced=2, unblocks=1, claimed_ships=2),
    )
    assert total == WorkAccount(
        verified_ships=1,
        claimed_ships=2,
        catches=1,
        advance_commits=3,
        grooms=3,
        unblocks=1,
        surfaced=2,
    )


def test_merge_identity_is_idle():
    """A loop that never ran an iteration closes honestly IDLE."""
    assert classify_work(merge()).kind is WorkKind.IDLE


# ---------------------------------------------------------------------------
# 6. Validation — counters are non-negative integer counts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verified_ships": -1},
        {"advance_commits": -3},
        {"grooms": 1.5},
        {"catches": "2"},
        {"surfaced": True},  # bools are not counts
    ],
)
def test_bad_counter_rejected(kwargs):
    with pytest.raises(ValueError):
        WorkAccount(**kwargs)


def test_to_dict_round_trip_shape():
    acc = WorkAccount(verified_ships=1, claimed_ships=3, catches=1)
    d = classify_work(acc).to_dict()
    assert d["verdict"] == "SHIPPED"
    assert d["account"]["overclaim"] == 1
    assert d["account"]["verified_ships"] == 1


# ---------------------------------------------------------------------------
# 7. The CLI verb — the verdict IS the exit code (no plan, no git needed).
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_cli_shipped_exit_zero(tmp_path: Path):
    r = _run_cli("work-account", "--verified-ships", "1",
                 "--advance-commits", "4", cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.startswith("SHIPPED")
    # The composed headline crosses the pipe under the platform's console
    # encoding, so pin the two phrases, not the `·` separator byte (the exact
    # separator is pinned in-process by test_lead_token_composes_in_precedence_order).
    assert "1 pick shipped" in r.stdout
    assert "4 commits advanced" in r.stdout


def test_cli_caught_exit_three(tmp_path: Path):
    r = _run_cli("work-account", "--catches", "1", cwd=tmp_path)
    assert r.returncode == 3
    assert r.stdout.startswith("CAUGHT")


def test_cli_idle_exit_four(tmp_path: Path):
    r = _run_cli("work-account", cwd=tmp_path)
    assert r.returncode == 4
    assert r.stdout.startswith("IDLE")


def test_cli_claims_alone_idle(tmp_path: Path):
    """The non-forgeability rail through the real boundary: a self-reported
    ship with no oracle answer exits 4, with the over-claim named."""
    r = _run_cli("work-account", "--claimed-ships", "2", cwd=tmp_path)
    assert r.returncode == 4
    assert "2 claimed ships unadjudicated" in r.stdout


def test_cli_negative_count_is_contract_error(tmp_path: Path):
    r = _run_cli("work-account", "--grooms", "-1", cwd=tmp_path)
    assert r.returncode == 2
    assert "non-negative" in r.stderr


def test_cli_json_shape(tmp_path: Path):
    r = _run_cli("work-account", "--verified-ships", "1",
                 "--claimed-ships", "3", "--json", cwd=tmp_path)
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "SHIPPED"
    assert payload["account"]["overclaim"] == 2


def test_cli_exit_codes_contract_row(tmp_path: Path):
    """`dos exit-codes work-account` publishes the verdict→code table."""
    r = _run_cli("exit-codes", "work-account", "--json", cwd=tmp_path)
    assert r.returncode == 0
    row = json.loads(r.stdout)["work-account"]
    assert row["SHIPPED"] == 0
    assert row["CAUGHT"] == 3
    assert row["IDLE"] == 4
