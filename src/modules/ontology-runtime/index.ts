/**
 * Ontology-runtime module — keeps each session's ontology current, driven by the agent.
 *
 * Every session has a runtime ontology (ontology/src/runtime, Python) that starts from
 * the base model in ontology/ and grows by expansions. Nothing here polls for changes:
 * the agent's container asks (container/agent-runner/src/ontology-gate.ts) and waits.
 *
 *   - `ontology_sync` reason "turn", before every turn: when the agent group's setup
 *     (container config: skills, MCP servers, packages; Claude settings; the installed
 *     container skills; OneCLI credential grants) is unchanged since the session's last
 *     sync, the answer is immediate. Otherwise `ontology_runtime.py sync --session` runs:
 *     base changes, the operator's answers (it waits for them) and the agent's OneCLI
 *     policy groups. An approved MCP server or package restarts the container, so its
 *     first turn is where that change is synced.
 *   - `ontology_sync` reason "skill", before the agent loads a skill: the same sync with
 *     `--skill`, which also expands that skill (when it has no reviewed graph) and makes
 *     and switches on its policy group.
 *   - after either, in the background (nobody waits): `sync --sources` expands the other
 *     queued sources (skills with no reviewed graph, added packages), one per run so a
 *     later sync waits behind at most one. Policies use only reviewed graphs.
 *   - `ontology_turn_end`, after every turn (nobody waits): `sync --traffic` expands from
 *     the calls the gateway recorded for the turn.
 *
 * A sync that fails answers `ok: false` and the agent's action is refused (fail closed).
 * One sync runs at a time per session; output goes to logs/ontology-runtime/runtime.log.
 * Policy groups no session needs any more are switched off by the next sync of any
 * session (every turn end runs one) and once at host start.
 * Questions to the operator (questions.ts) are delivered on a short loop of their own.
 *
 * Disabled with NANOCLAW_ONTOLOGY_RUNTIME=false, or when ontology/.venv is missing; then
 * containers are started without NANOCLAW_ONTOLOGY_GATE and never ask. One agent group is
 * left out with `ncl groups config update --ontology off` (a baseline for comparison): its
 * containers never ask, and the Python side skips its sessions.
 */
import { execFile, spawn } from 'child_process';
import crypto from 'crypto';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { DATA_DIR } from '../../config.js';
import { CONTAINER_RUNTIME_BIN } from '../../container-runtime.js';
import { getContainerConfig } from '../../db/container-configs.js';
import { registerDeliveryAction } from '../../delivery.js';
import { unguarded } from '../../guard/index.js';
import { onHostShutdown, onHostStart } from '../../host-lifecycle.js';
import { log } from '../../log.js';
import { writeSessionMessage } from '../../session-manager.js';
import type { Session } from '../../types.js';

import { setOntologyGate } from './gate.js';
import { deliverQuestions } from './questions.js';

const QUESTION_POLL_MS = 3_000;

const ROOT = process.cwd();
const ONTOLOGY_DIR = path.join(ROOT, 'ontology');
const PYTHON = path.join(ONTOLOGY_DIR, '.venv', 'bin', 'python');
const ENTRY = path.join(ONTOLOGY_DIR, 'ontology_runtime.py');
const SKILLS_DIR = path.join(ROOT, 'container', 'skills');
const LOG_DIR = path.join(ROOT, 'logs', 'ontology-runtime');

let running = false;
let questionTimer: NodeJS.Timeout | null = null;
const children = new Set<ReturnType<typeof spawn>>();

/** Per session: the setup fingerprint its last successful sync covered. */
const synced = new Map<string, string>();
/** Per session: the tail of its queue (one Python run at a time per session). */
const queues = new Map<string, Promise<unknown>>();
/** Requests being handled (a container re-sends while it waits). */
const inFlight = new Set<string>();
/** Sessions with a traffic expansion queued but not started. */
const trafficQueued = new Set<string>();
/** Sessions whose queued sources are being expanded in the background. */
const expanding = new Set<string>();
/** Sessions whose last background expansion failed: retried on their next turn. */
const retrySources = new Set<string>();
/** `sync --sources` exit 3: it expanded one source and more are queued. */
const MORE_SOURCES = 'more sources queued';

function enqueue<T>(sessionId: string, job: () => Promise<T>): Promise<T> {
  const next = (queues.get(sessionId) ?? Promise.resolve()).then(job, job);
  queues.set(
    sessionId,
    next.catch(() => undefined),
  );
  return next;
}

// ── the agent group's setup, fingerprinted ────────────────────────────────

function statLine(p: string): string {
  try {
    const s = fs.statSync(p);
    return `${p}:${s.size}:${s.mtimeMs}`;
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing file is part of the fingerprint
  } catch {
    return `${p}:-`;
  }
}

