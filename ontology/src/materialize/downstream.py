"""What the remote effects may set off further in the remote environment.

Starts from the direct effects of the remote table (rows the gateway lets through)
and follows the environment graph, up to MAX_STEPS steps:

- triggers: ar:mayTrigger on the kind (or a kind it specializes) whose
  ar:onEffectType is the effect: the environment itself causes ar:effectType on
  ar:affectsKind (e.g. a created commit starts a workflow run);
- containment down: Delete on a kind deletes the kinds it contains, and so on;
- containment up (one level, final): Create, Update, Delete, or Execute on a kind
  updates the contents of the kinds that directly contain it (ar:containsKind),
  e.g. a new issue updates its repository; this does not propagate further;
- references (on Delete, final): deleting a kind affects the kinds that refer to
  it (ar:referencesKind and its sub-properties), e.g. a deleted label or branch
  leaves dangling references in issues or pull requests.

Triggers and deletions chain; the other two end where they apply.

Reads are included as starting effects; they propagate only through reactions the
model declares for them. Transmissions are not followed. For each influenced
resource, the shortest path per effect is kept, with the tools and Cedar entries
that start it. All of it is read from the model; nothing is inferred elsewhere.
"""

from rdflib import URIRef

from ..common.namespaces import AR
from ..common.rdf import short
from .modifications.common import ancestors
from .snapshot import Snapshot

MAX_STEPS = 4
CHANGES = {"Create", "Update", "Delete", "Execute", "Delegate", "Configure", "Install"}
# Effects a downstream path starts from: changes, and reads (what the agent can take in is an
# effect on that instance too). A read only propagates where the model says something reacts
# to it (ar:mayTrigger with ar:onEffectType ar:Read); containment updates follow changes only.
STARTS = CHANGES | {"Read"}
REFERENCE_PROPS = (AR.referencesKind, AR.authoredByKind, AR.assignedToKind)


def starts(rows: list[tuple]) -> list[tuple[URIRef, str, str, str, str, str]]:
    """(kind, effect, starter label, Cedar entry, status, location) for each changing effect of the remote rows."""
    out = []
    for res, loc, effects, via, status, _gate in rows:
        kind = AR[res.strip(" └")]
        cedar = next((line[len("cedar: "):] for line in loc.splitlines() if line.startswith("cedar: ")), "-")
        labels = [line.rstrip(":") for line in via.splitlines() if line and not line.startswith(" ")]
        for effect in (e.strip() for e in effects.split(",")):
            if effect in STARTS:
                for label in labels:
                    for st in status.splitlines():
                        out.append((kind, effect, label, cedar, st, loc))
    return out


def steps(snap: Snapshot, kind: URIRef, effect: str) -> list[tuple[URIRef, str, str, bool, tuple | None]]:
    """(next kind, next effect, how, final, handler) one step from (kind, effect); a final
    step does not propagate further. handler is (event, handler kind) for a trigger that
    only fires through instances subscribed to the event (ar:triggerEvent)."""
    m, out = snap.model, []
    for a in ancestors(snap, kind):
        for node in m.objects(a, AR.mayTrigger):
            on = m.value(node, AR.onEffectType)
            target, eff = m.value(node, AR.affectsKind), m.value(node, AR.effectType)
            if on is not None and short(on) == effect and target is not None and eff is not None:
                note = m.value(node, URIRef("http://www.w3.org/2000/01/rdf-schema#comment"))
                event, handler = m.value(node, AR.triggerEvent), m.value(node, AR.triggerHandlerKind)
                out.append((target, short(eff), f"triggers{f' ({note})' if note else ''}", False,
                            (str(event), short(handler)) if event is not None and handler is not None else None))
    if effect == "Delete":
        for child in sorted(set(m.objects(kind, AR.containsKind)), key=short):
            out.append((child, "Delete", "contains", False, None))
        for prop in REFERENCE_PROPS:
            for referrer in sorted(set(m.subjects(prop, kind)) - {kind}, key=short):
                out.append((referrer, "Update", f"referenced by ({short(prop)}): dangling reference", True, None))
    if effect in {"Create", "Update", "Delete", "Execute"}:
        for container in sorted(set(m.subjects(AR.containsKind, kind)), key=short):
            out.append((container, "Update", "contents of", True, None))
    return out


