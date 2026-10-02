/**
 * Render one session's full activity as a single timeline: messages in/out,
 * every agent action (tool calls with input/output, subagents, assistant
 * text), and the internet requests the OneCLI gateway saw for that agent,
 * nested under the tool call they happened during.
 *
 *   pnpm exec tsx scripts/trace.ts                      # list sessions with logs
 *   pnpm exec tsx scripts/trace.ts <session-id>         # timeline
 *   pnpm exec tsx scripts/trace.ts <session-id> --full  # + full payloads
 *   pnpm exec tsx scripts/trace.ts <session-id> --follow
 *   pnpm exec tsx scripts/trace.ts <session-id> --json  # merged, machine-readable
 *   ... --no-network                                    # skip the gateway log
 *   ... --quiet                                         # hide model calls + telemetry
 *
 * Sources: logs/sessions/<agent-group>/<session>/actions.jsonl (written by
 * src/modules/action-log) and `docker logs onecli`. The gateway identifies
 * agents per agent group, not per session, so with several live sessions in
 * one group a request is attributed by time only.
 */
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(process.cwd(), 'logs', 'sessions');
const GATEWAY = process.env.ONECLI_GATEWAY_CONTAINER || 'onecli';

interface Entry {
  source: 'inbound' | 'outbound' | 'agent' | 'network';
  ts: string;
  [key: string]: unknown;
}

interface NetEvent extends Entry {
  source: 'network';
  method?: string;
  url?: string;
  host?: string;
  status?: number;
  injections?: number;
  error?: string;
}

const args = process.argv.slice(2);
const flag = (name: string) => args.includes(`--${name}`);
const target = args.find((a) => !a.startsWith('--'));
const FULL = flag('full');
const QUIET = flag('quiet'); // hide model calls + telemetry
const color = process.stdout.isTTY && !flag('json');
const c = (code: string, text: string) => (color ? `\x1b[${code}m${text}\x1b[0m` : text);
const dim = (t: string) => c('2', t);

function listSessions(): Array<{ group: string; session: string; file: string; mtime: Date }> {
  if (!fs.existsSync(ROOT)) return [];
  return fs
    .readdirSync(ROOT)
    .flatMap((group) =>
      fs.readdirSync(path.join(ROOT, group)).map((session) => {
        const file = path.join(ROOT, group, session, 'actions.jsonl');
        return { group, session, file, mtime: fs.existsSync(file) ? fs.statSync(file).mtime : new Date(0) };
      }),
    )
    .filter((s) => fs.existsSync(s.file))
    .sort((a, b) => b.mtime.getTime() - a.mtime.getTime());
}

function readEntries(file: string): Entry[] {
  const seen = new Set<string>();
  const out: Entry[] = [];
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    const e = JSON.parse(line) as Entry;
    const key = `${e.source}:${e.seq ?? e.id}`;
    if (seen.has(key)) continue; // at-least-once export
    seen.add(key);
    out.push(e);
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts));
}

/** OneCLI agent id for an agent group (OneCLI's identifier == agent group id). */
function onecliAgentId(group: string): string | null {
  try {
    const res = JSON.parse(execFileSync('onecli', ['agents', 'list'], { encoding: 'utf8' })) as {
      data?: Array<{ id: string; identifier: string }>;
    };
    return res.data?.find((a) => a.identifier === group)?.id ?? null;
  } catch {
    return null;
  }
}

// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*m/g;

