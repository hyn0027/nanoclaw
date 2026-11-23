"""What a session's agent group is built from, and what changed since the last look.

The fingerprint names the session's skills (with their SKILL.md hashes), whether
Claude Code's bundled skills are on, its MCP servers, packages, and provider. It is
read straight from the central DB (read-only) and the checkout: no container needed.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from rdflib import Graph

from ..materialize.model import add_mcp_graphs, skill_directories
from .paths import CENTRAL_DB, SKILLS_SRC, group_settings


def central(sql: str, *params) -> list[dict]:
    con = sqlite3.connect(f"file:{CENTRAL_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def active_sessions() -> list[dict]:
    return central("SELECT id, agent_group_id, created_at, container_status FROM sessions WHERE status = 'active' ORDER BY created_at")


def latest_session() -> dict | None:
    """The most recently created session, preferring active ones."""
    rows = central("SELECT id, agent_group_id, status, created_at, container_status FROM sessions "
                   "ORDER BY status = 'active' DESC, created_at DESC LIMIT 1")
    return rows[0] if rows else None


def session(session_id: str) -> dict | None:
    rows = central("SELECT id, agent_group_id, status, created_at, container_status FROM sessions WHERE id = ?", session_id)
    return rows[0] if rows else None


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def bundled_skills(group_id: str) -> str:
    """'disabled' when the group's Claude settings turn Claude Code's bundled skills off."""
    path = group_settings(group_id)
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError):
        return "unknown"
    off = settings.get("disableBundledSkills") is True or (settings.get("env") or {}).get("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS") in ("1", "true")
    return "disabled" if off else "enabled"


def granted_credentials(group_id: str) -> dict | None:
    """OneCLI credentials granted to the group's agent: name -> id, updatedAt, hostPattern
    (never values). None when OneCLI's database cannot be read."""
    from ..materialize.gateway_policy import granted_secrets, read_gateway_policy
    try:
        policy = read_gateway_policy()
    except Exception:  # noqa: BLE001 - unreachable OneCLI must not stop a sync
        return None
    if policy is None:
        return None
    agent = next((a for a in policy.get("agents", []) if a["identifier"] == group_id), None)
    if not agent:
        return {}
    return {s["name"]: {"id": s["id"], "updatedAt": str(s.get("updatedAt")), "hostPattern": s.get("hostPattern")}
            for s in granted_secrets(policy, agent["id"])}


def fingerprint(group_id: str) -> dict:
    rows = central("SELECT * FROM container_configs WHERE agent_group_id = ?", group_id)
    cfg = rows[0] if rows else {}
    selection = json.loads(cfg.get("skills") or '"all"')
    available = sorted(p.name for p in SKILLS_SRC.iterdir() if p.is_dir()) if SKILLS_SRC.is_dir() else []
    names = available if selection == "all" else [s for s in selection if s in available]
    mcp = json.loads(cfg.get("mcp_servers") or "{}")
    return {
        "skills": {n: sha(SKILLS_SRC / n / "SKILL.md") for n in names},
        "bundled_skills": bundled_skills(group_id),
        "mcp_servers": {n: (s.get("url") or " ".join([s.get("command", ""), *s.get("args", [])]).strip()) for n, s in mcp.items()},
        "packages": {"apt": json.loads(cfg.get("packages_apt") or "[]"), "npm": json.loads(cfg.get("packages_npm") or "[]")},
        "credentials": granted_credentials(group_id),
        "provider": cfg.get("provider") or "claude",
        "model": cfg.get("model"),
    }


def modeled_skills() -> set[str]:
    return set(skill_directories())


def modeled_mcp(servers: dict[str, str]) -> dict[str, str]:
    """Configured MCP server name -> reviewed graph's server id, for those that have one."""
    return add_mcp_graphs(Graph(), {n: {"url": u} for n, u in servers.items()})


def diff(old: dict | None, new: dict) -> list[dict]:
    """Changes from ``old`` to ``new``, one entry each. Everything is 'added' the first time."""
    old = old or {"skills": {}, "mcp_servers": {}, "packages": {"apt": [], "npm": []}, "bundled_skills": None, "provider": None, "model": None}
    out = []
    for name in sorted(set(old["skills"]) | set(new["skills"])):
        if name not in old["skills"]:
            out.append({"what": "skill", "change": "added", "name": name})
        elif name not in new["skills"]:
            out.append({"what": "skill", "change": "removed", "name": name})
        elif old["skills"][name] != new["skills"][name]:
            out.append({"what": "skill", "change": "changed", "name": name})
    for name in sorted(set(old["mcp_servers"]) | set(new["mcp_servers"])):
        if name not in old["mcp_servers"]:
            out.append({"what": "mcp", "change": "added", "name": name, "endpoint": new["mcp_servers"][name]})
        elif name not in new["mcp_servers"]:
            out.append({"what": "mcp", "change": "removed", "name": name})
        elif old["mcp_servers"][name] != new["mcp_servers"][name]:
            out.append({"what": "mcp", "change": "changed", "name": name, "endpoint": new["mcp_servers"][name]})
    for manager in ("apt", "npm"):
        before, after = set(old["packages"].get(manager, [])), set(new["packages"].get(manager, []))
        out += [{"what": "package", "change": "added", "name": f"{manager}:{p}"} for p in sorted(after - before)]
        out += [{"what": "package", "change": "removed", "name": f"{manager}:{p}"} for p in sorted(before - after)]
    before, after = old.get("credentials") or {}, new.get("credentials")
    if after is not None:  # None: OneCLI unreadable (and no earlier reading), compare next time
        for name in sorted(set(before) | set(after)):
            if name not in before:
                out.append({"what": "credential", "change": "added", "name": name, "endpoint": after[name]["hostPattern"]})
            elif name not in after:
                out.append({"what": "credential", "change": "removed", "name": name})
            elif before[name] != after[name]:
                out.append({"what": "credential", "change": "changed", "name": name, "endpoint": after[name]["hostPattern"]})
    if old.get("bundled_skills") != new["bundled_skills"]:
        out.append({"what": "bundled_skills", "change": new["bundled_skills"], "name": "claude-code"})
    for key in ("provider", "model"):
        if old.get(key) != new.get(key):
            out.append({"what": key, "change": "changed", "name": str(new.get(key))})
    return out
