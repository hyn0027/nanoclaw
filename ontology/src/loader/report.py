"""Text report: one function per table, printed in a fixed order."""

from rdflib import Graph

from table_output import print_table

from ..common.namespaces import AR
from ..common.rdf import ancestors, label, names, notes
from .catalog import Catalog
from .inventory import Inventory


def print_summary(inv: Inventory) -> None:
    print_table(
        "Ontology report",
        ("Metric", "Value"),
        [
            ("Loaded skills", ", ".join(sorted(inv.skills)) or "(none)"),
            ("Operations", str(len(inv.operations))),
            ("Potential effect/resource rows", str(len(inv.effect_rows))),
            ("Native endpoint capabilities", str(len(inv.native))),
            ("Potential Internet calls", str(len(inv.calls))),
        ],
        (32, 88),
    )


def print_skill_controls(inv: Inventory, catalog: Catalog) -> None:
    if not inv.skills:
        return
    print_table(
        "Skill control context (candidate gates, not blanket grants)",
        ("Skill", "Relevant controls"),
        [
            (skill, names(set(catalog[skill]["graph"].objects(catalog[skill]["node"], AR.subjectToControlKind))))
            for skill in sorted(inv.skills)
        ],
        (28, 92),
    )


def print_operations(inv: Inventory) -> None:
    if not inv.operation_rows:
        return
    rows = []
    for skill, operation, graph in inv.operation_rows:
        key = f"{skill} / {label(operation)}"
        rows.extend(
            [
                (key, "Via", names(set(graph.objects(operation, AR.invokedThroughKind)))),
                (key, "Targets", names(set(graph.objects(operation, AR.targetsKind)))),
                (key, "Requires", names(set(graph.objects(operation, AR.requires)))),
                (key, "Evidence", names(set(graph.objects(operation, AR.sourceStatus)))),
                (key, "Adaptation", names(set(graph.objects(operation, AR.adaptationStatus)))),
            ]
        )
        if note := notes(graph, operation, AR.sourceNote):
            rows.append((key, "Note", note))
        for effect_type, resource in sorted(
            {
                (effect_type, resource)
                for effect in graph.objects(operation, AR.hasPotentialEffect)
                for effect_type in graph.objects(effect, AR.effectType)
                for resource in graph.objects(effect, AR.affectsKind)
            },
            key=lambda row: (str(row[0]), str(row[1])),
        ):
            rows.append((key, "Effect", f"{label(effect_type)} → {label(resource)}"))
    print_table(
        "Skill operations (possible effects, not observed actions)",
        ("Skill / operation", "Field", "Details"),
        rows,
        (38, 14, 70),
    )


def print_native_capabilities(inv: Inventory, combined: Graph) -> None:
    if not inv.native:
        return
    rows = []
    for capability in inv.native:
        key = label(capability)
        endpoint = combined.value(capability, AR.nativeEndpointKind)
        resource = combined.value(capability, AR.nativeResourceKind)
        effect_types_text = names(set(combined.objects(capability, AR.nativeEffectType)))
        rows.extend(
            [
                (key, "Endpoint", label(endpoint)),
                (key, "Effects", f"{effect_types_text} → {label(resource)}"),
                (key, "Scope", names(set(combined.objects(capability, AR.nativeScopeKind)))),
                (key, "Requires", names(set(combined.objects(capability, AR.nativeRequires)))),
                (key, "Note", notes(combined, capability, AR.sourceNote)),
            ]
        )
        if refined := set(combined.objects(capability, AR.refinesCapability)):
            rows.append((key, "Refines", names(refined)))
    print_table(
        "Native endpoint capabilities (independent of skills; conditional)",
        ("Capability", "Field", "Details"),
        rows,
        (36, 14, 72),
    )


