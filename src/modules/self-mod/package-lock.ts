/**
 * What a group's image actually installed — the lockfile a global install lacks.
 *
 * Requested specs may be bare names (newest at build time), so after every
 * per-group image build the host runs the new image once and reads the installed
 * versions: `dpkg-query` for apt, and for npm each package's own package.json under pnpm's
 * global directory, read with node (the image's `pnpm` is a corepack shim that downloads
 * pnpm when run, so it cannot run offline). The probe runs with no network. The result is kept at
 * data/package-locks/<agent-group-id>.json (host-only; the agent's workspace never
 * sees it), newest build first, with the previous builds as history.
 */
import { execFile } from 'child_process';
import fs from 'fs';
import path from 'path';
import { promisify } from 'util';

import { DATA_DIR } from '../../config.js';
import { packageName } from './packages.js';

const execFileAsync = promisify(execFile);
export const PACKAGE_LOCK_DIR = path.join(DATA_DIR, 'package-locks');
const HISTORY = 20;

export interface PackageBuildRecord {
  built_at: string;
  image_tag: string;
  image_id: string | null;
  base_image_id: string | null;
  requested: { apt: string[]; npm: string[] };
  /** name -> installed version; null when it could not be read */
  installed: { apt: Record<string, string | null>; npm: Record<string, string | null> };
  /** set when the versions could not be read from the image */
  error?: string;
}

export interface PackageLock {
  agent_group_id: string;
  current: PackageBuildRecord;
  history: PackageBuildRecord[];
}

const APT_MARK = '__NANOCLAW_APT__';
const NPM_MARK = '__NANOCLAW_NPM__';

/** Node script: prints {name: version} for the npm packages named in argv, read from the
 *  installed package.json files under $PNPM_HOME/global/<n>/node_modules. */
const NPM_PROBE = [
  "const fs=require('fs'),p=require('path');",
  "const g=p.join(process.env.PNPM_HOME||'/pnpm','global');",
  'const roots=fs.existsSync(g)?fs.readdirSync(g).map(d=>p.join(g,d,"node_modules")):[];',
  'const out={};',
  'for(const n of process.argv.slice(1)){for(const r of roots){try{out[n]=JSON.parse(fs.readFileSync(p.join(r,n,"package.json"),"utf8")).version;break}catch(e){}}}',
  'console.log(JSON.stringify(out))',
].join('');

/** Parse the probe output: dpkg lines, then the npm probe's {name: version} (or a
 *  `pnpm ls -g --json` listing). */
export function parseInstalled(
  stdout: string,
  apt: string[],
  npm: string[],
): { apt: Record<string, string | null>; npm: Record<string, string | null> } {
  const aptOut = stdout.split(APT_MARK)[1]?.split(NPM_MARK)[0] ?? '';
  const npmOut = stdout.split(NPM_MARK)[1] ?? '';
  const aptVersions = new Map<string, string>();
  for (const line of aptOut.split('\n')) {
    const at = line.indexOf('=');
    if (at > 0) aptVersions.set(line.slice(0, at).trim(), line.slice(at + 1).trim());
  }
  const npmVersions = new Map<string, string>();
  try {
    const parsed = JSON.parse(npmOut.trim() || '{}') as unknown;
    if (Array.isArray(parsed)) {
      for (const entry of parsed as Array<{ dependencies?: Record<string, { version?: string }> }>) {
        for (const [name, dep] of Object.entries(entry.dependencies ?? {})) {
          if (dep?.version) npmVersions.set(name, dep.version);
        }
      }
    } else if (parsed && typeof parsed === 'object') {
      for (const [name, version] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof version === 'string') npmVersions.set(name, version);
      }
    }
    // eslint-disable-next-line no-catch-all/no-catch-all -- unreadable listing: versions stay null
  } catch {
    /* versions stay null */
  }
  const pick = (kind: 'apt' | 'npm', specs: string[], found: Map<string, string>) =>
    Object.fromEntries(specs.map((s) => [packageName(kind, s), found.get(packageName(kind, s)) ?? null]));
  return { apt: pick('apt', apt, aptVersions), npm: pick('npm', npm, npmVersions) };
}

