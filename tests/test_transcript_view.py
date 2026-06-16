"""Tests for `dos transcript` — the agent-output-stream browser (transcript_view).

A read-only projection: it parses a transcript JSONL into typed StreamRecords and
renders them with modular show/hide. These tests pin the verified record shapes,
the filter algebra, the no-crash floor, and the JSON/text renderers. Fixtures use
neutral synthetic roots only (no dev-machine path, the privacy rule).
"""

from __future__ import annotations

import json

from dos import transcript_view as tv


# ---------------------------------------------------------------------------
# Fixtures — synthetic transcript lines mirroring the verified real schema.
# ---------------------------------------------------------------------------
def _line(obj: dict) -> str:
    return json.dumps(obj)


def _assistant_text(text: str, model: str = "claude-opus-4-8") -> str:
    return _line({
        "type": "assistant",
        "timestamp": "2026-06-16T00:00:00Z",
        "message": {"role": "assistant", "model": model,
                    "content": [{"type": "text", "text": text}]},
    })


def _tool_call(name: str, inp: dict, tid: str = "tu_1", caller: dict | None = None) -> str:
    block = {"type": "tool_use", "id": tid, "name": name, "input": inp}
    if caller is not None:
        block["caller"] = caller
    return _line({
        "type": "assistant",
        "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [block]},
    })


def _tool_result(tid: str, content, is_error: bool = False) -> str:
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error:
        block["is_error"] = True
    return _line({"type": "user", "message": {"role": "user", "content": [block]}})


def _user(text: str) -> str:
    return _line({"type": "user", "message": {"role": "user", "content": text}})


def _synthetic(text: str = "API Error: 429 Rate limited") -> str:
    return _line({
        "type": "assistant",
        "isApiErrorMessage": True,
        "apiErrorStatus": 429,
        "message": {"role": "assistant", "model": "<synthetic>",
                    "stop_reason": "stop_sequence",
                    "content": [{"type": "text", "text": text}]},
    })


# ---------------------------------------------------------------------------
# parse_records — the shapes.
# ---------------------------------------------------------------------------
def test_parse_basic_stream_shapes():
    lines = [
        _user("do the thing"),
        _assistant_text("on it"),
        _tool_call("Bash", {"command": "ls"}, "tu_1"),
        _tool_result("tu_1", [{"type": "text", "text": "file.py"}]),
        _assistant_text("done"),
    ]
    recs = tv.parse_records(lines)
    kinds = [r.kind for r in recs]
    assert kinds == [
        tv.KIND_USER, tv.KIND_TEXT, tv.KIND_TOOL_CALL, tv.KIND_TOOL_RESULT, tv.KIND_TEXT,
    ]
    assert [r.index for r in recs] == [0, 1, 2, 3, 4]
    call = recs[2]
    assert call.tool_name == "Bash"
    assert call.tool_input == {"command": "ls"}
    assert call.tool_use_id == "tu_1"
    res = recs[3]
    assert res.text == "file.py"
    assert res.tool_use_id == "tu_1"
    assert res.is_error is False


def test_parse_multi_block_assistant_turn():
    line = _line({
        "type": "assistant",
        "message": {"role": "assistant", "model": "m",
                    "content": [
                        {"type": "text", "text": "let me run it"},
                        {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "x"}},
                    ]},
    })
    recs = tv.parse_records([line])
    assert [r.kind for r in recs] == [tv.KIND_TEXT, tv.KIND_TOOL_CALL]


def test_parse_bare_string_content():
    recs = tv.parse_records([_user("hello"), _assistant_text("hi")])
    assert recs[0].kind == tv.KIND_USER and recs[0].text == "hello"
    assert recs[1].kind == tv.KIND_TEXT and recs[1].text == "hi"


def test_parse_tool_result_bare_string_and_error():
    recs = tv.parse_records([
        _tool_result("a", "plain output"),
        _tool_result("b", [{"type": "text", "text": "boom", "is_error": True}]),
    ])
    assert recs[0].text == "plain output" and recs[0].is_error is False
    assert recs[1].is_error is True


def test_parse_tool_result_bare_string_error_from_block_flag():
    # is_error must survive when content is a BARE STRING but the block declares it.
    recs = tv.parse_records([_tool_result("a", "kaboom", is_error=True)])
    assert recs[0].text == "kaboom"
    assert recs[0].is_error is True