function parseGatewayLine(line: string, agentId: string): NetEvent | null {
  const clean = line.replace(ANSI, '');
  if (!clean.includes(`agent_id="${agentId}"`)) return null;
  const ts = clean.match(/^(\d{4}-\d\d-\d\dT[\d:.]+Z)/)?.[1];
  if (!ts) return null;
  const normalized = new Date(ts).toISOString();
  const mitm = clean.match(/MITM method=(\S+) url=(\S+) status=(\d+)(?:.*?injections_applied=(\d+))?/);
  if (mitm) {
    return {
      source: 'network',
      ts: normalized,
      method: mitm[1],
      url: mitm[2].replace(/:443(?=\/)/, ''),
      status: Number(mitm[3]),
      injections: mitm[4] ? Number(mitm[4]) : undefined,
    };
  }
  const err = clean.match(/(WARN|ERROR).*?(?:host=(\S+))?.*?error=(.+)$/);
  if (err) {
    const host = clean.match(/host=(\S+?)(?::443)?[\s}"]/)?.[1];
    return { source: 'network', ts: normalized, host, error: err[3].trim() };
  }
  return null;
}

function readNetwork(agentId: string, since?: string): NetEvent[] {
  try {
    const argv = ['logs', ...(since ? ['--since', since] : []), GATEWAY];
    const raw = execFileSync('docker', argv, {
      encoding: 'utf8',
      maxBuffer: 512 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    // docker logs splits stdout/stderr; the gateway logs to stdout.
    return raw
      .split('\n')
      .map((l) => parseGatewayLine(l, agentId))
      .filter((e): e is NetEvent => e !== null);
  } catch {
    return [];
  }
}

// ── rendering ────────────────────────────────────────────────────────────────

function time(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour12: false });
}

function oneLine(value: unknown, max = 160): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value);
  const flat = (s ?? '').replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

function full(value: unknown): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (s ?? '')
    .split('\n')
    .map((l) => `             ${dim('│')} ${l}`)
    .join('\n');
}

function messageText(content: unknown): string {
  if (content && typeof content === 'object') {
    const o = content as Record<string, unknown>;
    return String(o.text ?? o.content ?? o.action ?? JSON.stringify(o));
  }
  return String(content);
}

function toolSummary(data: Record<string, unknown>): string {
  const input = (data.input ?? {}) as Record<string, unknown>;
  const key = input.command ?? input.url ?? input.file_path ?? input.pattern ?? input.query ?? input.description;
  return `${c('1', String(data.tool ?? '?'))} ${oneLine(key ?? input, 140)}`;
}

function isModelCall(n: NetEvent): boolean {
  return /api\.anthropic\.com\/v1\/messages/.test(n.url ?? '');
}

/** Claude Code's own usage reporting — background noise, never caused by a tool call. */
function isTelemetry(n: NetEvent): boolean {
  return /api\.anthropic\.com\/api\/event_logging|datadoghq\.com/.test(n.url ?? n.host ?? '');
}

function renderNet(n: NetEvent, indent: string): string {
  if (n.error) return `${indent}${c('31', '🌐 ✗')} ${n.host ?? ''} ${c('31', n.error)}`;
  const status = (n.status ?? 0) >= 400 ? c('31', String(n.status)) : c('32', String(n.status));
  const cred = n.injections ? dim(' [credential injected]') : '';
  if (isModelCall(n)) return `${indent}${dim(`🧠 model call ${n.method} ${n.url} ${n.status}`)}`;
  if (isTelemetry(n)) return `${indent}${dim(`📊 telemetry ${n.method} ${n.url} ${n.status}`)}`;
  return `${indent}🌐 ${n.method} ${oneLine(n.url, 140)} ${status}${cred}`;
}

