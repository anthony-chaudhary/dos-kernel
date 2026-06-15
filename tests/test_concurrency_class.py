"""Concurrency-class budgets as declared data (`dos.concurrency_class`) + the
`dos arbitrate --class-budget` / `[[concurrency_class]]` operator surface — C13.

The arbiter enforcement already ships (test_arbiter.py pins it); these pin the
SURFACE: the data model + validation, the `[[concurrency_class]]` toml reader, the
`--class-budget KIND=N` flag parse, the config layering, and the end-to-end refuse
through `dos arbitrate`.
"""
from __future__ import annotations

import contextlib
import io
import json

import pytest

from dos import concurrency_class as cc
from dos import config as _config


# ── the data model + validation ─────────────────────────────────────────────


def test_concurrency_class_valid():
    c = cc.ConcurrencyClass(name="priority", max_concurrent=3)
    assert c.name == "priority" and c.max_concurrent == 3


def test_concurrency_class_rejects_empty_name():
    with pytest.raises(ValueError):
        cc.ConcurrencyClass(name="", max_concurrent=1)


def test_concurrency_class_rejects_negative():
    with pytest.raises(ValueError):
        cc.ConcurrencyClass(name="x", max_concurrent=-1)


def test_concurrency_class_rejects_bool_as_int():
    # bool is an int subclass; a `max_concurrent = true` in toml is a declaration
    # error, not "1 concurrent".
    with pytest.raises(ValueError):
        cc.ConcurrencyClass(name="x", max_concurrent=True)


def test_zero_budget_is_valid():
    # 0 = admit none of this kind — drastic but legal.
    assert cc.ConcurrencyClass(name="x", max_concurrent=0).max_concurrent == 0


# ── docs/97 §model: rank + region_source fields (back-compat defaulted) ──────


def test_rank_and_region_source_default_to_back_compat():
    # The two new fields default so every existing two-arg construction is unchanged.
    c = cc.ConcurrencyClass(name="priority", max_concurrent=3)
    assert c.rank == 0 and c.region_source is None


def test_rank_rejects_bool():
    # bool is an int subclass; `rank = true` in toml is a declaration error.
    with pytest.raises(ValueError):
        cc.ConcurrencyClass(name="p", max_concurrent=1, rank=True)


def test_rank_rejects_non_int():
    with pytest.raises(ValueError):
        cc.ConcurrencyClass(name="p", max_concurrent=1, rank="2")


def test_region_source_kind_is_carried():
    c = cc.ConcurrencyClass(
        name="priority", max_concurrent=3, rank=4,
        region_source=cc.TopPickablePlanKind())
    assert c.rank == 4
    assert isinstance(c.region_source, cc.TopPickablePlanKind)


# ── docs/97 §model: RegionSource discriminant + parse ───────────────────────


def test_region_source_from_string_known_kinds():
    assert isinstance(cc.region_source_from_string("top_pickable_plan"),
                      cc.TopPickablePlanKind)
    assert isinstance(cc.region_source_from_string("fixed_trees"), cc.FixedTrees)
    assert isinstance(cc.region_source_from_string("whole_workspace"),
                      cc.WholeWorkspace)


def test_region_source_from_string_none_is_none():
    # No declared source → None = "this kind is already on the existing ladder".
    assert cc.region_source_from_string(None) is None
    assert cc.region_source_from_string("") is None


def test_region_source_from_string_unknown_raises():
    # A typo'd discriminant is loud-on-malformed, not a silent drop.
    with pytest.raises(ValueError):
        cc.region_source_from_string("magic_pool")


def test_region_source_from_string_is_case_insensitive():
    assert isinstance(cc.region_source_from_string("Top_Pickable_Plan"),
                      cc.TopPickablePlanKind)


def test_fixed_trees_as_dict_roundtrips():
    ft = cc.FixedTrees((("api", ("api/**",)), ("worker", ("worker/**", "lib/**"))))
    assert ft.as_dict() == {"api": ["api/**"], "worker": ["worker/**", "lib/**"]}


