"""Propose additions to a session ontology from evidence, and turn them into RDF.

Evidence is either a batch of captured calls (trigger "traffic") or a source with no
reviewed graph: a skill's SKILL.md ("skill:<name>"), or a package ("package:<mgr>:<name>").
The model sees the current ontology (base kinds plus every earlier expansion of this
session) and answers in a fixed JSON shape (SCHEMA). Everything it proposes is checked
against the ontology before it becomes triples: unknown kinds, effect types, or
instances are dropped and listed as rejected in the expansion's state entry.

Additions are additive only. A proposal about a base kind (a new containment, a new
reaction) is stored in the expansion graph as extra triples about that kind; the base
files are never touched.
"""

import hashlib
import json
import re

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ..common.namespaces import AR
from ..common.rdf import short
from .paths import SKILLS_SRC
from .store import RT

EFFECT_TYPES = ["Read", "Create", "Update", "Delete", "Transmit", "Execute", "Configure", "Authenticate", "Install", "Delegate", "Validate", "Notify"]
KIND_NAME = re.compile(r"^[A-Z][A-Za-z0-9]{2,80}$")
CALL_BODY_CHARS = 16000
BATCH_CHARS = 220000
SKILL_SOURCE_CHARS = 60000

_strs = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "nothing_new", "kinds", "kind_updates", "triggers", "operations", "instances", "instance_links", "correspondences"],
    "properties": {
        "summary": {"type": "string", "description": "What this evidence adds to the ontology, in 1-4 sentences."},
        "nothing_new": {"type": "boolean"},
        "kinds": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "comment", "specializes", "contained_in", "contains", "references", "shares", "stored_as", "evidence"],
            "properties": {
                "name": {"type": "string", "description": "PascalCase, new: not an existing kind name"},
                "comment": {"type": "string"},
                "specializes": {"type": ["string", "null"], "description": "existing or new kind this is a kind of"},
                "contained_in": _strs, "contains": _strs, "references": _strs, "shares": _strs, "stored_as": _strs,
                "evidence": {**_strs, "description": "call ids (traffic) or 'source'"},
            }}},
        "kind_updates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "contained_in", "contains", "references", "shares", "stored_as", "note", "evidence"],
            "properties": {
                "kind": {"type": "string", "description": "an EXISTING kind"},
                "contained_in": _strs, "contains": _strs, "references": _strs, "shares": _strs, "stored_as": _strs,
                "note": {"type": "string"}, "evidence": _strs,
            }}},
        "triggers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["subject", "on_effect", "effect", "affects", "event", "handler", "handler_instance", "comment", "evidence"],
            "properties": {
                "subject": {"type": "string", "description": "kind name"}, "on_effect": {"enum": EFFECT_TYPES}, "effect": {"enum": EFFECT_TYPES},
                "affects": {"type": "string", "description": "kind name"},
                "event": {"type": ["string", "null"], "description": "event name the reaction subscribes to (e.g. 'issues', 'push')"},
                "handler": {"type": ["string", "null"], "description": "KIND name whose instances subscribe to the event (e.g. GitHubWebhook); never a URL or address"},
                "handler_instance": {"type": ["string", "null"], "description": "instance id of the specific subscriber, when the evidence names one (e.g. the web hook to lists.example.org)"},
                "comment": {"type": "string"}, "evidence": _strs,
            }}},
        "operations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "comment", "invoked_through", "mcp_tool", "endpoint_pattern", "effects", "evidence"],
            "properties": {
                "name": {"type": "string", "description": "snake_case operation name"},
                "comment": {"type": "string"},
                "invoked_through": {"type": "string", "description": "an existing invocation surface kind"},
                "mcp_tool": {"type": ["string", "null"]}, "endpoint_pattern": {"type": ["string", "null"]},
                "effects": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["effect", "affects"],
                                                         "properties": {"effect": {"enum": EFFECT_TYPES}, "affects": {"type": "string"}}}},
                "evidence": _strs,
            }}},
        "instances": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "kind", "label", "in", "attributes", "evidence"],
            "properties": {
                "id": {"type": "string", "description": "stable slug, e.g. github-repo-hyn0027-agent-playground; reuse an existing instance id to add to it"},
                "kind": {"type": "string"}, "label": {"type": "string"},
                "in": {"type": ["string", "null"], "description": "id of the containing instance"},
                "attributes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "value"],
                                                            "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}},
                "evidence": _strs,
            }}},
        "instance_links": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "relation", "to", "comment", "evidence"],
            "properties": {"from": {"type": "string"}, "relation": {"type": "string"}, "to": {"type": "string"},
                           "comment": {"type": "string"}, "evidence": _strs}}},
        "correspondences": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind_a", "kind_b", "comment", "evidence"],
            "properties": {"kind_a": {"type": "string"}, "kind_b": {"type": "string"}, "comment": {"type": "string"}, "evidence": _strs}}},
    },
}

