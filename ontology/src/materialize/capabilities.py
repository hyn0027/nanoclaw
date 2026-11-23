"""Assess the model's native capabilities for this session.

A capability is available when all its requirements are satisfied, conditional
when some are unknown, and unavailable when any is unsatisfied.
"""

from rdflib import URIRef
from rdflib.namespace import RDF

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .constants import AVAILABLE, CONDITIONAL, OBSERVED, UNAVAILABLE, UNKNOWN, UNSATISFIED
from .snapshot import Snapshot


def capability_scope(snap: Snapshot, cap: URIRef) -> list[URIRef]:
    """Concrete instances a capability can reach in this session."""
    scope_kind = next(snap.model.objects(cap, AR.nativeScopeKind), None)
    if short(cap) == "NativeMessageDelivery":
        return [snap.node(f"grant_{d['local_name']}") for d in snap.facts["grants"]]
    hits = list(snap.graph.subjects(RDF.type, scope_kind)) if scope_kind else []
    if hits:
        return hits
    if scope_kind == AR.NanoClawContainerFileSystem:
        return filesystem_scope(snap, cap)
    return []


def filesystem_scope(snap: Snapshot, cap: URIRef) -> list[URIRef]:
    """Reads reach every mount; writes only read-write mounts and the writable container layer."""
    writes = bool(set(snap.model.objects(cap, AR.nativeEffectType)) & {AR.Create, AR.Update, AR.Delete})
    reach = [snap.node(f"mount{m['Destination']}") for m in snap.mounts if m["RW"] or not writes]
    if writes and not snap.facts["inspect"]["HostConfig"].get("ReadonlyRootfs"):
        reach.append(snap.add(snap.node("container_layer"), AR.NanoClawContainerLayer, OBSERVED, "container layer"))
    return reach


def requirement_status(snap: Snapshot, reqs) -> tuple[URIRef, list[URIRef], list[URIRef]]:
    """(status, unsatisfied requirements, unknown ones) of a capability or operation."""
    statuses = {r: snap.conditions.get(r, (UNKNOWN, ""))[0] for r in sorted(reqs)}
    blocked = [r for r, s in statuses.items() if s == UNSATISFIED]
    pending = [r for r, s in statuses.items() if s == UNKNOWN]
    return (UNAVAILABLE if blocked else CONDITIONAL if pending else AVAILABLE), blocked, pending


def assess_capabilities(snap: Snapshot) -> None:
    """Status of every native capability, recorded in the snapshot graph and in
    ``snap.cap_status``; also fills ``snap.refined_by``.

    A generic capability with provider-specific refinements keeps its own
    status, but the refinements are what later tables use.
    """
    for refined, generic in snap.model.subject_objects(AR.refinesCapability):
        snap.refined_by.setdefault(generic, []).append(refined)
    for cap in sorted(set(snap.model.subjects(RDF.type, AR.NativeCapability))):
        status, blocked, pending = requirement_status(snap, snap.model.objects(cap, AR.nativeRequires))
        snap.cap_status[cap] = status
        snap.add(snap.node(f"capability_{short(cap)}"), OBS.CapabilityAssessment, OBSERVED, short(cap), capability=cap, status=status,
                 blockedBy=blocked, pendingOn=pending, scopedTo=capability_scope(snap, cap))
