"""What the operator is asked about (questions.py asks), and when.

A Topic is the fields to fill, guidance for Claude, optional fixed steps, and where the
answer is saved. Add an environment by adding a Topic to TOPICS. Without fixed steps,
Claude writes the choice questions from the field schema.

Questions draw only on the topic's abstract model (its fields and guidance), the credential
being asked about, and the operator's answers: never on observed instances (materialize
runs, discovered repositories, captured traffic), which can be stale or partial.

Two kinds of topic:
- a credential's scope (one topic per ar:CredentialType in the ontology, e.g. a GitHub
  fine-grained token; steps, fields and checks all come from the type): no API reveals what such a token can do, and
  probing it with writes is not acceptable, so the operator is asked once when an agent is
  granted a credential for one of the topic's hosts. The answer becomes a declaration in
  local/credential-scopes.json (materialize/instances/declarations.py) that later runs
  trust. A declaration that goes stale (the token was replaced, updated, or expired) asks
  again.
- a missing credential (credential): the agent's MCP servers or loaded skills call a host
  no granted credential covers. The card grants a stored credential in one click (OneCLI's
  grants API), or, when the vault has none, names a terminal command that stores a new one
  (the token is typed hidden and goes straight to the vault, never through chat).
"""

import getpass
import json
import sys

from ..common import credential_types as ct_model
from ..materialize.gateway_policy import granted_secrets, patterns_overlap, read_gateway_policy
from ..materialize.instances.declarations import for_host, load, save
from . import base, policies, questions
from .paths import NANOCLAW
from .questions import Step, Topic
from .store import now


# ── a credential's scope, from its type in the ontology (ar:CredentialType) ────
# The steps follow what the service lets a credential be scoped to: its resource owner,
# how resources are selected, each permission at the levels it allows, and expiry. Common
# permissions get a card each (ar:askedFirst); the rest are named in one card's Other.

LEVEL_LABEL = {"read": "Read", "write": "Read and write", "admin": "Admin"}


def _with(name: str, level: str | None) -> dict:
    """Option ``set``: one permission at a level (None drops it; merged by name, questions._apply)."""
    return {"permissions": [{"name": name, "level": level or "none"}]}


def _labels(perms: list) -> str:
    return "; ".join(f"{p.label} ({'/'.join(p.levels)})" for p in perms)


def scope_fields(ct: ct_model.CredentialType) -> dict:
    return {
        "owner_kind": {"enum": list(ct.owner_kinds), "description": "kind of the single owner whose resources the credential reaches"},
        "owner_name": {"type": "string", "description": 'the owner\'s name; "" for the credential\'s own user account'},
        "selection": {"enum": [x.key for x in ct.selections],
                      "description": "; ".join(f"{x.key} = {x.label}" for x in ct.selections)},
        "resources": {"type": "array", "items": {"type": "string"},
                      "description": "names, for a by-name selection, exactly as the operator gave them; [] otherwise"},
        "permissions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "level"],
                        "properties": {"name": {"enum": [x.key for x in ct.permissions]}, "level": {"enum": [*ct_model.LEVELS, "none"]}}},
                        "description": "permissions the answer settles, at their levels; merged with those already given "
                                       "(\"none\" removes one); leave out the rest"},
        "expires": {"type": "string", "description": 'expiry date YYYY-MM-DD, or "never" (no expiry, or unknown)'},
    }


def scope_guidance(ct: ct_model.CredentialType) -> str:
    return (f"The credential is a {ct.label}. Use only the permission keys and levels the context lists, mapping the "
            "operator's words to them (a display name, or a near synonym); a level a permission does not allow cannot be "
            "granted. Take resource and owner names only from the operator's answer; never invent them. "
            + (ct.comment + " " if ct.comment else "") + ct_model.facts(ct))


