/**
 * Ontology gate: the agent waits for the host's runtime ontology before it acts.
 *
 * The host keeps a per-session ontology (src/modules/ontology-runtime) and the agent's
 * OneCLI policy groups in step with what the agent can reach. This makes the agent
 * wait for that work instead of racing it:
 *
 *   - before every turn (`syncBeforeTurn`): the host checks whether the agent group's
 *     skills, MCP servers, packages or credential grants changed since the last sync,
 *     answering at once when nothing did;
 *   - before a skill is loaded (`syncBeforeSkill`): its policy group is made and
 *     switched on, and its graph proposed if it has no reviewed one;
 *   - after every turn (`notifyTurnEnd`, fire-and-forget): the host expands from the
 *     calls the turn made.
 *
 * Waits are unbounded on purpose: the host may be waiting for the operator to answer
 * a question (e.g. a token's scope); see host-request.ts for the transport. A failure
 * (`ok: false`) means the action is refused (fail closed).
 *
 * Wire format: the request is an outbound system message
 * `{ action: "ontology_sync", requestId, reason: "turn" | "skill", skill? }`; the reply
 * is an inbound system message carrying the same `requestId` and
 * `frame: { ok: boolean, error?: string }`, found with the CLI-response lookup.
 */
import { askHost, newRequestId, sendToHost, type HostAnswer } from './host-request.js';

type GateResult = HostAnswer;

const syncedSkills = new Set<string>();

function log(msg: string): void {
  console.error(`[ontology-gate] ${msg}`);
}

function request(reason: 'turn' | 'skill', skill?: string): Promise<GateResult> {
  const content = { action: 'ontology_sync', requestId: newRequestId('ont'), reason, ...(skill ? { skill } : {}) };
  return askHost(content, `${reason}${skill ? ` ${skill}` : ''} (ontology sync)`, log);
}

/** On only when the host runs the ontology runtime (it sets NANOCLAW_ONTOLOGY_GATE=1). */
function gateOn(): boolean {
  return process.env.NANOCLAW_ONTOLOGY_GATE === '1';
}

/** What the user is told when a turn is refused because the sync failed. */
export function turnRefusal(error: string | undefined): string {
  return `I can't act on this yet: updating my environment's policies failed (${error ?? 'unknown error'}). It will be retried with your next message.`;
}

/** Wait until the host's ontology covers the agent's current setup. */
export function syncBeforeTurn(): Promise<GateResult> {
  return gateOn() ? request('turn') : Promise.resolve({ ok: true });
}

/** Wait until the skill's policy group and graph are in place (once per container). */
export async function syncBeforeSkill(skill: string): Promise<GateResult> {
  if (!gateOn() || syncedSkills.has(skill)) return { ok: true };
  const result = await request('skill', skill);
  if (result.ok) syncedSkills.add(skill);
  return result;
}

/** The skill a tool call loads, if any: the Skill tool, or a Read of a skill's SKILL.md. */
export function skillOfToolCall(tool: string, input: Record<string, unknown> | undefined): string | null {
  if (tool === 'Skill') {
    const name =
      String(input?.skill ?? '')
        .split(':')
        .pop() ?? '';
    return name || null;
  }
  if (tool === 'Read') {
    const m = /skills\/([A-Za-z0-9_.-]+)\/SKILL\.md$/.exec(String(input?.file_path ?? ''));
    return m ? m[1] : null;
  }
  return null;
}

/** Tell the host a turn ended, so it expands from that turn's calls. Never throws. */
export async function notifyTurnEnd(): Promise<void> {
  if (!gateOn()) return;
  try {
    await sendToHost({ action: 'ontology_turn_end', requestId: newRequestId('ont') });
    // eslint-disable-next-line no-catch-all/no-catch-all -- a missed notice only delays expansion to the next turn
  } catch (err) {
    log(`turn end notice not sent: ${err instanceof Error ? err.message : String(err)}`);
  }
}
