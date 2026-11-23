/**
 * Permission mode: Claude Code's own safeguards for the agent's tool calls, as baselines to
 * compare the runtime ontology against. Set per agent group with
 * `ncl groups config update --permission-mode bypass|auto|manual`; the host passes it as
 * NANOCLAW_PERMISSION_MODE.
 *
 *   - bypass (default, variable unset): every tool call runs unchecked; the container,
 *     the OneCLI gateway and host approvals are the boundary.
 *   - auto: Claude Code's classifier approves or denies each call that would need
 *     permission. There is no person to ask, so a call it leaves to a person is denied.
 *   - manual: each call that would need permission waits for an owner or admin to
 *     Allow or Deny it in a DM card (host side: src/modules/permission-mode/).
 *
 * In auto and manual mode only low-risk tools are pre-approved: reading and searching,
 * skills, subagents, and NanoClaw's own MCP tools (their privileged actions already need
 * host approval). Bash, file writes, web access and other MCP servers go through the mode.
 *
 * Every decision is written to the action log as `permission_decision`
 * (`{ tool, mode, decision: allow | deny, by: operator | classifier | no-one, reason? }`),
 * next to the `tool_start` / `tool_end` events, so conditions can be compared.
 */
import type { CanUseTool, HookCallback, PermissionMode as SdkPermissionMode } from '@anthropic-ai/claude-agent-sdk';

import { recordAction } from './db/action-log.js';
import { askHost, newRequestId } from './host-request.js';

export type PermissionMode = 'bypass' | 'auto' | 'manual';

/** Pre-approved in auto and manual mode (the SDK never asks about them). */
export const PREAPPROVED_TOOLS = [
  'Read',
  'Glob',
  'Grep',
  'TodoWrite',
  'ToolSearch',
  'Skill',
  'Task',
  'TaskOutput',
  'TaskStop',
  'TeamCreate',
  'TeamDelete',
  'mcp__nanoclaw__*',
];

const MAX_INPUT_CHARS = 1500;

function log(msg: string): void {
  console.error(`[permission-mode] ${msg}`);
}

export function permissionMode(): PermissionMode {
  const mode = process.env.NANOCLAW_PERMISSION_MODE;
  return mode === 'auto' || mode === 'manual' ? mode : 'bypass';
}

function decision(
  tool: string,
  mode: PermissionMode,
  allowed: boolean,
  by: string,
  extra: { toolUseId?: string; agentId?: string; reason?: string },
): void {
  recordAction({
    event: 'permission_decision',
    toolUseId: extra.toolUseId,
    agentId: extra.agentId,
    data: { tool, mode, decision: allowed ? 'allow' : 'deny', by, ...(extra.reason ? { reason: extra.reason } : {}) },
  });
}

/** The tool input as the operator sees it on the card, clipped. */
function describeInput(input: Record<string, unknown>): string {
  const text = JSON.stringify(input, null, 1);
  return text.length > MAX_INPUT_CHARS ? `${text.slice(0, MAX_INPUT_CHARS)}…` : text;
}

/** Manual mode: ask an owner or admin; auto mode: nobody to ask, so deny. */
const canUseTool: CanUseTool = async (toolName, input, options) => {
  const mode = permissionMode();
  const ids = { toolUseId: options.toolUseID, agentId: options.agentID };
  if (mode !== 'manual') {
    const reason = 'auto mode has no person to ask';
    decision(toolName, mode, false, 'no-one', { ...ids, reason });
    return { behavior: 'deny', message: `This ${toolName} call needs a person's approval, and none is available.` };
  }
  const answer = await askHost(
    {
      action: 'permission_request',
      requestId: newRequestId('perm'),
      tool: toolName,
      input: describeInput(input),
      title: options.title,
      reason: options.decisionReason,
      blockedPath: options.blockedPath,
    } as { action: string; requestId: string },
    `${toolName} (permission)`,
    log,
  );
  decision(toolName, mode, answer.ok, 'operator', { ...ids, reason: answer.error });
  return answer.ok
    ? { behavior: 'allow', updatedInput: input }
    : {
        behavior: 'deny',
        message: `The operator denied this ${toolName} call${answer.error ? `: ${answer.error}` : ''}. Do not retry it another way.`,
      };
};

/** Auto mode's classifier denials (the SDK's PermissionDenied hook). */
export const permissionDeniedHook: HookCallback = async (input) => {
  const i = input as { tool_name?: string; tool_use_id?: string; agent_id?: string; reason?: string };
  decision(i.tool_name ?? '', permissionMode(), false, 'classifier', {
    toolUseId: i.tool_use_id,
    agentId: i.agent_id,
    reason: i.reason,
  });
  return {};
};

/**
 * The SDK options for the mode. `base` is the provider's execution policy and allowlist,
 * used as they are in bypass mode.
 */
export function permissionOptions(
  base: { permissionMode: SdkPermissionMode; allowDangerouslySkipPermissions: boolean },
  allowedTools: string[],
): {
  permissionMode: SdkPermissionMode;
  allowDangerouslySkipPermissions: boolean;
  allowedTools: string[];
  canUseTool?: CanUseTool;
} {
  const mode = permissionMode();
  if (mode === 'bypass') return { ...base, allowedTools };
  return {
    permissionMode: mode === 'auto' ? 'auto' : 'default',
    allowDangerouslySkipPermissions: false,
    allowedTools: PREAPPROVED_TOOLS,
    canUseTool,
  };
}
