/**
 * One-time backfill: convert a session's Claude transcript (recorded before
 * the action log existed) into action-log entries, so `scripts/trace.ts`
 * shows the session's whole history.
 *
 *   pnpm exec tsx scripts/action-log-backfill.ts <session-id>
 *
 * Only transcript events older than the session's first native action-log
 * row are converted, so running it after the action log is live never
 * duplicates. Entries carry `backfill: true`. Messages in/out need no backfill
 * — the exporter copies those from the start of the mailbox.
 */
import fs from 'node:fs';
import path from 'node:path';

import Database from 'better-sqlite3';

const sessionId = process.argv[2];
if (!sessionId) {
  console.error('Usage: pnpm exec tsx scripts/action-log-backfill.ts <session-id>');
  process.exit(1);
}

const DATA = path.resolve('data', 'v2-sessions');
const LOGS = path.resolve('logs', 'sessions');
const group = fs.readdirSync(DATA).find((g) => fs.existsSync(path.join(DATA, g, sessionId)));
if (!group) {
  console.error(`Session ${sessionId} not found under ${DATA}`);
  process.exit(1);
}

const outbound = new Database(path.join(DATA, group, sessionId, 'outbound.db'), { readonly: true });
const continuation = (
  outbound.prepare("SELECT value FROM session_state WHERE key = 'continuation:claude'").get() as
    | { value: string }
    | undefined
)?.value;
outbound.close();
if (!continuation) {
  console.error('No Claude continuation recorded for this session — nothing to backfill.');
  process.exit(0);
}

const projects = path.join(DATA, group, '.claude-shared', 'projects');
const transcript = fs
  .readdirSync(projects)
  .map((p) => path.join(projects, p, `${continuation}.jsonl`))
  .find((f) => fs.existsSync(f));
if (!transcript) {
  console.error(`Transcript ${continuation}.jsonl not found under ${projects}`);
  process.exit(1);
}

const dir = path.join(LOGS, group, sessionId);
fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
const actionsFile = path.join(dir, 'actions.jsonl');

// Stop at the first native agent row; also skip if a backfill already ran.
let cutoff = '9999';
if (fs.existsSync(actionsFile)) {
  for (const line of fs.readFileSync(actionsFile, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    const e = JSON.parse(line) as { source: string; ts: string; backfill?: boolean };
    if (e.backfill) {
      console.error('Backfill already present — nothing to do.');
      process.exit(0);
    }
    if (e.source === 'agent' && e.ts < cutoff) cutoff = e.ts;
  }
}

interface Block {
  type?: string;
  text?: string;
  thinking?: string;
  id?: string;
  name?: string;
  input?: unknown;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
}

const toolNames = new Map<string, string>();
const entries: Array<Record<string, unknown>> = [];
let n = 0;
const push = (ts: string, event: string, data: unknown, toolUseId: string | null = null) =>
  entries.push({ source: 'agent', ts, id: `bf-${++n}`, event, toolUseId, agentId: null, data, backfill: true });

for (const line of fs.readFileSync(transcript, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  const rec = JSON.parse(line) as { type?: string; timestamp?: string; message?: { content?: unknown } };
  if (!rec.timestamp || rec.timestamp >= cutoff) continue;
  const content = rec.message?.content;
  if (rec.type === 'user' && typeof content === 'string') {
    push(rec.timestamp, 'prompt', { text: content });
    continue;
  }
  if (!Array.isArray(content)) continue;
  for (const b of content as Block[]) {
    if (rec.type === 'assistant' && b.type === 'text' && b.text)
      push(rec.timestamp, 'assistant_text', { text: b.text });
    else if (rec.type === 'assistant' && b.type === 'thinking' && b.thinking)
      push(rec.timestamp, 'thinking', { text: b.thinking });
    else if (rec.type === 'assistant' && b.type === 'tool_use' && b.id) {
      toolNames.set(b.id, b.name ?? '?');
      push(rec.timestamp, 'tool_start', { tool: b.name, input: b.input }, b.id);
    } else if (rec.type === 'user' && b.type === 'tool_result' && b.tool_use_id) {
      const tool = toolNames.get(b.tool_use_id);
      push(
        rec.timestamp,
        b.is_error ? 'tool_error' : 'tool_end',
        b.is_error ? { tool, error: b.content } : { tool, output: b.content },
        b.tool_use_id,
      );
    } else if (rec.type === 'user' && b.type === 'text' && b.text) push(rec.timestamp, 'prompt', { text: b.text });
  }
}

if (entries.length)
  fs.appendFileSync(actionsFile, entries.map((e) => JSON.stringify(e)).join('\n') + '\n', { mode: 0o600 });
console.log(
  `Backfilled ${entries.length} agent actions from ${path.relative(process.cwd(), transcript)} → ${path.relative(process.cwd(), actionsFile)}`,
);