def test_top_pickable_plan_binding_carries_pool_and_callback():
    b = cc.TopPickablePlanBinding(pool=("PA", "PB"),
                                  derive_tree=lambda pid: [f"{pid.lower()}/**"])
    assert b.pool == ("PA", "PB")
    assert b.derive_tree("PA") == ["pa/**"]


# ── as_arbiter_budgets + from_table ─────────────────────────────────────────


def test_as_arbiter_budgets_projects_dict():
    b = cc.ClassBudgets((
        cc.ConcurrencyClass("priority", 3),
        cc.ConcurrencyClass("apply", 1),
    ))
    assert b.as_arbiter_budgets() == {"priority": 3, "apply": 1}


def test_from_table_parses_array_of_tables():
    arr = [
        {"name": "priority", "max_concurrent": 3},
        {"name": "apply", "max_concurrent": 1},
    ]
    b = cc.ClassBudgets.from_table(arr)
    assert b.as_arbiter_budgets() == {"priority": 3, "apply": 1}


def test_from_table_empty_and_none():
    assert cc.ClassBudgets.from_table(None).as_arbiter_budgets() == {}
    assert cc.ClassBudgets.from_table([]).as_arbiter_budgets() == {}


def test_from_table_duplicate_name_last_wins():
    arr = [{"name": "p", "max_concurrent": 3}, {"name": "p", "max_concurrent": 9}]
    assert cc.ClassBudgets.from_table(arr).as_arbiter_budgets() == {"p": 9}


def test_from_table_rejects_non_list():
    with pytest.raises(ValueError):
        cc.ClassBudgets.from_table({"name": "p", "max_concurrent": 1})  # a dict, not array


def test_from_table_rejects_entry_missing_keys():
    with pytest.raises(ValueError):
        cc.ClassBudgets.from_table([{"name": "p"}])  # no max_concurrent
    with pytest.raises(ValueError):
        cc.ClassBudgets.from_table([{"max_concurrent": 1}])  # no name


def test_from_table_rejects_non_table_entry():
    with pytest.raises(ValueError):
        cc.ClassBudgets.from_table(["priority=3"])  # a string, not a table


def test_from_table_reads_rank_and_region_source():
    arr = [{
        "name": "priority", "max_concurrent": 3, "rank": 4,
        "region_source": "top_pickable_plan",
    }]
    b = cc.ClassBudgets.from_table(arr)
    (c,) = b.classes
    assert c.rank == 4
    assert isinstance(c.region_source, cc.TopPickablePlanKind)
    # The budget projection is unchanged by the new fields.
    assert b.as_arbiter_budgets() == {"priority": 3}


def test_from_table_rank_and_region_source_default_when_absent():
    # An entry with only name+max_concurrent keeps the back-compat defaults.
    (c,) = cc.ClassBudgets.from_table([{"name": "p", "max_concurrent": 1}]).classes
    assert c.rank == 0 and c.region_source is None


def test_from_table_rejects_unknown_region_source():
    with pytest.raises(ValueError):
        cc.ClassBudgets.from_table(
            [{"name": "p", "max_concurrent": 1, "region_source": "bogus"}])


# ── parse_cli_budgets (the --class-budget flag) ─────────────────────────────


def test_parse_cli_budgets_ok():
    assert cc.parse_cli_budgets(["priority=3", "apply=1"]) == {"priority": 3, "apply": 1}


def test_parse_cli_budgets_empty():
    assert cc.parse_cli_budgets(None) == {}
    assert cc.parse_cli_budgets([]) == {}


@pytest.mark.parametrize("bad", ["priority", "=3", "priority=", "priority=x", "priority=-1"])
def test_parse_cli_budgets_rejects_malformed(bad):
    with pytest.raises(ValueError):
        cc.parse_cli_budgets([bad])


# ── config layering: [[concurrency_class]] in dos.toml ──────────────────────


