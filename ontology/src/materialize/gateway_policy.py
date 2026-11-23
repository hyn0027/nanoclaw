"""OneCLI gateway policy: the workspace's Cedar policies and credential grants.

Targets the local OneCLI 2.14 build (branch `local-2.14.0`), where Cedar is the
gateway's only decision engine. A request passes when at least one enabled
`permit` policy in the agent's workspace matches and no enabled `forbid` does;
everything else is denied, credentialed or not, LLM hosts included, and
requests without an agent identity are refused. Each policy is one Cedar
statement stored as its own row (`cedar_policy_rules`) in a group
(`cedar_policy_groups`); it applies only when it and its group are both on, and
the gateway names it "group/policy". The action is exactly
the HTTP method (`OneCLI::Action::"GET"`), the resource the host
(`OneCLI::Host::"<host>"`). OneCLI's own `policy_rules_v2` rules only decide
which stored secrets the gateway injects for which agents (grants); they never
allow a request.

Everything is read (read-only) from OneCLI's Postgres: the 2.14 CLI needs a
user login and no longer lists rules. Secret values, 1Password references, and
agent access tokens are never selected.

Policies are summarized (``parse_policy``): the policy head and the common `when`
clauses (`resource.name like`, `context.path like`, `context.method ==`,
`principal.identifier ==`) are parsed into a host/method/path label; any other
condition marks the policy "conditional" and is treated as applying, so the
results stay may-claims. A concrete request can also be put to the gateway's own
evaluator (``evaluate``).
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .runtime import tool

POSTGRES_CONTAINER = "onecli-postgres-1"
GATEWAY_CONTAINER = "onecli-gateway"

POLICY_QUERY = """
select json_build_object(
  'cedarTable', to_regclass('public.cedar_policy_rules') is not null,
  'agents', coalesce((select json_agg(json_build_object(
      'id', a.id, 'name', a.name, 'identifier', a.identifier, 'workspaceId', a.workspace_id))
    from agents a), '[]'::json),
  'secrets', coalesce((select json_agg(json_build_object(
      'id', s.id, 'name', s.name, 'type', s.type, 'hostPattern', s.host_pattern, 'pathPattern', s.path_pattern,
      'scope', s.scope, 'workspaceId', s.workspace_id, 'updatedAt', s.updated_at))
    from secrets s), '[]'::json),
  'grants', coalesce((select json_agg(json_build_object(
      'name', r.name, 'workspaceId', r.workspace_id,
      'agentIds', (select coalesce(json_agg(i.agent_id), '[]'::json) from policy_rule_identities i where i.rule_id = r.id and i.agent_id is not null),
      'viaUserOrGroup', exists(select 1 from policy_rule_identities i where i.rule_id = r.id and (i.user_id is not null or i.group_id is not null)),
      'targets', (select coalesce(json_agg(json_build_object(
          'kind', t.kind, 'secretId', t.secret_id, 'secretScope', t.secret_scope)), '[]'::json)
        from policy_rule_targets t where t.rule_id = r.id)))
    from policy_rules_v2 r
    where r.status = 'published' and r.enabled and not r.is_default and r.action = 'allow'
      and r.generation = (select max(generation) from policy_rules_v2 g
                          where g.status = 'published' and g.scope = r.scope
                            and g.workspace_id is not distinct from r.workspace_id
                            and g.organization_id is not distinct from r.organization_id)), '[]'::json),
  'groupsTable', to_regclass('public.cedar_policy_groups') is not null);
