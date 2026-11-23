"""Where the installed skills' operations take effect in this session.

For each ``ar:Operation`` a loaded skill declares, each ``ar:hasPotentialEffect``
(effect type, affected kind) is placed by the affected kind's side in the model:

``container``  the observed mounts exposing the kind or a kind it may reside in
               (mutating effects only where the mount is read-write), with the
               host path the mount binds from
``host``       the kind's model location (``hostPathPattern``, else containing kind)
``remote``     the open ``routesToKind`` paths from the operation's endpoints; a
               path through the OneCLI gateway yields one row per Cedar permit
               applying to the agent (for an MCP tool: per the gateway's own
               decision on the tools/call request)

Status follows the operation's ``ar:requires`` exactly as for native
capabilities. Controls are ``ar:protectedByKind`` of the kinds involved and
nothing else (the skill-wide ``ar:subjectToControlKind`` is not attributed to
single effects).
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from rdflib import URIRef
from rdflib.namespace import RDF

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .capabilities import requirement_status
from .constants import MUTATING_EFFECTS
from .gateway_policy import evaluate, policy_entries
from .modifications.common import ancestors, endpoint_name, gate, host_backing, kind_location, location, render_paths, route_paths, subpath
from .modifications.remote import GET_ONLY, credential_tokens, direct_rows, host_permit_rows, via_gateway
from .snapshot import Snapshot


def residence(snap: Snapshot, kind: URIRef) -> set[URIRef]:
    """Kinds ``kind`` (or a kind it specializes) may reside in, transitively."""
    m, seen, todo = snap.model, set(), list(ancestors(snap, kind))
    while todo:
        k = todo.pop()
        for loc in m.objects(k, AR.mayResideInKind):
            if loc not in seen:
                seen.add(loc)
                todo.extend(ancestors(snap, loc))
    return seen


def containers(snap: Snapshot, kind: URIRef) -> list[URIRef]:
    """``kind`` followed by the kinds containing it (``containsKind``), nearest first."""
    m, order, todo = snap.model, [kind], [kind]
    while todo:
        k = todo.pop(0)
        for a in sorted(ancestors(snap, k), key=short):
            for parent in sorted(m.subjects(AR.containsKind, a), key=short):
                if parent not in order:
                    order.append(parent)
                    todo.append(parent)
    return order


def effective_side(snap: Snapshot, kind: URIRef) -> URIRef | None:
    """The kind's side, else the side of a kind it may reside in, else of a containing kind."""
    for k in containers(snap, kind):
        side = snap.side_of(k)
        if side is not None:
            return side
        for home in sorted(residence(snap, k), key=short):
            side = snap.side_of(home)
            if side is not None:
                return side
    return None


def container_locations(snap: Snapshot, kind: URIRef) -> list[tuple[str, str, bool]]:
    """(container path, host backing, writable) of the observed mounts exposing ``kind``,
    else exposing the nearest kind containing it."""
    for k in containers(snap, kind):
        locs = mount_locations(snap, k)
        if locs:
            return locs
    return []


def mount_locations(snap: Snapshot, kind: URIRef) -> list[tuple[str, str, bool]]:
    """Mounts exposing ``kind`` itself (or a specialization); only when there are
    none, mounts exposing a kind it may reside in. A mount exposing the kind
    itself shadows its parent mounts, as the nested mount does in the container."""
    homes = residence(snap, kind)
    exact = [mnt for mnt in snap.mounts if any(
        (t := snap.graph.value(loc, RDF.type)) is not None and kind in ancestors(snap, t)
        for loc in snap.graph.objects(snap.node(f"mount{mnt['Destination']}"), OBS.exposes))]
    out = []
    for mnt in exact or snap.mounts:
        dest = mnt["Destination"]
        for loc in snap.graph.objects(snap.node(f"mount{dest}"), OBS.exposes):
            exposed = snap.graph.value(loc, RDF.type)
            if exposed is None or not (kind in ancestors(snap, exposed) or ancestors(snap, exposed) & homes):
                continue
            tail = subpath(kind, exposed)
            hpath = host_backing(snap, dest)
            out.append((f"{dest}/{tail}" if tail else dest, f"{hpath}/{tail}" if tail else hpath, bool(mnt["RW"])))
            break
    return sorted(set(out))