SYSTEM = """You extend a security ontology of an AI agent's environment. The agent runs in a \
NanoClaw container; everything it can reach is modeled as kinds (classes of world resources), \
how they contain or reference each other, what effects operations have on them, and how the \
environment itself reacts to effects (mayTrigger: when X undergoes effect A, the environment may \
cause effect B on Y, e.g. a new GitHub issue notifies watchers, a notification is emailed, a \
mailing list forwards it to its members).

Your job: from the evidence given, propose ONLY additions the evidence supports, so the ontology \
covers what this agent's environment actually contains and how it propagates effects:
- new kinds the evidence reveals (notification channels, people, email addresses, mailing lists, \
  webhooks, integrations, apps, teams, ...), placed with specializes / contained_in / references / \
  shares / stored_as against EXISTING kind names where possible;
- updates to existing kinds (a containment or reference the base lacks), never renaming or removing;
- environment reactions (triggers) the payloads show or that the revealed configuration implies \
  (a webhook URL on a repository, a notification setting, a subscription, a forwarding rule);
- operations, only when the evidence is a skill, package, or an MCP tool listing with no reviewed model;
- concrete instances: the actual repositories, users, email addresses, lists, channels, apps the \
  payloads name, with containment (in) and attributes copied from the payload. Prefer instances \
  over abstractions: "hyn0027/agent-playground", not "a repository";
- links between instances (member_of, delivers_to, owned_by, subscribed_to, same_as, ...);
- correspondences between kinds of DIFFERENT environments that can denote the same real-world \
  thing (a GitHub user's email and a Gmail address; a Slack user and a GitHub user), so separate \
  ontologies connect.

Credentials are not data: a request the gateway sends with an injected credential is just a request \
to that host. What the credential reaches there (the account, its repositories, projects, mailboxes, \
messages ...) is modeled only as that service's own detailed kinds: when traffic reaches a service \
whose data has no kinds yet, add them (under RemoteService, or under the service's existing kinds), \
never a generic "connected service data" kind.

Rules: use existing kind names exactly as listed. New kind names are PascalCase and prefixed with \
their environment (GitHubWebhook, GmailLabel). Every item cites evidence: the call ids it comes \
from, or "source". Do not invent instances or values that are not in the evidence. Secrets never \
appear in evidence (they are masked as ***); never reconstruct them. If the evidence adds nothing, \
set nothing_new to true and leave the lists empty."""


def kind_catalog(model: Graph) -> str:
    """Every world kind in the current ontology: name, what it specializes, what it contains, comment."""
    lines = []
    for kind in sorted(set(model.subjects(RDF.type, AR.WorldSurfaceKind)), key=short):
        spec = ",".join(sorted(short(s) for s in model.objects(kind, AR.specializesKind)))
        contains = ",".join(sorted(short(c) for c in model.objects(kind, AR.containsKind)))
        comment = re.sub(r"\s+", " ", str(model.value(kind, RDFS.comment) or ""))[:160]
        lines.append(f"{short(kind)}" + (f" <: {spec}" if spec else "") + (f" contains[{contains}]" if contains else "") + (f" — {comment}" if comment else ""))
    return "\n".join(lines)


