/**
 * Operator questions from the runtime ontology, delivered as DM button cards.
 *
 * The Python side (ontology/src/runtime/questions.py) owns all question logic. It writes
 * each question to data/ontology-runtime/questions/<id>.json with `card`, the one card
 * waiting for an answer. This module only shows that card (its choices, including
 * Python's "Back", plus "Other (please specify)") and records the answer in data/ontology-runtime/answers/<id>.json as
 * {"answers": {"<card n>": {"choice": value} | {"other": text}}}. This module is that
 * file's only writer. After "Other", the operator's next DM is the answer. When the
 * question is answered, it confirms with a short DM.
 *
 * The recipient is picked like an approval's (global admins, then owners). Only that
 * user's answers count.
 */
import fs from 'fs';
import path from 'path';

import type { InboundEvent } from '../../channels/adapter.js';
import { normalizeOptions } from '../../channels/ask-question.js';
import { registerQuestionRenderResolver } from '../../channels/question-render-registry.js';
import { DATA_DIR } from '../../config.js';
import { getDeliveryAdapter } from '../../delivery.js';
import { log } from '../../log.js';
import { registerResponseHandler, type ResponsePayload } from '../../response-registry.js';
import { registerMessageInterceptor } from '../../router.js';
import { pickApprovalDelivery, pickApprover } from '../approvals/primitive.js';

const RUNTIME_DIR = path.join(DATA_DIR, 'ontology-runtime');
const QUESTIONS_DIR = path.join(RUNTIME_DIR, 'questions');
const ANSWERS_DIR = path.join(RUNTIME_DIR, 'answers');
const ONTOLOGY_DIR = path.join(process.cwd(), 'ontology');
const CARD_PREFIX = 'ontq-';
const OTHER = { label: 'Other (please specify)', value: '@other' };
const BACK = '@back'; // Python's "Back to the last question" choice
const CAPTURE_WINDOW_MS = 15 * 60_000;
const MAX_TEXT = 2000;

interface Card {
  n: number;
  title: string;
  prompt: string;
  options: { label: string; value: string }[];
}
interface Question {
  id: string;
  title: string;
  status: 'open' | 'answered';
  card?: Card | null;
  result?: string;
}
interface Target {
  userId: string;
  channelType: string;
  platformId: string;
  instance: string | null;
}
type Answer = { choice: string } | { other: string };
interface HostAnswers {
  answers: Record<string, Answer>;
  /** card n -> when it was sent */
  sent: Record<string, string>;
  /** card n -> title and buttons as sent, in button order: resolves a click's index to
   *  its label, also after Python has moved on to the next card */
  cards?: Record<string, { title: string; options: Card['options'] }>;
  target?: Target;
  confirmed?: boolean;
  /** Waiting for the operator's next DM as the "Other" answer to card n, until when. */
  awaiting?: { n: number; until: string };
  updated_at?: string;
}

/** The card's choices with "Other" added, before Python's "Back" when there is one. Button
 *  indexes follow this order, so sending and resolving a click must both use it. */
function withOther(options: Card['options']): Card['options'] {
  const back = options.findIndex((o) => o.value === BACK);
  return back < 0 ? [...options, OTHER] : [...options.slice(0, back), OTHER, ...options.slice(back)];
}

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing or half-written file reads as absent
  } catch {
    return null;
  }
}

function readQuestion(id: string): Question | null {
  return readJson<Question>(path.join(QUESTIONS_DIR, `${id}.json`));
}

function loadHost(id: string): HostAnswers {
  return readJson<HostAnswers>(path.join(ANSWERS_DIR, `${id}.json`)) ?? { answers: {}, sent: {} };
}

