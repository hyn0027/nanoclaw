"""Effects on kinds resolved to the instances they land on.

``targets`` gives one ``Target`` per instance an effect on a kind reaches:

- a read or change: the existing instances, or the instances that contain them
  when only those are known (containers from ar:containsKind);
- a Create: "new <kind> in <container instance>", going up through containers with
  no instance yet ("new X in a new Y in <instance>"), else "new <kind>";
- on a downstream path, an effect on something an earlier step creates (or on what
  it contains): that new instance;
- web resources without a source: the hosts the row's Cedar permit covers;
- otherwise "none" (a source ran and found nothing) or "unenumerated" (with why).

A row's location narrows instances to those it names (attrs 'match'); a scope
(instance ids) narrows to instances related to it.
"""

from rdflib import URIRef

from ...common.namespaces import AR
from ...common.rdf import short
from ..modifications.common import ancestors
from ..snapshot import Snapshot
from .model import Instance, Instances, Target


def containers(snap: Snapshot, kind: URIRef) -> list[str]:
    """Kinds that directly contain the kind or one of its ancestors."""
    found = []
    for k in [kind, *ancestors(snap, kind)]:
        found += [short(c) for c in snap.model.subjects(AR.containsKind, k)]
    return list(dict.fromkeys(found))


def has_source(snap: Snapshot, kind_name: str) -> bool:
    inst: Instances = snap.instances
    return kind_name in inst.discovered or any(c in inst.discovered for c in containers(snap, AR[kind_name]))


def hosts_of(snap: Snapshot, location: str | None) -> list[str]:
    """Hosts a row's location names: the hosts its Cedar permit covers, or the host line itself."""
    if not location:
        return []
    lines = [l.strip() for l in location.splitlines()]
    cedar = next((l[len("cedar: "):] for l in lines if l.startswith("cedar: ")), None)
    hosts = [l for l in lines if l and not l.startswith("cedar: ") and "." in l and " " not in l]
    if cedar:
        for p in (snap.facts["gateway_policy"] or {}).get("policies", []):
            if p["name"] == cedar and (p.get("scope") or {}).get("host"):
                hosts.append(p["scope"]["host"])
    return list(dict.fromkeys(hosts))


def _in_scope(snap: Snapshot, kind_name: str, scope, location) -> list[Instance]:
    inst: Instances = snap.instances
    return inst.at([i for i in inst.by_kind.get(kind_name, []) if inst.related(i.id, scope)], location)


def _allowed(snap: Snapshot, found: list[Instance], kind_name: str, effect: str, excluded: dict) -> list[Instance]:
    """The instances an effect on kind_name can reach under the access checks; the rest are
    counted in ``excluded`` (why -> instance labels)."""
    inst: Instances = snap.instances
    kept = []
    for i in found:
        why = inst.denied_by(i, kind_name, effect)
        if why:
            excluded.setdefault(why, []).append(i.label)
        else:
            kept.append(i)
    return kept


def innermost(snap: Snapshot, kinds: list[str]) -> list[str]:
    """The kinds, innermost first: a kind contained by another of them comes before it
    (a message lands in its chat, not on the platform that contains the chat)."""
    def outer(k: str) -> int:  # how many of the other kinds k contains
        return sum(1 for other in kinds if other != k and k in containers(snap, AR[other]))
    return sorted(kinds, key=outer)


def _parents(snap: Snapshot, kind_name: str, scope, location, effect: str | None = None, excluded: dict | None = None) -> list[Instance]:
    """Instances of the innermost container kind that has any in scope (filtered by the
    access checks for ``effect`` when given)."""
    for c in innermost(snap, containers(snap, AR[kind_name])):
        found = _in_scope(snap, c, scope, location)
        if found:
            return _allowed(snap, found, kind_name, effect, excluded) if effect is not None else found
    return []


def _named(snap: Snapshot, kind_name: str, scope, location) -> list[Instance]:
    """Instances the row's location names explicitly (attrs 'match')."""
    inst: Instances = snap.instances
    return [i for i in inst.by_kind.get(kind_name, []) if location and i.attrs.get("match") and i.attrs["match"] in location and inst.related(i.id, scope)]


def _new_in(snap: Snapshot, kind_name: str, scope, location, depth: int = 3, excluded: dict | None = None) -> list[Target]:
    """Where a new instance of the kind lands, going up through containers with no instance
    (filtered by the access checks when ``excluded`` collects what they rule out)."""
    found = _parents(snap, kind_name, scope, location, "Create" if excluded is not None else None, excluded)
    if found:
        return [Target(kind_name, f"new {kind_name} in {p.label}", "new", p.id) for p in found]
    if _parents(snap, kind_name, scope, location):
        return []  # containers exist but the access checks rule them all out: do not invent new ones
    if depth:
        for c in containers(snap, AR[kind_name]):
            if has_source(snap, c):
                deeper = _new_in(snap, c, scope, location, depth - 1, excluded)
                if deeper:
                    return [Target(kind_name, t.label.replace(f"new {c} in ", f"new {kind_name} in a new {c} in ", 1), "new", t.instance_id) for t in deeper]
    return []


def _created_container(snap: Snapshot, kind_name: str, created: frozenset[str], depth: int = 3) -> str | None:
    """The nearest container kind (transitively) created earlier on the same path."""
    level = [kind_name]
    for _ in range(depth):
        level = [c for k in level for c in containers(snap, AR[k])]
        hit = next((c for c in level if c in created), None)
        if hit:
            return hit
    return None


