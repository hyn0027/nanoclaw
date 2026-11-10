"""Assemble selected graphs with provenance and shared-graph checks."""

import hashlib
import subprocess
from pathlib import Path

from rdflib import Graph, Literal
from rdflib.namespace import RDF

from ..common.namespaces import AR
from ..common.paths import ROOT, VOCABULARY
from ..common.rdf import required_literal
from .catalog import Catalog, CatalogEntry


def check_skill_source(ontology_id: str, entry: CatalogEntry) -> None:
    """A skill graph pins its SKILL.md (an MCP server graph its snapshot) by hash; a
    changed source means re-review. A relative source path is relative to the repository."""
    graph, node = entry["graph"], entry["node"]
    source = Path(required_literal(graph, node, AR.sourcePath))
    source = source if source.is_absolute() else ROOT / source
    expected_hash = required_literal(graph, node, AR.sourceSha256)
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        raise SystemExit(f"Ontology for {ontology_id} is stale: source skill changed")
    mapping = graph.value(node, AR.entityMappingPath)
    if mapping is not None:
        mapping_path = Path(str(mapping)) if Path(str(mapping)).is_absolute() else ROOT / str(mapping)
        if hashlib.sha256(mapping_path.read_bytes()).hexdigest() != str(graph.value(node, AR.entityMappingSha256)):
            raise SystemExit(f"Ontology for {ontology_id} is stale: entity mapping changed; rerun generate_mcp_ontology.py")


def check_module(ontology_id: str, entry: CatalogEntry) -> None:
    """Module source pins and the restrictions on always-loaded graphs."""
    graph, node = entry["graph"], entry["node"]
    repos = list(graph.objects(node, AR.sourceRepositoryPath))
    revisions = list(graph.objects(node, AR.sourceRevision))
    if len(repos) != len(revisions) or len(repos) > 1:
        raise SystemExit(f"Invalid source pin for {ontology_id}")
    if repos:
        actual = subprocess.check_output(["git", "-C", str(repos[0]), "rev-parse", "HEAD"], text=True).strip()
        if actual != str(revisions[0]):
            raise SystemExit(f"{ontology_id} is stale: source checkout changed")

    if (
        any(graph.subjects(RDF.type, AR.Operation))
        or any(graph.subjects(RDF.type, AR.PotentialEffect))
        or any(graph.triples((None, AR.declaresOperation, None)))
        or any(graph.triples((None, AR.hasPotentialEffect, None)))
    ):
        raise SystemExit(f"Always-loaded graph contains operations: {ontology_id}")
    if graph.value(node, AR.resourcesOnly) == Literal(True) and any(
        graph.subjects(RDF.type, AR.InvocationSurfaceKind)
    ):
        raise SystemExit(f"Resource-only graph contains invocation kinds: {ontology_id}")


def assemble(manifest: list[str], catalog: Catalog) -> Graph:
    """The vocabulary plus every selected graph, in manifest order."""
    combined = Graph().parse(VOCABULARY, format="turtle")
    for ontology_id in manifest:
        entry = catalog[ontology_id]
        if entry["skill"]:
            check_skill_source(ontology_id, entry)
        else:
            check_module(ontology_id, entry)
        combined += entry["graph"]
    return combined
