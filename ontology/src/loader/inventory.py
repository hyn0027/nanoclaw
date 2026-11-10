"""Gather what the selected skills declare and what native endpoints afford."""

from collections import defaultdict
from dataclasses import dataclass, field

from rdflib import Graph
from rdflib.namespace import RDF
from rdflib.term import Identifier

from ..common.namespaces import AR
from ..common.rdf import label
from .catalog import Catalog


@dataclass
class Inventory:
    skills: list[str]
    operations: set[Identifier] = field(default_factory=set)
    # (skill, operation, skill graph)
    operation_rows: list[tuple[str, Identifier, Graph]] = field(default_factory=list)
    # (skill, operation label, effect label, resource label)
    effect_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (skill, operation, call, skill graph)
    calls: list[tuple[str, Identifier, Identifier, Graph]] = field(default_factory=list)
    # resource kind -> effect types, from skill operations
    touches: dict[Identifier, set[Identifier]] = field(default_factory=lambda: defaultdict(set))
    native: list[Identifier] = field(default_factory=list)
    # resource kind -> effect types, from native capabilities
    native_touches: dict[Identifier, set[Identifier]] = field(default_factory=lambda: defaultdict(set))


def route_reaches(graph: Graph, invocations: set[Identifier], source: Identifier, target: Identifier) -> bool:
    """Check a possible mediation route, not actual runtime reachability."""
    seen = set()
    pending = [source]
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in seen and current in invocations:
            seen.add(current)
            pending.extend(graph.objects(current, AR.routesToKind))
    return False


def collect_skill_operations(inv: Inventory, catalog: Catalog, combined: Graph, invocations: set[Identifier]) -> None:
    effects = set()
    for skill in inv.skills:
        graph, node = catalog[skill]["graph"], catalog[skill]["node"]
        for operation in graph.objects(node, AR.declaresOperation):
            inv.operations.add(operation)
            inv.operation_rows.append((skill, operation, graph))
            for effect in graph.objects(operation, AR.hasPotentialEffect):
                for effect_type in graph.objects(effect, AR.effectType):
                    for resource in graph.objects(effect, AR.affectsKind):
                        inv.touches[resource].add(effect_type)
                        effects.add((skill, label(operation), label(effect_type), label(resource)))
            for call in graph.objects(operation, AR.mayInitiateInternetCall):
                # A call may be declared by a support module the skill requires (shared by several skills).
                if (call, RDF.type, AR.PotentialInternetCall) not in combined:
                    raise SystemExit(f"Undeclared Internet call: {call}")
                invoked = set(graph.objects(operation, AR.invokedThroughKind))
                vias = set(combined.objects(call, AR.callViaKind))
                if not any(route_reaches(combined, invocations, source, via) for source in invoked for via in vias):
                    raise SystemExit(f"No operation-to-call route: {operation} -> {call}")
                inv.calls.append((skill, operation, call, combined))
    inv.effect_rows = sorted(effects)
    inv.operation_rows.sort(key=lambda row: (row[0], str(row[1])))
    inv.calls.sort(key=lambda row: (row[0], str(row[1]), str(row[2])))


def collect_native_capabilities(inv: Inventory, combined: Graph) -> None:
    inv.native = sorted(set(combined.subjects(RDF.type, AR.NativeCapability)), key=str)
    for capability in inv.native:
        for resource in combined.objects(capability, AR.nativeResourceKind):
            inv.native_touches[resource].update(combined.objects(capability, AR.nativeEffectType))


def build_inventory(manifest: list[str], catalog: Catalog, combined: Graph, invocations: set[Identifier]) -> Inventory:
    inv = Inventory(skills=[ontology_id for ontology_id in manifest if catalog[ontology_id]["skill"]])
    collect_skill_operations(inv, catalog, combined, invocations)
    collect_native_capabilities(inv, combined)
    return inv
