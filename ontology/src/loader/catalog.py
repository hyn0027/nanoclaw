"""Manifest reading, graph discovery, and static dependency preflight."""

import json
import sys
from typing import TypedDict

from rdflib import Graph
from rdflib.namespace import RDF
from rdflib.term import Identifier

from ..common.namespaces import AR
from ..common.paths import MANIFEST, MCP_DIR, MODEL_DIRS, ROOT, SKILLS_DIR
from ..common.rdf import parse_graph, required_literal


class CatalogEntry(TypedDict):
    graph: Graph
    node: Identifier
    skill: bool


Catalog = dict[str, CatalogEntry]


def read_manifest() -> list[str]:
    """The manifest is one JSON list of unique ontology IDs."""
    manifest = json.loads(MANIFEST.read_text())
    if (
        not isinstance(manifest, list)
        or not all(isinstance(item, str) and item for item in manifest)
        or len(manifest) != len(set(manifest))
    ):
        raise SystemExit("manifest.json must be a list of unique ontology IDs")
    return manifest


def discover_catalog() -> Catalog:
    """Discover every graph once, keyed by its module ID, skill directory, or MCP server ID.

    A support graph under skills/ is still a module when its RDF metadata says
    ar:OntologyModule. MCP server graphs (ontology/mcp/) declare operations like
    skills and are catalogued as such.
    """
    catalog: Catalog = {}
    paths = [p for d in MODEL_DIRS for p in sorted(d.glob("*.trig"))] + sorted(SKILLS_DIR.glob("*.trig")) + sorted(MCP_DIR.glob("*.trig"))
    for path in paths:
        graph = parse_graph(path)
        nodes = [(node, False) for node in graph.subjects(RDF.type, AR.OntologyModule)]
        nodes += [(node, True) for node in graph.subjects(RDF.type, AR.Skill)]
        nodes += [(node, True) for node in graph.subjects(RDF.type, AR.MCPServer)]
        if len(nodes) != 1:
            raise SystemExit(f"Expected one module or skill in {path.relative_to(ROOT)}")
        node, is_skill = nodes[0]
        key = (AR.mcpServerId if (node, RDF.type, AR.MCPServer) in graph else AR.skillDirectory) if is_skill else AR.ontologyModuleId
        ontology_id = required_literal(graph, node, key)
        if ontology_id in catalog:
            raise SystemExit(f"Duplicate ontology ID: {ontology_id}")
        catalog[ontology_id] = {"graph": graph, "node": node, "skill": is_skill}
    return catalog


def check_selection(manifest: list[str], catalog: Catalog) -> None:
    """Reject unknown IDs and unselected dependencies before anything is loaded.

    Dependencies never trigger automatic loading.
    """
    unknown = set(manifest) - catalog.keys()
    if unknown:
        raise SystemExit("Unknown ontologies in manifest.json: " + ", ".join(sorted(unknown)))

    selected = set(manifest)
    missing = [
        (ontology_id, str(dependency))
        for ontology_id in manifest
        for dependency in catalog[ontology_id]["graph"].objects(
            catalog[ontology_id]["node"], AR.requiresOntologyModule
        )
        if str(dependency) not in selected or catalog[str(dependency)]["skill"]
    ]
    if missing:
        for ontology_id, dependency in missing:
            print(f"WARNING: {ontology_id} requires unselected module {dependency}", file=sys.stderr)
        raise SystemExit("Dependency preflight failed; no ontology graphs were loaded")
