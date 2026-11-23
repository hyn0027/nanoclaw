"""Keep each NanoClaw session's ontology current.

    python3 ontology_runtime.py sync --session <id> [--skill <name>]   # blocking, before the agent acts (host-run)
    python3 ontology_runtime.py sync --session <id> --traffic          # end of a turn: expand from its calls (host-run)
    python3 ontology_runtime.py tick [--session <id>]                  # everything at once, by hand
    python3 ontology_runtime.py status [--session <id>]
    python3 ontology_runtime.py show [--session <id>]  # what the expansions added (default: latest session)
    python3 ontology_runtime.py export [--session <id>] [--out file.trig]

A tick: (1) routes newly captured gateway calls to their sessions, (2) compares each
session's agent group (skills, MCP servers, packages, bundled skills) with what its
ontology last saw and expands for sources that have no reviewed graph, (3) expands
from the session's pending calls. Each expansion is a new named graph; state.json
records the head expansion and the last call id, so the next tick resumes there.
"""

import argparse
import fcntl
import json
import sys
from pathlib import Path

from rdflib import Dataset, Graph, Literal
from rdflib.namespace import XSD

from table_output import print_table, set_table_width

from ..materialize.model import add_mcp_graphs, add_skill_graphs, load_model
from . import base, capture, policies, questions, scope_questions
from .expand import SCHEMA, SYSTEM, batches, prompt, to_graph
from .llm import LLMError, call
from .paths import RUNTIME_DIR, SKILLS_SRC
from .store import RT, SessionOntology, now

SKILL_SOURCE_CHARS = 60000


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def session_model(fp: dict) -> Graph:
    """The base model for a session's group: universal + NanoClaw + Claude graphs, plus
    the reviewed graphs of its skills and MCP servers."""
    model = load_model()
    add_skill_graphs(model, sorted(fp["skills"]))
    add_mcp_graphs(model, {n: {"url": u} for n, u in fp["mcp_servers"].items()})
    return model


def context_text(ont: SessionOntology, fp: dict, modeled_mcp: dict) -> str:
    unmodeled_skills = sorted(set(fp["skills"]) - base.modeled_skills())
    unmodeled_mcp = {n: u for n, u in fp["mcp_servers"].items() if n not in modeled_mcp}
    return "\n".join([
        f"Ontology {ont.ontology_id}, agent group {ont.group_id}.",
        f"Skills: {', '.join(sorted(fp['skills'])) or 'none'} (no reviewed model: {', '.join(unmodeled_skills) or 'none'}).",
        f"Claude Code bundled skills: {fp['bundled_skills']}.",
        f"MCP servers: {json.dumps(fp['mcp_servers'])} (no reviewed model: {json.dumps(unmodeled_mcp) or 'none'}; "
        "if the evidence shows their tools/list, propose operations for them with mcp_tool set and invoked_through MCPToolCall or the closest invocation kind).",
        f"Packages: {json.dumps(fp['packages'])}.",
    ])


def run_expansion(ont: SessionOntology, model: Graph, *, trigger: str, title: str, evidence: str, context: str,
                  status, call_ids: set[str], source_skill: str | None, llm_model: str | None, dry_run: bool) -> dict | None:
    expansions = ont.expansion_graph()
    full = model + expansions
    text = prompt(full, expansions, ont.session_id, title, evidence, context)
    expansion_id = ont.next_expansion_id()
    log(f"{ont.session_id}: expansion {expansion_id} ({trigger}; {len(call_ids)} calls; prompt {len(text)} chars)")
    if dry_run:
        out = RUNTIME_DIR / "dry-run-prompt.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(SYSTEM + "\n\n" + text)
        log(f"  dry run: prompt written to {out}")
        return None
    proposal, usage = call(SYSTEM, text, SCHEMA, llm_model)
    graph, rejected, counts = to_graph(proposal, full, expansions, session_id=ont.session_id, expansion_id=expansion_id,
                                       status=status, call_ids=call_ids, source_skill=source_skill)
    node = next(graph.subjects(RT.expansionId, Literal(expansion_id)))
    graph.add((node, RT.ontologyId, Literal(ont.ontology_id)))
    graph.add((node, RT.trigger, Literal(trigger)))
    graph.add((node, RT.createdAt, Literal(now(), datatype=XSD.dateTime)))
    graph.add((node, RT.generatedBy, Literal(", ".join(usage["models"]))))
    if ont.state["head"]:
        graph.add((node, RT.previousExpansion, Literal(ont.state["head"])))
    entry = {"created_at": now(), "trigger": trigger, "calls": sorted(call_ids), "summary": proposal.get("summary", ""),
             "nothing_new": proposal.get("nothing_new", False), "counts": counts, "rejected": rejected, "llm": usage}
    ont.add_expansion(expansion_id, graph, entry)
    ont.save()
    log(f"  {expansion_id}: {json.dumps(counts)}; rejected {len(rejected)}; {proposal.get('summary', '')[:300]}")
    return entry


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


