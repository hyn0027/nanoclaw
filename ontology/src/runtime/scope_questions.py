"""Credential-scope questions: which granted credentials need the operator to say what
they can do, the steps to ask, and how the answers become a declaration.

One entry per environment in SCOPED (hosts its credentials are used at, the steps, the
apply function). Add an environment by adding an entry; questions.py stays generic.
"""

import json
from datetime import datetime, timezone

from ..common.paths import ROOT
from ..materialize.gateway_policy import granted_secrets, patterns_overlap, read_gateway_policy
from ..materialize.instances.declarations import PATH, for_host, load, save
from . import questions

LEVELS = [{"label": "None", "value": "none"}, {"label": "Read", "value": "read"}, {"label": "Read and write", "value": "write"}]


# ── GitHub (fine-grained personal access token) ──────────────────────────────

GITHUB_AREAS = [("contents", "Code (contents: files, commits, branches, releases)"), ("issues", "Issues"),
                ("pull_requests", "Pull requests"), ("actions", "Actions (workflow runs, artifacts)"),
                ("workflows", "Workflow files (.github/workflows)"), ("administration", "Administration (settings, collaborators)")]
GITHUB_PRESETS = {
    "read": {"contents": "read", "issues": "read", "metadata": "read", "pull_requests": "read"},
    "write": {"contents": "write", "issues": "write", "metadata": "read", "pull_requests": "write"},
}
GITHUB_USER = ["profile", "gists", "starring", "notifications", "projects", "copilot"]


def github_steps(secret: dict, known_private: list[str]) -> list[dict]:
    seen = ", ".join(known_private)
    repo_options = ([{"label": f"Only: {seen}"[:80], "value": "seen"}] if known_private else []) + [
        {"label": "Every repository it can see", "value": "all"},
        {"label": "Others: I'll type them", "value": "@text"}]
    steps = [
        {"id": "repos", "prompt": f"Which repositories can the '{secret['name']}' token act on?"
                                  + (f" It can see these private ones: {seen}." if known_private else ""),
         "options": repo_options, "free_text": "owner/name patterns, space-separated (owner/* for all of an owner)"},
        {"id": "access", "prompt": "What can it do in those repositories?",
         "options": [{"label": "Read only: code, issues, pull requests", "value": "read"},
                     {"label": "Read and write: code, issues, pull requests", "value": "write"},
                     {"label": "Everything its tools allow", "value": "all"},
                     {"label": "Let me choose per area", "value": "custom"}]},
        *({"id": f"perm.{key}", "prompt": f"{label}: which access?", "options": LEVELS, "when": {"step": "access", "in": ["custom"]}}
          for key, label in GITHUB_AREAS),
        {"id": "user", "prompt": "Account-level permissions (profile, gists, stars, notifications, projects)?",
         "options": [{"label": "None", "value": "none"}, {"label": "Read", "value": "read"}, {"label": "Read and write", "value": "all"}]},
        {"id": "expires", "prompt": "Does the token expire?",
         "options": [{"label": "No expiry, or I don't know", "value": "none"}, {"label": "Yes: I'll type the date", "value": "@text"}],
         "free_text": "expiry date YYYY-MM-DD"},
    ]
    return steps


