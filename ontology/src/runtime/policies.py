"""OneCLI Cedar policy groups that follow what each agent has loaded, per agent.

OneCLI has two layers of policy groups. The workspace layer (groups naming no single agent,
e.g. ``default`` with the model API) is the operator's and is never touched here. The agent
layer is derived here, per agent (an agent is a NanoClaw agent group; OneCLI gives each its
own identity, ``OneCLI::Agent``):

- A *template* holds the policies for one skill or MCP server, written for any principal.
  Templates live in NanoClaw, not in OneCLI: ``data/ontology-runtime/policy-templates/
  <kind>-<name>.json`` ({name, source, description, policies}), so the workspace layer
  only ever holds what the operator wrote. Edit a template file to change every agent's
  copy (the next reconcile carries it over).
- An *agent copy*, the only thing created in OneCLI: source ``<template source>@<agent-
  group-id>`` and name ``<template name>:<agent-group-id>``, the template's policies with
  the principal narrowed to that agent (``principal == OneCLI::Agent::"<uuid>"``). It is on
  while that agent needs it: one of its active sessions loaded the skill (a Skill tool call,
  or reading its SKILL.md, in the session's action log), or the agent group has the MCP
  server configured with a session active. On means every policy in it is on ("allow every
  operation" of what was loaded); off when the agent no longer needs it. So one agent
  loading a skill never opens anything for another agent. A copy without a template is
  kept and used as is (a group for that agent only); a template is made only for an agent
  that has no copy yet.

A loaded skill or MCP server with no template gets one: an MCP server with a pinned
snapshot (ontology/mcp/sources/<id>.json) from generate_mcp_policies.py (every tool on,
no approval holds); anything else drafted by Claude (`claude -p`) from the skill's files
and its ontology's internet calls. Every policy of a new template is checked by OneCLI's
validator (the gateway's own Cedar parser) before it is saved.
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
TEMPLATE_DIR = RUNTIME_DIR / "policy-templates"
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

    Per agent: an agent is the agent group (one OneCLI identity), so only this group's own
    sessions count, and policy groups are switched per agent (see the module docstring)."""
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


