"""Settle the model's requirements for this session.

Each ar:Requirement becomes satisfied, unsatisfied, or unknown with its evidence
(observed key=value facts).
Requirements that depend on a concrete path or a per-request decision stay
unknown and are never assumed satisfied; so does any requirement without an
evaluator here.
"""

from rdflib import URIRef
from rdflib.namespace import RDF

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .constants import OBSERVED, SATISFIED, UNKNOWN, UNSATISFIED
from .gateway_policy import applicable_rules, cedar_like
from .provider_tools import tool_status, tools_implementing
from .runtime import container_command
from .snapshot import Snapshot


def evaluate(snap: Snapshot) -> dict[str, tuple[URIRef, str]]:
    """Requirement local name -> (status, evidence)."""
    f, config = snap.facts, snap.facts["config"]
    mounts = {m["Destination"]: bool(m["RW"]) for m in f["inspect"]["Mounts"]}
    provider = (config.get("provider") or f["session"].get("agent_provider") or "claude").lower()
    claude = provider == "claude"
    extras = [d for d in mounts if d.startswith("/workspace/extra/")]
    origin = bool(f["session"].get("messaging_group_id"))

    def mounted(path: str, what: str) -> tuple[URIRef, str]:
        return (SATISFIED if path in mounts else UNSATISFIED), f"mount {path}=" + ("present" if path in mounts else "absent")

    claude_state = claude and "/home/node/.claude" in mounts
    any_rw_extra = any(mounts[d] for d in extras)
    onecli_files = any("onecli" in d for d in mounts)
    return {
        "cond_default_claude_provider": (SATISFIED if claude_state else UNSATISFIED, f"provider={provider}; mount /home/node/.claude={'present' if '/home/node/.claude' in mounts else 'absent'}"),
        "cond_builtin_messaging_tool_available": (SATISFIED, "unconditional"),
        "cond_shared_skills_exist": mounted("/app/skills", "/app/skills"),
        "cond_container_config_exists": mounted("/workspace/agent/container.json", "container.json"),
        "cond_standing_instructions_composed": mounted("/workspace/agent/CLAUDE.md", "CLAUDE.md"),
        "cond_additional_mount_admitted": (SATISFIED if extras else UNSATISFIED, f"extra_mounts={len(extras)}"),
        "cond_additional_mount_readwrite": (SATISFIED if any_rw_extra else UNSATISFIED, f"rw_extra_mounts={sum(mounts[d] for d in extras)}"),
        "cond_onecli_files_contributed": (SATISFIED if onecli_files else UNSATISFIED, f"onecli_mounts={sum('onecli' in d for d in mounts)}"),
        "cond_network_client_available": (SATISFIED, f"curl={f['curl']}") if f["curl"] else (UNKNOWN, "curl=absent"),
        "cond_destination_projected_and_authorized": (SATISFIED if f["grants"] or origin else UNSATISFIED, f"destination_grants={len(f['grants'])}; origin_chat={'present' if origin else 'absent'}"),
        "cond_network_route_permitted": network_route_requirement(snap),
        "cond_container_file_readable": (UNKNOWN, "per-path"),
        "cond_container_file_writable": (UNKNOWN, "per-path"),
        "cond_ncl_available": (UNSATISFIED if snap.cli_scope == "disabled" else SATISFIED, f"cli_scope={snap.cli_scope}"),
        "cond_cli_scope_global": (SATISFIED if snap.cli_scope == "global" else UNSATISFIED, f"cli_scope={snap.cli_scope}"),
    }


def network_route_requirement(snap: Snapshot) -> tuple[URIRef, str]:
    """Unsatisfied when lockdown forces the gateway and no Cedar permit applies to the agent."""
    agent = snap.facts["onecli_agent"]
    permits = [p for p in applicable_rules(snap.facts["gateway_policy"], agent["id"] if agent else None) if p["scope"]["effect"] == "permit"]
    evidence = f"lockdown={'on' if snap.lockdown_on else 'off'}; cedar_permits={len(permits)}"
    if snap.lockdown_on and not permits:
        return UNSATISFIED, evidence
    return UNKNOWN, evidence + "; per-request"


def provider(snap: Snapshot) -> str:
    return (snap.facts["config"].get("provider") or snap.facts["session"].get("agent_provider") or "claude").lower()


def evaluate_tool_requirements(snap: Snapshot) -> dict[URIRef, tuple[URIRef, str]]:
    """Requirements decided by which provider tools are enabled (from the model)."""
    lists, out = snap.facts["claude_tools"], {}
    if provider(snap) != "claude":
        for cond in set(snap.model.subjects(AR.requiresProviderTool, None)) | set(snap.model.subjects(AR.satisfiedByEndpointKind, None)):
            out[cond] = (UNKNOWN, f"provider={provider(snap)}; modeled=claude")
        return out
    for cond, name in snap.model.subject_objects(AR.requiresProviderTool):
        enabled, reason = tool_status(lists, str(name))
        out[cond] = (SATISFIED if enabled else UNSATISFIED, f"{name}: {reason}")
    for cond, endpoint in snap.model.subject_objects(AR.satisfiedByEndpointKind):
        tools = tools_implementing(snap.model, endpoint)
        enabled = [name for _, name in tools if tool_status(lists, name)[0]]
        if enabled:
            out[cond] = (SATISFIED, "enabled=" + ", ".join(enabled))
        else:
            out[cond] = (UNSATISFIED, "enabled=none; implementers=" + (", ".join(n for _, n in tools) or "none"))
    return out


def evaluate_declared(snap: Snapshot) -> dict[URIRef, tuple[URIRef, str]]:
    """Requirements whose check the model declares: ``ar:requiresContainerCommand``
    (command -v in the session container), ``ar:requiresCredentialForHost``
    (a credential granted to this agent whose host pattern covers the host).
    A requirement with several checks holds only when all do."""
    m, out = snap.model, {}
    commands: dict[str, str] = {}
    declared = set(m.subjects(AR.requiresContainerCommand, None)) | set(m.subjects(AR.requiresCredentialForHost, None))
    for cond in sorted(declared):
        results = []
        for name in sorted(str(c) for c in m.objects(cond, AR.requiresContainerCommand)):
            if name not in commands:
                commands[name] = container_command(snap.facts["container_name"], name)
            results.append((bool(commands[name]), f"{name}={commands[name] or 'absent'}"))
        for host in sorted(str(h) for h in m.objects(cond, AR.requiresCredentialForHost)):
            hit = sorted(s["name"] for s in snap.facts["secrets"] if s.get("hostPattern") and cedar_like(host, s["hostPattern"].lower()))
            results.append((bool(hit), f"credential[{host}]={', '.join(hit) or 'none'}"))
        out[cond] = (SATISFIED if all(ok for ok, _ in results) else UNSATISFIED, "; ".join(e for _, e in results))
    return out


def resolve_conditions(snap: Snapshot) -> None:
    for name, result in evaluate(snap).items():
        snap.conditions[AR[name]] = result
    snap.conditions.update(evaluate_tool_requirements(snap))
    snap.conditions.update(evaluate_declared(snap))
    for cond in sorted(set(snap.model.subjects(RDF.type, AR.Requirement))):
        if cond not in snap.conditions:
            snap.conditions[cond] = (UNKNOWN, "evaluator=none")
    for cond, (status, reason) in snap.conditions.items():
        snap.add(snap.node(f"eval_{short(cond)}"), OBS.ConditionEvaluation, OBSERVED, short(cond), evaluates=cond, status=status, reason=reason)
