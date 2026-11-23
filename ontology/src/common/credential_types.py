"""Credential access models from the ontology (ar:CredentialType), for asking about and checking
a credential's declared scope. The ontology is the one source: the question cards
(runtime/topics.py), the agent's credential notice, and the materializer's access check
(materialize/instances/envs/*.py) all read it here.

A declared scope (local/credential-scopes.json) has the shape
    {"credentialType": id, "owner": {"kind": "user" | "organization", "name": str | null},
     "selection": selection key, "resources": [names] (for a by-name selection),
     "permissions": {permission key: level}}
and is checked against the type: unknown permissions and levels a permission does not
allow are dropped (``normalize``)."""

from dataclasses import dataclass, field
from functools import lru_cache
from fnmatch import fnmatchcase

from rdflib import RDF, RDFS, Dataset, Graph, Literal, Namespace

from .paths import MCP_DIR, MODEL_DIRS

AR = Namespace("urn:agent-risk:")
LEVELS = ("read", "write", "admin")  # each includes the ones before it


@dataclass(frozen=True)
class Permission:
    key: str
    label: str
    held_by: str  # kind name: the repository, the owner's account, or an organization
    levels: tuple[str, ...]
    governs: tuple[str, ...]  # kinds: read needs read, a change needs write
    governs_read: tuple[str, ...]  # kinds whose reads need this permission
    asked_first: bool


@dataclass(frozen=True)
class Selection:
    key: str
    label: str
    kind: str
    by_name: bool
    read_only: bool


@dataclass(frozen=True)
class AccessFact:
    effect: str | None  # "Read", or None for every effect
    kind: str
    condition: str | None
    comment: str


@dataclass(frozen=True)
class CredentialType:
    id: str
    label: str
    service: str
    hosts: tuple[str, ...]
    owner_kinds: tuple[str, ...]
    selections: tuple[Selection, ...]
    permissions: tuple[Permission, ...]
    always: tuple[AccessFact, ...]
    never: tuple[AccessFact, ...]
    max_lifetime_days: int | None
    may_never_expire: bool
    comment: str = ""
    by_key: dict = field(default_factory=dict, compare=False, hash=False)

    def permission(self, key: str) -> Permission | None:
        return self.by_key.get(key)

    def permissions_for(self, held_by: str) -> list[Permission]:
        return [p for p in self.permissions if p.held_by == held_by]

    def selection(self, key: str) -> Selection | None:
        return next((s for s in self.selections if s.key == key), None)


def _name(node) -> str:
    return str(node).removeprefix(str(AR))


def _graph() -> Graph:
    g = Graph()
    for path in [p for d in (*MODEL_DIRS, MCP_DIR) for p in sorted(d.glob("*.trig"))]:
        for sub in Dataset().parse(path, format="trig").graphs():
            g += sub
    return g


def _fact(g: Graph, node) -> AccessFact:
    effect = g.value(node, AR.accessEffect)
    condition = g.value(node, AR.accessCondition)
    return AccessFact(_name(effect) if effect else None, _name(g.value(node, AR.accessKind)),
                      str(condition) if condition else None, str(g.value(node, RDFS.comment) or ""))


