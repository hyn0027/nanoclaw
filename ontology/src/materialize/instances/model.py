"""Instances discovered in a session, and the targets an effect resolves to."""

from dataclasses import dataclass, field


@dataclass
class Instance:
    """One concrete thing behind a model kind (``kind`` is the kind's local name).

    ``parent`` is the id of the instance that contains it (ar:containsKind), ``attrs``
    anything an environment records for resolution or risk analysis. Reserved attrs:
    ``match`` (a string that, found in a row's location, says the row is about this
    instance) and ``events`` (event names it subscribes to, for ar:triggerEvent).
    """
    kind: str
    id: str
    label: str
    parent: str | None = None
    attrs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """What one effect lands on.

    status: "existing" (instance_id is it), "new" (a new instance; instance_id is the
    container it lands in, when known), "hosts" (web resources at a host a Cedar permit
    covers; no instance), "none" (a source ran and found nothing to affect),
    "unenumerated" (no source could list it; label says why), or "excluded" (instances
    an access check, e.g. a declared token scope, rules out; label says how many and why).
    """
    kind: str
    label: str
    status: str
    instance_id: str | None = None


class Instances:
    def __init__(self) -> None:
        self.by_kind: dict[str, list[Instance]] = {}
        self.by_id: dict[str, Instance] = {}
        self.discovered: set[str] = set()       # kinds a source ran for (even with zero found)
        self.notes: dict[str, str] = {}         # kind -> why it is not (fully) enumerated
        self.sources: dict[str, str] = {}       # kind -> which source listed it
        self.warnings: list[str] = []           # declaration vs. observation mismatches, stale declarations
        # Access checks registered by sources (e.g. a declared token scope): (instance, kind, effect)
        # -> False when the effect cannot reach that instance, None when the check has no say.
        self.access_checks: list[tuple[str, object]] = []   # (why, check)

    def ran(self, kinds: list[str], source: str) -> None:
        for k in kinds:
            self.discovered.add(k)
            self.sources[k] = source

    def note(self, kinds: list[str], text: str) -> None:
        for k in kinds:
            if text not in self.notes.get(k, ""):
                self.notes[k] = f"{self.notes[k]}; {text}" if k in self.notes else text

    def add(self, inst: Instance) -> Instance:
        if inst.id not in self.by_id:
            self.by_kind.setdefault(inst.kind, []).append(inst)
            self.by_id[inst.id] = inst
        return self.by_id[inst.id]

    def denied_by(self, instance: Instance, kind: str, effect: str) -> str | None:
        """Why an effect on ``kind`` cannot reach ``instance`` (or an instance it holds), or None."""
        for why, check in self.access_checks:
            if check(instance, kind, effect) is False:
                return why
        return None

    def lineage(self, iid: str) -> list[str]:
        """The instance and its containers, innermost first."""
        out, cur = [], self.by_id.get(iid)
        while cur is not None and cur.id not in out:
            out.append(cur.id)
            cur = self.by_id.get(cur.parent) if cur.parent else None
        return out

    def children(self, iid: str) -> list[Instance]:
        return [i for i in self.by_id.values() if i.parent == iid]

    def related(self, iid: str, scope: frozenset[str] | None) -> bool:
        """In scope: a scope member, an ancestor or descendant of one (e.g. its account,
        or a workflow in a scoped repository), or sharing a container below the top
        level with one (e.g. the same repository)."""
        if scope is None:
            return True
        around = {i for s in scope for i in self.lineage(s)}
        if iid in around or scope & set(self.lineage(iid)):
            return True
        mine = {i for i in self.lineage(iid) if self.by_id[i].parent}
        return bool(mine & {i for i in around if self.by_id[i].parent})

    def at(self, items: list[Instance], location: str | None) -> list[Instance]:
        """Instances a row's location names (attrs 'match' found in it); all when it names none."""
        if not location:
            return items
        named = [i for i in items if i.attrs.get("match") and i.attrs["match"] in location]
        return named or items
