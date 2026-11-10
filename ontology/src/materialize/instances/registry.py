"""Environment sources: how each environment's instances are discovered.

An environment (NanoClaw itself, GitHub, Vercel, ...) registers one function with
``@environment``. Discovery calls every registered source whose ``applies`` test
passes for the session, in registration order, with a ``Context``:

    from ..registry import Context, environment
    from ..model import Instance

    @environment("slack", kinds=["SlackWorkspace", "SlackChannel"], live=True,
                 applies=lambda snap: any("slack.com" in (s.get("hostPattern") or "") for s in snap.facts["secrets"]))
    def slack(ctx: Context) -> None:
        team = ctx.probe.get_json("https://slack.com/api/team.info", {"Authorization": "Bearer placeholder"})
        ws = ctx.add(Instance("SlackWorkspace", f"slack:{team['team']['id']}", team["team"]["name"]))
        ...

Rules for a source:

- ``kinds`` are the model kinds (local names, e.g. "GitHubRepository") it lists. They
  count as discovered once the function returns without ``ctx.fail``, even if it found
  none, so "none exists" can be told apart from "not enumerated".
- Set ``parent`` to the containing instance's id, following the model's
  ar:containsKind, so resolution can place new instances ("new X in <parent>") and
  narrow by scope. Put a ``match`` attr on instances a row location can name (a host,
  a chat id), and ``events`` on handlers of ar:triggerEvent reactions.
- ``live=True`` sources get ``ctx.probe`` (``probe.ContainerProbe``): read-only
  requests from inside the session's container, as the agent, through the gateway.
  They are skipped under ``--no-discovery``. Use only reads: ``probe.get_json`` (GET)
  and ``probe.mcp(...).call`` (read-only MCP tools; others are refused). Never put
  secret values in labels or attrs (names of variables are fine, values are not).
- Limits that cannot be observed (e.g. a fine-grained token's repositories) come from
  the user's declaration: ``ctx.declared(host)`` (see declarations.py). Apply one with
  ``ctx.restrict(why, check)``; check(instance, kind, effect) returns False when the
  effect cannot reach the instance, and resolution then leaves it out and says so.
  Compare the declaration with what you observe and ``ctx.warn`` on mismatches.
- On a failure that stops the source, call ``ctx.fail(reason)``: its kinds get the
  reason as a note and are not marked discovered. Partial gaps (e.g. a listing that
  only covers owned repositories) go in ``ctx.note(kinds, text)``.

Modules in ``instances/envs/`` are imported automatically, so adding an environment
is adding one module there.
"""

from dataclasses import dataclass, field
from typing import Callable

from ..probe import ContainerProbe, ProbeError
from ..snapshot import Snapshot
from .model import Instance, Instances


class SourceFailed(Exception):
    pass


@dataclass
class Context:
    snap: Snapshot
    instances: Instances
    probe: ContainerProbe | None
    source: "Source"

    def add(self, inst: Instance) -> Instance:
        return self.instances.add(inst)

    def note(self, kinds: list[str], text: str) -> None:
        self.instances.note(kinds, text)

    def fail(self, reason: str) -> None:
        raise SourceFailed(reason)

    def warn(self, text: str) -> None:
        self.instances.warnings.append(f"{self.source.name}: {text}")

    def declared(self, host: str):
        """The user's declaration for the granted credential covering ``host`` (declarations.py), or None.
        A stale one (token replaced since) is still returned, with a warning."""
        from .declarations import for_host
        d = for_host(self.snap.facts["secrets"], host)
        if d and d.stale:
            self.warn(f"declared scope of credential '{d.credential}' may be stale: {d.stale}; re-confirm with credential_scope.py set")
        return d

    def restrict(self, why: str, check) -> None:
        """Register an access check: check(instance, kind, effect) returns False when the effect
        cannot reach that instance (it is then excluded from resolution, with ``why``)."""
        self.instances.access_checks.append((why, check))


@dataclass
class Source:
    name: str
    kinds: list[str]
    run: Callable[[Context], None]
    live: bool = False
    applies: Callable[[Snapshot], bool] = field(default=lambda snap: True)
    description: str = ""


SOURCES: list[Source] = []


def environment(name: str, kinds: list[str], live: bool = False,
                applies: Callable[[Snapshot], bool] = lambda snap: True, description: str = ""):
    """Register the decorated function as the instance source of an environment."""
    def register(fn: Callable[[Context], None]) -> Callable[[Context], None]:
        doc = (fn.__doc__ or "").strip().splitlines()
        SOURCES.append(Source(name, kinds, fn, live, applies, description or (doc[0] if doc else name)))
        return fn
    return register


def run_sources(snap: Snapshot, instances: Instances, probe: ContainerProbe | None) -> None:
    for source in SOURCES:
        if not source.applies(snap):
            continue
        if source.live and probe is None:
            instances.note(source.kinds, f"{source.name}: live discovery skipped (--no-discovery)")
            continue
        try:
            source.run(Context(snap, instances, probe, source))
        except (SourceFailed, ProbeError) as e:
            instances.note(source.kinds, f"{source.name}: {e}")
            continue
        instances.ran(source.kinds, source.description)
