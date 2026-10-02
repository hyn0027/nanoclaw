import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { INBOUND_SCHEMA, OUTBOUND_SCHEMA } from '../../mailbox/sqlite/schema.js';
import { wrapSqliteInbound, wrapSqliteOutbound } from '../../mailbox/sqlite/index.js';
import type { MailboxSession } from '../../mailbox/types.js';

import { EXPORT_BATCH, exportSessionActivity, readCursor } from './export.js';

let dir: string;
let inboundDb: Database.Database;
let outboundDb: Database.Database;
let mailbox: MailboxSession;

function addInbound(seq: number, ts: string, content: object): void {
  inboundDb
    .prepare(`INSERT INTO messages_in (id, seq, kind, timestamp, content) VALUES (?, ?, 'chat', ?, ?)`)
    .run(`in-${seq}`, seq, ts, JSON.stringify(content));
}

function addAction(ts: string, event: string, data: object, toolUseId: string | null = null): void {
  outboundDb
    .prepare(`INSERT INTO action_log (timestamp, event, tool_use_id, agent_id, data) VALUES (?, ?, ?, NULL, ?)`)
    .run(ts, event, toolUseId, JSON.stringify(data));
}

function lines(): Array<Record<string, unknown>> {
  return fs
    .readFileSync(path.join(dir, 'actions.jsonl'), 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line));
}

beforeEach(() => {
  dir = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'action-log-')), 'ag', 'sess');
  inboundDb = new Database(':memory:');
  outboundDb = new Database(':memory:');
  inboundDb.exec(INBOUND_SCHEMA);
  outboundDb.exec(OUTBOUND_SCHEMA);
  mailbox = {
    ...wrapSqliteInbound(inboundDb),
    ...wrapSqliteOutbound(() => outboundDb),
  } as MailboxSession;
});

afterEach(() => {
  inboundDb.close();
  outboundDb.close();
});

describe('action-log export', () => {
  it('merges inbound messages and agent actions in timestamp order with parsed payloads', () => {
    addInbound(2, '2026-10-02T14:00:00.000Z', { text: 'hi' });
    addAction('2026-10-02T14:00:01.000Z', 'tool_start', { tool: 'Bash', input: { command: 'ls' } }, 'tu_1');
    addAction('2026-10-02T14:00:02.000Z', 'tool_end', { tool: 'Bash', output: 'a' }, 'tu_1');

    expect(exportSessionActivity(mailbox, dir)).toBe(3);
    const out = lines();
    expect(out.map((e) => [e.source, e.event ?? e.kind])).toEqual([
      ['inbound', 'chat'],
      ['agent', 'tool_start'],
      ['agent', 'tool_end'],
    ]);
    expect(out[0].content).toEqual({ text: 'hi' });
    expect(out[1].data).toEqual({ tool: 'Bash', input: { command: 'ls' } });
    expect(out[1].toolUseId).toBe('tu_1');
  });

  it('only appends new rows on later polls', () => {
    addAction('2026-10-02T14:00:01.000Z', 'tool_start', { tool: 'Read' });
    exportSessionActivity(mailbox, dir);
    expect(exportSessionActivity(mailbox, dir)).toBe(0);

    addAction('2026-10-02T14:00:03.000Z', 'tool_end', { tool: 'Read' });
    expect(exportSessionActivity(mailbox, dir)).toBe(1);
    expect(lines()).toHaveLength(2);
    expect(readCursor(dir).actionId).toBe(2);
  });

  it('drains more than one batch in a single call', () => {
    for (let i = 0; i < EXPORT_BATCH + 5; i++)
      addAction(new Date(Date.UTC(2026, 9, 2, 0, 0, i)).toISOString(), 'x', {});
    expect(exportSessionActivity(mailbox, dir)).toBe(EXPORT_BATCH + 5);
  });

  it('tolerates session DBs created before the action_log table existed', () => {
    outboundDb.exec('DROP TABLE action_log');
    addInbound(2, '2026-10-02T14:00:00.000Z', { text: 'hi' });
    expect(exportSessionActivity(mailbox, dir)).toBe(1);
  });
});
