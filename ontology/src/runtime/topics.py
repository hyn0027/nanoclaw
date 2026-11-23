"""What the operator is asked about (questions.py asks), and when.

A Topic is the fields to fill, guidance for Claude, optional fixed steps, and where the
answer is saved. Add an environment by adding a Topic to TOPICS. Without fixed steps,
Claude writes the choice questions from the field schema.

Questions draw only on the topic's abstract model (its fields and guidance), the credential
being asked about, and the operator's answers: never on observed instances (materialize
runs, discovered repositories, captured traffic), which can be stale or partial.

Today every topic is a credential's scope: no API reveals what a fine-grained token can do,
and probing it with writes is not acceptable, so the operator is asked once when an
agent is granted a credential for one of the topic's hosts. The answer becomes a
declaration in local/credential-scopes.json (materialize/instances/declarations.py) that
later runs trust. A declaration that goes stale (the token was replaced, updated, or
expired) asks again.
"""

import json
import sys

from ..materialize.gateway_policy import granted_secrets, patterns_overlap, read_gateway_policy
from ..materialize.instances.declarations import for_host, load, save
from . import base, questions
from .questions import Step, Topic
from .store import now


# ── GitHub (fine-grained personal access token) ──────────────────────────────

_perms = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "level"],
                                     "properties": {"name": {"type": "string"}, "level": {"enum": ["read", "write"]}}}}
GITHUB_FIELDS = {
    "repositories": {"type": "array", "items": {"type": "string"},
                     "description": 'owner/name or fnmatch patterns (owner/*); ["*"] for every repository it can see'},
    "permissions": {**_perms, "description": "repository permissions by GitHub fine-grained name (contents, issues, pull_requests, "
                    'statuses, actions, workflows, checks, administration, ...); [{"name": "*", "level": "write"}] for everything '
                    "its tools allow; leave out what it lacks"},
    "user_permissions": {**_perms, "description": "account permissions (profile, gists, starring, notifications, projects, copilot); "
                         '[] for none; [{"name": "*", "level": "write"}] for all'},
    "expires": {"type": "string", "description": 'expiry date YYYY-MM-DD, or "never" (no expiry, or unknown)'},
}
GITHUB_GUIDANCE = ("The credential is a GitHub fine-grained personal access token. Use GitHub's fine-grained permission names "
                   'exactly ("Code" or "Contents" is contents, "Pull requests" is pull_requests, "Commit statuses" is statuses); '
                   'read-and-write is "write". Take repository names only from the operator\'s answer; never invent them.')
GITHUB_AREAS = [("contents", "Code (contents: files, commits, branches, releases)"), ("issues", "Issues"),
                ("pull_requests", "Pull requests"), ("actions", "Actions (workflow runs, artifacts)"),
                ("workflows", "Workflow files (.github/workflows)"), ("administration", "Administration (settings, collaborators)")]
GITHUB_USER = ["profile", "gists", "starring", "notifications", "projects", "copilot"]
ALL = [{"name": "*", "level": "write"}]


def _levels(names: list[str], level: str) -> list[dict]:
    return [{"name": n, "level": level} for n in names]


def _with(name: str, level: str | None):
    """Option ``set``: add (or drop, for None) one permission."""
    def update(fields: dict) -> dict:
        perms = [p for p in fields.get("permissions") or [] if p["name"] != name]
        return {"permissions": perms + ([{"name": name, "level": level}] if level else [])}
    return update


def github_steps(meta: dict) -> list[Step]:
    custom = lambda choices: choices.get("access") == "custom"  # noqa: E731
    return [
        Step("repos", f"Which repositories can the '{meta['credential']}' token act on? (Other: list owner/name patterns.)",
             [{"label": "Every repository it can see", "value": "all", "set": {"repositories": ["*"]}}], asks=("repositories",)),
        Step("access", "What can it do in those repositories?", [
            {"label": "Read only: code, issues, pull requests", "value": "read",
             "set": {"permissions": _levels(["contents", "issues", "metadata", "pull_requests"], "read")}},
            {"label": "Read and write: code, issues, pull requests", "value": "write",
             "set": {"permissions": _levels(["contents", "issues", "pull_requests"], "write") + _levels(["metadata"], "read")}},
            {"label": "Everything its tools allow", "value": "all", "set": {"permissions": ALL}},
            {"label": "Let me choose per area", "value": "custom", "set": {"permissions": _levels(["metadata"], "read")}},
        ], asks=("permissions",)),
        *(Step(f"perm.{key}", f"{label}: which access?",
               [{"label": "None", "value": "none", "set": _with(key, None)},
                {"label": "Read", "value": "read", "set": _with(key, "read")},
                {"label": "Read and write", "value": "write", "set": _with(key, "write")}],
               when=custom, filled=lambda f, key=key: any(p["name"] in (key, "*") for p in f.get("permissions") or []))
          for key, label in GITHUB_AREAS),
        Step("user", "Account-level permissions (profile, gists, stars, notifications, projects)?", [
            {"label": "None", "value": "none", "set": {"user_permissions": []}},
            {"label": "Read", "value": "read", "set": {"user_permissions": _levels(GITHUB_USER, "read")}},
            {"label": "Read and write", "value": "all", "set": {"user_permissions": ALL}},
        ], asks=("user_permissions",)),
        Step("expires", "Does the token expire? (Other: type the date, YYYY-MM-DD.)", [
            {"label": "No expiry, or I don't know", "value": "never", "set": {"expires": "never"}},
        ], asks=("expires",)),
    ]


