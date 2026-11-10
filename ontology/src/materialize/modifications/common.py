"""Helpers shared by the inside / host / remote modification tables."""

import re
from collections.abc import Callable

from rdflib import URIRef

from ...common.namespaces import AR
from ...common.rdf import short
from ..constants import CONTAINER_ONLY, REPORTED_EFFECTS, STATUS_RANK, SUBPATHS, UNAVAILABLE
from ..provider_tools import tool_status, tools_implementing
from ..snapshot import Snapshot

# (capability, status, reported effect types)
CapabilityHit = tuple[URIRef, URIRef, set[URIRef]]

ENDPOINT_NAMES = {
    AR.NanoClawContainerShellExecution: "shell",
    AR.NanoClawProviderFileTool: "file tools",
    AR.NanoClawSendMessageTool: "send_message",
    AR.NanoClawSendFileTool: "send_file",
    AR.NanoClawProviderSubagentTool: "subagent tool",
    AR.NanoClawProviderWebFetchTool: "web fetch",
    AR.NanoClawProviderWebSearchTool: "web search",
    AR.NanoClawContainerTasksCLI: "ncl tasks",
    AR.NanoClawContainerGroupAdminCLI: "ncl (own group)",
    AR.NanoClawContainerGlobalAdminCLI: "ncl (global scope)",
}


def reported_capabilities(snap: Snapshot, pred: Callable[[URIRef], bool]) -> list[CapabilityHit]:
    """Capabilities that are not unavailable, read or change something, and match ``pred``.

    Requires ``assess_capabilities`` to have filled ``snap.cap_status``.
    """
    out = []
    for cap, status in snap.cap_status.items():
        if cap in snap.refined_by:
            continue  # reported through its provider-specific refinements
        effects = set(snap.model.objects(cap, AR.nativeEffectType)) & REPORTED_EFFECTS
        if effects and status != UNAVAILABLE and pred(cap):
            out.append((cap, status, effects))
    return out


def merge(snap: Snapshot, items: list[CapabilityHit]) -> tuple[str, str, str]:
    """(effects, via, best status) for a group of capabilities, as display strings."""
    m = snap.model
    effects = sorted({short(e) for _, _, es in items for e in es})
    via = sorted({endpoint_name(snap, m.value(c, AR.nativeEndpointKind)) for c, _, _ in items})
    status = min((st for _, st, _ in items), key=lambda st: STATUS_RANK[st])
    return ", ".join(effects), ", ".join(via), short(status)


def endpoint_name(snap: Snapshot, endpoint: URIRef) -> str:
    """Provider tool name (e.g. "Write"); for a generic endpoint, the enabled
    provider tools implementing it (e.g. "Bash"); else a friendly or local name."""
    name = snap.model.value(endpoint, AR.providerToolName)
    if name is not None:
        return str(name)
    enabled = [n for _, n in tools_implementing(snap.model, endpoint) if tool_status(snap.facts["claude_tools"], n)[0]]
    if enabled:
        return ", ".join(enabled)
    return ENDPOINT_NAMES.get(endpoint, short(endpoint))


SPECIALIZES = (AR.specializesKind, AR.specializesInvocationKind)


def ancestors(snap: Snapshot, kind: URIRef) -> set[URIRef]:
    """``kind`` and every kind it specializes, transitively."""
    m, seen, todo = snap.model, set(), [kind]
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        for p in SPECIALIZES:
            todo.extend(m.objects(k, p))
    return seen


def control_kinds(snap: Snapshot, *kinds: URIRef | None) -> set[URIRef]:
    """``ar:protectedByKind`` of the kinds and of everything they specialize."""
    return {c for k in kinds if k is not None for a in ancestors(snap, k) for c in snap.model.objects(a, AR.protectedByKind)}


def controls(snap: Snapshot, *kinds: URIRef | None) -> str:
    return ", ".join(sorted(short(c) for c in control_kinds(snap, *kinds))) or "-"


def gate(snap: Snapshot, facts: list[str], *kinds: URIRef | None) -> str:
    """Gate cell: one "key: value" fact per line, then the model's controls as an indented list."""
    found = sorted(short(c) for c in control_kinds(snap, *kinds))
    lines = list(facts) + (["controls:"] + [f"  {c}" for c in found] if found else [])
    return "\n".join(lines) or "-"