def scope_steps(ct: ct_model.CredentialType):
    def steps(meta: dict) -> list[Step]:
        user = next((k for k in ct.owner_kinds if ct_model.owner_role(k) == "user"), None)
        others = [k for k in ct.owner_kinds if k != user]
        out = []
        if ct.owner_kinds:
            out.append(Step("owner", f"Whose resources does the '{meta['credential']}' credential reach?",
                            ([{"label": "My own account", "value": user, "set": {"owner_kind": user, "owner_name": ""}}] if user else [])
                            + [{"label": f"An {ct_model.owner_role(k)} (then its name)" if ct_model.owner_role(k)[0] in "aeiou"
                                else f"A {ct_model.owner_role(k)} (then its name)", "value": k, "set": {"owner_kind": k}} for k in others],
                            asks=("owner_kind",)))
            out.append(Step("owner_name", "What is its name? (Other: type it.)", [], asks=("owner_name",),
                            when=lambda f: f.get("owner_kind") not in (None, user)))
        selection = ct.selections
        noun = ct_model.kind_noun(ct, selection[0].kind) if selection else "resources"
        out.append(Step("selection", f"Which {noun} can it reach?",
                        [{"label": x.label, "value": x.key,
                          "set": {"selection": x.key, **({} if x.by_name else {"resources": []}),
                                  **({"permissions": []} if x.read_only else {})}} for x in selection],
                        asks=("selection",)))
        by_name = {x.key for x in selection if x.by_name}
        read_only = {x.key for x in selection if x.read_only}
        out.append(Step("resources", f"Which {noun}? (Other: type their names.)", [], asks=("resources",),
                        when=lambda f: f.get("selection") in by_name))
        resource_kind = selection[0].kind if selection else None
        granted = lambda f: f.get("selection") not in read_only  # noqa: E731
        first = [x for x in ct.permissions_for(resource_kind) if x.asked_first]
        for perm in first:
            out.append(Step(f"perm.{perm.key}", f"{perm.label}: which access?",
                            [{"label": "None", "value": "none", "set": _with(perm.key, None)}]
                            + [{"label": LEVEL_LABEL[lv], "value": lv, "set": _with(perm.key, lv)} for lv in perm.levels],
                            when=granted, filled=lambda f, key=perm.key: any(x["name"] == key for x in f.get("permissions") or [])))
        rest = [x for x in ct.permissions_for(resource_kind) if not x.asked_first]
        if rest:
            out.append(Step("perm.more", f"Any other {ct_model.kind_noun(ct, resource_kind, plural=False)} permissions? "
                            f"(Other: name them with their access.) The others are: {_labels(rest)}.",
                            [{"label": "No others", "value": "none", "set": {}}], when=granted, filled=lambda f: False))
        for owner in ct.owner_kinds:
            held = ct.permissions_for(owner)
            if held:
                role = ct_model.owner_role(owner)
                out.append(Step(f"perm.{role}", f"Any {role} permissions? (Other: name them with their access.) "
                                f"They are: {_labels(held)}.",
                                [{"label": "None", "value": "none", "set": {}}],
                                when=lambda f, owner=owner: f.get("owner_kind", owner) == owner, filled=lambda f: False))
        out.append(Step("expires", "When does it expire? (Other: type the date, YYYY-MM-DD.)",
                        [{"label": "No expiry, or I don't know" if ct.may_never_expire else "I don't know",
                          "value": "never", "set": {"expires": "never"}}], asks=("expires",)))
        return out
    return steps


def _scope_from_fields(ct: ct_model.CredentialType, fields: dict) -> tuple[dict, list[str]]:
    perms, dropped = ct_model.normalize(ct, {x["name"]: x["level"] for x in fields.get("permissions") or []})
    sel = ct.selection(fields.get("selection") or "")
    return {"credentialType": ct.id, "owner": {"kind": fields.get("owner_kind") or ct.owner_kinds[0], "name": fields.get("owner_name") or None},
            "selection": fields.get("selection"), "resources": list(fields.get("resources") or []) if sel and sel.by_name else [],
            "permissions": perms}, dropped


def scope_describe(ct: ct_model.CredentialType):
    def describe(fields: dict) -> str:
        scope, dropped = _scope_from_fields(ct, fields)
        expires = "no expiry" if fields.get("expires") in (None, "never") else f"expires {fields['expires']}"
        return f"{ct_model.describe_scope(ct, scope)}; {expires}" + (f" (not possible, left out: {', '.join(dropped)})" if dropped else "")
    return describe


def scope_context(ct: ct_model.CredentialType):
    def context(meta: dict) -> str:
        perms = "; ".join(f"{x.key} = {x.label} [{ct_model.owner_role(x.held_by) if x.held_by in ct.owner_kinds else 'per resource'}: "
                          f"{'/'.join(x.levels)}]" for x in ct.permissions)
        return f"Credential: '{meta['credential']}' (used at {meta['host_pattern']}). Permissions of a {ct.label}: {perms}."
    return context


