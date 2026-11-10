"""OneCLI Cedar policy groups that follow what the agent has loaded.

Only the always-on groups (no ``source`` key, e.g. ``default`` with the model API) are on
by default. A group whose source is ``skill:<name>`` is on while some active session has
loaded that skill (the agent invoked it: a Skill tool call, or reading its SKILL.md, seen
in the session's action log); a group whose source is ``mcp:<id>`` is on while some
active session's agent group has that MCP server configured. Switching a group on also
switches every policy in it on ("allow every operation" of what was loaded). When no
active session needs a group any more (session closed, server removed), it is switched
off. Groups without a skill:/mcp: source are never touched.

A loaded skill or MCP server with no group gets one: an MCP server with a pinned
snapshot (ontology/mcp/sources/<id>.json) from generate_mcp_policies.py (every tool on,
no approval holds); anything else drafted by Claude (`claude -p`) from the skill's files
and its ontology's internet calls, validated by OneCLI when it is saved.

FUTURE WORK: policies are workspace-wide, so a group switched on for one agent is on
for every agent in the workspace (this is a single-agent prototype). Per-agent groups
(policies naming the agent as principal, switched per agent) are the intended next step.
"""

import fcntl
import json
import os
import re
import urllib.error
import urllib.request

from rdflib import Graph

from ..common.namespaces import AR
from ..common.paths import MCP_DIR, ROOT
from ..materialize.gateway_policy import api_key
from . import base
from .llm import LLMError, call
from .paths import NANOCLAW, RUNTIME_DIR, SKILLS_SRC, session_dir
from .store import now

API = os.environ.get("ONECLI_URL", "http://127.0.0.1:10256").rstrip("/") + "/v1/cedar-policy"
STATE = RUNTIME_DIR / "policies.json"
SKILL_FILE = re.compile(r"skills/([A-Za-z0-9_.-]+)/SKILL\.md")


class PolicyError(RuntimeError):
    pass