def invocation_kinds(model: Graph) -> str:
    return ", ".join(sorted(short(k) for k in set(model.subjects(RDF.type, AR.InvocationSurfaceKind))))


def runtime_state(expansions: Graph, session_id: str) -> str:
    """What earlier expansions of this session added: kinds, triggers, instances."""
    kinds = sorted(short(k) for k in expansions.subjects(RT.addedBy, None) if isinstance(k, URIRef) and (k, RDF.type, AR.WorldSurfaceKind) in expansions)
    prefix = instance_prefix(session_id)
    instances = []
    for i in sorted(set(s for s in expansions.subjects(RT.addedBy, None) if isinstance(s, URIRef) and str(s).startswith(prefix)), key=str):
        kind = next((short(t) for t in expansions.objects(i, RDF.type)), "?")
        instances.append(f"{str(i)[len(prefix):]} ({kind}): {expansions.value(i, RDFS.label)}")
    trig = []
    for subject, node in expansions.subject_objects(AR.mayTrigger):
        if (node, RT.addedBy, None) in expansions:
            trig.append(f"{short(subject)} {short(expansions.value(node, AR.onEffectType))} -> {short(expansions.value(node, AR.effectType))} {short(expansions.value(node, AR.affectsKind))}")
    return "\n".join([
        "Kinds added earlier in this session: " + (", ".join(kinds) or "none"),
        "Reactions added earlier: " + ("; ".join(sorted(trig)) or "none"),
        "Instances known so far (reuse these ids):", *(f"  {x}" for x in instances or ["none"]),
    ])


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + f"\n…[{len(text) - limit} more chars not shown]"


def render_call(r: dict) -> str:
    req, resp = r.get("request") or {}, r.get("response") or {}
    def body(b: dict) -> str:
        if not b or not b.get("bytes"):
            return "(empty)"
        if b.get("withheld"):
            return f"({b['withheld']}; {b['bytes']} bytes)"
        if b.get("binary") or b.get("undecoded_encoding"):
            return f"(binary or undecoded, {b['bytes']} bytes)"
        return _clip(b.get("text"), CALL_BODY_CHARS) + (" …[truncated by gateway]" if b.get("truncated") else "")
    shared = f"\nshared attribution across sessions: {r['shared_attribution']}" if r.get("shared_attribution") else ""
    return (f"### call {r['id']} at {r.get('ts')}\n{r.get('method')} {r.get('url')}  → status {resp.get('status')}{shared}\n"
            f"request headers: {json.dumps({k: v for k, v in (req.get('headers') or {}).items() if k.lower() in ('content-type', 'accept', 'mcp-session-id', 'user-agent')})}\n"
            f"request body:\n{body(req.get('body') or {})}\nresponse body:\n{body(resp.get('body') or {})}")


def digest(r: dict) -> str:
    req, resp = r.get("request") or {}, r.get("response") or {}
    key = json.dumps([r.get("method"), r.get("url"), (req.get("body") or {}).get("text"), resp.get("status"), (resp.get("body") or {}).get("text")])
    return hashlib.sha256(key.encode()).hexdigest()