def scope_save(ct: ct_model.CredentialType):
    def save_scope(q: dict, fields: dict) -> str:
        meta = q["meta"]
        scope, dropped = _scope_from_fields(ct, fields)
        expires = None if fields.get("expires") in (None, "never") else fields["expires"]
        words = "; ".join(questions.own_words(q))
        data = load()
        data["credentials"][meta["credential"]] = {
            "secretId": meta["secret_id"], "secretUpdatedAt": meta["secret_updated_at"], "hostPattern": meta["host_pattern"],
            "declaredAt": now(), "scope": scope, **({"expiresAt": expires} if expires else {}),
            "note": f"answered question {q['id']}" + (f"; in own words: {words[:300]}" if words else ""),
        }
        save(data)
        return f"saved {meta['credential']}: {scope_describe(ct)(fields)}"
    return save_scope


def scope_fields_from(ct: ct_model.CredentialType):
    """A saved declaration's scope back as question fields (to offer keeping it)."""
    def fields(declaration) -> dict:
        sc = declaration.scope
        owner = sc.get("owner") or {}
        return {"owner_kind": owner.get("kind"), "owner_name": owner.get("name") or "", "selection": sc.get("selection"),
                "resources": sc.get("resources") or [], "permissions": [{"name": k, "level": v} for k, v in sorted((sc.get("permissions") or {}).items())],
                "expires": declaration.expires or "never"}
    return fields


def scope_topic(ct: ct_model.CredentialType) -> Topic:
    return Topic(name=ct.id, what=ct.label, fields=scope_fields(ct), guidance=scope_guidance(ct),
                 describe=scope_describe(ct), save=scope_save(ct), steps=scope_steps(ct), context=scope_context(ct),
                 hosts=ct.hosts)


# ── a credential the agent needs and is not granted ─────────────────────────

CREDENTIAL_FIELDS = {
    "decision": {"type": "string", "description": '"grant:<secret id>" to give the agent that stored credential (ids are in '
                 'the context), or "skip" to leave it without one for now'},
}
CREDENTIAL_GUIDANCE = ("The agent calls a host that no credential granted to it covers, so those calls fail. The operator "
                       "decides whether to grant one of the stored credentials listed in the context. Use only ids from the "
                       "context; never invent one.")


def credential_steps(meta: dict) -> list[Step]:
    agent, host, need = meta["agent"], meta["host"], meta["need"]
    options = [{"label": f"Grant '{c['name']}'", "value": f"grant:{c['id']}", "set": {"decision": f"grant:{c['id']}"}}
               for c in meta["candidates"][:3]]
    options.append({"label": "Not now", "value": "skip", "set": {"decision": "skip"}})
    if meta["candidates"]:
        prompt = (f"{agent}'s {need} calls {host}, but no credential for it is granted to {agent}, so those calls fail "
                  f"(401). Grant a stored credential?")
    else:
        prompt = (f"{agent}'s {need} calls {host}, and there is no credential for it in the vault, so those calls fail (401). "
                  f"To add one without opening a page, run this in a terminal (the token is typed hidden and goes straight "
                  f"to the vault, then it is granted to {agent}):\n"
                  f"`cd {NANOCLAW / 'ontology'} && .venv/bin/python ontology_runtime.py add-credential`")
    return [Step("grant", prompt, options, asks=("decision",))]


def credential_context(meta: dict) -> str:
    stored = "; ".join(f"'{c['name']}' (id {c['id']}, used at {c['hostPattern']})" for c in meta["candidates"]) or "none"
    return f"Agent {meta['agent']}; host {meta['host']}, needed by its {meta['need']}. Stored credentials for it: {stored}."


def credential_describe(fields: dict) -> str:
    return "leave it without a credential for now" if fields["decision"] == "skip" else f"grant credential {fields['decision'][6:]}"


def credential_save(q: dict, fields: dict) -> str:
    meta, decision = q["meta"], fields["decision"] or "skip"
    if not decision.startswith("grant:"):
        return f"left {meta['agent']} without a credential for {meta['host']}"
    secret_id = decision.removeprefix("grant:")
    name = next((c["name"] for c in meta["candidates"] if c["id"] == secret_id), meta.get("added") or secret_id)
    try:
        policies.onecli(f"/agents/{meta['agent_uuid']}/grants/secrets/{secret_id}", "PUT")
    except policies.PolicyError as e:
        return f"could not grant '{name}' to {meta['agent']}: {e}"
    return f"granted '{name}' to {meta['agent']} for {meta['host']}"


