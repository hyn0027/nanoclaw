"""Read the calls the OneCLI gateway recorded and hand each to its session.

The gateway (GATEWAY_CAPTURE_DIR, see onecli apps/gateway/crates/proxy/src/capture.rs)
appends one JSON line per forwarded call to <capture dir>/<YYYYMMDDHH>.jsonl. Ids sort
in time order. This reader keeps a global cursor (the last id routed), routes each new
call to the active sessions of the agent group it came from (OneCLI's agent
identifier is the NanoClaw agent group id), and deletes hour files once every line in
them has been routed and the hour is over.

A call is attributed to every active session of its group that existed at the time:
the gateway knows the agent, not the session. With one session per group (a DM)
that is exact; otherwise each session's ontology notes the shared attribution.

Calls made by materialize's own discovery probes carry PROBE_HEADER and are skipped.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import CAPTURE_DIR, RUNTIME_DIR

PROBE_HEADER = "x-nanoclaw-ontology-probe"
CURSOR = RUNTIME_DIR / "capture-cursor.json"


def cursor() -> str:
    try:
        return json.loads(CURSOR.read_text())["last_id"] or ""
    except (OSError, ValueError, KeyError):
        return ""


def save_cursor(last_id: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({"last_id": last_id, "updated_at": datetime.now(timezone.utc).isoformat()}) + "\n")


def files() -> list[Path]:
    return sorted(CAPTURE_DIR.glob("[0-9]" * 10 + ".jsonl")) if CAPTURE_DIR.is_dir() else []


def new_records(after: str) -> list[dict]:
    """Complete lines with an id after ``after``, in id order."""
    out = []
    for path in files():
        lines = path.read_text(errors="replace").split("\n")
        for line in lines[:-1]:  # the last piece is empty or a line still being written
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("id", "") > after:
                out.append(record)
    return sorted(out, key=lambda r: r["id"])


def is_probe(record: dict) -> bool:
    headers = (record.get("request") or {}).get("headers") or {}
    return any(k.lower() == PROBE_HEADER for k in headers)


def prune(last_id: str) -> list[str]:
    """Delete hour files that are over and fully routed. Returns their names."""
    current_hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    removed = []
    for path in files():
        if path.stem >= current_hour:
            continue
        ids = []
        for line in path.read_text(errors="replace").splitlines():
            try:
                ids.append(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
        if all(i <= last_id for i in ids):
            path.unlink()
            removed.append(path.name)
    return removed


def route(records: list[dict], sessions: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """session id -> its records; plus counts of what was skipped and why."""
    by_session: dict[str, list[dict]] = {}
    skipped = {"probe": 0, "no_active_session": 0}
    for r in records:
        if is_probe(r):
            skipped["probe"] += 1
            continue
        group = (r.get("agent") or {}).get("identifier")
        targets = [s for s in sessions if s["agent_group_id"] == group and (s["created_at"] or "") <= (r.get("ts") or "9")]
        if not targets:
            skipped["no_active_session"] += 1
            continue
        for s in targets:
            item = dict(r)
            if len(targets) > 1:
                item["shared_attribution"] = [t["id"] for t in targets]
            by_session.setdefault(s["id"], []).append(item)
    return by_session, skipped