function skillsListing(): string[] {
  try {
    return fs
      .readdirSync(SKILLS_DIR)
      .sort()
      .flatMap((n) => [statLine(path.join(SKILLS_DIR, n)), statLine(path.join(SKILLS_DIR, n, 'SKILL.md'))]);
    // eslint-disable-next-line no-catch-all/no-catch-all -- no skills directory lists as empty
  } catch {
    return [];
  }
}

const ONECLI_POSTGRES = process.env.ONECLI_POSTGRES_CONTAINER || 'onecli-postgres-1';
// Ids and timestamps of OneCLI's credential grants and secrets — never values. A grant
// added or changed (e.g. GitHub granted to the agent) changes this digest.
const GRANTS_DIGEST_SQL = `select md5(coalesce(string_agg(x, '|' order by x), '')) from (
  select r.id||':'||r.generation||':'||r.status as x from policy_rules_v2 r
  union all select t.rule_id||':'||coalesce(t.secret_id,'')||':'||t.kind from policy_rule_targets t
  union all select i.rule_id||':'||coalesce(i.agent_id,'')||':'||coalesce(i.user_id,'')||':'||coalesce(i.group_id,'') from policy_rule_identities i
  union all select s.id||':'||s.updated_at from secrets s) q`;

/** Digest of OneCLI's grants and secrets, or a marker when its database cannot be read. */
function onecliGrants(): Promise<string> {
  return new Promise((resolve) => {
    execFile(
      CONTAINER_RUNTIME_BIN,
      [
        'exec',
        ONECLI_POSTGRES,
        'sh',
        '-c',
        'psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-onecli}" -At -v ON_ERROR_STOP=1 -c "$1"',
        'sh',
        GRANTS_DIGEST_SQL,
      ],
      { timeout: 10_000 },
      (err, stdout) => resolve(err ? `onecli:unreadable:${Date.now()}` : `onecli:${stdout.trim()}`),
    );
  });
}

/** What a sync of this agent group depends on, hashed (cheap: a config read, stats, one query). */
async function setupFingerprint(agentGroupId: string): Promise<string> {
  const parts = [
    JSON.stringify((await getContainerConfig(agentGroupId)) ?? null),
    statLine(path.join(DATA_DIR, 'v2-sessions', agentGroupId, '.claude-shared', 'settings.json')),
    ...skillsListing(),
    await onecliGrants(),
  ];
  return crypto.createHash('sha256').update(parts.join('\n')).digest('hex');
}

// ── running the Python runtime ─────────────────────────────────────────────

/** Run `ontology_runtime.py <args>`; resolves with the failure reason, or null on success. */
function runRuntime(args: string[]): Promise<string | null> {
  return new Promise((resolve) => {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    const out = fs.openSync(path.join(LOG_DIR, 'runtime.log'), 'a');
    const env = { ...process.env, PATH: `${process.env.PATH ?? ''}:${path.join(os.homedir(), '.local', 'bin')}` };
    const child = spawn(PYTHON, [ENTRY, ...args], { cwd: ONTOLOGY_DIR, env, stdio: ['ignore', out, 'pipe'] });
    children.add(child);
    let failure = '';
    child.stderr?.on('data', (chunk: Buffer) => {
      const text = chunk.toString('utf8');
      fs.appendFileSync(path.join(LOG_DIR, 'runtime.log'), text);
      const m = /SYNC FAILED: (.*)/.exec(text);
      if (m) failure = m[1].trim();
    });
    child.on('error', (err) => {
      children.delete(child);
      fs.closeSync(out);
      resolve(`could not start the ontology runtime: ${err.message}`);
    });
    child.on('exit', (code, signal) => {
      children.delete(child);
      fs.closeSync(out);
      if (code === 0) resolve(null);
      else if (code === 3) resolve(MORE_SOURCES);
      else
        resolve(
          failure ||
            `ontology ${args[0]} exited with ${signal ?? `code ${code}`} (see logs/ontology-runtime/runtime.log)`,
        );
    });
  });
}

async function reply(session: Session, requestId: string, error: string | null): Promise<void> {
  await writeSessionMessage(session.agent_group_id, session.id, {
    id: `ontology-resp-${requestId}`,
    kind: 'system',
    timestamp: new Date().toISOString(),
    content: JSON.stringify({
      type: 'ontology_response',
      requestId,
      frame: error ? { ok: false, error } : { ok: true },
    }),
    trigger: false,
  });
}

/** The group is left out of the runtime ontology (`--ontology off`). */
async function ontologyOff(session: Session): Promise<boolean> {
  return (await getContainerConfig(session.agent_group_id))?.ontology === 'off';
}