function render(entry: Entry, agentLabel: (id: unknown) => string): string | null {
  const t = dim(time(entry.ts));
  if (entry.source === 'network') return `${t}  ${renderNet(entry as NetEvent, '')}`;
  if (entry.source === 'inbound') {
    const text = messageText(entry.content);
    return `${t}  ${c('36', '📩 IN ')} ${dim(String(entry.kind))} ${FULL ? '\n' + full(text) : oneLine(text)}`;
  }
  if (entry.source === 'outbound') {
    const text = messageText(entry.content);
    return `${t}  ${c('35', '💬 OUT')} ${dim(String(entry.kind))} ${FULL ? '\n' + full(text) : oneLine(text)}`;
  }
  const data = (entry.data ?? {}) as Record<string, unknown>;
  const who = agentLabel(entry.agentId);
  switch (entry.event) {
    case 'prompt':
      return FULL ? `${t}  ${dim('↳ prompt to model')}\n${full(data.text)}` : null;
    case 'session_init':
      return `${t}  ${dim(`⚙  model session ${data.continuation} (${data.model})`)}`;
    case 'thinking':
      return `${t}  ${who}${dim('💭 ' + (FULL ? '\n' + full(data.text) : oneLine(data.text)))}`;
    case 'assistant_text':
      return `${t}  ${who}🗨  ${FULL ? '\n' + full(data.text) : oneLine(data.text)}`;
    case 'tool_start':
      return `${t}  ${who}🔧 ${toolSummary(data)}${FULL ? '\n' + full(data.input) : ''}`;
    case 'tool_end': {
      const ms = data.durationMs != null ? dim(` (${data.durationMs}ms)`) : '';
      return `${t}  ${who}${c('32', '✔')}  ${String(data.tool)}${ms} → ${FULL ? '\n' + full(data.output) : oneLine(data.output, 120)}`;
    }
    case 'tool_error':
      return `${t}  ${who}${c('31', '✘')}  ${String(data.tool)} ${c('31', oneLine(data.error, 200))}`;
    case 'tool_blocked':
      return `${t}  ${who}${c('31', '⛔ blocked')} ${String(data.tool)}`;
    case 'subagent_start':
      return `${t}  ${c('33', `🤖 subagent ${entry.agentId} started`)} ${dim(String(data.type ?? ''))}`;
    case 'subagent_stop':
      return `${t}  ${c('33', `🤖 subagent ${entry.agentId} finished`)} ${dim(oneLine(data.lastMessage ?? '', 100))}`;
    case 'turn_end': {
      const usage = data.usage as { input_tokens?: number; output_tokens?: number } | undefined;
      const tok = usage ? ` ${usage.input_tokens ?? 0} in / ${usage.output_tokens ?? 0} out tokens` : '';
      const cost = typeof data.costUsd === 'number' ? ` $${data.costUsd.toFixed(4)}` : '';
      return `${t}  ${dim(`── turn end (${data.subtype})${tok}${cost} ──`)}`;
    }
    default:
      return `${t}  ${dim(`${String(entry.event)} ${oneLine(data)}`)}`;
  }
}

/**
 * Nest network events under the tool call whose [start, end] window contains
 * them (latest-started wins when parallel calls overlap). Everything else is
 * interleaved by time.
 */
function timeline(entries: Entry[], net: NetEvent[]): Array<{ entry: Entry; children: NetEvent[] }> {
  const windows = new Map<string, { start: string; end: string; children: NetEvent[] }>();
  for (const e of entries) {
    if (e.source !== 'agent' || typeof e.toolUseId !== 'string') continue;
    if (e.event === 'tool_start') windows.set(e.toolUseId, { start: e.ts, end: '9999', children: [] });
    if ((e.event === 'tool_end' || e.event === 'tool_error') && windows.has(e.toolUseId)) {
      windows.get(e.toolUseId)!.end = e.ts;
    }
  }
  const loose: NetEvent[] = [];
  for (const n of net) {
    let best: { start: string; children: NetEvent[] } | undefined;
    for (const w of windows.values()) {
      if (n.ts >= w.start && n.ts <= w.end && (!best || w.start > best.start)) best = w;
    }
    if (best && !isModelCall(n) && !isTelemetry(n)) best.children.push(n);
    else loose.push(n);
  }
  const rows = [...entries, ...loose]
    .sort((a, b) => a.ts.localeCompare(b.ts))
    .map((entry) => ({
      entry,
      children:
        entry.source === 'agent' && entry.event === 'tool_start' && typeof entry.toolUseId === 'string'
          ? (windows.get(entry.toolUseId)?.children ?? [])
          : [],
    }));
  return rows;
}