def narrow(snap: Snapshot, scope: frozenset[str] | None, handler: tuple | None) -> tuple[frozenset[str] | None, str | None]:
    """Scope after a step: for an event trigger, the discovered handler instances in scope
    that list the event (None reason) or, when none do, (None, why it stops)."""
    inst = snap.instances
    if handler is None or inst is None:
        return scope, None
    event, kind = handler
    if kind not in inst.discovered:
        return scope, None  # nothing discovered for the handler kind: cannot narrow
    candidates = [h for h in inst.by_kind.get(kind, []) if inst.related(h.id, scope)]
    hits = [h.id for h in candidates if event in h.attrs.get("events", [])]
    if hits:
        return frozenset(hits), None
    where = f"{len(candidates)} {kind} in scope" if candidates else f"no {kind} in scope"
    return frozenset(), f"no {kind} lists '{event}' ({where})"


def assess_downstream(snap: Snapshot, rows: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(rows, ruled out). Rows: (resource, influence, paths, started by, status, where it
    starts, instances), most influenced first. Where it starts is the concrete location of
    the remote rows the paths begin at; instances are the concrete instances each effect
    may land on, narrowed along the path (an event trigger continues only through the
    discovered handlers, e.g. workflows, that list the event). Ruled out: (step, started
    by, why) for reactions no discovered instance can carry."""
    from .instances import scope_of, targets

    best: dict[tuple[URIRef, str], list[str]] = {}       # (kind, effect) -> shortest path (lines)
    scopes: dict[tuple[URIRef, str], frozenset[str] | None] = {}
    made: dict[tuple[URIRef, str], frozenset[str]] = {}    # kinds created earlier on the shortest path
    starters: dict[URIRef, set[str]] = {}
    statuses: dict[URIRef, set[str]] = {}
    places: dict[URIRef, list[str]] = {}
    ruled_out: dict[tuple[str, str], set[str]] = {}
    for kind, effect, label, cedar, status, loc in starts(rows):
        starter = f"{label} [cedar: {cedar}]"
        first = f"{starter}: {effect} {short(kind)}"
        if snap.instances:
            first_targets = targets(snap, short(kind), effect, loc)
            blocked = [t for t in first_targets if t.status == "excluded"]
            if blocked and all(t.status in ("excluded", "none") for t in first_targets):
                ruled_out.setdefault((f"{effect} {short(kind)} (the agent's own effect)", blocked[0].label), set()).add(starter)
                continue
        scope0 = scope_of(snap, short(kind), effect, loc) if snap.instances else None
        frontier = [(kind, effect, [first], False, scope0, frozenset({short(kind)}) if effect == "Create" else frozenset())]
        seen = {(kind, effect, scope0)}
        for depth in range(MAX_STEPS + 1):
            nxt = []
            for k, e, path, final, scope, created in frontier:
                key = (k, e)
                if key not in best or len(path) < len(best[key]):
                    best[key] = path
                    made[key] = created - {short(k)} if e == "Create" else created
                scopes[key] = None if (key in scopes and scopes[key] is None) or scope is None else (scopes.get(key, frozenset()) | scope)
                starters.setdefault(k, set()).add(starter)
                statuses.setdefault(k, set()).add(status)
                if loc not in places.setdefault(k, []):
                    places[k].append(loc)
                if final or depth == MAX_STEPS:
                    continue
                for k2, e2, how, fin, handler in steps(snap, k, e):
                    scope2, why = narrow(snap, scope, handler)
                    if why:
                        ruled_out.setdefault((f"{e} {short(k)} → {how}: {e2} {short(k2)}", why), set()).add(starter)
                        continue
                    if (k2, e2, scope2) not in seen:
                        seen.add((k2, e2, scope2))
                        nxt.append((k2, e2, path + [f"  → {how}: {e2} {short(k2)}"], fin, scope2,
                                    created | {short(k2)} if e2 == "Create" else created))
            frontier = nxt
    out = []
    for kind in sorted({k for k, _ in best}, key=lambda k: (-len([1 for kk, _ in best if kk == k]), short(k))):
        effects = sorted(e for k, e in best if k == kind)
        paths = "\n".join("\n".join(best[(kind, e)]) for e in effects)
        who = sorted(starters[kind])
        started = "\n".join(who)
        instances = []
        for e in effects:
            direct = len(best[(kind, e)]) == 1  # the agent's own effect; later steps are reactions
            found = targets(snap, short(kind), e, places[kind][0] if places[kind] else None, scopes[(kind, e)], made[(kind, e)], direct) if snap.instances else []
            found = [t for t in found if t.status != "excluded"]  # ruled out: not shown
            snap.downstream_targets[(short(kind), e)] = found
            instances += [f"{e}:"] + [f"  {t.label}" for t in found]
        out.append((short(kind), ", ".join(effects), paths, started, "\n".join(sorted(statuses[kind])), "\n".join(sorted(places[kind])), "\n".join(instances)))
    ruled = [(step, "\n".join(sorted(who)), why) for (step, why), who in sorted(ruled_out.items())]
    return out, ruled
