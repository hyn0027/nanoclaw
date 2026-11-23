# NanoClaw skill-surface ontology prototype

## Start here

This project turns natural-language agent skills into a queryable, composable model of:

1. **what an environment contains** — files, repositories, mailboxes, messages, services, credentials, processes, and other resource kinds;
2. **how actions enter that environment** — shell, MCP tools, messaging tools, provider file/web tools, NanoClaw CLI paths, and other invocation kinds;
3. **what a skill says can be done** — named operations with prerequisites and provenance; and
4. **what those operations may affect** — typed potential effects such as read, create, update, delete, transmit, execute, configure, or notify.
5. **which outbound Internet requests they may eventually cause** — including requests made by a shell command, a delegated worker, or a host channel adapter after a local tool call.

The central analysis path is:

```text
selected skill
  → operation
  → invocation endpoint/channel
  → possible mediation route
  → potential effect
  → affected resource kind
  → broader resource kinds

selected skill → operation → potential Internet call → public endpoint kind
                                  ├─ call initiator and stage
                                  ├─ invocation path
                                  ├─ endpoint URL/template, if established
                                  └─ conditions and evidence
```

For example, the Discord skill does not merely say “send a message.” It records that the operation is invoked through `NanoClawSendMessageTool`, may traverse the outbound mailbox and channel adapter, may create a `DiscordMessage`, may transmit to a `DiscordChannel`, and requires a registered adapter, a projected destination, and platform permission.

The intended use is conservative security analysis: assemble the environment ontology with one or more skill ontologies, then ask which resources could possibly be reached or changed and through which interfaces. Results are **over-approximations of modeled possibility**, not proof that an action is enabled, authorized, successful, or observed at runtime.

### What this project is not

This is not:

- an ontology of the agent's beliefs, prompts, goals, or internal reasoning;
- a planner or workflow engine that predicts action order;
- a live inventory of deployed tools, mounts, credentials, users, or permissions;
- a policy-enforcement mechanism;
- proof that every effect in a skill will occur; or
- a claim that an original skill executes unchanged under NanoClaw.

The model describes **potential endpoints and world-facing consequences**. Runtime reachability still depends on concrete configuration, arguments, identity, authority, state, and policy decisions.

### Thirty-second orientation

```text
ontology/universal/*.trig            generic always-available modules (files, shell, Internet, tool endpoints)
ontology/nanoclaw/*.trig             NanoClaw harness modules (core, messaging, control, runtime, network)
ontology/claude/*.trig               Claude provider modules (built-in tools)
ontology/skills/<skill>.trig         operations and potential effects for one skill
ontology/skills/git-resources.trig   shared support ontology, not a skill
ontology/skills/container-tooling.trig  shared support for NanoClaw's container skills, not a skill
ontology/skills/vercel.trig          shared Vercel support (CLI, API, account entities), not a skill
ontology/universal/vocabulary.ttl    schema vocabulary
ontology/universal/shapes.ttl        SHACL integrity constraints
ontology/examples/*.ttl              standalone concrete observation fixtures
manifest.json                        one list of selected ontology IDs
loader.py                            entry point: assembly, validation, and reports
materialize.py                       entry point: instantiate the model for one live NanoClaw session
src/                                 implementation package for both entry points
llm_annotation.py                    optional LLM joint-risk annotation
observations/                        session snapshots written by materialize.py (git-ignored)
```

The loader never fetches or dynamically introduces a dependency. A dependency named by any selected ontology must already appear in `manifest.json`; otherwise loading aborts before the combined graph is assembled.

### Sources and evidence boundary

The `ontology/` directory combines source-derived models:

- `ontology/universal/` describes the generic resource vocabulary (files, shell, Internet, tool endpoints), plus the shared vocabulary and SHACL shapes.
- `ontology/nanoclaw/` describes the NanoClaw harness architecture that is present independently of a selected skill.
- `ontology/claude/` describes the Claude provider's built-in tools as a NanoClaw container exposes them.
- `ontology/skills/` describes interfaces and potential effects asserted by individual `SKILL.md` files, plus support graphs shared only by those skills. Sources are third-party skills under `top_skills/*/SKILL.md` and the container skills NanoClaw ships in its checkout (`container/skills/*/SKILL.md`: `agent-browser`, `frontend-engineer`, `onecli-gateway`, `self-customize`, `welcome`), which NanoClaw mounts into every session.