def test_config_loads_class_budgets_from_toml(tmp_path):
    (tmp_path / "dos.toml").write_text(
        "[[concurrency_class]]\nname = 'priority'\nmax_concurrent = 2\n\n"
        "[[concurrency_class]]\nname = 'apply'\nmax_concurrent = 1\n",
        encoding="utf-8",
    )
    cfg = _config.load_workspace_config(tmp_path)
    assert cfg.class_budgets.as_arbiter_budgets() == {"priority": 2, "apply": 1}


def test_config_no_table_is_empty_budgets(tmp_path):
    (tmp_path / "dos.toml").write_text("[lanes]\nconcurrent = ['a']\n", encoding="utf-8")
    cfg = _config.load_workspace_config(tmp_path)
    assert cfg.class_budgets.as_arbiter_budgets() == {}


def test_config_default_has_no_budgets():
    # A workspace with no dos.toml → the empty default (today's unbounded behavior).
    assert _config.default_config().class_budgets.as_arbiter_budgets() == {}


# ── end-to-end through `dos arbitrate --class-budget` ───────────────────────


def _arbitrate(*argv):
    from dos import cli

    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = cli.main(["arbitrate", *argv])
    return rc, buf.getvalue().strip(), err.getvalue().strip()


def test_cli_class_budget_is_threaded_into_the_arbiter(tmp_path, monkeypatch):
    # The CLI's job is PLUMBING: it merges config + --class-budget flags and hands
    # the {kind: N} dict to arbiter.arbitrate (whose ENFORCEMENT is pinned by
    # test_arbiter.py, and which bites only on a host-supplied auto_pick_order the
    # generic `dos arbitrate` taxonomy doesn't produce). Spy on the kernel call to
    # prove the flag arrives as the budget dict.
    from dos import arbiter

    seen = {}
    real = arbiter.arbitrate

    def spy(*a, **kw):
        seen["class_budgets"] = kw.get("class_budgets")
        return real(*a, **kw)

    monkeypatch.setattr(arbiter, "arbitrate", spy)
    rc, _out, _err = _arbitrate(
        "--workspace", str(tmp_path), "--lane", "x", "--kind", "keyword",
        "--tree", "src/**", "--leases", "[]",
        "--class-budget", "priority=3", "--class-budget", "apply=1",
    )
    assert rc == 0
    assert seen["class_budgets"] == {"priority": 3, "apply": 1}


def test_cli_flag_overlays_config_budget(tmp_path, monkeypatch):
    # An explicit --class-budget WINS over a [[concurrency_class]] of the same name.
    (tmp_path / "dos.toml").write_text(
        "[[concurrency_class]]\nname = 'priority'\nmax_concurrent = 9\n",
        encoding="utf-8")
    from dos import arbiter

    seen = {}
    real = arbiter.arbitrate
    monkeypatch.setattr(
        arbiter, "arbitrate",
        lambda *a, **kw: (seen.update(b=kw.get("class_budgets")) or real(*a, **kw)))
    _arbitrate(
        "--workspace", str(tmp_path), "--lane", "x", "--kind", "keyword",
        "--tree", "src/**", "--leases", "[]", "--class-budget", "priority=2",
    )
    assert seen["b"] == {"priority": 2}  # flag (2) overlays config (9)


def test_cli_malformed_class_budget_is_contract_error(tmp_path):
    rc, _out, err = _arbitrate(
        "--workspace", str(tmp_path), "--lane", "x", "--kind", "priority",
        "--tree", "src/**", "--leases", "[]", "--class-budget", "priority",  # no =N
    )
    assert rc == 2  # contract_error
    assert "class-budget" in err.lower()


def test_cli_no_budget_flag_is_unchanged(tmp_path):
    # Absent --class-budget and no dos.toml → budgets None → byte-identical to the
    # pre-C13 arbitrate (a clean acquire on a free world).
    rc, out, _err = _arbitrate(
        "--workspace", str(tmp_path), "--lane", "x", "--kind", "keyword",
        "--tree", "src/**", "--leases", "[]",
    )
    assert json.loads(out)["outcome"] == "acquire"
    assert rc == 0
