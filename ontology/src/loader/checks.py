"""Structural checks on the assembled graph, then SHACL validation.

Each relation's endpoints must be typed as the relation expects; specialization
must be acyclic; endpoint and public-Internet kinds must specialize their shared
parents; and every OWL-defined kind needs a SHACL target shape.
"""

from dataclasses import dataclass

from pyshacl import validate
from rdflib import Graph
from rdflib.namespace import OWL, RDF
from rdflib.term import Identifier

from ..common.namespaces import AR, SH
from ..common.paths import SHAPES
from ..common.rdf import ancestors

RESOURCE_RELATIONS = (
    AR.specializesKind,
    AR.containsKind,
    AR.mayBeStoredAsKind,
    AR.sharesKind,
    AR.mayResideInKind,
)


@dataclass(frozen=True)
class Kinds:
    world: set[Identifier]
    invocations: set[Identifier]


def kinds_of(combined: Graph) -> Kinds:
    return Kinds(
        world=set(combined.subjects(RDF.type, AR.WorldSurfaceKind)),
        invocations=set(combined.subjects(RDF.type, AR.InvocationSurfaceKind)),
    )


def check_relation_types(combined: Graph, kinds: Kinds) -> None:
    world, invocations = kinds.world, kinds.invocations
    for predicate in RESOURCE_RELATIONS:
        for subject, target in combined.subject_objects(predicate):
            if subject not in world or target not in world:
                raise SystemExit(f"Untyped resource relation: {subject} {predicate} {target}")
    for subject, target in combined.subject_objects(AR.specializesInvocationKind):
        if subject not in invocations or target not in invocations:
            raise SystemExit(f"Untyped invocation relation: {subject} -> {target}")
    for subject, target in combined.subject_objects(AR.routesToKind):
        if subject not in invocations or target not in world | invocations:
            raise SystemExit(f"Untyped route: {subject} -> {target}")
    for subject, control in combined.subject_objects(AR.protectedByKind):
        if (
            subject not in world | invocations
            or control not in world
            or (control, RDF.type, AR.SecurityControlKind) not in combined
        ):
            raise SystemExit(f"Untyped control: {subject} -> {control}")
    for skill, control in combined.subject_objects(AR.subjectToControlKind):
        if not ({(skill, RDF.type, AR.Skill), (skill, RDF.type, AR.MCPServer)} & set(combined.triples((skill, RDF.type, None)))) or (control, RDF.type, AR.SecurityControlKind) not in combined:
            raise SystemExit(f"Untyped skill control: {skill} -> {control}")


def check_mounts_and_sides(combined: Graph, kinds: Kinds) -> None:
    world, invocations = kinds.world, kinds.invocations
    for mount, resource in combined.subject_objects(AR.mountExposesKind):
        if (
            mount not in world
            or AR.NanoClawMount not in ancestors(combined, mount, AR.specializesKind)
            or resource not in world
        ):
            raise SystemExit(f"Untyped mount exposure: {mount} -> {resource}")
    components = set(combined.subjects(RDF.type, AR.SystemComponentKind))
    for subject, side in combined.subject_objects(AR.locatedOn):
        if subject not in world or side not in components:
            raise SystemExit(f"Untyped location side: {subject} -> {side}")
    for subject, side in combined.subject_objects(AR.executesOn):
        if subject not in invocations or side not in components:
            raise SystemExit(f"Untyped execution side: {subject} -> {side}")
    for mount, source in combined.subject_objects(AR.bindsFromKind):
        if (mount, AR.mountExposesKind, None) not in combined or source not in world:
            raise SystemExit(f"Untyped bind source: {mount} -> {source}")


def check_provider_tools(combined: Graph, kinds: Kinds) -> None:
    """Provider tool names, tool requirements, and capability refinements."""
    capabilities = set(combined.subjects(RDF.type, AR.NativeCapability))
    tool_names = {str(name) for kind, name in combined.subject_objects(AR.providerToolName) if kind in kinds.invocations}
    for kind, _ in combined.subject_objects(AR.providerToolName):
        if kind not in kinds.invocations:
            raise SystemExit(f"Provider tool name on a non-invocation kind: {kind}")
    for requirement, name in combined.subject_objects(AR.requiresProviderTool):
        if (requirement, RDF.type, AR.Requirement) not in combined or str(name) not in tool_names:
            raise SystemExit(f"Requirement names an unmodeled provider tool: {requirement} -> {name}")
    for requirement, endpoint in combined.subject_objects(AR.satisfiedByEndpointKind):
        if (requirement, RDF.type, AR.Requirement) not in combined or endpoint not in kinds.invocations:
            raise SystemExit(f"Untyped tool requirement: {requirement} -> {endpoint}")
    for refined, generic in combined.subject_objects(AR.refinesCapability):
        if refined not in capabilities or generic not in capabilities:
            raise SystemExit(f"Refinement between non-capabilities: {refined} -> {generic}")


def check_hierarchies(combined: Graph, kinds: Kinds) -> None:
    world, invocations = kinds.world, kinds.invocations
    for predicate in (AR.specializesKind, AR.specializesInvocationKind):
        if combined.query(f"ASK {{ ?kind <{predicate}>+ ?kind }}").askAnswer:
            raise SystemExit(f"Cycle in {predicate}")

    for kind in combined.subjects(RDF.type, AR.ToolEndpointKind):
        if kind not in invocations or (
            kind != AR.ToolEndpoint
            and AR.ToolEndpoint not in ancestors(combined, kind, AR.specializesInvocationKind)
        ):
            raise SystemExit(f"Tool endpoint lacks the shared parent: {kind}")

    public_endpoints = set(combined.subjects(RDF.type, AR.PublicInternetEndpointKind))
    for endpoint in public_endpoints:
        if endpoint not in world or (
            endpoint != AR.PublicInternetEndpoint
            and AR.PublicInternetEndpoint not in ancestors(combined, endpoint, AR.specializesKind)
        ):
            raise SystemExit(f"Public endpoint lacks the shared parent: {endpoint}")
    for call, endpoint in combined.subject_objects(AR.callEndpointKind):
        if endpoint not in public_endpoints:
            raise SystemExit(f"Internet call has unqualified endpoint: {call} -> {endpoint}")


def validate_shapes(combined: Graph, kinds: Kinds) -> None:
    shapes = Graph().parse(SHAPES, format="turtle")
    defined = set(combined.subjects(OWL.equivalentClass, None)) & (kinds.world | kinds.invocations)
    unshaped = defined - set(shapes.objects(None, SH.targetClass))
    if unshaped:
        raise SystemExit("Equivalent kinds without SHACL shapes: " + ", ".join(map(str, unshaped)))
    conforms, _, report = validate(combined, shacl_graph=shapes)
    if not conforms:
        raise SystemExit(f"SHACL validation failed:\n{report}")


def run_checks(combined: Graph) -> Kinds:
    """All structural checks, then SHACL. Returns the typed kind sets."""
    kinds = kinds_of(combined)
    check_relation_types(combined, kinds)
    check_mounts_and_sides(combined, kinds)
    check_provider_tools(combined, kinds)
    check_hierarchies(combined, kinds)
    validate_shapes(combined, kinds)
    return kinds