class SyncFailed(RuntimeError):
    """A blocking sync could not finish (the agent's action is refused)."""


def update_base(sess: dict) -> tuple[SessionOntology, dict, list[dict], dict, bool]:
    """Compare the session's agent group with what its ontology last saw; record changes.
    Sources that need a proposed graph (a skill with no reviewed model, an added package)
    are queued in state["pending_sources"] until their expansion succeeds."""
    ont = SessionOntology(sess["agent_group_id"], sess["id"])
    new_session = ont.state["base"] is None
    fp = base.fingerprint(sess["agent_group_id"])
    if fp["credentials"] is None and ont.state["base"]:
        fp["credentials"] = ont.state["base"].get("credentials")
    changes = base.diff(ont.state["base"], fp)
    modeled_mcp = base.modeled_mcp(fp["mcp_servers"])
    if new_session:
        log(f"{sess['id']}: new session ontology {ont.ontology_id}, starting from the base model")
    if changes:
        reviewed = base.modeled_skills()
        for c in changes:
            if c["what"] == "skill" and c["change"] != "removed":
                c["reviewed_model"] = c["name"] in reviewed
            if c["what"] == "mcp" and c["change"] != "removed":
                c["reviewed_model"] = c["name"] in modeled_mcp
        ont.state["base_changes"].append({"at": now(), "head": ont.state["head"], "changes": changes})
        ont.state["base"] = fp
        queue = ont.state.setdefault("pending_sources", [])
        for c in changes:
            log(f"{sess['id']}: base {c['what']} {c['change']}: {c['name']}" + ("" if c.get("reviewed_model", True) else " (no reviewed model)"))
            needs = (c["what"] == "skill" and c["change"] != "removed" and not c.get("reviewed_model", True)) or \
                    (c["what"] == "package" and c["change"] == "added")
            if needs and not any(q["what"] == c["what"] and q["name"] == c["name"] for q in queue):
                queue.append({"what": c["what"], "name": c["name"], "change": c["change"]})
        if fp["bundled_skills"] != "disabled":
            log(f"{sess['id']}: warning: Claude Code bundled skills are {fp['bundled_skills']}; they have no ontology")
    ont.save()
    return ont, fp, changes, modeled_mcp, new_session


def open_questions(sess: dict, changes: list[dict], new_session: bool) -> list[dict]:
    """Ask what only the operator knows (e.g. a token's scope) and move open questions on.
    Returns the session's questions still waiting for an answer."""
    if not new_session and changes:
        for q in scope_questions.on_connect(sess["agent_group_id"], sess["id"], changes):
            log(f"{sess['id']}: asking question {q['id']} ({q['title']}): {q['intro'].split('.')[0]}")
    scope_questions.needed(sess["agent_group_id"], [sess["id"]])
    waiting = []
    for q in questions.all_questions():
        if q["status"] != "open" or sess["id"] not in q["sessions"]:
            continue
        try:
            scope_questions.advance(q)  # interprets a plain-language answer, or applies a complete one
        except LLMError as e:
            log(f"{sess['id']}: question {q['id']}: could not interpret the answer yet: {e}")
        q = questions.get(q["id"]) or q
        if q["status"] == "open":
            waiting.append(q)
        elif q.get("result"):
            log(f"{sess['id']}: question {q['id']} answered: {q['result']}")
    return waiting


