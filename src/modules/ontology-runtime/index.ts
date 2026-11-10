/**
 * Ontology-runtime module — keeps each session's ontology current.
 *
 * Every session has a runtime ontology (ontology/src/runtime, Python) that starts
 * from the base model in ontology/ and grows by expansions when:
 *   - the session's agent group gains a skill, MCP server, or package (or the skill
 *     text changes, or Claude Code's bundled skills are switched on);
 *   - the OneCLI gateway records new calls for the agent (GATEWAY_CAPTURE_DIR,
 *     mounted from data/onecli-capture/);
 *   - a OneCLI credential grant or secret is added or changed (a digest of ids and
 *     timestamps read from OneCLI's Postgres, never values).
 *
 * This module only decides WHEN to run: every POLL_MS it fingerprints those inputs
 * (cheap stat calls and a config read) and, once a change has held still for one
 * poll — or MAX_WAIT_MS after it was first seen, so a steady stream of calls is
 * still batched — runs `ontology/.venv/bin/python ontology_runtime.py tick`. One
 * tick at a time; output goes to logs/ontology-runtime/runtime.log. The tick itself
 * is resumable (state per session, cursor over the capture files), so a skipped or
 * killed run only delays the next one.
 *
 * It also delivers the runtime's questions to the operator (questions.ts): facts it
 * cannot observe, such as a token's scope, asked as DM button cards; and it switches
 * OneCLI policy groups within seconds of the agent loading a skill (watchSkills ->
 * `ontology_runtime.py policies`, see ontology/src/runtime/policies.py). Every tick
 * also reconciles the groups (sessions closed, MCP servers added or removed).
 *
 * Disabled with NANOCLAW_ONTOLOGY_RUNTIME=false, or when ontology/.venv is missing.
 */
import { execFile, spawn, type ChildProcess } from 'child_process';
import crypto from 'crypto';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { DATA_DIR, SESSION_LOGS_DIR } from '../../config.js';
import { CONTAINER_RUNTIME_BIN } from '../../container-runtime.js';
import { getAllContainerConfigs } from '../../db/container-configs.js';
import { getActiveSessions, getRunningSessions } from '../../db/sessions.js';
import { onHostShutdown, onHostStart } from '../../host-lifecycle.js';
import { log } from '../../log.js';

import { deliverQuestions } from './questions.js';

const POLL_MS = 15_000;
const MAX_WAIT_MS = 120_000;
const TICK_TIMEOUT_MS = 30 * 60_000;

const ROOT = process.cwd();
const ONTOLOGY_DIR = path.join(ROOT, 'ontology');
const PYTHON = path.join(ONTOLOGY_DIR, '.venv', 'bin', 'python');
const ENTRY = path.join(ONTOLOGY_DIR, 'ontology_runtime.py');
const CAPTURE_DIR = process.env.NANOCLAW_CAPTURE_DIR || path.join(DATA_DIR, 'onecli-capture');
const SKILLS_DIR = path.join(ROOT, 'container', 'skills');
const LOG_DIR = path.join(ROOT, 'logs', 'ontology-runtime');
const RUNTIME_DIR = path.join(DATA_DIR, 'ontology-runtime');

let running = false;
let timer: NodeJS.Timeout | null = null;
let child: ChildProcess | null = null;
let processed = ''; // fingerprint the last successful tick started from
let seen = ''; // fingerprint at the previous poll
let changedSince = 0;

// ── policy groups follow loaded skills ─────────────────────────────────────
// The action log (src/modules/action-log) is exported about once a second. When a
// running session's log gains a Skill call (or a read of a SKILL.md), switch OneCLI
// policy groups right away instead of waiting for the next tick.
const SKILL_WATCH_MS = 2_000;
const logOffsets = new Map<string, number>();
let hostStartedAt = Date.now();
let skillTimer: NodeJS.Timeout | null = null;
let policyChild: ChildProcess | null = null;
let policyAgain = false;

function runPolicies(): void {
  if (policyChild) {
    policyAgain = true;
    return;
  }
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const out = fs.openSync(path.join(LOG_DIR, 'runtime.log'), 'a');
  const env = { ...process.env, PATH: `${process.env.PATH ?? ''}:${path.join(os.homedir(), '.local', 'bin')}` };
  policyChild = spawn(PYTHON, [ENTRY, 'policies'], { cwd: ONTOLOGY_DIR, env, stdio: ['ignore', out, out] });
  const kill = setTimeout(() => policyChild?.kill('SIGTERM'), TICK_TIMEOUT_MS);
  policyChild.on('exit', (code) => {
    clearTimeout(kill);
    fs.closeSync(out);
    policyChild = null;
    if (code !== 0) log.warn('Ontology policy switch failed', { code, log: path.join(LOG_DIR, 'runtime.log') });
    if (policyAgain) {
      policyAgain = false;
      runPolicies();
    }
  });
  policyChild.on('error', (err) => log.warn('Ontology policy switch could not start', { err }));
}

/** New bytes of each running session's action log; true when one of them loads a skill. */
async function skillLoaded(): Promise<boolean> {
  let loaded = false;
  for (const s of await getRunningSessions()) {
    const file = path.join(SESSION_LOGS_DIR, s.agent_group_id, s.id, 'actions.jsonl');
    let size: number;
    let mtime: number;
    try {
      ({ size, mtimeMs: mtime } = fs.statSync(file));
      // eslint-disable-next-line no-catch-all/no-catch-all -- no action log yet
    } catch {
      continue;
    }
    // First sight of a log written before this host started: its history is the tick's
    // job (policies.reconcile reads every log). A log started since: read it all.
    const from = logOffsets.get(file) ?? (mtime > hostStartedAt ? 0 : size);
    logOffsets.set(file, size);
    if (size <= from) continue;
    const fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(size - from);
    fs.readSync(fd, buf, 0, buf.length, from);
    fs.closeSync(fd);
    const text = buf.toString('utf8');
    if (text.includes('"tool":"Skill"') || text.includes('SKILL.md')) loaded = true;
  }
  return loaded;
}