def batches(records: list[dict]) -> list[tuple[list[dict], str, set[str]]]:
    """Distinct calls grouped into prompt-sized batches: [(distinct records, rendered text,
    ids of every call the batch covers, repeats included)]."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(digest(r), []).append(r)
    out, current, text, covered, size = [], [], [], set(), 0
    for same in groups.values():
        r = same[0]
        block = render_call(r) + (f"\n(identical call repeated {len(same)} times: {', '.join(x['id'] for x in same[1:6])}{' …' if len(same) > 6 else ''})" if len(same) > 1 else "")
        if current and size + len(block) > BATCH_CHARS:
            out.append((current, "\n\n".join(text), covered))
            current, text, covered, size = [], [], set(), 0
        current.append(r)
        text.append(block)
        covered |= {x["id"] for x in same}
        size += len(block)
    if current:
        out.append((current, "\n\n".join(text), covered))
    return out


def skill_evidence(name: str) -> str:
    """SKILL.md plus the skill's other text files, up to a budget."""
    root = SKILLS_SRC / name
    parts, size = [], 0
    for path in [root / "SKILL.md", *sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SKILL.md")]:
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            parts.append(f"## {path.relative_to(root)} (binary, not shown)")
            continue
        block = f"## {path.relative_to(root)}\n{text}"
        if size + len(block) > SKILL_SOURCE_CHARS:
            parts.append(f"## {path.relative_to(root)} ({len(text)} chars, not shown: budget)")
            continue
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts)


def prompt(model: Graph, expansions: Graph, session_id: str, evidence_title: str, evidence: str, context: str) -> str:
    return "\n\n".join([
        f"# Session {session_id}", context,
        "# Existing world kinds (name <: specializes, contains[...] — comment)", kind_catalog(model),
        "# Existing invocation surface kinds (for operations.invoked_through)", invocation_kinds(model),
        "# This session's runtime additions so far", runtime_state(expansions, session_id),
        f"# Evidence: {evidence_title}", evidence,
    ])


# ── turning a proposal into RDF ──────────────────────────────────────────────

def instance_prefix(session_id: str) -> str:
    return f"urn:nanoclaw:session:{session_id}:instance:"


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:120]


def instance_uri(session_id: str, slug: str) -> URIRef:
    return URIRef(instance_prefix(session_id) + slugify(slug))


