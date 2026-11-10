"""Check the snapshot's instance types and write it as one named graph."""

from pathlib import Path

from rdflib import Dataset
from rdflib.namespace import RDF

from ..common.namespaces import AR, OBS
from .constants import ASSESSMENT_CLASSES
from .snapshot import Snapshot


def check_instance_types(snap: Snapshot) -> None:
    """Every instance must be typed with a kind the model declares (or an assessment class)."""
    known = set(snap.model.subjects(RDF.type, None)) | ASSESSMENT_CLASSES
    unknown = sorted({o for o in snap.graph.objects(None, RDF.type) if o not in known})
    if unknown:
        raise SystemExit("Snapshot uses undeclared kinds: " + ", ".join(map(str, unknown)))


def write_snapshot(snap: Snapshot, out_dir: Path) -> Path:
    """Write ``<session>-<timestamp>.trig`` containing the snapshot's named graph."""
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{snap.facts['session']['id']}-{snap.taken_at.replace(':', '')}.trig"
    ds = Dataset()
    for prefix, ns in (("ar", AR), ("obs", OBS), ("inst", snap.inst)):
        ds.bind(prefix, ns)
    named = ds.graph(snap.graph.identifier)
    for triple in snap.graph:
        named.add(triple)
    ds.serialize(path, format="trig")
    return path