def test_parse_thinking_block():
    line = _line({"type": "assistant", "message": {"role": "assistant", "model": "m",
                  "content": [{"type": "thinking", "thinking": "hmm"}]}})
    recs = tv.parse_records([line])
    assert recs[0].kind == tv.KIND_THINKING and recs[0].text == "hmm"


def test_parse_redacted_thinking_block():
    line = _line({"type": "assistant", "message": {"role": "assistant", "model": "m",
                  "content": [{"type": "redacted_thinking", "data": "xx"}]}})
    recs = tv.parse_records([line])
    assert recs[0].kind == tv.KIND_THINKING
    assert "redacted" in recs[0].text.lower()


def test_parse_caller_surfaced():
    recs = tv.parse_records([_tool_call("Bash", {"command": "x"}, caller={"type": "subagent"})])
    assert recs[0].caller == "subagent"


def test_parse_synthetic_death():
    recs = tv.parse_records([_assistant_text("real"), _synthetic("API Error: 429 Rate limited")])
    assert recs[0].kind == tv.KIND_TEXT
    syn = recs[1]
    assert syn.kind == tv.KIND_SYNTHETIC
    assert syn.is_error is True
    assert syn.model == "<synthetic>"
    assert "429" in syn.text


def test_parse_synthetic_collapses_to_single_record():
    # A model=="<synthetic>" record with extra blocks still collapses to ONE record.
    line = _line({"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>",
                  "content": [{"type": "text", "text": "rate limited"}]}})
    recs = tv.parse_records([line])
    assert len(recs) == 1 and recs[0].kind == tv.KIND_SYNTHETIC


def test_parse_synthetic_by_isApiErrorMessage_marks_blocks():
    # isApiErrorMessage (model present) marks its text block synthetic, not collapsed wholesale.
    line = _line({"type": "assistant", "isApiErrorMessage": True,
                  "message": {"role": "assistant", "model": "claude-x",
                              "content": [{"type": "text", "text": "limit"}]}})
    recs = tv.parse_records([line])
    assert recs[0].kind == tv.KIND_SYNTHETIC


def test_parse_meta_record_for_non_message_types():
    recs = tv.parse_records([
        _line({"type": "attachment", "attachment": {}}),
        _line({"type": "ai-title", "timestamp": "t"}),
    ])
    assert [r.kind for r in recs] == [tv.KIND_META, tv.KIND_META]
    assert recs[0].text == "attachment"
    assert recs[1].text == "ai-title"


def test_parse_unmodelled_block_becomes_countable_meta():
    # An unknown block type is NOT silently dropped — it becomes a meta record.
    line = _line({"type": "assistant", "message": {"role": "assistant", "model": "m",
                  "content": [{"type": "server_tool_use", "id": "s"}]}})
    recs = tv.parse_records([line])
    assert len(recs) == 1
    assert recs[0].kind == tv.KIND_META
    assert recs[0].text == "server_tool_use"


# ---------------------------------------------------------------------------
# humanize_tool — MCP-name humanization.
# ---------------------------------------------------------------------------
def test_humanize_mcp_tool():
    assert tv.humanize_tool("mcp__plugin_dos__dos_verify") == "plugin_dos · dos_verify"
    assert tv.humanize_tool("Bash") == "Bash"
    assert tv.humanize_tool("mcp__x") == "mcp__x"  # malformed, returned unchanged


# ---------------------------------------------------------------------------
# No-crash floor.
# ---------------------------------------------------------------------------
def test_parse_no_crash_on_garbage():
    lines = [
        "",
        "   ",
        "not json at all",
        "{ torn json",
        json.dumps([1, 2, 3]),
        json.dumps("a string"),
        _assistant_text("survivor"),
    ]
    recs = tv.parse_records(lines)
    assert len(recs) == 1
    assert recs[0].text == "survivor"


def test_load_records_missing_file_is_empty(tmp_path):
    assert tv.load_records(str(tmp_path / "nope.jsonl")) == []


def test_load_records_reads_a_file(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_assistant_text("from disk") + "\n", encoding="utf-8")
    recs = tv.load_records(str(p))
    assert len(recs) == 1 and recs[0].text == "from disk"


def test_load_records_non_utf8_does_not_crash(tmp_path):
    p = tmp_path / "t.jsonl"
    data = (_assistant_text("ok") + "\n").encode("utf-8") + b"\xff\xfe garbage\n"
    p.write_bytes(data)
    recs = tv.load_records(str(p))
    assert any(r.text == "ok" for r in recs)


