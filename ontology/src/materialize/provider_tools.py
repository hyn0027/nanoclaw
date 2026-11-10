"""Which provider tools are enabled in this deployment.

NanoClaw's Claude provider exposes the tools in TOOL_ALLOWLIST and removes those
in SDK_DISALLOWED_TOOLS (container/agent-runner/src/providers/claude-config.ts).
Both lists are read from the checkout, so editing them changes the next
snapshot.
"""

import re
from pathlib import Path

from rdflib import Graph, URIRef

from ..common.namespaces import AR

CLAUDE_CONFIG = Path("container/agent-runner/src/providers/claude-config.ts")


def read_tool_lists(nanoclaw: Path) -> dict[str, list[str]]:
    """{"allowlist": [...], "disallowed": [...]} as declared in the checkout."""
    source = (nanoclaw / CLAUDE_CONFIG).read_text()

    def array(name: str) -> list[str]:
        match = re.search(rf"export const {name}\s*=\s*\[(.*?)\]", source, re.S)
        if not match:
            raise SystemExit(f"Could not find {name} in {CLAUDE_CONFIG}")
        return re.findall(r"'([^']+)'", match.group(1))

    return {"allowlist": array("TOOL_ALLOWLIST"), "disallowed": array("SDK_DISALLOWED_TOOLS")}


def tool_status(lists: dict[str, list[str]], name: str) -> tuple[bool, str]:
    """(enabled, reason) for one provider tool name."""
    if name in lists["disallowed"]:
        return False, "SDK_DISALLOWED_TOOLS"
    if name in lists["allowlist"]:
        return True, "TOOL_ALLOWLIST"
    return False, "absent from TOOL_ALLOWLIST"


def tool_name(model: Graph, kind: URIRef) -> str | None:
    value = model.value(kind, AR.providerToolName)
    return str(value) if value is not None else None


def provider_tools(model: Graph) -> list[tuple[URIRef, str]]:
    """(tool kind, provider tool name) for every modeled provider tool."""
    return sorted(((k, str(n)) for k, n in model.subject_objects(AR.providerToolName)), key=lambda kn: kn[1])


def tools_implementing(model: Graph, endpoint: URIRef) -> list[tuple[URIRef, str]]:
    """Provider tools whose kind specializes ``endpoint`` (directly or transitively)."""
    return [
        (kind, name) for kind, name in provider_tools(model)
        if endpoint in set(model.transitive_objects(kind, AR.specializesInvocationKind))
    ]
