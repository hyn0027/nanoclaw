import { describe, expect, it } from 'vitest';

import { invalidSpec, mergePackages, packageName, removePackage, validatePackages } from './packages.js';

describe('package specs', () => {
  it('accepts names and exact pins', () => {
    for (const s of [
      'vercel',
      '@modelcontextprotocol/server-memory',
      'pkg@1.2.3',
      '@scope/pkg@1.2.3-beta.1',
      'pkg@1.0.0+build.5',
    ]) {
      expect(invalidSpec('npm', s)).toBeNull();
    }
    for (const s of ['ffmpeg', 'g++', 'libssl-dev', 'ffmpeg=7:6.1.1-3', 'curl=8.5.0-2ubuntu10.6']) {
      expect(invalidSpec('apt', s)).toBeNull();
    }
  });

  it('rejects ranges, tags, URLs, flags and shell characters', () => {
    for (const s of [
      'pkg@^1.2.3',
      'pkg@latest',
      'pkg@1.2',
      'git+https://x/y.git',
      '-g',
      'pkg; rm -rf /',
      'pkg@1.2.3 other',
      'PKG',
    ]) {
      expect(invalidSpec('npm', s)).not.toBeNull();
    }
    for (const s of ['-y', 'ffmpeg=latest', 'ffmpeg && curl x', 'ffmpeg=>7']) {
      expect(invalidSpec('apt', s)).not.toBeNull();
    }
  });

  it('names a spec without its version, keeping a scope', () => {
    expect(packageName('npm', '@scope/pkg@1.2.3')).toBe('@scope/pkg');
    expect(packageName('npm', '@scope/pkg')).toBe('@scope/pkg');
    expect(packageName('npm', 'pkg@1.2.3')).toBe('pkg');
    expect(packageName('apt', 'ffmpeg=7:6.1')).toBe('ffmpeg');
  });

  it('replaces an earlier entry for the same package', () => {
    expect(mergePackages('npm', ['a', '@s/b@1.0.0'], ['@s/b@2.0.0', 'c'])).toEqual(['a', '@s/b@2.0.0', 'c']);
    expect(mergePackages('apt', ['ffmpeg'], ['ffmpeg=7:6.1'])).toEqual(['ffmpeg=7:6.1']);
  });

  it('removes by name or exact spec', () => {
    expect(removePackage('npm', ['a@1.0.0', 'b'], 'a')).toEqual(['b']);
    expect(removePackage('npm', ['a@1.0.0', 'b'], 'a@1.0.0')).toEqual(['b']);
  });

  it('validates request lists', () => {
    expect(validatePackages([], [])).toMatch(/at least one/);
    expect(validatePackages([], Array(21).fill('a'))).toMatch(/max 20/);
    expect(validatePackages(['ffmpeg'], ['pkg@1.2.3'])).toBeNull();
    expect(validatePackages([], ['pkg@^1'])).toMatch(/invalid npm package/);
  });
});

describe('installed versions', () => {
  it('reads dpkg lines and the npm probe output', async () => {
    const { parseInstalled } = await import('./package-lock.js');
    const out = [
      '__NANOCLAW_APT__',
      'ffmpeg=7:6.1.1-3',
      '__NANOCLAW_NPM__',
      JSON.stringify({ vercel: '39.1.0', '@s/pkg': '1.2.3' }),
    ].join('\n');
    expect(parseInstalled(out, ['ffmpeg', 'curl'], ['vercel', '@s/pkg@1.2.3'])).toEqual({
      apt: { ffmpeg: '7:6.1.1-3', curl: null },
      npm: { vercel: '39.1.0', '@s/pkg': '1.2.3' },
    });
    expect(parseInstalled('', ['ffmpeg'], ['vercel'])).toEqual({ apt: { ffmpeg: null }, npm: { vercel: null } });
  });
});
