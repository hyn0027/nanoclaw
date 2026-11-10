"""What this session can cause the host to read or change, outside its container.

Rows: (resource, location, effects, via, status, gate), the layout shared by all
three tables; via is the host step with the entry points that reach it.
Host-side steps are found by walking routesToKind from container-side tool
endpoints; each step's resources are the world kinds it routes to. Host-side
reads come from capabilities whose resource is located on the host. Locations,
effects, and controls are read from the model (hostPathPattern / containsKind,
nativeEffectType, protectedByKind).
"""

from rdflib import URIRef
from rdflib.namespace import RDF

from ...common.namespaces import AR
from ...common.rdf import short
from ..constants import LOCAL_CHANNELS
from ..provider_tools import tool_status
from ..audience import destination_location, destination_lines
from ..snapshot import Snapshot
from .common import gate, kind_location, merge, reported_capabilities, step_effects, tool_label, via_steps


def container_entry_points(snap: Snapshot) -> list[URIRef]:
    """Agent-callable endpoints running in the container, filtered by cli_scope."""
    m, scope = snap.model, snap.cli_scope
    lists = snap.facts["claude_tools"]
    entries = [
        k for k in m.subjects(RDF.type, AR.ToolEndpointKind)
        if snap.side_of(k) == AR.NanoClawAgentContainer
        and (m.value(k, AR.providerToolName) is None or tool_status(lists, str(m.value(k, AR.providerToolName)))[0])
    ]
    ncl_kinds = {
        k for k in entries
        if k == AR.NanoClawContainerNclCLI or AR.NanoClawContainerNclCLI in m.transitive_objects(k, AR.specializesInvocationKind)
    }
    if scope == "disabled":
        return [k for k in entries if k not in ncl_kinds]
    if scope != "global":
        return [k for k in entries if k != AR.NanoClawContainerGlobalAdminCLI]
    return entries


def reachable_host_steps(snap: Snapshot) -> dict[URIRef, set[URIRef]]:
    """Host-side invocation kind -> container entry points that reach it.

    Host delivery is a hub every outbound row passes through, so the walk does
    not continue past it; specific host steps have direct routes in the model.
    """
    m = snap.model
    reached: dict[URIRef, set[URIRef]] = {}
    for entry in container_entry_points(snap):
        todo, seen = [entry], set()
        while todo:
            k = todo.pop()
            if k in seen:
                continue
            seen.add(k)
            if k != entry and snap.side_of(k) == AR.NanoClawHostProcess and (k, RDF.type, AR.InvocationSurfaceKind) in m:
                reached.setdefault(k, set()).add(entry)
            if k == AR.NanoClawHostDeliveryInvocation:
                continue
            todo.extend(t for t in m.objects(k, AR.routesToKind) if (t, RDF.type, AR.InvocationSurfaceKind) in m)
            todo.extend(m.objects(k, AR.specializesInvocationKind))  # inherit routes of broader kinds
    return reached


def resource_effects(snap: Snapshot, resource: URIRef) -> str:
    """Effects of the reported capabilities on ``resource`` (from the model)."""
    m = snap.model
    hits = reported_capabilities(snap, lambda c: m.value(c, AR.nativeResourceKind) == resource)
    return ", ".join(sorted({short(e) for _, _, es in hits for e in es})) or "-"


def route_rows(snap: Snapshot, reached: dict[URIRef, set[URIRef]]) -> list[tuple]:
    m = snap.model
    agent_grants = [d for d in snap.facts["grants"] if d["target_type"] == "agent"]
    rows = []
    for step, sources in sorted(reached.items(), key=lambda kv: short(kv[0])):
        resources = sorted((t for t in m.objects(step, AR.routesToKind) if (t, RDF.type, AR.WorldSurfaceKind) in m), key=short)
        leaves = {e for e in sources if not any(e in m.objects(o, AR.specializesInvocationKind) for o in sources)}
        sources = sorted(tool_label(e) for e in leaves)
        # Observed: agent-to-agent routes need an agent destination grant.
        status = "Unavailable" if step == AR.NanoClawAgentRouteInvocation and not agent_grants else "Available"
        for res in resources:
            if snap.side_of(res) not in (AR.NanoClawHostProcess, None):
                continue
            effects = ", ".join(sorted(short(e) for e in step_effects(snap, step))) or resource_effects(snap, res)
            location = kind_location(snap, res)
            if step == AR.NanoClawAgentRouteInvocation:  # which agent groups, and who is behind them
                location = "\n".join([location, *[f"→ {d['local_name']}" + "".join(f"\n  {l}" for l in destination_lines(snap, d)) for d in agent_grants]])
            rows.append((short(res), location, effects, via_steps(short(step), sources), status, gate(snap, [], step, res)))
    return rows


def host_read_rows(snap: Snapshot) -> list[tuple]:
    """Capabilities on host-located resources (e.g. ncl reads), whose results return to the agent."""
    m = snap.model
    hits = reported_capabilities(snap, lambda c: snap.side_of(m.value(c, AR.nativeResourceKind)) == AR.NanoClawHostProcess)
    rows = []
    for cap, status, effects in sorted(hits, key=lambda h: short(h[0])):
        resource = m.value(cap, AR.nativeResourceKind)
        eff, via, st = merge(snap, [(cap, status, effects)])
        rows.append((short(resource), kind_location(snap, resource), eff, via, st, gate(snap, [], resource, m.value(cap, AR.nativeEndpointKind))))
    return rows


def local_delivery_rows(snap: Snapshot, reached: dict[URIRef, set[URIRef]]) -> list[tuple]:
    """Deliveries to local channels (CLI, Emacs) stay on this machine."""
    if AR.NanoClawChannelAdapterDelivery not in reached:
        return []
    cell = gate(snap, [], AR.NanoClawChannelAdapterDelivery, AR.NanoClawHostDeliveryInvocation)
    via = via_steps("NanoClawChannelAdapterDelivery", sorted(tool_label(e) for e in reached[AR.NanoClawChannelAdapterDelivery]))
    return [
        ("MessagingMessage", destination_location(snap, d), ", ".join(sorted(short(e) for e in step_effects(snap, AR.NanoClawChannelAdapterDelivery))) or "-", via, "Available", cell)
        for d in snap.facts["grants"]
        if d["target_type"] == "channel" and d.get("channel_type") in LOCAL_CHANNELS
    ]


def host_rows(snap: Snapshot) -> tuple[list[tuple], dict[URIRef, set[URIRef]]]:
    """Host table rows, plus the reached host steps whose effect is remote (adapter delivery)."""
    reached = reachable_host_steps(snap)
    remote_via_host = {step: srcs for step, srcs in reached.items() if step == AR.NanoClawChannelAdapterDelivery}
    rows = route_rows(snap, reached) + host_read_rows(snap) + local_delivery_rows(snap, reached)
    return rows, remote_via_host
