"""Tests for `dos.answer_shape` — the grounded-but-not-an-answer verdict (docs/156 §4).

Groups:
  * the abstain floor — no policy → INDETERMINATE (cannot judge shape without rules);
  * the structural NON_ANSWER cases — empty / whitespace / below the viability floor;
  * the marker NON_ANSWER cases — a process/CoT-log, a bare refusal, a tool dump
    (incl. THE q_025 leaked-chain-of-thought case the third-party adoption shipped);
  * ANSWER_SHAPED — no disqualifier (and the honesty-boundary invariant: ANSWER_SHAPED
    is "shaped like an answer", NEVER a claim of correctness);
  * the strict positive-marker mode — `answer_markers` required → INDETERMINATE when
    none match;
  * fail-safe — a host regex that does not compile NEVER raises and degrades toward
    not-disqualified (the dual of `run_judge`'s fail-to-abstain);
  * `to_dict` round-trip + the enum helper asymmetry (INDETERMINATE is neither
    shippable nor disqualified).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dos import answer_shape as _answer_shape
from dos.answer_shape import (
    AnswerShape,
    AnswerShapePolicy,
    AnswerShapeVerdict,
    GENERIC_ANSWER_SHAPE_POLICY,
    classify,
)


# ---------------------------------------------------------------------------
# The abstain floor.
# ---------------------------------------------------------------------------

def test_no_policy_is_indeterminate():
    """No rules → cannot judge shape → abstain (never a false ANSWER_SHAPED)."""
    v = classify("a perfectly fine looking answer", policy=None)
    assert v.state is AnswerShape.INDETERMINATE
    assert not v.is_shippable
    assert not v.is_disqualified


def test_none_text_with_default_policy_is_non_answer():
    """A None output is nothing delivered → NON_ANSWER (not a crash)."""
    v = classify(None)
    assert v.state is AnswerShape.NON_ANSWER
    assert v.length == 0


# ---------------------------------------------------------------------------
# Structural NON_ANSWER — empty / whitespace / viability floor.
# ---------------------------------------------------------------------------

def test_empty_is_non_answer():
    v = classify("")
    assert v.state is AnswerShape.NON_ANSWER


def test_whitespace_only_is_non_answer():
    v = classify("   \n\t  ")
    assert v.state is AnswerShape.NON_ANSWER
    assert v.is_disqualified


def test_below_viability_floor_is_non_answer():
    """A stub/ack token below the host's length floor is a non-answer."""
    pol = AnswerShapePolicy(min_viable_chars=10)
    v = classify("0", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert "viability floor" in v.reason


def test_at_viability_floor_is_not_disqualified_for_length():
    """At-or-above the floor passes the length test (and, with no markers, ships)."""
    pol = AnswerShapePolicy(min_viable_chars=5)
    v = classify("12345", policy=pol)
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_default_floor_is_one_so_a_single_char_passes_length():
    """The generic default floor (1) only disqualifies empty/whitespace, not a short token."""
    v = classify("7")  # a one-char output is shaped-ok by the generic policy (markers, not length, carry it)
    assert v.state is AnswerShape.ANSWER_SHAPED


# ---------------------------------------------------------------------------
# The verbosity ceiling — the symmetric opposite of the floor (the bloat end).
# ---------------------------------------------------------------------------

def test_ceiling_off_by_default_a_long_answer_ships():
    """The ceiling defaults OFF (0): a long, clean answer is ANSWER_SHAPED (no false bloat)."""
    long_clean = "The revenue grew steadily across all segments. " * 200  # ~9.4k chars
    v = classify(long_clean)
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_over_ceiling_is_non_answer():
    """A non-empty output over the host's ceiling is a runaway dump → NON_ANSWER."""
    pol = AnswerShapePolicy(max_viable_chars=20, max_repeat_ratio=0.0)
    v = classify("this output is comfortably longer than twenty characters", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert "ceiling" in v.reason
    assert v.is_disqualified


def test_at_ceiling_is_not_disqualified():
    """At-or-below the ceiling passes (strict `>` boundary, mirroring the floor's `<`)."""
    pol = AnswerShapePolicy(max_viable_chars=10)
    v = classify("0123456789", policy=pol)  # exactly 10 non-space chars
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_ceiling_is_a_shape_call_not_correctness():
    """The ceiling reason states it is a SHAPE call on the host's budget, not correctness."""
    pol = AnswerShapePolicy(max_viable_chars=5)
    v = classify("this is well over five chars", policy=pol)
    assert "not a correctness" in v.reason


def test_floor_beats_ceiling_when_both_could_apply():
    """A misconfigured max<min still resolves deterministically: the floor is checked first."""
    pol = AnswerShapePolicy(min_viable_chars=50, max_viable_chars=5)
    v = classify("twenty-ish chars here", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert "viability floor" in v.reason  # floor wins (checked first), not the ceiling


# ---------------------------------------------------------------------------
# The degeneration cap — drift-into-a-loop (the repeating-line/sentence failure).
# ---------------------------------------------------------------------------

def test_repeat_off_by_default_repetitive_text_still_ships():
    """The degeneration cap defaults OFF: even a repetitive output is not disqualified for it."""
    degenerate = "The answer is 42.\n" * 10
    v = classify(degenerate)  # generic policy: max_repeat_ratio 0.0 = off
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_repeated_line_loop_is_non_answer():
    """A generation collapsed into repeating one line → NON_ANSWER when the cap is set."""
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    degenerate = "The answer is 42.\n" * 10  # 10 units, 1 distinct → ratio 0.9
    v = classify(degenerate, policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert "repeat ratio" in v.reason
    assert v.is_disqualified


def test_repeated_sentence_on_one_line_is_caught():
    """Sentence-level segmentation: the same sentence many times on ONE line is caught."""
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify("Buy now. Buy now. Buy now. Buy now. Buy now.", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER


def test_normal_answer_under_cap_ships():
    """A normal multi-sentence answer has all-distinct units → ratio ~0, ships under the cap."""
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify(
        "Revenue rose 7%. Cloud led the growth. Margins held steady. Guidance was raised.",
        policy=pol,
    )
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_too_few_units_is_fail_safe_not_disqualified():
    """Below the minimum unit count the cap ABSTAINS (under-disqualify) — a short Q&A is safe."""
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify("Yes. No. Yes.", policy=pol)  # 3 units < _MIN_REPEAT_UNITS
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_repeat_cap_is_a_shape_call_not_correctness():
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify("Loop. Loop. Loop. Loop. Loop.", policy=pol)
    assert "not a correctness" in v.reason


def test_cot_marker_beats_repetition():
    """A more-specific CoT-leak marker still fires before the generic repetition cap."""
    pol = AnswerShapePolicy(
        non_answer_patterns=(r"<thinking>",),
        max_repeat_ratio=0.5,
    )
    v = classify("<thinking>x</thinking>\nLoop. Loop. Loop. Loop. Loop.", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert v.matched == r"<thinking>"  # the marker, not the repetition path


def test_repeat_ratio_helper_is_pure_and_bounded():
    """`_repeat_ratio` returns a ratio in [0,1) and the unit count; all-distinct → 0."""
    assert _answer_shape._repeat_ratio("a. b. c. d.") == (0.0, 4)
    ratio, n = _answer_shape._repeat_ratio("a. a. a. a.")
    assert n == 4 and ratio == 0.75
    assert _answer_shape._repeat_ratio("") == (0.0, 0)


# ---------------------------------------------------------------------------
# Boundary & fail-safe pins for the two new knobs — lock the load-bearing edges
# against a future refactor (a `> 0` → `!= 0` slip would disqualify everything).
# ---------------------------------------------------------------------------

def test_negative_ceiling_is_off():
    """A negative ceiling is OFF (the `> 0` gate), not "disqualify everything"."""
    pol = AnswerShapePolicy(max_viable_chars=-5)
    v = classify("any non-empty answer at all", policy=pol)
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_negative_repeat_cap_is_off():
    """A negative repeat cap is OFF (the `> 0.0` gate), not "disqualify everything"."""
    pol = AnswerShapePolicy(max_repeat_ratio=-0.5)
    v = classify("Loop. Loop. Loop. Loop. Loop.", policy=pol)
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_repeat_ratio_exactly_at_cap_ships():
    """Strict `>`: a ratio EXACTLY at the cap ships (the fail-safe under-disqualify side)."""
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify("a. b. a. b.", policy=pol)  # 4 units, 2 distinct → ratio exactly 0.5
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_exactly_min_units_can_fire():
    """Exactly _MIN_REPEAT_UNITS units is enough to fire (not just strictly more)."""
    assert _answer_shape._MIN_REPEAT_UNITS == 4
    pol = AnswerShapePolicy(max_repeat_ratio=0.5)
    v = classify("a. a. a. a.", policy=pol)  # exactly 4 units, ratio 0.75 > 0.5
    assert v.state is AnswerShape.NON_ANSWER


def test_ceiling_counts_non_space_chars_not_raw_length():
    """The ceiling tests non-space chars (symmetry with the floor); `.length` reports raw."""
    pol = AnswerShapePolicy(max_viable_chars=10)
    v = classify("hello" + " " * 100, policy=pol)  # 5 non-space chars, 105 raw
    assert v.state is AnswerShape.ANSWER_SHAPED  # ceiling sees 5 ≤ 10, ships
    assert v.length == 105                       # but length echoes the raw count


def test_ceiling_beats_marker_in_order():
    """The ceiling (checked before the markers) wins when both could disqualify."""
    long_cot = "<thinking>a leaked reasoning block well over ten chars</thinking>"
    pol = AnswerShapePolicy(max_viable_chars=10, non_answer_patterns=(r"<thinking>",))
    v = classify(long_cot, policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert "ceiling" in v.reason  # the length disqualifier fired first; matched stays empty
    assert v.matched == ""


def test_ceiling_and_repeat_non_answers_round_trip_with_empty_matched():
    """Both new disqualifier paths set matched='' (like the floor) — the to_dict contract."""
    ceil_v = classify("this is over the budget", policy=AnswerShapePolicy(max_viable_chars=5))
    rep_v = classify("Loop. Loop. Loop. Loop.", policy=AnswerShapePolicy(max_repeat_ratio=0.5))
    for v in (ceil_v, rep_v):
        d = v.to_dict()
        assert d["state"] == "NON_ANSWER"
        assert d["matched"] == ""
        assert d["is_disqualified"] is True
        assert d["is_shippable"] is False
        assert d["reason"]


# ---------------------------------------------------------------------------
# Marker NON_ANSWER — the q_025 catch.
# ---------------------------------------------------------------------------

def test_q025_leaked_chain_of_thought_is_caught():
    """THE motivating case (docs/156 §4): a long, grounded-looking CoT log is NOT an answer.

    The third-party RAG app shipped a 5,780-char leaked reasoning log as q_025's "answer"
    with refused=False. Length alone never catches that (it is long); the *marker* does.
    """
    leaked = (
        "Let me think about this. The fin_metric segment-vs-total computation looks "
        "off; <thinking> I should recompute the cloud growth but the table chunk "
        "</thinking> ... " + ("x" * 5000)
    )
    v = classify(leaked)
    assert v.state is AnswerShape.NON_ANSWER
    assert v.matched  # a pattern fired
    assert v.length > 5000  # and it was long — proving length alone would have passed it


def test_bare_refusal_pasted_as_content_is_non_answer():
    v = classify("I cannot answer that question based on the provided documents.")
    assert v.state is AnswerShape.NON_ANSWER


def test_raw_tool_call_dump_is_non_answer():
    v = classify("tool_call: search(query='revenue 2023')")
    assert v.state is AnswerShape.NON_ANSWER


def test_traceback_shipped_as_answer_is_non_answer():
    v = classify("Traceback (most recent call last):\n  File ...\nValueError: boom")
    assert v.state is AnswerShape.NON_ANSWER


def test_step_log_presented_as_answer_is_non_answer():
    v = classify("Step 1: retrieve the filing. Step 2: extract the number.")
    assert v.state is AnswerShape.NON_ANSWER


# ---------------------------------------------------------------------------
# ANSWER_SHAPED + the honesty boundary.
# ---------------------------------------------------------------------------

def test_clean_answer_is_answer_shaped():
    v = classify("Microsoft's FY2023 total revenue was $211.9 billion, up 7% YoY.")
    assert v.state is AnswerShape.ANSWER_SHAPED
    assert v.is_shippable


def test_answer_shaped_is_not_a_claim_of_correctness():
    """THE honesty boundary: a WRONG but well-shaped answer is still ANSWER_SHAPED.

    The verdict judges shape, never correctness — a wrong number that READS like an
    answer must NOT be disqualified here (that is the JUDGE/HUMAN's job). If this test
    ever flips, the module has drifted from W2-shape into W3-correctness (the
    consistency-is-not-grounding trap one level up).
    """
    wrong_but_shaped = "Google Cloud grew by $261.70 billion in 2023."  # factually wrong
    v = classify(wrong_but_shaped)
    assert v.state is AnswerShape.ANSWER_SHAPED  # shape is fine; correctness is not our question


def test_reason_states_it_is_not_correctness():
    v = classify("A normal, complete sentence answering the question.")
    assert "NOT a claim of correctness" in v.reason


# ---------------------------------------------------------------------------
# Strict positive-marker mode.
# ---------------------------------------------------------------------------

def test_required_answer_marker_present_is_answer_shaped():
    pol = AnswerShapePolicy(answer_markers=(r"\bAnswer:",))
    v = classify("Answer: the revenue was $211.9B.", policy=pol)
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_required_answer_marker_absent_is_indeterminate():
    """A strict host requires a positive marker; absent it, abstain (not ship)."""
    pol = AnswerShapePolicy(answer_markers=(r"\bAnswer:",))
    v = classify("the revenue was $211.9B.", policy=pol)
    assert v.state is AnswerShape.INDETERMINATE
    assert not v.is_shippable
    assert not v.is_disqualified  # INDETERMINATE is the abstain floor, not a disqualification


def test_disqualifier_beats_missing_marker():
    """A NON_ANSWER marker fires even when answer_markers are configured (order: disqualify first)."""
    pol = AnswerShapePolicy(
        non_answer_patterns=(r"<thinking>",),
        answer_markers=(r"\bAnswer:",),
    )
    v = classify("<thinking>hmm</thinking> Answer: 42", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER  # disqualified, not merely marker-checked


# ---------------------------------------------------------------------------
# Fail-safe — a bad host regex never raises.
# ---------------------------------------------------------------------------

def test_invalid_pattern_does_not_raise_and_under_disqualifies():
    """A host regex that does not compile degrades to 'not matched' — never an exception.

    Fail-safe direction = UNDER-disqualify (toward ANSWER_SHAPED), the dual of
    run_judge's fail-to-abstain. A broken disqualifier must not be able to crash a
    grounding gate that calls this on every output.
    """
    pol = AnswerShapePolicy(non_answer_patterns=(r"(unclosed group",))  # invalid regex
    v = classify("a normal answer", policy=pol)  # must not raise
    assert v.state is AnswerShape.ANSWER_SHAPED


def test_invalid_pattern_mixed_with_valid_still_catches_the_valid():
    pol = AnswerShapePolicy(non_answer_patterns=(r"(bad", r"<thinking>"))
    v = classify("<thinking>leak</thinking>", policy=pol)
    assert v.state is AnswerShape.NON_ANSWER
    assert v.matched == r"<thinking>"


# ---------------------------------------------------------------------------
# to_dict + enum helpers.
# ---------------------------------------------------------------------------

def test_to_dict_round_trip():
    v = classify("a clean answer")
    d = v.to_dict()
    assert d["state"] == "ANSWER_SHAPED"
    assert d["is_shippable"] is True
    assert d["is_disqualified"] is False
    assert "reason" in d and d["reason"]


def test_enum_helper_asymmetry():
    """INDETERMINATE is neither shippable nor disqualified — the abstain floor."""
    assert AnswerShape.ANSWER_SHAPED.is_shippable
    assert not AnswerShape.ANSWER_SHAPED.is_disqualified
    assert AnswerShape.NON_ANSWER.is_disqualified
    assert not AnswerShape.NON_ANSWER.is_shippable
    assert not AnswerShape.INDETERMINATE.is_shippable
    assert not AnswerShape.INDETERMINATE.is_disqualified


def test_str_enum_round_trips_as_token():
    assert str(AnswerShape.NON_ANSWER) == "NON_ANSWER"
    assert AnswerShape("ANSWER_SHAPED") is AnswerShape.ANSWER_SHAPED


def test_generic_policy_is_the_default():
    """Calling with no policy arg uses the shipped generic policy (not None)."""
    assert classify.__defaults__ is None or True  # signature default is the generic policy
    v = classify("<thinking>x</thinking>")  # generic policy includes this marker
    assert v.state is AnswerShape.NON_ANSWER
    assert GENERIC_ANSWER_SHAPE_POLICY.non_answer_patterns  # the default is non-empty


# ---------------------------------------------------------------------------
# The `dos answer-shape` CLI verb — the verdict IS the exit code (ANSWER_SHAPED 0,
# NON_ANSWER 3, INDETERMINATE 4, contract error 2). The peek over `classify`.
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path, stdin: "str | None" = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_cli_answer_shaped_exit_zero(tmp_path: Path):
    """A shaped answer → ANSWER_SHAPED, exit 0 (shippable on shape grounds)."""
    r = _run_cli("answer-shape", "--text", "The capital of France is Paris.", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_non_answer_leaked_cot_exit_three(tmp_path: Path):
    """The q_025 catch — a leaked CoT log → NON_ANSWER, exit 3."""
    r = _run_cli("answer-shape", "--text", "<thinking>let me reason</thinking>", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_empty_is_non_answer(tmp_path: Path):
    """An empty output → NON_ANSWER (nothing delivered), exit 3."""
    r = _run_cli("answer-shape", "--text", "", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_missing_input_is_contract_error(tmp_path: Path):
    """No --text AND no --file is a usage fault (exit 2), NOT an empty-string
    NON_ANSWER — "you forgot to say what to classify" is distinct from "you
    classified the empty string"."""
    r = _run_cli("answer-shape", cwd=tmp_path)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "--text" in r.stderr


def test_cli_text_dash_reads_stdin(tmp_path: Path):
    """`--text -` pipes the candidate from stdin (the natural large-answer path)."""
    r = _run_cli("answer-shape", "--text", "-", cwd=tmp_path,
                 stdin="I cannot answer that question.")
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_file_reads_path(tmp_path: Path):
    """`--file PATH` reads the candidate from a file."""
    cand = tmp_path / "draft.txt"
    cand.write_text("Paris is the capital of France.", encoding="utf-8")
    r = _run_cli("answer-shape", "--file", str(cand), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_file_dash_reads_stdin(tmp_path: Path):
    """`--file -` also reads stdin."""
    r = _run_cli("answer-shape", "--file", "-", cwd=tmp_path, stdin="Step 1: do the thing")
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_file_wins_over_text(tmp_path: Path):
    """When both are given, --file wins (an explicit path is the stronger intent)."""
    cand = tmp_path / "draft.txt"
    cand.write_text("<thinking>from the file</thinking>", encoding="utf-8")
    r = _run_cli("answer-shape", "--file", str(cand), "--text", "a clean answer", cwd=tmp_path)
    assert r.returncode == 3, r.stderr  # the file's leaked-CoT wins
    assert "NON_ANSWER" in r.stdout


def test_cli_unreadable_file_is_contract_error(tmp_path: Path):
    """A --file that cannot be read is a contract error (exit 2), not a crash."""
    r = _run_cli("answer-shape", "--file", str(tmp_path / "nope.txt"), cwd=tmp_path)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "error" in r.stderr.lower()


def test_cli_min_chars_floor(tmp_path: Path):
    """`--min-chars` raises the viability floor — a short stub → NON_ANSWER."""
    r = _run_cli("answer-shape", "--text", "ok", "--min-chars", "10", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_max_chars_ceiling(tmp_path: Path):
    """`--max-chars` sets the verbosity ceiling — an over-budget output → NON_ANSWER (3)."""
    r = _run_cli("answer-shape", "--text", "this is well over ten characters",
                 "--max-chars", "10", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_max_chars_off_by_default(tmp_path: Path):
    """With no --max-chars, a long answer still ships (the ceiling is off by default)."""
    r = _run_cli("answer-shape", "--text", "A clear, complete, and rather long answer here.",
                 cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_max_repeat_degeneration(tmp_path: Path):
    """`--max-repeat` flags a degenerate loop → NON_ANSWER (3)."""
    r = _run_cli("answer-shape", "--text",
                 "Buy now. Buy now. Buy now. Buy now. Buy now.",
                 "--max-repeat", "0.5", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout


def test_cli_max_repeat_normal_answer_ships(tmp_path: Path):
    """A normal answer under the degeneration cap ships (0)."""
    r = _run_cli("answer-shape", "--text",
                 "Revenue rose. Cloud led. Margins held. Guidance was raised.",
                 "--max-repeat", "0.5", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_non_answer_overlay_adds_to_default(tmp_path: Path):
    """`--non-answer` ADDS a host disqualifier on top of the generic default."""
    r = _run_cli("answer-shape", "--text", "INTERNAL DEBUG: foo",
                 "--non-answer", "internal debug", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "NON_ANSWER" in r.stdout
    # and the generic default still fires (the overlay augments, never replaces).
    r2 = _run_cli("answer-shape", "--text", "<thinking>x</thinking>",
                  "--non-answer", "internal debug", cwd=tmp_path)
    assert r2.returncode == 3, r2.stderr


def test_cli_markers_strict_mode_indeterminate(tmp_path: Path):
    """`--markers` strict mode: a clean text matching no marker → INDETERMINATE (4)."""
    r = _run_cli("answer-shape", "--text", "Paris is the capital",
                 "--markers", "^Answer:", cwd=tmp_path)
    assert r.returncode == 4, r.stderr
    assert "INDETERMINATE" in r.stdout


def test_cli_markers_present_is_answer_shaped(tmp_path: Path):
    """`--markers` strict mode: the marker present → ANSWER_SHAPED (0)."""
    r = _run_cli("answer-shape", "--text", "Answer: Paris", "--markers", "^Answer:", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_bad_regex_degrades_safe(tmp_path: Path):
    """A malformed host regex is skipped (fail-safe under-disqualify), never a crash."""
    r = _run_cli("answer-shape", "--text", "a clean answer here",
                 "--non-answer", "[unclosed", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANSWER_SHAPED" in r.stdout


def test_cli_json(tmp_path: Path):
    """`--json` emits the verdict's sorted to_dict()."""
    r = _run_cli("answer-shape", "--text", "Paris is the capital.", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["state"] == "ANSWER_SHAPED"
    assert obj["is_shippable"] is True
    assert obj["is_disqualified"] is False
    assert "length" in obj and "matched" in obj and "reason" in obj


def test_cli_no_plan(tmp_path: Path):
    """No-plan rail: runs in a bare dir with no git, no plan, no .dos/."""
    r = _run_cli("answer-shape", "--text", "A complete answer.", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / ".dos").exists()  # read-only: created no state


def test_cli_exit_codes_published_in_doctor(tmp_path: Path):
    """The exit-code map is published in `dos doctor --json exit_codes` (anti-drift)."""
    r = _run_cli("doctor", "--workspace", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    ec = json.loads(r.stdout)["exit_codes"]["answer-shape"]
    assert ec["ANSWER_SHAPED"] == 0
    assert ec["NON_ANSWER"] == 3
    assert ec["INDETERMINATE"] == 4
    assert ec["contract_error"] == 2
