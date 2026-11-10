/**
 * Package specs for a group's image (`install_packages`, `ncl groups config add-package`).
 *
 * A spec is a bare name or a name pinned to one exact version:
 *   apt  `ffmpeg`, `ffmpeg=7:6.1.1-3`                 (Debian version: starts with a digit)
 *   npm  `vercel`, `@scope/pkg`, `@scope/pkg@1.2.3`   (exact semver; no ranges, tags or URLs)
 *
 * Specs are interpolated into the generated Dockerfile's RUN lines
 * (container-runner.ts buildAgentGroupImage), so every entry point validates
 * them here and the build re-validates before writing the Dockerfile.
 * The agent-runner keeps a copy of these patterns
 * (container/agent-runner/src/mcp-tools/self-mod.ts); keep the two in sync.
 */

export const APT_SPEC_RE = /^[a-z0-9][a-z0-9._+-]*(=[0-9][A-Za-z0-9.+~:-]*)?$/;
export const NPM_SPEC_RE =
  /^(@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*(@\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?)?$/;
export const MAX_PACKAGES = 20;

export type PackageKind = 'apt' | 'npm';

/** The package name of a spec, without its pinned version. */
export function packageName(kind: PackageKind, spec: string): string {
  if (kind === 'apt') return spec.split('=')[0];
  const at = spec.indexOf('@', 1); // a scope's leading @ is not a version separator
  return at === -1 ? spec : spec.slice(0, at);
}

/** Why a spec is invalid, or null. */
export function invalidSpec(kind: PackageKind, spec: string): string | null {
  const ok = kind === 'apt' ? APT_SPEC_RE.test(spec) : NPM_SPEC_RE.test(spec);
  if (ok) return null;
  return kind === 'apt'
    ? `invalid apt package "${spec}": use a name or name=<exact version> (lowercase letters, digits, ._+-)`
    : `invalid npm package "${spec}": use a name or name@<exact version> like @scope/pkg@1.2.3 (no ranges, tags, URLs or flags)`;
}

/** The first problem with a request's package lists, or null. */
export function validatePackages(apt: string[], npm: string[]): string | null {
  if (apt.length + npm.length === 0) return 'at least one apt or npm package is required';
  if (apt.length + npm.length > MAX_PACKAGES) return `max ${MAX_PACKAGES} packages per request`;
  for (const spec of apt) {
    const why = invalidSpec('apt', spec);
    if (why) return why;
  }
  for (const spec of npm) {
    const why = invalidSpec('npm', spec);
    if (why) return why;
  }
  return null;
}

/** Add specs to a list; a spec replaces an existing entry for the same package (so a pin can change). */
export function mergePackages(kind: PackageKind, existing: string[], added: string[]): string[] {
  const out = [...existing];
  for (const spec of added) {
    const name = packageName(kind, spec);
    const at = out.findIndex((p) => packageName(kind, p) === name);
    if (at === -1) out.push(spec);
    else out[at] = spec;
  }
  return out;
}

/** Remove a package by name or exact spec. */
export function removePackage(kind: PackageKind, existing: string[], specOrName: string): string[] {
  const name = packageName(kind, specOrName);
  return existing.filter((p) => p !== specOrName && packageName(kind, p) !== name);
}
