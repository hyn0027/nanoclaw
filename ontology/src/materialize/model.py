"""Load the universal model and answer the questions the materializer asks of it."""

import hashlib
import re
from fnmatch import fnmatchcase
from pathlib import Path

from rdflib import Dataset, Graph, URIRef
from rdflib.namespace import RDF

from ..common.namespaces import AR
from ..common.paths import MCP_DIR, MODEL_DIRS, SKILLS_DIR, VOCABULARY


def load_model() -> Graph:
    """The vocabulary plus every universal, NanoClaw, and Claude graph, merged."""
    model = Graph()
    model.parse(VOCABULARY, format="turtle")
    for path in [p for d in MODEL_DIRS for p in sorted(d.glob("*.trig"))]:
        for graph in Dataset().parse(path, format="trig").graphs():
            for triple in graph:
                model.add(triple)
    return model


def skill_modules() -> dict[str, Path]:
    """Support-module id (ar:ontologyModuleId) -> graph file, for modules kept with the skills."""
    found = {}
    for path in sorted(SKILLS_DIR.glob("*.trig")):
        match = re.search(r'ar:ontologyModuleId "([^"]+)"', path.read_text())
        if match:
            found[match.group(1)] = path
    return found


def add_skill_graphs(model: Graph, installed: list[str]) -> list[str]:
    """Merge the graphs of installed skills that have one, plus the support modules
    they require (transitively) from the skills folder. Returns the loaded skill names."""
    skills, modules = skill_directories(), skill_modules()
    todo = [skills[name] for name in installed if name in skills]
    loaded: set[Path] = set()
    while todo:
        path = todo.pop()
        if path in loaded:
            continue
        loaded.add(path)
        for graph in Dataset().parse(path, format="trig").graphs():
            for triple in graph:
                model.add(triple)
        todo.extend(modules[m] for m in re.findall(r'"([^"]+)"', " ".join(re.findall(r'ar:requiresOntologyModule ([^;.]+)', path.read_text()))) if m in modules)
    return sorted(name for name in installed if name in skills)


def stale_skill_graphs(names: list[str]) -> dict[str, str]:
    """Skill name -> why its graph no longer matches its SKILL.md (missing file or changed hash)."""
    skills, out = skill_directories(), {}
    for name in names:
        text = skills[name].read_text()
        path = re.search(r'ar:sourcePath "([^"]+)"', text)
        pinned = re.search(r'ar:sourceSha256 "([^"]+)"', text)
        if not (path and pinned):
            continue
        source = Path(path.group(1))
        if not source.is_file():
            out[name] = f"sourcePath={source} missing"
        elif hashlib.sha256(source.read_bytes()).hexdigest() != pinned.group(1):
            out[name] = f"sourceSha256 differs from {source}"
    return out


def add_mcp_graphs(model: Graph, mcp_servers: dict) -> dict[str, str]:
    """Merge the graph of each configured MCP server whose URL matches a graph's
    ar:mcpEndpointPattern. Returns {configured server name: graph's mcpServerId}."""
    matched: dict[str, str] = {}
    graphs = {}
    for path in sorted(MCP_DIR.glob("*.trig")):
        graph = Graph()
        for g in Dataset().parse(path, format="trig").graphs():
            graph += g
        graphs[path] = graph
    # Support modules kept with the MCP graphs (e.g. github-environment), by module ID.
    modules = {str(mid): g for g in graphs.values() for mid in g.objects(None, AR.ontologyModuleId)}
    for graph in graphs.values():
        for server in graph.subjects(RDF.type, AR.MCPServer):
            patterns = [str(p) for p in graph.objects(server, AR.mcpEndpointPattern)]
            server_id = str(graph.value(server, AR.mcpServerId))
            for name, spec in (mcp_servers or {}).items():
                url = spec.get("url") if isinstance(spec, dict) else None
                if url and any(fnmatchcase(url, p) for p in patterns):
                    matched[name] = server_id
            if server_id in matched.values():
                model += graph
                for required in graph.objects(server, AR.requiresOntologyModule):
                    if str(required) in modules:
                        model += modules[str(required)]
    return matched


def pinned_source(model: Graph) -> tuple[Path | None, str]:
    """NanoClaw checkout and revision the model is pinned to (nanoclaw-core)."""
    repo = next(model.objects(AR.NanoClawCoreModule, AR.sourceRepositoryPath), None)
    revision = str(next(model.objects(AR.NanoClawCoreModule, AR.sourceRevision), ""))
    return (Path(str(repo)) if repo is not None else None), revision


def skill_directories() -> dict[str, Path]:
    """Skill directory name -> skill graph file."""
    found = {}
    for path in sorted(SKILLS_DIR.glob("*.trig")):
        match = re.search(r'ar:skillDirectory "([^"]+)"', path.read_text())
        if match:
            found[match.group(1)] = path
    return found


def side_of(model: Graph, kind: URIRef) -> URIRef | None:
    """executesOn / locatedOn, inherited along the specialization hierarchy."""
    seen, todo = set(), [kind]
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        side = model.value(k, AR.executesOn) or model.value(k, AR.locatedOn)
        if side is not None:
            return side
        todo.extend(model.objects(k, AR.specializesInvocationKind))
        todo.extend(model.objects(k, AR.specializesKind))
    return None


def mount_kind(model: Graph, dest: str, rw: bool) -> URIRef | None:
    """Most specific mount kind whose modeled containerPath matches (trailing * = prefix)."""
    best: tuple[int, URIRef] | None = None
    for kind, path in model.subject_objects(AR.containerPath):
        path = str(path)
        hit = dest.startswith(path[:-1]) if path.endswith("*") else dest == path
        modes = set(model.objects(kind, AR.mountAccessMode))
        if hit and (not modes or (AR.ReadWriteMountMode if rw else AR.ReadOnlyMountMode) in modes):
            if best is None or len(path) > best[0]:
                best = (len(path), kind)
    return best[1] if best else None
