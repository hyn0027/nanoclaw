"""Turn collected facts into typed instances of the model's kinds."""

from rdflib import Literal, URIRef
from rdflib.namespace import XSD

from ..common.namespaces import AR, OBS
from ..common.rdf import short
from .constants import FROM_CONFIG, OBSERVED
from .gateway_policy import applicable_rules, effective_mode, rule_label
from .model import mount_kind
from .snapshot import Snapshot


def add_identity(snap: Snapshot) -> tuple[URIRef, URIRef]:
    """Snapshot metadata, agent group, session, and the group's other sessions."""
    f, g = snap.facts, snap.graph
    sess, group, config = f["session"], f["group"], f["config"]
    meta = snap.node("snapshot")
    snap.add(meta, OBS.Snapshot, OBSERVED, f"Snapshot of {sess['id']}", takenAt=Literal(snap.taken_at, datatype=XSD.dateTime), nanoclawRevision=f["nanoclaw_head"])
    grp = snap.add(snap.node(f"group_{group['id']}"), AR.NanoClawAgentGroup, FROM_CONFIG, group["name"], identifier=group["id"], folder=group.get("folder"), cliScope=config.get("cli_scope") or "group")
    session = snap.add(snap.node(f"session_{sess['id']}"), AR.NanoClawSession, FROM_CONFIG, sess["id"], identifier=sess["id"], inAgentGroup=grp, containerStatus=sess["container_status"])
    g.add((meta, OBS.ofSession, session))
    for other in f["group_sessions"]:
        if other["id"] != sess["id"]:
            peer = snap.add(snap.node(f"session_{other['id']}"), AR.NanoClawSession, FROM_CONFIG, other["id"], identifier=other["id"], inAgentGroup=grp, containerStatus=other["container_status"])
            g.add((session, OBS.sharesGroupStateWith, peer))
    return grp, session


def add_messaging(snap: Snapshot, grp: URIRef, session: URIRef) -> dict | None:
    """Origin chat, wirings, destination grants, and projected destinations. Returns the origin chat."""
    f, g = snap.facts, snap.graph
    sess, group = f["session"], f["group"]
    mgs = {m["id"]: m for m in f["messaging_groups"]}
    origin = mgs.get(sess.get("messaging_group_id"))
    if origin:
        mg = snap.add(snap.node(f"mg_{origin['id']}"), AR.MessagingGroup, FROM_CONFIG, origin["name"] or origin["id"], identifier=origin["id"], channelType=origin["channel_type"], unknownSenderPolicy=origin["unknown_sender_policy"])
        g.add((session, OBS.originChat, mg))
    for w in f["wirings"]:
        target = mgs.get(w["messaging_group_id"], {})
        snap.add(snap.node(f"wiring_{w['id']}"), AR.NanoClawWiring, FROM_CONFIG, f"{target.get('name')} → {group['name']}", identifier=w["id"], wiresAgentGroup=grp, wiresMessagingGroup=snap.node(f"mg_{w['messaging_group_id']}"), engageMode=w["engage_mode"], senderScope=w["sender_scope"])

    for d in f["grants"]:
        grant = snap.add(snap.node(f"grant_{d['local_name']}"), AR.NanoClawDestinationGrant, FROM_CONFIG, d["local_name"], localName=d["local_name"], targetType=d["target_type"], targetId=d["target_id"], channelType=d.get("channel_type"), heldBy=grp)
        g.add((session, OBS.mayAddress, grant))
    for d in f["projected"]:
        snap.add(snap.node(f"projected_{d['name']}"), AR.NanoClawProjectedDestination, OBSERVED, d["name"], localName=d["name"], inSession=session, projectionOf=snap.node(f"grant_{d['name']}"))
    granted = {d["local_name"] for d in f["grants"]}
    for d in f["projected"]:
        if d["name"] not in granted:
            snap.findings.append(f"destination {d['name']}: projected=yes; central_grant=absent")
    return origin


