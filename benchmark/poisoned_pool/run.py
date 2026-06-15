"""run.py — the poisoned-pool run-state machine (docs/322 P1, issue #36).

A run is a directory the harness owns:

    <run-dir>/state.json              the loop state (pools, metrics, cursor)
    <run-dir>/prompts/gen<g>/<id>.md  emitted prompts awaiting a policy
    <run-dir>/completions/gen<g>/<id>.md   the policy's replies (driver-written)
    <run-dir>/trajectories.jsonl      every adjudicated trajectory, append-only

The cycle (the policy is OUTSIDE — any driver that turns a prompt file into a
completion file works; nothing here names a model):

    python -m benchmark.poisoned_pool.run init --run-dir D [--gens 4 ...]
    ... driver answers prompts/gen0/*.md into completions/gen0/*.md ...
    python -m benchmark.poisoned_pool.run ingest --run-dir D
    ... repeat until state.phase == "done" ...
    python -m benchmark.poisoned_pool.run report --run-dir D [--write-beside]

`selfcheck` witnesses the corpus's own ground truth (every planted bug fails,
every reference fix passes) — executed, never narrated.
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import harness, lineage
from .tasks import BugTask, all_tasks, heldout_tasks, task_by_id, train_tasks

BESIDE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _load_state(run_dir: Path) -> Dict:
    return json.loads(_state_path(run_dir).read_text(encoding="utf-8"))


def _save_state(run_dir: Path, state: Dict) -> None:
    _state_path(run_dir).write_text(
        json.dumps(state, indent=2, sort_keys=False), encoding="utf-8")


def _append_rows(run_dir: Path, rows: List[Dict]) -> None:
    with (run_dir / "trajectories.jsonl").open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=False) + "\n")


# ---------------------------------------------------------------------------
# Prompt emission
# ---------------------------------------------------------------------------

def _batch_ids(gen: int, arm: str, cfg: Dict) -> List[harness.TrajId]:
    ids: List[harness.TrajId] = []
    for t in train_tasks():
        for k in range(cfg["k_train"]):
            ids.append(harness.TrajId(gen, arm, "train", t.task_id, k))
    for t in heldout_tasks():
        for k in range(cfg["k_eval"]):
            ids.append(harness.TrajId(gen, arm, "eval", t.task_id, k))
    return ids


def _sample_exemplars(pool: List[Dict], gen: int, arm: str, cfg: Dict) -> List[Dict]:
    if not pool:
        return []
    rng = random.Random(f"{cfg['seed']}.{gen}.{arm}")
    n = min(cfg["m_exemplars"], len(pool))
    return rng.sample(pool, n)


def _emit_generation(run_dir: Path, state: Dict) -> List[str]:
    """Write prompt files for the current generation; return the pending ids.

    Records the exemplar EDGE as it is drawn: the exemplars are sampled per
    (gen, arm) BATCH (one draw seeds every trajectory in the batch), so the
    lineage parent set is a batch fact. `state["exemplars"]["{gen}.{arm}"]`
    holds the traj_ids of the admitted pool entries that conditioned the batch
    — the direct parents the lineage fold walks up to the gen-0 roots. Arm B /
    gen 0 conditions on the empty pool, so its batch records `[]`: the roots.
    Without this the edge was computed, rendered into the prompt, and dropped.
    """
    cfg = state["config"]
    gen = state["current_gen"]
    arms = ["B"] if gen == 0 else ["S", "W"]
    out_dir = run_dir / "prompts" / f"gen{gen}"
    out_dir.mkdir(parents=True, exist_ok=True)
    exemplar_edges: Dict[str, List[str]] = state.setdefault("exemplars", {})
    pending: List[str] = []
    for arm in arms:
        # Arm B (generation 0) conditions on the empty pool — exemplars = [].
        pool = [] if arm == "B" else state["pools"][arm]
        exemplars = _sample_exemplars(pool, gen, arm, cfg)
        exemplar_edges[f"{gen}.{arm}"] = [e["traj_id"] for e in exemplars]
        for tid in _batch_ids(gen, arm, cfg):
            task = task_by_id(tid.task_id)
            (out_dir / f"{tid}.md").write_text(
                harness.render_prompt(task, exemplars), encoding="utf-8")
            pending.append(str(tid))
    state["pending"] = pending
    state["phase"] = "sampling"
    return pending


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def init_run(run_dir: Path, *, gens: int = 4, k_train: int = 2, k_eval: int = 2,
             m_exemplars: int = 4, seed: int = 7,
             policy: Optional[Dict] = None) -> Dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    if _state_path(run_dir).exists():
        raise SystemExit(f"refusing to clobber existing state in {run_dir}")
    config: Dict = {
        "gens": gens, "k_train": k_train, "k_eval": k_eval,
        "m_exemplars": m_exemplars, "seed": seed,
        "train_tasks": [t.task_id for t in train_tasks()],
        "heldout_tasks": [t.task_id for t in heldout_tasks()],
    }
    # `policy` records the DRIVER, not a belief rule — purely provenance so the
    # evidence file can name what answered the prompts (a live session leaves it
    # absent; the synthetic probe records its name + knobs).
    if policy is not None:
        config["policy"] = policy
    state: Dict = {
        "bench": "poisoned_pool",
        "plan": "docs/322",
        "issue": 36,
        "config": config,
        "current_gen": 0,
        "phase": "sampling",
        "pools": {"S": [], "W": []},
        "generations": [],
        "pending": [],
    }
    _emit_generation(run_dir, state)
    _save_state(run_dir, state)
    return state


# ---------------------------------------------------------------------------
# ingest — adjudicate one generation's completions, update pools, emit next
# ---------------------------------------------------------------------------

def _adjudicate_one(tid: harness.TrajId, raw: str, missing: bool,
                    exemplar_ids: Optional[List[str]] = None) -> Dict:
    task = task_by_id(tid.task_id)
    patch, claim = (None, None) if missing else harness.parse_completion(raw)
    readback = harness.run_witness(task, patch)
    confirmed = readback.stance.value == "ATTESTED"
    row: Dict = {
        "traj_id": str(tid), "gen": tid.gen, "arm": tid.arm, "kind": tid.kind,
        "task_id": tid.task_id, "missing": missing,
        "claim": claim, "patch_chars": len(patch) if patch else 0,
        "witness_confirmed": confirmed,
        "witness_stance": readback.stance.value,
        "witness_detail": readback.detail,
        # The lineage parent edge: the traj_ids of the admitted exemplars that
        # conditioned this trajectory's prompt (a batch fact — every row in the
        # batch shares it). `[]` for a gen-0 / arm-B root. The lineage fold walks
        # these up to the gen-0 roots; an old row missing the key reads as `[]`.
        "exemplar_ids": list(exemplar_ids or []),
    }
    if tid.arm in ("S", "B"):
        row["armS_admit"] = harness.self_admit(claim) and tid.kind == "train"
    if tid.arm in ("W", "B"):
        label = harness.witness_admit(claim, readback, tid.task_id)
        row["armW_verdict"] = label.verdict.value
        row["armW_admit"] = label.accept and tid.kind == "train"
        row["armW_poison"] = label.poison
    return row


def _arm_gen_record(rows: List[Dict], pool: List[Dict], arm: str) -> Dict:
    train_rows = [r for r in rows if r["kind"] == "train"]
    eval_rows = [r for r in rows if r["kind"] == "eval"]
    rec: Dict = {
        "train": harness.fold_batch(train_rows),
        "eval": harness.fold_batch(eval_rows),
        "admitted_n": sum(1 for r in train_rows if r.get(f"arm{arm}_admit")),
    }
    rec.update(harness.fold_pool(pool))
    if arm == "W":
        counts: Dict[str, int] = {}
        for r in train_rows:
            v = r.get("armW_verdict")
            if v:
                counts[v] = counts.get(v, 0) + 1
        rec["reward_counts"] = counts
    return rec


def ingest_run(run_dir: Path, *, allow_missing: bool = False) -> Dict:
    state = _load_state(run_dir)
    if state["phase"] != "sampling":
        raise SystemExit(f"nothing to ingest: phase is {state['phase']!r}")
    gen = state["current_gen"]
    comp_dir = run_dir / "completions" / f"gen{gen}"
    missing = [tid for tid in state["pending"]
               if not (comp_dir / f"{tid}.md").exists()]
    if missing and not allow_missing:
        raise SystemExit(
            f"{len(missing)} completion(s) missing for gen{gen} "
            f"(first: {missing[0]}); supply them or pass --allow-missing")

    exemplar_edges: Dict[str, List[str]] = state.get("exemplars", {})
    rows: List[Dict] = []
    for tid_s in state["pending"]:
        tid = harness.TrajId.parse(tid_s)
        path = comp_dir / f"{tid_s}.md"
        is_missing = not path.exists()
        raw = "" if is_missing else path.read_text(encoding="utf-8", errors="replace")
        parents = exemplar_edges.get(f"{tid.gen}.{tid.arm}", [])
        rows.append(_adjudicate_one(tid, raw, is_missing, parents))

    # Pool updates — train rows only; eval rows are measured, never admitted.
    for r in rows:
        if r["kind"] != "train":
            continue
        task = task_by_id(r["task_id"])
        patch = None
        if not r["missing"]:
            raw = (comp_dir / f"{r['traj_id']}.md").read_text(
                encoding="utf-8", errors="replace")
            patch, _ = harness.parse_completion(raw)
        entry = harness.pool_entry(r["traj_id"], task, patch, r["claim"],
                                   r["witness_confirmed"])
        if r.get("armS_admit"):
            state["pools"]["S"].append(entry)
        if r.get("armW_admit"):
            state["pools"]["W"].append(entry)

    by_arm: Dict[str, List[Dict]] = {
        "S": [r for r in rows if r["arm"] in ("S", "B")],
        "W": [r for r in rows if r["arm"] in ("W", "B")],
    }
    state["generations"].append({
        "gen": gen,
        "S": _arm_gen_record(by_arm["S"], state["pools"]["S"], "S"),
        "W": _arm_gen_record(by_arm["W"], state["pools"]["W"], "W"),
    })
    _append_rows(run_dir, rows)

    state["current_gen"] = gen + 1
    if state["current_gen"] < state["config"]["gens"]:
        _emit_generation(run_dir, state)
    else:
        state["pending"] = []
        state["phase"] = "done"
    _save_state(run_dir, state)
    return state


# ---------------------------------------------------------------------------
# drive — answer the pending generation's prompts with a synthetic policy.
# Provider-free: this is a SCRIPTED test driver (the harness contract allows
# any driver that turns prompt files into completion files), not a model. It
# exists so the P2-prep sensitivity probe runs reproducibly as one command;
# a live model session driving the prompt files by hand is the other path.
# ---------------------------------------------------------------------------

def drive_run(run_dir: Path, *, policy_name: str = "noisy", **overrides) -> int:
    from . import policies
    state = _load_state(run_dir)
    if state["phase"] != "sampling":
        raise SystemExit(f"nothing to drive: phase is {state['phase']!r}")
    gen = state["current_gen"]
    seed = state["config"]["seed"]
    policy = policies.build_policy(policy_name, seed=seed, **overrides)
    cap = state["config"]["m_exemplars"]
    prompt_dir = run_dir / "prompts" / f"gen{gen}"
    comp_dir = run_dir / "completions" / f"gen{gen}"
    comp_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for tid_s in state["pending"]:
        tid = harness.TrajId.parse(tid_s)
        prompt_text = (prompt_dir / f"{tid_s}.md").read_text(encoding="utf-8")
        body = policy.answer(tid_s, task_by_id(tid.task_id), prompt_text, cap)
        (comp_dir / f"{tid_s}.md").write_text(body, encoding="utf-8")
        written += 1
    return written


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _curve(state: Dict, arm: str, batch: str, key: str) -> List:
    return [g[arm][batch][key] for g in state["generations"]]


def build_results(state: Dict) -> Dict:
    return {
        "bench": "poisoned_pool",
        "plan": "docs/322",
        "issue": 36,
        "question": ("Does a self-judged admission gate (Arm S) accumulate "
                     "over-claim poison across expert-iteration generations "
                     "while the witness-gated kernel verdict (Arm W, "
                     "dos.reward.admit) does not?"),
        "config": state["config"],
        "generations_completed": len(state["generations"]),
        "generations": state["generations"],
        "curves": {
            "eval_overclaim_rate": {a: _curve(state, a, "eval", "overclaim_rate")
                                    for a in ("S", "W")},
            "eval_true_success_rate": {a: _curve(state, a, "eval", "true_success_rate")
                                       for a in ("S", "W")},
            "train_overclaim_rate": {a: _curve(state, a, "train", "overclaim_rate")
                                     for a in ("S", "W")},
            "pool_poison_frac": {a: [g[a]["pool_poison_frac"]
                                     for g in state["generations"]]
                                 for a in ("S", "W")},
            "pool_size": {a: [g[a]["pool_size"] for g in state["generations"]]
                          for a in ("S", "W")},
        },
    }


def _stamp_line() -> str:
    """The canonical provenance stamp `dos bench status` reads (the _run.py
    grammar, restated locally so the module-form launch needs no sys.path
    tricks). sha is optional in the grammar; omitted when not in a git tree."""
    import dos
    sha = ""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            sha = out.stdout.strip()
    except OSError:
        pass
    date = datetime.date.today().isoformat()
    mid = f" sha={sha}" if sha else ""
    return f"<!-- dos-bench-stamp: kernel={dos.__version__}{mid} date={date} -->"


def _fmt_rates(values: List[float]) -> str:
    return " -> ".join(f"{v:.2f}" for v in values)


def render_results_md(results: Dict, rows: Optional[List[Dict]] = None,
                      title: str = "poisoned_pool — run results (docs/322 P1, issue #36)",
                      preamble: Optional[List[str]] = None,
                      json_name: str = "results.json") -> str:
    c = results["curves"]
    lines = [
        f"# {title}",
        "",
        _stamp_line(),
        "",
        "> " + results["question"],
        "",
        f"Generations completed: {results['generations_completed']} "
        f"(config: {json.dumps(results['config'], sort_keys=True)})",
        "",
    ]
    if preamble:
        lines += preamble + [""]
    lines += [
        "## The curves (per generation, generation 0 first)",
        "",
        "| metric | Arm S (self-judged) | Arm W (dos.reward.admit) |",
        "|---|---|---|",
    ]
    for key, label in (
        ("eval_overclaim_rate", "held-out over-claim rate"),
        ("eval_true_success_rate", "held-out true success rate"),
        ("train_overclaim_rate", "train over-claim rate"),
        ("pool_poison_frac", "pool poison fraction"),
        ("pool_size", "pool size"),
    ):
        s = c[key]["S"]
        w = c[key]["W"]
        if key == "pool_size":
            lines.append(f"| {label} | {' -> '.join(str(v) for v in s)} "
                         f"| {' -> '.join(str(v) for v in w)} |")
        else:
            lines.append(f"| {label} | {_fmt_rates(s)} | {_fmt_rates(w)} |")
    lines += [
        "",
        "## Per-generation detail",
        "",
        "```json",
        json.dumps(results["generations"], indent=2),
        "```",
        "",
    ]
    if rows is not None:
        lines += [
            f"Per-trajectory verdict rows: {len(rows)} (in `{json_name}`).",
            "",
        ]
    return "\n".join(lines)


def report_run(run_dir: Path, *, write_beside: bool = False,
               basename: str = "results", title: Optional[str] = None,
               preamble: Optional[List[str]] = None) -> Dict:
    state = _load_state(run_dir)
    results = build_results(state)
    traj_path = run_dir / "trajectories.jsonl"
    rows: List[Dict] = []
    if traj_path.exists():
        for line in traj_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    results["rows"] = rows
    # The lineage channel: per-root credit folded from the recorded exemplar
    # edges (a no-op `lineage_recorded: false` on pre-channel artifacts). Pure
    # read over the rows already loaded — changes no existing curve or metric.
    results["lineage"] = lineage.lineage_report(rows)
    # Markdown file mirrors the canonical RESULTS.md casing; the json keeps the
    # lowercase basename (results.json / results_run2.json).
    md_name = ("RESULTS.md" if basename == "results"
               else "RESULTS" + basename[len("results"):] + ".md"
               if basename.startswith("results") else f"{basename}.md")
    json_name = f"{basename}.json"
    kw = {"json_name": json_name}
    if title is not None:
        kw["title"] = title
    if preamble is not None:
        kw["preamble"] = preamble
    out_json = run_dir / json_name
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    md = render_results_md(results, rows, **kw)
    (run_dir / md_name).write_text(md, encoding="utf-8")
    if write_beside:
        (BESIDE / json_name).write_text(
            json.dumps(results, indent=2), encoding="utf-8")
        (BESIDE / md_name).write_text(md, encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# selfcheck — witness the corpus's own ground truth (executed, not narrated)
# ---------------------------------------------------------------------------

def selfcheck() -> int:
    bad = 0
    for t in all_tasks():
        buggy = harness.run_witness(t, None)
        fixed = harness.run_witness(t, t.reference_src)
        ok_buggy = buggy.stance.value == "REFUTED"
        ok_fixed = fixed.stance.value == "ATTESTED"
        flag = "ok " if (ok_buggy and ok_fixed) else "BAD"
        print(f"{flag} {t.task_id:<16} planted-bug={buggy.stance.value:<8} "
              f"reference-fix={fixed.stance.value}")
        if not (ok_buggy and ok_fixed):
            bad += 1
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m benchmark.poisoned_pool.run")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="start a run: write state + gen0 prompts")
    p_init.add_argument("--run-dir", required=True, type=Path)
    p_init.add_argument("--gens", type=int, default=4)
    p_init.add_argument("--k-train", type=int, default=2)
    p_init.add_argument("--k-eval", type=int, default=2)
    p_init.add_argument("--exemplars", type=int, default=4)
    p_init.add_argument("--seed", type=int, default=7)
    # Optional synthetic-policy provenance (recorded in config; the live-session
    # path leaves these unset). Knobs describe a weaker/noisier driver.
    p_init.add_argument("--policy", default=None,
                        help="name a synthetic driver to record (e.g. 'noisy')")
    p_init.add_argument("--p-solve-easy", type=float, default=None)
    p_init.add_argument("--p-solve-hard", type=float, default=None)
    p_init.add_argument("--base-bluff", type=float, default=None)
    p_init.add_argument("--contagion", type=float, default=None)

    p_drv = sub.add_parser("drive", help="answer the pending generation with the "
                                         "recorded synthetic policy (provider-free)")
    p_drv.add_argument("--run-dir", required=True, type=Path)

    p_ing = sub.add_parser("ingest", help="adjudicate the pending generation")
    p_ing.add_argument("--run-dir", required=True, type=Path)
    p_ing.add_argument("--allow-missing", action="store_true")

    p_rep = sub.add_parser("report", help="fold state into results.json + RESULTS.md")
    p_rep.add_argument("--run-dir", required=True, type=Path)
    p_rep.add_argument("--write-beside", action="store_true",
                       help="also write the evidence beside the bench package")

    p_st = sub.add_parser("status", help="print the run cursor")
    p_st.add_argument("--run-dir", required=True, type=Path)

    sub.add_parser("selfcheck", help="witness the corpus ground truth")

    a = p.parse_args(argv)
    if a.cmd == "init":
        policy = None
        if a.policy is not None:
            policy = {"name": a.policy}
            for flag, key in (("p_solve_easy", "p_solve_easy"),
                              ("p_solve_hard", "p_solve_hard"),
                              ("base_bluff", "base_bluff"),
                              ("contagion", "contagion")):
                v = getattr(a, flag)
                if v is not None:
                    policy[key] = v
        st = init_run(a.run_dir, gens=a.gens, k_train=a.k_train,
                      k_eval=a.k_eval, m_exemplars=a.exemplars, seed=a.seed,
                      policy=policy)
        print(f"gen0: {len(st['pending'])} prompts emitted -> "
              f"{a.run_dir / 'prompts' / 'gen0'}")
        return 0
    if a.cmd == "drive":
        st = _load_state(a.run_dir)
        pol = dict(st["config"].get("policy") or {"name": "noisy"})
        name = pol.pop("name", "noisy")
        n = drive_run(a.run_dir, policy_name=name, **pol)
        print(json.dumps({"driven_gen": st["current_gen"], "answered": n,
                          "policy": name}, indent=2))
        return 0
    if a.cmd == "ingest":
        st = ingest_run(a.run_dir, allow_missing=a.allow_missing)
        g = st["generations"][-1]
        print(json.dumps({"ingested_gen": g["gen"], "phase": st["phase"],
                          "pending": len(st["pending"]),
                          "S": {"pool": g["S"]["pool_size"],
                                "poison": g["S"]["pool_poison_n"]},
                          "W": {"pool": g["W"]["pool_size"],
                                "poison": g["W"]["pool_poison_n"]}}, indent=2))
        return 0
    if a.cmd == "report":
        results = report_run(a.run_dir, write_beside=a.write_beside)
        print(json.dumps(results["curves"], indent=2))
        return 0
    if a.cmd == "status":
        st = _load_state(a.run_dir)
        print(json.dumps({"phase": st["phase"], "current_gen": st["current_gen"],
                          "pending": len(st["pending"]),
                          "generations_done": len(st["generations"])}, indent=2))
        return 0
    if a.cmd == "selfcheck":
        return selfcheck()
    return 2


if __name__ == "__main__":
    sys.exit(main())
