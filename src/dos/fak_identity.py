"""Read-only join from a DOS run/trace id to fak's transcript UUID ledger."""
from __future__ import annotations

import json
import os
from pathlib import Path


def transcript_uuid_for_trace(trace_id: str, *, workspace: Path, env: dict[str, str] | None = None) -> str:
    """Return the latest UUID mapped to *trace_id*, or the honest empty floor."""
    env = os.environ if env is None else env
    reg_dir = Path(env.get("FLEET_REG_DIR") or (workspace / "tools" / "_registry"))
    path = reg_dir / "session_identity.jsonl"
    found = ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if str(row.get("trace", "")).strip() == trace_id:
                    found = str(row.get("uuid", "")).strip()
    except OSError:
        return ""
    return found
