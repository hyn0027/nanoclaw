"""Printed report: one function per table."""

from rdflib.namespace import RDF

from table_output import print_table

from .gateway_policy import applicable_rules, effective_mode
from .snapshot import Snapshot


def print_session(snap: Snapshot) -> None:
    f = snap.facts
    sess, group, config, agent = f["session"], f["group"], f["config"], f["onecli_agent"]
    policy = f["gateway_policy"]
    rules = applicable_rules(policy, agent["id"] if agent else None)
    permits = sum(p["scope"]["effect"] == "permit" for p in rules)
    _, evidence = effective_mode(policy)
    print_table("Session snapshot", ("Field", "Value"), [
        ("Session", sess["id"]),
        ("Agent group", f"{group['name']} ({group['id']})"),
        ("Other sessions in group", str(len(f["group_sessions"]) - 1)),
        ("Container", f["container_name"]),
        ("Provider", config.get("provider") or "claude"),
        ("cli_scope", snap.cli_scope),
        ("Egress lockdown", "on" if snap.lockdown_on else "off"),
        ("Destination grants", ", ".join(d["local_name"] for d in f["grants"]) or "none"),
        ("Container skills", ", ".join(f["container_skills"]) or "none"),
        (f"Materialized skills ({f.get('loaded_skills_scope', 'all')})",
         "\n".join(f"{n}" + (f" (first loaded {f['loaded_skills'][n]['first']} in {f['loaded_skills'][n]['session']})" if n in f.get("loaded_skills", {}) else "")
                   for n in f.get("materialized_skills", f["container_skills"])) or "none: no skill loaded yet"),
        ("Skills without ontology", ", ".join(n for n in f["container_skills"] if n not in f.get("skills_with_ontology", f.get("modeled_skills", []))) or "none"),
        ("MCP servers (third-party)", ", ".join(config.get("mcp_servers") or {}) or "none"),
        ("MCP servers without ontology", ", ".join(n for n in config.get("mcp_servers") or {} if n not in f.get("modeled_mcp", {})) or "none"),
        ("Gateway identity", agent["identifier"] if agent else "none"),
        ("Granted credentials", ", ".join(f"{s['name']} ({s['hostPattern']})" for s in f["secrets"]) or "none"),
        ("Gateway policy", evidence),
        ("Cedar policies in scope", f"permit={permits}; forbid={len(rules) - permits}"),
        ("Ignored policy groups", ", ".join(sorted({p["group"] for p in (f["gateway_policy"] or {}).get("ignoredPolicies", [])}))
         + f" ({', '.join(p['name'] for p in (f['gateway_policy'] or {}).get('ignoredPolicies', []))}; treated as nonexistent)"
         if (f["gateway_policy"] or {}).get("ignoredPolicies") else "none"),
        ("Claude tool lists", f"TOOL_ALLOWLIST={len(f['claude_tools']['allowlist'])}; SDK_DISALLOWED_TOOLS={len(f['claude_tools']['disallowed'])}"),
        ("Instances / triples", f"{len(set(snap.graph.subjects(RDF.type, None)))} / {len(snap.graph)}"),
    ], (26, 90))


COLUMNS = ("Resource", "Location", "Effects", "Via", "Status", "Gate")
WIDTHS = (32, 44, 18, 52, 11, 44)


REMOTE_COLUMNS = ("Endpoint", "What can happen there", "Route", "Status", "Gate")
REMOTE_WIDTHS = (60, 66, 44, 11, 40)


def parse_via(cell: str) -> tuple[list[str], list[str]]:
    """(labels, routes) of a via cell: label lines ("<tool or operation>:") each followed by
    an indented route, or "<label>: <route>" on one line."""
    labels, routes, body = [], [], []

    def flush() -> None:
        if body:
            routes.append("\n".join(body))
            body.clear()

    for line in cell.splitlines():
        if line.startswith(" "):
            body.append(line[2:])
            continue
        flush()
        if line.endswith(":"):
            labels.append(line[:-1])
        elif ": " in line:
            head, tail = line.split(": ", 1)
            labels.append(head)
            body.append(tail)
        else:
            labels.append(line)
    flush()
    return labels, routes