def expand_sources(ont: SessionOntology, fp: dict, modeled_mcp: dict, args, strict: bool) -> None:
    """Propose graphs for the queued sources. ``strict``: the first failure raises SyncFailed."""
    queue = list(ont.state.get("pending_sources", []))
    model = None
    for c in queue:
        model = model or session_model(fp)
        if c["what"] == "skill":
            evidence = skill_evidence(c["name"])
            title = f"skill {c['name']} (container/skills/{c['name']}), no reviewed model"
            source_skill = c["name"]
        else:
            evidence = (f"The agent group's container now installs {c['name']}. Propose operations and kinds for what this "
                        "package lets the agent reach or change, from your knowledge of it; mark evidence 'source'.")
            title = f"package {c['name']} installed in the agent container"
            source_skill = None
        try:
            run_expansion(ont, model, trigger=f"{c['what']}:{c['name']}", title=title, evidence=evidence,
                          context=context_text(ont, fp, modeled_mcp), status=RT.ProposedFromSource, call_ids=set(),
                          source_skill=source_skill, llm_model=args.model, dry_run=args.dry_run)
        except LLMError as e:
            log(f"{ont.session_id}: expansion for {c['what']} {c['name']} failed: {e}")
            if strict:
                raise SyncFailed(f"ontology expansion for {c['what']} {c['name']} failed: {e}") from e
            continue
        ont.state["pending_sources"] = [q for q in ont.state.get("pending_sources", []) if q != c]
        ont.save()


def expand_traffic(ont: SessionOntology, fp: dict, modeled_mcp: dict, args) -> None:
    pending = ont.pending()
    if not pending:
        return
    model = session_model(fp)
    for records, text, covered in batches(pending):
        try:
            run_expansion(ont, model, trigger="traffic", title=f"{len(records)} distinct calls the gateway recorded for this agent",
                          evidence=text, context=context_text(ont, fp, modeled_mcp), status=RT.ObservedTraffic,
                          call_ids=covered, source_skill=None, llm_model=args.model, dry_run=args.dry_run)
        except LLMError as e:
            log(f"{ont.session_id}: traffic expansion failed, calls stay pending: {e}")
            break
        if args.dry_run:
            break
        ont.clear_pending(covered, keep=args.keep_calls)


def tick_session(sess: dict, args) -> None:
    ont, fp, changes, modeled_mcp, new_session = update_base(sess)
    waiting = open_questions(sess, changes, new_session)
    if waiting:
        for q in waiting:
            log(f"{sess['id']}: waiting for the answer to question {q['id']} ({q['title']}); expansions held, "
                f"{len(ont.pending())} calls pending. Answer the NanoClaw DM, or: ontology_runtime.py answer {q['id']}")
        return
    if args.no_llm:
        return
    expand_sources(ont, fp, modeled_mcp, args, strict=False)
    expand_traffic(ont, fp, modeled_mcp, args)


def route_capture(keep_calls: bool) -> None:
    """Hand newly captured gateway calls to the sessions they belong to."""
    after = capture.cursor()
    records = capture.new_records(after)
    by_session, skipped = capture.route(records, base.active_sessions())
    if records:
        log(f"capture: {len(records)} new calls; routed {sum(len(v) for v in by_session.values())}; skipped {json.dumps(skipped)}")
    for s in base.active_sessions():
        if s["id"] in by_session:
            ont = SessionOntology(s["agent_group_id"], s["id"])
            ont.add_pending(by_session[s["id"]])
            ont.save()
    if records:
        capture.save_cursor(records[-1]["id"])
    if not keep_calls:
        removed = capture.prune(capture.cursor())
        if removed:
            log(f"capture: removed routed hour files {', '.join(removed)}")


def sync_session(sess: dict, args) -> None:
    """Blocking sync before the agent acts (a new turn, or loading a skill): base changes,
    the operator's answers (waits for them), expansions for new sources, and the agent's
    OneCLI policy groups. Raises SyncFailed when any of it cannot finish."""
    if args.skill and not policies.mark_loaded(sess["agent_group_id"], sess["id"], args.skill):
        log(f"{sess['id']}: skill {args.skill} is not a NanoClaw container skill; nothing to record for it")
    ont, fp, changes, modeled_mcp, new_session = update_base(sess)
    for q in open_questions(sess, changes, new_session):
        log(f"{sess['id']}: the agent waits for the answer to question {q['id']} ({q['title']})")
        questions.wait(q, scope_questions.advance, poll_s=2)
        scope_questions.advance(q)
    expand_sources(ont, fp, modeled_mcp, args, strict=True)
    try:
        summary = policies.reconcile(generate=True, log=log)
    except (policies.PolicyError, LLMError) as e:
        raise SyncFailed(f"OneCLI policy groups: {e}") from e
    mine = [s for s, item in summary["desired"].items() if sess["agent_group_id"] in item["agents"]]
    missing = [s for s in mine if s in summary["missing"]]
    if missing:
        raise SyncFailed(f"no OneCLI policy group could be made for {', '.join(missing)} (see logs/ontology-runtime/runtime.log)")