def add_container(snap: Snapshot, session: URIRef) -> URIRef:
    f = snap.facts
    inspect, host = f["inspect"], f["inspect"]["HostConfig"]
    container = snap.add(snap.node(f"container_{f['container_name']}"), AR.NanoClawAgentContainer, OBSERVED, f["container_name"], servesSession=session, image=inspect["Config"]["Image"], runsAsUser=inspect["Config"].get("User") or None, readOnlyRootFs=bool(host.get("ReadonlyRootfs")))
    snap.add(snap.node("image"), AR.NanoClawAgentImage, OBSERVED, inspect["Config"]["Image"], usedBy=container, perGroupImage=bool(f["config"].get("image_tag")))
    snap.graph.add((container, OBS.onSide, AR.NanoClawAgentContainer))
    return container


def add_mount(snap: Snapshot, m: dict, container: URIRef) -> None:
    """One mount: a container-side location bound from a separate host-side location."""
    g = snap.graph
    dest, rw, source = m["Destination"], bool(m["RW"]), m.get("Source")
    kind = mount_kind(snap.model, dest, rw)
    if kind is None:
        kind = AR.NanoClawMount
        snap.findings.append(f"mount {dest}: modeled_kind=none")
    mode = AR.ReadWriteMountMode if rw else AR.ReadOnlyMountMode
    mount = snap.add(snap.node(f"mount{dest}"), kind, OBSERVED, dest, containerPath=dest, hostPath=snap.host_path(source), accessMode=mode, mountedIn=container)
    modeled = set(snap.model.objects(kind, AR.mountAccessMode))
    if modeled and mode not in modeled:
        snap.findings.append(f"mount {dest}: observed_mode={short(mode)}; {short(kind)}.mountAccessMode={', '.join(short(x) for x in modeled)}")
    exposed_kinds = list(snap.model.objects(kind, AR.mountExposesKind))
    if kind == AR.OneCLIFileMount:
        exposed_kinds = [AR.OneCLICACertificate if "-ca" in dest or dest.endswith(".pem") else AR.OneCLICredentialStub]
    for exposed in exposed_kinds:
        location = snap.add(snap.node(f"location{dest}_{short(exposed)}"), exposed, OBSERVED, f"{dest} ({short(exposed)})", containerPath=dest, onSide=AR.NanoClawAgentContainer)
        g.add((mount, OBS.exposes, location))
    for host_kind in snap.model.objects(kind, AR.bindsFromKind):
        backing = snap.add(snap.node(f"host{source}"), host_kind, OBSERVED, snap.host_path(source), hostPath=snap.host_path(source), onSide=AR.NanoClawHostProcess)
        g.add((mount, OBS.bindsFrom, backing))
        for location in g.objects(mount, OBS.exposes):
            g.add((location, OBS.backedBy, backing))


def add_mailboxes(snap: Snapshot, session: URIRef) -> None:
    """Session mailbox files, which live in the session workspace mount."""
    mounts = snap.facts["inspect"]["Mounts"]
    if "/workspace" not in {m["Destination"] for m in mounts}:
        return
    ws = next(m for m in mounts if m["Destination"] == "/workspace")
    for kind, name in ((AR.NanoClawInboundMailbox, "inbound.db"), (AR.NanoClawOutboundMailbox, "outbound.db")):
        snap.add(snap.node(f"mailbox_{name}"), kind, FROM_CONFIG, f"/workspace/{name}", containerPath=f"/workspace/{name}", hostPath=f"{snap.host_path(ws.get('Source'))}/{name}", inSession=session)