function saveHost(id: string, data: HostAnswers): void {
  fs.mkdirSync(ANSWERS_DIR, { recursive: true });
  const file = path.join(ANSWERS_DIR, `${id}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify({ ...data, updated_at: new Date().toISOString() }, null, 2) + '\n', {
    mode: 0o600,
  });
  fs.renameSync(`${file}.tmp`, file);
}

/** Answered in chat or in the terminal (`ontology_runtime.py answer`). */
function isAnswered(id: string, host: HostAnswers, n: number): boolean {
  const terminal = readJson<{ answers: Record<string, Answer> }>(path.join(ANSWERS_DIR, `${id}.terminal.json`));
  return String(n) in host.answers || String(n) in (terminal?.answers ?? {});
}

function listQuestions(): Question[] {
  let names: string[] = [];
  try {
    names = fs.readdirSync(QUESTIONS_DIR).filter((n) => /^q[0-9a-f]+\.json$/.test(n));
    // eslint-disable-next-line no-catch-all/no-catch-all -- no questions directory yet
  } catch {
    return [];
  }
  return names.map((n) => readQuestion(n.slice(0, -5))).filter((q): q is Question => q !== null);
}

async function resolveTarget(): Promise<Target | null> {
  const picked = await pickApprovalDelivery(await pickApprover(null), '');
  if (!picked) return null;
  const mg = picked.messagingGroup;
  return {
    userId: picked.userId,
    channelType: mg.channel_type,
    platformId: mg.platform_id,
    instance: mg.instance ?? null,
  };
}

async function send(target: Target, content: Record<string, unknown>): Promise<void> {
  const adapter = getDeliveryAdapter();
  if (!adapter) throw new Error('no delivery adapter');
  await adapter.deliver(
    target.channelType,
    target.platformId,
    null,
    'chat-sdk',
    JSON.stringify(content),
    undefined,
    target.instance ?? undefined,
  );
}

/** Send each open question's current card once; confirm answered questions once. */
export async function deliverQuestions(): Promise<void> {
  for (const q of listQuestions()) {
    const host = loadHost(q.id);
    if (q.status === 'answered') {
      if (!host.confirmed && host.target) {
        await send(host.target, { text: `✅ ${q.title}: saved. ${q.result ?? ''}`.trim() });
        saveHost(q.id, { ...host, confirmed: true });
      }
      continue;
    }
    const card = q.card;
    if (!card || host.sent[card.n] || isAnswered(q.id, host, card.n)) continue;
    const target = host.target ?? (await resolveTarget());
    if (!target) {
      log.warn('Ontology question: no owner or admin reachable by DM', { questionId: q.id });
      continue;
    }
    const terminal =
      card.n === 1
        ? `\n\n(Or answer in a terminal: \`cd ${ONTOLOGY_DIR} && .venv/bin/python ontology_runtime.py answer ${q.id}\`)`
        : '';
    const options = withOther(card.options);
    // Recorded before sending, so a click can never arrive before its labels are known.
    saveHost(q.id, { ...host, target, cards: { ...host.cards, [card.n]: { title: card.title, options } } });
    await send(target, {
      type: 'ask_question',
      questionId: `${CARD_PREFIX}${q.id}-${card.n}`,
      title: card.title,
      question: `${card.prompt}${terminal}`,
      options: normalizeOptions(options),
    });
    const after = loadHost(q.id);
    saveHost(q.id, { ...after, sent: { ...after.sent, [card.n]: new Date().toISOString() } });
    log.info('Ontology question card sent', { questionId: q.id, card: card.n, to: target.userId });
  }
}

function parseCardId(questionId: string): { id: string; n: number } | null {
  const match = /^ontq-(q[0-9a-f]+)-(\d+)$/.exec(questionId);
  return match ? { id: match[1], n: Number(match[2]) } : null;
}

/** The card as sent, so the chat bridge shows the clicked button's label, not its index. */
function renderCard(questionId: string) {
  const ref = parseCardId(questionId);
  if (!ref) return undefined;
  const current = readQuestion(ref.id)?.card;
  const card =
    loadHost(ref.id).cards?.[ref.n] ??
    (current?.n === ref.n ? { title: current.title, options: withOther(current.options) } : undefined);
  return card ? { title: card.title, options: normalizeOptions(card.options) } : undefined;
}

async function handleResponse(payload: ResponsePayload): Promise<boolean> {
  if (!payload.questionId.startsWith(CARD_PREFIX)) return false;
  const ref = parseCardId(payload.questionId);
  const q = ref ? readQuestion(ref.id) : null;
  const n = ref?.n ?? -1;
  if (!q || q.status !== 'open' || q.card?.n !== n) return true; // stale card
  const host = loadHost(q.id);
  const target = host.target;
  if (target && payload.userId && target.userId !== payload.userId && !target.userId.endsWith(`:${payload.userId}`)) {
    log.warn('Ontology question: click from someone other than the asked user ignored', {
      questionId: q.id,
      userId: payload.userId,
    });
    return true;
  }
  if (isAnswered(q.id, host, n)) return true;
  const options = host.cards?.[n]?.options ?? withOther(q.card.options);
  const option =
    (/^\d+$/.test(payload.value) ? options[Number(payload.value)] : undefined) ??
    options.find((o) => o.value === payload.value);
  if (!option) return true;
  if (option === OTHER || option.value === OTHER.value) {
    if (!target) return true;
    saveHost(q.id, { ...host, awaiting: { n, until: new Date(Date.now() + CAPTURE_WINDOW_MS).toISOString() } });
    await send(target, {
      text: 'Type your answer in your own words in your next message here. It goes to the ontology, not to the agent.',
    });
    return true;
  }
  saveHost(q.id, { ...host, answers: { ...host.answers, [n]: { choice: option.value } } });
  log.info('Ontology question answered', { questionId: q.id, card: n, value: option.value });
  return true; // Python applies it; the next card goes out on a later delivery round
}

/** Router interceptor: the operator's next DM after they chose "Other". */
async function captureText(event: InboundEvent): Promise<boolean> {
  for (const q of listQuestions()) {
    if (q.status !== 'open') continue;
    const host = loadHost(q.id);
    const t = host.target;
    if (!host.awaiting || !t || t.platformId !== event.platformId || t.channelType !== event.channelType) continue;
    if (host.awaiting.until < new Date().toISOString() || q.card?.n !== host.awaiting.n) {
      saveHost(q.id, { ...host, awaiting: undefined });
      continue;
    }
    let text = '';
    try {
      const parsed = JSON.parse(event.message.content) as Record<string, unknown>;
      text = typeof parsed.text === 'string' ? parsed.text.trim() : '';
      // eslint-disable-next-line no-catch-all/no-catch-all -- not a text message
    } catch {
      text = '';
    }
    if (!text) return false;
    const n = host.awaiting.n;
    saveHost(q.id, {
      ...host,
      awaiting: undefined,
      answers: { ...host.answers, [n]: { other: text.slice(0, MAX_TEXT) } },
    });
    log.info('Ontology question: typed answer captured', { questionId: q.id, card: n, chars: text.length });
    await send(t, { text: 'Got it. Working out what that means.' });
    return true;
  }
  return false;
}

registerQuestionRenderResolver((questionId) =>
  questionId.startsWith(CARD_PREFIX) ? renderCard(questionId) : undefined,
);
registerResponseHandler(handleResponse);
registerMessageInterceptor(captureText);