def print_mounts(combined: Graph) -> None:
    mounts = sorted(set(combined.subjects(AR.mountExposesKind, None)), key=str)
    if not mounts:
        return
    rows = []
    for mount in mounts:
        key = label(mount)
        rows.extend(
            [
                (key, "Mode", names(set(combined.objects(mount, AR.mountAccessMode)))),
                (key, "Container path", names(set(combined.objects(mount, AR.containerPath)))),
                (key, "Exposes", names(set(combined.objects(mount, AR.mountExposesKind)))),
            ]
        )
        for source in sorted(combined.objects(mount, AR.bindsFromKind), key=str):
            pattern = names(set(combined.objects(source, AR.hostPathPattern)))
            rows.append((key, "Host source", f"{label(source)} ({pattern})"))
        conditions = set(combined.objects(mount, AR.mountConditionalOn))
        if conditions:
            rows.append((key, "Requires", names(conditions)))
    print_table(
        "Container mount exposures and host sources (alternatives; not effective file permissions)",
        ("Mount", "Field", "Details"),
        rows,
        (36, 14, 72),
    )


def print_affected_resources(inv: Inventory, combined: Graph) -> None:
    affected = set(inv.touches) | set(inv.native_touches)
    if not affected:
        return
    rows = []
    for resource in sorted(affected, key=str):
        key = label(resource)
        start = len(rows)
        if definition := notes(combined, resource):
            rows.append((key, "Definition", definition))
        parents = ancestors(combined, resource, AR.specializesKind)
        if parents:
            rows.append((key, "Subtypes of", names(parents)))
        containers = set(combined.subjects(AR.containsKind, resource))
        if containers:
            rows.append((key, "Contained in", names(containers)))
        locations = set(combined.transitive_objects(resource, AR.mayResideInKind)) - {resource}
        if locations:
            rows.append((key, "May reside in", names(locations)))
        mounts_for_resource = {
            mount for location in locations | {resource} for mount in combined.subjects(AR.mountExposesKind, location)
        }
        if mounts_for_resource:
            rows.append((key, "Possible mounts", names(mounts_for_resource)))
        controls = set(combined.objects(resource, AR.protectedByKind))
        for parent in parents:
            controls.update(combined.objects(parent, AR.protectedByKind))
        if controls:
            rows.append((key, "Controls", names(controls)))
        if len(rows) == start:
            rows.append((key, "Context", "(no further description recorded)"))
    print_table(
        "Affected resource kinds (type-level context)",
        ("Resource kind", "Field", "Details"),
        rows,
        (36, 18, 68),
    )


def print_internet_calls(inv: Inventory) -> None:
    if not inv.calls:
        return
    rows = []
    for skill, operation, call, graph in inv.calls:
        key = f"{skill} / {label(operation)}"
        vias = names(set(graph.objects(call, AR.callViaKind)))
        conditions = set(graph.objects(operation, AR.requires)) | set(graph.objects(call, AR.callConditionalOn))
        stage = label(graph.value(call, AR.callStage))
        initiator = label(graph.value(call, AR.callInitiatorKind))
        endpoint = label(graph.value(call, AR.callEndpointKind))
        pattern = graph.value(call, AR.callEndpointPattern) or "(not established)"
        rows.extend(
            [
                (key, "Call", label(call)),
                (key, "Stage / initiator", f"{stage} / {initiator}"),
                (key, "Via", vias),
                (key, "Endpoint", endpoint),
                (key, "Pattern", str(pattern)),
                (key, "Conditions", names(conditions)),
            ]
        )
    print_table(
        "Potential Internet calls (possibilities, not observed traffic)",
        ("Skill / operation", "Field", "Details"),
        rows,
        (38, 18, 68),
    )


def print_report(inv: Inventory, catalog: Catalog, combined: Graph) -> None:
    print_summary(inv)
    print_skill_controls(inv, catalog)
    print_operations(inv)
    print_native_capabilities(inv, combined)
    print_mounts(combined)
    print_affected_resources(inv, combined)
    print_internet_calls(inv)
