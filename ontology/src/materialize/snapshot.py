"""The snapshot graph and the state shared by every materializer stage."""

import re

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from ..common.namespaces import AR, OBS
from . import model as model_queries


class Snapshot:
    """One session's instance graph plus the facts and model it was built from.

    Stages add instances through ``add`` and record results on ``conditions``,
    ``cap_status``, and ``findings``.
    """

    def __init__(self, facts: dict, model: Graph, taken_at: str) -> None:
        self.facts, self.model = facts, model
        sid = facts["session"]["id"]
        stamp = taken_at.replace(":", "").replace("-", "").replace(".", "")
        self.inst = Namespace(f"urn:agent-risk:instance:{sid}:{stamp}:")
        self.graph = Graph(identifier=URIRef(f"urn:agent-risk:snapshot:{sid}:{stamp}"))
        self.graph.bind("ar", AR)
        self.graph.bind("obs", OBS)
        self.graph.bind("inst", self.inst)
        self.taken_at = taken_at
        self.conditions: dict[URIRef, tuple[URIRef, str]] = {}
        self.cap_status: dict[URIRef, URIRef] = {}
        # generic capability -> provider-specific refinements loaded in the model
        self.refined_by: dict[URIRef, list[URIRef]] = {}
        self.findings: list[str] = []
        # unmerged remote rows (set by assess_modifications), input to the downstream table
        self.raw_remote: list[tuple] = []
        # concrete instances behind the kinds (set by instances.discover)
        self.instances = None
        # (kind, effect) -> instances.Target list, per downstream effect (set by assess_downstream)
        self.downstream_targets: dict = {}

    # ── derived facts ──

    @property
    def lockdown_on(self) -> bool:
        """Egress lockdown is observed as the container sitting on an internal network."""
        return any(n["internal"] for n in self.facts["networks"].values())

    @property
    def cli_scope(self) -> str:
        return self.facts["config"].get("cli_scope") or "group"

    @property
    def mounts(self) -> list[dict]:
        return sorted(self.facts["inspect"]["Mounts"], key=lambda m: m["Destination"])

    def host_path(self, path: str | None) -> str | None:
        """Host path relative to the NanoClaw checkout when inside it."""
        if not path:
            return None
        root = str(self.facts["nanoclaw_root"]).rstrip("/") + "/"
        return path[len(root):] if path.startswith(root) else path

    def side_of(self, kind: URIRef) -> URIRef | None:
        return model_queries.side_of(self.model, kind)

    # ── graph building ──

    def node(self, local: str) -> URIRef:
        return self.inst[re.sub(r"[^A-Za-z0-9_.-]", "_", local)]

    def add(self, node: URIRef, kind: URIRef, evidence: URIRef, label: str, **props: object) -> URIRef:
        """Type ``node`` as ``kind`` with a label, evidence kind, and ``obs:`` properties.

        List values add one triple per item; None values are skipped.
        """
        g = self.graph
        g.add((node, RDF.type, kind))
        g.add((node, RDFS.label, Literal(label)))
        g.add((node, OBS.evidenceKind, evidence))
        for key, value in props.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is None:
                    continue
                obj = item if isinstance(item, (URIRef, BNode, Literal)) else Literal(item)
                g.add((node, OBS[key], obj))
        return node
