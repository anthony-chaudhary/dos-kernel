"""Decide whether a self-scoreboard sweep is safe to publish automatically.

The scheduled workflow runs this before touching tracked scoreboard artifacts.
A changed raw-unwitnessed set needs human adjudication, so the only automatic
transition is exact set equality with the committed adjudication records.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def short_set(values: object) -> set[str]:
    if not isinstance(values, list):
        raise ValueError("expected a JSON list of SHAs")
    result: set[str] = set()
    for value in values:
        text = str(value).strip().lower()
        if not text:
            raise ValueError("empty SHA")
        result.add(text[:7])
    return result


def classify(sweep: dict, adjudications: dict) -> dict[str, object]:
    raw = short_set(sweep.get("unwitnessed_shas"))
    records = adjudications.get("records")
    if not isinstance(records, list):
        raise ValueError("adjudications.records must be a list")
    adjudicated = short_set([record.get("sha", "") for record in records])
    return {
        "safe": raw == adjudicated,
        "raw_unwitnessed": sorted(raw),
        "adjudicated": sorted(adjudicated),
        "new": sorted(raw - adjudicated),
        "resolved": sorted(adjudicated - raw),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sweep", required=True, type=Path)
    parser.add_argument("--adjudications", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = classify(
            json.loads(args.sweep.read_text(encoding="utf-8")),
            json.loads(args.adjudications.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"scoreboard-refresh: REFUSED {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, sort_keys=True)
    print(rendered)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"safe={'true' if result['safe'] else 'false'}\n")
            handle.write(f"new={','.join(result['new'])}\n")
            handle.write(f"resolved={','.join(result['resolved'])}\n")
    return 0 if result["safe"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
