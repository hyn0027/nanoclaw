# Per-session runtime ontology

Every NanoClaw session keeps its own ontology of what the agent can reach and how the
environment reacts. It starts from the base model in `ontology/` (the reviewed graphs:
universal, NanoClaw, Claude, skills, MCP servers) and grows at runtime. The base files are
never edited; growth is a sequence of expansions stored with the session.

## When it grows

| Trigger | What happens |
|---|---|
| A skill is added to the agent group, or its `SKILL.md` changes | A skill with a reviewed graph (`ontology/ontology/skills/*.trig`) is loaded. A skill without one gets a proposed graph from its `SKILL.md` and files (`rt:ProposedFromSource`). |
| An MCP server is added | A server with a reviewed graph is loaded. For one without, the tools it lists in traffic (`tools/list`) are turned into operations when the agent first uses it. |
| A package is added (`install_packages`) | Operations and kinds are proposed from knowledge of the package (`rt:ProposedFromSource`). |
| Claude Code's bundled skills are switched on | Recorded as a base change, with a warning: they have no ontology. |
| The agent makes network calls | The OneCLI gateway records each call (request and response). The calls are batched to an LLM that proposes new kinds, containments, environment reactions (`ar:mayTrigger`), concrete instances, and correspondences between environments (`rt:ObservedTraffic`). |

The LLM is Claude through headless Claude Code (`claude -p`, the operator's subscription),
with no tools, no MCP servers, and no settings, answering in a fixed JSON schema. Every
proposal is checked against the ontology before it becomes triples; anything that names an
unknown kind, instance, or effect type is dropped and listed as rejected.

## Pieces

- **OneCLI gateway** (`onecli/apps/gateway/crates/proxy/src/capture.rs`, local patch). With
  `GATEWAY_CAPTURE_DIR` set it appends one JSON line per forwarded call to
  `data/onecli-capture/<YYYYMMDDHH>.jsonl` (mode 0600). The request is recorded as the agent
  sent it, before credential injection (`authorization`, `x-api-key`, cookies dropped); the
  response with `set-cookie` dropped and gzip decoded; secret-named JSON and form values are
  masked; Vercel environment-variable values are withheld. `api.anthropic.com` and
  `*.datadoghq.com` are not recorded (`GATEWAY_CAPTURE_EXCLUDE`). Bodies are capped at 1 MiB.
- **The agent waits** (`container/agent-runner/src/ontology-gate.ts`). Nothing polls for
  changes; the agent's container asks the host and waits for the answer:
  - before every turn (a new batch, or a follow-up pushed into an open query): when the
    agent group's setup (container config: skills, MCP servers, packages; Claude settings;
    `container/skills/`; OneCLI credential grants) is unchanged since the session's last
    sync, the host answers at once. Otherwise it runs `ontology_runtime.py sync --session`:
    base changes, the operator's answers (it waits for them, however long), expansions for
    new sources, and the agent's OneCLI policy groups. An approved MCP server or package
    restarts the container, so its first turn is where that change is synced;
  - before the agent loads a skill (the Skill tool, or a Read of a `SKILL.md`; once per skill
    per container): `sync --skill`, which also makes and switches on the skill's policy group;
  - after every turn (nobody waits): `sync --traffic` expands from the calls the turn made.

  A sync that fails is refused (fail closed): the turn is not run and the user is told why,
  or the Skill call is blocked with the reason. While waiting the container touches its
  heartbeat, so the stuck-container sweep leaves it alone, and re-sends the request every
  5 minutes in case the host restarted. A skill loaded some other way (e.g. `cat SKILL.md`
  in Bash) is picked up from the action log by the end-of-turn sync, without blocking.