def test_load_records_is_streaming_not_full_read(tmp_path, monkeypatch):
    # load_records must NOT call readlines()/read() (the materialization the
    # streaming path avoids) — it iterates the file object lazily.
    p = tmp_path / "t.jsonl"
    p.write_text(_assistant_text("a") + "\n" + _assistant_text("b") + "\n", encoding="utf-8")

    import io
    real_open = io.open
    seen = {"readlines": False, "read": False}

    class _Spy:
        def __init__(self, fh):
            self._fh = fh

        def __iter__(self):
            return iter(self._fh)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._fh.__exit__(*a)

        def readlines(self, *a, **k):
            seen["readlines"] = True
            return self._fh.readlines(*a, **k)

        def read(self, *a, **k):
            seen["read"] = True
            return self._fh.read(*a, **k)

    def _spy_open(*a, **k):
        return _Spy(real_open(*a, **k))

    monkeypatch.setattr("builtins.open", _spy_open)
    recs = tv.load_records(str(p))
    assert [r.text for r in recs] == ["a", "b"]
    assert seen["readlines"] is False
    assert seen["read"] is False


def test_load_records_from_stream():
    import io
    stream = io.StringIO(_user("hi") + "\n" + _assistant_text("yo") + "\n")
    recs = tv.load_records_from_stream(stream)
    assert [r.kind for r in recs] == [tv.KIND_USER, tv.KIND_TEXT]
    assert recs[1].text == "yo"


def test_load_records_from_stream_no_crash_on_torn_line():
    import io
    stream = io.StringIO(_assistant_text("ok") + "\n{ torn")
    recs = tv.load_records_from_stream(stream)
    assert len(recs) == 1 and recs[0].text == "ok"


# ---------------------------------------------------------------------------
# resolve_shown — the --show/--hide algebra.
# ---------------------------------------------------------------------------
def test_resolve_shown_default():
    assert set(tv.resolve_shown(None, None)) == set(tv.DEFAULT_SHOWN)


def test_resolve_shown_show_is_additive():
    shown = tv.resolve_shown(["thinking"], None)
    assert tv.KIND_THINKING in shown
    assert tv.KIND_TEXT in shown


def test_resolve_shown_all():
    assert set(tv.resolve_shown(["all"], None)) == set(tv.ALL_KINDS)


def test_resolve_shown_hide_subtracts():
    shown = tv.resolve_shown(None, ["user", "tool_result"])
    assert tv.KIND_USER not in shown
    assert tv.KIND_TOOL_RESULT not in shown
    assert tv.KIND_TEXT in shown


def test_resolve_shown_unknown_token_ignored():
    assert set(tv.resolve_shown(["bogus"], ["alsobogus"])) == set(tv.DEFAULT_SHOWN)


def test_resolve_shown_hide_all():
    assert tv.resolve_shown(["all"], ["all"]) == ()


# ---------------------------------------------------------------------------
# apply_filter — the browse knobs.
# ---------------------------------------------------------------------------
def _sample():
    return tv.parse_records([
        _user("question"),
        _assistant_text("thinking out loud"),
        _tool_call("Bash", {"command": "ls"}, "t1"),
        _tool_result("t1", "ok"),
        _tool_call("Grep", {"pattern": "x"}, "t2"),
        _tool_result("t2", [{"type": "text", "text": "no match", "is_error": True}]),
        _assistant_text("all done"),
    ])


def test_filter_by_kind():
    flt = tv.StreamFilter(shown=(tv.KIND_TOOL_CALL,))
    out = tv.apply_filter(_sample(), flt)
    assert all(r.kind == tv.KIND_TOOL_CALL for r in out)
    assert len(out) == 2


def test_filter_errors_only():
    flt = tv.StreamFilter(shown=tv.ALL_KINDS, errors_only=True)
    out = tv.apply_filter(_sample(), flt)
    assert len(out) == 1
    assert out[0].is_error is True


def test_filter_by_tool_name_keeps_only_that_tool():
    flt = tv.StreamFilter(shown=tv.ALL_KINDS, tools=("bash",))
    out = tv.apply_filter(_sample(), flt)
    assert [r.tool_name for r in out] == ["Bash"]