def location(cpath: str, mode: str | None = None, hpath: str | None = None) -> str:
    """Container location cell: the container path with its mount mode, and the host path it binds from."""
    text = f"{cpath} ({mode})" if mode and mode != "-" else cpath
    return text + (f"\nhost: {hpath}" if hpath and hpath != "-" else "")


def via_steps(head: str, sources: list[str], arrow: str = "←") -> str:
    """A step followed by its sources, one per indented line."""
    return "\n".join([head] + [f"  {arrow} {s}" for s in sources])


def fill_pattern(snap: Snapshot, pattern: str) -> str:
    """A model host-path pattern with this session's observed identifiers filled in."""
    f = snap.facts
    values = {"folder": f["group"].get("folder"), "agentGroupId": f["group"]["id"], "sessionId": f["session"]["id"],
              "image": f["inspect"]["Config"].get("Image")}
    return re.sub(r"\{(\w+)\}", lambda mt: values.get(mt.group(1)) or mt.group(0), pattern)


def subpath(kind: URIRef, parent: URIRef) -> str:
    tail = SUBPATHS.get(kind, "")
    return tail.get(parent, "") if isinstance(tail, dict) else tail


def host_paths(snap: Snapshot, kind: URIRef, seen: frozenset = frozenset()) -> list[str]:
    """Host paths of a kind, from the model, in this order:

    1. its own ``hostPathPattern`` (inherited along specializesKind);
    2. the ``hostPathPattern`` of what a mount exposing it ``bindsFromKind``;
    3. the paths of the kinds containing it (``containsKind``) or that it may
       reside in, plus its subpath there: kinds on its own side first, kinds
       with no side only when those give no path.
    """
    m = snap.model
    for a in sorted(ancestors(snap, kind)):
        pattern = m.value(a, AR.hostPathPattern)
        if pattern is not None:
            return [fill_pattern(snap, str(pattern))]
    bound = sorted({
        fill_pattern(snap, str(m.value(m.value(mount, AR.bindsFromKind), AR.hostPathPattern)))
        for a in ancestors(snap, kind) for mount in m.subjects(AR.mountExposesKind, a)
        if m.value(m.value(mount, AR.bindsFromKind), AR.hostPathPattern) is not None
    })
    if bound:
        return bound
    side = snap.side_of(kind)
    parents = sorted(set(m.subjects(AR.containsKind, kind)) | set(m.objects(kind, AR.mayResideInKind)) - seen - {kind})
    # Parents on the kind's own side first; parents with no side only when those yield nothing.
    same = [p for p in parents if side is not None and snap.side_of(p) == side]
    unsided = [p for p in parents if snap.side_of(p) is None or side is None]
    for tier in (same, unsided):
        out = []
        for parent in tier:
            tail = subpath(kind, parent)
            out += [f"{p.rstrip('/')}/{tail}" if tail else p for p in host_paths(snap, parent, seen | {kind})]
        if out:
            return sorted(set(out))
    return []


def kind_location(snap: Snapshot, kind: URIRef) -> str:
    """Where a host-visible kind lives: its host paths (``host_paths``), one per line;
    else the system component it is located on."""
    paths = host_paths(snap, kind)
    if paths:
        return "\n".join(paths)
    side = snap.side_of(kind)
    return short(side) if side is not None else "-"


def step_effects(snap: Snapshot, step: URIRef) -> set[URIRef]:
    """``ar:stepEffectType`` of a step and the kinds it specializes."""
    return {e for a in ancestors(snap, step) for e in snap.model.objects(a, AR.stepEffectType)}


def blocked_by_observation(snap: Snapshot, kind: URIRef) -> bool:
    """A route step is closed when a control it is protected by is observed active."""
    return snap.lockdown_on and AR.NanoClawEgressLockdownControl in control_kinds(snap, kind)


def route_paths(snap: Snapshot, start: URIRef, resource: URIRef, max_depth: int = 8) -> list[list[URIRef]]:
    """Open ``routesToKind`` paths from ``start`` to the first kind on ``resource``'s side.

    Routes are inherited from specialized kinds; steps closed by an observed
    control (``blocked_by_observation``) are skipped.
    """
    m, target, out = snap.model, snap.side_of(resource), []

    def walk(path: list[URIRef]) -> None:
        k = path[-1]
        if len(path) > 1 and (k == resource or snap.side_of(k) == target):
            out.append(path)
            return
        if len(path) > max_depth:
            return
        for n in sorted({t for a in ancestors(snap, k) for t in m.objects(a, AR.routesToKind)}):
            if n not in path and not blocked_by_observation(snap, n):
                walk(path + [n])

    if target is not None:
        walk([start])
    return drop_subsumed(out)