async function inspectId(runtime: string, image: string): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(runtime, ['image', 'inspect', '--format', '{{.Id}}', image]);
    return stdout.trim() || null;
    // eslint-disable-next-line no-catch-all/no-catch-all -- an id we cannot read is recorded as null
  } catch {
    return null;
  }
}

/** Read the installed versions from a freshly built image and record them. Never throws. */
export async function recordInstalledPackages(opts: {
  runtime: string;
  agentGroupId: string;
  imageTag: string;
  baseImageId: string | null;
  apt: string[];
  npm: string[];
}): Promise<PackageBuildRecord> {
  const { runtime, agentGroupId, imageTag, apt, npm } = opts;
  const aptNames = apt.map((s) => packageName('apt', s));
  // Names are validated specs (packages.ts), so they are safe in the probe script.
  const script = [
    `echo ${APT_MARK}`,
    aptNames.length
      ? `dpkg-query -W -f='\${Package}=\${Version}\\n' ${aptNames.join(' ')} 2>/dev/null || true`
      : 'true',
    `echo ${NPM_MARK}`,
    // The node script arrives in an env var, so it needs no shell quoting.
    npm.length
      ? `node -e "$NANOCLAW_NPM_PROBE" ${npm.map((s) => packageName('npm', s)).join(' ')} || echo '{}'`
      : "echo '{}'",
  ].join('; ');
  const record: PackageBuildRecord = {
    built_at: new Date().toISOString(),
    image_tag: imageTag,
    image_id: await inspectId(runtime, imageTag),
    base_image_id: opts.baseImageId,
    requested: { apt, npm },
    installed: { apt: {}, npm: {} },
  };
  try {
    const { stdout } = await execFileAsync(
      runtime,
      [
        'run',
        '--rm',
        '--network',
        'none',
        '-e',
        `NANOCLAW_NPM_PROBE=${NPM_PROBE}`,
        '--entrypoint',
        'sh',
        imageTag,
        '-c',
        script,
      ],
      {
        timeout: 120_000,
      },
    );
    record.installed = parseInstalled(stdout, apt, npm);
    // eslint-disable-next-line no-catch-all/no-catch-all -- recording must not fail a build that succeeded
  } catch (e) {
    record.error = e instanceof Error ? e.message.split('\n')[0] : String(e);
    record.installed = parseInstalled('', apt, npm);
  }
  writeLock(agentGroupId, record);
  return record;
}

export function readLock(agentGroupId: string): PackageLock | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(PACKAGE_LOCK_DIR, `${agentGroupId}.json`), 'utf8')) as PackageLock;
    // eslint-disable-next-line no-catch-all/no-catch-all -- no lock yet
  } catch {
    return null;
  }
}

function writeLock(agentGroupId: string, record: PackageBuildRecord): void {
  const previous = readLock(agentGroupId);
  const lock: PackageLock = {
    agent_group_id: agentGroupId,
    current: record,
    history: previous ? [previous.current, ...previous.history].slice(0, HISTORY) : [],
  };
  fs.mkdirSync(PACKAGE_LOCK_DIR, { recursive: true });
  const file = path.join(PACKAGE_LOCK_DIR, `${agentGroupId}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(lock, null, 2) + '\n');
  fs.renameSync(`${file}.tmp`, file);
}

/** "vercel@39.1.0, ffmpeg=7:6.1.1-3" — for notes and logs. */
export function describeInstalled(record: PackageBuildRecord): string {
  const parts = [
    ...Object.entries(record.installed.npm).map(([n, v]) => `${n}@${v ?? '?'}`),
    ...Object.entries(record.installed.apt).map(([n, v]) => `${n}=${v ?? '?'}`),
  ];
  return parts.join(', ');
}