The NanoClaw model was checked against local checkout commit `08ceb915e23fff1ec22a322cd44bb82294d9907b` (branch `customized`: upstream `7902716b` plus local commits). It models architecture abstractly: installed channel adapters appear only as the generic adapter kinds, and per-install settings (such as whether egress lockdown is enabled) are conditions, not facts. Host-only behavior that an agent cannot trigger is out of scope, as is model-provider API traffic. Repository code is treated as the primary source; the [shared architecture discussion](https://chatgpt.com/s/cx_6ab168a066788191a0767199e971bbd4) was useful orientation but is not the authority when it differs from code. The model is not a statement about the live configuration of any NanoClaw installation.

All graphs use `ar: <urn:agent-risk:>`. These are local identifiers, not dereferenceable Web URLs. A published ontology should use an owned persistent HTTPS namespace.

```text
skill_ontologies/
  manifest.json          one list of selected ontology IDs
  loader.py              entry point → src.loader
  llm_annotation.py      resource pairing, prompts, and OpenAI calls
  table_output.py        aligned text tables for the CLI reports
  generate_mcp_ontology.py   MCP server snapshot → ontology/mcp/<id>.trig
  generate_mcp_policies.py   MCP server snapshot → OneCLI Cedar policy group
  materialize.py         entry point → src.materialize
  observations/          snapshot graphs (git-ignored; contain real identifiers)
  src/                   code
    common/              paths.py, namespaces.py, rdf.py (parsing and label helpers)
    loader/              catalog.py (manifest, discovery, dependency preflight)
                         assemble.py (source pins, shared-graph rules)
                         checks.py (typed relations, hierarchies, SHACL)
                         inventory.py (operations, effects, calls, native capabilities)
                         report.py (one function per table)
                         annotation_input.py (resource descriptions for the LLM)
                         cli.py (main)
    materialize/         runtime.py (read-only CLI wrappers) and collect.py (facts)
                         model.py (model queries: sides, mount kinds, pins)
                         snapshot.py (snapshot graph and shared state)
                         instantiate.py, conditions.py, capabilities.py, routes.py
                         gateway_policy.py (OneCLI Cedar policies, credential grants, strict deny)
                         provider_tools.py (reads NanoClaw's tool lists), tools.py (per-tool table)
                         modifications/ (inside.py, host.py, remote.py, common.py)
                         report.py, output.py, constants.py, cli.py (main)
  ontology/              every ontology graph
    universal/
      vocabulary.ttl
      shapes.ttl
      tool-endpoints.trig
      local-filesystem.trig
      freeform-internet.trig
      shell-execution.trig
    nanoclaw/
      nanoclaw-core.trig
      nanoclaw-messaging.trig
      nanoclaw-control.trig
      nanoclaw-runtime.trig
      nanoclaw-network.trig
    claude/
      claude-tools.trig
    skills/
      git-resources.trig
      container-tooling.trig
      vercel.trig
      ...one graph per skill...
    mcp/                 generated MCP server graphs; sources/ holds their pinned snapshots
    examples/            sample observation graphs for external RDF tools
```

### Non-negotiable project invariants

An agent changing this project should preserve these rules:

- `manifest.json` is one JSON array of unique ontology IDs.
- Every `.trig` file contains exactly one non-empty named graph with an IRI identifier.
- Every module graph contains exactly one `ar:OntologyModule` with a unique `ar:ontologyModuleId`.
- Every skill graph contains exactly one `ar:Skill` with a unique `ar:skillDirectory`, one source path, and one source hash.
- Dependency metadata validates a static load policy; it never triggers dynamic loading.
- Always-loaded graphs contain no operations, effects, or operation/effect links.
- A module marked `ar:resourcesOnly true` contains no invocation kinds.
- Every declared `ar:ToolEndpointKind` is an invocation kind and ultimately specializes `ar:ToolEndpoint`.
- Resource and invocation specialization graphs are acyclic.
- Every kind with an `owl:equivalentClass` definition has a SHACL target shape.
- Skill-source hashes and pinned repository revisions are drift detectors, not security attestations.

## Conceptual model

The ontology deliberately keeps several concepts separate because collapsing them creates misleading security conclusions.

| Concept | RDF type | Meaning | Example |
|---|---|---|---|
| Skill | `ar:Skill` | Provenance-bearing model derived from one `SKILL.md` | `ar:skill_discord` |
| Ontology module | `ar:OntologyModule` | Statically discoverable graph and its dependencies | `ar:NanoClawMessagingModule` |
| World surface kind | `ar:WorldSurfaceKind` | Kind of state, resource, component, recipient, or external object | `ar:LocalFile`, `ar:DiscordMessage` |
| Invocation surface kind | `ar:InvocationSurfaceKind` | Kind of path through which behavior is invoked or mediated | `ar:ShellExecution`, `ar:NanoClawOutboundMailboxWrite` |
| Tool endpoint kind | `ar:ToolEndpointKind` | Invocation kind callable by, or on behalf of, an agent | `ar:MCPToolEndpoint`, `ar:NanoClawSendMessageTool` |
| Operation | `ar:Operation` | Action described by a selected skill | `ar:op_discord_send` |
| Native capability | `ar:NativeCapability` | Potential action of a loaded harness endpoint, independent of skill text | container file read; shell file write |
| Potential effect | `ar:PotentialEffect` | Possible typed consequence of an operation | create a message; transmit to a channel |
| Potential Internet call | `ar:PotentialInternetCall` | Possible outbound request, distinguished from the operation and its world effect | a later host Discord API request |
| Requirement | `ar:Requirement` | Condition needed for the modeled action/effect | destination exists; permission is granted |
| Security control kind | `ar:SecurityControlKind` | Kind of configuration or enforcement point | destination ACL; mount allowlist |
| Mount access mode | `ar:MountAccessMode` | Read-only or read-write at one mount boundary, not effective OS permission | `ar:ReadOnlyMountMode` |
| Adaptation mapping | `ar:AdaptationMapping` | Auditable source-skill-to-NanoClaw translation | generic message send → NanoClaw send tool |

### Operations and effects

A skill connects operations to interfaces, resources, conditions, evidence, and effects:

```turtle
ar:op_discord_send a ar:Operation ;
  ar:invokedThroughKind ar:NanoClawSendMessageTool ;
  ar:targetsKind ar:DiscordChannel ;
  ar:requires ar:cond_discord_adapter_registered,
    ar:cond_discord_target_permission ;
  ar:sourceStatus ar:SkillText ;
  ar:adaptationStatus ar:InferredForNanoClaw ;
  ar:hasPotentialEffect [
    a ar:PotentialEffect ;
    ar:effectType ar:Create ;
    ar:affectsKind ar:DiscordMessage
  ] .
```

Important interpretation rules:

- `targetsKind` says what an operation is directed at; it is not itself an effect.
- `hasPotentialEffect` is a **may-effect**. It does not assert success or occurrence.
- `requires` records a condition but does not evaluate it.
- `sourceStatus` describes evidence from the source skill or bundled code.
- `adaptationStatus` separately marks environment-specific translation.
- `invokedThroughKind` identifies an entry point; `routesToKind` describes possible mediation after that point.
- A read effect is still security-relevant even when it does not mutate world state.

### Internet-call qualification

An `ar:PotentialInternetCall` is a **may-call** to an endpoint kind explicitly typed `ar:PublicInternetEndpointKind` and specialized from `ar:PublicInternetEndpoint`. The call names its initiating component (`callInitiatorKind`), invocation path (`callViaKind`), stage (`callStage`), destination kind (`callEndpointKind`), source evidence, and—only when supported—an `callEndpointPattern` URL or protocol template. It inherits the operation's `requires` conditions and can add `callConditionalOn` conditions. SHACL and the loader reject incomplete calls and destination kinds outside this public-endpoint hierarchy.

The boundary is public Internet egress, including a direct request or one mediated by OneCLI. A local shell process, stdio MCP call, Unix socket, mailbox write, container-to-host handoff, and private-network hop do **not** qualify by themselves. Nor does merely targeting a remote-looking resource or using a tool named `web` prove a concrete public endpoint. `routesToKind` is a possible architecture path; `mayInitiateInternetCall` is a separate, explicit operation-level claim.

For example, `op_discord_send` invokes a local MCP tool and writes the outbound mailbox; the modeled Internet call is the *later host adapter delivery* to `DiscordAPIEndpoint`. `op_meme_local_render` may fetch a template image only on a cache miss. `op_sherpa_download` has separate possible runtime-archive and voice-model calls. `op_prepare_worktree`'s `git fetch` qualifies only if the verified canonical remote is public-Internet-addressable. These call objects are categories of possible requests, not a packet count; retries, redirects, pagination, and downstream services may produce more calls.

The absence of a call object is **not proof of no egress**. In particular, arbitrary shell scripts, `op run` child commands, a delegated coding worker, optional browser/MCP servers, and provider web tools can perform additional calls that cannot be enumerated from these skill files. A direct 1Password CLI call is modeled only conditionally for cloud-backed modes; local desktop IPC is not itself Internet egress. Generic destinations such as `NotificationDestination` and `ConfiguredNanoClawChannel` are not automatically classified as public endpoints because they may resolve to local or private routes.

### Four relationships that must not be conflated

```text
specializesKind             subtype:       DiscordMessage is a MessagingMessage
containsKind                composition:   DiscordChannel may contain DiscordMessage
routesToKind                mediation:     SendMessageTool may route to mailbox write
protectedByKind             control link:  host delivery is subject to destination ACL
```

Only specialization means “is a.” Containment does not imply subtype. A route is a possible architecture path, not runtime reachability. A protection edge says a control is relevant, not that it is enabled or permits the action.

### Why both RDF/OWL and SHACL are used

- RDF/TriG preserves named-graph provenance and supports cross-skill composition.
- OWL class definitions support positive subtype and necessary-and-sufficient classification in an OWL reasoner.
- SHACL enforces structural expectations and closed-world validation requirements.
- Python performs checks that are operational or awkward to encode declaratively, including source hashes, Git revision pins, dependency policy, cycle detection, and report generation.

OWL uses open-world semantics: an absent fact is unknown, not false. SHACL is used where the project intentionally needs closed-world validation. The loader checks the OWL definitions and SHACL shapes but does not run an OWL reasoner over instance observations.

## What gets loaded

The active model is assembled as follows:

```text
vocabulary.ttl
  + shared ontology graphs listed in manifest.json
  + skill graphs listed in manifest.json
  + MCP server graphs listed in manifest.json (ontology/mcp/)
  = combined validation and reporting graph
```

`manifest.json` lists selected ontology IDs without separate module and skill fields. RDF resources inside each graph own its identity, dependencies, and provenance. The loader discovers the corresponding graph files from that metadata.

The current module inventory is:

| Module ID | File | Responsibility |
|---|---|---|
| `tool-endpoints` | `ontology/universal/tool-endpoints.trig` | Common callable endpoint parent, messaging tools, and MCP tool/transport distinctions |
| `local-filesystem` | `ontology/universal/local-filesystem.trig` | Files, directories, metadata, symlinks, and structural entry observations |
| `freeform-internet` | `ontology/universal/freeform-internet.trig` | Uncontrolled Internet hosts, endpoints, services, web resources, logs, and accounts |
| `shell-execution` | `ontology/universal/shell-execution.trig` | Shell/command/script invocation and process-related resources |
| `nanoclaw-core` | `ontology/nanoclaw/nanoclaw-core.trig` | Host, database, agent groups, sessions, containers, runners, and provider runtime |
| `nanoclaw-messaging` | `ontology/nanoclaw/nanoclaw-messaging.trig` | Channels, destinations, inbound/outbound mailboxes, delivery, and built-in messaging tools |
| `nanoclaw-control` | `ontology/nanoclaw/nanoclaw-control.trig` | `ncl` management paths, tasks, guards, approval, roles, and destination controls |
| `nanoclaw-runtime` | `ontology/nanoclaw/nanoclaw-runtime.trig` | Container/host filesystems, mounts, shell/file tools, isolation, and resource limits |
| `nanoclaw-network` | `ontology/nanoclaw/nanoclaw-network.trig` | OneCLI, direct/proxied egress, local/remote MCP, and lockdown controls |
| `claude-tools` | `ontology/claude/claude-tools.trig` | Claude built-in tools: names, the NanoClaw endpoints they implement, per-tool capabilities and refinements |
| `git-resources` | `ontology/skills/git-resources.trig` | Reusable Git resource kinds and qualified working-tree definitions |
| `container-tooling` | `ontology/skills/container-tooling.trig` | Shared by NanoClaw's container skills: the agent-browser CLI and browser state (pages, cookies, saved auth state, captures), and package managers with the npm registry |
| `vercel` | `ontology/skills/vercel.trig` | Shared by `vercel-cli` and `frontend-engineer`: the `vercel` CLI, `api.vercel.com` and the calls to it, the CLI and credential requirements, the linked project directory and pulled `.env`, and the Vercel account's entities (account, project, deployment, build, environment variable, domain) with containment and what Vercel does on its own (a deployment is built; a production deployment takes over the project's domains; builds read the project's environment variables and reach the Internet outside the gateway) |

`git-resources` lives under `ontology/skills/` because it is skill-oriented support vocabulary, but it is still an ontology module rather than an `ar:Skill`. It is selected because `gh-issues` requires it; `coding-agent` also requires it. Static dependency validation would reject either skill if this ontology were absent. Folder placement and loading policy are separate concerns. `container-tooling` is the same kind of support module: the agent-browser CLI and its browser state, and package managers with the npm registry, shared by `agent-browser`, `frontend-engineer`, `self-customize`, and `vercel-cli`. `vercel` likewise holds everything about Vercel that `vercel-cli` and `frontend-engineer` share, so neither skill graph redefines it; a skill graph holds only that skill's operations and adaptation notes. A `ar:PotentialInternetCall` declared in a support module may be initiated by operations of several skills.

The current skill inventory is:

| Skill directory | Graph file | Main modeled capability | Declared module dependencies |
|---|---|---|---|
| `agent-browser` | `ontology/skills/agent-browser.trig` | NanoClaw container skill: headless browsing, form interaction, site logins, uploads, saved auth state, cookies, page JS | `container-tooling`, `nanoclaw-runtime`, `nanoclaw-network` |
| `frontend-engineer` | `ontology/skills/frontend-engineer.trig` | NanoClaw container skill: edit, install, build, dev server, browser verify, Vercel deploy (conditional on `/add-vercel`), report | `container-tooling`, `vercel`, `nanoclaw-runtime`, `nanoclaw-network`, `nanoclaw-messaging` |
| `vercel-cli` | `ontology/skills/vercel-cli.trig` | NanoClaw container skill (copied in by `/add-vercel`): `whoami`, local build, preview/production deploy, inspect/list, live-URL check with curl, `link`, `domains add`, `env pull` (decrypted values into a local `.env`), `env add`, delegating site builds to a Frontend Engineer agent (`create_agent` + `send_message`) | `vercel`, `container-tooling`, `nanoclaw-core`, `nanoclaw-runtime`, `nanoclaw-network`, `nanoclaw-control`, `nanoclaw-messaging` |
| `onecli-gateway` | `ontology/skills/onecli-gateway.trig` | NanoClaw container skill: explains gateway credential injection; operations are credential stubs and connect links (what a credential reaches is modeled by each service's own ontology) | `nanoclaw-runtime`, `nanoclaw-network`, `nanoclaw-messaging` |
| `self-customize` | `ontology/skills/self-customize.trig` | NanoClaw container skill: edit memory/instructions, `install_packages`, `add_mcp_server`, `create_agent` builder, delegation; builder source edits unsupported (`/app/src` is read-only) | `container-tooling`, `nanoclaw-core`, `nanoclaw-runtime`, `nanoclaw-control`, `nanoclaw-messaging`, `nanoclaw-network` |
| `welcome` | `ontology/skills/welcome.trig` | NanoClaw container skill: greeting and capability tour via messaging tools, channel addenda, first memory entries | `nanoclaw-core`, `nanoclaw-runtime`, `nanoclaw-messaging` |
| `1password-1.0.0` | `ontology/skills/1password.trig` | CLI/browser-oriented credential access and injection | `nanoclaw-runtime`, `nanoclaw-network` |
| `coding-agent` | `ontology/skills/coding-agent.trig` | Worktrees, coding workers, process control, PRs, notification | `git-resources`, `nanoclaw-runtime`, `nanoclaw-messaging`, `nanoclaw-network` |
| `configure-channel` | `ontology/skills/configure-channel.trig` | Channel inspection/configuration and delivery tests | `nanoclaw-messaging`, `nanoclaw-control` |
| `diagram-maker-1.0.0` | `ontology/skills/diagram-maker.trig` | Read/write/verify diagram artifacts | `nanoclaw-runtime` |
| `discord` | `ontology/skills/discord.trig` | Send text/files, edit, and react through NanoClaw messaging | `nanoclaw-messaging` |
| `gh-issues` | `ontology/skills/gh-issues.trig` | GitHub issue/PR orchestration, Git, delegation, notification | `git-resources`, `nanoclaw-core`, `nanoclaw-runtime`, `nanoclaw-network`, `nanoclaw-messaging` |
| `meme-maker-1.0.0` | `ontology/skills/meme-maker.trig` | Local/hosted meme rendering and catalog refresh | `nanoclaw-runtime`, `nanoclaw-network` |
| `sherpa-onnx-tts-1.0.0` | `ontology/skills/sherpa-onnx-tts.trig` | Download/configure/run local TTS and produce audio | `nanoclaw-core`, `nanoclaw-runtime`, `nanoclaw-network`, `nanoclaw-control` |
| `skill-creator-1.0.0` | `ontology/skills/skill-creator.trig` | Inspect, stage, edit, and validate skill files | `nanoclaw-runtime` |
| `taskflow-inbox-triage` | `ontology/skills/taskflow-inbox-triage.trig` | Approximate durable task coordination, triage, and notification | `nanoclaw-core`, `nanoclaw-messaging`, `nanoclaw-control` |

## Static loading policy

The manifest currently lists thirteen shared ontologies (including `claude-tools`, `git-resources`, `container-tooling`, and `vercel`), the GitHub environment and MCP graphs, and eight skill ontologies: `discord`, `gh-issues`, and the six container skills in NanoClaw's `container/skills/` (`agent-browser`, `frontend-engineer`, `onecli-gateway`, `self-customize`, `vercel-cli`, `welcome`). Five shared ontologies describe NanoClaw itself: core, messaging/mailboxes, management controls, container/filesystem runtime, and network/OneCLI paths. Generic tool, filesystem, internet, and shell graphs are also selected. Git resource definitions live under `ontology/skills/` even though they are selected on every run; folder placement and load policy are separate concerns.

Dependencies never load dynamically. Every ontology module owns its metadata in RDF:

```turtle
ar:NanoClawMessagingModule a ar:OntologyModule ;
  ar:ontologyModuleId "nanoclaw-messaging" ;
  ar:requiresOntologyModule "tool-endpoints", "local-filesystem", "nanoclaw-core" .
```

Each skill likewise owns its directory name, source provenance, and dependencies on its `ar:Skill` resource:

```turtle
ar:skill_coding_agent a ar:Skill ;
  ar:skillDirectory "coding-agent" ;
  ar:sourcePath "/path/to/top_skills/coding-agent/SKILL.md" ;
  ar:sourceSha256 "..." ;
  ar:requiresOntologyModule "git-resources", "nanoclaw-runtime", "nanoclaw-messaging" ;
  ...
```

The loader discovers module graphs in `ontology/universal/*.trig`, `ontology/nanoclaw/*.trig`, `ontology/claude/*.trig`, and `ontology/skills/*.trig`, and discovers skill graphs in `ontology/skills/*.trig` and MCP server graphs (catalogued like skills) in `ontology/mcp/*.trig`. Each file contains one named graph. Fixed infrastructure files remain at `ontology/universal/vocabulary.ttl` and `ontology/universal/shapes.ttl`; this avoids duplicating their paths in a catalog.

During dependency preflight, the loader parses graph metadata, checks that selected ontology IDs exist, and requires every dependency to already be selected in `manifest.json`. It does not assemble the combined graph until this preflight succeeds. As selected graphs are added, it verifies module source revisions and skill-source hashes. A missing or unknown dependency produces a warning and aborts; dependencies are never introduced automatically. Individual named graphs remain in the catalog; no redundant dataset copy is constructed.

`manifest.json` is the only ontology-selection configuration file and is a literal JSON array. The loader resolves each ID against RDF metadata; no type label is needed in the list. To change the loaded ontologies, edit this list and run `python loader.py`. Use `--max-pairs N` to limit LLM annotation and `--model MODEL` to select the model. API credentials may be supplied by environment variables or a local `.env` file.

```json
[
  "tool-endpoints",
  "local-filesystem",
  "freeform-internet",
  "shell-execution",
  "nanoclaw-core",
  "nanoclaw-messaging",
  "nanoclaw-control",
  "nanoclaw-runtime",
  "nanoclaw-network",
  "discord",
  "gh-issues",
  "git-resources",
  "claude-tools",
  "container-tooling",
  "vercel",
  "agent-browser",
  "frontend-engineer",
  "vercel-cli",
  "onecli-gateway",
  "self-customize",
  "welcome",
  "github-environment",
  "mcp-github"
]
```

Every always-loaded graph is checked to contain no `ar:Operation`, `ar:PotentialEffect`, `ar:declaresOperation`, or `ar:hasPotentialEffect`. The NanoClaw runtime, network, messaging, and control graphs and `claude-tools` may declare `ar:NativeCapability` facts instead; these describe conditional endpoint affordances, not skill actions or observed calls. `local-filesystem.trig` and `freeform-internet.trig` are additionally marked `resourcesOnly` and cannot declare invocation kinds.

### Loader sequence

For an agent modifying this project, the order matters:

1. Read `manifest.json` as a JSON array of unique ontology IDs.
2. Discover every `*.trig` file under `ontology/universal/`, `ontology/nanoclaw/`, `ontology/claude/`, `ontology/skills/`, and `ontology/mcp/`. Each must contain exactly one non-empty named graph.
3. In one pass, identify each graph as a module, skill, or MCP server from its RDF metadata and reject duplicate IDs.
4. Resolve the manifest IDs and check every selected graph's declared dependencies. Missing dependencies warn and abort before any active graph is loaded.
5. Load the vocabulary and selected graphs in manifest order, checking module source pins and skill-source hashes as they are added.
6. Check typed relationship endpoints, mount sides and bind sources (`locatedOn`/`executesOn` name a system component; `bindsFromKind` only on mounts), provider tool names and refinements, subtype cycles, endpoint parentage, shared-graph restrictions, and equivalent-class shape coverage.
7. Run SHACL over the assembled graph.
8. Print loaded skill operations, potential effect/resource rows, and modeled potential Internet calls.
9. Combine skill-declared effects with native endpoint capability effects, form unordered pairs of distinct affected resource kinds, apply `max_pairs`, and ask an OpenAI model about joint consequences. `max_pairs=0` skips annotation.

This sequence prevents a dependency declaration from silently widening the environment model. It also separates asserted named-graph data from the temporary union graph used for validation and reporting.

### Selection

`manifest.json` is the complete selection. To run only shared environment ontologies, remove the skill IDs from the list and run `python loader.py`. To add a skill, add its `ar:skillDirectory` value and every declared dependency it needs. Missing dependencies produce a warning and abort; none are added automatically.

## Resource and interface semantics

Resource kinds are RDF individuals typed `ar:WorldSurfaceKind`; invocation kinds are individuals typed `ar:InvocationSurfaceKind`. They are also usable as RDFS/OWL classes through OWL 2 punning.

- `ar:specializesKind`: resource **is-a** relationship.
- `ar:specializesInvocationKind`: interface/invocation **is-a** relationship.
- `ar:containsKind`: possible part or containment relationship, not subtype.
- `ar:mayBeStoredAsKind`: possible physical representation of logical state.
- `ar:sharesKind`: possible shared state scope, not containment or universal visibility.
- `ar:routesToKind`: possible mediation/dataflow step from an invocation kind.
- `ar:protectedByKind`: a security control relevant to a surface; it does not prove that the control is enabled or effective.
- `ar:subjectToControlKind`: controls relevant to some paths of a skill, not a conjunctive permission check on every operation.
- `ar:mayResideInKind`: possible location of a file/resource kind, not a claim about every concrete file.
- `ar:mountExposesKind`, `ar:mountAccessMode`, and `ar:mountConditionalOn`: what one mount can expose, its mode, and any unresolved condition.

Multiple parents are allowed. The loader validates specialization edges and rejects hierarchy cycles, but keeps the default report focused on operations and effects. An external OWL reasoner can project these edges into `rdfs:subClassOf` when class-level reasoning is needed.

`ar:ToolEndpoint` is the common callable parent. Shell execution, messaging endpoints, MCP tool endpoints, NanoClaw built-in MCP tools, provider file/web tools, and both forms of `ncl` are represented beneath it. A mediation component such as a channel adapter or mailbox write is an invocation surface but is not automatically an agent-facing tool endpoint.

## NanoClaw core scopes

The central distinction is:

```text
NanoClaw host process
  ├─ central database: users, roles, groups, wirings, sessions, config
  └─ orchestration: routing, guards, container lifecycle, delivery

Agent group
  ├─ persistent identity and configuration
  ├─ shared workspace / standing instructions
  └─ shared long-term memory

Session
  ├─ distinct conversation and provider continuation
  ├─ distinct inbound/outbound mailbox pair
  └─ per-session container lifecycle
```

Several sessions in the same agent group can share group working files and memory. Session separation therefore does not by itself establish confidentiality. A scheduled task runs in its own per-series task session, not in the session that created it.

A provider subagent (for example Claude's Task tool) is not a NanoClaw agent group: it runs in the same container with the same files, tools, network route, and gateway identity, so it adds no isolation. A separately isolated agent requires `create_agent`.

## Messaging and mailbox routes

Inbound and outbound paths are modeled as separate chains:

```text
Inbound platform event
  → NanoClaw channel-adapter inbound
  → host router and access/command gates
  → inbound mailbox write
  → agent-runner mailbox read
  → configured provider

Built-in send/edit/reaction tool
  → outbound mailbox write
  → host delivery and authorization
  → channel-adapter delivery
  → external messaging platform
```

`NanoClawSessionMailbox` is storage-neutral. The inspected checkout registers SQLite and uses the familiar `inbound.db` / `outbound.db` split, but the ontology does not define the abstraction as SQLite-only.

The split records the intended write ownership, which is a convention rather than a mount-level boundary:

- host writes the inbound side and the container reads it. The runner opens it read-only, but the file sits on the read-write `/workspace` mount, so any container process can alter it (`NativeShellInboundMailboxMutation`);
- container writes the outbound side and the host reads it. Any container process can insert rows, not only the built-in tools (`NativeShellOutboundMailboxWrite`), and the host also writes there in two cases: a direct reply when the command gate denies a command, and cleanup of orphaned processing claims;
- host delivery records and container processing acknowledgements remain on their owning sides.

Destinations are split in two. `NanoClawDestinationGrant` is the central record that host delivery authorizes non-origin sends against. `NanoClawProjectedDestination` is the session's copy, used only to resolve a name; editing it grants nothing. The session's origin chat is always permitted.

`send_message`, `send_file`, `edit_message`, `add_reaction`, `ask_user_question`, and `send_card` are separate endpoint kinds. NanoClaw does not expose one universal message action with read, search, delete, poll, pin, presence, and channel-management operations.

## Management paths and authorization

Every container `ncl` read verb is open (no approval) and returns host state into the container. These reads are modeled as native capabilities on host-located resources: `NativeNclGroupStateRead`, `NativeNclSessionHistoryRead` (any session transcript of the agent's own group, including other chats), and `NativeNclTaskRead` under `cond_ncl_available`; `NativeNclRouteConfigurationRead`, `NativeNclRoleMembershipRead`, `NativeNclDroppedMessageRead`, and `NativeNclApprovalRead` under `cond_cli_scope_global`.

Container and host invocations of `ncl` are distinct:

```text
Container: ncl → shell → outbound cli_request → host guard → action
Host:      ncl → shell → Unix socket → host dispatch → action
```

The container path is subject to `cli_scope` and action-specific guards. The host socket path is treated as trusted operator access by the current implementation. The ontology distinguishes scope eligibility from action decisions: `global` scope does not mean that every action bypasses approval. Under the default `group` scope, container `ncl` reaches its own group's `groups`, `sessions`, `destinations`, `members`, and `tasks`; task commands need no approval, while writes to the others are held for approval (`NanoClawContainerTasksCLI`, `NanoClawContainerGroupAdminCLI`). `global` scope adds the remaining resources, with every write held (`NanoClawContainerGlobalAdminCLI`).

Built-in `create_agent`, `install_packages`, and `add_mcp_server` tools emit structured host actions through the outbound mailbox. `install_packages` and `add_mcp_server` are always held for admin approval; `create_agent` is held unless the requesting group has `cli_scope=global`. Adding an MCP server and later calling its tools are different events. Approval of installation is not a general runtime policy for each third-party MCP call.

Inside the container, the Claude provider runs with `bypassPermissions`: every allowed tool call is auto-approved (`NanoClawProviderToolPolicyControl`). Per-call approval does not exist; privileged effects are gated host-side instead.

The modeled controls include user/group admission, inbound command gate, CLI scope, host ALLOW/HOLD/DENY guard, human approval, destination ACLs, mount allowlisting, container isolation, optional resource limits, gateway policy, and optional egress lockdown. Host `ncl` access is separately associated with the operator's Unix-socket boundary; the inspected CLI guard treats host callers as trusted rather than applying the container `cli_scope` rule. A `protectedByKind` edge is a candidate enforcement relationship, not runtime evidence.

## Filesystem and shell boundaries

`NanoClawContainerShellExecution` is a subtype of generic shell execution and runs inside the session container. It is not arbitrary host-shell access. The model separates:

- session workspace (`/workspace`, RW);
- agent-group workspace (`/workspace/agent`, RW but with nested RO configuration files);
- composed standing instructions and `container.json` (RO nested mounts);
- runner source and shared skills (RO);
- operator-configured additional mounts under `/workspace/extra`.

A write to a container path backed by a read-write host mount may change the underlying host directory. Additional mounts are modeled as protected by the host-side allowlist, but the actual path, realpath result, blocked patterns, and mode must be supplied by runtime observation.

### Container side and host side

Every NanoClaw location and step is assigned to the component it belongs to:

- `ar:locatedOn` on resource kinds and `ar:executesOn` on invocation kinds name `ar:NanoClawAgentContainer`, `ar:NanoClawHostProcess`, `ar:OneCLIGateway`, or `ar:ExternalNetwork` (everything beyond the local machine). Both are inherited along the specialization hierarchy unless a narrower kind states its own.
- Remote kinds (`Internet`, `RemoteNetworkEndpoint`, `RemoteWebResource`, `RemoteService` and so `RemoteMCPService`, `MessagingPlatform` and its groups, threads, and messages) are on `ar:ExternalNetwork`. Exceptions: `NanoClawHostReachableService` is on the host, and local channels (CLI, Emacs) deliver on the host; a snapshot resolves that per channel. Provider web search executes on `ar:ExternalNetwork` (at the model provider), while web fetch, shell requests, and MCP clients start in the container and cross the gateway.
- Container-side location kinds (`NanoClawSessionWorkspace`, `NanoClawAgentGroupWorkspace`, `NanoClawProviderStateDirectory`, ...) are **views at container paths**. The host directory behind each is a separate host-side kind (`NanoClawHostSessionDirectory`, `NanoClawHostGroupDirectory`, `NanoClawHostProviderStateDirectory`, ...) with an `ar:hostPathPattern` relative to the checkout.
- Each mount kind states its `ar:containerPath` (a trailing `*` marks a prefix) and its host source with `ar:bindsFromKind`. A write through the container view changes that host location; the host side can also have writers the container never sees (inbound rows, attachments, task-log appends, files recomposed at spawn).
- The container's writable layer and its processes (`NanoClawContainerProcess`, which includes local MCP servers) have no host counterpart. There is no agent-reachable host shell: host-side effects happen only through fixed host steps such as `NanoClawHostDeliveryInvocation`, `NanoClawHostCommandDispatch`, `NanoClawHostTaskLogAppend`, `NanoClawHostImageBuild`, `NanoClawHostConfigMaterialization`, and `NanoClawHostAttachmentStaging`.
- Container entry points route directly to the host step they cause (for example `NanoClawInstallPackagesTool → NanoClawHostImageBuild`). Host delivery is a hub every outbound row passes through, so tools are not routed to the whole fan-out behind it.

The loader checks that sides name a system component, that `bindsFromKind` appears only on mounts, and (via SHACL) that every mount has one container path and one host source on the host side.

### Permission and mount mapping

The ontology names individual mount kinds and their **mount-layer** access modes:

| NanoClaw location | Mount mode | Meaning |
|---|---|---|
| `/workspace` | RW | Session directory: both mailbox files, heartbeat, `outbox/`, and `inbox/` attachments |
| `/app/.nanoclaw-session.json` | RO | Host-written session identity file |
| `/workspace/agent` | RW | Group working files, persistent memory, conversation archives, task run logs, and `plugin-data/`, shared by the group's sessions |
| `/workspace/agent/container.json`, `CLAUDE.md`, and `plugins/` | RO | Nested mounts that override the writable parent at those paths; some depend on materialization/provider choice |
| `/app/src` and `/app/skills` | RO | Shared runner and installed skill content; the skills mount requires the directory to exist |
| `/home/node/.claude` | RW in the default Claude configuration | Group-scoped provider state, including settings loaded by later sessions of the group; other providers may contribute different mounts |
| `/workspace/extra/<name>` | RO or RW, conditional | Operator-selected host directory; no allowlist means no additional mount. RW requires both `readonly: false` in group config and `allowReadWrite: true` on the matched root |
| OneCLI CA and credential-stub paths | RO, conditional | Gateway-contributed files; credential stubs are not the underlying secrets |

The container's private writable layer is a possible local-file location but **not** a host bind mount. `ar:mayResideInKind` links file kinds in each skill to plausible locations; `ar:mountExposesKind` then links locations to mount kinds. For example, a coding worktree may be in the group workspace, an extra host mount, or a container-private layer. This is a set of alternatives, not one simultaneous permission or a claim that an arbitrary host repository is visible. A diagram template may be in read-only shared skills, while an output artifact needs a writable target. File-writing operations now explicitly require `ar:cond_container_file_writable`; file-reading operations that depend on a concrete path require `ar:cond_container_file_readable`.

Host-mediated operations are different: a container `ncl` request for a scheduled task is checked by CLI scope and host action guards, then the host may write a task log. The container's mount mode is not the host's write authorization for that action.

An admitted extra mount is read-only unless both RW conditions hold. The allowlist checks the **mount root** realpath against roots and blocked patterns; it does not inspect every descendant. Host OS file modes/ACLs, container identity, symlinks, nested mounts, provider tool policy, and the concrete path can restrict access further. The host project root is not automatically mounted. Loaded skill instructions do not themselves confer file, network, messaging, or service permissions. Each skill has `ar:subjectToControlKind` edges for relevant control families; those edges do not mean every control is on every route or that authorization succeeded.

For a type-level *candidate* mount-mode query after loading the selected graphs:

```sparql
PREFIX ar: <urn:agent-risk:>
SELECT ?location ?mount ?mode WHERE {
  ar:LocalGitRepository ar:mayResideInKind+ ?location .
  ?mount ar:mountExposesKind ?location ; ar:mountAccessMode ?mode .
}
```

The query does not return container-private locations (which are not mounts), decide which alternative was configured, account for nested path overrides, or compute effective access. That requires a runtime inventory of mount specs, canonical paths, process identity, OS permissions, tool policy, and remote-service authorization. SHACL validates the declarations' structure and RO/RW consistency; it does not evaluate those runtime facts.

## Claude built-in tools

`ontology/claude/claude-tools.trig` models the Claude Agent SDK tools a NanoClaw container can expose:

- **Every tool is a kind** with its exact SDK name in `ar:providerToolName`, specializing the provider-neutral NanoClaw endpoint it implements: `Bash` → `NanoClawContainerShellExecution`; `Read`, `Write`, `Edit`, `Glob`, `Grep`, `NotebookEdit` → `NanoClawProviderFileTool`; `WebFetch` → `NanoClawProviderWebFetchTool`; `WebSearch` → `NanoClawProviderWebSearchTool` (executes on `ar:ExternalNetwork`); `Task` → `NanoClawProviderSubagentTool`; `TaskOutput`/`TaskStop` → `ProcessControlTool`; `TeamCreate`/`TeamDelete` → `SubagentSpawnTool`; `TodoWrite`, `ToolSearch`, `Skill` → `ToolEndpoint`. The twelve tools NanoClaw disables (`CronCreate`, `ScheduleWakeup`, `AskUserQuestion`, `SendMessage`, plan/worktree tools, ...) are modeled too, so re-enabling one is visible.
- **Enablement is a requirement.** Each tool has `ar:cond_claude_<Name>` with `ar:requiresProviderTool "<Name>"`. The generic NanoClaw requirements (`cond_container_shell_available`, `cond_provider_file_tool_available`, `cond_provider_web_tool_available`, `cond_provider_subagent_tool_available`) carry `ar:satisfiedByEndpointKind`: they hold when at least one enabled tool specializes that endpoint. Disabling `Bash` therefore makes every generic shell capability unavailable.
- **Per-tool precision via refinements.** Where tools differ, Claude capabilities `ar:refinesCapability` a generic one: `Edit` and `NotebookEdit` update but cannot create or delete; `Write` creates or overwrites; `Glob` reads only paths (`LocalFileMetadata`); `Read`/`Grep` read contents; `WebFetch` both reads a page and can transmit data in the URL it chooses; `WebSearch` only reads results (its query is the only input). A runtime assessment reports the refinements in place of the generic capability.
- **Tools with no generic counterpart** get their own capabilities: `TaskOutput` (read background output), `TaskStop` (terminate a process), `TeamCreate`/`TeamDelete` (in-session teammates, `ClaudeAgentTeam`), `TodoWrite` (`ClaudeTodoList` under the provider state directory, group-scoped), `ToolSearch` (`ClaudeToolCatalog`, no world effect), `Skill` (`ClaudeSkillDefinition`).

The loader checks that tool names sit on invocation kinds, that every `requiresProviderTool` names a modeled tool, and that refinements link two native capabilities. `materialize.py` reads both lists from `container/agent-runner/src/providers/claude-config.ts` in the checkout (`src/materialize/provider_tools.py`), so editing them changes the next snapshot.

## Network, OneCLI, and MCP

The ontology deliberately separates proxy configuration from forced egress:

```text
Proxy-aware request → OneCLI proxy/gateway → remote endpoint
Direct request      ───────────────────────→ remote endpoint
```

With egress lockdown disabled—the default—the direct path may exist. Effective lockdown places the agent on a Docker internal network and blocks the direct path; it does not transparently convert arbitrary sockets into proxy requests. Gateway policy applies only to requests reaching the gateway.

Gateway policy (`OneCLIGatewayPolicy`, each policy as `OneCLIPolicyRule`) follows the local OneCLI 2.14 build (branch `local-2.14.0`), where Cedar is the gateway's only decision engine. Each policy is one Cedar statement (`cedar_policy_rules`), switched on or off on its own; the action is exactly the HTTP method (`OneCLI::Action::"GET"`) and the resource the host (`OneCLI::Host::"<host>"`). A request passes only when an enabled `permit` matches and no enabled `forbid` does: deny-by-default for every request, credentialed or not, LLM hosts included, and requests without an agent identity are refused. OneCLI's own rules (`policy_rules_v2`) only grant credentials: they decide which stored secrets the gateway injects for which agents and never allow a request. With lockdown on, the enabled permit policies are a complete egress allowlist.

OneCLI gateway processing is modeled separately from the credential vault, per-agent identity, credential stubs, and CA material. The gateway identity is per agent group, so gateway credentials and policy cannot distinguish a group's sessions or subagents.

Provider web tools are split by where they run: `NanoClawProviderWebFetchTool` fetches from inside the container (gateway or direct route), while `NanoClawProviderWebSearchTool` runs on the model provider's side: the query leaves inside the model request (through the gateway), but the searches and site contacts happen at the provider, so local egress controls cannot restrict them. With lockdown off, the direct route can also reach services on the Docker host (`NanoClawHostReachableService`). For intercepted HTTPS, the gateway can access decrypted HTTP contents. This does not establish which contents are logged or how long they are retained.

Third-party MCP has two important paths:

```text
Local stdio MCP:
provider ↔ stdio transport ↔ local MCP process
                              └─ optional, separate network request

Remote HTTP MCP:
provider → HTTP transport → remote MCP service
                              └─ its downstream API calls occur remotely
```

Local stdio is not Internet traffic. A local server's later HTTP call can use OneCLI or attempt direct egress. For remote MCP, local controls cover the client-to-MCP leg; the remote service's later calls do not automatically traverse the local gateway or mailbox.

## Skill adaptation policy

The former compatibility vocabulary has been removed. Skill graphs now name NanoClaw endpoints directly. This is a **translation of the ontology models**, not a claim that the original skill text can execute unchanged. The source `SKILL.md` hashes remain pinned as provenance and drift checks.

An operation translated beyond what its source states uses `ar:adaptationStatus ar:InferredForNanoClaw`. Its original `ar:sourceStatus` remains separate, so script-inspected and example-only evidence is not erased by the translation. The loader report shows it in each operation's Adaptation row.

The detailed crosswalk is RDF inside each skill graph. Every skill links to named `ar:AdaptationMapping` resources with `ar:sourceAssumption`, `ar:nanoclawModel`, one or more `ar:adaptationDisposition` values, and `ar:semanticDifference`. When appropriate, `ar:adaptedOperation` links the crosswalk entry to the modeled operation. All skills also link to `ar:NanoClawSkillAdaptationPolicy`, which holds the shared interpretation rule and pinned NanoClaw revision.

Important conservative choices include:

- Discord retains only native send, file, edit, and reaction actions. Read/search/delete/poll/pin/thread/presence are omitted rather than invented.
- channel configuration uses host-side `ncl` resource management plus a separately registered adapter; it does not assume a generic credential/config command.
- coding workers execute inside the session container and need an explicitly visible repository mount. Background process control remains provider-dependent.
- GitHub orchestration maps ephemeral sub-agents to NanoClaw's long-lived `create_agent` plus named-destination messaging.
- TaskFlow is approximated with scheduled tasks, run logs, mailbox wakeups, and companion agents. It is not an exact suspend/wait/resume implementation.
- `self-customize` names a `send_to_agent` tool that does not exist; delegation is modeled as `send_message` to the builder's agent destination (approximate). Its builder editing the runner source is modeled as unsupported, since `/app/src` is a read-only mount and the project root is not mounted.
- `frontend-engineer`'s Vercel deploy is conditional on the `vercel` CLI, which only `/add-vercel` installs; `welcome`'s channel addenda are conditional on a channel skill having added them.
- Each `ar:PotentialInternetCall` belongs to exactly one operation, so operations that reach the same endpoint kind (for example, browser page loads, form submissions, and uploads) each have their own call.

`Messaging-route` detects operations beneath the generic messaging invocation family. `NanoClaw-mailbox` is stricter and is `yes` only when the declared route reaches a NanoClaw mailbox read or write stage.

## Extending the model

### Add a new universal or support module

1. Add one `.trig` file under `ontology/universal/` or `ontology/skills/`.
2. Put exactly one non-empty named graph in that file.
3. Declare exactly one `ar:OntologyModule` resource with a unique string ID.
4. Declare every module dependency with `ar:requiresOntologyModule`.
5. If the graph must contain resources only, set `ar:resourcesOnly true`.
6. If it models a particular source checkout, add both `ar:sourceRepositoryPath` and `ar:sourceContentHash` (the hash of the checkout's files outside `ontology/` when you reviewed it; `src/common/source.py`, and the loader prints the current value when it is stale).
7. Add the ontology ID to `manifest.json` only when it should be selected explicitly.
8. Run the validation commands below.

Minimal example:

```turtle
@prefix ar: <urn:agent-risk:> .

ar:g_example_resources {
  ar:ExampleResourcesModule a ar:OntologyModule ;
    ar:ontologyModuleId "example-resources" ;
    ar:requiresOntologyModule "local-filesystem" ;
    ar:resourcesOnly true .

  ar:ExampleArtifact a ar:WorldSurfaceKind ;
    ar:specializesKind ar:LocalFile .
}
```

Do not put skill operations or `ar:PotentialEffect` nodes in an always-loaded module. Always-loaded graphs describe the environment available for composition; selected skill graphs introduce claims about actions. Native endpoint affordances may be declared as `ar:NativeCapability` with endpoint, effect type, resource kind, scope, requirements, and source note, without pretending that a skill invoked them.

### Add an MCP server ontology

MCP server graphs live under `ontology/mcp/`, next to `ontology/skills/`. Each is generated, not hand-written, from a pinned snapshot of the server in `ontology/mcp/sources/<id>.json`: the server's own `tools/list` (tool names, descriptions, annotations), each tool's toolsets, the toolset descriptions from the server's documentation, the transport, and the endpoint pattern. `ontology/mcp/github.trig` (the GitHub remote MCP server, 102 tools in 24 documented toolsets) is the first.

1. Fetch the snapshot from the live server (`tools/list` per toolset, e.g. through the OneCLI gateway as the agent) and the docs' toolset descriptions; write `ontology/mcp/sources/<id>.json`.
2. Run `python3 generate_mcp_ontology.py <id>`. It writes one `ar:MCPServer` (`ar:mcpServerId "mcp-<id>"`, `ar:mcpEndpointPattern`, `ar:sourcePath` / `ar:sourceSha256` pinning the snapshot), a tool endpoint kind specializing NanoClaw's remote-HTTP (or stdio) MCP tool, one resource kind per documented toolset (`<Prefix><Toolset>Data`, from the docs' description) plus one for tools no toolset lists, a credential requirement for the endpoint host (`ar:requiresCredentialForHost`), and one `ar:Operation` per tool with `ar:mcpToolName`, its toolsets, and a `ar:PotentialInternetCall` to the endpoint.
3. Effects land on the entities of the service's environment module when an entity mapping exists. For GitHub, the environment is `ontology/mcp/github-environment.trig` (module `github-environment`): users, organizations, teams, repositories, branches, commits, files, tags, releases, issues, comments, reactions, labels, milestones, pull requests, reviews, review comments, check runs, workflows, runs, jobs, artifacts, alerts, advisories, rulesets, custom properties, discussions, gists, notifications, stars, projects, Copilot tasks and spaces. They are linked by containment (`ar:containsKind`: a repository contains its issues; deleting it removes them) and by references (`ar:referencesKind`, with `ar:authoredByKind` and `ar:assignedToKind`: an issue's author, assignees, labels, milestone, related issues). `ontology/mcp/sources/github-entities.json` maps each tool, and each value of a tool's `method` argument, to the effects it has on those entities (e.g. `issue_write` method `create` → Create GitHubIssue, `update` → Update GitHubIssue; `merge_pull_request` → Update PullRequest, Create Commit, Update Branch), authored from the tool's description and input schema (`ar:MCPToolSchema`, the method in `ar:mcpToolMethod`); non-read-only tools also Transmit their arguments. A tool without a mapping falls back to its MCP `ToolAnnotations` under the specification's defaults (`ar:MCPToolAnnotation`) on a per-toolset kind: `readOnlyHint` true gives Read; otherwise Transmit and Create, plus Update and Delete unless `destructiveHint` is explicitly false. The graph pins the snapshot (`ar:sourceSha256`) and the mapping (`ar:entityMappingSha256`) separately.
4. Add the environment module and `mcp-<id>` to `manifest.json`. The loader validates it like a skill graph and rejects it as stale when the snapshot changes without regenerating.

In a session snapshot, `materialize.py` loads the graph of each configured MCP server whose URL matches `ar:mcpEndpointPattern`; its tools appear in the effect tables as `mcp__<server>__<tool>`. A remote MCP tool is placed by the OneCLI gateway's own Cedar decision for the `tools/call` request it sends (`POST /v1/cedar/evaluate` on the local Cedar build): under the deciding permit or held for approval. A call the gateway denies (no enabled permit, or a forbid) is not shown, nor is a host no enabled permit covers: the tables list what can happen, so switching a policy off removes its rows. The server row itself is decided the same way for its `initialize` request. Materialize reads the OneCLI API key from `$ONECLI_API_KEY` or the NanoClaw checkout's `.env` only to authenticate that call; it is never printed or recorded.

### Add a new skill ontology

1. Read the entire source `SKILL.md` and any scripts or references needed to understand its actual interfaces.
2. Create one file under `ontology/skills/` with one named graph.
3. Declare exactly one `ar:Skill` resource.
4. Record the exact source directory name in `ar:skillDirectory`.
5. Record the absolute source file in `ar:sourcePath` and its SHA-256 in `ar:sourceSha256`.
6. Declare all required environment modules with `ar:requiresOntologyModule`.
7. Model resource kinds and invocation kinds separately.
8. Model each supported action as an `ar:Operation` with at least one invocation kind, potential effect, and evidence status.
9. Record requirements even when the loader cannot evaluate them.
10. Add explicit `ar:AdaptationMapping` entries for every non-trivial NanoClaw translation, approximation, condition, or omission.
11. Add the skill directory to `manifest.json` only if it should load by default.
12. Validate the new skill explicitly before changing defaults.

Minimal skeleton:

```turtle
@prefix ar: <urn:agent-risk:> .

ar:g_example_skill {
  ar:skill_example a ar:Skill ;
    ar:skillDirectory "example-skill" ;
    ar:sourcePath "/absolute/path/to/top_skills/example-skill/SKILL.md" ;
    ar:sourceSha256 "<sha256>" ;
    ar:requiresOntologyModule "nanoclaw-runtime" ;
    ar:governedByAdaptationPolicy ar:NanoClawSkillAdaptationPolicy ;
    ar:hasAdaptationMapping ar:adapt_example_action ;
    ar:declaresOperation ar:op_example_action .

  ar:ExampleOutput a ar:WorldSurfaceKind ;
    ar:specializesKind ar:LocalFile .

  ar:cond_example_writable a ar:Requirement .

  ar:op_example_action a ar:Operation ;
    ar:invokedThroughKind ar:NanoClawProviderFileTool ;
    ar:targetsKind ar:ExampleOutput ;
    ar:requires ar:cond_example_writable ;
    ar:sourceStatus ar:SkillText ;
    ar:adaptationStatus ar:InferredForNanoClaw ;
    ar:hasPotentialEffect [
      a ar:PotentialEffect ;
      ar:effectType ar:Create ;
      ar:affectsKind ar:ExampleOutput
    ] .

  ar:adapt_example_action a ar:AdaptationMapping ;
    ar:sourceAssumption "The source skill can write an output file." ;
    ar:nanoclawModel "NanoClawProviderFileTool in container-visible storage." ;
    ar:adaptationDisposition ar:ConditionalAdaptation ;
    ar:semanticDifference "The writable scope is limited to container-visible storage." ;
    ar:adaptedOperation ar:op_example_action .
}
```

Compute the source hash on macOS with:

```bash
shasum -a 256 /absolute/path/to/SKILL.md
```

When the source skill changes, do not merely update the hash. Re-review the skill, revise its ontology and adaptation mappings, and only then update `ar:sourceSha256`.

### Add or change a subtype definition

Use `ar:specializesKind` or `ar:specializesInvocationKind` for ordinary necessary-only taxonomy edges. Use `owl:equivalentClass` only when the RDF facts are genuinely sufficient to recognize membership.

Every modeled kind with `owl:equivalentClass` must also be targeted by a SHACL shape. The loader enforces this coverage rule. If recognition depends on an external check, represent the check result as observation data rather than pretending OWL performed it. `gitRepositoryValidated true` is the existing example.

### Modeling checklist

Before accepting a new graph, ask:

- Is this node a resource/state kind, an invocation path, an operation, or a concrete observation?
- Is the relationship subtype, containment, storage representation, sharing, mediation, or protection?
- Does the source actually assert the action, or is it a NanoClaw inference?
- Is the effect a possibility, and have its requirements been recorded?
- Does a remote MCP service create a control boundary different from a local stdio server?
- Does a container path map to a read-write host mount, a read-only mount, or container-local state?
- Does a messaging action use a built-in endpoint, a projected destination, an adapter, or an added third-party tool?
- Would the graph accidentally imply broader host, network, credential, or message-history authority than the source supports?
- Are unsupported actions omitted or explicitly recorded as unsupported rather than silently invented?

## Qualified subtype definitions

Every specialization edge is a necessary-only definition. A kind becomes necessary-and-sufficient only with `owl:equivalentClass`; the loader then requires a corresponding SHACL target shape.

`ontology/skills/git-resources.trig` demonstrates the pattern without falsely defining every Git repository as a directory containing `.git`:

- `GitMetadataEntryCandidate`: exactly an entry named `.git`.
- `ConventionalGitWorkingTreeCandidate`: exactly a local directory containing such an entry.
- `ConventionalGitWorkingTree`: candidate plus explicit `gitRepositoryValidated true` evidence.
- `LocalGitRepository`: primitive broader kind because bare repositories, linked worktrees, and external Git directories need not contain a `.git` entry.

An external OWL-RL reasoner can infer positive membership for concrete observations. SHACL can then check the required evidence. `loader.py` validates the selected ontology graphs and the matching SHACL shapes; it does not load observation files or perform instance classification. Missing facts do not prove non-membership.

## Run and validate

The prototype has no packaging wrapper. Run it from this directory with Python 3 and install its dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Edit `manifest.json` to select the ontologies, then run (this annotates all pairs):

```bash
python3 loader.py
```

`loader.py` is a thin entry point; the execution flow is `src.loader.cli.main()`, which calls one function per stage (`read_manifest` / `discover_catalog` / `check_selection` → `assemble` → `run_checks` → `build_inventory` → `print_report` → `analyze_pairs`). Importing the package does not read ontologies, print reports, or call the LLM; call `main()` explicitly to run the workflow, or call the stages individually.

For example, add `coding-agent` to the manifest alongside its declared dependencies to include that skill. Remove `discord` or `gh-issues` from the list to exclude them. The files in `ontology/examples/` remain standalone observation fixtures for external RDF/OWL/SHACL experiments; this loader does not read them.

### Reading the report

The loader prints aligned, wrapping tables (fitted into the terminal width, or `--width N`; the widest columns shrink first, headers wrap too) for its summary, skill control context, operations, native capabilities, mount exposures, affected resources, and Internet calls. Each selected skill operation shows its invocation endpoint(s), targets, requirements, source/adaptation evidence, available source note, and typed resource effects. Native endpoint capabilities from the loaded NanoClaw and Claude graphs (file-tool read/write, shell execution and file/network access, direct shell writes to mailbox, task-log, and provider-settings state, provider web fetch and search, subagent start, built-in message/file-send paths, open container `ncl` reads of host state, and per-tool Claude capabilities and refinements) are **conditional possibilities**, not extra skill operations, confirmed tool exposure, or permission grants. Resource rows include definitions when recorded, subtype and direct containment context, plausible file locations/mounts, and relevant controls. Internet calls show initiator, endpoint, and conditions. No runtime mount inventory, effective ACL decision, or observed action is inferred.

Both **skill-declared effects and native endpoint capability effects** are passed to the LLM pair annotator. This permits cross-surface assessments such as native file reading combined with a skill's message creation, and it can increase the number and cost of pair assessments. The annotator prints its pair selection, per-pair assessments, effect types, and summary in tables; the saved JSONL format is unchanged. It counts unordered pairs of distinct resource kinds across both sources. Repeated touches to one kind are consolidated into a single set of effect types. Skill, operation, and native-capability provenance remains in the local report but is excluded from the annotation input.

### Optional joint-consequence analysis

`llm_annotation.py` owns the resource pairing, prompt, response schema, API calls, and annotation output. `loader.py` builds a list of resource descriptions and effect types, then calls `analyze_pairs(resources, model=..., max_pairs=...)`. The annotation module never receives the ontology graph, selected skills, operation identifiers, source paths, adaptation mappings, or invocation routes. Continue running `loader.py` as the entry point; importing `llm_annotation.py` alone does not run an analysis.

Each resource description contains its RDF kind identifier, label, definition, subtype ancestors (`parents`), possible containing kinds (`contained_in`), and deduplicated effect types (`effects`, such as `Read`, `Update`, or `Transmit`). Definitions project `rdfs:label`, `rdfs:comment`, `skos:definition`, and `owl:equivalentClass`, including connected OWL expressions and definitions of referenced terms. They are serialized as sorted, canonical N-Triples (also valid Turtle), stabilizing blank-node identifiers for prompt caching across runs. They exclude graph/skill provenance and source metadata. Related kinds carry their own definitions. A missing definition is `null`; the loader does not invent prose. Parents follow explicit `ar:specializesKind` links; containing kinds follow inverse `ar:containsKind` links, transitively. Containment is not subtype, ownership, or proof of co-location. Resource/platform names remain meaningful (for example, `DiscordMessage`); this removes explicit skill provenance, not every possible inference about capability from a resource's name or definition.

To cap the number of annotated pairs, run `python3 loader.py --max-pairs 50`. Omit the flag to include every pair, or use `--max-pairs 0` to skip annotation. The limit must be a non-negative integer. Pairs are a uniform random sample of all candidate pairs, in random order (and with no fixed first/second item), so a limit does not favor any resource. The seed is printed and saved with each result; `--seed N` reproduces a selection for the same items. The catalog remains sorted for stable prompt caching when the selected resource set is unchanged. The report shows both the selected and total pair counts; unselected pairs remain unanalyzed. The underlying `analyze_pairs(resources, model="gpt-6-luna", max_pairs=50)` function remains available for programmatic use.

Put `OPENAI_API_KEY=...` in this directory's `.env` file or your process environment. `.env` is git-ignored; do not commit or print it. The default model is `gpt-6-luna`; use `--model MODEL` to override it, or pass `model=...` to `analyze_pairs`. `OPENAI_MODEL` is no longer used. A supplied model must support the Responses API and structured outputs and be available to your API account. Running the loader directly makes paid API requests for the selected pairs, without an environment toggle or confirmation prompt. Use `--max-pairs 0` to skip annotation. Missing API credentials are reported by the OpenAI SDK.

```bash
python3 loader.py --model gpt-6-luna --max-pairs 50
```

The loader makes one OpenAI Responses API call per resource pair, returning one assessment object per call. Calls run on `--workers` threads (default 20); the first runs alone so it writes the prompt cache the others then read. Results are saved and printed from the main thread in completion order, and a failed call is saved with its error and counted ("Failed calls") instead of stopping the run. A shared resource catalog contains definitions, subtype/containment context, and effect types from skill operations and native endpoint capabilities; each call adds only the two resource kind identifiers to that shared prefix. The simplified prompt asks for a consequence requiring **both** resources in one hypothetical agent session, grounded only in the catalog. `Read` means access, not mutation; skill identity, capability identity, and actual execution are not assumed. Each flagged result prints both resources' possible effect types, the consequence, why both matter, and necessary assumptions. The printed effects are the supplied possibilities, not observed actions or a claim that every effect occurs. Removing operation and capability provenance deliberately loses which effects belong to the same action, their prerequisites, and whether a native endpoint is actually exposed. The model's answer is a brainstorming aid, **not a sound inference or proof of an exploitable path**. The ontology is a may-effect model; it does not establish action order, argument values, concrete instances, permissions, runtime reachability, or that two effects actually occur in one session. Without a limit, cost scales approximately quadratically in the number of distinct affected kinds. A supplied `max_pairs` caps the pairs sent for annotation; omitted pairs are not assessed. For example, `--max-pairs 50` makes up to 50 assessment calls, depending on the available pairs.

### Saved assessments

Each annotation run creates `results/assessments-<UTC timestamp>.jsonl` and prints its absolute path. Each line is a JSON object with `model`, `first`, `second`, and `assessment`. The two resource objects include the definitions, hierarchy context, and possible effects supplied for that pair. The assessment contains `joint_risk`, `consequence`, `why_both`, and `assumptions`; negative assessments are saved too. Each completed assessment is appended and the file closed before continuing, preserving earlier results if a later call fails or the run is interrupted. A partial file contains only completed assessments, not evidence that every selected pair was analyzed. Runs use separate files rather than overwriting previous results. `--max-pairs 0` creates no result file. The `results/` directory is git-ignored because outputs may contain sensitive resource descriptions. These files save parsed assessments, not raw API responses, and are not used as an answer cache.

### Prompt caching

The instructions, output schema, and catalog are identical across assessments. Only resources appearing in selected pairs are included in the catalog. GPT-6 and GPT-5.6 requests use an explicit cache breakpoint after the catalog, with `prompt_cache_options={"mode": "explicit", "ttl": "30m"}`; the changing pair is not written to that prefix cache. A stable `prompt_cache_key` is supplied for all models; older models use their automatic prefix caching instead of the newer explicit controls. This is API prompt caching, not a local cache of generated answers. Each response's reported cached-input-token count is printed when usage is available. Cache hits are not guaranteed: minimum prefix lengths, expiry, model changes, and changes to the resource catalog can prevent reuse. The first cache write may cost more than ordinary input; reuse can reduce input-processing cost. See the [official prompt-caching guide](https://developers.openai.com/api/docs/guides/prompt-caching).

### Expected failures and what they mean

| Error | Meaning | Correct response |
|---|---|---|
| `Unknown ontologies in manifest.json` | A listed ID matches no discovered graph | Fix the selection or graph metadata |
| `--max-pairs must be non-negative` | A negative annotation limit was supplied | Use zero to skip, a positive limit, or omit the flag for all pairs |
| `Duplicate ontology ID` | Two graphs claim the same identity | Give each graph a unique ID |
| `Expected one named graph` | A TriG file violates the discovery convention | Keep one populated named graph per file |
| `is stale: source checkout changed` | The pinned NanoClaw checkout moved | Re-review the changed source before updating the revision |
| `source skill has changed` | `SKILL.md` no longer matches `ar:sourceSha256` | Re-review and update the skill graph, then its hash |
| `Dependency preflight failed` | A declared requirement is unknown or absent from the selected ontologies | Add/review the required ontology and explicitly update `manifest.json` |
| `Always-loaded graph contains operations` | Environment and selected-skill responsibilities were mixed | Move operations/effects into a skill graph |
| `Cycle in` | A specialization path loops back to itself | Correct the taxonomy; do not use subtype for containment/routes |
| `Equivalent kinds without SHACL shapes` | An OWL definition lacks its closed-world validator | Add a corresponding target shape |
| `SHACL validation failed` | Selected ontology graph structure violates a shape | Read the focus node, path, and source shape in the report |

An absent dependency is intentionally not repaired automatically. Treat the abort as a request for an explicit environment-policy decision.

## Instantiating the model for a live session

The universal graphs describe what *any* NanoClaw session might do. `materialize.py` turns them into a snapshot of *one* running session: it observes that session's actual configuration, records it as typed instances of the ontology's kinds, settles the model's requirements for it, and reports what that session can still reach or change.

```bash
python3 materialize.py                      # most recently active session with a running container
python3 materialize.py --session <id>       # a specific session (its container must be running)
python3 materialize.py --no-write           # print the report without saving a snapshot
python3 materialize.py --nanoclaw <path>    # checkout to inspect (default: nanoclaw-core's sourceRepositoryPath)
python3 materialize.py --out <dir>          # snapshot directory (default: observations/)
python3 materialize.py --no-discovery       # skip the live, read-only GitHub/Vercel instance discovery
python3 materialize.py --width 140          # fit tables into 140 characters (default: terminal width; 160 when redirected)
python3 materialize.py --max-pairs 50       # annotate up to 50 host/downstream resource pairs with the LLM (default: all; 0: skip)
python3 materialize.py --cross-only         # annotate only host–remote instance pairs
python3 materialize.py --seed 7             # reproduce a random pair sample (default: a fresh seed, printed)
python3 materialize.py --workers 20         # parallel LLM calls for the annotation (default: 20)
python3 materialize.py --model <model>      # OpenAI model for that annotation (default: gpt-6-luna)
```

It needs a running NanoClaw service, Docker, and the local Cedar build of OneCLI 2.14. It also requires that OneCLI is the only, deny-by-default way out: the `cedar_policy_rules` table present in OneCLI's database (the Cedar build), `GATEWAY_STRICT_DENY=true` on the `onecli-gateway` container, and egress lockdown on. If any of these is missing (or the policy cannot be read), it prints why and exits without a report, because web egress is reported per Cedar policy and the policies would not bound it. `ncl`, `docker`, and `git` are found on `PATH` or in `~/.local/bin`, where NanoClaw setup installs them. It changes nothing in NanoClaw: every read goes through read-only interfaces.

### How it works

1. **Load the model.** `vocabulary.ttl` plus every `ontology/universal/`, `ontology/nanoclaw/`, and `ontology/claude/` graph. If the checkout's source (every file outside `ontology/`) differs from what `nanoclaw-core` pins (`sourceContentHash`), it warns that the results may be stale. Skill graphs are only consulted to match installed skills by `skillDirectory`. Each skill in `container/skills/` without a graph prints `warning: skill <name> has no ontology` (its operations are then missing from every table), a matched graph whose `sourceSha256` no longer matches the `SKILL.md` prints `warning: skill <name> ontology is stale`, and each configured MCP server no `ar:mcpEndpointPattern` matches prints `warning: MCP server <name> has no ontology`. The session table repeats the unmodeled ones.
2. **Collect facts** (`collect.py`, through the read-only wrappers in `runtime.py`), names and settings only, never secret values, tokens, or environment values:

   | Source | Facts |
   |---|---|
   | host `ncl ... --json` | session, agent group, group config (`cli_scope`, provider, MCP servers, packages, extra mounts), other sessions in the group, messaging groups, wirings, destination grants, roles, members |
   | session `inbound.db` (opened read-only) | projected destinations |
   | `docker inspect` / `docker network inspect` | mounts with RO/RW mode, image, user, hardening flags, resource limits, networks and whether they are internal |
   | `docker exec ... command -v curl` | whether a network client exists in the container |
   | OneCLI's Postgres (`docker exec onecli-postgres-1 psql`, one read-only query) | agents (id, name, identifier, workspace; never the access token), secret names, types, and host/path patterns (never values or 1Password references), published credential grants (`policy_rules_v2` allow rules with secret targets), and every Cedar policy with its code and on/off state. The 2.14 `onecli` CLI needs a user login and no longer lists rules, so it is not used |
   | `docker inspect onecli-gateway` | whether `GATEWAY_STRICT_DENY` is enabled (only that variable is interpreted) |
   | the checkout | installed `container/skills`, whether the agent-to-agent module is present, `TOOL_ALLOWLIST` / `SDK_DISALLOWED_TOOLS` from `container/agent-runner/src/providers/claude-config.ts`, `HEAD` |

3. **Instantiate** (`instantiate.py`, into the `Snapshot` from `snapshot.py`). Mounts are matched to mount kinds by the model's `ar:containerPath` (most specific match). Each mount yields a container-side location instance (`obs:onSide ar:NanoClawAgentContainer`) and a separate host-side instance of its `ar:bindsFromKind` kind (`obs:onSide ar:NanoClawHostProcess`, host path relative to the checkout), linked by `obs:bindsFrom` / `obs:backedBy`. Each fact becomes an instance typed with an existing kind (`a ar:NanoClawSession`, `a ar:NanoClawSessionWorkspaceMount`, `a ar:NanoClawDestinationGrant`, `a ar:NanoClawEgressLockdownControl`, ...) and linked with `obs:` properties (`obs:inAgentGroup`, `obs:mountedIn`, `obs:exposes`, `obs:accessMode`, ...). Every instance carries `obs:evidenceKind`: `obs:Observed` (read from the running container or gateway) or `obs:FromConfiguration` (read from configuration). Observed mounts are classified by container path, and each mount's location is typed with the kinds the model says it exposes. Mismatches are reported as model/runtime differences: a mount whose mode contradicts its kind, an unknown mount path, or a projected destination with no central grant.
4. **Resolve requirements** (`conditions.py`). Each `ar:Requirement` in the universal graphs gets an `obs:ConditionEvaluation` with status `obs:Satisfied`, `obs:Unsatisfied`, or `obs:Unknown` and its evidence as observed `key=value` facts (e.g. `extra_mounts=0`, `lockdown=on; cedar_permits=2`), never explanatory text. For example, `cond_additional_mount_admitted` is unsatisfied when no `/workspace/extra` mount exists, and `cond_default_claude_provider` is satisfied when the provider is Claude and `/home/node/.claude` is mounted. Tool requirements are decided from the tool lists: `cond_claude_<Name>` holds when that tool is enabled, and a generic requirement with `ar:satisfiedByEndpointKind` holds when some enabled tool implements that endpoint. `cond_network_route_permitted` is unsatisfied only when lockdown forces the gateway and no Cedar permit applies to the agent. Requirements may also declare their own check in the ontology: `ar:requiresContainerCommand` (satisfied when `command -v <name>` succeeds in the session container, e.g. `cond_vercel_cli_installed` → `vercel`, `cond_agent_browser_available` → `agent-browser`) and `ar:requiresCredentialForHost` (satisfied when a credential granted to the agent has a host pattern covering the host, e.g. `cond_vercel_credential` → `api.vercel.com`). Credentials are part of operations and calls, not world kinds: a call to a host carries the credential granted for that host (shown in its row as the injected credential), and an operation that needs one says so with `ar:requiresCredentialForHost`. What a credential reaches at its host is modeled only by that service's detailed ontology (GitHub, Vercel, or kinds the runtime adds from traffic); there is no generic "connected service data" kind. Raw HTTP from the container with an injected credential is the native shell network request (`NativeShellNetworkRequest`, Transmit to a remote endpoint); its rows name the credential for hosts that have one. Requirements that depend on a concrete path or a per-request decision (`cond_container_file_readable`, `cond_container_file_writable`, and `cond_network_route_permitted` in general) stay **unknown**, never assumed satisfied; any requirement without an evaluator is also unknown.
5. **Assess** (`capabilities.py`, `routes.py`, and the `modifications/` package: `inside.py`, `host.py`, `remote.py`):
   - every `ar:NativeCapability` becomes `obs:Available` (all requirements satisfied), `obs:Conditional` (some unknown), or `obs:Unavailable` (some unsatisfied), with the concrete instances it can reach. File-writing capabilities reach only read-write mounts and the writable container layer; reads reach every mount.
   - routes and management paths are assessed from observed facts: lockdown on (the container is on an internal network) blocks the direct route and host services; `cli_scope` decides which container `ncl` paths exist and whether `create_agent` needs approval.
   - what the session can read or change is reported in three tables. **Inside the container** (reads and changes): every location, writable or read-only, with the modeled resources residing there (minus those shadowed by a nested read-only mount), and capabilities aimed at specific resources (mailboxes, outbox, task logs, provider settings, todo list, skills, background output, processes, subagents), each with the host path it reads or writes through to (`-` for container-only) and the observed mount mode. Read capabilities reach every location; write capabilities only writable ones, so a read-only mount shows `Read` alone. **Outside the container** (reads and changes): host-side steps found by walking `routesToKind` from container-side tool endpoints (stopping at the delivery hub), with the entry points that trigger each; host-side reads from capabilities whose resource is located on the host (open `ncl` reads, including `ncl sessions history` over every session of the group, and, under global scope, users, roles, routing, dropped messages, and pending approvals), whose results return into the container. Each host step's resources are the world kinds it routes to; their locations come from the model (`hostPathPattern` filled with the session's identifiers, else the containing kind via `containsKind`, else the system component), and their effects from the capabilities on that resource. **Outside this machine**: capabilities whose resource is on `ar:ExternalNetwork`. Gateway-routed ones (web fetch, shell network requests) are listed once per enabled Cedar `permit` policy that applies to the agent, located at the policy's parsed `<methods> <host><path>`; a GET-only tool (WebFetch) is omitted from policies that permit only other methods. The path column is the model's open `routesToKind` path from the endpoint to the first kind on the resource's side (steps protected by `NanoClawEgressLockdownControl` are dropped when lockdown is observed on). Provider web search has no such path: its endpoint `executesOn` `ExternalNetwork`. Also listed: configured remote MCP servers, and messages delivered through a remote channel adapter to each channel destination grant. Model-provider API traffic is out of scope. In all three tables, rows for the same resource at the same location are merged into one: effects and via are unioned, and when the merged rows differed in status or gate, each gate is labeled with the effects (and status) it applies to.

   No column carries hand-written explanation. Controls are always the model's `ar:protectedByKind` of the kinds involved (inherited along `specializesKind` / `specializesInvocationKind`); gate cells for gateway routes are tokens: `cedar=<group>/<policy>`, `@approval`, `@rate_limit=<spec>`, `principal-scoped`, `conditional`, `forbid=<names>`, `credential=<secret names>`, `controls=<kinds>`. Some display facts are still code-held tables rather than ontology triples: `KNOWN_PATHS` and `SUBPATHS` (container subpaths of resources), endpoint display names, `GET_ONLY`, `LOCAL_CHANNELS`, and the rule that agent-to-agent routes need an agent destination grant.
6. **Check and write** (`output.py`). Every `rdf:type` in the snapshot must be a kind declared in the model (or an `obs:` assessment class), otherwise it aborts. The snapshot is written as one named graph, `urn:agent-risk:snapshot:<session>:<timestamp>`, to `observations/<session>-<timestamp>.trig`.

### Reading the report

The report prints the session header, the three effect tables, and model/runtime differences. Mounts, requirement evaluations, tool and capability assessments, routes, and Cedar policies are computed and recorded in the snapshot graph, not printed.

| Section | Meaning |
|---|---|
| Session snapshot | session, agent group, container, provider, `cli_scope`, lockdown, destination grants, container skills and those without an ontology, MCP servers and those without an ontology, gateway identity, granted credentials, gateway policy, Cedar policies in scope (permit / forbid), Claude tool-list counts |
| Inside the container / On the host, outside the container / Off this machine (remote) | three tables with the same columns. **Resource**: the modeled kind. **Location**: inside, the container path with its mount mode and, on the next line, the host path it binds from (a write there changes the host too); on the host, the model's host path: the kind's own `hostPathPattern`, else that of the host directory its mount binds from (plus subpath), else that of the nearest containing kind on the same side (e.g. destination grants → `data/v2.db`); `{image}` is the session container's image; remote, for a path through the OneCLI gateway, the Cedar policy entry that lets it through (`cedar: <group>/<policy>`; one row per applying permit), under the host when a granted credential or the operation names one (`cedar: none` when no permit covers that host); for channel delivery, the destination. Policies are never described in the tables: in the remote table each Cedar endpoint shows the entry's code verbatim under its name. Matching rows to permits still parses the policies internally. **Effects** (reads included): from native capabilities, skill operations, or, for a host step, its `ar:stepEffectType`. **Via**: the tools; for a host step, the step with each entry point on a `← ` line; for a remote route, each tool or operation label followed by its model path, one `→ step` per line (labels sharing a path are listed together over it); skill rows read `<skill>/<operation> (<tools>)`. **Status**: one per line when merged rows differ. **Gate**: one `key: value` fact per line (`forbid`, `credential`, `destination`, `mount`, `path`, `blocked` / `pending` requirements), then `controls:` with the model's `ar:protectedByKind` kinds indented; when merged rows have different gates, each is listed under a `[...]` line naming the effects (or the via step) it applies to. Rows spanning several lines are separated by a dotted rule. |
| Off this machine (remote): what can happen at each endpoint | one row per endpoint: the Cedar policy entry a gateway path passes (`cedar: <group>/<policy>`, with its Cedar code verbatim below the name), else the destination (`discord: …`), `*`, or `-`. **What can happen there** lists every resource reached through that entry, one `<effects> · <resource>` line each (with `@ <host>` when a granted credential or the operation names the host, and `[status]` when statuses differ), followed by `via` the tools and skill operations doing it. **Route** lists each model path once; paths sharing everything after their first step are shown as one tail under the comma-separated first steps. **Gate** is the union of the rows' facts and controls. The snapshot graph keeps one record per resource and location. |
| Downstream in remote environments | what the remote effects may set off further, from the environment graph only. It starts from every changing effect and every read of the remote table (reads propagate only through reactions the model declares for them, `ar:mayTrigger` with `ar:onEffectType ar:Read`; transmissions are not followed) and follows, up to four steps: **triggers** (`ar:mayTrigger` on a kind: when it undergoes `ar:onEffectType`, the environment itself causes `ar:effectType` on `ar:affectsKind`, e.g. a created commit or issue starts a workflow run (push or issues event), and an executed run may push commits, publish releases, comment, upload artifacts, report checks, and transmit to the Internet from GitHub's runners, outside the OneCLI gateway); **delete cascades** (deleting a kind deletes what it contains); then, as a final step, **dangling references** (a deleted kind leaves references in the kinds that refer to it). A kind is never counted as affected only because something it contains is (a new issue is not an effect on its repository). Each row is an influenced resource with its effects, the shortest path per effect from the tool and Cedar entry that starts it, the starters, and their status. |
| (skill operations) | not a separate table: every `ar:Operation` of an installed, modeled skill (its graph and the support modules it requires, e.g. `container-tooling`, are merged into the model for the run) adds rows to the three tables above, with `<skill>/<operation> (<tools>)` in the via column. Each potential effect is placed by the affected kind's side, inherited along `mayResideInKind` and `containsKind` when the kind has none: container rows use the observed mounts exposing the kind (a mount exposing the kind itself shadows parent mounts; mutating effects on a read-only mount appear as `Unavailable` with `mount=ro`), host rows the model location, remote rows the open route path, one per applying Cedar permit through the gateway. Status comes from the operation's `ar:requires`; requirements that are not satisfied appear in the gate as `blocked:` / `pending:` lines with their evidence. When the operation's internet call names its endpoint (`ar:callEndpointPattern`, placeholders excepted), its remote row is placed at that host and matched only against Cedar permits covering it; with none, the row is `Unavailable` with `cedar: none`. Skill rows are merged among themselves, never into native rows. An effect on a kind with no side is listed under model/runtime differences. |
| Model/runtime differences | observations the model does not account for |

`Available` means nothing observed rules the effect out; it is still a may-effect, not evidence that it happened or would succeed. `Conditional` means it depends on a concrete path or a per-request gateway or remote decision.

### Concrete instances

Every effect ends at instances, not kinds (`src/materialize/instances/`: `model.py` for `Instance`/`Instances`/`Target`, `registry.py` for environment sources, `envs/` with one module per environment, `resolve.py` for effects → targets). Discovery runs every applicable environment source once per run:

| Source | Kinds | How |
|---|---|---|
| NanoClaw records | `NanoClawAgentGroup`, `NanoClawDestinationGrant`, `MessagingGroup` (with recipients), `MessagingPlatform`, `NanoClawScheduledTask` | the collected `ncl` facts |
| GitHub MCP, as the agent | `GitHubUser`, `GitHubRepository`, `GitHubWorkflow` (with the events under its `on:`) | `get_me`, `search_repositories user:<login>`, `get_file_contents .github/workflows/` and each workflow file |
| Vercel REST, as the agent | `VercelAccount` (personal and teams), `VercelProject`, `VercelDomain`, `VercelDeployment` (recent 20), `VercelEnvironmentVariable` (names, targets, types; never values) | `GET /v2/user`, `/v2/teams`, `/v9/projects`, `/v9/projects/<id>/env`, `/v5/domains`, `/v6/deployments` |

#### Declared credential scopes

Some limits of a token cannot be observed. Reads overstate it (any token reads public repositories), GitHub has no API that lists a fine-grained token's selected repositories or permissions, and probing with writes is not acceptable. The owner declares them instead, in `local/credential-scopes.json` (git-ignored; it names private resources), and materialize trusts the declaration. The runtime asks for it (a DM card or the terminal, with choices or a plain-language description; see `docs/ontology-runtime.md` in NanoClaw) when the credential is first connected; to change it, e.g. after changing the token's permissions:

```bash
python3 ontology_runtime.py ask-scope GitHub     # asks again; answer in the DM, or: ontology_runtime.py answer <id>
```

A declaration is keyed by the OneCLI credential name and records the secret's id and `updated_at` (never its value); when the secret is later replaced or updated, materialize warns that the declaration may be stale. An optional `expiresAt` makes the declaration stale after that date. For GitHub, `repositories` are `owner/name` patterns, `user_permissions` the account-level ones (`"*"` by default, `{}` for none; e.g. `gists`, `starring`, `profile`, `administration` to create repositories; map `USER_PERMISSION` in `envs/github.py`), and `permissions` is `"*"` (anything the token's tools can do there) or fine-grained permission levels (`issues`, `contents`, `pull_requests`, `actions`, `workflows`, `checks`, `discussions`, `administration`, ...; the kind → permission map is `PERMISSION` in `envs/github.py`).

How it is used: the GitHub source registers an access check (`ctx.restrict`). The agent's own effects (the remote table, and the first step of each downstream path) reach only instances the check allows: changes need the repository in the declaration with write on the kind's permission; reads need the repository public or declared readable. The rest are not shown: an effect whose every target is excluded disappears from the remote table, starts no downstream chain, and makes no joint-risk item. The reasons are kept in the run's `ruled-out.jsonl` (e.g. "Create GitHubIssue (the agent's own effect)": outside the declared token scope). Reactions the environment carries out itself (a workflow run acts with its own token) are not checked against the declaration; they inherit the narrowed scope of the path. With a declaration, public repositories outside it are only readable, like every public repository on GitHub, so they are not listed one by one: they become a single instance, "any public repository on GitHub (readable by any token; not writable under the declared scope)", and workflow discovery runs only in the listed repositories (where the token's effects can start). Undeclared private repositories the token sees stay listed, with a warning. Reading a repository needs `metadata` (map `READ_PERMISSION`), changing its settings `administration`. Repositories get a `declared_access` attribute, and the Discovered instances table shows the declaration. Mismatches with what discovery observes are warnings: a declared repository the token cannot see, or a private repository the token sees but the declaration leaves out (a fine-grained token sees exactly the private repositories it was granted). With no declaration, every repository the token sees counts as writable, and the table says so.

#### Adding an environment

Each environment is one module in `src/materialize/instances/envs/` (imported automatically) that registers a source:

```python
from ..model import Instance
from ..registry import Context, environment

@environment("slack", kinds=["SlackWorkspace", "SlackChannel"], live=True,
             applies=lambda snap: any("slack.com" in (s.get("hostPattern") or "") for s in snap.facts["secrets"]))
def slack(ctx: Context) -> None:
    """Slack Web API as the agent (team.info, conversations.list)"""
    team = ctx.probe.get_json("https://slack.com/api/team.info", {"Authorization": "Bearer placeholder"})["team"]
    ws = ctx.add(Instance("SlackWorkspace", f"slack:{team['id']}", team["name"]))
    ...
```

`kinds` are the model kinds it lists (they count as discovered when the function returns, even with none found, so "none exists" differs from "not enumerated"). Set `parent` to the containing instance's id following the model's `ar:containsKind`, so Creates are placed ("new X in <parent>") and scopes narrow; put a `match` attr on instances a row location can name (a host, a chat id, a repository) and `events` on handlers of `ar:triggerEvent` reactions. `live=True` sources get `ctx.probe` and are skipped under `--no-discovery`; they may only read (`get_json`, `mcp(...).call` of read-only tools) and never record secret values. A `ProbeError` escaping a source stops it and notes the error on its kinds; `ctx.note(kinds, text)` records a partial gap. The kinds themselves come from the environment's ontology module (e.g. `github-environment`, `vercel`), so a new environment usually means a module there too.

The live sources run from inside the session's container (`src/materialize/probe.py`: `docker exec <container> curl`), so they take the agent's own path through the proxy and the OneCLI gateway, under its Cedar policies and with the credentials the gateway injects for it; their answer is what the agent can see. They can only read: REST calls are GET, and MCP calls are limited to tools the server's snapshot marks `readOnlyHint` (the probe refuses others). Each request is logged to the run's `requests.jsonl` (method, URL, tool, arguments, status; no bodies), and the instances, with their sources and gaps, to `instances.json`. `--no-discovery` skips the live sources. Discovered instances are also typed into the snapshot graph (`obs:Discovered`, `obs:instanceId`, `obs:partOf`).

Resolution turns an effect on a kind into instance lines (marked `▸`; the terminal shows five per run, `report.md` all):

- a read or change lands on the existing instances, or "<kind> in <container instance>" when only containers are known (containers from `ar:containsKind`);
- a Create is "new <kind> in <container instance>", going up through containers without instances ("new VercelDeployment in a new VercelProject in personal account …"), else "new <kind> (alongside N existing)";
- a row's location narrows the instances it names (a message row's DM, a credential row's host);
- web resources without an instance source resolve to the hosts the row's Cedar permit covers ("BrowserPage at *.vercel.app");
- anything else says why it is not enumerated, or "none: no <kind> exists" when a source ran and found nothing.

Where they appear: a **Discovered instances** table after the session table; under each resource in the remote endpoint table; appended to the location of host rows whose kind has its own source (changes scoped to the session's agent group unless `cli_scope` is global); and the downstream table's **Instances** column.

Downstream chains are narrowed along the way. Each path carries a scope of instance ids. An `ar:mayTrigger` node with `ar:triggerEvent` and `ar:triggerHandlerKind` (the GitHub event triggers, handler `GitHubWorkflow`) continues only through discovered handler instances in scope that list the event; when none does, the step is dropped and not shown; its reason (e.g. "no GitHubWorkflow lists 'issues' (5 GitHubWorkflow in scope)") goes to the run's `ruled-out.jsonl`. Effects on something an earlier step of the path creates land on that new instance ("VercelBuild of the new VercelDeployment created earlier on this path"). Limits: repositories are those `user:<login>` finds (organization and collaborator repositories the token may reach are not listed; the gap is recorded); workflow `paths`/`branches` filters and `workflow_call` chains are not evaluated; an instance source the gateway refuses leaves its kinds "not enumerated" with the error.

### Concrete recipients

Messaging rows name who is actually behind each destination, from NanoClaw's own records (`src/materialize/audience.py`; `ncl users`, `user-dms`, `messaging-groups`, `roles`, and every group's `wirings` and `members`). A channel destination's location is the grant, then its chat and people: for a DM, the platform chat id, each recipient (the user its `user_dms` entry points at, or for a local channel the user the chat names, e.g. `cli:local`) with their roles and agent-group memberships, and the count ("recipients: 1 (only this user)"); for a group chat, that everyone in the platform channel receives it, since NanoClaw keeps no member list, plus any users it does know there. The host row for agent-to-agent routing lists each agent destination's group, the chats it is wired to with their recipients, and its members. The downstream table's **Where it starts** column carries the concrete location of the remote rows each resource's paths begin at (the Cedar entry and host, or the destination with its recipients), so e.g. `MessagingGroup` names the DM and its one recipient. These lines also reach the joint-risk annotation through the rows.

### Session joint-risk annotation

Like the loader's [joint-consequence analysis](#optional-joint-consequence-analysis), but over this session's results (`src/materialize/pair_risks.py`, reusing `llm_annotation.analyze_pairs` with its own prompt and schema). The items are the resources of two tables, **On the host, outside the container** and **Downstream in remote environments**, one per table and resource kind (rows of one kind at several locations are consolidated). Every unordered pair of items (host–host, host–remote, remote–remote) is one Responses API call, after the report and the snapshot are written; `--max-pairs` keeps a uniform random sample of N pairs (seed printed; `--seed` reproduces it), as in the loader. Omitting the flag annotates every pair (35 items, 595 pairs on the reference session) and makes that many paid calls; `--max-pairs 0` skips it.

**Items are instances, not kinds.** Every effect of the host and downstream tables is resolved to the instances it lands on (the same targets the tables show), and each instance is one item: "new GitHubIssue in hyn0027/slicing", "the DM with hyn0027", "groups/dm-with-hyn0027/CLAUDE.md" (a host kind without an instance source is its location), "VercelBuild of the new VercelDeployment in a new VercelProject in personal account …". Instances a source found nothing for ("none") are not items. Each item carries `instance` and `instance_status` (existing, new, hosts, existing (location)), the instance's recorded attributes, `instance_in` (what holds it), and `instance_contains` (e.g. a repository's workflows with their events); for a new instance, or one known only by its container, the container's record is under `container_attrs`/`container_contains` instead. Items also carry `kind` (resolved in the catalog's `kinds`: definition, parents, possible containers), `side`, `where`, `effects`, `status`, and `rows`/`start_rows` as ids of the report rows behind them, which the catalog's `rows` holds once (so 109 items share 76 rows instead of copying them). The prompt asks for risks of these two instances, naming them, not of their kinds. With 109 items on the reference session there are 5,886 pairs; `--cross-only` keeps one host and one remote instance per pair (990).