def targets(snap: Snapshot, kind_name: str, effect: str, location: str | None = None,
            scope: frozenset[str] | None = None, created: frozenset[str] = frozenset(), direct: bool = True) -> list[Target]:
    """``direct``: the agent's own effect, subject to the access checks (e.g. a declared token
    scope); False for reactions the environment itself carries out (a workflow run acts
    with its own token), which are already narrowed by the scope they inherit."""
    excluded: dict[str, list[str]] = {}
    found = _targets(snap, kind_name, effect, location, scope, created, excluded if direct else None)
    return found + [Target(kind_name, f"excluded {len(labels)}: {', '.join(labels[:5])}{' …' if len(labels) > 5 else ''} ({why})", "excluded")
                    for why, labels in excluded.items()]


def _targets(snap: Snapshot, kind_name: str, effect: str, location, scope, created, excluded: dict | None) -> list[Target]:
    inst: Instances = snap.instances
    kind = AR[kind_name]
    own = _in_scope(snap, kind_name, scope, location)
    if excluded is not None and effect != "Create":
        own = _allowed(snap, own, kind_name, effect, excluded)
    if snap.side_of(kind) == AR.NanoClawAgentContainer:  # e.g. the browser session: it lives in this container
        return [Target(kind_name, f"{kind_name} in container {snap.facts['container_name']}", "existing")]
    if effect == "Create":
        where = _new_in(snap, kind_name, scope, location, excluded=excluded)
        if where:
            return where
        if excluded:
            return []  # every place it could land is ruled out
        named = _named(snap, kind_name, scope, location)
        if named:  # e.g. new data in the account a credential row names
            return [Target(kind_name, f"new {kind_name} in {i.label}", "new", i.id) for i in named]
        if kind_name in inst.discovered:
            existing = len(inst.by_kind.get(kind_name, []))
            return [Target(kind_name, f"new {kind_name}" + (f" (alongside {existing} existing)" if existing else " (none exists yet)"), "new")]
    else:
        if kind_name in created:
            where = _new_in(snap, kind_name, scope, location) or [Target(kind_name, f"new {kind_name}", "new")]
            return [Target(kind_name, f"the {t.label} created earlier on this path", "new", t.instance_id) for t in where]
        holder = _created_container(snap, kind_name, created)
        if holder and not own:
            where = _new_in(snap, holder, scope, location) or [Target(holder, f"new {holder}", "new")]
            return [Target(kind_name, f"{kind_name} of the {t.label} created earlier on this path", "new", t.instance_id) for t in where]
    if own:
        return [Target(kind_name, i.label, "existing", i.id) for i in own]
    parents = _parents(snap, kind_name, scope, location, effect if excluded is not None else None, excluded)
    if parents:
        return [Target(kind_name, f"{kind_name} in {p.label}", "existing", p.id) for p in parents]
    if excluded:
        return []
    if has_source(snap, kind_name):
        why = f" ({inst.notes[kind_name]})" if kind_name in inst.notes else ""
        return [Target(kind_name, f"none: no {kind_name} " + ("in scope" if scope is not None else "exists") + why, "none")]
    hosts = hosts_of(snap, location)
    if hosts:
        return [Target(kind_name, f"{kind_name} at {h}", "hosts") for h in hosts]
    note = inst.notes.get(kind_name) or next((inst.notes[c] for c in containers(snap, kind) if c in inst.notes), None)
    return [Target(kind_name, f"not enumerated: {note or f'no instance source for {kind_name}'}", "unenumerated")]


def resolve(snap: Snapshot, kind_name: str, effect: str, location: str | None = None,
            scope: frozenset[str] | None = None, created: frozenset[str] = frozenset(), direct: bool = True) -> list[str]:
    """The targets' labels, for the tables."""
    return [t.label for t in targets(snap, kind_name, effect, location, scope, created, direct)]


def scope_of(snap: Snapshot, kind_name: str, effect: str, location: str | None = None) -> frozenset[str] | None:
    """Instance ids an effect lands on (its own instances, else their containers), narrowed
    to those the row's location names; None if unknown."""
    inst: Instances = snap.instances
    own = [i.id for i in _allowed(snap, inst.at(inst.by_kind.get(kind_name, []), location), kind_name, effect, {})]
    parents = [i.id for i in _parents(snap, kind_name, None, location, effect, {})]
    ids = parents if (effect == "Create" and parents) else (own or parents)
    return frozenset(ids) if ids else None


def own_group_scope(snap: Snapshot, effect: str) -> frozenset[str] | None:
    """Host changes stay in the session's agent group unless cli_scope is global; reads and
    delegation reach whatever exists."""
    if snap.cli_scope == "global" or effect in ("Read", "Delegate"):
        return None
    return frozenset({snap.facts["group"]["id"]})


def with_instances(snap: Snapshot, rows: list[tuple]) -> list[tuple]:
    """Host rows with the instances behind kinds that have their own instance source
    appended to the location (a file kind's location already is its instance)."""
    inst: Instances = snap.instances
    out = []
    for res, loc, effects, via, status, gate in rows:
        kind = res.strip(" └")
        extra = []
        if kind in inst.discovered:
            for e in row_effects(effects):
                shown = [t.label for t in targets(snap, kind, e, None, own_group_scope(snap, e)) if t.status != "excluded"]
                if shown:
                    extra += [f"{e}:", *[f"  ▸ {l}" for l in shown]]
        out.append((res, "\n".join([loc, *extra]) if extra else loc, effects, via, status, gate))
    return out


def row_effects(cell: str) -> list[str]:
    return sorted({x.strip() for x in cell.replace("\n", ",").split(",") if x.strip() and x.strip() != "-"}) or ["Read"]
