/**
 * Manual permission mode, host side: the agent's tool call waits for a person.
 *
 * An agent group set to `--permission-mode manual` runs Claude Code in its default
 * permission mode; each tool call that needs permission is sent here by the container
 * (container/agent-runner/src/permission-mode.ts) as a `permission_request` system message.
 * This module DMs an owner or admin (picked like an approval) an Allow / Deny card, and on
 * the click writes the answer back to the session, where the waiting container finds it.
 *
 * It does not use the approvals primitive on purpose: a rejected approval writes a chat
 * message into the session, which would wake the agent and change the run being compared.
 *
 * Pending requests live in memory. After a host restart the container re-sends its request
 * (every 5 minutes) and a fresh card goes out; a click on the old card is ignored.
 */
import { normalizeOptions } from '../../channels/ask-question.js';
import { registerQuestionRenderResolver } from '../../channels/question-render-registry.js';
import { getMessagingGroup } from '../../db/messaging-groups.js';
import { getAgentGroup } from '../../db/agent-groups.js';
import { getDeliveryAdapter, registerDeliveryAction } from '../../delivery.js';
import { unguarded } from '../../guard/index.js';
import { log } from '../../log.js';
import { registerResponseHandler, type ResponsePayload } from '../../response-registry.js';
import { writeSessionMessage } from '../../session-manager.js';
import type { Session } from '../../types.js';
import { pickApprovalDelivery, pickApprover } from '../approvals/primitive.js';

const CARD_PREFIX = 'permq-';
const OPTIONS = [
  { label: 'Allow', selectedLabel: '✅ Allowed', value: 'allow', style: 'primary' as const },
  { label: 'Deny', selectedLabel: '❌ Denied', value: 'deny', style: 'danger' as const },
];

interface Pending {
  session: Session;
  userId: string;
  title: string;
  question: string;
}

/** requestId -> the request waiting for a click. */
const pending = new Map<string, Pending>();

async function answer(session: Session, requestId: string, ok: boolean, error?: string): Promise<void> {
  await writeSessionMessage(session.agent_group_id, session.id, {
    id: `perm-resp-${requestId}`,
    kind: 'system',
    timestamp: new Date().toISOString(),
    content: JSON.stringify({
      type: 'permission_response',
      requestId,
      frame: ok ? { ok: true } : { ok: false, ...(error ? { error } : {}) },
    }),
    trigger: false,
  });
}

function text(value: unknown, max = 300): string {
  return typeof value === 'string' ? value.slice(0, max) : '';
}

async function handleRequest(content: Record<string, unknown>, session: Session): Promise<void> {
  const requestId = text(content.requestId, 100);
  if (!requestId || pending.has(requestId)) return; // a re-send while the card is out
  const tool = text(content.tool, 100) || 'a tool';

  const approvers = await pickApprover(session.agent_group_id);
  const origin = session.messaging_group_id ? await getMessagingGroup(session.messaging_group_id) : undefined;
  const target = approvers.length
    ? await pickApprovalDelivery(approvers, origin?.channel_type ?? '', origin?.instance)
    : null;
  const adapter = getDeliveryAdapter();
  if (!target || !adapter) {
    log.warn('Permission request denied: no owner or admin reachable by DM', { sessionId: session.id, tool });
    await answer(session, requestId, false, 'no owner or admin could be asked');
    return;
  }

  const agent = (await getAgentGroup(session.agent_group_id))?.name ?? 'The agent';
  const title = `Allow ${tool}?`;
  const why = text(content.title) || text(content.reason);
  const question =
    `${agent} wants to use ${tool}.` +
    (why ? `\n${why}` : '') +
    (text(content.blockedPath) ? `\nPath: ${text(content.blockedPath)}` : '') +
    `\n\`\`\`\n${text(content.input, 1500)}\n\`\`\``;

  pending.set(requestId, { session, userId: target.userId, title, question });
  try {
    await adapter.deliver(
      target.messagingGroup.channel_type,
      target.messagingGroup.platform_id,
      null,
      'chat-sdk',
      JSON.stringify({
        type: 'ask_question',
        questionId: `${CARD_PREFIX}${requestId}`,
        title,
        question,
        options: normalizeOptions(OPTIONS),
      }),
      undefined,
      target.messagingGroup.instance ?? undefined,
    );
    log.info('Permission card sent', { sessionId: session.id, tool, to: target.userId });
    // eslint-disable-next-line no-catch-all/no-catch-all -- an undeliverable card must not leave the agent waiting
  } catch (err) {
    pending.delete(requestId);
    log.error('Permission card delivery failed; the call is denied', { sessionId: session.id, tool, err });
    await answer(session, requestId, false, 'the approval card could not be delivered');
  }
}

async function handleResponse(payload: ResponsePayload): Promise<boolean> {
  if (!payload.questionId.startsWith(CARD_PREFIX)) return false;
  const requestId = payload.questionId.slice(CARD_PREFIX.length);
  const req = pending.get(requestId);
  if (!req) return true; // answered already, or the host restarted
  if (payload.userId && req.userId !== payload.userId && !req.userId.endsWith(`:${payload.userId}`)) {
    log.warn('Permission card: click from someone other than the asked user ignored', { userId: payload.userId });
    return true;
  }
  const option =
    (/^\d+$/.test(payload.value) ? OPTIONS[Number(payload.value)] : undefined) ??
    OPTIONS.find((o) => o.value === payload.value);
  if (!option) return true;
  pending.delete(requestId);
  await answer(req.session, requestId, option.value === 'allow');
  log.info('Permission decided', { sessionId: req.session.id, requestId, decision: option.value });
  return true;
}

registerDeliveryAction(
  'permission_request',
  async (content, session) => {
    void handleRequest(content, session).catch((err) =>
      log.warn('Permission request error', { sessionId: session.id, err }),
    );
  },
  unguarded('asks a person to allow or deny one of the agent’s own tool calls; grants nothing on its own'),
);
registerResponseHandler(handleResponse);
registerQuestionRenderResolver((questionId) => {
  if (!questionId.startsWith(CARD_PREFIX)) return undefined;
  const req = pending.get(questionId.slice(CARD_PREFIX.length));
  return { title: req?.title ?? 'Permission', question: req?.question, options: normalizeOptions(OPTIONS) };
});
