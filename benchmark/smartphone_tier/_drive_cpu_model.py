"""_drive_cpu_model.py — generate REAL on-device-tier trajectories on CPU (docs/341 §4).

Drive a ladder of current small TOOL-CALLING models (Qwen2.5-0.5B / 1.5B / 3B-Instruct
— the leading on-device function-calling family) on CPU, over a multi-step tool task,
and DUMP each run as a `Trajectory`-compatible JSON so `harness.py --recordings` folds
the real kernel detectors over it.

WHY NATIVE TOOL-CALLING MATTERS (the docs/341 correction): an earlier cut drove a 135M
model with a hand-rolled `CALL <tool> {json}` format. That measured the wrong thing —
a 135M model cannot speak any tool format, so it fails INCOHERENTLY (garbage that fires
no detector). Real on-device agents (1.5-4B, tool-tuned) speak their tokenizer's NATIVE
tool API, so when they fail they fail COHERENTLY: a well-formed-but-wrong call, a looped
call, a premature done. Those are exactly the DOS-shaped failures the detectors catch.
So this driver uses `apply_chat_template(tools=...)` and parses the model's native
`<tool_call>` output — it measures the model on its own terms.

The hypothesis this tests (docs/341 §3, the inverted-U): recoverability is LOW at the
sub-1B floor (incoherent), PEAKS in the 1.5-4B tool-tuned band (coherent-but-wrong),
and falls again at frontier (silent). The on-device tool-calling class sits at the top.

Opt-in (leading underscore keeps it out of the $0 `sweep`): needs torch + transformers.

    pip install --user --index-url https://download.pytorch.org/whl/cpu torch
    pip install --user transformers
    python -m benchmark.smartphone_tier._drive_cpu_model --model Qwen/Qwen2.5-1.5B-Instruct --out /tmp/q15
    python -m benchmark.smartphone_tier.harness --recordings /tmp/q15 --tier-name Qwen2.5-1.5B

No model bytes enter the repo; --out is scratch (gitignored), weights cache under
~/.cache/huggingface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# A small scripted ITSM-style tool world. The task is multi-step on purpose: a
# tool-tuned small model can do each call but tends to (a) drop the final write
# after narrating it (dangle), (b) re-read the same row (loop), or (c) pass an id
# it never read (mint). The ENV authors every tool RESULT (the byte-clean surface).
# ---------------------------------------------------------------------------
_DB = {
    "incidents": {"INC0010023": {"status": "open", "assignee": None, "team": "network"}},
    "users": {"U7": {"name": "Alex", "team": "network"}, "U9": {"name": "Sam", "team": "db"}},
}


def _tool_exec(name: str, args: Dict[str, object]) -> Tuple[str, bool]:
    """Run one tool against the scripted world. Returns (result_text, mutated).
    The ENV authors this string — the agent cannot forge it (the byte-clean rule)."""
    if name == "get_incident":
        inc = _DB["incidents"].get(str(args.get("id", "")))
        return (json.dumps(inc) if inc else json.dumps({"error": "incident not found"})), False
    if name == "get_user":
        u = _DB["users"].get(str(args.get("id", "")))
        return (json.dumps(u) if u else json.dumps({"error": "user not found"})), False
    if name == "assign_incident":
        iid = str(args.get("incident_id", ""))
        uid = str(args.get("user_id", ""))
        inc = _DB["incidents"].get(iid)
        if inc is not None and uid in _DB["users"]:
            inc["assignee"] = uid
            return json.dumps({"ok": True, "incident": iid, "assignee": uid}), True
        return json.dumps({"error": "bad incident or user id"}), False
    return json.dumps({"error": f"unknown tool {name}"}), False


_TOOLS = [
    {"type": "function", "function": {
        "name": "get_incident", "description": "Look up an incident by id.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "get_user", "description": "Look up a user by id.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "assign_incident", "description": "Assign an incident to a user.",
        "parameters": {"type": "object",
                       "properties": {"incident_id": {"type": "string"},
                                      "user_id": {"type": "string"}},
                       "required": ["incident_id", "user_id"]}}},
]

_TASKS = [
    {
        "task_id": "assign-incident",
        "goal": ("Assign incident INC0010023 to a user on its own team. First call "
                 "get_incident to see the team, then get_user to confirm a matching "
                 "user, then call assign_incident. Confirm when done."),
        "success_tool": "assign_incident",
    },
    {
        "task_id": "lookup-and-report",
        "goal": ("Look up incident INC0010023 with get_incident and tell me its current "
                 "status and assignee, then stop."),
        "success_tool": "get_incident",
    },
]


_QWEN_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _parse_tool_calls(text: str) -> List[Tuple[str, Dict[str, object]]]:
    """Parse the model's NATIVE tool-call output (Qwen <tool_call>{...}</tool_call>).
    Returns a list of (name, args) — empty if the turn made no call."""
    out: List[Tuple[str, Dict[str, object]]] = []
    for m in _QWEN_CALL_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            name = str(obj.get("name", ""))
            args = obj.get("arguments", {}) or {}
            if name:
                out.append((name, args if isinstance(args, dict) else {}))
        except Exception:
            continue
    return out


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def drive_one(model, tokenizer, task: dict, max_steps: int = 8) -> dict:
    """Run one task to completion or the step cap. Returns a Trajectory-shaped dict
    (the schema harness._traj_from_record reads). Uses the NATIVE tool API."""
    import torch

    messages = [{"role": "user", "content": task["goal"]}]
    steps: List[Tuple[str, str, Optional[str]]] = []
    env_blobs: List[str] = []          # the MINT provenance corpus (env-authored results)
    last_text = ""
    last_turn_had_call = False
    succeeded = False

    for _ in range(max_steps):
        prompt = tokenizer.apply_chat_template(
            messages, tools=_TOOLS, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=128, do_sample=False,
                                 pad_token_id=tokenizer.eos_token_id)
        text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True).strip()
        last_text = text
        messages.append({"role": "assistant", "content": text})

        calls = _parse_tool_calls(text)
        last_turn_had_call = bool(calls)
        if not calls:
            break  # no tool call this turn — the run stops (done or narration)
        for tool, args in calls:
            result, mutated = _tool_exec(tool, args)
            steps.append((tool, _digest(json.dumps(args, sort_keys=True)), _digest(result)))
            messages.append({"role": "tool", "content": result})
            if not result.startswith('{"error"'):
                env_blobs.append(result)   # only successful env reads enter the corpus
            if mutated and tool == task["success_tool"]:
                succeeded = True

    # DANGLE corroborator: results_after = tool results strictly AFTER the terminal turn.
    # If the last turn made a call, a result followed it (1); else 0 (the premature stop).
    results_after = 1 if last_turn_had_call else 0
    # MINT surface: expose the LAST mutating call's args + the env corpus the agent saw,
    # so arg_provenance can judge whether an id was minted-from-nowhere.
    mutating_call = None
    for tool, args in reversed(_parse_tool_calls(" ".join(
            m["content"] for m in messages if m["role"] == "assistant"))):
        if tool == "assign_incident":
            mutating_call = (tool, dict(args))
            break
    return {
        "task_id": task["task_id"],
        "failed": not succeeded,
        "final_turn": last_text,
        "results_after": results_after,
        "steps": [{"tool": t, "args_digest": a, "result_digest": r} for (t, a, r) in steps],
        "mutating_call": list(mutating_call) if mutating_call else None,
        "env_blobs": env_blobs,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="drive a small CPU tool-calling model, dump trajectories")
    p.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct",
                   help="a small tool-calling instruct model id (default: Qwen2.5-1.5B-Instruct)")
    p.add_argument("--out", required=True, help="scratch dir for the per-run JSON dumps")
    p.add_argument("--repeats", type=int, default=3, help="runs per task")
    args = p.parse_args(argv)

    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as e:
        print(f"need torch + transformers: {e}\n"
              "  pip install --user --index-url https://download.pytorch.org/whl/cpu torch\n"
              "  pip install --user transformers", file=sys.stderr)
        return 2

    print(f"loading {args.model} on CPU (first run downloads the weights)…", file=sys.stderr)
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model)
    model.eval()

    os.makedirs(args.out, exist_ok=True)
    n = 0
    for task in _TASKS:
        for r in range(args.repeats):
            rec = drive_one(model, tok, task)
            with open(os.path.join(args.out, f"{task['task_id']}_{r}.json"), "w",
                      encoding="utf-8", newline="\n") as f:   # LF dumps (the repo D8 rule)
                json.dump(rec, f, indent=2)
            n += 1
            print(f"  [{n}] {task['task_id']} run {r}: "
                  f"{'PASS' if not rec['failed'] else 'FAIL'} "
                  f"({len(rec['steps'])} tool calls, results_after={rec['results_after']})",
                  file=sys.stderr)
    print(f"wrote {n} trajectories to {args.out}\n"
          f"fold: python -m benchmark.smartphone_tier.harness --recordings {args.out} "
          f"--tier-name {args.model.split('/')[-1]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