def union_gates(gates: list[str]) -> str:
    """Facts (one per line, deduplicated) followed by the union of the controls."""
    facts, controls = [], set()
    for g in gates:
        in_controls = False
        for line in g.splitlines():
            text = line.strip()
            if text in ("-", "") or (text.startswith("[") and text.endswith("]")):
                continue
            if text == "controls:":
                in_controls = True
            elif in_controls and line.startswith("  ") and ":" not in text:
                controls.add(text)
            else:
                in_controls = False
                if text not in facts:
                    facts.append(text)
    lines = facts + (["controls:"] + [f"  {c}" for c in sorted(controls)] if controls else [])
    return "\n".join(lines) or "-"


def endpoint_key(location: str) -> tuple[str, str]:
    """(row key, qualifier): the Cedar policy entry when the location names one (any host
    above it becomes the qualifier), else the whole location."""
    lines = location.splitlines()
    cedar = [l for l in lines if l.startswith("cedar: ")]
    if cedar:
        return cedar[0], ", ".join(l for l in lines if not l.startswith("cedar: "))
    return location, ""


def merge_routes(routes: list[str]) -> list[str]:
    """Routes sharing everything after their first step: the first steps listed together
    over one copy of the shared tail."""
    tails: dict[str, list[str]] = {}
    for r in routes:
        head, _, tail = r.partition("\n")
        tails.setdefault(tail, [])
        if head not in tails[tail]:
            tails[tail].append(head)
    return [", ".join(heads) + (f"\n{tail}" if tail else "") for tail, heads in tails.items()]


INSTANCE = "▸ "
INSTANCES_SHOWN = 5


def compact(cell: str, limit: int = INSTANCES_SHOWN) -> str:
    """Terminal form of a cell: each run of instance lines ("▸ ...") cut to ``limit``."""
    out, run = [], 0
    lines = cell.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(INSTANCE):
            run += 1
            if run <= limit:
                out.append(line)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if run > limit and not nxt.lstrip().startswith(INSTANCE):
                out.append(f"{line[:len(line) - len(line.lstrip())]}{INSTANCE}+{run - limit} more (all in report.md)")
        else:
            run = 0
            out.append(line)
    return "\n".join(out)


def print_full(title: str, headers: tuple, rows: list[tuple], widths: tuple) -> None:
    """Terminal shows compacted instance lists; the run log records the full rows."""
    print_table(title, headers, [tuple(compact(c) for c in row) for row in rows], widths, record_rows=rows)


def instance_lines(resolver, res: str, effects: set[str], loc: str) -> tuple[list[str], set[str]]:
    """(instance lines, effects that can happen) for a resource: lines once when every
    effect lands on the same instances, else per effect. The resolver returns None for an
    effect every target of which is ruled out (e.g. by a declared token scope); such
    effects are left out."""
    if resolver is None:
        return [], effects
    by_lines: dict[tuple, list[str]] = {}
    for e in sorted(effects):
        found = resolver(res, e, loc)
        if found is not None:
            by_lines.setdefault(tuple(found), []).append(e)
    kept = {e for effs in by_lines.values() for e in effs}
    if len(by_lines) <= 1:
        return [f"  {INSTANCE}{l}" for l in next(iter(by_lines), ())], kept
    return [x for lines, effs in by_lines.items() for x in [f"  {', '.join(effs)}:", *[f"    {INSTANCE}{l}" for l in lines]]], kept


