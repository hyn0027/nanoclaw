"""What this session can read or change, split by where it happens.

``inside``  in the container, reads and changes (with the host path a bind mount
            reads or writes through to)
``host``    on the host, through host steps the agent can trigger
``remote``  beyond the local machine
"""

from ...common.namespaces import AR, OBS
from ..constants import OBSERVED
from ..snapshot import Snapshot
from .common import merge_rows
from .host import host_rows
from .inside import inside_rows
from .remote import remote_rows


def record(snap: Snapshot, inside: list[tuple], outside: list[tuple], remote: list[tuple]) -> None:
    """Store every row as an obs:ModificationAssessment on its side."""
    for side, prefix, rows in ((AR.NanoClawAgentContainer, "in", inside), (AR.NanoClawHostProcess, "out", outside), (AR.ExternalNetwork, "remote", remote)):
        for i, (res, loc, effects, via, status, gate) in enumerate(rows):
            snap.add(snap.node(f"modification_{prefix}_{i}"), OBS.ModificationAssessment, OBSERVED, f"{res.strip(' └')} @ {loc.splitlines()[0]}",
                     resource=res.strip(" └"), onSide=side, location=loc, effects=effects, via=via, status=status, gate=gate)


def assess_modifications(snap: Snapshot, operations: tuple[list[tuple], list[tuple], list[tuple]] = ([], [], [])) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """(inside, outside, remote) rows: native capabilities first, then the skill
    operations' effects (``operations``, same layouts); call after ``assess_capabilities``.

    Rows for the same resource at the same location are merged, native and skill
    rows separately, so a skill effect always stays attributable to its operation.
    """
    op_inside, op_outside, op_remote = operations
    outside, remote_via_host = host_rows(snap)
    inside = merge_rows(inside_rows(snap), 2, effects=2, via=3, gate=5) + merge_rows(op_inside, 2, effects=2, via=3, gate=5)
    outside = merge_rows(outside, 2, effects=2, via=3, gate=5) + merge_rows(op_outside, 2, effects=2, via=3, gate=5)
    native_remote = remote_rows(snap, remote_via_host)
    snap.raw_remote = native_remote + op_remote  # unmerged, for the downstream table
    remote = merge_rows(native_remote, 2, effects=2, via=3, gate=5) + merge_rows(op_remote, 2, effects=2, via=3, gate=5)
    record(snap, inside, outside, remote)
    return inside, outside, remote