def api(method: str, path: str = "", body: object | None = None) -> object:
    key = api_key(NANOCLAW / ".env")
    if not key:
        raise PolicyError("no ONECLI_API_KEY (environment or NanoClaw .env)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except ValueError:
            detail = e.reason
        raise PolicyError(f"{method} {path or '/'}: HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise PolicyError(f"cannot reach {API}: {e.reason}") from e


# ── what is loaded ───────────────────────────────────────────────────────────

def loads_in_lines(lines, known: set[str]) -> dict[str, str]:
    """Skills loaded in these action-log lines: name -> first time. A load is a Skill tool
    call, or any tool input naming a skill's SKILL.md (Read, Bash cat, ...)."""
    found: dict[str, str] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") != "tool_start":
            continue
        data = rec.get("data") or {}
        names = []
        if data.get("tool") == "Skill":
            names.append(str((data.get("input") or {}).get("skill", "")).split(":")[-1])
        names += SKILL_FILE.findall(json.dumps(data.get("input") or {}))
        for name in names:
            if name in known and name not in found:
                found[name] = rec.get("ts") or now()
    return found


def installed_skills() -> set[str]:
    return {p.name for p in SKILLS_SRC.iterdir() if p.is_dir()} if SKILLS_SRC.is_dir() else set()


def skills_ever_loaded(group_id: str, session_id: str | None = None) -> dict[str, dict]:
    """Skills loaded in any session of the agent group (closed ones included), or only in
    ``session_id``: name -> {"first": ts, "session": id}. Reads the action logs whole.

    FUTURE WORK: this assumes a single agent (the current prototype). With several agents,
    "ever loaded" will be tracked per agent, so one agent's loads never widen what is
    materialized (or switched on) for another; see the per-agent note in this module's docstring."""
    root = NANOCLAW / "logs" / "sessions" / group_id
    logs = [root / session_id / "actions.jsonl"] if session_id else sorted(root.glob("*/actions.jsonl"))
    known, out = installed_skills(), {}
    for log in logs:
        if not log.exists():
            continue
        with open(log, errors="replace") as f:
            for name, ts in loads_in_lines(f, known).items():
                if name not in out or ts < out[name]["first"]:
                    out[name] = {"first": ts, "session": log.parent.name}
    return out


def skill_loads(group_id: str, session_id: str) -> dict[str, str]:
    """Skills the session's agent has loaded: name -> first time seen. Reads the session's
    action log incrementally (offset kept in <session>/ontology/loads.json, written only here)."""
    path = session_dir(group_id, session_id) / "loads.json"
    state = json.loads(path.read_text()) if path.exists() else {"offset": 0, "skills": {}}
    log = NANOCLAW / "logs" / "sessions" / group_id / session_id / "actions.jsonl"
    if not log.exists():
        return state["skills"]
    with open(log, "rb") as f:
        f.seek(state["offset"] if state["offset"] <= log.stat().st_size else 0)
        chunk = f.read()
    complete = chunk[: chunk.rfind(b"\n") + 1]
    for name, ts in loads_in_lines(complete.decode(errors="replace").splitlines(), installed_skills()).items():
        state["skills"].setdefault(name, ts)
    state["offset"] = (state["offset"] if state["offset"] <= log.stat().st_size else 0) + len(complete)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return state["skills"]


def desired() -> dict[str, dict]:
    """source -> {kind, name, why[]} for every group some active session needs."""
    want: dict[str, dict] = {}
    for s in base.active_sessions():
        for name in skill_loads(s["agent_group_id"], s["id"]):
            want.setdefault(f"skill:{name}", {"kind": "skill", "name": name, "why": []})["why"].append(f"{s['id']} loaded it")
        servers = base.fingerprint(s["agent_group_id"])["mcp_servers"]
        modeled = base.modeled_mcp(servers)
        for name, endpoint in servers.items():
            sid = modeled.get(name, f"mcp-{name}").removeprefix("mcp-")
            want.setdefault(f"mcp:{sid}", {"kind": "mcp", "name": sid, "server": name, "endpoint": endpoint, "why": []})["why"].append(
                f"{s['agent_group_id']} has MCP server {name} ({s['id']} active)")
    return want


# ── generating a missing group ───────────────────────────────────────────────

CEDAR_REFERENCE = """OneCLI gateway Cedar (one statement per policy; a request is allowed when some policy that is on permits it):
- principal OneCLI::Agent::"<id>" (attributes id, identifier = NanoClaw agent group, name). Use plain `principal` (workspace-wide).
- action OneCLI::Action::"GET" | "POST" | ... (exactly the HTTP method); several: action in [OneCLI::Action::"GET", OneCLI::Action::"HEAD"]; any: plain `action`.
- resource OneCLI::Host::"api.example.com" (attribute name; wildcard: `resource` with `when { resource.name like "*.example.com" }`).
- context: method, host, port, path, query (record), query_string, headers (record, lower-case), has_body, body, json, body_truncated,
  has_credential, is_llm_host. Guard optional parts with `has` (context has json && context.json has method && ...).
- annotations: @approval (hold for a human), @rate_limit("60/minute"). Do NOT use @approval here.
Examples:
permit(principal, action, resource == OneCLI::Host::"api.vercel.com");
permit(principal, action in [OneCLI::Action::"GET", OneCLI::Action::"HEAD", OneCLI::Action::"OPTIONS"], resource) when { resource.name like "*.vercel.app" };
permit(principal, action == OneCLI::Action::"POST", resource == OneCLI::Host::"api.githubcopilot.com") when {
  context.path like "/mcp*" && context has json && context.json has method && context.json.method == "tools/call" &&
  context.json has params && context.json.params has name && context.json.params.name == "issue_write" };"""

GROUP_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["description", "policies"],
    "properties": {
        "description": {"type": "string"},
        "policies": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description", "policyText"],
            "properties": {"name": {"type": "string", "description": "kebab-case, unique in the group"},
                           "description": {"type": "string"}, "policyText": {"type": "string", "description": "exactly one Cedar permit statement"}}}},
    },
}

GROUP_SYSTEM = """You write OneCLI gateway Cedar policies for one AI-agent skill or MCP server. The gateway denies every \
outbound request no policy permits. The group you write is switched on only while the agent has loaded this skill \
or server, and it should allow every operation the skill or server legitimately needs (initially permissive within \
its own destinations: all methods it may use on its hosts), and nothing beyond them. Never permit all hosts. No \
@approval. Prefer one policy per destination or purpose, named so an operator recognizes it.

Permit only destinations the skill itself must reach to do its own job: the hosts its scripts, CLIs or documented \
workflow call. Hosts that appear only as examples or illustrations (sample curl lines, "e.g. Gmail, GitHub, Stripe") \
are not needs. Never write a catch-all such as "any host the gateway holds a credential for" or "any connected \
service": access to other services is granted by their own skills and MCP servers, never by this group. A skill that \
only explains infrastructure or conventions (how the credential proxy works, how to format messages, how to behave) \
grants nothing. If it needs no network access of its own, return no policies and say so in the description."""