- **Host module** (`src/modules/ontology-runtime/`). Answers the container's `ontology_sync`
  and `ontology_turn_end` messages (one Python run at a time per session), delivers the
  runtime's questions as DM cards (a 3 s loop), and starts containers with
  `NANOCLAW_ONTOLOGY_GATE=1` so they wait. Policy groups no session needs any more are
  switched off by the next sync of any session and once at host start. Output:
  `logs/ontology-runtime/runtime.log`. Off with `NANOCLAW_ONTOLOGY_RUNTIME=false`, or when
  `ontology/.venv` is missing (containers then never wait).
- **Runtime** (`ontology/src/runtime/`, Python). `sync` is what the host runs (above); `tick` (by hand) routes new captured calls to the active
  sessions of the agent group they came from, records base changes, and expands.

## State and resuming

```
data/v2-sessions/<group>/<session>/ontology/
  state.json            ontology id (ont-<session>), head expansion, base fingerprint,
                        last call id, one entry per expansion and base change
  pending.jsonl         routed calls not yet expanded
  expansions/NNNN.trig  one named graph per expansion: urn:nanoclaw:ontology:ont-<session>/NNNN
  ontology.trig         current view (runtime vocabulary + every expansion)
data/ontology-runtime/capture-cursor.json   last captured call id routed to sessions
```

Expansion ids are `ont-<session>/<NNNN>`; each graph names the previous one, the calls it
came from (`rt:fromCall`), and its trigger. A tick resumes from the cursor and the session's
head: new calls only extend what is already there. Routed hour files are deleted once their
hour is over; expanded calls are removed from `pending.jsonl` (`tick --keep-calls` keeps both).

## OneCLI policy groups follow what is loaded

The gateway denies whatever no Cedar policy permits. Only groups without a `skill:`/`mcp:`
source (`default`: the model API) are always on. The rest follow the active sessions:

- `skill:<name>` groups are on while some active session has loaded the skill: the agent
  invoked it (`Skill` tool call, or reading its `SKILL.md`, seen in the session's action log).
  The host watches running sessions' action logs every 2 s and runs
  `ontology_runtime.py policies` at once, so the switch lands within seconds; the first
  request right after loading can still race it;
- `mcp:<id>` groups are on while some active session's agent group has that MCP server;
- switching a group on switches every policy in it on (every operation of what was loaded;
  the GitHub group is generated with no approval holds);
- a group no active session needs any more is switched off (each tick reconciles: sessions
  closed, servers removed);
- a loaded skill or server with no group gets one: from the pinned snapshot for an MCP server
  (`generate_mcp_policies.py --all-on --no-approval`), otherwise drafted by Claude (`claude -p`)
  from the skill's files and its ontology's declared destinations, validated by OneCLI on save
  (retried with the validator's error). Drafts never permit all hosts, so a skill with no fixed
  destinations (e.g. `agent-browser`) gets an empty group.

Groups and the state of the last reconcile: `data/ontology-runtime/policies.json`; skills each
session loaded: `<session>/ontology/loads.json`. Code: `ontology/src/runtime/policies.py`.

**Per agent.** An agent is a NanoClaw agent group, and OneCLI gives each its own identity
(`OneCLI::Agent`). OneCLI has two layers: the workspace layer (groups naming no single agent, e.g.
`default`; each can be switched off per agent) is the operator's and is never touched by the
runtime. The runtime only derives the agent layer:

- a *template* holds a skill's or MCP server's policies for any principal. It lives in NanoClaw,
  not OneCLI: `data/ontology-runtime/policy-templates/<kind>-<name>.json`. Every policy is checked
  by OneCLI's validator before the file is saved. Edit a file to change every agent's copy;
- an *agent copy* is the only thing created in OneCLI (source `<template source>@<agent-group-id>`,
  name `<template name>:<agent-group-id>`): the template's policies with the principal narrowed to
  that agent (`principal == OneCLI::Agent::"<uuid>"`), on only while that agent needs it, and shown
  on that agent's Policies page;
- a copy without a template is kept and used as is (a group for that agent only); a template is
  made only for an agent that has no copy yet. Templates found in OneCLI (from before) are moved
  out to files automatically.

So one agent loading a skill opens nothing for another agent. Likewise, "ever loaded" (which
skills materialize includes) is read only from the agent group's own sessions. A copy is made
only once the agent has a OneCLI identity (after its first request through the gateway).

## Questions to the operator

Some facts cannot be observed. Today: what a granted credential can do (no API reveals a
fine-grained GitHub token's repositories or permissions). The runtime asks:

- when, during a session, its agent group is connected to an environment: an MCP server
  added (`ncl groups config add-mcp-server`, or the agent's approved `add_mcp_server`) or a
  OneCLI credential grant added or changed, for a credential that environment uses. With a
  saved answer the first card is "still right? keep / change"; without one, the full steps;
- for any session, when the granted credential has no declaration in
  `ontology/local/credential-scopes.json`, or a stale one (token replaced, updated, or past
  its declared expiry).

A session that starts already connected with a current saved answer is not asked. How:

- `materialize.py` run in a terminal asks there, step by step, and continues;
- otherwise the question is written to `data/ontology-runtime/questions/<id>.json` and the
  host DMs the owner (global admins first, like approvals) one button card per step:
  repositories, access preset (or per area), account permissions, expiry. Clicks are recorded
  in `data/ontology-runtime/answers/<id>.json`; options that need typing (repository
  patterns, an expiry date) point to `ontology_runtime.py answer <id>` in a terminal;
- when every step is answered, the next tick writes the declaration, closes the question,
  and the host confirms by DM.

**In your own words.** Every question's first card has "Describe it in my own words"; in the
terminal you can type `d` (or just write words) at any prompt. In Discord your next DM is
captured for the question instead of reaching the agent (a router message interceptor, 15-minute
window); the "type it" options (repository patterns, expiry date) work the same way. Claude
(`claude -p`) turns the description into a declaration and the next card asks "I understood: …
Correct?" — save it, describe again, or go back to the choices. Permission names follow
GitHub's fine-grained names, so ones the choices do not list (e.g. `statuses`) can be declared.

**Asking again.** Widening a fine-grained token's permissions on GitHub keeps the token, so
OneCLI's secret (and hence staleness detection) does not change. Run
`ontology_runtime.py ask-scope GitHub` to be asked again (DM, or `answer <id>` in a terminal).

Until then materialize waits (in a terminal it asks; without one it waits for the DM answer)
and the runtime holds that session's expansions; captured calls stay pending, nothing is
assumed. `ontology_runtime.py questions` lists open questions. Per-environment steps and the
mapping to a declaration live in `ontology/src/runtime/scope_questions.py` (`SCOPED`).

## Commands

```bash
cd ontology
.venv/bin/python ontology_runtime.py status
.venv/bin/python ontology_runtime.py questions [--all]
.venv/bin/python ontology_runtime.py policies [--no-generate] [--verbose]
.venv/bin/python ontology_runtime.py answer <question id>
.venv/bin/python ontology_runtime.py ask-scope GitHub
.venv/bin/python ontology_runtime.py show --session <id>
.venv/bin/python ontology_runtime.py tick [--session <id>] [--dry-run] [--no-llm]
.venv/bin/python ontology_runtime.py export --session <id>   # base + expansions, one TriG file
.venv/bin/python materialize.py --session <id>               # ticks first, then uses base + expansions
```

`materialize.py --no-expand` skips the tick; `--base-only` ignores the expansions.

## Limits

- The gateway knows the agent group, not the session: a call goes to every active session of
  its group (exact for a DM group with one session; otherwise marked as shared).
- Requests the gateway denies by policy are not recorded (recording starts once a request is
  allowed or held for approval); one held and then refused is recorded with no response.
- Materialize's own discovery probes send `X-NanoClaw-Ontology-Probe: 1` and are skipped.
- Expansions are LLM proposals grounded in what the environment returned. They are
  may-claims, not a reviewed model.
