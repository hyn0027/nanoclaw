"""Network routes and management paths for this session.

Status comes from observed facts (egress lockdown, cli_scope, mounts); controls
are the model's ``ar:protectedByKind`` of each kind.
"""

from ..common.namespaces import AR, OBS
from .constants import OBSERVED
from .modifications.common import controls
from .snapshot import Snapshot


def assess_routes(snap: Snapshot) -> list[tuple[str, str, str, str]]:
    lockdown, scope = snap.lockdown_on, snap.cli_scope
    extra = sum(m["Destination"].startswith("/workspace/extra/") for m in snap.facts["inspect"]["Mounts"])
    lock = f"lockdown={'on' if lockdown else 'off'}"
    cli = f"cli_scope={scope}"
    rows = [
        ("NanoClawProxyAwareHTTPRequest", "available", lock),
        ("NanoClawDirectNetworkRequest", "blocked" if lockdown else "available", lock),
        ("NanoClawHostReachableService", "unreachable" if lockdown else "reachable", lock),
        ("NanoClawContainerTasksCLI", "unavailable" if scope == "disabled" else "available", cli),
        ("NanoClawContainerGroupAdminCLI", "unavailable" if scope == "disabled" else "available", cli),
        ("NanoClawContainerGlobalAdminCLI", "available" if scope == "global" else "unavailable", cli),
        ("NanoClawCreateAgentTool", "available", cli),
        ("NanoClawAdditionalHostDirectory", "present" if extra else "absent", f"extra_mounts={extra}"),
    ]
    out = []
    for name, status, evidence in rows:
        gate = controls(snap, AR[name])
        snap.add(snap.node(f"route_{name}"), OBS.RouteAssessment, OBSERVED, name, assesses=AR[name], status=status, reason=evidence, controls=gate)
        out.append((name, status, evidence, gate))
    return out
