"""Instantiate the universal NanoClaw ontology for one concrete, running session.

Collects facts about a session from NanoClaw's own interfaces (host `ncl`, the
session's mailbox files, `docker inspect`, the OneCLI CLI, and the checkout's
directories), writes them as typed instances in a timestamped snapshot graph,
resolves the universal model's requirements for that session, and reports
which native capabilities and routes remain possible.

    python3 materialize.py                      # the most recently active running session
    python3 materialize.py --session <id>
    python3 materialize.py --no-write           # report only

Snapshots land in observations/ (git-ignored): they contain real identifiers,
handles, and paths. Secret values, tokens, and environment values are never
collected. Results are still may-claims: a capability marked available means
nothing observed rules it out, not that it was exercised or will succeed.
"""

import argparse

from table_output import set_table_width
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..common.paths import OBSERVATIONS_DIR
from .capabilities import assess_capabilities
from .gateway_policy import DEFAULT_IGNORED_GROUPS, deny_by_default_problem, set_ignored_groups
from .collect import collect
from .conditions import resolve_conditions
from .instantiate import instantiate
from .model import add_mcp_graphs, add_skill_graphs, load_model, pinned_source, skill_directories, stale_skill_graphs
from .modifications import assess_modifications
from .operations import assess_operations
from .downstream import assess_downstream
from .instances import discover, with_instances, write_instances
from .pair_risks import annotate
from .probe import ContainerProbe
from .run_log import RunLog
from .output import check_instance_types, write_snapshot
from .report import print_report
from .routes import assess_routes
from .snapshot import Snapshot
from .tools import assess_tools


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", help="session id (default: most recently active running session)")
    parser.add_argument("--nanoclaw", type=Path, help="NanoClaw checkout (default: nanoclaw-core sourceRepositoryPath)")
    parser.add_argument("--out", type=Path, default=OBSERVATIONS_DIR, help="snapshot directory; run logs go to <out>/runs/")
    parser.add_argument("--width", type=int, help="fit printed tables into this many characters (default: the terminal width, or 160 when output is redirected)")
    parser.add_argument("--no-write", action="store_true", help="print the report without writing a snapshot file")
    parser.add_argument("--no-discovery", action="store_true",
                        help="skip live instance discovery (read-only GitHub/Vercel calls made from the container as the agent); NanoClaw records and credential grants are still used")
    parser.add_argument("--max-pairs", type=int, help="maximum host/downstream resource pairs to annotate with the LLM (default: all; 0: skip)")
    parser.add_argument("--cross-only", action="store_true", help="annotate only host–remote instance pairs")
    parser.add_argument("--workers", type=int, default=20, help="parallel LLM calls for the pair annotation (default: 20)")
    parser.add_argument("--seed", type=int, help="random seed for the pair sample (default: a fresh one, printed)")
    parser.add_argument("--model", default="gpt-6-luna", help="OpenAI model for the pair annotation (default: gpt-6-luna)")
    parser.add_argument("--no-expand", action="store_true",
                        help="do not run a runtime-ontology tick first (route new gateway captures, expand); the session's existing expansions are still loaded")
    parser.add_argument("--base-only", action="store_true", help="ignore the session's runtime expansions: the base model only")
    parser.add_argument("--ignore-policy-group", action="append", metavar="GROUP",
                        help="treat this OneCLI Cedar policy group as nonexistent (repeatable; default: "
                             + ", ".join(DEFAULT_IGNORED_GROUPS) + ")")
    parser.add_argument("--skills-loaded-in", choices=("group", "session"), default="group",
                        help="materialize only skills the agent has loaded (invoked, or read its SKILL.md): in any session of its "
                             "agent group, closed ones included (group, default), or in this session only (session)")
    parser.add_argument("--all-skills", action="store_true", help="materialize every installed skill, loaded or not")
    parser.add_argument("--include-default-policy", action="store_true",
                        help="consider every policy group, including the default ones listed above")
    args = parser.parse_args()
    if args.max_pairs is not None and args.max_pairs < 0:
        parser.error("--max-pairs must be non-negative")
    return args


def main() -> None:
    args = parse_args()
    set_table_width(args.width)
    ignored = set(args.ignore_policy_group or ()) | (set() if args.include_default_policy else set(DEFAULT_IGNORED_GROUPS))
    set_ignored_groups(ignored)
    model = load_model()
    pinned_repo, pinned_rev = pinned_source(model)
    nanoclaw = args.nanoclaw or pinned_repo
    facts = collect(nanoclaw, args.session)
    # Only skills the agent has ever loaded (the action log shows it invoked them) are
    # materialized, unless --all-skills. FUTURE WORK: assumes a single agent for now;
    # "ever loaded" will later be tracked per agent.
    from ..runtime.policies import skills_ever_loaded
    session = facts["session"]
    facts["loaded_skills"] = skills_ever_loaded(session["agent_group_id"], session["id"] if args.skills_loaded_in == "session" else None)
    facts["loaded_skills_scope"] = "all installed (--all-skills)" if args.all_skills else (
        "this session" if args.skills_loaded_in == "session" else "any session of the agent group")
    materialized = facts["container_skills"] if args.all_skills else [n for n in facts["container_skills"] if n in facts["loaded_skills"]]
    facts["materialized_skills"] = materialized
    facts["skills_with_ontology"] = sorted(skill_directories())
    facts["modeled_skills"] = add_skill_graphs(model, materialized)
    facts["modeled_mcp"] = add_mcp_graphs(model, facts["config"].get("mcp_servers") or {})
    facts["runtime_ontology"] = None if args.base_only else load_runtime(facts, model, expand=not args.no_expand)
    taken_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    log = RunLog(args.out, facts["session"]["id"], taken_at)
    try:
        run(args, facts, model, pinned_rev, taken_at, log)
    finally:
        log.close()
    print(f"\nRun log: {log.dir}")


