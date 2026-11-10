"""Generate a OneCLI Cedar policy group for an MCP server from its pinned snapshot.

    python3 generate_mcp_policies.py github > mcp-github.json
    python3 generate_mcp_policies.py github --enable get_me,issue_write > mcp-github.json
    python3 generate_mcp_policies.py github --all-on --no-approval > mcp-github.json
    onecli-policy sync mcp-github.json

Reads the same snapshot as generate_mcp_ontology.py (ontology/mcp/sources/<id>.json)
and writes the JSON spec `onecli-policy sync` takes: one group (source "mcp:<id>")
with the protocol policies the transport needs and one policy per tool, so every
tool can be switched on or off on its own.

- protocol: initialize, notifications, ping, and listing (tools, resources, prompts)
- read-content: resources/read, prompts/get, completion/complete, as far as the
  server declares those capabilities
- stream-and-session: GET and DELETE on the endpoint (streamable HTTP)
- tool-<name>: tools/call with params.name == <name>. Without --enable, read-only
  tools (readOnlyHint) are on and the others off; with --enable, exactly the listed
  tools are on (and read-content only when listed). Non-read-only tools carry @approval when destructive under the MCP
  ToolAnnotations defaults (destructiveHint unset counts as true), so an enabled
  write tool still holds each call for a human.
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "ontology" / "mcp" / "sources"


def cedar_str(text: str) -> str:
    return json.dumps(text)


def method_policy(host: str, path_pattern: str, methods: list[str]) -> str:
    listed = ",\n    ".join(cedar_str(m) for m in methods)
    return f'''permit(
  principal,
  action == OneCLI::Action::"POST",
  resource == OneCLI::Host::{cedar_str(host)}
) when {{
  context.path like {cedar_str(path_pattern)} &&
  context has json && context.json has method &&
  [
    {listed}
  ].contains(context.json.method)
}};'''


def tool_policy(host: str, path_pattern: str, tool: str, approval: bool) -> str:
    return ("@approval\n" if approval else "") + f'''permit(
  principal,
  action == OneCLI::Action::"POST",
  resource == OneCLI::Host::{cedar_str(host)}
) when {{
  context.path like {cedar_str(path_pattern)} &&
  context has json && context.json has method && context.json.method == "tools/call" &&
  context.json has params && context.json.params has name &&
  context.json.params.name == {cedar_str(tool)}
}};'''


def describe(tool: dict) -> str:
    a = tool["annotations"]
    kind = "read-only" if a.get("readOnlyHint") else ("destructive" if a.get("destructiveHint", True) else "additive write")
    sets = ", ".join(tool["toolsets"]) or "no documented toolset"
    title = tool.get("title") or tool["name"]
    return f"{title} ({kind}; toolset {sets})"[:500]


def generate(server_id: str, enable: set[str] | None = None, approval: bool = True, all_on: bool = False) -> dict:
    """``approval``: hold destructive tools for a human (@approval). ``all_on``: every policy on."""
    snap = json.loads((SOURCES / f"{server_id}.json").read_text())
    unknown = sorted((enable or set()) - {t["name"] for t in snap["tools"]} - {"read-content"})
    if unknown:
        sys.exit(f"unknown tools for --enable: {', '.join(unknown)}")
    url = urlparse(snap["callEndpoint"])
    host = url.hostname
    path_pattern = (url.path.rstrip("/") or "") + "*"
    caps = snap.get("capabilities") or {}
    title = snap["server"].get("title") or snap["server"]["name"]

    listing = ["initialize", "notifications/initialized", "notifications/cancelled", "ping", "tools/list"]
    content = []
    if "resources" in caps:
        listing += ["resources/list", "resources/templates/list"]
        content.append("resources/read")
    if "prompts" in caps:
        listing.append("prompts/list")
        content.append("prompts/get")
    if "completions" in caps:
        content.append("completion/complete")

    policies = [
        {"name": "protocol", "description": "MCP handshake, notifications, ping, and listing (JSON-RPC over POST)",
         "policyText": method_policy(host, path_pattern, listing)},
    ]
    if content:
        policies.append({"name": "read-content", "description": f"Server capabilities {', '.join(sorted(caps))}: " + ", ".join(content),
                         "enabled": all_on or enable is None or "read-content" in enable,
                         "policyText": method_policy(host, path_pattern, content)})
    policies.append({"name": "stream-and-session", "description": "Server stream (GET) and session close (DELETE) of the streamable HTTP transport",
                     "policyText": f'''permit(
  principal,
  action in [OneCLI::Action::"GET", OneCLI::Action::"DELETE"],
  resource == OneCLI::Host::{cedar_str(host)}
) when {{ context.path like {cedar_str(path_pattern)} }};'''})
    for tool in sorted(snap["tools"], key=lambda t: t["name"]):
        a = tool["annotations"]
        read_only = bool(a.get("readOnlyHint", False))
        destructive = not read_only and bool(a.get("destructiveHint", True))
        policies.append({
            "name": f"tool-{tool['name']}",
            "description": describe(tool),
            "enabled": all_on or ((tool["name"] in enable) if enable is not None else read_only),
            "policyText": tool_policy(host, path_pattern, tool["name"], approval=approval and destructive),
        })
    return {
        "name": f"mcp-{server_id}",
        "source": f"mcp:{server_id}",
        "description": f"{title} ({snap['callEndpoint']}): protocol plus one policy per tool, generated from ontology/mcp/sources/{server_id}.json. "
                       + ("Every tool on; " if all_on else f"{len(enable)} tools on, the rest off; " if enable is not None else "Read-only tools on, write tools off; ")
                       + ("enabled write tools are held for approval when destructive." if approval else "no tool is held for approval."),
        "policies": policies,
    }


def main() -> None:
    args = sys.argv[1:]
    enable = None
    approval = "--no-approval" not in args
    all_on = "--all-on" in args
    args = [a for a in args if a not in ("--no-approval", "--all-on")]
    if "--enable" in args:
        i = args.index("--enable")
        enable = {t.strip() for t in args[i + 1].split(",") if t.strip()} if i + 1 < len(args) else set()
        args = args[:i] + args[i + 2:]
    if len(args) != 1:
        sys.exit("usage: python3 generate_mcp_policies.py <server-id> [--enable tool,tool,...]  > spec.json")
    json.dump(generate(args[0], enable, approval, all_on), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