@lru_cache(maxsize=1)
def credential_types() -> dict[str, CredentialType]:
    g = _graph()
    out = {}
    for t in g.subjects(RDF.type, AR.CredentialType):
        perms = []
        for p in g.objects(t, AR.hasPermission):
            levels = {str(lv) for lv in g.objects(p, AR.allowsLevel)}
            perms.append(Permission(
                key=str(g.value(p, AR.permissionKey)), label=str(g.value(p, RDFS.label)),
                held_by=_name(g.value(p, AR.permissionHeldBy)), levels=tuple(lv for lv in LEVELS if lv in levels),
                governs=tuple(sorted(_name(k) for k in g.objects(p, AR.governsKind))),
                governs_read=tuple(sorted(_name(k) for k in g.objects(p, AR.governsReadOfKind))),
                asked_first=g.value(p, AR.askedFirst) == Literal(True)))
        perms.sort(key=lambda p: p.label.lower())
        selections = sorted((Selection(str(g.value(s, AR.selectionKey)), str(g.value(s, RDFS.label)),
                                       _name(g.value(s, AR.selectsKind)), g.value(s, AR.selectedByName) == Literal(True),
                                       g.value(s, AR.selectionReadOnly) == Literal(True))
                             for s in g.objects(t, AR.hasResourceSelection)), key=lambda s: ("public", "all", "selected").index(s.key)
                            if s.key in ("public", "all", "selected") else 9)
        lifetime = g.value(t, AR.maxLifetimeDays)
        ct = CredentialType(
            id=str(g.value(t, AR.credentialTypeId)), label=str(g.value(t, RDFS.label)),
            service=_name(g.value(t, AR.credentialForService)),
            hosts=tuple(sorted(str(h) for h in g.objects(t, AR.credentialForHost))),
            owner_kinds=tuple(sorted(_name(k) for k in g.objects(t, AR.resourceOwnerKind))),
            selections=tuple(selections), permissions=tuple(perms),
            always=tuple(_fact(g, f) for f in g.objects(t, AR.alwaysReaches)),
            never=tuple(_fact(g, f) for f in g.objects(t, AR.neverReaches)),
            max_lifetime_days=int(lifetime) if lifetime is not None else None,
            may_never_expire=g.value(t, AR.mayNeverExpire) == Literal(True),
            comment=str(g.value(t, RDFS.comment) or ""))
        ct.by_key.update({p.key: p for p in perms})
        out[ct.id] = ct
    return out


def for_host(host_pattern: str) -> CredentialType | None:
    """The credential type presented to a host a credential's host pattern covers."""
    pattern = (host_pattern or "").lower()
    return next((t for t in credential_types().values()
                 if any(fnmatchcase(h, pattern) or fnmatchcase(pattern, h) or h == pattern for h in t.hosts)), None)


def level_at_least(have: str | None, need: str) -> bool:
    return have in LEVELS and LEVELS.index(have) >= LEVELS.index(need)


def normalize(ct: CredentialType, permissions: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Keep only the type's permissions at levels they allow; also return what was dropped."""
    kept, dropped = {}, []
    for key, level in permissions.items():
        p = ct.permission(key)
        if p is None:
            dropped.append(f"{key} (not a permission of a {ct.label})")
        elif level not in p.levels:
            dropped.append(f"{p.label} {level} (allowed: {', '.join(p.levels)})")
        else:
            kept[key] = level
    return kept, dropped


def owner_role(kind: str) -> str:
    """'user' or 'organization' (or the kind's own name) for an owner kind like GitHubUser."""
    for role in ("User", "Organization", "Enterprise"):
        if kind.endswith(role):
            return role.lower()
    return kind


def kind_noun(ct: CredentialType, kind: str, plural: bool = True) -> str:
    """'repositories' for GitHubRepository (the service prefix and CamelCase dropped)."""
    prefix = ct.service.removesuffix("Resource")
    words = "".join(f" {c.lower()}" if c.isupper() else c for c in kind.removeprefix(prefix)).strip()
    if not plural:
        return words
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


def describe_scope(ct: CredentialType, scope: dict) -> str:
    """A declared scope as one line, with the type's permission labels."""
    owner = scope.get("owner") or {}
    who = "the credential's own account" if owner_role(owner.get("kind", "")) == "user" and not owner.get("name") \
        else f"{owner_role(owner.get('kind', ''))} {owner.get('name') or '?'}"
    sel = ct.selection(scope.get("selection", ""))
    if sel is None:
        reach = "unknown"
    elif sel.by_name:
        reach = ", ".join(scope.get("resources") or []) or "none"
    else:
        reach = sel.label.lower()
    perms = scope.get("permissions") or {}
    granted = ", ".join(f"{k} {lv}" for k, lv in sorted(perms.items())) or "none beyond metadata"
    noun = kind_noun(ct, sel.kind) if sel else "resources"
    return f"owner: {who}; {noun}: {reach}; permissions: {granted}"


def facts(ct: CredentialType) -> str:
    """What every credential of the type always and never reaches, as sentences."""
    parts = [f.comment for f in ct.always] + [f.comment for f in ct.never]
    return " ".join(dict.fromkeys(p for p in parts if p))  # in order, each once
