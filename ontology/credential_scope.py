"""Declare what a credential's token may do, for materialize to trust and reuse.

    python3 credential_scope.py show
    python3 credential_scope.py suggest GitHub
    python3 credential_scope.py set GitHub --repos hyn0027/agent-playground [--permission issues=write --permission contents=read]
        [--user-permission gists=write | --no-user-permissions] [--expires 2027-01-14] [--note TEXT]
    python3 credential_scope.py rm GitHub

Writes local/credential-scopes.json (git-ignored). The credential is named as in
OneCLI; its secret id and updated_at are recorded (never its value), so a later
token change marks the declaration stale. ``suggest`` reads the latest materialize
run's instances.json: for GitHub, the private repositories the token can see,
which for a fine-grained token are exactly the private ones it was granted.
Permissions default to "*" (anything the token's tools can do in those
repositories); narrow them with --permission <name>=<none|read|write> using
GitHub's fine-grained permission names (issues, contents, pull_requests, actions,
workflows, checks, discussions, administration, ...).
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from src.common.paths import ROOT
from src.materialize.gateway_policy import read_gateway_policy
from src.materialize.instances.declarations import PATH, load, save


def secret(name: str) -> dict:
    policy = read_gateway_policy()
    if policy is None:
        sys.exit("OneCLI's database is unreachable")
    found = [s for s in policy["secrets"] if s["name"] == name]
    if not found:
        sys.exit(f"no OneCLI secret named {name!r}; known: {', '.join(sorted(s['name'] for s in policy['secrets']))}")
    return found[0]


def cmd_show(args) -> None:
    data = load()
    if not data["credentials"]:
        print(f"no declarations ({PATH})")
    for name, d in data["credentials"].items():
        print(f"{name}: {json.dumps(d['scope'])}  declared {d.get('declaredAt')}" + (f"  expires {d['expiresAt']}" if d.get("expiresAt") else "")
              + (f"  ({d['note']})" if d.get("note") else ""))


def cmd_set(args) -> None:
    s = secret(args.credential)
    def levels(items: list[str] | None, flag: str):
        if not items:
            return "*"
        out = {}
        for p in items:
            key, _, level = p.partition("=")
            if level not in ("none", "read", "write"):
                sys.exit(f"{flag} {p}: level must be none, read, or write")
            out[key] = level
        return out
    permissions = levels(args.permission, "--permission")
    user_permissions = {} if args.no_user_permissions else levels(args.user_permission, "--user-permission")
    data = load()
    data["credentials"][args.credential] = {
        "secretId": s["id"], "secretUpdatedAt": s.get("updatedAt"), "hostPattern": s.get("hostPattern"),
        "declaredAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": {"repositories": args.repos, "permissions": permissions, "user_permissions": user_permissions},
        **({"expiresAt": args.expires} if args.expires else {}), "note": args.note or "",
    }
    save(data)
    print(f"declared {args.credential}: {json.dumps(data['credentials'][args.credential]['scope'])} -> {PATH}")


def cmd_rm(args) -> None:
    data = load()
    if data["credentials"].pop(args.credential, None) is None:
        sys.exit(f"no declaration for {args.credential}")
    save(data)
    print(f"removed {args.credential}")


def cmd_suggest(args) -> None:
    runs = sorted((ROOT / "observations" / "runs").glob("*/instances.json"))
    if not runs:
        sys.exit("no materialize run with instances.json yet; run materialize.py first")
    data = json.loads(runs[-1].read_text())
    private = [i["attrs"]["match"] for i in data["instances"] if i["kind"] == "GitHubRepository" and i["attrs"].get("private")]
    print(f"from {runs[-1].parent.name}: private repositories the token sees: {', '.join(private) or 'none'}")
    if private:
        print(f"  python3 credential_scope.py set {args.credential} --repos {' '.join(private)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    p = sub.add_parser("set")
    p.add_argument("credential")
    p.add_argument("--repos", nargs="+", required=True, help="owner/name patterns (fnmatch, e.g. owner/*)")
    p.add_argument("--permission", action="append", help="name=none|read|write (repeatable); default: all")
    p.add_argument("--user-permission", action="append", help="account-level name=none|read|write (repeatable); default: all")
    p.add_argument("--no-user-permissions", action="store_true", help="the token has no user (account-level) permissions")
    p.add_argument("--expires", help="YYYY-MM-DD; after it the declaration is reported stale")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("rm")
    p.add_argument("credential")
    p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("suggest")
    p.add_argument("credential")
    p.set_defaults(fn=cmd_suggest)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
