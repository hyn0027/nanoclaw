"""Vercel, through its REST API, as the agent (the gateway injects the Vercel token)."""

from ...gateway_policy import patterns_overlap
from ...probe import ProbeError
from ..model import Instance
from ..registry import Context, environment

API = "https://api.vercel.com"
AUTH = {"Authorization": "Bearer placeholder"}


def _granted(snap) -> bool:
    return any(patterns_overlap(s.get("hostPattern") or "", "api.vercel.com") for s in snap.facts["secrets"])


@environment("vercel", kinds=["VercelAccount", "VercelProject", "VercelDomain", "VercelDeployment", "VercelEnvironmentVariable"],
             live=True, applies=_granted)
def vercel(ctx: Context) -> None:
    """Vercel REST API as the agent (GET user, teams, projects, domains, deployments, env names)"""
    get = lambda path: ctx.probe.get_json(f"{API}{path}", AUTH)  # noqa: E731
    user = get("/v2/user")["user"]
    scopes = [(None, ctx.add(Instance("VercelAccount", f"vercel:{user['id']}", f"personal account {user.get('username')} ({user['id']})")))]
    try:
        for t in get("/v2/teams").get("teams", []):
            scopes.append((t["id"], ctx.add(Instance("VercelAccount", f"vercel:{t['id']}", f"team {t.get('slug') or t.get('name')} ({t['id']})"))))
    except ProbeError as e:
        ctx.note(["VercelAccount"], f"teams not listed: {e}")
    for team, account in scopes:
        q = f"teamId={team}" if team else ""
        try:
            for p in get(f"/v9/projects?limit=100&{q}").get("projects", []):
                project = ctx.add(Instance("VercelProject", f"vproject:{p['id']}", f"{p['name']} ({p.get('framework') or 'no framework'}) in {account.label}",
                                           account.id, {"match": p["name"]}))
                for e in get(f"/v9/projects/{p['id']}/env?{q}").get("envs", []):
                    # names, targets, and types only; values are never recorded
                    ctx.add(Instance("VercelEnvironmentVariable", f"venv:{p['id']}:{e.get('id') or e['key']}",
                                     f"{e['key']} ({', '.join(e.get('target') or [])}; {e.get('type')}) in {p['name']}", project.id))
            for d in get(f"/v5/domains?limit=100&{q}").get("domains", []):
                ctx.add(Instance("VercelDomain", f"vdomain:{d['name']}", f"{d['name']} in {account.label}", account.id, {"match": d["name"]}))
            for d in get(f"/v6/deployments?limit=20&{q}").get("deployments", []):
                parent = f"vproject:{d.get('projectId')}"
                ctx.add(Instance("VercelDeployment", f"vdeploy:{d['uid']}", f"https://{d.get('url')} ({d.get('target') or 'preview'}, {d.get('state') or d.get('readyState')})",
                                 parent if parent in ctx.instances.by_id else account.id, {"match": d.get("url") or d["uid"]}))
        except ProbeError as e:
            ctx.note(["VercelProject", "VercelDomain", "VercelDeployment", "VercelEnvironmentVariable"], f"listing in {account.label} stopped: {e}")