"""


def read_strict_deny() -> bool:
    """Whether the gateway container runs with GATEWAY_STRICT_DENY enabled."""
    argv = [tool("docker"), "inspect", GATEWAY_CONTAINER, "--format", "{{range .Config.Env}}{{println .}}{{end}}"]
    try:
        env = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=30).stdout.splitlines()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    value = next((line.split("=", 1)[1] for line in env if line.startswith("GATEWAY_STRICT_DENY=")), "")
    return value.strip().lower() in ("1", "true", "yes")


# Policy groups materialize treats as if they did not exist (set from the CLI). The
# default is the always-on `default` group (the model API): the analysis is about what
# the agent's skills and servers open, not the infrastructure every agent needs.
DEFAULT_IGNORED_GROUPS = ("default",)
_ignored_groups: set[str] = set()


def set_ignored_groups(names) -> None:
    _ignored_groups.clear()
    _ignored_groups.update(names)


def _ignored(policy_id: str | None) -> bool:
    return bool(policy_id) and policy_id.split("/", 1)[0] in _ignored_groups


def read_gateway_policy() -> dict | None:
    """{"cedarTable", "strict", "agents", "secrets", "grants", "policies", "ignoredPolicies"}, or None
    when the database is unreachable. Each policy carries its parsed ``scope`` (``parse_policy``);
    policies of ignored groups (``set_ignored_groups``) are moved to "ignoredPolicies"."""
    argv = [
        tool("docker"), "exec", "-i", POSTGRES_CONTAINER, "sh", "-c",
        'psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-onecli}" -At -v ON_ERROR_STOP=1',
    ]
    try:
        out = subprocess.run(argv, input=POLICY_QUERY, check=True, capture_output=True, text=True, timeout=30).stdout
        policy = json.loads(out.strip())
        policy["policies"] = read_policies(argv, policy["groupsTable"]) if policy["cedarTable"] else []
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    policy["strict"] = read_strict_deny()
    policy["ignoredPolicies"] = [p for p in policy["policies"] if p.get("group") in _ignored_groups]
    policy["policies"] = [p for p in policy["policies"] if p.get("group") not in _ignored_groups]
    for p in policy["policies"]:
        p["scope"] = parse_policy(p["text"])
    return policy


# Policies apply only when switched on in a group that is switched on; the gateway names them "group/policy".
GROUPED_POLICIES = """
select coalesce(json_agg(json_build_object(
    'name', g.name || '/' || c.name, 'group', g.name, 'groupSource', g.source, 'description', c.description,
    'text', c.policy_text, 'enabled', c.enabled and g.enabled, 'workspaceId', c.workspace_id,
    -- Two-layer policies (OneCLI local migration 20261113000000): agents that do not use
    -- this workspace-wide group. Read via to_jsonb so older databases without the column work.
    'excludedAgentIds', coalesce(to_jsonb(g) -> 'excluded_agent_ids', '[]'::jsonb))
  order by g.position, g.created_at, c.position, c.created_at), '[]'::json)
from cedar_policy_rules c join cedar_policy_groups g on g.id = c.group_id;
"""

# Before policy groups (migration 20261017000000_cedar_policy_groups).
UNGROUPED_POLICIES = """
select coalesce(json_agg(json_build_object(
    'name', c.name, 'group', null, 'groupSource', null, 'description', c.description,
    'text', c.policy_text, 'enabled', c.enabled, 'workspaceId', c.workspace_id)
  order by c.position, c.created_at), '[]'::json)