function agentLabeler(): (id: unknown) => string {
  const ids = new Map<string, number>();
  return (id) => {
    if (typeof id !== 'string' || !id) return '';
    if (!ids.has(id)) ids.set(id, ids.size + 1);
    return c('33', `[sub${ids.get(id)}] `);
  };
}

function printRows(rows: Array<{ entry: Entry; children: NetEvent[] }>, label: (id: unknown) => string): void {
  for (const { entry, children } of rows) {
    if (QUIET && entry.source === 'network' && (isModelCall(entry as NetEvent) || isTelemetry(entry as NetEvent)))
      continue;
    const line = render(entry, label);
    if (line) console.log(line);
    for (const n of children) console.log(`${dim(time(n.ts))}  ${renderNet(n, '   └ ')}`);
  }
}

// ── main ─────────────────────────────────────────────────────────────────────

const sessions = listSessions();
if (!target) {
  if (sessions.length === 0) {
    console.log(`No session logs yet under ${ROOT}.`);
  } else {
    console.log('Sessions with action logs (newest first):\n');
    for (const s of sessions) console.log(`  ${s.session}  ${dim(s.group)}  ${dim(s.mtime.toLocaleString())}`);
    console.log(
      `\nUsage: pnpm exec tsx scripts/trace.ts <session-id> [--full] [--follow] [--json] [--quiet] [--no-network]`,
    );
  }
  process.exit(0);
}

const match = sessions.find((s) => s.session === target) ?? sessions.find((s) => s.group === target);
if (!match) {
  console.error(`No action log for "${target}". Run without arguments to list sessions.`);
  process.exit(1);
}

const agentId = flag('no-network') ? null : onecliAgentId(match.group);
if (!flag('no-network') && !agentId)
  console.error(dim('(network: OneCLI agent not found — showing agent actions only)'));

const entries = readEntries(match.file);
const first = entries[0]?.ts;
const net = agentId
  ? readNetwork(agentId).filter((n) => !first || n.ts >= new Date(Date.parse(first) - 5000).toISOString())
  : [];

if (flag('json')) {
  for (const e of [...entries, ...net].sort((a, b) => a.ts.localeCompare(b.ts))) console.log(JSON.stringify(e));
  process.exit(0);
}

const label = agentLabeler();
console.log(c('1', `Session ${match.session}`) + dim(`  (agent group ${match.group})\n`));
printRows(timeline(entries, net), label);

if (flag('follow')) {
  console.log(dim('\n── following (Ctrl-C to stop) ──'));
  let offset = fs.statSync(match.file).size;
  let buffer = '';
  setInterval(() => {
    const size = fs.statSync(match.file).size;
    if (size <= offset) return;
    const fd = fs.openSync(match.file, 'r');
    const chunk = Buffer.alloc(size - offset);
    fs.readSync(fd, chunk, 0, chunk.length, offset);
    fs.closeSync(fd);
    offset = size;
    buffer += chunk.toString('utf8');
    const parts = buffer.split('\n');
    buffer = parts.pop() ?? '';
    for (const p of parts) {
      if (!p.trim()) continue;
      const line = render(JSON.parse(p) as Entry, label);
      if (line) console.log(line);
    }
  }, 1000);
  if (agentId) {
    const child = spawn('docker', ['logs', '-f', '--since', '1s', GATEWAY], { stdio: ['ignore', 'pipe', 'pipe'] });
    let pending = '';
    child.stdout.on('data', (d: Buffer) => {
      pending += d.toString('utf8');
      const parts = pending.split('\n');
      pending = parts.pop() ?? '';
      for (const p of parts) {
        const n = parseGatewayLine(p, agentId);
        if (n) console.log(`${dim(time(n.ts))}  ${renderNet(n, '')}`);
      }
    });
  }
}
