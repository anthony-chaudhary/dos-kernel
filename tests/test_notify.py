"""The notification seam (`dos.notify`) — pure, no network (docs/225).

Covers the kernel side only: the two pure adapters (projection data →
`Notification`), the severity escalation, the resolver (built-in `null` first,
unknown fails loud), and `send_safely`'s fail-soft contract. The Slack DRIVER is
tested separately against a fake client (`test_notify_slack.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dos import notify
from dos.notify import (
    Notification,
    NotifyAction,
    NotifyResult,
    NullNotifier,
    Severity,
    action_for_source,
    notification_for_decisions,
    notification_for_top,
    resolve_notifier,
    send_safely,
)


# ---------------------------------------------------------------------------
# Tiny duck-typed stand-ins — the adapters read only these fields, so a test
# does not need to spin up the heavier decisions/dispatch_top machinery. (Two
# tests below ALSO exercise the real types, to prove the duck-typing matches.)
# ---------------------------------------------------------------------------


@dataclass
class _Kind:
    value: str


@dataclass
class _Row:
    kind: _Kind
    lane: str = ""
    reason_token: str = ""
    reason_text: str = ""
    dup_count: int = 1
    proposed_command: str = ""


@dataclass
class _LaneState:
    lane: str
    chip: str
    holder: str = ""


@dataclass
class _Frame:
    workspace: str = "/ws"
    lanes: tuple = ()
    verdicts: tuple = ()


# ---------------------------------------------------------------------------
# Notification / NotifyResult value types.
# ---------------------------------------------------------------------------


def test_notification_to_dict_roundtrips_fields():
    n = Notification(
        severity=Severity.WARN,
        title="t",
        summary="body",
        fields=(("a", "1"), ("b", "2")),
        key="k",
        source="decisions",
    )
    d = n.to_dict()
    assert d["severity"] == "WARN"
    assert d["fields"] == [["a", "1"], ["b", "2"]]
    assert d["key"] == "k" and d["source"] == "decisions"


def test_notifyresult_to_dict():
    r = NotifyResult(delivered=True, detail="posted ts=1.2", ref="1.2")
    assert r.to_dict() == {"delivered": True, "detail": "posted ts=1.2", "ref": "1.2"}


# ---------------------------------------------------------------------------
# NotifyAction + action_for_source — the clickable follow-up (docs/387).
# ---------------------------------------------------------------------------


def test_notify_action_to_dict():
    a = NotifyAction(label="open it", command="dos top", url="https://x")
    assert a.to_dict() == {"label": "open it", "command": "dos top", "url": "https://x"}


def test_action_for_source_maps_the_three_projections():
    for source, verb in (("decisions", "decisions"), ("top", "top"), ("pulse", "pulse")):
        a = action_for_source(source)
        assert a is not None
        assert a.command == f"dos {verb}"
        assert a.url == ""  # no explicit deep link by default


def test_action_for_source_folds_workspace_into_command():
    a = action_for_source("decisions", root="/ws")
    assert a.command == "dos decisions --workspace /ws"


def test_action_for_source_quotes_a_root_with_spaces():
    a = action_for_source("top", root="/two words/ws")
    assert a.command == 'dos top --workspace "/two words/ws"'


def test_action_for_source_unknown_is_none():
    assert action_for_source("nope") is None
    assert action_for_source("") is None


def test_notification_to_dict_carries_action_or_none():
    a = NotifyAction(label="L", command="dos top")
    n = Notification(Severity.INFO, "t", "s", action=a)
    assert n.to_dict()["action"] == {"label": "L", "command": "dos top", "url": ""}
    # an action-less notification serializes a JSON-null, not a missing key
    assert Notification(Severity.INFO, "t", "s").to_dict()["action"] is None


# ---------------------------------------------------------------------------
# notification_for_decisions — severity escalation + TOP rows as fields.
# ---------------------------------------------------------------------------


def test_decisions_empty_is_info_and_clear_title():
    n = notification_for_decisions([], summary="(none)")
    assert n.severity is Severity.INFO
    assert "clear" in n.title
    assert n.fields == ()
    assert n.source == "decisions"


def test_decisions_any_row_is_warn():
    rows = [_Row(_Kind("ARBITER_REFUSE"), lane="src", reason_token="LANE_BUSY")]
    n = notification_for_decisions(rows, summary="…")
    assert n.severity is Severity.WARN
    assert n.title == "1 decision need you" or n.title == "1 decisions need you"


def test_decisions_liveness_escalates_to_urgent():
    rows = [
        _Row(_Kind("WEDGE"), lane="docs", reason_text="no pick"),
        _Row(_Kind("LIVENESS"), lane="src", reason_text="spinning",
             proposed_command="dos halt --handle H1"),
    ]
    n = notification_for_decisions(rows, summary="…")
    assert n.severity is Severity.URGENT


def test_decisions_top_limits_fields_and_carries_stop_command():
    rows = [
        _Row(_Kind("LIVENESS"), lane="src", reason_text="hung",
             proposed_command="dos halt --handle H1"),
        _Row(_Kind("ARBITER_REFUSE"), lane="docs", reason_token="LANE_BUSY"),
        _Row(_Kind("WEDGE"), lane="tests", reason_text="drain"),
    ]
    n = notification_for_decisions(rows, summary="…", top=2)
    assert len(n.fields) == 2  # capped at top
    # The LIVENESS row's paste-to-stop command is carried in its field value.
    assert "dos halt --handle H1" in n.fields[0][1]
    assert n.fields[0][0].startswith("LIVENESS @ src")


def test_decisions_dup_count_shown_in_field():
    rows = [_Row(_Kind("ARBITER_REFUSE"), lane="src", reason_token="BUSY", dup_count=4)]
    n = notification_for_decisions(rows, summary="…")
    assert "×4" in n.fields[0][1]


def test_decisions_adapter_attaches_the_decisions_action():
    n = notification_for_decisions([], summary="(none)", root="/ws")
    assert n.action is not None
    assert n.action.command == "dos decisions --workspace /ws"


# ---------------------------------------------------------------------------
# notification_for_top — severity from the worst lane + non-free lanes as fields.
# ---------------------------------------------------------------------------


def test_top_all_free_is_info():
    f = _Frame(lanes=(_LaneState("main", "⚪ FREE"), _LaneState("global", "⚪ FREE")))
    n = notification_for_top(f, summary="screen")
    assert n.severity is Severity.INFO
    assert n.fields == ()  # free lanes are not surfaced as fields
    assert "0 advancing" in n.title and "0 stalled" in n.title
    assert n.key == "dos-top:/ws"


def test_top_spinning_is_warn_and_lists_lane():
    f = _Frame(lanes=(
        _LaneState("src", "🟡 SPINNING", holder="host:42"),
        _LaneState("docs", "⚪ FREE"),
    ))
    n = notification_for_top(f, summary="screen")
    assert n.severity is Severity.WARN
    assert n.fields[0][0] == "src"
    assert "SPINNING" in n.fields[0][1] and "host:42" in n.fields[0][1]


def test_top_stalled_is_urgent():
    f = _Frame(lanes=(_LaneState("src", "🔴 STALLED"),))
    n = notification_for_top(f, summary="screen")
    assert n.severity is Severity.URGENT


def test_top_verdict_tally_appended():
    f = _Frame(
        lanes=(_LaneState("src", "🟢 ADVANCING"),),
        verdicts=("a", "b", "c"),
    )
    n = notification_for_top(f, summary="screen")
    assert ("recent verdicts", "3") in n.fields


def test_top_severity_reads_word_not_glyph():
    # A chip with a different glyph but the same STATE word must still classify by
    # the word (the dispatch_top contract the adapter relies on).
    f = _Frame(lanes=(_LaneState("src", "X STALLED"),))
    n = notification_for_top(f, summary="s")
    assert n.severity is Severity.URGENT


def test_top_adapter_self_sources_action_root_from_the_frame():
    # No explicit root → the top adapter folds the frame's workspace into the open.
    f = _Frame(workspace="/ws", lanes=(_LaneState("src", "🟢 ADVANCING"),))
    n = notification_for_top(f, summary="screen")
    assert n.action is not None
    assert n.action.command == "dos top --workspace /ws"


# ---------------------------------------------------------------------------
# Resolver — built-in null first, unknown fails loud, kwargs forwarded.
# ---------------------------------------------------------------------------


def test_resolve_null_builtin():
    nt = resolve_notifier("null")
    assert isinstance(nt, NullNotifier)
    assert nt.name == "null"


def test_null_notifier_delivers_nothing():
    nt = NullNotifier()
    r = nt.send(Notification(Severity.INFO, "t", "s"))
    assert r.delivered is False
    assert "null" in r.detail


def test_resolve_unknown_fails_loud_with_known_list():
    with pytest.raises(ValueError) as ei:
        resolve_notifier("nope")
    msg = str(ei.value)
    assert "unknown notifier 'nope'" in msg
    assert "null" in msg  # the known list is shown so a typo is diagnosable


def test_active_notifier_names_includes_null_first():
    names = notify.active_notifier_names()
    assert names and names[0] == "null"


# ---------------------------------------------------------------------------
# send_safely — the fail-soft floor: any raise becomes a non-delivered result.
# ---------------------------------------------------------------------------


class _RaisingNotifier:
    name = "boom"

    def send(self, note):
        raise RuntimeError("network down")


class _GoodNotifier:
    name = "ok"

    def __init__(self):
        self.sent = []

    def send(self, note):
        self.sent.append(note)
        return NotifyResult(delivered=True, detail="posted", ref="1.23")


class _BadShapeNotifier:
    name = "weird"

    def send(self, note):
        return "not a NotifyResult"


def test_send_safely_swallows_a_raise():
    r = send_safely(_RaisingNotifier(), Notification(Severity.URGENT, "t", "s"))
    assert r.delivered is False
    assert "error: network down" in r.detail


def test_send_safely_passes_through_a_good_result():
    nt = _GoodNotifier()
    note = Notification(Severity.INFO, "t", "s")
    r = send_safely(nt, note)
    assert r.delivered is True and r.ref == "1.23"
    assert nt.sent == [note]


def test_send_safely_rejects_a_non_result_shape():
    r = send_safely(_BadShapeNotifier(), Notification(Severity.INFO, "t", "s"))
    assert r.delivered is False
    assert "non-NotifyResult" in r.detail


# ---------------------------------------------------------------------------
# Real-type cross-checks — prove the duck-typing matches the actual projections.
# ---------------------------------------------------------------------------


def test_adapter_matches_real_decision_type():
    from dos.decisions import Decision, DecisionKind, ResolverKind

    d = Decision(
        kind=DecisionKind.LIVENESS,
        resolver_kind=ResolverKind.ORACLE,
        lane="src",
        reason_token="",
        reason_text="run spinning",
        run_id="RID-1",
        age_seconds=30,
        source_path="/j",
        proposed_command="dos halt --handle H9",
    )
    n = notification_for_decisions([d], summary="body")
    assert n.severity is Severity.URGENT
    assert n.fields[0][0] == "LIVENESS @ src"
    assert "dos halt --handle H9" in n.fields[0][1]


def test_adapter_matches_real_frame_type():
    from dos.dispatch_top import Frame, LaneState, CHIP_SPINNING, CHIP_FREE

    f = Frame(
        workspace="/ws",
        now_iso="2026-06-07T00:00:00+00:00",
        lanes=(
            LaneState(lane="src", chip=CHIP_SPINNING, holder="h:1"),
            LaneState(lane="global", chip=CHIP_FREE, is_exclusive=True),
        ),
    )
    n = notification_for_top(f, summary="screen")
    assert n.severity is Severity.WARN
    assert n.fields[0][0] == "src" and "SPINNING" in n.fields[0][1]
    assert n.key == "dos-top:/ws"
