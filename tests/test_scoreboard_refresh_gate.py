from __future__ import annotations

from scripts.scoreboard_refresh_gate import classify, main


def _adj(*shas):
    return {"records": [{"sha": sha} for sha in shas]}


def test_equal_unwitnessed_set_is_safe_despite_order_and_sha_width():
    result = classify(
        {"unwitnessed_shas": ["abcdef0", "1234567"]},
        _adj("1234567890abcdef", "abcdef0123456789"),
    )
    assert result["safe"] is True
    assert result["new"] == []
    assert result["resolved"] == []


def test_new_or_resolved_raw_fire_refuses_automatic_refresh():
    result = classify({"unwitnessed_shas": ["abcdef0", "7654321"]}, _adj("abcdef0"))
    assert result == {
        "safe": False,
        "raw_unwitnessed": ["7654321", "abcdef0"],
        "adjudicated": ["abcdef0"],
        "new": ["7654321"],
        "resolved": [],
    }


def test_cli_writes_machine_outputs_on_refusal(tmp_path):
    sweep = tmp_path / "sweep.json"
    adj = tmp_path / "adj.json"
    output = tmp_path / "output"
    sweep.write_text('{"unwitnessed_shas":["abcdef0"]}', encoding="utf-8")
    adj.write_text('{"records":[]}', encoding="utf-8")

    assert main([
        "--sweep", str(sweep), "--adjudications", str(adj),
        "--github-output", str(output),
    ]) == 3
    assert "safe=false" in output.read_text(encoding="utf-8")
    assert "new=abcdef0" in output.read_text(encoding="utf-8")