def mark_loaded(group_id: str, session_id: str, skill: str) -> bool:
    """Record that the session's agent is loading ``skill`` now (the blocking sync on a
    Skill call runs before the call reaches the action log). False for a skill that is
    not a NanoClaw container skill (nothing to record: it has no policy group or graph)."""
    if skill not in installed_skills():
        return False
    path = session_dir(group_id, session_id) / "loads.json"
    state = json.loads(path.read_text()) if path.exists() else {"offset": 0, "skills": {}}
    state["skills"].setdefault(skill, now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return True


def desired() -> dict[str, dict]:
    """template source -> {kind, name, ..., agents: {agent_group_id: [why]}} for every
    template some agent's active session needs."""
    want: dict[str, dict] = {}

    def need(source: str, item: dict, agent: str, why: str) -> None:
        want.setdefault(source, {**item, "agents": {}})["agents"].setdefault(agent, []).append(why)

    for s in base.active_sessions():
        ag = s["agent_group_id"]
        for name in skill_loads(ag, s["id"]):
            need(f"skill:{name}", {"kind": "skill", "name": name}, ag, f"{s['id']} loaded it")
        servers = base.fingerprint(ag)["mcp_servers"]
        modeled = base.modeled_mcp(servers)
        for name, endpoint in servers.items():
            sid = modeled.get(name, f"mcp-{name}").removeprefix("mcp-")
            need(f"mcp:{sid}", {"kind": "mcp", "name": sid, "server": name, "endpoint": endpoint}, ag,
                 f"has MCP server {name} ({s['id']} active)")
    return want


# ── per-agent copies ─────────────────────────────────────────────────────────

AGENT_SEP = "@"
HEAD = re.compile(r"\b(permit|forbid)\s*\(\s*principal\s*,")


def template_file(source: str):
    """data/ontology-runtime/policy-templates/<kind>-<name>.json for ``skill:<name>`` / ``mcp:<id>``."""
    kind, _, name = source.partition(":")
    return TEMPLATE_DIR / f"{kind}-{re.sub(r'[^A-Za-z0-9._-]+', '-', name)}.json"


def read_template(source: str) -> dict | None:
    path = template_file(source)
    return json.loads(path.read_text()) if path.exists() else None


def write_template(template: dict) -> None:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    keep = {"name": template["name"], "source": template["source"], "description": template.get("description") or "",
            "policies": [{"name": p["name"], "description": p.get("description"), "policyText": p["policyText"]}
                         for p in template["policies"]]}
    template_file(template["source"]).write_text(json.dumps(keep, indent=2) + "\n")


def is_template(source: str) -> bool:
    return source.startswith(("skill:", "mcp:")) and AGENT_SEP not in source


def copy_source(template_source: str, agent_group: str) -> str:
    return f"{template_source}{AGENT_SEP}{agent_group}"


def scope_to_agent(policy_text: str, agent_uuid: str) -> str:
    """Narrow a policy's principal to one OneCLI agent (a policy already naming a principal is kept)."""
    return HEAD.sub(lambda m: f'{m.group(1)}(principal == OneCLI::Agent::"{agent_uuid}",', policy_text, count=1)


def onecli_agents() -> dict[str, dict]:
    """agent group id -> OneCLI agent {id, identifier, name}."""
    return {a["identifier"]: a for a in api("GET").get("agents", []) if a.get("identifier")}


def copy_body(template: dict, agent_group: str, agent: dict, enabled: bool) -> dict:
    note = f"For agent {agent.get('name') or agent_group} ({agent_group}) only, copied from template {template['name']}. "
    return {
        "name": f"{template['name']}:{agent_group}"[:100],
        "source": copy_source(template["source"], agent_group),
        "description": (note + (template.get("description") or ""))[:500],
        "enabled": enabled,
        "policies": [{"name": p["name"], "description": p.get("description"), "policyText": scope_to_agent(p["policyText"], agent["id"]),
                      "enabled": True} for p in template["policies"]],
    }


def same_content(group: dict, body: dict) -> bool:
    key = lambda ps: sorted((p["name"], p["policyText"], p.get("description") or "") for p in ps)  # noqa: E731
    return group.get("name") == body["name"] and (group.get("description") or "") == body["description"] and key(group["policies"]) == key(body["policies"])


# ── generating a missing group ───────────────────────────────────────────────

CEDAR_REFERENCE = """OneCLI gateway Cedar (one statement per policy; a request is allowed when some policy that is on permits it):
- principal OneCLI::Agent::"<id>" (attributes id, identifier = NanoClaw agent group, name). Use plain `principal`: the group is a template, and NanoClaw narrows the principal to each agent in that agent's copy.
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


def validate_template(template: dict) -> None:
    """Every policy through OneCLI's validator (the gateway's Cedar parser); PolicyError if one is invalid."""
    for p in template["policies"]:
        res = api("POST", "/validate", {"name": f"{template['name']}/{p['name']}", "policyText": p["policyText"]})
        if not res.get("valid"):
            raise PolicyError(f"{p['name']}: {res.get('error')}")


def create_template(source: str, want: dict, log) -> dict:
    """Make and save the template for a skill or MCP server (not a OneCLI group)."""
    name = f"{want['kind']}-{want['name']}"
    snapshot = MCP_DIR / "sources" / f"{want['name']}.json"
    if want["kind"] == "mcp" and snapshot.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("generate_mcp_policies", ROOT / "generate_mcp_policies.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        template = {**mod.generate(want["name"], approval=False, all_on=True), "source": source}
        validate_template(template)
        write_template(template)
        return template
    error = None
    for attempt in range(3):
        drafted = draft_group(want, error)
        suffix = " (drafted by Claude for the runtime ontology; review it)"
        template = {"name": name, "source": source,
                    "description": drafted["description"][: 500 - len(suffix)] + suffix,
                    "policies": drafted["policies"]}
        try:
            validate_template(template)
            write_template(template)
            return template
        except PolicyError as e:
            error = str(e)
            log(f"policies: draft for {source} rejected (attempt {attempt + 1}): {error[:300]}")
    raise PolicyError(f"could not draft a valid template for {source}: {error}")


def move_templates_out(groups: list[dict], changes: list[str]) -> None:
    """Templates once kept as (switched-off) OneCLI groups: save each as a file (unless one
    exists) and delete the group, so OneCLI's workspace layer holds only the operator's groups."""
    for g in groups:
        if not is_template(g.get("source") or ""):
            continue
        if read_template(g["source"]) is None:
            write_template(g)
        api("DELETE", f"/groups/{g['id']}")
        changes.append(f"template {g['name']} moved out of OneCLI to {template_file(g['source']).name}")


# ── reconcile ────────────────────────────────────────────────────────────────

def reconcile(generate: bool, log) -> dict:
    """Bring OneCLI's groups in line with what each agent's active sessions have loaded:
    templates off, one copy per agent that needs it, on while needed. Returns a summary."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUNTIME_DIR / "policies.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    want = desired()
    groups = api("GET")["groups"]
    by_source = {g["source"]: g for g in groups if g.get("source")}
    changes = []

    move_templates_out(groups, changes)

    # A template is needed only for agents that have no copy yet: an agent's existing copy is
    # used as is when its template is gone (e.g. a group kept for one agent only).
    templates = {s: read_template(s) for s in want}
    missing = sorted(s for s, item in want.items() if templates[s] is None
                     and any(copy_source(s, ag) not in by_source for ag in item["agents"]))
    created = []
    if generate:
        for source in missing:
            try:
                g = create_template(source, want[source], log)
                created.append(source)
                templates[source] = g
                changes.append(f"template {g['name']} created: {len(g['policies'])} policies ({template_file(source).name})")
            except (PolicyError, LLMError) as e:
                changes.append(f"template for {source} not created: {e}")

    agents = onecli_agents() if want else {}
    needed_copies = set()
    for source, item in want.items():
        template = templates.get(source)
        for ag, why in item["agents"].items():
            src = copy_source(source, ag)
            needed_copies.add(src)
            if template is None:
                existing = by_source.get(src)
                if existing is not None and not existing["enabled"]:
                    api("PATCH", f"/groups/{existing['id']}", {"enabled": True})
                    changes.append(f"group {existing['name']} on ({'; '.join(why)}; no template, used as is)")
                if existing is not None:
                    for p in [p for p in existing["policies"] if not p["enabled"]]:
                        api("PATCH", f"/policies/{p['id']}", {"enabled": True})
                continue
            agent = agents.get(ag)
            if agent is None:
                changes.append(f"{template['name']} for {ag}: no OneCLI agent for it yet (it has not made a request); not switched on")
                continue
            body = copy_body(template, ag, agent, enabled=True)
            existing = by_source.get(src)
            reason = "; ".join(why)
            if existing is None or not same_content(existing, body):
                api("PUT", "/groups/sync", body)
                changes.append(f"group {body['name']} {'created' if existing is None else 'updated from template'} and on: "
                               f"{len(body['policies'])} policies for {agent.get('name') or ag} ({reason})")
                continue
            if not existing["enabled"]:
                api("PATCH", f"/groups/{existing['id']}", {"enabled": True})
                changes.append(f"group {existing['name']} on ({reason})")
            off = [p for p in existing["policies"] if not p["enabled"]]
            for p in off:
                api("PATCH", f"/policies/{p['id']}", {"enabled": True})
            if off:
                changes.append(f"group {existing['name']}: {len(off)} policies switched on (every operation allowed)")

    for g in groups:
        source = g.get("source") or ""
        if source.startswith(("skill:", "mcp:")) and AGENT_SEP in source and source not in needed_copies and g["enabled"]:
            api("PATCH", f"/groups/{g['id']}", {"enabled": False})
            changes.append(f"group {g['name']} off (its agent no longer needs it)")

    for c in changes:
        log(f"policies: {c}")
    summary = {"at": now(), "desired": want, "missing": [s for s in missing if s not in created], "changes": changes}
    STATE.write_text(json.dumps(summary, indent=2) + "\n")
    return summary