### Run logs

Every run writes `observations/runs/<UTC timestamp>-<session>/` (under `--out`; git-ignored, since it holds real identifiers, handles, and paths):

| File | Content |
|---|---|
| `console.log` | the terminal output as printed, stdout and stderr |
| `report.md` | the warnings, every report table with full cell contents (no wrapping or truncation; line breaks and indentation kept as `<br>` and `&nbsp;`), the model/runtime differences, and the snapshot path |
| `instances.json` | the discovered instances, their sources, and the gaps (see [Concrete instances](#concrete-instances)) |
| `ruled-out.jsonl` | steps and effects not shown because discovered instances or a declared scope rule them out, with why |
| `requests.jsonl` | every discovery request made as the agent: method, URL, tool, arguments, status (no bodies) |
| `joint-risks.md` / `joint-risks.jsonl` | the session joint-risk annotation, when it ran |

The run ends by printing the directory. Tables reach `report.md` through `table_output.record_tables`, which records each printed table's rows before they are wrapped for the terminal.

### Limits of a snapshot

- **Live sessions only.** Mounts and network attachment are read from the running container; a session without one is rejected rather than reconstructed from configuration.
- **Point in time.** A later change (a new grant, MCP server, or mount) needs a new snapshot.
- **Display tables in the script.** Subpaths of resources inside a mount (`inbound.db`, `memory/`, `tasks/`, ...) and the effect and concrete location of each host step are listed in `src/materialize/constants.py` and `modifications/host.py`, not the ontology.
- **Hard-coded management mapping.** The `ncl`, `create_agent`, `install_packages`, and `add_mcp_server` rows in `src/materialize/routes.py` and `modifications/host.py` encode NanoClaw's guard rules in the script, because the ontology states them only in comments. Update the script when those guards change.
- **Claude provider only.** Tool availability is read from NanoClaw's `TOOL_ALLOWLIST` / `SDK_DISALLOWED_TOOLS`, which apply to the Claude provider; for other providers the tool requirements stay unknown. The lists are install-wide: NanoClaw has no per-group tool setting.
- **Cedar policies are summarized, not evaluated.** Each policy's head and the common `when` clauses (`resource.name like`, `context.host`, `context.path like`, `context.method ==`, `principal.identifier ==`) become a host/method/path label; a policy scoped to another agent is dropped. Any other condition (`unless`, `||`, body, header, query, or JSON checks) marks the policy "conditional", and it is still counted as applying; a conditional `forbid` is listed as an exception but never treated as blocking. Host patterns use Cedar `like` semantics (`*` matches any characters, so `*.wikipedia.org` does not match `wikipedia.org`). Grants to a user or group are counted for the agent, because their agents are not resolved. Under the gateway, a policy it cannot parse blocks everything; the snapshot reports such a policy as a finding and shows nothing reachable.
- **Undeclared observation vocabulary.** `obs:` classes and properties are not yet declared in `vocabulary.ttl` or validated by SHACL; only instance types are checked.
- **Sensitive output.** Snapshots contain real session, group, and messaging identifiers, handles, and host paths. `observations/` is git-ignored; do not commit or share snapshots.

Example query over a snapshot (read-write mounts and what they expose):

```sparql
PREFIX ar:  <urn:agent-risk:>
PREFIX obs: <urn:agent-risk:observation:>
SELECT ?path ?kind WHERE {
  ?mount obs:containerPath ?path ; obs:accessMode ar:ReadWriteMountMode ; obs:exposes ?location .
  ?location a ?kind .
}
```

## Querying the model

`loader.py` contains a built-in potential-effect report, but the same TriG files can be loaded into RDFLib, Apache Jena, GraphDB, Stardog, RDF4J, or another RDF dataset implementation. Queries that rely on property paths require a SPARQL 1.1 implementation.

Useful SPARQL paths:

```sparql
PREFIX ar: <urn:agent-risk:>

# All broader resource kinds.
SELECT ?narrow ?broader WHERE {
  ?narrow ar:specializesKind+ ?broader .
}

# All shell-mediated operations.
SELECT DISTINCT ?operation WHERE {
  ?operation ar:invokedThroughKind/ar:specializesInvocationKind* ar:ShellExecution .
}

# Potentially affected resources, retaining skill-graph provenance.
SELECT DISTINCT ?graph ?skill ?operation ?effectType ?resourceKind WHERE {
  GRAPH ?graph {
    ?skill a ar:Skill ;
           ar:declaresOperation ?operation .
    ?operation ar:hasPotentialEffect ?effect .
    ?effect ar:effectType ?effectType ;
            ar:affectsKind ?resourceKind .
  }
}

# Potential effects on a resource kind or any of its subtypes.
SELECT DISTINCT ?operation ?effectType ?resourceKind WHERE {
  ?operation ar:hasPotentialEffect ?effect .
  ?effect ar:effectType ?effectType ;
          ar:affectsKind ?resourceKind .
  ?resourceKind ar:specializesKind* ar:LocalFile .
}

# Possible NanoClaw mediation chain from a concrete endpoint.
SELECT ?next WHERE {
  ar:NanoClawSendMessageTool ar:routesToKind+ ?next .
}

# Explicitly modeled Internet-call possibilities in selected skill graphs.
# Endpoint patterns are optional: an absent pattern means the exact URL is not established.
SELECT ?graph ?operation ?call ?stage ?initiator ?endpoint ?pattern WHERE {
  GRAPH ?graph {
    ?operation ar:mayInitiateInternetCall ?call .
    ?call a ar:PotentialInternetCall ;
          ar:callStage ?stage ;
          ar:callInitiatorKind ?initiator ;
          ar:callEndpointKind ?endpoint .
    OPTIONAL { ?call ar:callEndpointPattern ?pattern }
  }
}

# Calls to GitHub's Git or REST endpoint kinds, including narrower kinds.
SELECT DISTINCT ?operation ?call ?endpoint WHERE {
  ?operation ar:mayInitiateInternetCall ?call .
  ?call ar:callEndpointKind ?endpoint .
  VALUES ?githubEndpoint { ar:GitHubGitEndpoint ar:GitHubRESTEndpoint }
  ?endpoint ar:specializesKind* ?githubEndpoint .
}

# Auditable source-to-NanoClaw skill adaptations.
SELECT ?skill ?assumption ?nanoclawModel ?disposition ?difference WHERE {
  ?skill ar:hasAdaptationMapping ?mapping .
  ?mapping ar:sourceAssumption ?assumption ;
           ar:nanoclawModel ?nanoclawModel ;
           ar:adaptationDisposition ?disposition ;
           ar:semanticDifference ?difference .
}
```

Do not merge subtype, containment, route, and protection edges into one undifferentiated reachability relation. A file inside a directory is not a kind of directory; a route protected by a policy is not proof the policy allows it; a possible path is not proof that credentials and permissions make it executable.

## Limits

This is a manually reviewed architecture model, not a complete code audit or deployed inventory. The ontology and `loader.py` do not observe installed channel adapters, live users or roles, actual mount paths, filesystem modes, container network membership, OneCLI policies, credentials, provider tool inventories, or remote MCP behavior; `materialize.py` observes a subset of these for one live session (see above), and remote MCP behavior remains unobserved. The repository documentation itself warns that some design documents can drift; stable source links in the graph pin the inspected commit, but later NanoClaw revisions require re-review.

Most kinds are primitive because the code does not provide reliable necessary-and-sufficient recognition facts in RDF form. `routesToKind` is an over-approximation of possible mediation, not temporal semantics or guaranteed transitive execution. Inferring which resources can actually change still requires concrete tool availability, arguments, identity, authority, configuration, current state, and policy decisions. Generic shell execution and delegated workers remain open-ended.

## Runtime ontology (in NanoClaw)

This project now lives in the NanoClaw checkout as `ontology/` and also runs at runtime:
each session keeps its own ontology, starting from the graphs here and growing from
skill/MCP/package changes and from the calls the OneCLI gateway records for the agent.
`ontology_runtime.py` (`src/runtime/`) does the work; NanoClaw's host module
`src/modules/ontology-runtime/` decides when. `materialize.py` runs a tick for its session
first and uses the session's expansions (`--no-expand`, `--base-only` to opt out). See
`../docs/ontology-runtime.md`. The base graphs under `ontology/` are never edited at runtime.

### Ignored policy groups

`materialize.py` treats the OneCLI Cedar group `default` (the always-on model-API permit) as if
it did not exist, so the tables show only what the agent's skills and servers open. Requests
only an ignored group permits count as denied. `--include-default-policy` considers every group;
`--ignore-policy-group NAME` (repeatable) ignores more. The session table lists what was ignored.
The default list is `DEFAULT_IGNORED_GROUPS` in `src/materialize/gateway_policy.py`.

### Only loaded skills are materialized

`materialize.py` instantiates a skill only if the agent has loaded it: a `Skill` tool call, or a
tool reading its `SKILL.md`, in the action logs (`logs/sessions/<group>/*/actions.jsonl`) of any
session of the agent group, closed sessions included (`--skills group`, the default). `--skills session`
limits that to the session being materialized; `--skills all` instantiates every installed skill. The session
table lists each materialized skill with when and in which session it was first loaded.

This is per agent: an agent is the agent group, so only its own sessions count, and one agent's
loads never widen what is materialized for another. Cedar policy groups are per agent too (see
`docs/ontology-runtime.md` in NanoClaw: per-agent copies of skill and MCP templates).
