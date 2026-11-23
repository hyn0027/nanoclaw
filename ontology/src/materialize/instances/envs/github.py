"""GitHub, through the GitHub MCP server the session is configured with, as the agent."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase

from ....common.paths import MCP_DIR
from ...probe import ProbeError
from ..model import Instance
from ..registry import Context, environment

SERVER_ID = "mcp-github"
KINDS = ["GitHubUser", "GitHubRepository", "GitHubWorkflow"]

# Fine-grained token permission each repository-held kind needs, for a declared scope
# ({"permissions": {"issues": "write", ...}}). "metadata" is always readable.
PERMISSION = {
    "GitHubIssue": "issues", "GitHubIssueComment": "issues", "GitHubLabel": "issues", "GitHubMilestone": "issues",
    "GitHubIssueTypeOrField": "issues", "GitHubReaction": "issues",
    "GitHubPullRequest": "pull_requests", "GitHubPullRequestReview": "pull_requests", "GitHubReviewComment": "pull_requests",
    "GitHubBranch": "contents", "GitHubCommit": "contents", "GitHubRepositoryFile": "contents", "GitHubTag": "contents",
    "GitHubRelease": "contents", "GitHubCopilotTask": "contents",
    "GitHubWorkflow": "workflows", "GitHubWorkflowRun": "actions", "GitHubWorkflowJob": "actions", "GitHubWorkflowArtifact": "actions",
    "GitHubCheckRun": "checks",
    "GitHubDiscussion": "discussions", "GitHubDiscussionComment": "discussions", "GitHubDiscussionCategory": "discussions",
    "GitHubRepository": "administration", "GitHubRuleset": "administration", "GitHubRepositoryCollaborator": "administration",
    "GitHubCustomProperty": "administration",
    "GitHubCodeScanningAlert": "security_events", "GitHubCodeQualityFinding": "security_events",
    "GitHubSecretScanningAlert": "secret_scanning_alerts", "GitHubDependabotAlert": "dependabot_alerts",
    "GitHubSecurityAdvisory": "repository_advisories",
}
LEVEL = {"none": 0, "read": 1, "write": 2}

# Fine-grained user permission each account-held kind needs ({"user_permissions": {...}}).
USER_PERMISSION = {
    "GitHubUser": "profile", "GitHubRepository": "administration", "GitHubGist": "gists", "GitHubStar": "starring", "GitHubProject": "projects",
    "GitHubCopilotSpace": "copilot", "GitHubNotification": "notifications",
}

# Reading these needs a different permission than changing them (a repository's metadata
# is readable with "metadata"; changing its settings needs "administration").
READ_PERMISSION = {"GitHubRepository": "metadata"}


def declared_level(scope: dict, repo: str, kind: str, read: bool = False) -> int:
    """Access level (0 none, 1 read, 2 write) a declared scope gives on ``kind`` in ``repo``
    (``read``: for a read, which may need a different permission)."""
    if not any(fnmatchcase(repo, pattern) for pattern in scope.get("repositories", [])):
        return 0
    permissions = scope.get("permissions", "*")
    if permissions == "*":
        return 2
    perm = (READ_PERMISSION.get(kind) if read else None) or PERMISSION.get(kind)
    return LEVEL.get(permissions.get(perm, "none"), 0) if perm else 1


def access_check(ctx: Context, scope: dict):
    """check(instance, kind, effect): False when the declared token scope cannot do the effect
    on that instance (its repository is outside the scope, or the permission is too low);
    reads of public repositories are always possible."""
    def check(instance: Instance, kind: str, effect: str):
        inst = ctx.instances
        repo = next((inst.by_id[i] for i in inst.lineage(instance.id) if inst.by_id[i].kind == "GitHubRepository"), None)
        if repo is None:
            # account-held (the user's gists, stars, profile): the token's user permissions
            if kind not in USER_PERMISSION or effect == "Read":
                return None
            user = scope.get("user_permissions", "*")
            return None if user == "*" or LEVEL.get(user.get(USER_PERMISSION[kind], "none"), 0) >= 2 else False
        if effect == "Read" and not repo.attrs.get("private"):
            return None
        need = 1 if effect == "Read" else 2
        return None if declared_level(scope, repo.attrs.get("match", ""), kind, read=effect == "Read") >= need else False
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
        from ....runtime.scope_questions import require
        session = ctx.snap.facts["session"]
        if require(session["agent_group_id"], session["id"], "github", sorted(r["full_name"] for r in repos if r.get("private"))):
            declaration = ctx.declared(host)
    scope = declaration.scope if declaration else None
    patterns = (scope or {}).get("repositories", [])
    declared = lambda name: any(fnmatchcase(name, p) for p in patterns)  # noqa: E731
    if scope is not None:
        # checked against everything the token sees, before trimming
        for pattern in patterns:
            if not any(fnmatchcase(r["full_name"], pattern) for r in repos):
                ctx.warn(f"declared repository {pattern} is not among the repositories the token sees (search user:{me['login']}); "
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
    ctx.restrict(f"outside the GitHub token scope {declaration.describe()}", access_check(ctx, scope))
    for repo in ctx.instances.by_kind.get("GitHubRepository", []):
        if repo.attrs.get("aggregate"):
            repo.attrs["declared_access"] = "read (any token)"
            continue
        level = max((declared_level(scope, repo.attrs["match"], k, read) for k in PERMISSION for read in (False, True)), default=0)
        repo.attrs["declared_access"] = ("write" if level == 2 else "read" if level == 1 else "none") + " (declared)"
    ctx.note(["GitHubRepository"], f"scope {declaration.describe()}: repositories {', '.join(patterns) or 'none'}; "
                                   f"permissions {json.dumps(scope.get('permissions', '*'))}; user permissions {json.dumps(scope.get('user_permissions', '*'))}"
                                   + (f"; expires {declaration.expires}" if declaration.expires else ""))