def github_apply(q: dict, given: dict) -> str:
    meta = q["meta"]
    if questions.nl_mode(q, given):
        r = q["interpretation"]["result"]
        perms = "*" if r["all_permissions"] else {p["name"]: p["level"] for p in r["permissions"]}
        user = "*" if r["all_user_permissions"] else {p["name"]: p["level"] for p in r["user_permissions"]}
        return write_declaration(q, {"repositories": r["repositories"], "permissions": perms, "user_permissions": user},
                                 r.get("expires"), note=f"described in words: {q['interpretation']['text'].removeprefix('text:')[:300]}")
    if given.get("keep") == "keep":
        data = load()
        d = data["credentials"][meta["credential"]]
        d.update({"secretId": meta["secret_id"], "secretUpdatedAt": meta["secret_updated_at"],
                  "confirmedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                  "confirmedBy": f"question {q['id']}"})
        save(data)
        return f"kept {meta['credential']}: {json.dumps(d['scope'])}"
    repos = given["repos"]
    repositories = (meta["known_private"] if repos == "seen" else ["*"] if repos == "all"
                    else repos.removeprefix("text:").split())
    access = given["access"]
    if access == "all":
        permissions = "*"
    elif access == "custom":
        permissions = {"metadata": "read", **{k: given[f"perm.{k}"] for k, _ in GITHUB_AREAS}}
    else:
        permissions = GITHUB_PRESETS[access]
    user = {"none": {}, "read": {k: "read" for k in GITHUB_USER}, "all": "*"}[given["user"]]
    expires = given.get("expires", "none")
    return write_declaration(q, {"repositories": repositories, "permissions": permissions, "user_permissions": user},
                             expires.removeprefix("text:") if expires.startswith("text:") else None)


def write_declaration(q: dict, scope: dict, expires: str | None, note: str = "") -> str:
    meta = q["meta"]
    data = load()
    data["credentials"][meta["credential"]] = {
        "secretId": meta["secret_id"], "secretUpdatedAt": meta["secret_updated_at"], "hostPattern": meta["host_pattern"],
        "declaredAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": scope, **({"expiresAt": expires} if expires else {}),
        "note": f"answered question {q['id']}" + (f"; {note}" if note else ""),
    }
    save(data)
    return f"declared {meta['credential']}: {SCOPED[meta['env']]['describe'](scope)}" + (f"; expires {expires}" if expires else "")


# Plain-language answers: what Claude returns for a description of a GitHub token.
_level = {"enum": ["none", "read", "write"]}
_perm_list = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "level"],
                                          "properties": {"name": {"type": "string"}, "level": _level}}}
GITHUB_NL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["understood", "summary", "unclear", "repositories", "all_permissions", "permissions",
                 "all_user_permissions", "user_permissions", "expires"],
    "properties": {
        "understood": {"type": "boolean", "description": "false when the description leaves the repositories or the access undetermined"},
        "summary": {"type": "string", "description": "one sentence the operator can confirm, naming repositories, permissions, account access, expiry"},
        "unclear": {"type": ["string", "null"], "description": "what is missing or ambiguous, when understood is false"},
        "repositories": {"type": "array", "items": {"type": "string"}, "description": "owner/name or fnmatch patterns (owner/*, * for every repository)"},
        "all_permissions": {"type": "boolean"},
        "permissions": {**_perm_list, "description": "GitHub fine-grained repository permission names (contents, issues, pull_requests, statuses, actions, workflows, checks, administration, metadata, ...); metadata is always read"},
        "all_user_permissions": {"type": "boolean"},
        "user_permissions": {**_perm_list, "description": "account permission names (profile, gists, starring, notifications, ...); empty for none"},
        "expires": {"type": ["string", "null"], "description": "YYYY-MM-DD, or null"},
    },
}
GITHUB_NL_SYSTEM = """You turn an operator's plain-language description of a GitHub fine-grained personal access token's access into a structured declaration. Use GitHub's fine-grained permission names exactly (e.g. "Commit statuses" is statuses, "Code" or "Contents" is contents, "Pull requests" is pull_requests, "Workflows" is workflows). Read-and-write is "write". Include metadata read whenever any repository permission is given. Resolve "the private repo it can see", "the playground repo" and similar against the repositories listed in the context; never invent repositories. Only mark understood=false when the repositories or the permissions truly cannot be determined."""


def github_describe(scope: dict) -> str:
    repos = ", ".join(scope.get("repositories", [])) or "no repositories"
    perms = scope.get("permissions", "*")
    perms = "everything its tools allow" if perms == "*" else ", ".join(f"{k} {v}" for k, v in sorted(perms.items()))
    user = scope.get("user_permissions", "*")
    user = "all account permissions" if user == "*" else "no account permissions" if not user else ", ".join(f"{k} {v}" for k, v in sorted(user.items()))
    return f"repositories {repos}; {perms}; {user}"


SCOPED = {
    "github": {"hosts": ["api.githubcopilot.com", "api.github.com"], "steps": github_steps, "apply": github_apply,
               "describe": github_describe, "what": "GitHub token", "nl_schema": GITHUB_NL_SCHEMA, "nl_system": GITHUB_NL_SYSTEM},
}


# ── generic ─────────────────────────────────────────────────────────────────

