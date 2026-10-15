/**
 * Action-log module — per-session audit export.
 *
 * Polls every running session about once a second (and every active session
 * once a minute, which picks up the final rows after a container exits) and
 * appends new activity to logs/sessions/<agent-group>/<session>/actions.jsonl.
 * See export.ts for the file format. `scripts/trace.ts` renders it.
 *
 * Disabled with NANOCLAW_ACTION_LOG=false.
 */
import { mapConcurrent } from '../../concurrency.js';
import { ACTION_LOG_ENABLED, SESSION_LOGS_DIR } from '../../config.js';
import { getActiveSessions, getRunningSessions } from '../../db/sessions.js';
import { onHostShutdown, onHostStart } from '../../host-lifecycle.js';
import { log } from '../../log.js';
import { withExistingMailboxSession } from '../../session-manager.js';
import type { Session } from '../../types.js';

import { exportSessionActivity, sessionLogDir } from './export.js';

const ACTIVE_POLL_MS = 1000;
const SWEEP_POLL_MS = 60_000;
const CONCURRENCY = 4;

const inflight = new Set<string>();
const timers = new Set<NodeJS.Timeout>();
let running = false;

async function exportSession(session: Session): Promise<void> {
  if (inflight.has(session.id)) return;
  inflight.add(session.id);
  try {
    await withExistingMailboxSession(session.agent_group_id, session.id, (mailbox) =>
      exportSessionActivity(mailbox, sessionLogDir(SESSION_LOGS_DIR, session.agent_group_id, session.id)),
    );
    // eslint-disable-next-line no-catch-all/no-catch-all -- audit export must never affect the session; retried next tick
  } catch (err) {
    log.warn('Action-log export failed', { sessionId: session.id, err });
  } finally {
    inflight.delete(session.id);
  }
}

function loop(intervalMs: number, list: () => Promise<Session[]>): void {
  const tick = async (): Promise<void> => {
    if (!running) return;
    try {
      await mapConcurrent(await list(), CONCURRENCY, exportSession);
      // eslint-disable-next-line no-catch-all/no-catch-all -- a failed tick is retried on the next one
    } catch (err) {
      log.warn('Action-log poll error', { err });
    }
    if (!running) return;
    const timer = setTimeout(() => {
      timers.delete(timer);
      void tick();
    }, intervalMs);
    timers.add(timer);
  };
  void tick();
}

onHostStart(() => {
  if (!ACTION_LOG_ENABLED) return;
  running = true;
  loop(ACTIVE_POLL_MS, getRunningSessions);
  loop(SWEEP_POLL_MS, getActiveSessions);
  log.info('Action-log export started', { dir: SESSION_LOGS_DIR });
});

onHostShutdown(async () => {
  // Never started (disabled, or shut down before host start, e.g. during crash backoff): nothing to flush.
  if (!running) return;
  running = false;
  for (const t of timers) clearTimeout(t);
  timers.clear();
  // Final flush so the last turn before shutdown is not left in the mailbox only.
  try {
    await mapConcurrent(await getActiveSessions(), CONCURRENCY, exportSession);
    // eslint-disable-next-line no-catch-all/no-catch-all -- shutdown must continue
  } catch (err) {
    log.warn('Action-log final flush failed', { err });
  }
});
