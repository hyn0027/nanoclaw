"""What this session can read or change inside its container.

Rows: (resource, location, effects, via, status, gate), the layout shared by
all three tables. The location is the container path with its mount mode and the
host path it binds from: a write through a bind mount also changes that host
path; a read through one reads it. The gate lists the model's
``ar:protectedByKind`` of the resource and endpoint kinds.
"""

from rdflib import URIRef
from rdflib.namespace import RDF

from ...common.namespaces import AR, OBS
from ...common.rdf import short
from ..constants import CONTAINER_ONLY, MUTATING_EFFECTS, SUBPATHS
from ..snapshot import Snapshot
from .common import CapabilityHit, gate, host_backing, location, merge, reported_capabilities

# Container paths of resources targeted by a specific capability.
KNOWN_PATHS = {
    AR.NanoClawInboundMailbox: "/workspace/inbound.db",
    AR.NanoClawOutboundMessage: "/workspace/outbound.db",
    AR.NanoClawOutboxArtifact: "/workspace/outbox/",
    AR.NanoClawProviderStateDirectory: "/home/node/.claude",
    AR.NanoClawTaskRunLog: "/workspace/agent/tasks/",
    AR.ClaudeTodoList: "/home/node/.claude/todos/",
    AR.ClaudeSkillDefinition: "/home/node/.claude/skills/",
}

LAYER_PATH = "/"


def exposed_kinds(snap: Snapshot, dest: str) -> list[URIRef]:
    return [snap.graph.value(loc, RDF.type) for loc in snap.graph.objects(snap.node(f"mount{dest}"), OBS.exposes)]


def locations(snap: Snapshot) -> tuple[list[tuple[URIRef, str | None, bool]], set[URIRef]]:
    """(location kind, mount path or None for the container layer, writable) and
    the kinds exposed by read-only mounts.

    A kind exposed by a mount is skipped as a location when it resides in
    another kind the same mount exposes (it becomes a sub-row instead).
    """
    m = snap.model
    ro_kinds, found = set(), []
    for mnt in snap.mounts:
        exposed = exposed_kinds(snap, mnt["Destination"])
        for kind in exposed:
            nested = any(other in m.objects(kind, AR.mayResideInKind) for other in exposed if other != kind)
            if not nested:
                found.append((kind, mnt["Destination"], bool(mnt["RW"])))
            if not mnt["RW"]:
                ro_kinds.add(kind)
    if not snap.facts["inspect"]["HostConfig"].get("ReadonlyRootfs"):
        found.append((AR.NanoClawContainerLayer, None, True))
    return found, ro_kinds


def location_rows(snap: Snapshot) -> list[tuple]:
    """Each container location and the modeled resources residing in it.

    Read capabilities reach every location; write capabilities only writable ones.
    """
    m = snap.model
    fs_caps = reported_capabilities(snap, lambda c: m.value(c, AR.nativeScopeKind) == AR.NanoClawContainerFileSystem)
    read_caps = [hit for hit in fs_caps if not hit[2] & MUTATING_EFFECTS]
    found, ro_kinds = locations(snap)
    rows = []
    for kind, dest, writable in found:
        caps = fs_caps if writable else read_caps
        if not caps:
            continue
        effects, via, status = merge(snap, caps)
        cpath = dest or LAYER_PATH
        hpath = host_backing(snap, dest) if dest else CONTAINER_ONLY
        mode = "rw" if writable else "ro"
        endpoints = [m.value(c, AR.nativeEndpointKind) for c, _, _ in caps]
        rows.append((short(kind), location(cpath, mode, hpath), effects, via, status, gate(snap, [], kind, *endpoints)))
        for sub in sorted(m.subjects(AR.mayResideInKind, kind)):
            # A sub-kind behind its own read-only mount gets that mount's row instead.
            if (writable and sub in ro_kinds) or (sub, RDF.type, AR.WorldSurfaceKind) not in m:
                continue
            tail = SUBPATHS.get(sub, "")
            if isinstance(tail, dict):
                tail = tail.get(kind, "")
            sub_c = f"{dest}/{tail}" if dest and tail else cpath
            sub_h = f"{hpath}/{tail}" if dest and tail else hpath
            rows.append((f"└ {short(sub)}", location(sub_c, mode, sub_h), effects, via, status, gate(snap, [], sub, *endpoints)))
    return rows


def targeted_rows(snap: Snapshot) -> list[tuple]:
    """Capabilities aimed at a specific in-container resource.

    Delivery (a host-side step), host-side resources, and remote resources go to
    the other tables.
    """
    m = snap.model
    specific = reported_capabilities(
        snap,
        lambda c: m.value(c, AR.nativeScopeKind) != AR.NanoClawContainerFileSystem
        and m.value(c, AR.nativeResourceKind) != AR.NanoClawDestinationGrant
        and snap.side_of(m.value(c, AR.nativeResourceKind)) not in (AR.ExternalNetwork, AR.NanoClawHostProcess),
    )
    grouped: dict[URIRef, list[CapabilityHit]] = {}
    for item in specific:
        grouped.setdefault(m.value(item[0], AR.nativeResourceKind), []).append(item)
    rows = []
    for kind, items in sorted(grouped.items(), key=lambda kv: short(kv[0])):
        effects, via, status = merge(snap, items)
        cpath, mode = KNOWN_PATHS.get(kind), "-"
        if cpath:
            parent = max((x for x in snap.mounts if cpath.startswith(x["Destination"])), key=lambda x: len(x["Destination"]), default=None)
            hpath = f"{host_backing(snap, parent['Destination'])}{cpath[len(parent['Destination']):]}" if parent else CONTAINER_ONLY
            mode = ("rw" if parent["RW"] else "ro") if parent else "-"
        else:
            cpath, hpath = "-", CONTAINER_ONLY
        endpoints = [m.value(c, AR.nativeEndpointKind) for c, _, _ in items]
        rows.append((short(kind), location(cpath, mode, hpath), effects, via, status, gate(snap, [], kind, *endpoints)))
    return rows


def inside_rows(snap: Snapshot) -> list[tuple]:
    return location_rows(snap) + targeted_rows(snap)
