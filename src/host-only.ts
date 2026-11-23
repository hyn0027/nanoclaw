/**
 * Marks messages the agent never sees.
 *
 * The agent's own messages go out through the delivery poll (delivery.ts, from its
 * outbound DB). Everything else the host sends itself (approval cards, ontology questions,
 * permission cards, sender and channel approvals, notices) goes through
 * `getDeliveryAdapter()`, which passes each message through `markHostOnly`: a note at the end
 * says the agent does not see it. The chat bridge keeps the note when it rewrites an
 * answered card (`isHostOnlyCard`).
 *
 * A message whose substance the agent is told (e.g. a credential question: the agent gets
 * the grant or the saved scope) carries `seenByAgent: true`; it is sent without the note
 * (the flag itself is removed).
 */
export const HOST_ONLY_NOTE = '🔒 Not seen by the agent';

/** Question ids of host-only cards sent since start (for re-rendering after a click). */
const hostOnlyCards = new Set<string>();
const MAX_REMEMBERED = 5000;

export function withHostOnlyNote(text: string): string {
  return text.includes(HOST_ONLY_NOTE) ? text : `${text}\n\n${HOST_ONLY_NOTE}`;
}

/** The chat-sdk message content with the note added (text, or a card's question). */
export function markHostOnly(content: string): string {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(content) as Record<string, unknown>;
    // eslint-disable-next-line no-catch-all/no-catch-all -- not JSON: leave it as it is
  } catch {
    return content;
  }
  if (!parsed || typeof parsed !== 'object') return content;
  if (parsed.seenByAgent === true) {
    const { seenByAgent: _seen, ...rest } = parsed;
    return JSON.stringify(rest);
  }
  if (parsed.type === 'ask_question' && typeof parsed.question === 'string') {
    if (typeof parsed.questionId === 'string') {
      if (hostOnlyCards.size >= MAX_REMEMBERED) hostOnlyCards.clear();
      hostOnlyCards.add(parsed.questionId);
    }
    return JSON.stringify({ ...parsed, question: withHostOnlyNote(parsed.question) });
  }
  if (typeof parsed.text === 'string' && parsed.text.trim()) {
    return JSON.stringify({ ...parsed, text: withHostOnlyNote(parsed.text) });
  }
  return content;
}

export function isHostOnlyCard(questionId: string): boolean {
  return hostOnlyCards.has(questionId);
}
