"""Per-tool view of the provider's built-in tools for this session."""

from rdflib import URIRef

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .constants import OBSERVED
from .provider_tools import provider_tools, tool_status
from .snapshot import Snapshot


def implemented_kind(snap: Snapshot, kind: URIRef) -> str:
    """The NanoClaw (or generic) endpoint kind a provider tool specializes."""
    parents = sorted(short(p) for p in snap.model.objects(kind, AR.specializesInvocationKind))
    return ", ".join(parents) or "-"


def tool_capabilities(snap: Snapshot, kind: URIRef) -> list[URIRef]:
    """Capabilities this tool provides: its own, plus generic ones on the endpoint it
    implements that no provider-specific refinement replaces."""
    m = snap.model
    own = list(m.subjects(AR.nativeEndpointKind, kind))
    if own:
        return own
    parents = set(m.transitive_objects(kind, AR.specializesInvocationKind)) - {kind}
    return [c for p in parents for c in m.subjects(AR.nativeEndpointKind, p) if c not in snap.refined_by]


def assess_tools(snap: Snapshot) -> list[tuple[str, str, str, str, str]]:
    """Rows (tool, status, implements, runs on, capabilities with their status).

    Call after ``assess_capabilities``. Each tool is also recorded as an
    instance of its tool kind with obs:enabled.
    """
    lists = snap.facts["claude_tools"]
    claude = (snap.facts["config"].get("provider") or "claude").lower() == "claude"
    rows = []
    for kind, name in provider_tools(snap.model):
        enabled, source = tool_status(lists, name) if claude else (False, f"provider={snap.facts['config'].get('provider')}")
        reason = f"{'enabled' if enabled else 'disabled'} ({source})"
        caps = tool_capabilities(snap, kind)
        caps_text = ", ".join(f"{short(c)} ({short(snap.cap_status[c])})" for c in sorted(caps, key=short) if c in snap.cap_status) or "-"
        side = snap.side_of(kind)
        rows.append((name, reason, implemented_kind(snap, kind), short(side) if side is not None else "-", caps_text))
        snap.add(snap.node(f"tool_{name}"), kind, OBSERVED, name, enabled=enabled, reason=reason, providesCapability=caps)
    rows.sort(key=lambda r: (not r[1].startswith("enabled"), r[0]))
    return rows


def tool_summary(rows: list[tuple]) -> str:
    enabled = [r[0] for r in rows if r[1].startswith("enabled")]
    return f"{len(enabled)} of {len(rows)} enabled"
