# Permission modes and the ontology switch

Two per-agent-group settings make Claude Code's own safeguards available as baselines to
compare the runtime ontology ([ontology-runtime.md](ontology-runtime.md)) against.

```bash
ncl groups config update --id <group-id> --permission-mode bypass|auto|manual
ncl groups config update --id <group-id> --ontology on|off
ncl groups restart --id <group-id>      # both take effect on the next container
```

| `--permission-mode` | What happens to a tool call that needs permission |
|---|---|
| `bypass` (default) | Nothing: every call runs unchecked. The container, the OneCLI gateway and host approvals are the boundary. |
| `auto` | Claude Code's auto-mode classifier allows or denies it. A call it leaves to a person is denied (there is no one to ask). |
| `manual` | An owner or admin gets an Allow / Deny card in their DM (picked like an approval); the agent waits for the click. |

In `auto` and `manual` mode only low-risk tools are pre-approved: Read, Glob, Grep, TodoWrite,
ToolSearch, Skill, subagents (Task…, Team…) and NanoClaw's own MCP tools, whose privileged
actions already need host approval. Bash, Write, Edit, NotebookEdit, WebFetch, WebSearch and
other MCP servers go through the mode.

`--ontology off` leaves the group out of the runtime ontology: its containers start without the
ontology gate, and the Python runtime skips its sessions (no policy groups, questions or
expansions). Combined, the conditions are:

| Condition | `--permission-mode` | `--ontology` |
|---|---|---|
| No safeguard | `bypass` | `off` |
| Claude Code auto mode | `auto` | `off` |
| Manual approval | `manual` | `off` |
| Runtime ontology | `bypass` | `on` |
| Both | `auto` or `manual` | `on` |

## Records

Every decision goes to the session's action log
(`logs/sessions/<group>/<session>/actions.jsonl`) as a `permission_decision` event:

```json
{"event":"permission_decision","toolUseId":"…","data":{"tool":"Bash","mode":"manual","decision":"deny","by":"operator"}}
```

`by` is `operator` (manual card), `classifier` (an auto-mode denial) or `no-one` (auto mode, a call
left to a person). Calls the classifier allows have no decision event; they show as the usual
`tool_start` / `tool_end`.

## Code

- `container/agent-runner/src/permission-mode.ts`: SDK options per mode, the approval callback,
  the decision records.
- `container/agent-runner/src/host-request.ts`: the ask-the-host-and-wait transport (shared with
  the ontology gate).
- `src/modules/permission-mode/`: the manual-mode card and its answer. It does not use the
  approvals primitive, because a rejected approval writes a chat message into the session.
- `container_configs.permission_mode` / `.ontology` (migration 026), passed to the container as
  `NANOCLAW_PERMISSION_MODE` and (when on) `NANOCLAW_ONTOLOGY_GATE`.