def call_paths(m, op: URIRef) -> list[str]:
    """Request paths of an operation's internet calls (callEndpointPattern)."""
    return sorted({urlparse(str(p)).path or "/" for call in m.objects(op, AR.mayInitiateInternetCall)
                   for p in m.objects(call, AR.callEndpointPattern) if "{" not in str(p)}) or ["/"]


def mcp_tool_rows(snap: Snapshot, res: str, effects: str, route: str, status: str, hosts: list[str], tool: str,
                  paths: list[str], kinds: list[URIRef], facts: list[str], env: Path) -> list[tuple]:
    """One row per host, placed by the gateway's own Cedar decision for the tools/call
    request the tool sends, under the deciding permit. A denied call gets no row."""
    agent = snap.facts["onecli_agent"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}}})
    rows = []
    for host in hosts:
        credential = credential_tokens(snap, host)
        for path in paths:
            result = evaluate(agent["id"], "POST", host, path, body, bool(credential), env) if agent else None
            if result is None:
                rows.append((res, f"{host}\ncedar: ?", effects, route, "Conditional", gate(snap, ["cedar: not evaluated (gateway not reachable)", *credential, *facts], *kinds)))
            elif result["decision"] in ("allow", "approval"):
                extra = ["@approval: held for a human"] if result["decision"] == "approval" else []
                rows.append((res, f"{host}\ncedar: {result['policy']}", effects, route, status, gate(snap, [*extra, *credential, *facts], *kinds)))
            # Denied (no enabled permit, or a forbid): it cannot happen, so it is not shown.
    return rows


