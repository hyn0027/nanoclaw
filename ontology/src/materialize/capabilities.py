"""Assess the model's native capabilities for this session.

A capability is available when all its requirements are satisfied, conditional
when some are unknown, and unavailable when any is unsatisfied.
"""

from rdflib import URIRef
from rdflib.namespace import RDF, RDFS

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .constants import AVAILABLE, CONDITIONAL, OBSERVED, UNAVAILABLE, UNKNOWN, UNSATISFIED
from .snapshot import Snapshot


def capability_scope(snap: Snapshot, cap: URIRef) -> list[tuple[URIRef, str]]:
    """Concrete instances a capability can reach in this session."""
    scope_kind = next(snap.model.objects(cap, AR.nativeScopeKind), None)
    if short(cap) == "NativeMessageDelivery":
        return [
            (snap.node(f"grant_{d['local_name']}"), f"{d['local_name']} ({d['target_type']}:{d.get('channel_type') or d['target_id']})")
            for d in snap.facts["grants"]
        ]
    hits = [(s, str(snap.graph.value(s, RDFS.label))) for s in snap.graph.subjects(RDF.type, scope_kind)] if scope_kind else []
    if hits:
        return hits
    if scope_kind == AR.NanoClawContainerFileSystem:
        return filesystem_scope(snap, cap)
    return []


def filesystem_scope(snap: Snapshot, cap: URIRef) -> list[tuple[URIRef, str]]:
    """Reads reach every mount; writes only read-write mounts and the writable container layer."""
    effects = set(snap.model.objects(cap, AR.nativeEffectType))
    writes = bool(effects & {AR.Create, AR.Update, AR.Delete})
    mounts = [m for m in snap.mounts if m["RW"] or not writes]
    reach = [(snap.node(f"mount{m['Destination']}"), f"{m['Destination']} ({'rw' if m['RW'] else 'ro'})") for m in mounts]
    if writes and not snap.facts["inspect"]["HostConfig"].get("ReadonlyRootfs"):
        layer = snap.add(snap.node("container_layer"), AR.NanoClawContainerLayer, OBSERVED, "container layer")
        reach.append((layer, "NanoClawContainerLayer (rw)"))
    return reach


def endpoint_label(snap: Snapshot, cap: URIRef) -> str:
    """The provider tool name of a capability's endpoint, else its kind's local name."""
    endpoint = snap.model.value(cap, AR.nativeEndpointKind)
    name = snap.model.value(endpoint, AR.providerToolName)
    return str(name) if name is not None else short(endpoint)


def assess_capabilities(snap: Snapshot) -> list[dict]:
    """Status of every native capability; also fills ``snap.cap_status`` and ``snap.refined_by``.

    A generic capability with provider-specific refinements keeps its own
    status, but the refinements are what later tables use.
    """
    for refined, generic in snap.model.subject_objects(AR.refinesCapability):
        snap.refined_by.setdefault(generic, []).append(refined)
    rows = []
    for cap in sorted(set(snap.model.subjects(RDF.type, AR.NativeCapability))):
        reqs = sorted(snap.model.objects(cap, AR.nativeRequires))
        statuses = {r: snap.conditions.get(r, (UNKNOWN, ""))[0] for r in reqs}
        blocked = [r for r, s in statuses.items() if s == UNSATISFIED]
        pending = [r for r, s in statuses.items() if s == UNKNOWN]
        status = UNAVAILABLE if blocked else CONDITIONAL if pending else AVAILABLE
        snap.cap_status[cap] = status
        scope = capability_scope(snap, cap)
        snap.add(snap.node(f"capability_{short(cap)}"), OBS.CapabilityAssessment, OBSERVED, short(cap), capability=cap, status=status, blockedBy=blocked, pendingOn=pending, scopedTo=[s for s, _ in scope])
        why = "; ".join([f"blocked: {short(r)}" for r in blocked] + [f"pending: {short(r)}" for r in pending]) or "-"
        if cap in snap.refined_by:
            why = "refined_by=" + ", ".join(sorted(short(r) for r in snap.refined_by[cap])) + f"; {why}"
        rows.append({
            "capability": short(cap),
            "endpoint": endpoint_label(snap, cap),
            "status": short(status),
            "effect": ", ".join(short(e) for e in sorted(snap.model.objects(cap, AR.nativeEffectType))),
            "resource": short(next(snap.model.objects(cap, AR.nativeResourceKind))),
            "why": why,
            "scope": ", ".join(label for _, label in scope) or "-",
        })
    return rows
