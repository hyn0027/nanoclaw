"""Per-tool view of the provider's built-in tools for this session."""

from rdflib import URIRef

from ..common.namespaces import AR
from .constants import OBSERVED
from .provider_tools import provider_tools, tool_status
from .snapshot import Snapshot


def tool_capabilities(snap: Snapshot, kind: URIRef) -> list[URIRef]:
    """Capabilities this tool provides: its own, plus generic ones on the endpoint it
    implements that no provider-specific refinement replaces."""
    m = snap.model
    own = list(m.subjects(AR.nativeEndpointKind, kind))
    if own:
        return own
    parents = set(m.transitive_objects(kind, AR.specializesInvocationKind)) - {kind}
    return [c for p in parents for c in m.subjects(AR.nativeEndpointKind, p) if c not in snap.refined_by]


def assess_tools(snap: Snapshot) -> None:
    """Record each provider tool as an instance of its tool kind with obs:enabled.
    Call after ``assess_capabilities`` (uses ``snap.refined_by``)."""
    lists = snap.facts["claude_tools"]
    claude = (snap.facts["config"].get("provider") or "claude").lower() == "claude"
    for kind, name in provider_tools(snap.model):
        enabled, source = tool_status(lists, name) if claude else (False, f"provider={snap.facts['config'].get('provider')}")
        reason = f"{'enabled' if enabled else 'disabled'} ({source})"
        snap.add(snap.node(f"tool_{name}"), kind, OBSERVED, name, enabled=enabled, reason=reason, providesCapability=tool_capabilities(snap, kind))