def cmd_sync(args) -> None:
    sess = base.session(args.session)
    if not sess or sess.get("status") != "active":
        sys.exit(f"no active session {args.session}")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUNTIME_DIR / "tick.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # wait for any other run; a sync never skips
    if args.traffic:
        # end of a turn: expand from the calls it made (never blocks the agent; failures retry next turn)
        route_capture(args.keep_calls)
        ont, fp, changes, modeled_mcp, new_session = update_base(sess)
        if open_questions(sess, changes, new_session):
            log(f"{sess['id']}: traffic expansion held until the open question is answered")
        else:
            expand_traffic(ont, fp, modeled_mcp, args)
        try:
            policies.reconcile(generate=True, log=log)  # skills loaded without the Skill tool (e.g. cat SKILL.md)
        except (policies.PolicyError, LLMError) as e:
            log(f"policies: {e}")
        return
    try:
        sync_session(sess, args)
    except SyncFailed as e:
        log(f"{sess['id']}: sync failed, the agent's action is refused: {e}")
        print(f"SYNC FAILED: {e}", file=sys.stderr, flush=True)
        sys.exit(2)
    log(f"{sess['id']}: sync done" + (f" (skill {args.skill})" if args.skill else ""))


def cmd_tick(args) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUNTIME_DIR / "tick.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another tick is running; skipping")
        return
    sessions = base.active_sessions()
    if args.session:
        sessions = [s for s in sessions if s["id"] == args.session]
        if not sessions:
            sys.exit(f"no active session {args.session}")
    route_capture(args.keep_calls)
    for s in sessions:
        tick_session(s, args)
    if not args.session:
        try:
            policies.reconcile(generate=not args.no_llm and not args.dry_run, log=log)
        except policies.PolicyError as e:
            log(f"policies: {e}")


def cmd_questions(args) -> None:
    rows = []
    for q in questions.all_questions():
        if q["status"] != "open" and not args.all:
            continue
        given = questions.answers(q)
        step = questions.next_step(q, given) if q["status"] == "open" else None
        rows.append((q["id"], q["status"], q["title"], ", ".join(q["sessions"]), f"{len(given)} answered",
                     step["prompt"] if step else (q.get("result") or "-")))
    if not rows:
        print("no open questions" if not args.all else "no questions")
        return
    print_table("Questions", ("Id", "Status", "Question", "Sessions", "Progress", "Next step / result"), rows, (10, 9, 40, 30, 12, 70))


def cmd_ask_scope(args) -> None:
    opened = scope_questions.reopen(args.credential)
    if not opened:
        sys.exit(f"no active session's agent is granted a credential named {args.credential!r}")
    for q in opened:
        print(f"opened question {q['id']} ({q['title']}) for {', '.join(q['sessions'])}; "
              f"it arrives as a DM within ~15 s, or answer here: ontology_runtime.py answer {q['id']}")


def cmd_answer(args) -> None:
    q = questions.get(args.id)
    if not q:
        sys.exit(f"no question {args.id}")
    if q["status"] != "open":
        sys.exit(f"question {args.id} is {q['status']}: {q.get('result')}")
    questions.ask_tty(q)
    done = scope_questions.advance(q)
    print(done["result"] if done else "not complete yet")


def cmd_policies(args) -> None:
    try:
        summary = policies.reconcile(generate=not args.no_generate, log=log)
    except policies.PolicyError as e:
        sys.exit(f"policies: {e}")
    if args.verbose or not summary["changes"]:
        print(f"needed: {', '.join(sorted(summary['desired'])) or 'none'}; missing groups: {', '.join(summary['missing']) or 'none'}")


def find_session(session_id: str | None) -> SessionOntology:
    """The given session, or by default the most recently created one (active first)."""
    if session_id:
        s = base.session(session_id)
        if not s:
            sys.exit(f"unknown session {session_id}")
    else:
        s = base.latest_session()
        if not s:
            sys.exit("no sessions")
        print(f"session: {s['id']} ({s['status']}, latest; pass --session <id> for another)", file=sys.stderr)
    return SessionOntology(s["agent_group_id"], s["id"])


