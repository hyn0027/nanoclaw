import { afterEach, describe, expect, test } from 'bun:test';

import { permissionMode, permissionOptions, PREAPPROVED_TOOLS } from './permission-mode.js';

const BYPASS = { permissionMode: 'bypassPermissions' as const, allowDangerouslySkipPermissions: true };
const ALLOWLIST = ['Bash', 'Read', 'Write', 'mcp__nanoclaw__*', 'mcp__github__*'];

afterEach(() => {
  delete process.env.NANOCLAW_PERMISSION_MODE;
});

describe('permission mode', () => {
  test('unset or unknown is bypass, with the provider policy and allowlist unchanged', () => {
    for (const value of [undefined, '', 'nonsense']) {
      if (value === undefined) delete process.env.NANOCLAW_PERMISSION_MODE;
      else process.env.NANOCLAW_PERMISSION_MODE = value;
      expect(permissionMode()).toBe('bypass');
      const opts = permissionOptions(BYPASS, ALLOWLIST);
      expect(opts).toEqual({ ...BYPASS, allowedTools: ALLOWLIST });
      expect(opts.canUseTool).toBeUndefined();
    }
  });

  test('auto uses the classifier and pre-approves only low-risk tools', () => {
    process.env.NANOCLAW_PERMISSION_MODE = 'auto';
    const opts = permissionOptions(BYPASS, ALLOWLIST);
    expect(opts.permissionMode).toBe('auto');
    expect(opts.allowDangerouslySkipPermissions).toBe(false);
    expect(opts.allowedTools).toEqual(PREAPPROVED_TOOLS);
    expect(opts.allowedTools).not.toContain('Bash');
    expect(opts.allowedTools).not.toContain('mcp__github__*');
  });

  test('manual asks through canUseTool in the default mode', () => {
    process.env.NANOCLAW_PERMISSION_MODE = 'manual';
    const opts = permissionOptions(BYPASS, ALLOWLIST);
    expect(opts.permissionMode).toBe('default');
    expect(typeof opts.canUseTool).toBe('function');
    expect(opts.allowedTools).toContain('mcp__nanoclaw__*');
    expect(opts.allowedTools).not.toContain('Write');
  });

  test('auto mode denies a call left to a person (there is no one to ask)', async () => {
    process.env.NANOCLAW_PERMISSION_MODE = 'auto';
    const { canUseTool } = permissionOptions(BYPASS, ALLOWLIST);
    const result = await canUseTool!('Bash', { command: 'ls' }, {
      signal: new AbortController().signal,
      toolUseID: 'tu_1',
      requestId: 'r1',
    } as Parameters<NonNullable<typeof canUseTool>>[2]);
    expect(result.behavior).toBe('deny');
  });
});