async function watchSkills(): Promise<void> {
  if (!running) return;
  try {
    if (await skillLoaded()) runPolicies();
    // eslint-disable-next-line no-catch-all/no-catch-all -- retried on the next watch
  } catch (err) {
    log.warn('Ontology skill watch error', { err });
  }
  if (running) skillTimer = setTimeout(() => void watchSkills(), SKILL_WATCH_MS);
}

function statLine(p: string): string {
  try {
    const s = fs.statSync(p);
    return `${p}:${s.size}:${s.mtimeMs}`;
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing file is part of the fingerprint
  } catch {
    return `${p}:-`;
  }
}

function listing(dir: string, filter: (name: string) => boolean = () => true): string[] {
  try {
    return fs
      .readdirSync(dir)
      .filter(filter)
      .sort()
      .map((n) => statLine(path.join(dir, n)));
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missing directory lists as empty
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
      (err, stdout) => resolve(err ? 'onecli:unreadable' : `onecli:${stdout.trim()}`),
    );
  });
}

/** Everything a tick reacts to, hashed. */
async function fingerprint(): Promise<string> {
  const sessions = await getActiveSessions();
  const groups = [...new Set(sessions.map((s) => s.agent_group_id))].sort();
  const configs = (await getAllContainerConfigs()).filter((c) => groups.includes(c.agent_group_id));
  const parts = [
    ...sessions.map((s) => s.id).sort(),
    ...configs.map((c) => JSON.stringify(c)).sort(),
    ...groups.map((g) => statLine(path.join(DATA_DIR, 'v2-sessions', g, '.claude-shared', 'settings.json'))),
    ...listing(SKILLS_DIR).flatMap((d) => [d, statLine(path.join(d.split(':')[0], 'SKILL.md'))]),
    ...listing(CAPTURE_DIR, (n) => n.endsWith('.jsonl')),
    // an answer (chat or terminal) lets a held session expand, and a tick applies it
    ...listing(path.join(RUNTIME_DIR, 'answers'), (n) => n.endsWith('.json')),
    await onecliGrants(),
  ];
  return crypto.createHash('sha256').update(parts.join('\n')).digest('hex');
}

function runTick(startedFrom: string): void {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const out = fs.openSync(path.join(LOG_DIR, 'runtime.log'), 'a');
  const env = { ...process.env, PATH: `${process.env.PATH ?? ''}:${path.join(os.homedir(), '.local', 'bin')}` };
  const started = Date.now();
  child = spawn(PYTHON, [ENTRY, 'tick'], { cwd: ONTOLOGY_DIR, env, stdio: ['ignore', out, out] });
  const kill = setTimeout(() => child?.kill('SIGTERM'), TICK_TIMEOUT_MS);
  child.on('exit', (code, signal) => {
    clearTimeout(kill);
    fs.closeSync(out);
    child = null;
    if (code === 0) {
      processed = startedFrom;
      log.info('Ontology runtime tick done', { ms: Date.now() - started });
    } else {
      log.warn('Ontology runtime tick failed; retried on the next change', {
        code,
        signal,
        log: path.join(LOG_DIR, 'runtime.log'),
      });
      processed = ''; // retry
    }
  });
  child.on('error', (err) => log.warn('Ontology runtime tick could not start', { err }));
}

async function poll(): Promise<void> {
  if (!running) return;
  try {
    await deliverQuestions();
    // eslint-disable-next-line no-catch-all/no-catch-all -- a failed card is retried on the next poll
  } catch (err) {
    log.warn('Ontology question delivery failed', { err });
  }
  try {
    if (!child) {
      const fp = await fingerprint();
      const now = Date.now();
      if (fp !== processed) {
        if (fp !== seen) changedSince = changedSince || now;
        const settled = fp === seen;
        if (settled || now - changedSince >= MAX_WAIT_MS) {
          changedSince = 0;
          runTick(fp);
        }
      } else {
        changedSince = 0;
      }
      seen = fp;
    }
    // eslint-disable-next-line no-catch-all/no-catch-all -- the runtime ontology must never affect the host; next poll retries
  } catch (err) {
    log.warn('Ontology runtime poll error', { err });
  }
  if (running) timer = setTimeout(() => void poll(), POLL_MS);
}

onHostStart(() => {
  if (process.env.NANOCLAW_ONTOLOGY_RUNTIME === 'false') return;
  if (!fs.existsSync(PYTHON) || !fs.existsSync(ENTRY)) {
    log.info('Ontology runtime off: ontology/.venv or ontology_runtime.py missing', { python: PYTHON });
    return;
  }
  running = true;
  hostStartedAt = Date.now();
  void poll();
  void watchSkills();
  log.info('Ontology runtime started', { capture: CAPTURE_DIR, log: path.join(LOG_DIR, 'runtime.log') });
});

onHostShutdown(() => {
  running = false;
  if (timer) clearTimeout(timer);
  timer = null;
  if (skillTimer) clearTimeout(skillTimer);
  skillTimer = null;
  policyChild?.kill('SIGTERM');
  // A tick is resumable: stop it rather than hold up shutdown.
  child?.kill('SIGTERM');
});