async function handleSync(session: Session, requestId: string, reason: string, skill: string | null): Promise<void> {
  // Runtime off, or the group is left out of it: never hold the agent.
  if (!running || (await ontologyOff(session))) return reply(session, requestId, null);
  const started = Date.now();
  // An unchanged setup answers at once, without queueing behind a background expansion.
  if (reason === 'turn' && synced.get(session.id) === (await setupFingerprint(session.agent_group_id))) {
    await reply(session, requestId, null);
    if (retrySources.delete(session.id)) expandInBackground(session);
    return;
  }
  const error = await enqueue(session.id, async () => {
    if (reason === 'skill' && skill) {
      return runRuntime(['sync', '--session', session.id, '--skill', skill]);
    }
    const before = await setupFingerprint(session.agent_group_id);
    if (synced.get(session.id) === before) return null;
    const failed = await runRuntime(['sync', '--session', session.id]);
    if (!failed) synced.set(session.id, before);
    return failed;
  });
  await reply(session, requestId, error);
  if (!error) expandInBackground(session);
  const ms = Date.now() - started;
  if (error)
    log.warn('Ontology sync failed; the agent action is refused', { sessionId: session.id, reason, skill, error });
  else if (ms > 2_000) log.info('Ontology sync done', { sessionId: session.id, reason, skill, ms });
}

/** Expand the session's queued sources after the agent was answered, one per run. */
function expandInBackground(session: Session): void {
  if (!running || expanding.has(session.id)) return;
  expanding.add(session.id);
  const step = (): void => {
    void enqueue(session.id, () => runRuntime(['sync', '--session', session.id, '--sources'])).then((error) => {
      if (running && error === MORE_SOURCES) return step();
      expanding.delete(session.id);
      if (error) retrySources.add(session.id);
      if (error)
        log.warn('Ontology source expansion failed; retried after the next sync', { sessionId: session.id, error });
    });
  };
  step();
}

registerDeliveryAction(
  'ontology_sync',
  async (content, session) => {
    const requestId = typeof content.requestId === 'string' ? content.requestId : '';
    if (!requestId || inFlight.has(requestId)) return;
    inFlight.add(requestId);
    const reason = content.reason === 'skill' ? 'skill' : 'turn';
    const skill =
      typeof content.skill === 'string' && /^[A-Za-z0-9_.-]{1,100}$/.test(content.skill) ? content.skill : null;
    // Runs in the background: the delivery poll must not wait (a sync may wait for the operator).
    void handleSync(session, requestId, reason, skill)
      .catch((err) => log.warn('Ontology sync error', { sessionId: session.id, err }))
      .finally(() => inFlight.delete(requestId));
  },
  unguarded('read-only for the agent: syncs the host-side ontology and answers ok/refused'),
);

registerDeliveryAction(
  'ontology_turn_end',
  async (_content, session) => {
    if (!running || trafficQueued.has(session.id) || (await ontologyOff(session))) return;
    trafficQueued.add(session.id);
    void enqueue(session.id, () => {
      trafficQueued.delete(session.id);
      return runRuntime(['sync', '--session', session.id, '--traffic']);
    }).then((error) => {
      if (error)
        log.warn('Ontology traffic expansion failed; retried after the next turn', { sessionId: session.id, error });
    });
  },
  unguarded("notice only: expands the host-side ontology from the turn's recorded calls"),
);

async function pollQuestions(): Promise<void> {
  if (!running) return;
  try {
    await deliverQuestions();
    // eslint-disable-next-line no-catch-all/no-catch-all -- a failed card is retried on the next round
  } catch (err) {
    log.warn('Ontology question delivery failed', { err });
  }
  if (running) questionTimer = setTimeout(() => void pollQuestions(), QUESTION_POLL_MS);
}

onHostStart(() => {
  if (process.env.NANOCLAW_ONTOLOGY_RUNTIME === 'false') return;
  if (!fs.existsSync(PYTHON) || !fs.existsSync(ENTRY)) {
    log.info('Ontology runtime off: ontology/.venv or ontology_runtime.py missing', { python: PYTHON });
    return;
  }
  running = true;
  setOntologyGate(true);
  void pollQuestions();
  // Once at start: switch off policy groups no active session needs any more (sessions
  // closed while the host was down). Every sync reconciles them again.
  void runRuntime(['policies', '--no-generate']).then((error) => {
    if (error) log.warn('Ontology policy reconcile failed', { error });
  });
  log.info('Ontology runtime started (agent-driven sync)', { log: path.join(LOG_DIR, 'runtime.log') });
});

onHostShutdown(() => {
  running = false;
  setOntologyGate(false);
  if (questionTimer) clearTimeout(questionTimer);
  questionTimer = null;
  // Syncs are resumable; a waiting container re-sends its request after a restart.
  for (const child of children) child.kill('SIGTERM');
});
