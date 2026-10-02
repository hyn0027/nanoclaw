import { beforeEach, describe, expect, test } from 'bun:test';

import { getOutboundDb, initTestSessionDb } from '../mailbox/sqlite/connection.js';
import { capActionData, MAX_ACTION_DATA_CHARS, recordAction } from './action-log.js';

interface Row {
  id: number;
  timestamp: string;
  event: string;
  tool_use_id: string | null;
  agent_id: string | null;
  data: string;
}

function rows(): Row[] {
  return getOutboundDb().prepare('SELECT * FROM action_log ORDER BY id').all() as Row[];
}

beforeEach(() => {
  initTestSessionDb();
});

describe('action-log', () => {
  test('records tool calls with ids, subagent and JSON payload in order', () => {
    recordAction({ event: 'tool_start', toolUseId: 'tu_1', data: { tool: 'Bash', input: { command: 'ls' } } });
    recordAction({ event: 'tool_end', toolUseId: 'tu_1', agentId: 'sub_1', data: { tool: 'Bash', output: 'a\nb' } });

    const [start, end] = rows();
    expect(start.event).toBe('tool_start');
    expect(start.tool_use_id).toBe('tu_1');
    expect(start.agent_id).toBeNull();
    expect(JSON.parse(start.data)).toEqual({ tool: 'Bash', input: { command: 'ls' } });
    expect(end.agent_id).toBe('sub_1');
    expect(end.id).toBeGreaterThan(start.id);
    expect(new Date(start.timestamp).toISOString()).toBe(start.timestamp);
  });

  test('oversized payloads are capped with their original size', () => {
    const big = 'x'.repeat(MAX_ACTION_DATA_CHARS + 10);
    const capped = capActionData({ output: big }) as { truncated: boolean; originalChars: number; head: string };
    expect(capped.truncated).toBe(true);
    expect(capped.originalChars).toBeGreaterThan(MAX_ACTION_DATA_CHARS);
    expect(capped.head.length).toBe(MAX_ACTION_DATA_CHARS);
    expect(capActionData({ small: 1 })).toEqual({ small: 1 });
  });

  test('a recording failure never throws into the agent', () => {
    getOutboundDb().exec('DROP TABLE action_log');
    expect(() => recordAction({ event: 'tool_start', data: {} })).not.toThrow();
  });
});
