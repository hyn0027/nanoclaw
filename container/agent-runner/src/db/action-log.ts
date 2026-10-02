/**
 * Agent action log — every tool call (full input + output), subagent
 * lifecycle, and assistant text, appended to outbound.db `action_log`. The
 * host copies rows out to logs/sessions/<group>/<session>/actions.jsonl.
 *
 * Recording is best-effort: an audit failure must never break the agent.
 */
import { getAgentMailbox } from '../mailbox/index.js';
import type { ActionDraft } from '../mailbox/types.js';

/** Per-row payload ceiling; oversized payloads keep a head + the original size. */
export const MAX_ACTION_DATA_CHARS = 1_000_000;

export function capActionData(data: unknown): unknown {
  let serialized: string;
  try {
    serialized = JSON.stringify(data) ?? 'null';
  } catch {
    return { unserializable: String(data) };
  }
  if (serialized.length <= MAX_ACTION_DATA_CHARS) return data;
  return { truncated: true, originalChars: serialized.length, head: serialized.slice(0, MAX_ACTION_DATA_CHARS) };
}

export function recordAction(action: ActionDraft): void {
  try {
    getAgentMailbox().operations.appendAction?.({ ...action, data: capActionData(action.data) });
  } catch (err) {
    console.error(`[action-log] failed to record ${action.event}: ${err instanceof Error ? err.message : String(err)}`);
  }
}