TOPICS = {
    "credential": Topic(name="credential", what="missing credential", fields=CREDENTIAL_FIELDS,
                        guidance=CREDENTIAL_GUIDANCE, describe=credential_describe, save=credential_save,
                        steps=credential_steps, context=credential_context, confirm=False),
    **{ct.id: scope_topic(ct) for ct in ct_model.credential_types().values()},
}
FROM_DECLARATION = {ct.id: scope_fields_from(ct) for ct in ct_model.credential_types().values()}


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
        intro=(f"{why}. NanoClaw needs to know what the credential '{secret['name']}' (used at {secret.get('hostPattern')}) "
               "can do; no API reveals a fine-grained token's permissions, so it asks you and trusts the answer. "
               "The agent waits until you answer."),
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
            declaration = for_host(granted, _host(secret, topic))
            if declaration and not declaration.stale:
                continue  # already answered for this very token: asking again only repeats it
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



# ── when to ask: a host the agent needs and holds no credential for ──────────

def _url_host(endpoint: str) -> str | None:
    """The host of a URL or of an endpoint pattern like api.example.com/v1/*; None for a command line."""
    if "://" in endpoint:
        return endpoint.split("/")[2].split(":")[0] or None
    head = endpoint.split("/")[0]
    return head if "." in head and " " not in head else None


def _needs(group_id: str, session_id: str) -> dict[str, dict]:
    """host -> {"need": what calls it, "mcp": bool}, from the group's remote MCP servers and the
    hosts the session's loaded skills declare in their reviewed models."""
    out: dict[str, dict] = {}
    for name, endpoint in base.mcp_servers(group_id).items():
        if host := _url_host(endpoint):
            out.setdefault(host, {"need": f"MCP server '{name}'", "mcp": True})
    for skill in policies.skill_loads(group_id, session_id):
        for pattern in policies.skill_endpoints(skill):
            if host := _url_host(pattern):
                out.setdefault(host, {"need": f"skill '{skill}'", "mcp": False})
    return out


def missing_credentials(group_id: str, session_id: str) -> list[dict]:
    """Hosts the agent needs that no granted credential covers, each with the stored
    credentials that would (candidates). A skill host with no stored credential is left out:
    nothing says it needs one (many are public). An MCP server is assumed to."""
    policy = read_gateway_policy()
    agent = next((a for a in (policy or {}).get("agents", []) if a["identifier"] == group_id), None)
    if not agent:
        return []
    granted = granted_secrets(policy, agent["id"])
    out = []
    for host, need in sorted(_needs(group_id, session_id).items()):
        if any(patterns_overlap(s.get("hostPattern") or "", host) for s in granted):
            continue
        candidates = [{"id": s["id"], "name": s["name"], "hostPattern": s.get("hostPattern")}
                      for s in policy["secrets"]
                      if patterns_overlap(s.get("hostPattern") or "", host) and s.get("workspaceId") in (None, agent["workspaceId"])]
        if candidates or need["mcp"]:
            out.append({"host": host, "need": need["need"], "candidates": candidates, "agent_uuid": agent["id"]})
    return out


def _credential_key(session_id: str, host: str) -> str:
    return f"credential:{session_id}:{host}"


def ask_credentials(group_id: str, session_id: str) -> list[dict]:
    """Open a question for each host the agent needs and holds no credential for, unless this
    session's operator already answered it (granted, or chose not now)."""
    answered = {q["key"] for q in questions.all_questions() if q["status"] == "answered"}
    name = (base.central("SELECT name FROM agent_groups WHERE id = ?", group_id) or [{"name": group_id}])[0]["name"]
    out = []
    for m in missing_credentials(group_id, session_id):
        key = _credential_key(session_id, m["host"])
        if key in answered:
            continue
        out.append(questions.open_question(
            TOPICS["credential"], key=key, title=f"{name} needs a credential for {m['host']}",
            intro=f"{name}'s {m['need']} needs a credential it does not have.",
            meta={"agent": name, "group_id": group_id, **m}, sessions=[session_id]))
    return out