from cedar_policy_rules c;
"""


def read_policies(argv: list[str], grouped: bool) -> list[dict]:
    out = subprocess.run(argv, input=GROUPED_POLICIES if grouped else UNGROUPED_POLICIES, check=True, capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip())


GATEWAY_API = os.environ.get("ONECLI_GATEWAY_API", "http://127.0.0.1:10255")
_evaluations: dict[tuple, dict | None] = {}


def api_key(nanoclaw_env: Path) -> str | None:
    """OneCLI API key: $ONECLI_API_KEY, else the NanoClaw checkout's .env. Used only to
    authenticate to the gateway; never printed or recorded."""
    if os.environ.get("ONECLI_API_KEY"):
        return os.environ["ONECLI_API_KEY"]
    try:
        for line in nanoclaw_env.read_text().splitlines():
            if line.startswith("ONECLI_API_KEY="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def evaluate(agent_id: str, method: str, host: str, path: str, body: str,
             has_credential: bool, nanoclaw_env: Path) -> dict | None:
    """The gateway's own decision for a described request (POST /v1/cedar/evaluate):
    {"decision": "allow" | "approval" | "deny", "policy": Cedar id or None, "reason": ...}.
    None when the gateway cannot be asked. Nothing is forwarded or rate-counted."""
    key = (agent_id, method, host, path, body, has_credential)
    if key in _evaluations:
        return _evaluations[key]
    token = api_key(nanoclaw_env)
    result = None
    if token:
        req = urllib.request.Request(f"{GATEWAY_API}/v1/cedar/evaluate", method="POST", data=json.dumps({
            "agent_id": agent_id, "method": method, "host": host, "path": path, "body": body, "has_credential": has_credential,
        }).encode(), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                result = json.loads(res.read())
        except (urllib.error.URLError, ValueError, TimeoutError):
            result = None
    # The gateway decides with every enabled policy; a request only an ignored group
    # permits counts as denied. (The gateway names one deciding policy, so a request
    # another permit would also allow can be undercounted when that one is ignored.)
    if result and result.get("decision") in ("allow", "approval") and _ignored(result.get("policy")):
        result = {"decision": "deny", "policy": None, "reason": f"permitted only by ignored policy {result['policy']}"}
    _evaluations[key] = result
    return result


# ── Cedar summary ────────────────────────────────────────────────────────────

STRING = r'"((?:[^"\\]|\\.)*)"'
ENTITY = lambda kind: rf'OneCLI::{kind}::{STRING}'  # noqa: E731


def strip_comments(text: str) -> str:
    """Drop // comments outside string literals."""
    out, in_str, i = [], False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` outside brackets, braces, parentheses, and strings."""
    parts, depth, in_str, cur, i = [], 0, False, [], 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                cur.append(text[i:i + 2])
                i += 2
                continue
            in_str = c != '"'
        elif c == '"':
            in_str = True
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append("".join(cur).strip())
            cur, i = [], i + len(sep)
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur).strip())
    return parts


def matching_close(text: str, start: int) -> int:
    """Index of the bracket closing the one at ``start``."""
    depth, in_str, i = 0, False, start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def parse_policy(text: str) -> dict:
    """Summary of one Cedar statement.

    {"effect", "approval", "rate_limit", "methods" (None = any), "host" (Cedar `like`
    pattern, None = any), "path", "agent_ids", "identifiers", "workspace", "conditional",
    "parsed"}. ``parsed`` is False when the statement could not be read at all.
    """
    s = strip_comments(text).strip()
    scope = {"effect": None, "approval": False, "rate_limit": None, "methods": None, "host": None, "path": None,
             "agent_ids": [], "identifiers": [], "workspace": None, "conditional": False, "parsed": False}
    for name, arg in re.findall(rf'@(\w+)(?:\(\s*{STRING}\s*\))?', s):
        if name == "approval":
            scope["approval"] = True
        elif name == "rate_limit":
            scope["rate_limit"] = arg
    m = re.search(r'\b(permit|forbid)\s*\(', s)
    if not m:
        return scope
    scope["effect"] = m.group(1)
    close = matching_close(s, m.end() - 1)
    head = split_top(s[m.end():close])
    if len(head) != 3:
        return scope
    principal, action, resource = head
    if (pm := re.fullmatch(rf'principal\s*==\s*{ENTITY("Agent")}', principal)):
        scope["agent_ids"].append(pm.group(1))
    elif (pm := re.fullmatch(rf'principal\s+in\s+{ENTITY("Workspace")}', principal)):
        scope["workspace"] = pm.group(1)
    elif principal != "principal":
        scope["conditional"] = True
    if (am := re.fullmatch(rf'action\s*==\s*{ENTITY("Action")}', action)):
        scope["methods"] = [am.group(1)]
    elif (am := re.fullmatch(r'action\s+in\s*\[(.*)\]', action, re.S)):
        scope["methods"] = re.findall(ENTITY("Action"), am.group(1))
    elif action != "action":
        scope["conditional"] = True
    if (rm := re.fullmatch(rf'resource\s*==\s*{ENTITY("Host")}', resource)):
        scope["host"] = rm.group(1)
    elif resource != "resource":
        scope["conditional"] = True

    rest = s[close + 1:].strip().rstrip(";").strip()
    while rest:
        cm = re.match(r'(when|unless)\s*\{', rest)
        if not cm:
            scope["conditional"] = True
            break
        end = matching_close(rest, cm.end() - 1)
        body, rest = rest[cm.end():end], rest[end + 1:].strip()
        if cm.group(1) == "unless" or "||" in body:
            scope["conditional"] = True
            continue
        for clause in split_top(body, "&&"):
            if not clause:
                continue
            if (h := re.fullmatch(rf'(?:resource\.name|context\.host)\s*(==|like)\s*{STRING}', clause)) and scope["host"] is None:
                scope["host"] = h.group(2)
            elif (p := re.fullmatch(rf'context\.path\s*(==|like)\s*{STRING}', clause)) and scope["path"] is None:
                scope["path"] = p.group(2)
            elif (mm := re.fullmatch(rf'context\.method\s*==\s*{STRING}', clause)) and scope["methods"] is None:
                scope["methods"] = [mm.group(1)]
            elif (i := re.fullmatch(rf'principal\.identifier\s*==\s*{STRING}', clause)):
                scope["identifiers"].append(i.group(1))
            elif (i := re.fullmatch(rf'principal\.id\s*==\s*{STRING}', clause)):
                scope["agent_ids"].append(i.group(1))
            else:
                scope["conditional"] = True
    scope["parsed"] = True
    return scope


def cedar_like(value: str, pattern: str) -> bool:
    """Cedar `like`: `*` matches any characters, everything else is literal."""
    return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), value, re.S) is not None


def patterns_overlap(a: str | None, b: str | None) -> bool:
    """Whether two host patterns (None = any host) can match a common host."""
    if a is None or b is None:
        return True
    return cedar_like(a, b) or cedar_like(b, a) or ("*" in a and "*" in b)


# ── What applies to this agent ───────────────────────────────────────────────

def agent_record(policy: dict | None, agent_id: str | None) -> dict | None:
    if not policy or not agent_id:
        return None
    return next((a for a in policy["agents"] if a["id"] == agent_id), None)


def applies_to(p: dict, agent: dict) -> bool:
    sc = p["scope"]
    if agent["id"] in (p.get("excludedAgentIds") or []):
        return False  # switched off for this agent (the gateway enforces it as `unless principal in [...]`)
    return (p["workspaceId"] == agent["workspaceId"]
            and (not sc["agent_ids"] or agent["id"] in sc["agent_ids"])
            and (not sc["identifiers"] or agent["identifier"] in sc["identifiers"])
            and (not sc["workspace"] or sc["workspace"] == agent["workspaceId"]))


def applicable_rules(policy: dict | None, agent_id: str | None) -> list[dict]:
    """Enabled Cedar policies of the agent's workspace whose principal can be this agent."""
    agent = agent_record(policy, agent_id)
    if not agent:
        return []
    return [p for p in policy["policies"] if p["enabled"] and applies_to(p, agent)]


def invalid_policies(policy: dict | None, agent_id: str | None) -> list[str]:
    """Enabled policies the summary could not read (the gateway rejects unparseable ones and then denies everything)."""
    return [p["name"] for p in applicable_rules(policy, agent_id) if not p["scope"]["parsed"]]


def granted_secrets(policy: dict | None, agent_id: str | None) -> list[dict]:
    """Stored secrets the gateway injects for this agent: targets of published grants naming it.

    Grants to a user or group are counted too (their agents cannot be resolved here).
    """
    agent = agent_record(policy, agent_id)
    if not agent:
        return []
    granted: dict[str, dict] = {}
    for g in policy["grants"]:
        if g["workspaceId"] not in (None, agent["workspaceId"]) or not (agent["id"] in g["agentIds"] or g["viaUserOrGroup"]):
            continue
        for t in g["targets"]:
            if t["kind"] != "secret":
                continue
            for s in policy["secrets"]:
                if (s["id"] == t["secretId"]) if t["secretId"] else (s["scope"] == t["secretScope"] and s["workspaceId"] in (None, agent["workspaceId"])):
                    granted[s["id"]] = {**s, "grant": g["name"]}
    return sorted(granted.values(), key=lambda s: s["name"])


def rule_label(p: dict) -> str:
    """"<methods> <host><path>" from the parsed policy; * where the policy leaves it open."""
    sc = p["scope"]
    if not sc["parsed"]:
        return "unparsed"
    return f"{','.join(sc['methods']) if sc['methods'] else '*'} {sc['host'] or '*'}{sc['path'] or ''}"


def effective_mode(policy: dict | None) -> tuple[str, str]:
    """(mode: "deny" | "unknown", evidence)."""
    if not policy:
        return "unknown", "onecli_db=unreadable"
    if not policy.get("cedarTable"):
        return "unknown", "cedar_policy_rules=absent"
    return "deny", f"engine=cedar; strict_deny={'on' if policy.get('strict') else 'off'}"


def deny_by_default_problem(policy: dict | None, lockdown_on: bool) -> str | None:
    """The failed precondition (as evidence), or None when the Cedar permits bound web egress.

    Preconditions: the local Cedar build (deny-by-default for every request),
    strict deny on the gateway, and egress lockdown on.
    """
    if not policy:
        return "onecli_db=unreadable"
    if not policy.get("cedarTable"):
        return "cedar_policy_rules=absent"
    if not policy.get("strict"):
        return f"GATEWAY_STRICT_DENY=off ({GATEWAY_CONTAINER})"
    if not lockdown_on:
        return "lockdown=off"
    return None


def policy_entries(policy: dict | None, agent_id: str | None) -> list[dict]:
    """Permit policies that let requests through, each with the forbid policies that can carve exceptions out of it."""
    if invalid_policies(policy, agent_id):
        return []
    rules = applicable_rules(policy, agent_id)
    forbids = [p for p in rules if p["scope"]["effect"] == "forbid"]
    entries = []
    for p in sorted((p for p in rules if p["scope"]["effect"] == "permit"), key=lambda p: (p["scope"]["host"] or "", p["name"])):
        sc = p["scope"]
        carved = [f["name"] for f in forbids if patterns_overlap(f["scope"]["host"], sc["host"])]
        entries.append({
            "name": p["name"], "label": rule_label(p), "host": sc["host"], "methods": sc["methods"], "text": p["text"],
            # Displayed: only references to policy entries; their code is printed verbatim elsewhere.
            "where": f"cedar: {p['name']}",
            "tokens": [f"forbid: {', '.join(carved)}"] if carved else [],
        })
    return entries