def add_controls(snap: Snapshot, grp: URIRef, container: URIRef, origin: dict | None) -> None:
    """Networks and the security controls observed or configured for this session."""
    f = snap.facts
    host, config = f["inspect"]["HostConfig"], f["config"]
    for name, net in f["networks"].items():
        snap.add(snap.node(f"network_{name}"), AR.NanoClawInternalEgressNetwork if net["internal"] else AR.RemoteNetworkEndpoint, OBSERVED, name, attaches=container, internal=net["internal"], memberCount=len(net["members"]))
    snap.add(snap.node("control_lockdown"), AR.NanoClawEgressLockdownControl, OBSERVED, "egress lockdown", enabled=snap.lockdown_on, appliesTo=container)
    snap.add(snap.node("control_isolation"), AR.NanoClawContainerIsolationControl, OBSERVED, "container isolation", capDrop=host.get("CapDrop") or [], securityOpt=host.get("SecurityOpt") or [], init=bool(host.get("Init")), appliesTo=container)
    snap.add(snap.node("control_limits"), AR.NanoClawResourceLimitControl, OBSERVED, "resource limits", pidsLimit=host.get("PidsLimit") or 0, memoryBytes=host.get("Memory") or 0, nanoCpus=host.get("NanoCpus") or 0, shmBytes=host.get("ShmSize") or 0, appliesTo=container)
    snap.add(snap.node("control_cli_scope"), AR.NanoClawCLIScopeControl, FROM_CONFIG, "cli scope", value=config.get("cli_scope") or "group", appliesTo=grp)
    snap.add(snap.node("control_destination_acl"), AR.NanoClawDestinationACL, FROM_CONFIG, "destination ACL", enforced=f["a2a_installed"], appliesTo=grp)
    snap.add(snap.node("control_tool_policy"), AR.NanoClawProviderToolPolicyControl, FROM_CONFIG, "provider tool policy", permissionMode="bypassPermissions", appliesTo=container)
    if origin:
        snap.add(snap.node("control_user_access"), AR.NanoClawUserAccessControl, FROM_CONFIG, "sender admission", unknownSenderPolicy=origin["unknown_sender_policy"], appliesTo=snap.node(f"mg_{origin['id']}"))
    if f["roles"] or f["members"]:
        snap.add(snap.node("roles"), AR.NanoClawRoleMembershipState, FROM_CONFIG, "roles and members", ownerCount=sum(r["role"] == "owner" for r in f["roles"]), adminCount=sum(r["role"] == "admin" for r in f["roles"]), memberCount=len(f["members"]))


def add_gateway(snap: Snapshot, grp: URIRef) -> None:
    f = snap.facts
    agent = f["onecli_agent"]
    if not agent:
        return
    identity = snap.add(snap.node("onecli_identity"), AR.OneCLIAgentIdentity, OBSERVED, agent["name"] or agent["identifier"], identifier=agent["identifier"], identifies=grp)
    for s in f["secrets"]:
        snap.add(snap.node(f"secret_{s['name']}"), AR.Credential, OBSERVED, s["name"], hostPattern=s.get("hostPattern"), pathPattern=s.get("pathPattern"), secretType=s.get("type"), grantedBy=s.get("grant"), injectableFor=identity)
    policy = f["gateway_policy"]
    rules = applicable_rules(policy, agent["id"])
    mode, _ = effective_mode(policy)
    control = snap.add(snap.node("control_gateway_policy"), AR.OneCLIGatewayPolicy, OBSERVED, "gateway policy", engine="cedar", ruleCount=len(rules), mode=mode, strict=bool(policy and policy.get("strict")), appliesTo=identity)
    for p in rules:
        sc = p["scope"]
        rule = snap.add(snap.node(f"gateway_rule_{p['name']}"), AR.OneCLIPolicyRule, OBSERVED, p["name"], action=sc["effect"], matches=rule_label(p), group=p.get("group"), groupSource=p.get("groupSource"), methods=sc["methods"], hostPattern=sc["host"], pathPattern=sc["path"], requiresApproval=sc["approval"] or None, rateLimit=sc["rate_limit"], conditional=sc["conditional"], policyText=p["text"], description=p.get("description"))
        snap.graph.add((control, OBS.hasRule, rule))


def add_mcp_servers(snap: Snapshot, grp: URIRef) -> None:
    """MCP servers configured for the group (names and transport only)."""
    for name, spec in (snap.facts["config"].get("mcp_servers") or {}).items():
        remote = isinstance(spec, dict) and bool(spec.get("url"))
        snap.add(snap.node(f"mcp_{name}"), AR.RemoteMCPService if remote else AR.LocalMCPServerProcess, FROM_CONFIG, name, transport="http" if remote else "stdio", configuredFor=grp)


def instantiate(snap: Snapshot) -> None:
    grp, session = add_identity(snap)
    origin = add_messaging(snap, grp, session)
    container = add_container(snap, session)
    for m in snap.facts["inspect"]["Mounts"]:
        add_mount(snap, m, container)
    add_mailboxes(snap, session)
    add_controls(snap, grp, container, origin)
    add_gateway(snap, grp)
    add_mcp_servers(snap, grp)