def known_private_repos() -> list[str]:
    """Private repositories the latest materialize run saw (the runtime has no discovery)."""
    runs = sorted((ROOT / "observations" / "runs").glob("*/instances.json"))
    if not runs:
        return []
    data = json.loads(runs[-1].read_text())
    return sorted(i["attrs"]["match"] for i in data.get("instances", []) if i["kind"] == "GitHubRepository" and i["attrs"].get("private") and i["attrs"].get("match"))


def _granted(group_id: str) -> list[dict]:
    policy = read_gateway_policy()
    agent = next((a for a in (policy or {}).get("agents", []) if a["identifier"] == group_id), None)
    return granted_secrets(policy, agent["id"]) if agent else []


def ask(env: str, secret: dict, granted: list[dict], key: str, why: str, sessions: list[str],
        known_private: list[str] | None = None) -> dict:
    """Open (or reuse) the scope question for ``secret``. When a current declaration exists,
    the first step asks whether it still holds; the rest only follow a 'change'."""
    cfg = SCOPED[env]
    host = next(h for h in cfg["hosts"] if patterns_overlap(secret.get("hostPattern") or "", h))
    existing = for_host(granted, host)
    existing = existing if existing and not existing.stale else None
    private = known_private if known_private is not None else known_private_repos()
    steps = cfg["steps"](secret, private)
    steps[0] = {**steps[0], "options": [*steps[0]["options"], questions.NL_OPTION][: questions.MAX_OPTIONS]}
    if existing:
        summary = cfg["describe"](existing.scope)
        steps = [{"id": "keep", "prompt": f"Saved answer for '{secret['name']}' (declared {existing.declared_at}): {summary}. Still right?",
                  "options": [{"label": "Yes, keep it", "value": "keep"}, {"label": "No, change it", "value": "change"}, questions.NL_OPTION]},
                 *({**s, "when": [{"step": "keep", "in": ["change"]}, *([s["when"]] if s.get("when") else [])]} for s in steps)]
    return questions.open_question(
        key=key,
        title=f"Scope of the {cfg['what']} '{secret['name']}'",
        intro=(f"{why}. NanoClaw's ontology needs to know what the credential '{secret['name']}' (used at {secret.get('hostPattern')}) "
               "can do; no API reveals a fine-grained token's scope, so it asks you and trusts the answer. "
               "Until you answer, this session's ontology is not expanded."),
        steps=steps,
        meta={"env": env, "credential": secret["name"], "secret_id": secret["id"], "secret_updated_at": secret.get("updatedAt"),
              "host_pattern": secret.get("hostPattern"), "known_private": private},
        sessions=sessions)


def _env_for(endpoint: str | None) -> list[str]:
    """Scoped environments whose hosts an MCP endpoint URL or a credential host pattern names."""
    if not endpoint:
        return []
    host = endpoint.split("/")[2] if "://" in endpoint else endpoint
    return [name for name, cfg in SCOPED.items() if any(patterns_overlap(host, h) for h in cfg["hosts"])]


def on_connect(group_id: str, session_id: str, changes: list[dict]) -> list[dict]:
    """A session's agent gained MCP servers or credential grants (``changes`` from one
    base.diff): one question per credential they use."""
    granted = _granted(group_id)
    by_secret: dict[str, tuple[str, dict, list[str]]] = {}
    for c in changes:
        if c["what"] not in ("mcp", "credential") or c["change"] not in ("added", "changed"):
            continue
        for env in _env_for(c.get("endpoint")):
            hosts = SCOPED[env]["hosts"]
            secret = next((s for s in granted if any(patterns_overlap(s.get("hostPattern") or "", h) for h in hosts)), None)
            if not secret:
                continue  # connected, but no credential is granted for it: nothing to scope
            what = f"MCP server '{c['name']}'" if c["what"] == "mcp" else f"credential grant '{c['name']}'"
            by_secret.setdefault(secret["id"], (env, secret, []))[2].append(f"{what} {c['change']}")
    at = questions.now()
    return [ask(env, secret, granted, key=f"connect:{session_id}:{env}:{secret['id']}:{at}",
                why=f"Just connected for this agent: {', '.join(whats)}", sessions=[session_id])
            for env, secret, whats in by_secret.values()]