def load_runtime(facts: dict, model, expand: bool) -> dict | None:
    """Bring the session's runtime ontology up to date (a tick: new gateway captures, base
    changes) and merge its expansions into ``model``. None when the session has none yet."""
    from ..runtime import cli as runtime_cli
    from ..runtime.store import SessionOntology
    session = facts["session"]
    if expand:
        runtime_cli.cmd_tick(argparse.Namespace(session=session["id"], no_llm=False, dry_run=False, keep_calls=False, model=None))
    ont = SessionOntology(session["agent_group_id"], session["id"])
    if not ont.state["expansions"]:
        return {"id": ont.ontology_id, "head": None, "expansions": 0} if ont.exists else None
    model += ont.expansion_graph()
    return {"id": ont.ontology_id, "head": ont.state["head"], "expansions": len(ont.state["expansions"]), "dir": str(ont.dir)}


def run(args: argparse.Namespace, facts: dict, model, pinned_rev: str, taken_at: str, log: RunLog) -> None:
    for name in facts["container_skills"]:
        if name not in facts["skills_with_ontology"]:
            log.warn(f"skill {name} has no ontology (ontology/skills/*.trig with ar:skillDirectory \"{name}\"); its operations are not in the tables")
    for name, problem in stale_skill_graphs(facts["modeled_skills"]).items():
        log.warn(f"skill {name} ontology is stale: {problem}")
    for name in facts["config"].get("mcp_servers") or {}:
        if name not in facts["modeled_mcp"]:
            log.warn(f"MCP server {name} has no ontology (no ontology/mcp/*.trig ar:mcpEndpointPattern matches it); its tools are not in the tables")
    rt = facts.get("runtime_ontology")
    if rt:
        print(f"Runtime ontology {rt['id']}: head {rt['head'] or '(base only)'}, {rt['expansions']} expansion(s) merged")
    if facts["nanoclaw_head"] != pinned_rev:
        log.warn(f"nanoclaw_head={facts['nanoclaw_head'][:12]}; modeled_revision={pinned_rev[:12]}")

    snap = Snapshot(facts, model, taken_at)
    problem = deny_by_default_problem(facts["gateway_policy"], snap.lockdown_on)
    if problem:
        log.warn(f"precondition failed: {problem}")
        sys.exit(f"error: precondition failed: {problem} (required: cedar_policy_rules=present, GATEWAY_STRICT_DENY=on, lockdown=on)")
    instantiate(snap)
    resolve_conditions(snap)
    assess_capabilities(snap)  # fills snap.cap_status, used by every table
    assess_tools(snap)  # recorded in the snapshot graph only
    assess_routes(snap)  # recorded in the snapshot graph only
    requests: list[dict] = []
    probe = None if args.no_discovery else ContainerProbe(facts["container_name"], requests)
    discover(snap, probe)
    for warning in snap.instances.warnings:
        log.warn(warning)
    log.write_json("requests.jsonl", requests)
    write_instances(snap.instances, log.dir / "instances.json")
    inside, outside, remote = assess_modifications(snap, assess_operations(snap))
    outside_rows = outside  # without the instance lines, for the joint-risk items
    outside = with_instances(snap, outside)
    downstream, ruled_out = assess_downstream(snap, snap.raw_remote)
    check_instance_types(snap)

    log.start_tables()
    # not shown: what instances or a declared scope rule out; kept in the run log only
    log.write_json("ruled-out.jsonl", [{"step": a, "started_by": b.splitlines(), "why": c} for a, b, c in ruled_out])
    print_report(snap, inside, outside, remote, downstream)
    snapshot = None if args.no_write else write_snapshot(snap, args.out)
    if snapshot:
        print(f"\nSnapshot written: {snapshot}")
    report = log.write_report(f"materialize: session {facts['session']['id']} at {taken_at}", snap.findings, snapshot)
    print(f"Full report (unwrapped): {report}")
    annotate(snap, outside_rows, remote, downstream, model=args.model, max_pairs=args.max_pairs, log_dir=log.dir, cross_only=args.cross_only, seed=args.seed, workers=args.workers)
