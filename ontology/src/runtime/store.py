"""One session's ontology on disk.

    data/v2-sessions/<group>/<session>/ontology/
      state.json          ontology id, head expansion, base fingerprint, call cursor,
                          one entry per expansion and per base change
      pending.jsonl       captured calls routed to this session, not yet expanded
                          (removed once an expansion has read them; --keep-calls keeps
                          a copy under calls/)
      expansions/NNNN.trig  one named graph per expansion (never rewritten)
      ontology.trig       the current view: runtime vocabulary + every expansion, in
                          order (regenerated; the base model is loaded separately)

Only files in this folder change; the base graphs under ontology/ never do.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from rdflib import Dataset, Graph, Namespace, URIRef

from ..common.namespaces import AR
from .paths import RUNTIME_VOCABULARY, session_dir

RT = Namespace("urn:agent-risk:runtime:")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class SessionOntology:
    def __init__(self, group_id: str, session_id: str):
        self.group_id = group_id
        self.session_id = session_id
        self.dir = session_dir(group_id, session_id)
        self.state_path = self.dir / "state.json"
        self.pending_path = self.dir / "pending.jsonl"
        self.expansions_dir = self.dir / "expansions"
        self.state = self._load()

    @property
    def ontology_id(self) -> str:
        return f"ont-{self.session_id}"

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {
            "ontology_id": f"ont-{self.session_id}",
            "session_id": self.session_id,
            "agent_group_id": self.group_id,
            "created_at": now(),
            "head": None,          # last expansion id, None while only the base exists
            "base": None,          # fingerprint (base.py) the last run saw
            "last_call_id": None,  # newest captured call routed here
            "expansions": [],
            "base_changes": [],
        }

    @property
    def exists(self) -> bool:
        return self.state_path.exists()

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        write_atomic(self.state_path, json.dumps(self.state, indent=2) + "\n")

    # ── pending calls ────────────────────────────────────────────────
    def add_pending(self, records: list[dict]) -> None:
        if not records:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.pending_path, "a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        os.chmod(self.pending_path, 0o600)
        self.state["last_call_id"] = max(r["id"] for r in records)

    def pending(self) -> list[dict]:
        if not self.pending_path.exists():
            return []
        return [json.loads(line) for line in self.pending_path.read_text().splitlines() if line.strip()]

    def clear_pending(self, ids: set[str], keep: bool) -> None:
        rest, done = [], []
        for r in self.pending():
            (done if r["id"] in ids else rest).append(r)
        if keep and done:
            calls = self.dir / "calls"
            calls.mkdir(exist_ok=True)
            with open(calls / "calls.jsonl", "a") as f:
                for r in done:
                    f.write(json.dumps(r) + "\n")
        if rest:
            write_atomic(self.pending_path, "".join(json.dumps(r) + "\n" for r in rest))
        elif self.pending_path.exists():
            self.pending_path.unlink()

    # ── expansions ───────────────────────────────────────────────────
    def next_expansion_id(self) -> str:
        return f"{self.ontology_id}/{len(self.state['expansions']) + 1:04d}"

    def add_expansion(self, expansion_id: str, graph: Graph, entry: dict) -> Path:
        self.expansions_dir.mkdir(parents=True, exist_ok=True)
        number = expansion_id.rsplit("/", 1)[1]
        path = self.expansions_dir / f"{number}.trig"
        ds = Dataset()
        named = ds.graph(graph_uri(expansion_id))
        for prefix, ns in graph.namespaces():
            ds.bind(prefix, ns)
        for t in graph:
            named.add(t)
        path.write_text(ds.serialize(format="trig"))
        os.chmod(path, 0o600)
        self.state["expansions"].append({**entry, "id": expansion_id, "file": f"expansions/{path.name}"})
        self.state["head"] = expansion_id
        self.write_view()
        return path

    def expansion_graph(self) -> Graph:
        """Every expansion, merged, plus the runtime vocabulary."""
        merged = Graph()
        merged.parse(RUNTIME_VOCABULARY, format="turtle")
        for e in self.state["expansions"]:
            for g in Dataset().parse(self.dir / e["file"], format="trig").graphs():
                merged += g
        return merged

    def write_view(self) -> None:
        g = self.expansion_graph()
        g.bind("ar", AR)
        g.bind("rt", RT)
        write_atomic(self.dir / "ontology.trig", g.serialize(format="turtle"))



def graph_uri(expansion_id: str) -> URIRef:
    return URIRef(f"urn:nanoclaw:ontology:{expansion_id}")