def assess_operations(snap: Snapshot) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """(inside, host, remote) rows for the installed skills' operations, in the
    same column layout as the native rows of those tables. The via column names
    the operation: "<skill>/<operation> (<tools>)". Effects on a kind with no
    side in the model become findings."""
    m = snap.model
    agent = snap.facts["onecli_agent"]
    entries = policy_entries(snap.facts["gateway_policy"], agent["id"] if agent else None)
    inside, host, remote = [], [], []
    mcp_names = {server_id: name for name, server_id in snap.facts["modeled_mcp"].items()}
    sources = [(s, str(m.value(s, AR.skillDirectory) or short(s)), None) for s in sorted(m.subjects(RDF.type, AR.Skill), key=short)
               if str(m.value(s, AR.skillDirectory) or short(s)) in snap.facts["modeled_skills"]]
    sources += [(s, f"mcp:{mcp_names[str(m.value(s, AR.mcpServerId))]}", mcp_names[str(m.value(s, AR.mcpServerId))])
                for s in sorted(m.subjects(RDF.type, AR.MCPServer), key=short) if str(m.value(s, AR.mcpServerId)) in mcp_names]
    env = Path(snap.facts["nanoclaw_root"]) / ".env"
    for skill, name, mcp_name in sources:
        for op in sorted(m.objects(skill, AR.declaresOperation), key=short):
            status, blocked, pending = requirement_status(snap, m.objects(op, AR.requires))
            # Hosts the operation's internet calls name (callEndpointPattern), if any.
            # A pattern with a {placeholder} names no concrete host.
            hosts = sorted({urlparse(str(pat)).hostname or str(pat) for call in m.objects(op, AR.mayInitiateInternetCall)
                            for pat in m.objects(call, AR.callEndpointPattern) if "{" not in str(pat)})
            # Requirements not satisfied, with their evidence, shown in every gate of the operation.
            req_facts = [f"{'blocked' if r in blocked else 'pending'}: {short(r)} ({snap.conditions[r][1]})"
                         for r in sorted(blocked + pending) if r in snap.conditions]
            st = short(status)
            endpoints = sorted(m.objects(op, AR.invokedThroughKind), key=short)
            tool_name = m.value(op, AR.mcpToolName)
            if mcp_name and tool_name is not None:
                # An MCP tool: the name the agent's provider calls it by.
                label = via = tools = f"mcp__{mcp_name}__{tool_name}"
            else:
                tools = ", ".join(sorted({endpoint_name(snap, e) for e in endpoints})) or "-"
                label = f"{name}/{short(op)}"
                via = f"{label} ({tools})"
            effects: dict[URIRef, set[URIRef]] = {}
            for eff in m.objects(op, AR.hasPotentialEffect):
                kind, etype = m.value(eff, AR.affectsKind), m.value(eff, AR.effectType)
                if kind is not None and etype is not None:
                    effects.setdefault(kind, set()).add(etype)

            def controls_of(*kinds: URIRef, extra: list[str] | None = None) -> str:
                return gate(snap, (extra or []) + req_facts, *kinds)

            def eff_text(es: set[URIRef]) -> str:
                return ", ".join(sorted(short(e) for e in es))

            for kind, etypes in sorted(effects.items(), key=lambda kv: short(kv[0])):
                side = effective_side(snap, kind)
                res = short(kind)
                if side == AR.NanoClawAgentContainer:
                    locs = container_locations(snap, kind)
                    if not locs:
                        inside.append((res, "-", eff_text(etypes), via, st, controls_of(kind, *endpoints)))
                    for cpath, hpath, writable in locs:
                        where = location(cpath, "rw" if writable else "ro", hpath)
                        allowed = etypes if writable else etypes - MUTATING_EFFECTS
                        if allowed:
                            inside.append((res, where, eff_text(allowed), via, st, controls_of(kind, *endpoints)))
                        if etypes - allowed:
                            inside.append((res, where, eff_text(etypes - allowed), via, "Unavailable", controls_of(kind, *endpoints, extra=["mount: ro"])))
                elif side == AR.ExternalNetwork:
                    paths = [p for e in endpoints for p in route_paths(snap, e, kind)]
                    if not paths:
                        remote.append((res, "-", eff_text(etypes), f"{via}: -", "Unavailable", controls_of(kind, *endpoints, extra=["path: none"])))
                    for path in paths:
                        route = render_paths([path], via)
                        kinds = [*path, kind]
                        if not via_gateway(snap, path):
                            remote += direct_rows(snap, res, eff_text(etypes), route, st, path, kinds, req_facts)
                            continue
                        # Hosts: those the call names (the credential for a host shows in its row).
                        if mcp_name and tool_name is not None and hosts:
                            # Decided by the gateway itself: the tools/call request this tool sends.
                            remote += mcp_tool_rows(snap, res, eff_text(etypes), route, st, hosts, str(tool_name), call_paths(m, op), kinds, req_facts, env)
                            continue
                        if not hosts:
                            # The call names no endpoint: it can reach whatever a permit allows.
                            for entry in entries:
                                if tools in GET_ONLY and entry["methods"] is not None and "GET" not in entry["methods"]:
                                    continue
                                remote.append((res, entry["where"], eff_text(etypes), route, st,
                                               controls_of(*kinds, extra=entry["tokens"] + credential_tokens(snap, entry["host"]))))
                            continue
                        remote += host_permit_rows(snap, res, eff_text(etypes), route, st, hosts, entries, kinds, req_facts)
                elif side is not None:
                    host.append((res, kind_location(snap, kind), eff_text(etypes), via, st, controls_of(kind, *endpoints)))
                else:
                    snap.findings.append(f"{label}: {res}.side=none; effects={eff_text(etypes)}")
    return inside, host, remote