def _to_map(perms: list[dict]) -> dict | str:
    return "*" if any(p["name"] == "*" for p in perms) else {p["name"]: p["level"] for p in perms}


def _from_map(perms: dict | str) -> list[dict]:
    return ALL if perms == "*" else [{"name": n, "level": lv} for n, lv in sorted(perms.items()) if lv != "none"]


def github_scope(fields: dict) -> dict:
    perms = _to_map(fields["permissions"])
    if isinstance(perms, dict) and perms:
        perms.setdefault("metadata", "read")
    return {"repositories": fields["repositories"], "permissions": perms, "user_permissions": _to_map(fields["user_permissions"])}


def github_describe(fields: dict) -> str:
    scope = github_scope(fields)
    perms = scope["permissions"]
    perms = "everything its tools allow" if perms == "*" else ", ".join(f"{k} {v}" for k, v in sorted(perms.items())) or "no permissions"
    user = scope["user_permissions"]
    user = "all account permissions" if user == "*" else ", ".join(f"{k} {v}" for k, v in sorted(user.items())) or "no account permissions"
    expires = "no expiry" if fields["expires"] in (None, "never") else f"expires {fields['expires']}"
    return f"repositories {', '.join(scope['repositories']) or 'none'}; {perms}; {user}; {expires}"


def github_context(meta: dict) -> str:
    return f"Credential: '{meta['credential']}' (used at {meta['host_pattern']})."


def github_save(q: dict, fields: dict) -> str:
    meta = q["meta"]
    expires = None if fields["expires"] == "never" else fields["expires"]
    words = "; ".join(questions.own_words(q))
    data = load()
    data["credentials"][meta["credential"]] = {
        "secretId": meta["secret_id"], "secretUpdatedAt": meta["secret_updated_at"], "hostPattern": meta["host_pattern"],
        "declaredAt": now(), "scope": github_scope(fields), **({"expiresAt": expires} if expires else {}),
        "note": f"answered question {q['id']}" + (f"; in own words: {words[:300]}" if words else ""),
    }
    save(data)
    return f"saved {meta['credential']}: {github_describe(fields)}"


def github_fields(declaration) -> dict:
    scope = declaration.scope
    return {"repositories": scope.get("repositories", []), "permissions": _from_map(scope.get("permissions", "*")),
            "user_permissions": _from_map(scope.get("user_permissions", "*")), "expires": declaration.expires or "never"}


TOPICS = {
    "github": Topic(name="github", what="GitHub token", fields=GITHUB_FIELDS, guidance=GITHUB_GUIDANCE,
                    describe=github_describe, save=github_save, steps=github_steps, context=github_context,
                    hosts=("api.githubcopilot.com", "api.github.com")),
}
FROM_DECLARATION = {"github": github_fields}


# ── when to ask: credentials granted for a topic's hosts ─────────────────────

def _granted(group_id: str) -> list[dict]:
    policy = read_gateway_policy()
    agent = next((a for a in (policy or {}).get("agents", []) if a["identifier"] == group_id), None)
    return granted_secrets(policy, agent["id"]) if agent else []


def _host(secret: dict, topic: str) -> str | None:
    """The first of the topic's hosts the secret's host pattern covers."""
    return next((h for h in TOPICS[topic].hosts if patterns_overlap(secret.get("hostPattern") or "", h)), None)


def _secret_for(granted: list[dict], topic: str) -> dict | None:
    return next((s for s in granted if _host(s, topic)), None)


