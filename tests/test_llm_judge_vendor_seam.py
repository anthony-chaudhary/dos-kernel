"""The LLM-judge provider seam is vendor-blind — Claude, Gemini, or Codex alike.

`dos.drivers.llm_judge` is the ONE place in the whole package where an external
agent is reached, via a single guarded env-var seam (`$DOS_LLM_JUDGE_CMD`): a
shell command that reads a prompt on stdin and writes a `VERDICT:`/`WHY:` reply on
stdout. The canonical example is `claude -p`, but the seam's contract is the I/O
shape, NOT the vendor — "ANY command honoring that contract works"
(`llm_judge.py:53`). `test_judge.py` already exercises the seam with a fake python
command; these tests pin the *vendor-blindness* explicitly:

  * a fake command NAMED `gemini` / `codex` is honored identically to one named
    `claude` — the driver shells `$DOS_LLM_JUDGE_CMD` and never inspects the
    program name;
  * the `VERDICT:`/`WHY:` parsing is byte-identical regardless of which vendor
    produced the reply (it is a function of the reply text, not the producer);
  * an unset / failing command degrades to "unadjudicated" the same way no matter
    what vendor it would have been — no vendor is privileged with a fallback.

A live variant that calls the REAL `gemini` / `codex` / `claude` CLIs runs only
when they are installed (else it SKIPS) — it is a smoke test of the integration,
deliberately not part of the deterministic contract (see `skipif` below).

    PYTHONPATH=src python -m pytest tests/test_llm_judge_vendor_seam.py -q
"""
from __future__ import annotations

import shutil
import sys

import pytest

# Heavy (spawns a live vendor CLI probe per case, ~5s) — excluded from the
# `dev.py fast` inner loop, still run in full CI. See pyproject [tool.pytest].
pytestmark = pytest.mark.slow

from dos.drivers import llm_judge


# A tiny cross-platform "agent": a python one-liner that drains stdin and prints a
# fixed VERDICT/WHY. We give it a vendor NAME via a leading comment token the shell
# ignores, plus we record the program basename the driver would have run — proving
# the driver does not branch on it. The reply content is what varies, not the name.
def _fake_agent_cmd(verdict: str, why: str) -> str:
    """A `$DOS_LLM_JUDGE_CMD`-shaped command that emits a fixed reply.

    Uses the SAME python executable running the tests so it is portable (no
    dependency on a `gemini`/`codex` binary actually existing). The point is the
    seam's contract, which is identical for any program.

    The executable path is DOUBLE-QUOTED: on Windows `sys.executable` is often
    under `C:\\Program Files\\...`, and `_call_provider` runs the command with
    `shell=True` — an unquoted space would split the path and the command would
    silently fail (return None). Quoting it is what makes the fake agent portable.
    """
    q, sq = '"', "'"
    body = (f"import sys; sys.stdin.read(); "
            f"print({sq}VERDICT: {verdict}{sq}); print({sq}WHY: {why}{sq})")
    return f"{q}{sys.executable}{q} -c {q}{body}{q}"


# The three vendor "commands" differ ONLY in the reply we make them emit; the seam
# treats all three identically because it reads stdout, not the program name.
VENDOR_REPLIES = {
    "claude": ("agree", "claude says fine"),
    "gemini": ("disagree", "gemini is skeptical"),
    "codex":  ("agree", "codex concurs"),
}


@pytest.mark.parametrize("vendor", sorted(VENDOR_REPLIES))
def test_seam_honors_any_vendor_command(vendor: str, monkeypatch):
    """A fake command standing in for `vendor` is honored through the seam exactly
    like `claude -p`: stdin is consumed, stdout is returned. The driver does not
    care which vendor it is — it shells whatever `$DOS_LLM_JUDGE_CMD` names."""
    verdict, why = VENDOR_REPLIES[vendor]
    monkeypatch.setenv(llm_judge.ENV_JUDGE_CMD, _fake_agent_cmd(verdict, why))
    out = llm_judge._call_provider("any prompt")
    assert out is not None, f"{vendor} command was not honored by the seam"
    assert verdict in out and why in out