def cmd_status(args) -> None:
    sessions = [base.session(args.session)] if args.session else base.active_sessions()
    rows = []
    for s in filter(None, sessions):
        ont = SessionOntology(s["agent_group_id"], s["id"])
        st = ont.state
        rows.append((ont.ontology_id, s["agent_group_id"], st["head"] or "(base only)", str(len(st["expansions"])),
                     str(len(ont.pending())), st["last_call_id"] or "-", ", ".join(sorted((st["base"] or {}).get("skills", {}))) or "-",
                     ", ".join(sorted((st["base"] or {}).get("mcp_servers", {}))) or "-"))
    print_table("Session ontologies", ("Ontology", "Agent group", "Head", "Expansions", "Pending calls", "Last call", "Skills", "MCP"), rows,
                (40, 26, 44, 10, 13, 22, 50, 30))
    print(f"capture cursor: {capture.cursor() or '-'}")


def _name(node) -> str:
    s = str(node)
    return s.rsplit(":", 1)[-1] if s.startswith("urn:agent-risk:") else s


def _clip(text, n: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


RELATIONS = {"containsKind": "contains", "referencesKind": "references", "sharesKind": "shares",
             "mayBeStoredAsKind": "may be stored as"}


def abstract_changes(g: Graph) -> tuple[list[tuple[str, str]], int]:
    """What an expansion graph adds to the abstract model, as (change, description) rows,
    and how many concrete instances it adds (listed elsewhere)."""
    from rdflib import RDF, RDFS, URIRef
    from ..common.namespaces import AR
    rows: list[tuple[str, str]] = []
    for kind in sorted(set(g.subjects(RDF.type, AR.WorldSurfaceKind)), key=str):
        spec = ", ".join(sorted(_name(s) for s in g.objects(kind, AR.specializesKind)))
        rows.append(("new kind", f"{_name(kind)}" + (f" (a kind of {spec})" if spec else "") + f": {_clip(g.value(kind, RDFS.comment))}"))
    for pred, word in RELATIONS.items():
        for a, b in sorted(g.subject_objects(AR[pred]), key=lambda x: (str(x[0]), str(x[1]))):
            if isinstance(a, URIRef) and isinstance(b, URIRef) and str(a).startswith("urn:agent-risk:") and str(b).startswith("urn:agent-risk:"):
                rows.append(("new relation", f"{_name(a)} {word} {_name(b)}"))
    for kind, note in sorted(g.subject_objects(RT.runtimeNote), key=lambda x: str(x[0])):
        rows.append(("note on kind", f"{_name(kind)}: {_clip(g.value(note, RDFS.comment))}"))
    for subject, node in sorted(g.subject_objects(AR.mayTrigger), key=lambda x: str(x[0])):
        on, eff, target = (g.value(node, p) for p in (AR.onEffectType, AR.effectType, AR.affectsKind))
        event, handler = g.value(node, AR.triggerEvent), g.value(node, AR.triggerHandlerKind)
        via = ", ".join(x for x in (f"event '{event}'" if event else "", f"handled by {_name(handler)}" if handler else "") if x)
        rows.append(("new reaction", f"when {_name(subject)} undergoes {_name(on)}, the environment may {_name(eff)} "
                     f"{_name(target)}" + (f" ({via})" if via else "") + f": {_clip(g.value(node, RDFS.comment), 100)}"))
    for op in sorted(set(g.subjects(RDF.type, AR.Operation)), key=str):
        effects = ", ".join(sorted(f"{_name(g.value(e, AR.effectType))} {_name(g.value(e, AR.affectsKind))}"
                                   for e in g.objects(op, AR.hasPotentialEffect)))
        tool = g.value(op, AR.mcpToolName)
        rows.append(("new operation", f"{g.value(op, RDFS.label) or _name(op)} via {_name(g.value(op, AR.invokedThroughKind))}"
                     + (f" (MCP tool {tool})" if tool else "") + f": {effects or 'no effects'}"))
    for a, node in sorted(g.subject_objects(RT.correspondence), key=lambda x: str(x[0])):
        rows.append(("correspondence", f"{_name(a)} may denote the same thing as {_name(g.value(node, RT.target))}: "
                     f"{_clip(g.value(node, RDFS.comment), 100)}"))
    instances = len({s for s in g.subjects(RDF.type, None) if str(s).startswith("urn:nanoclaw:session:")})
    return rows, instances


def cmd_show(args) -> None:
    ont = find_session(args.session)
    rows = [(e["id"].rsplit("/", 1)[1], e["created_at"], e["trigger"], str(len(e["calls"])),
             ", ".join(f"{k} {v}" for k, v in e["counts"].items() if v) or "nothing new", str(len(e["rejected"])), e["summary"])
            for e in ont.state["expansions"]]
    print(f"{ont.ontology_id} — head {ont.state['head'] or '(base only)'}; {ont.dir}")
    print_table("Expansions", ("#", "When", "Trigger", "Calls", "Added", "Rejected", "Summary"), rows, (5, 20, 24, 6, 40, 8, 90))
    for c in ont.state["base_changes"]:
        print(f"base change at {c['at']}: " + "; ".join(f"{x['what']} {x['change']} {x['name']}" for x in c["changes"]))
    # The abstract model each expansion added (kinds, relations, reactions, operations,
    # correspondences); concrete instances are only counted.
    model_rows = []
    for e in ont.state["expansions"]:
        g = Graph()
        for named in Dataset().parse(ont.dir / e["file"], format="trig").graphs():
            g += named
        changes, instances = abstract_changes(g)
        num = e["id"].rsplit("/", 1)[1]
        model_rows += [(num, change, what) for change, what in changes]
        if not changes:
            model_rows.append((num, "-", "no abstract model change" + (f" ({instances} instance{'s' if instances != 1 else ''} only)" if instances else "")))
        elif instances:
            model_rows.append((num, "(instances)", f"{instances} concrete instance{'s' if instances != 1 else ''}, not part of the abstract model"))
    if model_rows:
        print_table("Abstract model added (not instances)", ("#", "Change", "What"), model_rows, (5, 16, 130))


def cmd_export(args) -> None:
    ont = find_session(args.session)
    fp = ont.state["base"] or base.fingerprint(ont.group_id)
    ds = Dataset()
    model = session_model(fp)
    base_graph = ds.graph(f"urn:nanoclaw:ontology:{ont.ontology_id}/base")
    base_graph += model
    for e in ont.state["expansions"]:
        for g in Dataset().parse(ont.dir / e["file"], format="trig").graphs():
            named = ds.graph(g.identifier)
            named += g
    out = args.out or ont.dir / "export.trig"
    Path(out).write_text(ds.serialize(format="trig"))
    print(f"wrote {out}: base {len(model)} triples + {len(ont.state['expansions'])} expansions")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick", help="route captured calls and expand session ontologies")
    t.add_argument("--session")
    t.add_argument("--no-llm", action="store_true", help="route and record base changes only")
    t.add_argument("--dry-run", action="store_true", help="write the first prompt to data/ontology-runtime/dry-run-prompt.txt instead of calling the model")
    t.add_argument("--keep-calls", action="store_true", help="keep capture files and expanded calls (sessions' calls/calls.jsonl)")
    t.add_argument("--model", help="model for claude -p (default: ONTOLOGY_MODEL or the subscription default)")
    sy = sub.add_parser("sync", help="blocking sync before the agent acts (what the host runs per turn / skill load)")
    sy.add_argument("--session", required=True)
    sy.add_argument("--skill", help="the agent is loading this skill")
    sy.add_argument("--traffic", action="store_true", help="end of a turn: expand from captured calls (non-blocking use)")
    sy.add_argument("--keep-calls", action="store_true")
    sy.add_argument("--model", help="model for claude -p")
    sy.set_defaults(dry_run=False)
    s = sub.add_parser("status")
    s.add_argument("--session")
    sh = sub.add_parser("show")
    sh.add_argument("--session", help="default: the most recent session")
    e = sub.add_parser("export")
    e.add_argument("--session", help="default: the most recent session")
    e.add_argument("--out", type=Path)
    po = sub.add_parser("policies", help="switch OneCLI policy groups to follow what active sessions loaded")
    po.add_argument("--no-generate", action="store_true", help="do not create missing groups")
    po.add_argument("--verbose", action="store_true")
    qs = sub.add_parser("questions", help="questions waiting for the operator")
    qs.add_argument("--all", action="store_true", help="include answered ones")
    k = sub.add_parser("ask-scope", help="ask again what a credential can do (e.g. after changing a token's permissions)")
    k.add_argument("credential", help="OneCLI secret name, e.g. GitHub")
    a = sub.add_parser("answer", help="answer a question in this terminal")
    a.add_argument("id")
    for p in (s, sh, qs):
        p.add_argument("--width", type=int, help="fit tables into this many characters")
    args = parser.parse_args()
    set_table_width(getattr(args, "width", None))
    {"tick": cmd_tick, "sync": cmd_sync, "status": cmd_status, "show": cmd_show, "export": cmd_export,
     "questions": cmd_questions, "answer": cmd_answer, "policies": cmd_policies, "ask-scope": cmd_ask_scope}[args.cmd](args)