def ask(topic: str, secret: dict, granted: list[dict], key: str, why: str, sessions: list[str]) -> dict:
    """Open (or reuse) the scope question for ``secret``, offering a current declaration to keep."""
    t = TOPICS[topic]
    existing = for_host(granted, _host(secret, topic))
    return questions.open_question(
        t, key=key,
        title=f"Scope of the {t.what} '{secret['name']}'",
        intro=(f"{why}. NanoClaw's ontology needs to know what the credential '{secret['name']}' (used at {secret.get('hostPattern')}) "
               "can do; no API reveals a fine-grained token's scope, so it asks you and trusts the answer. "
               "Until you answer, this session's ontology is not expanded."),
        meta={"credential": secret["name"], "secret_id": secret["id"], "secret_updated_at": secret.get("updatedAt"),
              "host_pattern": secret.get("hostPattern")},
        sessions=sessions,
        existing=FROM_DECLARATION[topic](existing) if existing and not existing.stale else None)


def _topics_for(endpoint: str | None) -> list[str]:
    """Topics whose hosts an MCP endpoint URL or a credential host pattern names."""
    if not endpoint:
        return []
    host = endpoint.split("/")[2] if "://" in endpoint else endpoint
    return [name for name, t in TOPICS.items() if any(patterns_overlap(host, h) for h in t.hosts)]


def on_connect(group_id: str, session_id: str, changes: list[dict]) -> list[dict]:
    """A session's agent gained MCP servers or credential grants (``changes`` from one
    base.diff): one question per credential they use."""
    granted = _granted(group_id)
    by_secret: dict[str, tuple[str, dict, list[str]]] = {}
    for c in changes:
        if c["what"] not in ("mcp", "credential") or c["change"] not in ("added", "changed"):
            continue
        for topic in _topics_for(c.get("endpoint")):
            secret = _secret_for(granted, topic)
            if not secret:
                continue  # connected, but no credential is granted for it: nothing to ask
            what = f"MCP server '{c['name']}'" if c["what"] == "mcp" else f"credential grant '{c['name']}'"
            by_secret.setdefault(secret["id"], (topic, secret, []))[2].append(f"{what} {c['change']}")
    at = now()
    return [ask(topic, secret, granted, key=f"connect:{session_id}:{topic}:{secret['id']}:{at}",
                why=f"Just connected for this agent: {', '.join(whats)}", sessions=[session_id])
            for topic, secret, whats in by_secret.values()]


def reopen(credential: str) -> list[dict]:
    """Ask again about ``credential`` for every active session whose agent is granted it
    (e.g. after widening a token's permissions on GitHub, which leaves the token, and so
    OneCLI's secret, unchanged and therefore undetectable)."""
    out = []
    by_group: dict[str, list[str]] = {}
    for s in base.active_sessions():
        by_group.setdefault(s["agent_group_id"], []).append(s["id"])
    for group_id, sessions in by_group.items():
        granted = _granted(group_id)
        secret = next((s for s in granted if s["name"] == credential), None)
        topic = next((name for name in TOPICS if secret and _host(secret, name)), None)
        if topic:
            out.append(ask(topic, secret, granted, key=f"manual:{secret['id']}:{now()}",
                           why="You asked to update what it can do", sessions=sessions))
    return out


def needed(group_id: str, sessions: list[str], topic: str | None = None) -> list[dict]:
    """Open questions (created if needed) for the group's granted credentials that have no
    declaration, or a stale one."""
    granted = _granted(group_id)
    out = []
    for name in TOPICS:
        secret = _secret_for(granted, name) if topic in (None, name) else None
        if not secret:
            continue
        declaration = for_host(granted, _host(secret, name))
        if declaration and not declaration.stale:
            continue
        why = f"The saved answer may no longer hold ({declaration.stale})" if declaration else "There is no saved answer yet"
        out.append(ask(name, secret, granted, key=f"credential-scope:{secret['name']}:{secret['id']}:{secret.get('updatedAt')}",
                       why=why, sessions=sessions))
    return out


def require(group_id: str, session_id: str, topic: str | None = None) -> bool:
    """Make sure the group's credentials are declared: ask in the terminal when there is
    one, else wait for the chat answer. True when something was answered."""
    asked = False
    for q in needed(group_id, [session_id], topic):
        q = questions.ask_tty(q) if sys.stdin.isatty() and sys.stderr.isatty() else questions.wait(q)
        if q["status"] == "answered":
            print(f"question {q['id']}: {q['result']}", file=sys.stderr)
            asked = True
    return asked