def credential_notices(session_id: str) -> list[str]:
    """What the agent should know about credential questions answered for this session since
    it was last told: once per question."""
    notices = []
    for q in questions.all_questions():
        if q["topic"] != "credential" or q["status"] != "answered" or session_id not in q["sessions"] or q.get("told"):
            continue
        meta, decision = q["meta"], q["fields"].get("decision") or "skip"
        if decision.startswith("grant:") and q.get("result", "").startswith("granted"):
            notices.append(f"The operator granted you a credential for {meta['host']} (used by your {meta['need']}); "
                           "calls there are authenticated now.")
        else:
            notices.append(f"You have no credential for {meta['host']}, so calls there from your {meta['need']} fail "
                           "(401, or an OAuth sign-in or /register attempt that the gateway blocks). That is a missing "
                           "credential, not a policy problem: tell the user the operator needs to grant one.")
        q["told"] = True
        questions.save(q)
    return notices


def credentials_notice(group_id: str, told: dict) -> list[str]:
    """Every credential granted to the agent, for the agent (whatever service it is for): name,
    where it is used, and the scope the operator declared. Sent once per session and again
    whenever the list changes (a grant added or removed, a scope declared or changed).
    ``told`` (kept in the session's state) is updated in place."""
    lines = []
    for secret in _granted(group_id):
        where = secret.get("hostPattern") or "?"
        if secret.get("pathPattern") not in (None, "", "*"):
            where += secret["pathPattern"]
        declaration = for_host([secret], secret.get("hostPattern") or "")
        ct = ct_model.credential_types().get((declaration.scope if declaration else {}).get("credentialType", "")) \
            or ct_model.for_host(secret.get("hostPattern") or "")
        always = f" Whatever its scope: {ct_model.facts(ct)}" if ct and ct_model.facts(ct) else ""
        if declaration and not declaration.stale and ct:
            scope = ct_model.describe_scope(ct, declaration.scope) + (f"; expires {declaration.expires}" if declaration.expires else "")
            lines.append(f"- '{secret['name']}' ({ct.label}) for {where}: declared scope: {scope}.{always}")
        elif declaration:
            lines.append(f"- '{secret['name']}' for {where}: scope unknown (the saved answer may no longer hold: {declaration.stale}).{always}")
        else:
            lines.append(f"- '{secret['name']}' for {where}: no scope declared.{always}")
    stamp = "\n".join(lines)
    if told.get("credentials") == stamp:
        return []
    told["credentials"] = stamp
    if not lines:
        return ["You hold no credentials: every call you make to an external service is unauthenticated."]
    return ["Your credentials, managed by the operator (the gateway adds them to matching requests; you never see their "
            "values):\n" + stamp + "\nA declared scope is what the credential may do: stay within it. A service's own reports "
            "about your permissions (fields such as 'permissions' or 'scopes' in its responses) may describe the account "
            "behind the credential rather than the credential itself, so do not infer access from them; trust this list. "
            "Calls to hosts not listed here carry no credential."]


def add_credential(qid: str | None = None) -> dict | None:
    """Terminal: store a new credential for an open missing-credential question with no
    stored candidate (token typed hidden, straight to OneCLI's vault), grant it to the agent,
    and answer the question."""
    open_qs = [q for q in questions.all_questions()
               if q["topic"] == "credential" and q["status"] == "open" and (qid is None or q["id"] == qid)]
    if not open_qs:
        sys.exit("no open missing-credential question" + (f" {qid}" if qid else ""))
    if len(open_qs) > 1:
        for i, q in enumerate(open_qs, 1):
            print(f"{i}. {q['title']} ({q['id']})")
        q = open_qs[int(input("Which one? ")) - 1]
    else:
        q = open_qs[0]
    meta = q["meta"]
    print(f"{q['title']}: a new credential for {meta['host']}, sent as the header 'Authorization: Bearer <token>'.")
    name = input(f"Name for it in the vault [{meta['host']}]: ").strip() or meta["host"]
    token = getpass.getpass("Token (hidden): ").strip()
    if not token:
        sys.exit("no token given; nothing stored")
    try:
        secret = policies.onecli("/secrets", "POST", {
            "name": name, "type": "generic", "value": token, "hostPattern": meta["host"],
            "injectionConfig": {"headerName": "Authorization", "valueFormat": "Bearer {value}"}})
    except policies.PolicyError as e:
        sys.exit(f"could not store it: {e}")
    finally:
        token = ""  # noqa: F841 - drop the value as soon as it is sent
    q["meta"] = {**meta, "added": name}
    questions.save(q)
    return questions.complete(q, {"decision": f"grant:{secret['id']}"}, "add-credential")
