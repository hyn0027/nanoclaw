"""GitHub, through the GitHub MCP server the session is configured with, as the agent."""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from ....common import credential_types as ct_model
from ....common.paths import MCP_DIR
from ...probe import ProbeError
from ..model import Instance
from ..registry import Context, environment

SERVER_ID = "mcp-github"
KINDS = ["GitHubUser", "GitHubRepository", "GitHubWorkflow"]

# What a declared scope allows comes from the credential type in the ontology
# (ar:GitHubFineGrainedToken in ontology/mcp/github-environment.trig): which permission
# governs each kind, at which levels, and what the token never reaches.
CREDENTIAL_TYPE = "github-fine-grained-token"
REPOSITORY = "GitHubRepository"


def credential_type() -> ct_model.CredentialType:
    return ct_model.credential_types()[CREDENTIAL_TYPE]


def in_scope(scope: dict, login: str):
    """full_name -> whether the declared selection grants that repository (public-only grants none).
    A token reaches one owner's repositories: the declared owner, or the token's own user."""
    owner = ((scope.get("owner") or {}).get("name") or login).lower()
    selection = scope.get("selection")
    if selection == "all":
        return lambda name: name.split("/", 1)[0].lower() == owner
    if selection == "selected":
        names = {(r if "/" in r else f"{owner}/{r}").lower() for r in scope.get("resources") or []}
        return lambda name: name.lower() in names
    return lambda name: False


def _repository_permission(kind: str, read: bool) -> ct_model.Permission | None:
    ct = credential_type()
    if read:
        found = next((p for p in ct.permissions if kind in p.governs_read), None)
        if found:
            return found
    return next((p for p in ct.permissions if kind in p.governs and p.held_by == REPOSITORY), None)


def declared_level(scope: dict, granted, repo: str, kind: str, read: bool = False) -> int:
    """Access level (0 none, 1 read, 2 write) the declared scope gives on ``kind`` in ``repo``."""
    if not granted(repo):
        return 0
    perm = _repository_permission(kind, read)
    if perm is None:
        return 1  # no permission governs it: readable with the repository
    level = "read" if perm.levels == ("read",) else (scope.get("permissions") or {}).get(perm.key)  # read-only ones (metadata) always hold
    return 2 if ct_model.level_at_least(level, "write") else 1 if ct_model.level_at_least(level, "read") else 0


def access_check(ctx: Context, scope: dict, login: str):
    """check(instance, kind, effect): False when the declared scope cannot do the effect on that
    instance, or no token of this type can; reads of public repositories are always possible."""
    ct = credential_type()
    granted = in_scope(scope, login)
    owner_kind = (scope.get("owner") or {}).get("kind")
    never = {f.kind for f in ct.never if f.condition is None}
    never_for_user_owner = {f.kind for f in ct.never if f.condition == "owned-by-user"}
    perms = scope.get("permissions") or {}

    def check(instance: Instance, kind: str, effect: str):
        if kind in never or (kind in never_for_user_owner and ct_model.owner_role(owner_kind or "") == "user"):
            return False
        inst = ctx.instances
        repo = next((inst.by_id[i] for i in inst.lineage(instance.id) if inst.by_id[i].kind == REPOSITORY), None)
        if repo is None:
            # held by the owner's account or organization: its permission, if the owner is of that kind
            held = next((p for p in ct.permissions if kind in p.governs and p.held_by != REPOSITORY), None)
            if held is None or effect == "Read":
                return None
            if held.held_by != owner_kind:
                return False
            return None if ct_model.level_at_least(perms.get(held.key), "write") else False
        if effect == "Read" and not repo.attrs.get("private"):
            return None
        need = 1 if effect == "Read" else 2
        return None if declared_level(scope, granted, repo.attrs.get("match", ""), kind, read=effect == "Read") >= need else False
    return check


def read_only_tools(server_id: str) -> set[str]:
    """Tools the server's pinned snapshot marks readOnlyHint; the probe refuses all others."""
    source = MCP_DIR / "sources" / f"{server_id.removeprefix('mcp-')}.json"
    snap = json.loads(source.read_text()) if source.exists() else {"tools": []}
    return {t["name"] for t in snap["tools"] if t.get("annotations", {}).get("readOnlyHint")}


