/**
 * Operator questions from the runtime ontology, delivered as DM button cards.
 *
 * The Python side (ontology/src/runtime/questions.py) writes a question — a list of
 * steps, each a prompt with at most five options — to
 * data/ontology-runtime/questions/<id>.json when it needs a fact it cannot observe
 * (e.g. a GitHub token's scope). This module sends the owner one card per step, in
 * order, and records each answer (a click, or the operator's next DM after a "type
 * it" option) in data/ontology-runtime/answers/<id>.json (this module is that file's
 * only writer; Python only reads it). The waiting Python sync applies a complete
 * answer and marks the question answered; this module then confirms with a short DM.
 *
 * The recipient is picked like an approval's (global admins, then owners). Only that
 * user's answers count.
 */
import fs from 'fs';
import path from 'path';

import type { InboundEvent } from '../../channels/adapter.js';
import { normalizeOptions } from '../../channels/ask-question.js';
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
// Plain-language answers (see ontology/src/runtime/questions.py): the "@nl" option arms
// capture of the operator's next DM as a description (answer key DESCRIBE); "@text"
// arms it as that step's typed value; "again" on a confirmation re-arms a description.
const NL = '@nl';
const TEXT = '@text';
const DESCRIBE = 'describe';
const CONFIRM = 'nl.confirm.';
const CAPTURE_WINDOW_MS = 15 * 60_000;
const MAX_TEXT = 2000;

interface Option {
  label: string;
  value: string;
}
interface Condition {
  step: string;
  in: string[];
}
interface Step {
  id: string;
  prompt: string;
  options: Option[];
  when?: Condition | Condition[];
  free_text?: string;
}
interface Question {
  id: string;
  title: string;
  intro: string;
  steps: Step[];
  status: 'open' | 'answered';
  result?: string;
}
interface Target {
  userId: string;
  channelType: string;
  platformId: string;
  instance: string | null;
}
interface HostAnswers {
  answers: Record<string, string>;
  sent: Record<string, string>;
  target?: Target;
  confirmed?: boolean;
  /** Waiting for the operator's next DM: answer key it fills, and until when. */
  awaiting?: { key: string; until: string };
  updated_at?: string;
}

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing or half-written file reads as absent
  } catch {
    return null;
  }
}

function hostFile(id: string): string {
  return path.join(ANSWERS_DIR, `${id}.json`);
}

function loadHost(id: string): HostAnswers {
  return readJson<HostAnswers>(hostFile(id)) ?? { answers: {}, sent: {} };
}

function saveHost(id: string, data: HostAnswers): void {
  fs.mkdirSync(ANSWERS_DIR, { recursive: true });
  const file = hostFile(id);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ ...data, updated_at: new Date().toISOString() }, null, 2) + '\n', {
    mode: 0o600,
  });
  fs.renameSync(tmp, file);
}

/** Answers from chat and from the terminal (`ontology_runtime.py answer`); the terminal wins. */
function allAnswers(id: string, host: HostAnswers): Record<string, string> {
  const terminal = readJson<{ answers: Record<string, string> }>(path.join(ANSWERS_DIR, `${id}.terminal.json`));
  return { ...host.answers, ...(terminal?.answers ?? {}) };
}

function latestConfirm(q: Question): Step | undefined {
  const confirms = q.steps.filter((s) => s.id.startsWith(CONFIRM));
  return confirms[confirms.length - 1];
}

/** A description is in play, and the operator has not gone back to the choices. */
function nlMode(q: Question, given: Record<string, string>): boolean {
  const latest = latestConfirm(q);
  return DESCRIBE in given && !(latest && given[latest.id] === 'choices');
}

/** Mirrors questions.py `applies`: conditions (one or a list) must hold; in plain-language
 *  mode only the latest confirmation applies. */
function applies(q: Question, step: Step, given: Record<string, string>): boolean {
  if (step.id.startsWith(CONFIRM)) return nlMode(q, given) && step === latestConfirm(q);
  if (nlMode(q, given)) return false;
  const conds = step.when === undefined ? [] : Array.isArray(step.when) ? step.when : [step.when];
  return conds.every((c) => c.in.includes(given[c.step] ?? ''));
}

function nextStep(q: Question, given: Record<string, string>): Step | undefined {
  return q.steps.find((s) => applies(q, s, given) && !(s.id in given));
}

