"""Resource descriptions handed to the LLM pair annotator.

Only kinds, definitions, hierarchy context, and effect types are passed; skill,
operation, and capability provenance stays local.
"""

from collections import defaultdict

from rdflib import BNode, Graph, Literal
from rdflib.compare import to_canonical_graph
from rdflib.namespace import OWL, RDF, RDFS, SKOS
from rdflib.term import Identifier

from ..common.namespaces import AR
from ..common.rdf import ancestors, label
from .inventory import Inventory


def describe_kind(graph: Graph, kind: Identifier) -> dict:
    """Extract a kind's description and OWL definition, excluding provenance."""
    definition = Graph()
    pending = [kind]
    seen = set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        for predicate, value in graph.predicate_objects(node):
            if predicate in (RDFS.label, RDFS.comment, SKOS.definition, OWL.equivalentClass) or (
                isinstance(node, BNode) and str(predicate).startswith((str(OWL), str(RDF)))
            ):
                definition.add((node, predicate, value))
                if not isinstance(value, Literal):
                    pending.append(value)
    lines = [f"{subject.n3()} {predicate.n3()} {value.n3()} ." for subject, predicate, value in to_canonical_graph(definition)]
    return {
        "kind": str(kind),
        "label": str(graph.value(kind, RDFS.label) or label(kind)),
        "definition": "\n".join(sorted(lines)) or None,
    }


def resource_context(graph: Graph, kind: Identifier, effects: set[Identifier]) -> dict:
    """Describe a touched resource, its subtype/containment context, and effects."""
    containers = set(graph.transitive_subjects(AR.containsKind, kind)) - {kind}
    return {
        **describe_kind(graph, kind),
        "parents": [describe_kind(graph, parent) for parent in sorted(ancestors(graph, kind, AR.specializesKind), key=str)],
        "contained_in": [describe_kind(graph, parent) for parent in sorted(containers, key=str)],
        "effects": sorted(map(label, effects)),
    }


def annotation_resources(inv: Inventory, combined: Graph) -> list[dict]:
    """Skill-declared and native effects, consolidated per resource kind."""
    analysis_touches = defaultdict(set)
    for source in (inv.touches, inv.native_touches):
        for kind, effect_types in source.items():
            analysis_touches[kind].update(effect_types)
    return [resource_context(combined, kind, effect_types) for kind, effect_types in analysis_touches.items()]
