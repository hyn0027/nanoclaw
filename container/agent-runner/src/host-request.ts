/**
 * Ask the host something and wait for its answer: the transport under the ontology gate
 * (ontology-gate.ts) and manual permission mode (permission-mode.ts).
 *
 * The request is an outbound system message `{ action, requestId, ... }`; the answer is an
 * inbound system message carrying the same `requestId` and `frame: { ok, error? }`, found
 * with the CLI-response lookup. Waits are unbounded on purpose (the host may be waiting
 * for a person). The heartbeat is touched while waiting so the host's stuck-container
 * sweep leaves the container alone, and the request is re-sent every few minutes in case
 * the host restarted and lost it.
 */
import { touchHeartbeat } from './heartbeat.js';
import { getAgentMailbox } from './mailbox/index.js';

const POLL_MS = 1_000;
const RESEND_MS = 5 * 60_000;

export interface HostAnswer {
  ok: boolean;
  error?: string;
  /** Something the agent should be told (e.g. a credential was granted, or is missing). */
  notice?: string;
}

export function newRequestId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Write a system message for the host (fire-and-forget). */
export async function sendToHost(content: { action: string; requestId: string }): Promise<void> {
  const mailbox = getAgentMailbox();
  await mailbox.run(() =>
    mailbox.operations.writeMessageOut({
      id: `${content.requestId}-${Date.now()}`,
      kind: 'system',
      content: JSON.stringify(content),
    }),
  );
}

/**
 * Send `content` and wait for the host's answer. `label` names the wait in the log, which
 * says when it is still waiting after 5 s and when it ends.
 */
export async function askHost(
  content: { action: string; requestId: string },
  label: string,
  log: (msg: string) => void,
): Promise<HostAnswer> {
  const mailbox = getAgentMailbox();
  const started = Date.now();
  let sentAt = 0;
  let announced = false;
  for (;;) {
    if (Date.now() - sentAt >= RESEND_MS) {
      await sendToHost(content);
      sentAt = Date.now();
    }
    const frame = await mailbox.run(() => {
      const row = mailbox.operations.findCliResponse(content.requestId);
      if (!row) return null;
      mailbox.operations.markMessages([row.id], 'completed');
      return (JSON.parse(row.content) as { frame: HostAnswer }).frame;
    });
    if (frame) {
      if (announced) log(`${label}: done after ${Math.round((Date.now() - started) / 1000)}s`);
      return frame;
    }
    if (!announced && Date.now() - started > 5_000) {
      log(`${label}: waiting for the host`);
      announced = true;
    }
    touchHeartbeat();
    await Bun.sleep(POLL_MS);
  }
}