function terminalCommand(id: string): string {
  return `cd ${ONTOLOGY_DIR} && .venv/bin/python ontology_runtime.py answer ${id}`;
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

function listQuestions(): Question[] {
  let names: string[] = [];
  try {
    names = fs.readdirSync(QUESTIONS_DIR).filter((n) => /^q[0-9a-f]+\.json$/.test(n));
    // eslint-disable-next-line no-catch-all/no-catch-all -- no questions directory yet
  } catch {
    return [];
  }
  return names.map((n) => readJson<Question>(path.join(QUESTIONS_DIR, n))).filter((q): q is Question => q !== null);
}

/** Send the next unsent step of every open question; confirm answered ones once. */
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
    const step = nextStep(q, allAnswers(q.id, host));
    if (!step || host.sent[step.id]) continue;
    const target = host.target ?? (await resolveTarget());
    if (!target) {
      log.warn('Ontology question: no owner or admin reachable by DM', { questionId: q.id });
      continue;
    }
    const index = q.steps.indexOf(step);
    const first = Object.keys(host.sent).length === 0;
    const terminal = step.options.some((o) => o.value === TEXT)
      ? `\n\n(Or answer everything in a terminal: \`${terminalCommand(q.id)}\`)`
      : '';
    await send(target, {
      type: 'ask_question',
      questionId: `${CARD_PREFIX}${q.id}-${index}`,
      title: q.title,
      question: `${first ? `${q.intro}\n\n` : ''}${step.prompt}${terminal}`,
      options: normalizeOptions(step.options.map((o) => ({ label: o.label, value: o.value }))),
    });
    saveHost(q.id, { ...host, target, sent: { ...host.sent, [step.id]: new Date().toISOString() } });
    log.info('Ontology question step sent', { questionId: q.id, step: step.id, to: target.userId });
  }
}

async function handleResponse(payload: ResponsePayload): Promise<boolean> {
  if (!payload.questionId.startsWith(CARD_PREFIX)) return false;
  const match = /^ontq-(q[0-9a-f]+)-(\d+)$/.exec(payload.questionId);
  if (!match) return true;
  const [, id, rawIndex] = match;
  const q = readJson<Question>(path.join(QUESTIONS_DIR, `${id}.json`));
  const host = loadHost(id);
  const step = q?.steps[Number(rawIndex)];
  if (!q || !step || q.status !== 'open') return true;
  const target = host.target;
  if (target && payload.userId && !target.userId.endsWith(`:${payload.userId}`) && target.userId !== payload.userId) {
    log.warn('Ontology question: click from someone other than the asked user ignored', {
      questionId: id,
      userId: payload.userId,
    });
    return true;
  }
  if (step.id in allAnswers(id, host)) return true; // already answered (e.g. in the terminal)
  const byIndex = /^\d+$/.test(payload.value) ? step.options[Number(payload.value)] : undefined;
  const option = byIndex ?? step.options.find((o) => o.value === payload.value);
  if (!option) return true;
  if (option.value === NL || option.value === TEXT || option.value === 'again') {
    if (!target) return true;
    const key = option.value === TEXT ? step.id : DESCRIBE;
    const answers = option.value === 'again' ? { ...host.answers, [step.id]: 'again' } : host.answers;
    const until = new Date(Date.now() + CAPTURE_WINDOW_MS).toISOString();
    saveHost(id, { ...host, answers, awaiting: { key, until } });
    await send(target, {
      text:
        key === DESCRIBE
          ? 'Describe the token\'s access in your own words in your next message here, e.g. "read and write on code, issues and pull requests in owner/repo, no account permissions, expires 2027-01-14". That message goes to the ontology, not to the agent.'
          : `Type ${step.free_text ?? 'the value'} in your next message here (it goes to the ontology, not to the agent).`,
    });
    return true;
  }
  saveHost(id, { ...host, answers: { ...host.answers, [step.id]: option.value } });
  log.info('Ontology question answered', { questionId: id, step: step.id, value: option.value });
  return true; // the next step goes out on the next delivery round
}

/** Router interceptor: the operator's next DM after they chose to type an answer. */
async function captureText(event: InboundEvent): Promise<boolean> {
  for (const q of listQuestions()) {
    if (q.status !== 'open') continue;
    const host = loadHost(q.id);
    const t = host.target;
    if (!host.awaiting || !t || t.platformId !== event.platformId || t.channelType !== event.channelType) continue;
    if (host.awaiting.until < new Date().toISOString()) {
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
    saveHost(q.id, {
      ...host,
      awaiting: undefined,
      answers: { ...host.answers, [host.awaiting.key]: `text:${text.slice(0, MAX_TEXT)}` },
    });
    log.info('Ontology question: typed answer captured', {
      questionId: q.id,
      key: host.awaiting.key,
      chars: text.length,
    });
    await send(t, {
      text:
        host.awaiting.key === DESCRIBE ? "Got it. Working out what that means; I'll ask you to confirm." : 'Got it.',
    });
    return true;
  }
  return false;
}

registerResponseHandler(handleResponse);
registerMessageInterceptor(captureText);