def reopen(credential: str) -> list[dict]:
    """Ask again about ``credential`` for every active session whose agent is granted it
    (e.g. after widening a token's permissions on GitHub, which leaves the token, and so
    OneCLI's secret, unchanged and therefore undetectable)."""
    from . import base
    out = []
    by_group: dict[str, list[str]] = {}
    for s in base.active_sessions():
        by_group.setdefault(s["agent_group_id"], []).append(s["id"])
    for group_id, sessions in by_group.items():
        granted = _granted(group_id)
        secret = next((s for s in granted if s["name"] == credential), None)
        if not secret:
            continue
        env = next((name for name, cfg in SCOPED.items() if any(patterns_overlap(secret.get("hostPattern") or "", h) for h in cfg["hosts"])), None)
        if env:
            out.append(ask(env, secret, granted, key=f"manual:{secret['id']}:{questions.now()}",
                           why="You asked to update what it can do", sessions=sessions))
    return out


def needed(group_id: str, sessions: list[str], env: str | None = None, known_private: list[str] | None = None) -> list[dict]:
    """Open questions (created if needed) for the group's granted credentials that have no
    declaration, or a stale one."""
    granted = _granted(group_id)
    out = []
    for name, cfg in SCOPED.items():
        if env and name != env:
            continue
        secret = next((s for s in granted if any(patterns_overlap(s.get("hostPattern") or "", h) for h in cfg["hosts"])), None)
        if not secret:
            continue
        host = next(h for h in cfg["hosts"] if patterns_overlap(secret.get("hostPattern") or "", h))
        declaration = for_host(granted, host)
        if declaration and not declaration.stale:
            continue
        why = f"The saved answer may no longer hold ({declaration.stale})" if declaration else "There is no saved answer yet"
        out.append(ask(name, secret, granted, key=f"credential-scope:{secret['name']}:{secret['id']}:{secret.get('updatedAt')}",
                       why=why, sessions=sessions, known_private=known_private))
    return out


def interpret_description(q: dict, text: str) -> dict:
    """Claude's reading of a plain-language answer to question ``q``."""
    from .llm import call
    cfg = SCOPED[q["meta"]["env"]]
    existing = for_host(_granted_cached(q), next(iter(cfg["hosts"])))
    context = "\n".join([
        f"Credential: '{q['meta']['credential']}' (used at {q['meta']['host_pattern']}).",
        f"Private repositories the token can see: {', '.join(q['meta'].get('known_private') or []) or 'unknown'}.",
        f"Current saved declaration: {cfg['describe'](existing.scope) if existing else 'none'}.",
        "The question's multiple-choice steps, for reference:",
        *(f"- {s['prompt']} [{', '.join(o['label'] for o in s['options'])}]" for s in q["steps"] if not s["id"].startswith(questions.CONFIRM)),
        "", f"The operator's description:\n{text}",
    ])
    result, _usage = call(cfg["nl_system"], context, cfg["nl_schema"])
    return result


def _granted_cached(q: dict) -> list[dict]:
    try:
        policy = read_gateway_policy()
        return [s for s in (policy or {}).get("secrets", []) if s["id"] == q["meta"]["secret_id"]]
    except Exception:  # noqa: BLE001
        return []


def advance(q: dict) -> dict | None:
    """Move an open question on: interpret a new plain-language answer, or apply a
    complete one. Returns the question when it got answered (closed). Locked, so a tick
    and a waiting materialize never interpret the same answer twice."""
    import fcntl
    questions.QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(questions.QUESTIONS_DIR / f"{q['id']}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        q = questions.get(q["id"]) or q
        if q["status"] != "open":
            return None
        given = questions.answers(q)
        text = questions.needs_interpretation(q, given)
        if text is not None:
            questions.interpret(q, text)
            return None
        if questions.complete(q, given):
            return questions.finish(q, SCOPED[q["meta"]["env"]]["apply"])
    return None



def require(group_id: str, session_id: str, env: str | None = None, known_private: list[str] | None = None) -> bool:
    """Make sure the group's credentials are declared: ask in the terminal when there is
    one, else wait for the chat answer. True when something was answered."""
    import sys
    asked = False
    for q in needed(group_id, [session_id], env, known_private):
        if sys.stdin.isatty() and sys.stderr.isatty():
            questions.ask_tty(q)
        else:
            questions.wait(q, advance)
        done = advance(q)
        if done:
            print(f"question {q['id']}: {done['result']}", file=sys.stderr)
            asked = True
    return asked
