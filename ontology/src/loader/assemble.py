"""Assemble selected graphs with provenance and shared-graph checks."""

import hashlib
from pathlib import Path

from rdflib import Graph, Literal
from rdflib.namespace import RDF

from ..common.namespaces import AR
from ..common.paths import ROOT, VOCABULARY
from ..common.rdf import required_literal
from ..common.source import source_hash
from .catalog import Catalog, CatalogEntry


def repo_path(value: str) -> Path:
    """A source path as recorded in a graph: absolute, or relative to the repository."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_skill_source(ontology_id: str, entry: CatalogEntry) -> None:
    """A skill graph pins its SKILL.md (an MCP server graph its snapshot and entity
    mapping) by hash; a changed source means re-review."""
    graph, node = entry["graph"], entry["node"]
    if sha256(repo_path(required_literal(graph, node, AR.sourcePath))) != required_literal(graph, node, AR.sourceSha256):
        raise SystemExit(f"Ontology for {ontology_id} is stale: source skill changed")
    mapping = graph.value(node, AR.entityMappingPath)
    if mapping is not None and sha256(repo_path(str(mapping))) != str(graph.value(node, AR.entityMappingSha256)):
        raise SystemExit(f"Ontology for {ontology_id} is stale: entity mapping changed; rerun generate_mcp_ontology.py")


def check_module(ontology_id: str, entry: CatalogEntry) -> None:
    """Module source pins and the restrictions on always-loaded graphs."""
    graph, node = entry["graph"], entry["node"]
    repos = list(graph.objects(node, AR.sourceRepositoryPath))
    hashes = list(graph.objects(node, AR.sourceContentHash))
    if len(repos) != len(hashes) or len(repos) > 1:
        raise SystemExit(f"Invalid source pin for {ontology_id}")
    if repos:
        actual = source_hash(Path(str(repos[0])))
        if actual != str(hashes[0]):
            raise SystemExit(f"{ontology_id} is stale: {repos[0]} changed outside ontology/ since the model was reviewed. "
                             f"Review the model against the change, then set ar:sourceContentHash \"{actual}\".")

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
