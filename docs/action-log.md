# Agent action log

Every session gets a host-side audit log of everything the agent did — messages in and out, every tool call with its full input and output, subagents, assistant text — viewable as one timeline merged with the internet requests the OneCLI gateway saw.

## Commands

Run from the repo root.

```bash
pnpm exec tsx scripts/trace.ts                        # list sessions that have logs (newest first)
pnpm exec tsx scripts/trace.ts <session-id>           # timeline: actions + network, one line per event
pnpm exec tsx scripts/trace.ts <session-id> --full    # + complete payloads (tool input/output, prompts, messages)
pnpm exec tsx scripts/trace.ts <session-id> --quiet   # hide model API calls and Claude telemetry
pnpm exec tsx scripts/trace.ts <session-id> --follow  # keep watching, print new activity live
pnpm exec tsx scripts/trace.ts <session-id> --json    # merged timeline as JSONL
pnpm exec tsx scripts/trace.ts <session-id> --no-network  # skip the gateway log
```

An agent group id works in place of a session id (picks its most recent session). Find session ids with `ncl sessions list` or the no-argument listing above.

Example (`--quiet`):

```
11:57:57  📩 IN  chat Run the shell command: echo action-log-test && curl … wikipedia …
11:58:01  🔧 Bash echo action-log-test && curl -s -o /dev/null -w '%{http_code}' https://en.wikipedia.org/wiki/Main_Page
11:58:01     └ 🌐 GET https://en.wikipedia.org/wiki/Main_Page 200
11:58:01  ✔  Bash (211ms) → {"stdout":"action-log-test\n200","stderr":"", …}
11:58:02  💬 OUT chat 200
11:58:02  ── turn end (success) 4 in / 111 out tokens $0.1068 ──
```

Other raw sources:

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

Subagent actions carry `agentId`; the timeline labels them `[sub1]`, `[sub2]`, …. Each `data` payload is capped at 1 MB (`{truncated, originalChars, head}` beyond that).

## How it works

1. **In the container** — `container/agent-runner/src/providers/claude.ts` hooks `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop` and the SDK event stream, and appends rows via `recordAction()` (`container/agent-runner/src/db/action-log.ts`) to `action_log` in `outbound.db`. Recording is best-effort; a failure never affects the agent.
2. **On the host** — `src/modules/action-log/` polls running sessions every ~1s (all active sessions every 60s, which catches the last rows after a container exits) and appends new rows from all three streams to `logs/sessions/…/actions.jsonl`, tracking progress in `cursor.json` next to it. `logs/` is never mounted into a container, so a copied row can no longer be altered by the agent. Export is at-least-once; the viewer dedupes.
3. **At view time** — `scripts/trace.ts` reads `actions.jsonl`, pulls the gateway's lines for the session's OneCLI agent (identifier = agent group id) from `docker logs onecli`, and nests each request under the tool call whose start/end window contains it.

Disable the export with `NANOCLAW_ACTION_LOG=false` in `.env` (restart the service).

Sessions that predate the action log can import their earlier history from the Claude transcript once:

```bash
pnpm exec tsx scripts/action-log-backfill.ts <session-id>
```

Backfilled entries carry `backfill: true`; the script refuses to run twice and never overlaps native rows.

## Limitations

- **No network payloads.** Internet requests show method, URL, status and whether a credential was injected — never headers or bodies. The gateway decrypts all traffic but only logs a summary line. A request body is visible only when it appears in a tool call (e.g. inline in a `curl` command) and a response only when the tool printed it. `WebFetch` output is the model's summary of the page, not the raw page.
- **Network lines are not persisted.** They are read live from `docker logs onecli` and disappear if that container is recreated.
- **Network attribution is by time.** OneCLI identifies agent groups, not sessions; with several live sessions in one group, or parallel tool calls, a request may be nested under the wrong call. Container startup traffic (settings, MCP registry) and Claude telemetry appear unnested.
- **Short tamper window.** The agent can write its own `outbound.db`; a row is out of its reach once the host copies it (~1s).
- **Claude provider only.** Other providers (Codex, OpenCode) don't write `action_log` yet; their messages in/out are still exported.
- **Inside scripts.** A shell script's internal steps aren't recorded individually — only the command and its output (its network requests still reach the gateway log). Files changed by shell commands show only as the command.

### The OneCLI web UI is not a full request log

The UI's request list (`request_logs` in OneCLI's database) only records requests the gateway acted on — in practice, those it injected a credential into. Plain pass-through traffic (e.g. a Wikipedia fetch) never appears there; it is only in `docker logs onecli`, which is what `trace.ts` reads.
