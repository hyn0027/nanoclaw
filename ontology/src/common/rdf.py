"""Small RDF helpers for parsing graphs and rendering identifiers."""

from pathlib import Path

from rdflib import Dataset, Graph, Literal, URIRef
from rdflib.namespace import RDFS
from rdflib.term import Identifier

from .paths import ROOT


def parse_graph(path: Path) -> Graph:
    """Read the single populated named graph in a TriG file."""
    dataset = Dataset().parse(path, format="trig")
    graphs = [graph for graph in dataset.graphs() if len(graph)]
    if len(graphs) != 1 or not isinstance(graphs[0].identifier, URIRef):
        raise SystemExit(f"Expected one named graph in {path.relative_to(ROOT)}")
    return graphs[0]


def required_literal(graph: Graph, node: Identifier, predicate: URIRef) -> str:
    """Read one nonempty literal, used for required ontology metadata."""
    values = list(graph.objects(node, predicate))
    if len(values) != 1 or not isinstance(values[0], Literal) or not str(values[0]):
        raise SystemExit(f"Expected one literal {predicate} on {node}")
    return str(values[0])


def label(node: Identifier) -> str:
    """Shorten a local RDF identifier for the text report."""
    return str(node).rsplit(":", 1)[-1]


def short(node: URIRef) -> str:
    """Local name of an ``urn:agent-risk:`` identifier; other IRIs unchanged."""
    text = str(node)
    return text.rsplit(":", 1)[-1] if text.startswith("urn:agent-risk:") else text


def names(nodes: set[Identifier]) -> str:
    """Render a set of RDF identifiers in stable, compact form."""
    return ", ".join(sorted(map(label, nodes))) or "(none recorded)"


def notes(graph: Graph, node: Identifier, predicate: URIRef = RDFS.comment) -> str:
    """Render any recorded prose for a node without inventing a description."""
    return " ".join(sorted(map(str, set(graph.objects(node, predicate)))))


def ancestors(graph: Graph, kind: Identifier, predicate: URIRef) -> set[Identifier]:
    """Return all transitive parents of a kind under one relation."""
    return set(graph.transitive_objects(kind, predicate)) - {kind}