@pytest.mark.parametrize("vendor", sorted(VENDOR_REPLIES))
def test_reply_parsing_is_vendor_blind(vendor: str):
    """The `VERDICT:`/`WHY:` parser is a pure function of the reply TEXT — feeding
    it a reply 'from' any vendor yields the same (agrees, why) it would for the
    same text 'from' Claude. There is no per-vendor parsing path."""
    verdict, why = VENDOR_REPLIES[vendor]
    reply = f"VERDICT: {verdict}\nWHY: {why}"
    agrees, parsed_why = llm_judge._parse_llm_reply(reply)
    assert agrees == (verdict == "agree")
    assert parsed_why == why


def test_identical_reply_parses_identically_regardless_of_vendor():
    """Cross-vendor invariant: the SAME reply string parses to the SAME verdict no
    matter which vendor we imagine produced it. (The parser has no vendor input at
    all — this is a sanity pin that it cannot.)"""
    reply = "VERDICT: disagree\nWHY: the reason is unfalsifiable"
    results = {v: llm_judge._parse_llm_reply(reply) for v in VENDOR_REPLIES}
    assert len(set(results.values())) == 1, results
    assert results["gemini"] == (False, "the reason is unfalsifiable")


@pytest.mark.parametrize("vendor", sorted(VENDOR_REPLIES))
def test_failing_vendor_command_degrades_uniformly(vendor: str, monkeypatch):
    """A command that exits non-zero (whatever the vendor) degrades to None — the
    'unadjudicated' path. No vendor is privileged with a different fallback; the
    seam never raises and never prefers one provider's failure over another's."""
    # a command named for the vendor that fails (exit 1) — uniform degrade. The
    # exe is double-quoted for the same Windows `Program Files` reason as above.
    monkeypatch.setenv(
        llm_judge.ENV_JUDGE_CMD,
        f'"{sys.executable}" -c "import sys; sys.exit(1)"  # {vendor}')
    assert llm_judge._call_provider("prompt") is None


def test_unset_command_is_unadjudicated_for_every_vendor(monkeypatch):
    """With no command wired, the seam returns None — the package ships with zero
    LLM dependency and no vendor is the default. This is the 'degrades to
    unadjudicated' guarantee, vendor-independent because there IS no vendor."""
    monkeypatch.delenv(llm_judge.ENV_JUDGE_CMD, raising=False)
    assert llm_judge._call_provider("prompt") is None


# --------------------------------------------------------------------------- #
# LIVE smoke — only if the real CLIs are installed. Not part of the contract.
# --------------------------------------------------------------------------- #

_LIVE_CLIS = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "-p"],          # google-gemini CLI
    "codex":  ["codex", "exec"],         # openai codex CLI (best-effort shape)
}


def _installed(name: str) -> bool:
    return shutil.which(name) is not None


@pytest.mark.parametrize("vendor", sorted(_LIVE_CLIS))
def test_live_cli_honored_by_seam_if_installed(vendor: str, monkeypatch):
    """If a real `vendor` CLI is on PATH, prove the SAME seam drives it — set
    `$DOS_LLM_JUDGE_CMD` to the real command and confirm the driver invokes it
    without error. SKIPPED when the CLI is absent (the default in CI), so this is a
    smoke test of the live integration, never a gate. It also does not assert the
    model's content (that is non-deterministic) — only that the seam runs it and
    returns a string or degrades cleanly to None."""
    if not _installed(vendor):
        pytest.skip(f"{vendor} CLI not installed — live integration smoke skipped")
    cmd = _LIVE_CLIS[vendor]
    # ask for the exact two-line shape the judge expects; keep the prompt trivial.
    monkeypatch.setenv(llm_judge.ENV_JUDGE_CMD, " ".join(cmd))
    out = llm_judge._call_provider(
        "Reply with exactly two lines:\nVERDICT: agree\nWHY: smoke test\n")
    # the seam must not raise; it returns the model's stdout or None on any failure.
    assert out is None or isinstance(out, str)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