def drop_subsumed(paths: list[list[URIRef]]) -> list[list[URIRef]]:
    """Drop a path whose steps all appear, in order, in a longer path (a shortcut
    edge summarizing the longer route); the detailed path carries every step's controls."""
    def within(short_path: list[URIRef], long_path: list[URIRef]) -> bool:
        it = iter(long_path)
        return len(short_path) < len(long_path) and all(k in it for k in short_path)
    return [p for p in paths if not any(within(p, q) for q in paths)]


def render_paths(paths: list[list[URIRef]], tool: str | None = None) -> str:
    """Route paths, one step per line ("→" indented under the first), under an optional tool label."""
    if not paths:
        return f"{tool}: -" if tool else "-"
    pad = "  " if tool else ""
    blocks = ["\n".join([pad + short(p[0])] + [f"{pad}  → {short(k)}" for k in p[1:]]) for p in paths]
    return "\n".join(([f"{tool}:"] if tool else []) + blocks)


def host_backing(snap: Snapshot, dest: str | None) -> str:
    """Host path behind a container mount path, or 'container only'."""
    mnt = next((x for x in snap.mounts if x["Destination"] == dest), None)
    return snap.host_path(mnt.get("Source")) if mnt else CONTAINER_ONLY


def tool_label(kind: URIRef) -> str:
    """NanoClaw MCP tools by their real names (NanoClawSendMessageTool -> send_message);
    other kinds by their local name without the NanoClaw prefix."""
    local = short(kind)
    if local.startswith("NanoClaw") and local.endswith("Tool"):
        return re.sub(r"(?<!^)(?=[A-Z])", "_", local[len("NanoClaw"):-len("Tool")]).lower().replace("m_c_p", "mcp")
    return local.replace("NanoClaw", "")


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def _union(values: list[str]) -> str:
    """Comma lists merge item by item; multi-line or structured cells join as whole blocks.

    Blocks of the form "<label>:" followed by an identical indented body (the
    same route under different tools or operations) are shown once, under all
    their labels.
    """
    if not any("\n" in v or mark in v for v in values for mark in ("←", "→", ":", "(")):
        return ", ".join(sorted({item for v in values for item in _split(v)}))
    bodies: dict[str, list[str]] = {}
    for value in dict.fromkeys(values):
        head, _, body = value.partition("\n")
        if head.endswith(":") and body:
            bodies.setdefault(body, []).append(head)
        else:
            bodies.setdefault(value, [])
    return "\n".join("\n".join(heads + [body]) if heads else body for body, heads in bodies.items())


def merge_rows(rows: list[tuple], key_width: int, effects: int, via: int, gate: int) -> list[tuple]:
    """Combine rows for the same resource at the same location (first ``key_width`` columns).

    Effects and via are unioned and statuses listed one per line. When the merged
    rows have different gates, each gate is listed under a "[...]" line naming
    what it applies to.
    """
    status = gate - 1
    groups: dict[tuple, list[tuple]] = {}
    for row in rows:
        groups.setdefault((row[0].strip(" └"),) + tuple(row[1:key_width]), []).append(row)
    out = []
    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        merged = list(group[0])
        merged[effects] = ", ".join(sorted({e for r in group for e in _split(r[effects])}))
        merged[via] = _union([r[via] for r in group])
        statuses = list(dict.fromkeys(r[status] for r in group))
        merged[status] = "\n".join(statuses)
        gates = list(dict.fromkeys(r[gate] for r in group))
        if len(gates) == 1:
            merged[gate] = gates[0]
        else:
            # Say what each gate applies to: its effects (and status) when those
            # differ between the rows, else the row's via step.
            by_effects = len({(r[effects], r[status]) for r in group}) > 1

            def label(r: tuple) -> str:
                if by_effects:
                    return r[effects] + (f" · {r[status]}" if len(statuses) > 1 else "")
                return r[via].splitlines()[0].rstrip(":")

            parts: dict[str, list[str]] = {}
            for r in group:
                parts.setdefault(label(r), [])
                if r[gate] not in parts[label(r)]:
                    parts[label(r)].append(r[gate])
            merged[gate] = "\n".join(
                f"[{name}]\n" + "\n".join("  " + line for g in gs for line in g.splitlines())
                for name, gs in parts.items()
            )
        out.append(tuple(merged))
    return out
