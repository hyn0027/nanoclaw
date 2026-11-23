# Agent action log

Every session gets a host-side audit log of everything the agent did — messages in and out, every tool call with its full input and output, subagents, assistant text.

## Where to look

| What | Where |
|------|-------|
| Session action log (raw) | `logs/sessions/<agent-group>/<session>/actions.jsonl` |
| Every request the gateway proxied (method, URL, status) | `docker logs onecli 2>&1 \| grep 'MITM method'` |
| Claude's own transcript | `data/v2-sessions/<agent-group>/.claude-shared/projects/*/<uuid>.jsonl` |
| Host routing / spawns / delivery | `logs/nanoclaw.log`, `logs/nanoclaw.error.log` |

## What is recorded

`actions.jsonl` has one JSON object per line, `source` one of:

| `source` | Events | From |
|----------|--------|------|
| `inbound` | every `messages_in` row (chat, tasks, system, wake) | session `inbound.db` |
| `outbound` | every `messages_out` row (replies, system actions) | session `outbound.db` |
| `agent` | `prompt`, `tool_start` (full input), `tool_end` (full output, duration), `tool_error`, `tool_blocked`, `subagent_start` / `subagent_stop`, `assistant_text`, `thinking`, `session_init`, `turn_end` (usage, cost) | `action_log` table in `outbound.db`, written by the Claude provider's SDK hooks |

Subagent actions carry `agentId`. Each `data` payload is capped at 1 MB (`{truncated, originalChars, head}` beyond that).

## How it works

1. **In the container** — `container/agent-runner/src/providers/claude.ts` hooks `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop` and the SDK event stream, and appends rows via `recordAction()` (`container/agent-runner/src/db/action-log.ts`) to `action_log` in `outbound.db`. Recording is best-effort; a failure never affects the agent.
2. **On the host** — `src/modules/action-log/` polls running sessions every ~1s (all active sessions every 60s, which catches the last rows after a container exits) and appends new rows from all three streams to `logs/sessions/…/actions.jsonl`, tracking progress in `cursor.json` next to it. `logs/` is never mounted into a container, so a copied row can no longer be altered by the agent. Export is at-least-once, so a row can appear twice.

Disable the export with `NANOCLAW_ACTION_LOG=false` in `.env` (restart the service).

## Limitations

- **Short tamper window.** The agent can write its own `outbound.db`; a row is out of its reach once the host copies it (~1s).
- **Claude provider only.** Other providers (Codex, OpenCode) don't write `action_log` yet; their messages in/out are still exported.
- **Inside scripts.** A shell script's internal steps aren't recorded individually — only the command and its output. Files changed by shell commands show only as the command.