def workflow_events(text: str) -> list[str]:
    """Event names under a workflow's top-level `on:` (string, list, or mapping form)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"""^(?:on|"on"|'on'|true)\s*:\s*(.*)$""", line)
        if not m:
            continue
        rest = m.group(1).split("#")[0].strip()
        if rest.startswith("["):
            return [e.strip(" '\"") for e in rest.strip("[]").split(",") if e.strip()]
        if rest:
            return [rest.strip(" '\"")]
        events, indent = [], None
        for sub in lines[i + 1:]:
            if not sub.strip() or sub.lstrip().startswith("#"):
                continue
            depth = len(sub) - len(sub.lstrip())
            if depth == 0:
                break
            if indent is None:
                indent = depth
            if depth == indent:
                key = sub.strip().lstrip("- ").split(":")[0].strip(" '\"")
                if key:
                    events.append(key)
        return events
    return []


def _server(snap) -> tuple[str, dict] | None:
    names = [name for name, sid in snap.facts["modeled_mcp"].items() if sid == SERVER_ID]
    return (names[0], (snap.facts["config"].get("mcp_servers") or {})[names[0]]) if names else None


@environment("github", kinds=KINDS, live=True, applies=lambda snap: _server(snap) is not None)
def github(ctx: Context) -> None:
    """GitHub MCP as the agent (get_me, search_repositories, get_file_contents)"""
    _, spec = _server(ctx.snap)
    mcp = ctx.probe.mcp(spec["url"], read_only_tools(SERVER_ID), spec.get("headers") or {})
    me = mcp.call("get_me", {})
    user = ctx.add(Instance("GitHubUser", f"github:{me['login']}", f"{me['login']} (the token's user)"))

    repos, page = [], 1
    try:
        while True:
            found = mcp.call("search_repositories", {"query": f"user:{me['login']}", "perPage": 100, "page": page})
            repos += found.get("items", [])
            if len(repos) >= found.get("total_count", 0) or not found.get("items"):
                break
            page += 1
    except ProbeError as e:
        ctx.note(["GitHubRepository"], f"repository listing stopped: {e}")
    # The token's scope, as the user declared it (not observable for fine-grained tokens).
    host = spec["url"].split("/")[2]
    declaration = ctx.declared(host)
    if declaration is None or declaration.stale:
        # Not observable: ask the operator (terminal, or a NanoClaw DM) and wait for the answer.
        from ....runtime.topics import require
        session = ctx.snap.facts["session"]
        if require(session["agent_group_id"], session["id"], CREDENTIAL_TYPE):
            declaration = ctx.declared(host)
    scope = declaration.scope if declaration else None
    declared = in_scope(scope, me["login"]) if scope else (lambda name: True)
    if scope is not None:
        # checked against everything the token sees, before trimming
        for name in scope.get("resources") or []:
            full = name if "/" in name else f"{(scope.get('owner') or {}).get('name') or me['login']}/{name}"
            if not any(r["full_name"].lower() == full.lower() for r in repos):
                ctx.warn(f"declared repository {full} is not among the repositories the token sees (search user:{me['login']}); "
                         "it may belong to an organization, or the declaration is wrong")
        for r in repos:
            if r.get("private") and not declared(r["full_name"]):
                ctx.warn(f"the token sees private repository {r['full_name']}, which the declaration leaves out; "
                         "a fine-grained token sees only the private repositories it was granted, so the declaration is likely incomplete")

    # With a declaration, public repositories outside it are only readable, like every public
    # repository on GitHub: they become one instance instead of a list of the user's own.
    # Undeclared private ones stay listed (the token was granted them; see the warning).
    kept = repos if scope is None else [r for r in repos if declared(r["full_name"]) or r.get("private")]
    for r in kept:
        flags = ", ".join(x for x in ("private" if r.get("private") else "public", "fork" if r.get("fork") else "", "archived" if r.get("archived") else "") if x)
        ctx.add(Instance("GitHubRepository", f"repo:{r['full_name']}", f"{r['full_name']} ({flags}; {r.get('open_issues_count', 0)} open issues/PRs)", user.id,
                         {"private": bool(r.get("private")), "default_branch": r.get("default_branch"), "archived": r.get("archived"),
                          "open_issues": r.get("open_issues_count", 0), "match": r["full_name"]}))
    if scope is None:
        ctx.note(["GitHubRepository"], f"repositories owned by {me['login']} (search user:{me['login']}); organization and collaborator repositories the token may also reach are not listed")
    else:
        others = len(repos) - len(kept)
        ctx.add(Instance("GitHubRepository", "repo:*public*", "any public repository on GitHub (readable by any token; not writable under the declared scope)",
                         None, {"private": False, "aggregate": True, "public_repositories_of_user_not_listed": others}))
        ctx.note(["GitHubRepository"], f"public repositories outside the declaration are one instance ('any public repository on GitHub'); "
                                       f"{others} of {me['login']}'s are not listed individually")

    def workflows(repo: dict) -> list[Instance]:
        owner, repo_name = repo["full_name"].split("/", 1)
        try:
            listing = mcp.call("get_file_contents", {"owner": owner, "repo": repo_name, "path": ".github/workflows/"})
        except ProbeError:
            return []  # no workflows directory
        out = []
        for entry in listing if isinstance(listing, list) else []:
            if entry.get("type") != "file" or not entry["name"].endswith((".yml", ".yaml")):
                continue
            try:
                content = mcp.call("get_file_contents", {"owner": owner, "repo": repo_name, "path": entry["path"]})
                text = "\n".join(content.get("resources", [])) if isinstance(content, dict) else str(content)
            except ProbeError:
                text = ""
            events = workflow_events(text)
            out.append(Instance("GitHubWorkflow", f"workflow:{repo['full_name']}/{entry['path']}",
                                f"{repo['full_name']} {entry['path']} (on: {', '.join(events) or 'unreadable'})", f"repo:{repo['full_name']}",
                                {"events": events, "path": entry["path"]}))
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:  # only where the token's effects can start
        for found in pool.map(workflows, kept):
            for w in found:
                ctx.add(w)

    if scope is None:
        ctx.note(["GitHubRepository"], f"no declared scope for the credential at {host}: every repository the token sees counts as writable "
                                       "(declare one with: ontology_runtime.py ask-scope <credential>)")
        return
    ctx.restrict(f"outside the GitHub token scope {declaration.describe()}", access_check(ctx, scope, me["login"]))
    governed = sorted({k for p in credential_type().permissions if p.held_by == REPOSITORY for k in (*p.governs, *p.governs_read)})
    for repo in ctx.instances.by_kind.get("GitHubRepository", []):
        if repo.attrs.get("aggregate"):
            repo.attrs["declared_access"] = "read (any token)"
            continue
        level = max((declared_level(scope, declared, repo.attrs["match"], k, read) for k in governed for read in (False, True)), default=0)
        repo.attrs["declared_access"] = ("write" if level == 2 else "read" if level == 1 else "none") + " (declared)"
    ctx.note(["GitHubRepository"], f"scope {declaration.describe()}: {ct_model.describe_scope(credential_type(), scope)}"
                                   + (f"; expires {declaration.expires}" if declaration.expires else ""))