def by_endpoint(rows: list[tuple], policy_text: dict[str, str] | None = None, resolver=None) -> list[tuple]:
    """One row per endpoint (the Cedar policy entry a gateway path passes, else the
    location: a destination, "*", "-"): every resource reached there with its effects,
    the host when one is known, and the tools or operations doing it; each distinct
    route once; the statuses; and the union of the gates. A Cedar endpoint shows the
    policy entry's code verbatim under its name."""
    groups: dict[str, list[tuple]] = {}
    for row in rows:
        groups.setdefault(endpoint_key(row[1])[0], []).append(row)
    out = []
    for where, items in groups.items():
        statuses = list(dict.fromkeys(st for r in items for st in r[4].splitlines()))
        what: dict[tuple[str, str], set[str]] = {}
        effects: dict[tuple[str, str], set[str]] = {}
        locs: dict[tuple[str, str], str] = {}
        routes: list[str] = []
        for res, loc, eff, via, status, _gate in items:
            labels, paths = parse_via(via)
            host = endpoint_key(loc)[1]
            key = (res.strip(" └") + (f" @ {host}" if host else ""), status.replace("\n", ", "))
            what.setdefault(key, set()).update(labels)
            locs.setdefault(key, loc)
            effects.setdefault(key, set()).update(e.strip() for e in eff.split(",") if e.strip())
            routes += [p for p in paths if p not in routes]
        lines = []
        for (res, status), labels in sorted(what.items()):
            tag = f"  [{status}]" if len(statuses) > 1 else ""
            shown, kept = instance_lines(resolver, res.split(" @ ")[0], effects[(res, status)], locs[(res, status)])
            if not kept:
                continue  # every target ruled out: it cannot happen, so it is not shown
            lines.append(f"{', '.join(sorted(kept))} · {res}{tag}")
            lines += shown
            lines.append(f"  via {', '.join(sorted(labels))}")
        if not lines:
            continue
        name = where[len("cedar: "):] if where.startswith("cedar: ") else None
        code = (policy_text or {}).get(name or "")
        endpoint = f"{where}\n" + "\n".join("  " + l for l in code.strip().splitlines()) if code else where
        out.append((endpoint, "\n".join(lines), "\n".join(merge_routes(routes)) or "-", "\n".join(statuses), union_gates([r[5] for r in items])))
    return sorted(out, key=lambda r: (not r[0].startswith("cedar: "), r[0]))


def print_modifications(snap: Snapshot, inside: list[tuple], outside: list[tuple], remote: list[tuple]) -> None:
    print_full("Inside the container: read or changed", COLUMNS, inside, WIDTHS)
    print_full("On the host, outside the container: read or changed", COLUMNS, outside, WIDTHS)
    policy = snap.facts["gateway_policy"] or {}
    texts = {p["name"]: p["text"] for p in policy.get("policies", [])}
    resolver = None
    if snap.instances is not None:
        from .instances import resolve
        from .instances import targets

        def resolver(res: str, effect: str, loc: str) -> list[str] | None:
            found = targets(snap, res, effect, loc)
            shown = [t.label for t in found if t.status != "excluded"]
            return None if found and not shown else shown
    print_full("Off this machine (remote): what can happen at each endpoint", REMOTE_COLUMNS, by_endpoint(remote, texts, resolver), REMOTE_WIDTHS)


def print_instances(snap: Snapshot) -> None:
    inst = snap.instances
    if inst is None:
        return
    kinds = sorted(inst.discovered | set(inst.notes))
    rows = [(k, "\n".join(f"{INSTANCE}{i.label}" for i in inst.by_kind.get(k, [])) or "none",
             "\n".join(x for x in (inst.sources.get(k), inst.notes.get(k)) if x)) for k in kinds]
    print_full("Discovered instances: what the kinds below stand for in this session", ("Kind", "Instances", "Source / note"), rows, (26, 90, 60))


def print_findings(snap: Snapshot) -> None:
    if snap.findings:
        print("\nModel/runtime differences:")
        for item in snap.findings:
            print(f"  - {item}")


STARTERS_SHOWN = 6


DOWNSTREAM_COLUMNS = ("Resource", "Influence", "Path (shortest per effect)", "Started by", "Status", "Where it starts", "Instances (narrowed along the path)")


def print_downstream(rows: list[tuple]) -> None:
    """The terminal shows the first STARTERS_SHOWN starters and INSTANCES_SHOWN instances per
    cell; the run log gets them all."""
    def short_starters(cell: str) -> str:
        who = cell.splitlines()
        return "\n".join(who[:STARTERS_SHOWN] + ([f"+{len(who) - STARTERS_SHOWN} more (all in report.md)"] if len(who) > STARTERS_SHOWN else []))
    shown = [tuple(compact(c) for c in (*row[:3], short_starters(row[3]), *row[4:])) for row in rows]
    print_table("Downstream in remote environments: what the remote effects may set off, and how",
                DOWNSTREAM_COLUMNS, shown, (26, 18, 70, 40, 11, 44, 60), record_rows=rows)



def print_report(snap: Snapshot, inside: list[tuple], outside: list[tuple], remote: list[tuple], downstream: list[tuple] = ()) -> None:
    """Session header, the three effect tables (native and skill-operation rows), and model/runtime differences.

    Everything else (mounts, requirements, tools, capabilities, routes, Cedar
    policies) is recorded in the snapshot graph, not printed.
    """
    print_session(snap)
    print_instances(snap)
    print_modifications(snap, inside, outside, remote)
    print_downstream(list(downstream))
    print_findings(snap)