def test_filter_grep_over_text_and_input():
    flt = tv.StreamFilter(shown=tv.ALL_KINDS, grep="ls")
    out = tv.apply_filter(_sample(), flt)
    assert any(r.tool_name == "Bash" for r in out)


def test_filter_last_tails_after_content_filter():
    flt = tv.StreamFilter(shown=(tv.KIND_TOOL_CALL,), last=1)
    out = tv.apply_filter(_sample(), flt)
    assert len(out) == 1 and out[0].tool_name == "Grep"


# ---------------------------------------------------------------------------
# build_view + counts + renderers.
# ---------------------------------------------------------------------------
def test_build_view_counts():
    recs = _sample()
    view = tv.build_view(recs, tv.StreamFilter(shown=tv.ALL_KINDS), source="x.jsonl")
    c = view.counts
    assert c[tv.KIND_TOOL_CALL] == 2
    assert c[tv.KIND_TOOL_RESULT] == 2
    assert c["errors"] == 1
    assert "claude-opus-4-8" in c["models"]
    assert view.total == len(recs)


def test_view_to_dict_roundtrips_records_and_is_json():
    view = tv.build_view(_sample(), tv.StreamFilter(shown=(tv.KIND_TOOL_CALL,)), source="s")
    d = view.to_dict()
    assert d["source"] == "s"
    assert d["shown"] == 2
    assert "schema" in d and "unstable" in d["schema"]
    assert all(r["kind"] == tv.KIND_TOOL_CALL for r in d["records"])
    json.dumps(d)


def test_render_text_has_header_records_and_footer():
    view = tv.build_view(_sample(), tv.StreamFilter(shown=tv.ALL_KINDS), source="abc.jsonl")
    out = tv.render_text(view)
    assert "transcript  abc.jsonl" in out
    assert "Bash" in out
    assert "errors: 1" in out
    assert "claude-opus-4-8" in out


def test_render_text_empty_filter_message():
    view = tv.build_view(_sample(), tv.StreamFilter(shown=(tv.KIND_THINKING,)), source="s")
    assert "nothing matched" in tv.render_text(view)


def test_render_record_truncates_one_line_by_default():
    r = tv.StreamRecord(index=0, kind=tv.KIND_TEXT, text="x" * 500)
    line = tv.render_record(r, full=False, width=40)
    assert len(line.split("\n")) == 1
    assert "…" in line


def test_render_record_full_expands_multiline():
    r = tv.StreamRecord(index=0, kind=tv.KIND_TEXT, text="line1\nline2\nline3")
    out = tv.render_record(r, full=True)
    assert "line1" in out and "line2" in out and "line3" in out
    assert len(out.split("\n")) == 3


def test_render_record_tool_result_error_tag():
    r = tv.StreamRecord(index=0, kind=tv.KIND_TOOL_RESULT, text="boom", is_error=True)
    assert "ERROR" in tv.render_record(r)


def test_render_record_synthetic_label():
    r = tv.StreamRecord(index=0, kind=tv.KIND_SYNTHETIC, text="429", is_error=True)
    assert "SYNTHETIC DEATH" in tv.render_record(r)


def test_render_record_humanizes_mcp_and_shows_caller():
    r = tv.StreamRecord(index=0, kind=tv.KIND_TOOL_CALL,
                        tool_name="mcp__srv__do_it", caller="child", tool_input={})
    out = tv.render_record(r)
    assert "srv · do_it" in out
    assert "[child]" in out


# ---------------------------------------------------------------------------
# discover_transcripts — host-AGNOSTIC (takes a directory, not host layout).
# ---------------------------------------------------------------------------
def test_discover_transcripts_finds_and_orders(tmp_path):
    proj = tmp_path / "sessions"
    proj.mkdir()
    older = proj / "older.jsonl"
    newer = proj / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    import os
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    found = tv.discover_transcripts(proj)
    assert [p.name for p in found] == ["newer.jsonl", "older.jsonl"]


def test_discover_transcripts_missing_dir_is_empty(tmp_path):
    assert tv.discover_transcripts(tmp_path / "nope") == []


def test_discover_transcripts_session_filter(tmp_path):
    proj = tmp_path / "sessions"
    proj.mkdir()
    (proj / "abc.jsonl").write_text("{}\n", encoding="utf-8")
    (proj / "def.jsonl").write_text("{}\n", encoding="utf-8")
    found = tv.discover_transcripts(proj, session="abc")
    assert [p.stem for p in found] == ["abc"]
