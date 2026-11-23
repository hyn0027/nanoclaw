"""What this session can reach or change beyond the local machine.

Rows: (resource, location, effects, via, status, gate), the layout shared by all
three tables. Via is the model's open ``routesToKind`` path from the endpoint to
the resource's side, one step per line under the tool name
(steps closed by an observed control are dropped). A path through the OneCLI
gateway yields one row per Cedar permit policy applying to the agent, located
at the policy entry ("cedar: <group>/<policy>"; a configured MCP server by the
gateway's own decision); its gate lists overlapping forbids and granted
credentials whose host pattern overlaps. Every gate also lists the model's ``protectedByKind`` controls along
the path. Reads are included because the remote party sees the request.
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from rdflib import URIRef

from ...common.namespaces import AR
from ...common.rdf import short
from ..constants import LOCAL_CHANNELS, UNAVAILABLE
from ..gateway_policy import evaluate, patterns_overlap, policy_entries
from ..audience import destination_location
from ..snapshot import Snapshot
from .common import ancestors, endpoint_name, gate, render_paths, route_paths, step_effects, tool_label

# Endpoints that only send GET requests (Claude's WebFetch): omitted from policies that permit only other methods.
GET_ONLY = {"WebFetch"}


def via_gateway(snap: Snapshot, path: list[URIRef]) -> bool:
    return any(snap.side_of(k) == AR.OneCLIGateway for k in path)


def channel_destinations(snap: Snapshot, path: list[URIRef]) -> list[dict] | None:
    """For a path through channel delivery, the agent's remote channel destination grants
    (where such a message can go); None for any other path."""
    if not any(AR.NanoClawChannelAdapterDelivery in ancestors(snap, k) for k in path):
        return None
    return [d for d in snap.facts["grants"] if d["target_type"] == "channel" and d.get("channel_type") not in LOCAL_CHANNELS]


def direct_rows(snap: Snapshot, res: str, effects: str, via: str, status: str, path: list[URIRef],
                kinds: list[URIRef], facts: list[str] | None = None) -> list[tuple]:
    """Rows for a path that does not pass the gateway: one per channel destination for
    channel delivery, else a single row reachable anywhere ("*")."""
    facts = facts or []
    dests = channel_destinations(snap, path)
    if dests is None:
        return [(res, "*", effects, via, status, gate(snap, facts, *kinds))]
    if not dests:
        return [(res, "-", effects, via, "Unavailable", gate(snap, ["destination: none", *facts], *kinds))]
    return [(res, destination_location(snap, d), effects, via, status,
             gate(snap, [f"destination: {d['local_name']}", *facts], *kinds)) for d in dests]


def credential_tokens(snap: Snapshot, host: str | None) -> list[str]:
    hit = sorted(s["name"] for s in snap.facts["secrets"] if s.get("hostPattern") and patterns_overlap(s["hostPattern"], host))
    return [f"credential: {', '.join(hit)}"] if hit else []


def host_permit_rows(snap: Snapshot, res: str, effects: str, via: str, status: str, hosts: list[str],
              entries: list[dict], kinds: list[URIRef], facts: list[str]) -> list[tuple]:
    """One row per (host, Cedar permit covering it). Hosts no enabled permit covers get no
    row; with no host at all (no granted credential) the row is Unavailable with "credential: none"."""
    if not hosts:
        return [(res, "-", effects, via, "Unavailable", gate(snap, ["credential: none", *facts], *kinds))]
    rows = []
    for host in hosts:
        matching = [e for e in entries if patterns_overlap(host, e["host"])]
        # A host no enabled permit covers cannot be reached: no row.
        for entry in matching:
            rows.append((res, f"{host}\n{entry['where']}", effects, via, status,
                         gate(snap, [*entry["tokens"], *credential_tokens(snap, host), *facts], *kinds)))
    return rows


def capability_rows(snap: Snapshot) -> list[tuple]:
    m = snap.model
    agent = snap.facts["onecli_agent"]
    entries = policy_entries(snap.facts["gateway_policy"], agent["id"] if agent else None)
    rows = []
    for cap, status in sorted(snap.cap_status.items(), key=lambda kv: short(kv[0])):
        resource = m.value(cap, AR.nativeResourceKind)
        if cap in snap.refined_by or status == UNAVAILABLE or snap.side_of(resource) != AR.ExternalNetwork:
            continue
        endpoint = m.value(cap, AR.nativeEndpointKind)
        effects = ", ".join(sorted(short(e) for e in m.objects(cap, AR.nativeEffectType)))
        name = endpoint_name(snap, endpoint)
        if snap.side_of(endpoint) == AR.ExternalNetwork:
            rows.append((short(resource), "-", effects, f"{name}:\n  executesOn ExternalNetwork", short(status), gate(snap, [], endpoint, resource)))
            continue
        paths = route_paths(snap, endpoint, resource)
        kinds = [k for p in paths for k in p] + [resource]
        if not paths:
            rows.append((short(resource), "-", effects, f"{name}: -", "Unavailable", gate(snap, ["path: none"], endpoint, resource)))
            continue
        for path in paths:
            if not via_gateway(snap, path):
                rows += direct_rows(snap, short(resource), effects, render_paths([path], name), short(status), path, kinds)
                continue
            for entry in entries:
                if name in GET_ONLY and entry["methods"] is not None and "GET" not in entry["methods"]:
                    continue
                rows.append((short(resource), entry["where"], effects, render_paths([path], name), short(status),
                             gate(snap, entry["tokens"] + credential_tokens(snap, entry["host"]), *kinds)))
    return rows


def mcp_rows(snap: Snapshot) -> list[tuple]:
    """Configured remote MCP servers: placed by the gateway's own Cedar decision for the
    MCP initialize request to the server's URL."""
    agent = snap.facts["onecli_agent"]
    env = Path(snap.facts["nanoclaw_root"]) / ".env"
    paths = route_paths(snap, AR.RemoteHTTPMCPTransport, AR.RemoteMCPService)
    kinds = [k for p in paths for k in p] + [AR.RemoteMCPService]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "materialize", "version": "0"}}})
    rows = []
    for name, spec in (snap.facts["config"].get("mcp_servers") or {}).items():
        if not (isinstance(spec, dict) and spec.get("url")):
            continue
        url = urlparse(spec["url"])
        host = (url.hostname or spec["url"]).lower()
        credential = credential_tokens(snap, host)
        result = evaluate(agent["id"], "POST", host, url.path or "/", body, bool(credential), env) if agent else None
        if result is None:
            where, status, facts = f"{host}\ncedar: ?", "Conditional", ["cedar: not evaluated (gateway not reachable)"]
        elif result["decision"] in ("allow", "approval"):
            where, status, facts = f"{host}\ncedar: {result['policy']}", "Available", []
        else:
            continue  # denied: the server cannot be reached, so it is not shown
        rows.append(("RemoteMCPService", where, "Transmit", render_paths(paths, f"mcp_servers.{name}"), status, gate(snap, [*facts, *credential], *kinds)))
    return rows


def message_rows(snap: Snapshot, via_host: dict[URIRef, set[URIRef]]) -> list[tuple]:
    if AR.NanoClawChannelAdapterDelivery not in via_host:
        return []
    tools = ", ".join(sorted(tool_label(e) for e in via_host[AR.NanoClawChannelAdapterDelivery]))
    paths = route_paths(snap, AR.NanoClawHostDeliveryInvocation, AR.MessagingMessage)
    via = render_paths(paths, tools)
    effects = ", ".join(sorted(short(e) for e in step_effects(snap, AR.NanoClawChannelAdapterDelivery))) or "-"
    return [
        ("MessagingMessage", destination_location(snap, d), effects, via, "Available",
         gate(snap, [f"destination: {d['local_name']}"], AR.NanoClawHostDeliveryInvocation, AR.NanoClawChannelAdapterDelivery))
        for d in snap.facts["grants"]
        if d["target_type"] == "channel" and d.get("channel_type") not in LOCAL_CHANNELS
    ]


def remote_rows(snap: Snapshot, via_host: dict[URIRef, set[URIRef]]) -> list[tuple]:
    return capability_rows(snap) + mcp_rows(snap) + message_rows(snap, via_host)