def to_graph(proposal: dict, model: Graph, expansions: Graph, *, session_id: str, expansion_id: str,
             status: URIRef, call_ids: set[str], source_skill: str | None = None) -> tuple[Graph, list[str], dict]:
    """(graph, rejected item descriptions, counts). ``model`` is base + earlier expansions."""
    g = Graph()
    g.bind("ar", AR)
    g.bind("rt", RT)
    rejected: list[str] = []
    counts = {"kinds": 0, "kind_updates": 0, "triggers": 0, "operations": 0, "instances": 0, "instance_links": 0, "correspondences": 0}
    world = {short(k) for k in model.subjects(RDF.type, AR.WorldSurfaceKind)} | {short(k) for k in expansions.subjects(RDF.type, AR.WorldSurfaceKind)}
    invocation = {short(k) for k in model.subjects(RDF.type, AR.InvocationSurfaceKind)}
    new_kinds = {k["name"] for k in proposal.get("kinds", []) if KIND_NAME.match(k["name"]) and k["name"] not in world}
    known = world | new_kinds
    exp = Literal(expansion_id)

    def cite(node, evidence: list[str]) -> None:
        g.add((node, RT.addedBy, exp))
        for e in evidence:
            if e in call_ids:
                g.add((node, RT.fromCall, Literal(e)))

    def relations(kind: URIRef, item: dict, where: str) -> None:
        for field, prop, reverse in (("contained_in", AR.containsKind, True), ("contains", AR.containsKind, False),
                                     ("references", AR.referencesKind, False), ("shares", AR.sharesKind, False),
                                     ("stored_as", AR.mayBeStoredAsKind, False)):
            for other in item.get(field, []):
                if other not in known:
                    rejected.append(f"{where}: {field} {other} (unknown kind)")
                    continue
                g.add((AR[other], prop, kind) if reverse else (kind, prop, AR[other]))

    for k in proposal.get("kinds", []):
        name = k["name"]
        if name not in new_kinds:
            rejected.append(f"kind {name}: " + ("already exists (use kind_updates)" if name in world else "not a PascalCase name"))
            continue
        kind = AR[name]
        g.add((kind, RDF.type, AR.WorldSurfaceKind))
        g.add((kind, RDFS.comment, Literal(k["comment"])))
        g.add((kind, AR.sourceStatus, status))
        if k.get("specializes"):
            if k["specializes"] in known:
                g.add((kind, AR.specializesKind, AR[k["specializes"]]))
            else:
                rejected.append(f"kind {name}: specializes {k['specializes']} (unknown kind)")
        relations(kind, k, f"kind {name}")
        cite(kind, k.get("evidence", []))
        counts["kinds"] += 1

    for u in proposal.get("kind_updates", []):
        if u["kind"] not in world:
            rejected.append(f"kind update {u['kind']}: not an existing kind")
            continue
        kind = AR[u["kind"]]
        relations(kind, u, f"kind update {u['kind']}")
        if u.get("note"):
            note = BNode()
            g.add((kind, RT.runtimeNote, note))
            g.add((note, RDFS.comment, Literal(u["note"])))
            cite(note, u.get("evidence", []))
        counts["kind_updates"] += 1

    prefix = instance_prefix(session_id)
    known_instances = {str(s)[len(prefix):] for s in expansions.subjects(RDF.type, None) if str(s).startswith(prefix)}
    proposed = {slugify(i["id"]) for i in proposal.get("instances", [])}
    for t in proposal.get("triggers", []):
        missing = [x for x in (t["subject"], t["affects"]) if x not in known]
        if missing:
            rejected.append(f"trigger {t['subject']} -> {t['affects']}: unknown kind {', '.join(missing)}")
            continue
        handler_instance = slugify(t["handler_instance"]) if t.get("handler_instance") else None
        if t.get("handler") and t["handler"] not in known:
            if slugify(t["handler"]) in proposed | known_instances and not handler_instance:
                handler_instance = slugify(t["handler"])  # an instance given where a kind was asked for
            else:
                rejected.append(f"trigger {t['subject']} -> {t['affects']}: handler {t['handler']} is not a kind (handler dropped, reaction kept)")
            t = {**t, "handler": None}
        if handler_instance and handler_instance not in proposed | known_instances:
            rejected.append(f"trigger {t['subject']} -> {t['affects']}: handler instance {handler_instance} unknown (dropped, reaction kept)")
            handler_instance = None
        node = BNode()
        g.add((AR[t["subject"]], AR.mayTrigger, node))
        g.add((node, AR.onEffectType, AR[t["on_effect"]]))
        g.add((node, AR.effectType, AR[t["effect"]]))
        g.add((node, AR.affectsKind, AR[t["affects"]]))
        g.add((node, RDFS.comment, Literal(t["comment"])))
        if t.get("event"):
            g.add((node, AR.triggerEvent, Literal(t["event"])))
        if t.get("handler"):
            g.add((node, AR.triggerHandlerKind, AR[t["handler"]]))
        if handler_instance:
            g.add((node, RT.handlerInstance, instance_uri(session_id, handler_instance)))
        g.add((node, AR.sourceStatus, status))
        cite(node, t.get("evidence", []))
        counts["triggers"] += 1

    skill_node = None
    for o in proposal.get("operations", []):
        if o["invoked_through"] not in invocation:
            rejected.append(f"operation {o['name']}: invoked_through {o['invoked_through']} is not an invocation surface kind")
            continue
        bad = [e["affects"] for e in o["effects"] if e["affects"] not in known]
        if bad:
            rejected.append(f"operation {o['name']}: unknown affected kind {', '.join(bad)}")
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{source_skill or 'runtime'}_{o['name']}")
        op = AR[f"rt_{slug}"]
        g.add((op, RDF.type, AR.Operation))
        g.add((op, RDFS.label, Literal(o["name"])))
        g.add((op, RDFS.comment, Literal(o["comment"])))
        g.add((op, AR.invokedThroughKind, AR[o["invoked_through"]]))
        g.add((op, AR.sourceStatus, status))
        g.add((op, AR.adaptationStatus, AR.RuntimeUnverified))
        if o.get("mcp_tool"):
            g.add((op, AR.mcpToolName, Literal(o["mcp_tool"])))
        for e in o["effects"]:
            eff = BNode()
            g.add((op, AR.hasPotentialEffect, eff))
            g.add((eff, RDF.type, AR.PotentialEffect))
            g.add((eff, AR.effectType, AR[e["effect"]]))
            g.add((eff, AR.affectsKind, AR[e["affects"]]))
        if source_skill:
            if skill_node is None:
                skill_node = AR[f"rt_skill_{re.sub(r'[^A-Za-z0-9]+', '_', source_skill)}"]
                g.add((skill_node, RDF.type, AR.Skill))
                g.add((skill_node, AR.skillDirectory, Literal(source_skill)))
                g.add((skill_node, AR.sourceStatus, status))
                g.add((skill_node, RT.addedBy, exp))
            g.add((skill_node, AR.declaresOperation, op))
        cite(op, o.get("evidence", []))
        counts["operations"] += 1

    for i in proposal.get("instances", []):
        if i["kind"] not in known:
            rejected.append(f"instance {i['id']}: unknown kind {i['kind']}")
            continue
        node = instance_uri(session_id, i["id"])
        g.add((node, RDF.type, AR[i["kind"]]))
        g.add((node, RDFS.label, Literal(i["label"])))
        g.add((node, AR.sourceStatus, status))
        if i.get("in"):
            container = slugify(i["in"])
            if container in proposed or container in known_instances:
                g.add((node, RT.instanceIn, instance_uri(session_id, container)))
            else:
                rejected.append(f"instance {i['id']}: in {i['in']} (unknown instance)")
        for a in i.get("attributes", []):
            attr = BNode()
            g.add((node, RT.attribute, attr))
            g.add((attr, RT.key, Literal(a["key"])))
            g.add((attr, RT.value, Literal(a["value"])))
        cite(node, i.get("evidence", []))
        counts["instances"] += 1

    all_instances = known_instances | proposed
    for link in proposal.get("instance_links", []):
        a, b = (slugify(x) for x in (link["from"], link["to"]))
        if a not in all_instances or b not in all_instances:
            rejected.append(f"link {link['from']} {link['relation']} {link['to']}: unknown instance")
            continue
        node = BNode()
        g.add((instance_uri(session_id, a), RT.relatedInstance, node))
        g.add((node, RT.relation, Literal(link["relation"])))
        g.add((node, RT.target, instance_uri(session_id, b)))
        g.add((node, RDFS.comment, Literal(link["comment"])))
        if link["relation"].lower() in ("same_as", "sameas", "same as"):
            g.add((instance_uri(session_id, a), OWL.sameAs, instance_uri(session_id, b)))
        cite(node, link.get("evidence", []))
        counts["instance_links"] += 1

    for c in proposal.get("correspondences", []):
        if c["kind_a"] not in known or c["kind_b"] not in known:
            rejected.append(f"correspondence {c['kind_a']} ~ {c['kind_b']}: unknown kind")
            continue
        g.add((AR[c["kind_a"]], RT.correspondsToKind, AR[c["kind_b"]]))
        node = BNode()
        g.add((AR[c["kind_a"]], RT.correspondence, node))
        g.add((node, RT.target, AR[c["kind_b"]]))
        g.add((node, RDFS.comment, Literal(c["comment"])))
        cite(node, c.get("evidence", []))
        counts["correspondences"] += 1

    # the expansion's own provenance node (the caller adds trigger, time, model)
    node = URIRef(f"urn:nanoclaw:ontology:{expansion_id}#expansion")
    g.add((node, RDF.type, RT.Expansion))
    g.add((node, RT.expansionId, exp))
    g.add((node, RT.summary, Literal(proposal.get("summary", ""))))
    for cid in sorted(call_ids):
        g.add((node, RT.fromCall, Literal(cid)))
    return g, rejected, counts
