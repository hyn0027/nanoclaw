/**
 * Copy one session's activity out of its mailbox into a host-only, append-only
 * JSONL file:
 *
 *   logs/sessions/<agent-group>/<session>/actions.jsonl
 *
 * Three streams land in it, each with a monotonic cursor kept beside the file:
 *   - inbound   — messages_in rows (what reached the agent)
 *   - outbound  — messages_out rows (replies, system actions)
 *   - agent     — action_log rows (tool calls with full input/output,
 *                 subagent lifecycle, assistant text, turn usage)
 *
 * The file lives outside every container mount, so once a row is copied the
 * agent can no longer alter it. Delivery is at-least-once: a crash between the
 * append and the cursor write re-appends that batch; readers dedupe on
 * (source, seq|id).
 */
import fs from 'node:fs';
import path from 'node:path';

import type { MailboxActionRow, MailboxActivityMessage, MailboxSession } from '../../mailbox/types.js';

export const EXPORT_BATCH = 500;

export interface ActionLogCursor {
  inSeq: number;
  outSeq: number;
  actionId: number;
}

export type ActionLogEntry =
  | ({ source: 'inbound' | 'outbound'; ts: string } & Omit<MailboxActivityMessage, 'timestamp' | 'content'> & {
        content: unknown;
      })
  | ({ source: 'agent'; ts: string } & Omit<MailboxActionRow, 'timestamp' | 'data'> & { data: unknown });

export function sessionLogDir(root: string, agentGroupId: string, sessionId: string): string {
  return path.join(root, agentGroupId, sessionId);
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
    // eslint-disable-next-line no-catch-all/no-catch-all -- non-JSON payloads are kept verbatim
  } catch {
    return text;
  }
}

export function readCursor(dir: string): ActionLogCursor {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(dir, 'cursor.json'), 'utf8')) as Partial<ActionLogCursor>;
    return { inSeq: raw.inSeq ?? 0, outSeq: raw.outSeq ?? 0, actionId: raw.actionId ?? 0 };
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing or corrupt cursor restarts the export; readers dedupe
  } catch {
    return { inSeq: 0, outSeq: 0, actionId: 0 };
  }
}

function writeCursor(dir: string, cursor: ActionLogCursor): void {
  const tmp = path.join(dir, `.cursor-${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(cursor), { mode: 0o600 });
  fs.renameSync(tmp, path.join(dir, 'cursor.json'));
}

function messageEntry(source: 'inbound' | 'outbound', row: MailboxActivityMessage): ActionLogEntry {
  const { timestamp, content, ...rest } = row;
  return { source, ts: timestamp, ...rest, content: parseJson(content) };
}

function actionEntry(row: MailboxActionRow): ActionLogEntry {
  const { timestamp, data, ...rest } = row;
  return { source: 'agent', ts: timestamp, ...rest, data: parseJson(data) };
}

/**
 * Drain everything new for one session. Returns the number of entries
 * appended. Reads run inside the caller's mailbox session; the file write is
 * synchronous so a batch is never half-recorded relative to its cursor.
 */
export function exportSessionActivity(mailbox: MailboxSession, dir: string): number {
  if (!mailbox.getActionsAfter && !mailbox.getInboundAfterSeq && !mailbox.getOutboundAfterSeq) return 0;
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const cursor = readCursor(dir);
  let written = 0;

  for (;;) {
    const inbound = mailbox.getInboundAfterSeq?.(cursor.inSeq, EXPORT_BATCH) ?? [];
    const outbound = mailbox.getOutboundAfterSeq?.(cursor.outSeq, EXPORT_BATCH) ?? [];
    const actions = mailbox.getActionsAfter?.(cursor.actionId, EXPORT_BATCH) ?? [];
    if (inbound.length + outbound.length + actions.length === 0) break;

    const entries = [
      ...inbound.map((row) => messageEntry('inbound', row)),
      ...outbound.map((row) => messageEntry('outbound', row)),
      ...actions.map(actionEntry),
    ].sort((a, b) => a.ts.localeCompare(b.ts));
    fs.appendFileSync(path.join(dir, 'actions.jsonl'), entries.map((e) => JSON.stringify(e)).join('\n') + '\n', {
      mode: 0o600,
    });

    if (inbound.length) cursor.inSeq = inbound[inbound.length - 1].seq;
    if (outbound.length) cursor.outSeq = outbound[outbound.length - 1].seq;
    if (actions.length) cursor.actionId = actions[actions.length - 1].id;
    writeCursor(dir, cursor);
    written += entries.length;

    if (inbound.length < EXPORT_BATCH && outbound.length < EXPORT_BATCH && actions.length < EXPORT_BATCH) break;
  }
  return written;
}
