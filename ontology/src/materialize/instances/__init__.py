"""Concrete instances behind the model's kinds, and effects resolved to them.

    model.py      Instance, Instances, Target
    registry.py   @environment sources and the Context they get (how to add one)
    envs/         one module per environment: nanoclaw, github, vercel
    resolve.py    effect on a kind -> targets (instances, new instances, hosts)

``discover`` runs every applicable source once per run and types the instances into
the snapshot graph; ``targets`` is used by the tables, the downstream
walk, and the joint-risk annotation.
"""

import json
from pathlib import Path

from ...common.namespaces import AR, OBS
from ..probe import ContainerProbe
from ..snapshot import Snapshot
from . import envs  # noqa: F401  (registers the environment sources)
from .model import Instances, Target
from .registry import run_sources
from .resolve import own_group_scope, row_effects, scope_of, targets, with_instances

__all__ = ["Target", "discover", "own_group_scope", "row_effects", "scope_of", "targets", "with_instances", "write_instances"]


def discover(snap: Snapshot, probe: ContainerProbe | None) -> None:
    inst = Instances()
    run_sources(snap, inst, probe)
    for i in inst.by_id.values():
        snap.add(snap.node(f"instance_{i.id}"), AR[i.kind], OBS.Discovered, i.label, instanceId=i.id,
                 partOf=snap.node(f"instance_{i.parent}") if i.parent else None)
    snap.instances = inst


def write_instances(inst: Instances, path: Path) -> None:
    path.write_text(json.dumps({"instances": [i.__dict__ for i in inst.by_id.values()], "notes": inst.notes,
                                "sources": inst.sources}, indent=1, ensure_ascii=False), encoding="utf-8")