def skill_endpoints(name: str) -> list[str]:
    """Internet call patterns the skill's reviewed ontology declares (empty when it has none)."""
    from ..materialize.model import add_skill_graphs
    g = Graph()
    add_skill_graphs(g, [name])
    return sorted({str(p) for p in g.objects(None, AR.callEndpointPattern)})


def draft_group(want: dict, error: str | None = None) -> dict:
    from .cli import skill_evidence
    if want["kind"] == "skill":
        subject = f"NanoClaw container skill '{want['name']}'"
        evidence = skill_evidence(want["name"])
        endpoints = skill_endpoints(want["name"])
        extra = f"Destinations its reviewed ontology declares: {', '.join(endpoints) or 'none'}."
    else:
        subject = f"MCP server '{want['server']}' at {want['endpoint']}"
        evidence = "(no pinned tool snapshot; permit the MCP transport on its endpoint: JSON-RPC POST, plus GET and DELETE for streamable HTTP)"
        extra = ""
    prompt = "\n\n".join(x for x in [
        f"# Write the policy group for the {subject}", extra, CEDAR_REFERENCE,
        "# Its files" if want["kind"] == "skill" else "# Its endpoint", evidence,
        f"# The previous attempt was rejected by OneCLI's validator:\n{error}" if error else "",
    ] if x)
    out, _usage = call(GROUP_SYSTEM, prompt, GROUP_SCHEMA)
    return out


def create_group(source: str, want: dict, log) -> dict:
    name = f"{want['kind']}-{want['name']}"
    snapshot = MCP_DIR / "sources" / f"{want['name']}.json"
    if want["kind"] == "mcp" and snapshot.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("generate_mcp_policies", ROOT / "generate_mcp_policies.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        group = mod.generate(want["name"], approval=False, all_on=True)
        return api("PUT", "/groups/sync", {**group, "enabled": True})
    error = None
    for attempt in range(3):
        drafted = draft_group(want, error)
        body = {"name": name, "source": source, "enabled": True,
                "description": f"{drafted['description']} (drafted by Claude for the runtime ontology; review it)",
                "policies": [{**p, "enabled": True} for p in drafted["policies"]]}
        try:
            return api("PUT", "/groups/sync", body)
        except PolicyError as e:
            error = str(e)
            log(f"policies: draft for {source} rejected (attempt {attempt + 1}): {error[:300]}")
    raise PolicyError(f"could not draft a valid group for {source}: {error}")


# ── reconcile ────────────────────────────────────────────────────────────────

def reconcile(generate: bool, log) -> dict:
    """Bring OneCLI's groups in line with what active sessions have loaded. Returns a summary."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUNTIME_DIR / "policies.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    want = desired()
    groups = api("GET")["groups"]
    by_source = {g["source"]: g for g in groups if g.get("source")}
    changes = []
    for g in groups:
        source = g.get("source") or ""
        if not source.startswith(("skill:", "mcp:")):
            continue
        on = source in want
        if g["enabled"] != on:
            api("PATCH", f"/groups/{g['id']}", {"enabled": on})
            changes.append(f"group {g['name']} {'on' if on else 'off'}" + (f" ({'; '.join(want[source]['why'])})" if on else " (no active session needs it)"))
        if on:
            off = [p for p in g["policies"] if not p["enabled"]]
            for p in off:
                api("PATCH", f"/policies/{p['id']}", {"enabled": True})
            if off:
                changes.append(f"group {g['name']}: {len(off)} policies switched on (every operation allowed)")
    missing = sorted(set(want) - set(by_source))
    created = []
    if generate:
        for source in missing:
            try:
                g = create_group(source, want[source], log)
                created.append(source)
                changes.append(f"group {g['name']} created and on: {len(g['policies'])} policies ({'; '.join(want[source]['why'])})")
            except (PolicyError, LLMError) as e:
                changes.append(f"group for {source} not created: {e}")
    for c in changes:
        log(f"policies: {c}")
    summary = {"at": now(), "desired": want, "missing": [s for s in missing if s not in created], "changes": changes}
    STATE.write_text(json.dumps(summary, indent=2) + "\n")
    return summary
